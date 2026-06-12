/*
 * IchiPing — 10_inference firmware (本実装版)。
 *
 * 09_collector のコマンド/サーボ/音響インフラを再利用しつつ、メインの仕事は
 * 「単発推論」と「baseline 管理」に絞った推論専用ファーム。データ取得 (RUN)
 * は意図的に外してある (学習データ採取は 09 の仕事)。
 *
 * 推論フロー (INFER 1 回):
 *   1. 選択中 pattern を play_and_capture で再生 + INMP441 録音 (2 s = 32000 sample)
 *   2. ichp_features_logmag_psd (CMSIS-DSP rFFT × 30 segment Welch)
 *   3. baseline diff (factory or live)
 *   4. INT8 量子化 (TFLite モデルの入力 scale/zp に合わせて)
 *   5. ichp_tflite_invoke → 32-class logits → argmax → 5-bit door 状態 decode
 *   6. RESULT 行 ASCII 送信 + TFT に表示
 *
 * Baseline:
 *   factory : factory_baseline.h の INT8 配列を dequant した 1024 float
 *             (eval_noise_low/s00000 平均、デフォルト)
 *   live    : BL CALIBRATE で N frame 静粛録音 → Welch 平均、RAM のみ保持
 *
 * Servo: 09 と同じコマンド体系 (SERVO/OPEN/CLOSE/OPEN ALL/CLOSE ALL 等) で
 *        PC 側からテスト用に動かせる。
 *
 * Pattern: 09 と同じ pattern_lib。起動時は空、PC client が起動直後に push する。
 */

#include "pin_mux.h"
#include "clock_config.h"
#include "board.h"
#include <math.h>

#include "fsl_debug_console.h"
#include "fsl_lpuart.h"
#include "fsl_lpi2c.h"
#include "fsl_sai.h"
#include "fsl_gpio.h"
#include "fsl_port.h"

#include "sai_mic.h"
#include "sai_speaker.h"
#include "ichiping_frame.h"
#include "ichp_cmd.h"
#include "pattern_lib.h"
#include "spk_eq.h"
#include "servo_config.h"
#include "servo_driver.h"
#include "ili9341.h"
#include "ichp_features.h"
#include "ichp_tflite.h"
#include "model_data.h"
#include "app.h"

#include <math.h>
#include <stdarg.h>
#include <stdbool.h>
#include <string.h>
#include <stdio.h>

extern void BOARD_InitHardware(void);

/* UI 関連の forward declaration (do_infer_once が UI ヘルパを呼ぶため) */
static void  ui_gpio_init(void);
static void  ui_read_toggles(uint8_t bits_out[5]);
static bool  ui_read_exec_button_edge(void);
static inline void ui_set_infer_led(bool on);
static void  apply_toggle_to_servo(uint8_t i, uint8_t new_bit);

/* TFT 表示 (推論結果 / サーボ実状態) リフレッシュ — 全ての servo 移動 / 推論完了で呼ぶ */
static void  tft_show_state(void);
static void  tft_draw_labels_once(void);

/* ---- Audio constants ---- */
#define INF_SAMPLE_RATE       ICHP_FEAT_RATE_HZ                         /* 16000 */
#define INF_WINDOW_SAMP       ICHP_FEAT_WINDOW_SAMP                     /* 32000 */
#define INF_DEFAULT_VOLUME    5
#define INF_NAMED_MOVE_MS     500u
#define INF_SERVO_SETTLE_MS   400u

#ifndef INF_UART_BAUD
#define INF_UART_BAUD         921600u
#endif
#ifndef INF_UART_BASE
#define INF_UART_BASE         LPUART4
#endif

#ifndef INF_I2C_BASE
#define INF_I2C_BASE          LPI2C2
#endif
#ifndef INF_I2C_CLK_FREQ
#define INF_I2C_CLK_FREQ      CLOCK_GetLPFlexCommClkFreq(2)
#endif
#define INF_I2C_BAUD          100000U
#define INF_TFT_SPI_BAUD      60000000U   /* 60 MHz — ILI9341 typical SPI 上限 (40 MHz は安定動作確認済、60 MHz は実機マージン要確認) */

/* ---- TFLite Micro tensor arena ----
 * Neutron XL (108 KB model) の実効 arena は ~32 KB だが、念のため余裕を持つ。
 * 不足なら ichp_tflite_init が ERR_ALLOC を返すので増やす。
 * SRAM 配置は SDK のデフォルトリンカで自動。 */
#define INF_TFLITE_ARENA_BYTES  (64u * 1024u)
__attribute__((aligned(16)))
static uint8_t s_tflite_arena[INF_TFLITE_ARENA_BYTES];

/* ---- Welch / features 用バッファ (一度確保して使い回す) ---- */
#include "arm_math.h"
static arm_rfft_fast_instance_f32 s_rfft;
static float    s_hann_window[ICHP_FEAT_NFFT];
static float    s_seg_buf    [ICHP_FEAT_NFFT];
static float    s_fft_out    [ICHP_FEAT_NFFT];      /* packed complex (re/im interleaved) */
static float    s_accum_power[ICHP_FEAT_NFFT/2u + 1u];  /* 1025 */
static float    s_logmag     [ICHP_FEAT_N_BINS];    /* 1024 */
static int8_t   s_input_int8 [ICHP_FEAT_N_BINS];    /* TFLite 入力 */
static int8_t   s_output_int8[ICHP_TFLITE_OUTPUT_LEN]; /* 32 */

static ichp_features_ctx_t s_feat;

/* ---- Audio capture buffer ---- */
static int16_t s_excite[INF_WINDOW_SAMP];
static int16_t s_audio [INF_WINDOW_SAMP];

/* ---- Runtime state ---- */

typedef enum { BL_MODE_FACTORY = 0, BL_MODE_LIVE = 1 } baseline_mode_t;

typedef struct {
    int32_t          volume_pct;
    baseline_mode_t  bl_mode;
    bool             stop_requested;
    bool             in_infer_stream;        /* INFER STREAM 実行中フラグ (EXEC ボタン抑制用) */
    uint16_t         result_seq;             /* RESULT 行に乗せる連番 */
    float            current_deg[ICHP_SERVO_COUNT];

    /* TFT 表示用: 直近の推論結果の "有効性" を追跡。
     * 推論実行時: bits を s_pred_bits に保存 + 当時のサーボ状態を s_pred_servo_at に保存 + valid=true。
     * tft_show_state(): 現在のサーボ状態が s_pred_servo_at と一致しなくなった瞬間に valid=false にし、
     *                   推論結果欄を "-----" 表示に戻す。 */
    bool             pred_valid;
    uint8_t          pred_bits[5];           /* 推論結果 (a,b,c,AB,BC) */
    uint8_t          pred_servo_at[5];       /* 推論時の物理サーボ bit 状態 */
} inf_state_t;

static inf_state_t s_state = {
    .volume_pct      = INF_DEFAULT_VOLUME,
    .bl_mode         = BL_MODE_FACTORY,
    .stop_requested  = false,
    .in_infer_stream = false,
    .result_seq      = 0,
    .current_deg     = { 0.0f, 0.0f, 0.0f, 0.0f, 0.0f },
    .pred_valid      = false,
    .pred_bits       = { 0, 0, 0, 0, 0 },
    .pred_servo_at   = { 0, 0, 0, 0, 0 },
};

static servo_driver_t s_servo;
static sai_mic_t      s_mic;
static sai_speaker_t  s_spk;
static ili9341_t      s_tft;

/* ---- SysTick / delay ---- */

static volatile uint32_t s_uptime_ms = 0;
void SysTick_Handler(void) { s_uptime_ms++; }
static void systick_init_1ms(void) { (void)SysTick_Config(SystemCoreClock / 1000u); }
static void delay_ms(uint32_t ms) {
    uint32_t end = s_uptime_ms + ms;
    while ((int32_t)(s_uptime_ms - end) < 0) { __WFI(); }
}

/* ---- UART helpers (09 と同一) ---- */

static void uart_init_bidi(void)
{
    lpuart_config_t cfg;
    LPUART_GetDefaultConfig(&cfg);
    cfg.baudRate_Bps = INF_UART_BAUD;
    cfg.enableTx = true;
    cfg.enableRx = true;
    LPUART_Init(INF_UART_BASE, &cfg, BOARD_DEBUG_UART_CLK_FREQ);
}

static void uart_write_line(const char *s)
{
    LPUART_WriteBlocking(INF_UART_BASE, (const uint8_t *)s, strlen(s));
    static const uint8_t crlf[2] = { '\r', '\n' };
    LPUART_WriteBlocking(INF_UART_BASE, crlf, 2);
}

static void uart_printf(const char *fmt, ...)
{
    char buf[200];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (n > 0) {
        if ((size_t)n >= sizeof(buf)) n = sizeof(buf) - 1;
        LPUART_WriteBlocking(INF_UART_BASE, (const uint8_t *)buf, (size_t)n);
        static const uint8_t crlf[2] = { '\r', '\n' };
        LPUART_WriteBlocking(INF_UART_BASE, crlf, 2);
    }
}

/* ---- Full-duplex play + capture (08/09 と同じ) ---- */

static inline int16_t mic_word_to_int16(uint32_t w, uint8_t shift)
{
    int32_t s = (int32_t)w; s >>= shift;
    if (s > INT16_MAX) s = INT16_MAX;
    if (s < INT16_MIN) s = INT16_MIN;
    return (int16_t)s;
}

static void play_and_capture(const int16_t *tx, int16_t *rx, size_t n)
{
    I2S_Type *base = (I2S_Type *)s_mic.cfg.sai_base;
    const uint8_t shift = s_mic.gain_shift;
    while (SAI_RxGetStatusFlag(base) & kSAI_FIFORequestFlag) { (void)SAI_ReadData(base, 0u); }
    SAI_RxEnable(base, true);
    size_t tx_i = 0, rx_i = 0;
    while (rx_i < n) {
        if (tx_i < n && (SAI_TxGetStatusFlag(base) & kSAI_FIFORequestFlag)) {
            uint32_t w = ((uint32_t)(int32_t)tx[tx_i]) << 16;
            SAI_WriteData(base, 0u, w);
            tx_i++;
        }
        if (SAI_RxGetStatusFlag(base) & kSAI_FIFORequestFlag) {
            rx[rx_i++] = mic_word_to_int16(SAI_ReadData(base, 0u), shift);
        }
    }
    SAI_RxEnable(base, false);
}

/* ---- TFT sprite framebuffer (LovyanGFX 風) ----
 *
 * 各行 (PRED / TRUE / cls14) を RAM 上で 1 つの矩形バッファに描画し、
 * 完成したら 1 set_window + 1 blit で TFT に送る。set_window コマンドの
 * 往復回数を「5 桁 × 1 行 = 5 → 1」に集約でき、SPI バス占有率が上がる。
 *
 * バッファサイズは PRED 行 (150 × 35 = 5250 px, 10.3 KB) を最大とした
 * 共有領域 1 つだけ。cls14 行 (240 × 14 = 3360 px) も同じバッファに収まる。
 */
#define FB_MAX_PIXELS  5250u
static uint16_t s_fb[FB_MAX_PIXELS];

static void fb_fill(uint16_t color, uint32_t n) {
    for (uint32_t i = 0; i < n; i++) s_fb[i] = color;
}

static void fb_draw_char(uint16_t fb_w, uint16_t fb_h,
                          uint16_t fx, uint16_t fy, char c,
                          uint16_t fg, uint16_t bg, uint8_t sz)
{
    if (c < 0x20 || c > 0x7E) c = '?';
    const uint8_t *g = ili9341_font5x7_glyph(c);
    for (uint16_t col = 0; col < 5u; col++) {
        uint8_t bits = g[col];
        for (uint16_t row = 0; row < 7u; row++) {
            uint16_t color = (bits & (1u << row)) ? fg : bg;
            for (uint16_t dy = 0; dy < sz; dy++) {
                uint16_t y = (uint16_t)(fy + row * sz + dy);
                if (y >= fb_h) continue;
                uint16_t *line = &s_fb[y * fb_w];
                for (uint16_t dx = 0; dx < sz; dx++) {
                    uint16_t x = (uint16_t)(fx + col * sz + dx);
                    if (x >= fb_w) continue;
                    line[x] = color;
                }
            }
        }
    }
    /* spacing column (col 5, all bg) */
    for (uint16_t row = 0; row < 7u * sz; row++) {
        uint16_t y = (uint16_t)(fy + row);
        if (y >= fb_h) continue;
        uint16_t *line = &s_fb[y * fb_w];
        for (uint16_t dx = 0; dx < sz; dx++) {
            uint16_t x = (uint16_t)(fx + 5u * sz + dx);
            if (x >= fb_w) continue;
            line[x] = bg;
        }
    }
}

static void fb_draw_string(uint16_t fb_w, uint16_t fb_h,
                            uint16_t fx, uint16_t fy, const char *s,
                            uint16_t fg, uint16_t bg, uint8_t sz)
{
    while (*s) {
        if ((uint32_t)fx + 6u * sz > fb_w) break;
        fb_draw_char(fb_w, fb_h, fx, fy, *s, fg, bg, sz);
        fx = (uint16_t)(fx + 6u * sz);
        s++;
    }
}

/* fb を TFT に転送 (set_window + blit)。 */
static void fb_flush(uint16_t x, uint16_t y, uint16_t w, uint16_t h) {
    (void)ili9341_set_window(&s_tft, x, y,
                              (uint16_t)(x + w - 1u), (uint16_t)(y + h - 1u));
    (void)ili9341_blit(&s_tft, s_fb, (size_t)w * h);
}

/* ---- 5-bit door 状態 decode ---- */

static void decode_doors(uint8_t state_idx, uint8_t bits_out[5])
{
    /* 学習時の state_idx = a*1 + b*2 + c*4 + AB*8 + BC*16 と同じ規約 */
    for (uint8_t k = 0; k < 5; k++) {
        bits_out[k] = (state_idx >> k) & 1u;
    }
}

/* 14 等価クラス分類 (pc/training/dataset.py class_of と完全一致)。
 * 物理モデル: マイクは room A。
 *   AB == 0: 全 B/C 側が観測不能 → a のみ意味あり (A1/A2)
 *   AB == 1, BC == 0: c は観測不能 → (a, b) で B1..B4
 *   AB == 1, BC == 1: 全観測可 → C1..C8 = "C" + (1 + a + 2b + 4c)
 */
static const char *class_of_14(uint8_t state_idx)
{
    uint8_t bits[5]; decode_doors(state_idx, bits);
    const uint8_t a = bits[0], b = bits[1], c = bits[2];
    const uint8_t ab = bits[3], bc = bits[4];
    if (ab == 0u) return (a == 0u) ? "A1" : "A2";
    if (bc == 0u) {
        /* (a, b) → B1/B2/B3/B4 */
        static const char *table[4] = { "B1", "B2", "B3", "B4" };
        /* dataset.class_of: (0,0)->B1 (1,0)->B2 (0,1)->B3 (1,1)->B4
         *   → idx = a | (b << 1) */
        return table[(a | (b << 1)) & 0x3u];
    }
    /* C1..C8: 1 + a + 2b + 4c */
    static const char *ctable[8] = { "C1","C2","C3","C4","C5","C6","C7","C8" };
    const unsigned cidx = (unsigned)(a + 2u*b + 4u*c);
    return ctable[cidx];
}

/* ---- 逐次サーボ駆動 (boot 初期化と OPEN/CLOSE ALL で共有) ----
 *
 * SG90 5 個を同時駆動すると電流ピークで届かないことがあるため、09 と同じ
 * 順序 + per-channel settle + PWM 解放パターン:
 *   OPEN  : a → b → c → AB → BC  (窓 → 扉、室内換気優先)
 *   CLOSE : BC → AB → c → b → a  (扉 → 窓、エアロック逆順)
 * 各 ch INF_NAMED_MOVE_MS (500 ms) 待ってから servo_set_off で PWM 解放。
 * 完了後は current_deg[] が物理位置と一致しているという不変条件を保つ。
 */
static void drive_all_seq(bool is_open)
{
    const servo_config_t *cfg = servo_config_get();
    const float *t = is_open ? cfg->open_deg : cfg->home_deg;
    for (uint8_t step = 0; step < ICHP_SERVO_COUNT; step++) {
        uint8_t i = is_open ? step : (uint8_t)(ICHP_SERVO_COUNT - 1u - step);
        (void)servo_set_deg(&s_servo, i, t[i]);
        s_state.current_deg[i] = t[i];
        delay_ms(INF_NAMED_MOVE_MS);
        (void)servo_set_off(&s_servo, i);
    }
}

/* ---- INFER 1 回 ---- */

static const float *current_baseline(void)
{
    if (s_state.bl_mode == BL_MODE_LIVE && ichp_baseline_live_calibrated()) {
        return ichp_baseline_live();
    }
    return ichp_baseline_factory();
}

static const char *current_baseline_name(void)
{
    return (s_state.bl_mode == BL_MODE_LIVE && ichp_baseline_live_calibrated())
           ? "live" : "factory";
}

/* render + play_and_capture → 1024-bin log-mag PSD を s_logmag に書き込む。
 * pattern が未選択なら false を返す (呼び出し側がエラーメッセージ)。 */
static bool capture_logmag(uint32_t *cap_ms_out)
{
    const pattern_t *p = pattern_lib_get(g_pattern_lib.selected);
    if (!p) return false;
    uint32_t n = pattern_render(p, s_excite, INF_WINDOW_SAMP,
                                INF_SAMPLE_RATE, s_state.volume_pct);
    if (n == 0u) return false;
    if (n > INF_WINDOW_SAMP) n = INF_WINDOW_SAMP;
    spk_eq_apply(s_excite, n);

    /* 録音長は学習時と同じ 2 秒固定 (= WINDOW_SAMP)。pattern が短ければ
     * 残り無音だが、Welch 平均では問題ない (静寂 segment が混ざるだけ)。 */
    uint32_t t0 = s_uptime_ms;
    play_and_capture(s_excite, s_audio, INF_WINDOW_SAMP);
    if (cap_ms_out) *cap_ms_out = s_uptime_ms - t0;

    ichp_features_logmag_psd(&s_feat, s_audio, s_logmag);
    return true;
}

static void do_infer_once(void)
{
    s_state.result_seq++;
    ui_set_infer_led(true);     /* 推論中インジケータ LED ON */
    uint32_t cap_ms = 0;
    if (!capture_logmag(&cap_ms)) {
        ui_set_infer_led(false);
        uart_write_line("ERR INFER no_pattern (push patterns via PAT_* first)");
        return;
    }

    /* baseline 引き */
    const float *bl = current_baseline();
    ichp_features_subtract_baseline(s_logmag, bl);

    /* per-frame normalize: noise_diff_norm 学習モデルの整合用。
     * (学習側 samples_to_noise_diff_norm_features と同じ zero-mean unit-variance)。
     * 通常の noise_diff モデルでは精度が落ちる (= 異なる入力分布になる) ので、
     * 焼くモデルとの整合に注意。
     */
    ichp_features_normalize_frame(s_logmag);

    /* INT8 量子化 (model 入力 qparams を毎回取る — モデル更新で変わるため) */
    float scale; int32_t zp;
    ichp_tflite_input_qparams(&scale, &zp);
    ichp_features_quantize_int8(s_logmag, scale, zp, s_input_int8);

    /* 推論 */
    ichp_tflite_result_t r;
    ichp_tflite_status_t st = ichp_tflite_invoke(s_input_int8, s_output_int8, &r);
    if (st != ICHP_TFLITE_OK) {
        ui_set_infer_led(false);
        uart_printf("ERR INFER tflite status=%d", (int)st);
        return;
    }

    uint8_t bits[5];
    decode_doors(r.argmax_idx, bits);
    uint8_t bits2[5];
    decode_doors(r.second_idx, bits2);

    /* RESULT 行 — 32cls (state_idx/state) と 14cls (cls14_name) を両方明示。
     * 32cls は生の argmax 出力 (state_idx 0..31)、14cls は dataset.class_of と
     * 完全一致する正規分類 (A1/A2/B1..B4/C1..C8)。PC 側はどちらも参照可。
     * 2 位候補も同時に出して margin の物理意味 (どの真隣に間違えそうか) を見せる。 */
    uart_printf("RESULT seq=%u "
                "cls32_idx=%u cls32_state=s%u%u%u%u%u cls14=%s "
                "second32_idx=%u second32_state=s%u%u%u%u%u "
                "baseline=%s argmax_q=%d second_q=%d margin=%d "
                "infer_us=%u cap_ms=%u "
                "doors a=%u b=%u c=%u AB=%u BC=%u",
                (unsigned)s_state.result_seq,
                (unsigned)r.argmax_idx,
                (unsigned)bits[0], (unsigned)bits[1], (unsigned)bits[2],
                (unsigned)bits[3], (unsigned)bits[4],
                class_of_14(r.argmax_idx),
                (unsigned)r.second_idx,
                (unsigned)bits2[0], (unsigned)bits2[1], (unsigned)bits2[2],
                (unsigned)bits2[3], (unsigned)bits2[4],
                current_baseline_name(),
                (int)r.argmax_logit, (int)r.second_logit,
                (int)(r.argmax_logit - r.second_logit),
                (unsigned)r.invoke_us,
                (unsigned)cap_ms,
                (unsigned)bits[0], (unsigned)bits[1], (unsigned)bits[2],
                (unsigned)bits[3], (unsigned)bits[4]);

    /* 推論結果を s_state.pred_* に保存 (TFT 表示 は tft_show_state が担当)。
     * 同時に「推論時の物理サーボ状態」も保存しておき、後で物理状態が変わったら
     * tft_show_state 側で pred_valid を自動 false に倒して "-----" 表示に戻す。 */
    {
        const servo_config_t *cfg = servo_config_get();
        for (uint8_t i = 0; i < 5; i++) {
            s_state.pred_bits[i] = bits[i];
            float cur = s_state.current_deg[i];
            float dop = fabsf(cur - cfg->open_deg[i]);
            float dho = fabsf(cur - cfg->home_deg[i]);
            s_state.pred_servo_at[i] = (dop < dho) ? 1u : 0u;
        }
        s_state.pred_valid = true;
    }
    tft_show_state();
    ui_set_infer_led(false);     /* 推論完了 → LED OFF */
}

/* ---- INFER STREAM ---- */

static void poll_stop(ichp_cmd_lbuf_t *lb)
{
    while (LPUART_GetStatusFlags(INF_UART_BASE) & kLPUART_RxDataRegFullFlag) {
        uint8_t c = LPUART_ReadByte(INF_UART_BASE);
        if (ichp_cmd_lbuf_feed(lb, (char)c)) {
            ichp_cmd_t cmd;
            const char *et = NULL, *ea = NULL;
            if (ichp_cmd_parse(lb->buf, &cmd, &et, &ea)) {
                if (cmd.kind == ICHP_CMD_STOP) {
                    s_state.stop_requested = true;
                    uart_write_line("OK STOP requested");
                }
            }
            ichp_cmd_lbuf_reset(lb);
        }
    }
}

static void do_infer_stream(int32_t n, ichp_cmd_lbuf_t *lb)
{
    s_state.stop_requested = false;
    s_state.in_infer_stream = true;
    uart_printf("OK INFER started count=%d", (int)n);
    int32_t done = 0;
    for (int32_t i = 0; i < n && !s_state.stop_requested; i++) {
        do_infer_once();
        done++;
        poll_stop(lb);
    }
    if (s_state.stop_requested) {
        uart_printf("OK INFER aborted done=%d", (int)done);
    } else {
        uart_printf("OK INFER done count=%d", (int)done);
    }
    s_state.in_infer_stream = false;
}

/* ---- BL CALIBRATE ---- */

static void do_bl_calibrate(int32_t n, ichp_cmd_lbuf_t *lb)
{
    s_state.stop_requested = false;
    uart_printf("OK BL calibrating frames=%d", (int)n);
    ichp_baseline_live_begin();
    int32_t collected = 0;
    for (int32_t i = 0; i < n && !s_state.stop_requested; i++) {
        uint32_t cap_ms = 0;
        if (!capture_logmag(&cap_ms)) {
            uart_write_line("ERR BL no_pattern");
            return;
        }
        ichp_baseline_live_accumulate(s_logmag);
        collected++;
        uart_printf("INFO BL frame %d/%d cap_ms=%u", (int)collected, (int)n, (unsigned)cap_ms);
        poll_stop(lb);
    }
    if (collected == 0) {
        uart_write_line("ERR BL no_frames");
        return;
    }
    ichp_baseline_live_commit((uint16_t)collected);
    /* 自動的に live モードに切替 (運用想定: calibrate したらすぐ使う) */
    s_state.bl_mode = BL_MODE_LIVE;
    float scale; int32_t zp; ichp_tflite_input_qparams(&scale, &zp);
    /* 統計情報: live baseline の min/max/mean を出すと運用判断しやすい */
    const float *bl = ichp_baseline_live();
    float lo = bl[0], hi = bl[0], sum = 0.0f;
    for (uint32_t k = 0; k < ICHP_FEAT_N_BINS; k++) {
        if (bl[k] < lo) lo = bl[k];
        if (bl[k] > hi) hi = bl[k];
        sum += bl[k];
    }
    const float mean = sum / (float)ICHP_FEAT_N_BINS;
    uart_printf("OK BL calibrated frames=%d mode=live "
                "bl_min=%d bl_max=%d bl_mean=%d (dB, int rounded)",
                (int)collected, (int)lroundf(lo), (int)lroundf(hi), (int)lroundf(mean));
}

/* ---- Servo / pattern コマンド (09 から流用、INFER 専用なので簡略) ---- */

static void say_config(void)
{
    const pattern_t *p = pattern_lib_get(g_pattern_lib.selected);
    float scale; int32_t zp;
    ichp_tflite_input_qparams(&scale, &zp);
    uart_printf("OK CONFIG rate=%u window=%u pattern=%s sel_idx=%u count=%u "
                "volume=%d baseline=%s in_scale_x1e6=%d in_zp=%d "
                "n_invokes=%u last_us=%u",
                (unsigned)INF_SAMPLE_RATE, (unsigned)INF_WINDOW_SAMP,
                p ? p->name : "(none)",
                (unsigned)g_pattern_lib.selected,
                (unsigned)g_pattern_lib.count,
                (int)s_state.volume_pct,
                current_baseline_name(),
                (int)lroundf(scale * 1e6f), (int)zp,
                (unsigned)ichp_tflite_total_invokes(),
                (unsigned)ichp_tflite_last_invoke_us());
}

static void say_home(void)
{
    const servo_config_t *cfg = servo_config_get();
    uart_printf("OK HOME a=%d b=%d c=%d AB=%d BC=%d",
                (int)cfg->home_deg[0], (int)cfg->home_deg[1], (int)cfg->home_deg[2],
                (int)cfg->home_deg[3], (int)cfg->home_deg[4]);
}
static void say_open(void)
{
    const servo_config_t *cfg = servo_config_get();
    uart_printf("OK OPEN a=%d b=%d c=%d AB=%d BC=%d",
                (int)cfg->open_deg[0], (int)cfg->open_deg[1], (int)cfg->open_deg[2],
                (int)cfg->open_deg[3], (int)cfg->open_deg[4]);
}

static void say_bl_status(void)
{
    uart_printf("OK BL mode=%s factory_available=1 live_calibrated=%d live_frames=%u",
                current_baseline_name(),
                (int)ichp_baseline_live_calibrated(),
                (unsigned)ichp_baseline_live_frames());
}

static void apply_cmd(const ichp_cmd_t *cmd, ichp_cmd_lbuf_t *lb)
{
    switch (cmd->kind) {
        case ICHP_CMD_COMMENT: break;
        case ICHP_CMD_PING:        uart_write_line("OK PONG " __DATE__ " " __TIME__); break;
        case ICHP_CMD_GET_CONFIG:  say_config(); break;
        case ICHP_CMD_GET_HOME:    say_home();   break;
        case ICHP_CMD_GET_OPEN:    say_open();   break;
        case ICHP_CMD_SET_VOLUME:
            s_state.volume_pct = cmd->volume_pct;
            uart_printf("OK VOLUME %d", (int)cmd->volume_pct); break;
        case ICHP_CMD_SET_HOME:
            (void)servo_config_set_home(cmd->servo_idx, cmd->deg);
            uart_printf("OK HOME %s %d", ICHP_SERVO_NAMES[cmd->servo_idx], (int)cmd->deg); break;
        case ICHP_CMD_SET_OPEN:
            (void)servo_config_set_open(cmd->servo_idx, cmd->deg);
            uart_printf("OK OPEN %s %d", ICHP_SERVO_NAMES[cmd->servo_idx], (int)cmd->deg); break;

        /* Servo direct */
        case ICHP_CMD_SERVO: {
            status_t s = servo_set_deg(&s_servo, cmd->servo_idx, cmd->deg);
            if (s != kStatus_Success) {
                uart_printf("ERR SERVO_I2C %s status=%ld",
                            ICHP_SERVO_NAMES[cmd->servo_idx], (long)s); break;
            }
            s_state.current_deg[cmd->servo_idx] = cmd->deg;
            delay_ms(INF_NAMED_MOVE_MS);
            (void)servo_set_off(&s_servo, cmd->servo_idx);
            uart_printf("OK SERVO %s deg=%d",
                        ICHP_SERVO_NAMES[cmd->servo_idx], (int)cmd->deg);
            break;
        }
        case ICHP_CMD_SERVO_OFF:
            (void)servo_set_off(&s_servo, cmd->servo_idx);
            uart_printf("OK SERVO %s off", ICHP_SERVO_NAMES[cmd->servo_idx]); break;
        case ICHP_CMD_SERVO_ALL_OFF:
            (void)servo_all_off(&s_servo);
            uart_write_line("OK SERVO all off"); break;
        case ICHP_CMD_OPEN:
        case ICHP_CMD_CLOSE: {
            const servo_config_t *cfg = servo_config_get();
            const bool  is_open = (cmd->kind == ICHP_CMD_OPEN);
            const float target  = is_open ? cfg->open_deg[cmd->servo_idx]
                                          : cfg->home_deg[cmd->servo_idx];
            const char *verb    = is_open ? "OPEN" : "CLOSE";
            status_t s = servo_set_deg(&s_servo, cmd->servo_idx, target);
            if (s != kStatus_Success) {
                uart_printf("ERR SERVO_I2C %s %s status=%ld",
                            verb, ICHP_SERVO_NAMES[cmd->servo_idx], (long)s); break;
            }
            s_state.current_deg[cmd->servo_idx] = target;
            delay_ms(INF_NAMED_MOVE_MS);
            (void)servo_set_off(&s_servo, cmd->servo_idx);
            uart_printf("OK %s %s deg=%d", verb, ICHP_SERVO_NAMES[cmd->servo_idx], (int)target);
            break;
        }
        case ICHP_CMD_OPEN_ALL:
        case ICHP_CMD_CLOSE_ALL: {
            const bool is_open = (cmd->kind == ICHP_CMD_OPEN_ALL);
            const char *verb   = is_open ? "OPEN" : "CLOSE";
            drive_all_seq(is_open);   /* 09 と同じ逐次 (OPEN: a→b→..→BC / CLOSE: 逆順) */
            uart_printf("OK %s all", verb); break;
        }

        /* Pattern (09 と同一) */
        case ICHP_CMD_PAT_CLEAR:
            pattern_lib_clear();
            uart_write_line("OK PAT cleared"); break;
        case ICHP_CMD_PAT_PULSE_BEGIN:
            if (pattern_lib_pulse_begin(cmd->pat_name))
                uart_printf("OK PAT pulse begin name=%s", cmd->pat_name);
            else uart_write_line("ERR PAT lib_full");
            break;
        case ICHP_CMD_PAT_TONE:
            if (pattern_lib_pulse_add_tone(cmd->pat_a, cmd->pat_b, cmd->pat_c))
                uart_printf("OK PAT tone hz=%u on=%u off=%u",
                            (unsigned)cmd->pat_a, (unsigned)cmd->pat_b, (unsigned)cmd->pat_c);
            else uart_write_line("ERR PAT not_building_or_tone_full");
            break;
        case ICHP_CMD_PAT_PULSE_END: {
            uint8_t rep = (cmd->pat_i < 1) ? 1u : (uint8_t)cmd->pat_i;
            if (pattern_lib_pulse_end(rep))
                uart_printf("OK PAT pulse end count=%u", (unsigned)g_pattern_lib.count);
            else uart_write_line("ERR PAT pulse_end_failed");
            break;
        }
        case ICHP_CMD_PAT_SWEEP:
            if (pattern_lib_add_sweep(cmd->pat_name, cmd->pat_a, cmd->pat_b,
                                      cmd->pat_c, cmd->pat_d))
                uart_printf("OK PAT sweep name=%s", cmd->pat_name);
            else uart_write_line("ERR PAT lib_full");
            break;
        case ICHP_CMD_PAT_NOISE:
            if (pattern_lib_add_noise(cmd->pat_name, cmd->pat_a,
                                      (uint16_t)cmd->pat_b, (uint8_t)cmd->pat_c))
                uart_printf("OK PAT noise name=%s dur=%u vol=%u shape=%u",
                            cmd->pat_name, (unsigned)cmd->pat_a,
                            (unsigned)cmd->pat_b, (unsigned)cmd->pat_c);
            else uart_write_line("ERR PAT lib_full");
            break;
        case ICHP_CMD_PAT_INFO:
            uart_printf("OK PAT count=%u selected=%u",
                        (unsigned)g_pattern_lib.count, (unsigned)g_pattern_lib.selected);
            break;
        case ICHP_CMD_PAT_SELECT:
            if (cmd->pat_i < 0 || (uint8_t)cmd->pat_i >= g_pattern_lib.count) {
                uart_printf("ERR PAT index_out_of_range %d", (int)cmd->pat_i);
            } else if (pattern_lib_select((uint8_t)cmd->pat_i)) {
                const pattern_t *p = pattern_lib_get((uint8_t)cmd->pat_i);
                uart_printf("OK PAT select idx=%d name=%s",
                            (int)cmd->pat_i, p ? p->name : "?");
            } else uart_write_line("ERR PAT select_failed");
            break;

        /* EQ (09 と同一、簡略) */
        case ICHP_CMD_EQ_ENABLE:  spk_eq_enable(true);  uart_write_line("OK EQ enabled"); break;
        case ICHP_CMD_EQ_DISABLE: spk_eq_enable(false); uart_write_line("OK EQ disabled"); break;
        case ICHP_CMD_EQ_RESET:   spk_eq_reset();       uart_write_line("OK EQ reset"); break;
        case ICHP_CMD_EQ_STATE:
            uart_printf("OK EQ state=%s",
                        spk_eq_is_enabled() ? "ENABLED" : "DISABLED"); break;

        /* INFER 関連 (本ファーム独自) */
        case ICHP_CMD_INFER:           do_infer_once(); break;
        case ICHP_CMD_INFER_STREAM:    do_infer_stream(cmd->infer_n, lb); break;
        case ICHP_CMD_STOP:
            /* INFER STREAM 中なら stream loop が捕まえる。
             * idle で来た STOP は ack のみ。 */
            uart_write_line("OK STOP idle"); break;

        /* Baseline */
        case ICHP_CMD_BL_STATUS:  say_bl_status(); break;
        case ICHP_CMD_BL_FACTORY:
            s_state.bl_mode = BL_MODE_FACTORY;
            uart_write_line("OK BL mode=factory"); break;
        case ICHP_CMD_BL_LIVE:
            if (!ichp_baseline_live_calibrated()) {
                uart_write_line("ERR BL live_not_calibrated (run BL CALIBRATE first)");
            } else {
                s_state.bl_mode = BL_MODE_LIVE;
                uart_write_line("OK BL mode=live");
            }
            break;
        case ICHP_CMD_BL_CALIBRATE: do_bl_calibrate(cmd->infer_n, lb); break;
        case ICHP_CMD_BL_CLEAR:
            ichp_baseline_live_clear();
            s_state.bl_mode = BL_MODE_FACTORY;
            uart_write_line("OK BL cleared mode=factory"); break;

        case ICHP_CMD_EMIT: {
            int32_t idx = cmd->pat_i;
            if (idx < 0 || idx >= (int32_t)g_pattern_lib.count) {
                uart_printf("ERR EMIT index_out_of_range %d", (int)idx); break;
            }
            const pattern_t *p = pattern_lib_get((uint8_t)idx);
            uint32_t n = pattern_render(p, s_excite, INF_WINDOW_SAMP,
                                        INF_SAMPLE_RATE, s_state.volume_pct);
            spk_eq_apply(s_excite, n);
            (void)sai_speaker_play_blocking(&s_spk, s_excite, (size_t)n);
            uart_printf("OK EMIT idx=%d name=%s samples=%u",
                        (int)idx, p->name, (unsigned)n);
            break;
        }

        /* 推論ファームでは未サポートな verb は黙って拒否 (RUN/SET PIN 等) */
        case ICHP_CMD_RUN:
            uart_write_line("ERR BAD_VERB RUN_not_supported_use_INFER"); break;
        case ICHP_CMD_SET_PIN:
        case ICHP_CMD_CLEAR_PIN:
        case ICHP_CMD_CLEAR_PINS:
        case ICHP_CMD_GET_PINS:
            uart_write_line("ERR BAD_VERB PIN_not_supported_in_inference_fw"); break;
        case ICHP_CMD_SET_REPEATS:
            uart_write_line("ERR BAD_VERB use_INFER_STREAM_count"); break;

        default:
            uart_write_line("ERR BAD_VERB"); break;
    }
}

/* ---- I2C / TFT init (09 から流用) ---- */

static void i2c_init(void)
{
    lpi2c_master_config_t i2c;
    LPI2C_MasterGetDefaultConfig(&i2c);
    i2c.baudRate_Hz = INF_I2C_BAUD;
    LPI2C_MasterInit(INF_I2C_BASE, &i2c, INF_I2C_CLK_FREQ);
}

static status_t tft_init(void)
{
    gpio_pin_config_t out = { kGPIO_DigitalOutput, 1 };
    GPIO_PinInit(BOARD_ILI_CS_GPIO,  BOARD_ILI_CS_PIN,  &out);
    GPIO_PinInit(BOARD_ILI_RES_GPIO, BOARD_ILI_RES_PIN, &out);
    GPIO_PinInit(BOARD_ILI_DC_GPIO,  BOARD_ILI_DC_PIN,  &out);
    GPIO_PinInit(BOARD_ILI_BL_GPIO,  BOARD_ILI_BL_PIN,  &out);
    s_tft = (ili9341_t){
        .spi          = BOARD_ILI_SPI_BASE,
        .spi_clk_hz   = BOARD_ILI_SPI_CLK_FREQ,
        .spi_baud_hz  = INF_TFT_SPI_BAUD,
        .cs_gpio = BOARD_ILI_CS_GPIO, .cs_pin = BOARD_ILI_CS_PIN,
        .dc_gpio = BOARD_ILI_DC_GPIO, .dc_pin = BOARD_ILI_DC_PIN,
        .res_gpio = BOARD_ILI_RES_GPIO, .res_pin = BOARD_ILI_RES_PIN,
        .bl_gpio = BOARD_ILI_BL_GPIO, .bl_pin = BOARD_ILI_BL_PIN,
        .rotation = ILI9341_ROT_LANDSCAPE_FLIP,   /* 左 90° 回転 (240x320 縦 → 320x240 横) */
    };
    status_t s = ili9341_init(&s_tft);
    if (s == kStatus_Success) {
        (void)ili9341_fill_screen(&s_tft, ILI9341_BLACK);
        (void)ili9341_fill_rect(&s_tft, 0, 0, 320, 28, ILI9341_NAVY);
        (void)ili9341_draw_string(&s_tft, 6, 7, "IchiPing infer",
                                  ILI9341_WHITE, ILI9341_NAVY, 2);
    }
    return s;
}

/* ---- UI GPIO (トグル × 5 + EXEC ボタン + 推論中 LED) ----
 *
 * 入力 6 個は内蔵 pull-up + active-low。スイッチ ON 側で GND に短絡されて
 * GPIO_PinRead == 0 → 論理的に "OPEN/PUSHED"、OFF 側で pull-up により 1 →
 * "CLOSE/RELEASED"。LED は active-high 出力。
 *
 * トグル状態の bit 並びは bits[5] = (a, b, c, AB, BC) の順で、内部の
 * decode_doors / drive_all_seq と同じ index 規約。
 */

#define UI_DEBOUNCE_MS 5u           /* スイッチ debounce 期間 */
#define UI_POLL_INTERVAL_MS 20u     /* メインループ poll 間隔 */

static void ui_pin_input_pullup(PORT_Type *port, GPIO_Type *gpio, uint32_t pin)
{
    const port_pin_config_t in_cfg = {
        kPORT_PullUp,                kPORT_LowPullResistor,
        kPORT_FastSlewRate,          kPORT_PassiveFilterEnable,  /* チャタリング軽減 */
        kPORT_OpenDrainDisable,      kPORT_LowDriveStrength,
        kPORT_MuxAlt0,               /* GPIO */
        kPORT_InputBufferEnable,     kPORT_InputNormal,
        kPORT_UnlockRegister,
    };
    PORT_SetPinConfig(port, pin, &in_cfg);
    gpio_pin_config_t gpio_cfg = { kGPIO_DigitalInput, 0 };
    GPIO_PinInit(gpio, pin, &gpio_cfg);
}

static void ui_pin_output_low(PORT_Type *port, GPIO_Type *gpio, uint32_t pin)
{
    const port_pin_config_t out_cfg = {
        kPORT_PullDisable,           kPORT_LowPullResistor,
        kPORT_FastSlewRate,          kPORT_PassiveFilterDisable,
        kPORT_OpenDrainDisable,      kPORT_LowDriveStrength,
        kPORT_MuxAlt0,               /* GPIO */
        kPORT_InputBufferDisable,    kPORT_InputNormal,
        kPORT_UnlockRegister,
    };
    PORT_SetPinConfig(port, pin, &out_cfg);
    gpio_pin_config_t gpio_cfg = { kGPIO_DigitalOutput, 0 };
    GPIO_PinInit(gpio, pin, &gpio_cfg);
}

static void ui_gpio_init(void)
{
    /* MCXN947 の各 PORT クロックを念のため全部有効化 (他のドライバ init で
     * 既に on のはずだが、UI 専用で確実に立てておく)。 */
    CLOCK_EnableClock(kCLOCK_Port0);
    CLOCK_EnableClock(kCLOCK_Port1);

    /* 入力 (5 トグル + EXEC ボタン) */
    ui_pin_input_pullup(BOARD_UI_TGL_A_PORT,    BOARD_UI_TGL_A_GPIO,    BOARD_UI_TGL_A_PIN);
    ui_pin_input_pullup(BOARD_UI_TGL_B_PORT,    BOARD_UI_TGL_B_GPIO,    BOARD_UI_TGL_B_PIN);
    ui_pin_input_pullup(BOARD_UI_TGL_C_PORT,    BOARD_UI_TGL_C_GPIO,    BOARD_UI_TGL_C_PIN);
    ui_pin_input_pullup(BOARD_UI_TGL_AB_PORT,   BOARD_UI_TGL_AB_GPIO,   BOARD_UI_TGL_AB_PIN);
    ui_pin_input_pullup(BOARD_UI_TGL_BC_PORT,   BOARD_UI_TGL_BC_GPIO,   BOARD_UI_TGL_BC_PIN);
    ui_pin_input_pullup(BOARD_UI_BTN_EXEC_PORT, BOARD_UI_BTN_EXEC_GPIO, BOARD_UI_BTN_EXEC_PIN);

    /* 出力 LED (active-high) */
    ui_pin_output_low(BOARD_UI_LED_INFER_PORT, BOARD_UI_LED_INFER_GPIO, BOARD_UI_LED_INFER_PIN);
}

/* 5 トグルの現状態を bits_out[5] = (a, b, c, AB, BC) に詰める。
 * 反転 (active-high 扱い) — スイッチ ON (GND ショート、pin=LOW) で CLOSE (0)、
 * OFF (解放、pull-up HIGH) で OPEN (1)。 */
static void ui_read_toggles(uint8_t bits_out[5])
{
    bits_out[0] = (GPIO_PinRead(BOARD_UI_TGL_A_GPIO,  BOARD_UI_TGL_A_PIN)  == 0u) ? 0u : 1u;
    bits_out[1] = (GPIO_PinRead(BOARD_UI_TGL_B_GPIO,  BOARD_UI_TGL_B_PIN)  == 0u) ? 0u : 1u;
    bits_out[2] = (GPIO_PinRead(BOARD_UI_TGL_C_GPIO,  BOARD_UI_TGL_C_PIN)  == 0u) ? 0u : 1u;
    bits_out[3] = (GPIO_PinRead(BOARD_UI_TGL_AB_GPIO, BOARD_UI_TGL_AB_PIN) == 0u) ? 0u : 1u;
    bits_out[4] = (GPIO_PinRead(BOARD_UI_TGL_BC_GPIO, BOARD_UI_TGL_BC_PIN) == 0u) ? 0u : 1u;
}

/* EXEC ボタン: active-low、debounce 付きの "押下開始エッジ" 検出。
 * 押した瞬間 (released → pressed) で 1 回だけ true を返す。 */
static bool ui_read_exec_button_edge(void)
{
    static bool      s_last_pressed = false;
    static uint32_t  s_last_change_ms = 0;
    bool pressed_now = (GPIO_PinRead(BOARD_UI_BTN_EXEC_GPIO,
                                     BOARD_UI_BTN_EXEC_PIN) == 0u);
    /* debounce */
    if (pressed_now != s_last_pressed) {
        if (s_uptime_ms - s_last_change_ms >= UI_DEBOUNCE_MS) {
            s_last_change_ms = s_uptime_ms;
            bool prev = s_last_pressed;
            s_last_pressed = pressed_now;
            return (!prev) && pressed_now;   /* released → pressed エッジ */
        }
    } else {
        s_last_change_ms = s_uptime_ms;       /* 安定中はタイマリセット */
    }
    return false;
}

static inline void ui_set_infer_led(bool on)
{
    GPIO_PinWrite(BOARD_UI_LED_INFER_GPIO, BOARD_UI_LED_INFER_PIN, on ? 1u : 0u);
}

/* ---- TFT 表示更新 (推論結果 / サーボ実状態) ----
 *
 * 表示順 (どちらの行も) [c, BC, b, AB, a]。
 *
 *   inf 行 (推論結果):
 *     - s_state.pred_valid == false: "-----" を GREY で表示
 *     - 物理サーボ状態が推論時から変わった: pred_valid を自動 false に → "-----" に戻る
 *     - 推論結果あり (pred_valid == true): 各 bit を色付き表示
 *         正解 + 観測可能 GREEN / 誤 + 観測可能 RED
 *         正解 + 非観測  DARK_GREEN / 誤 + 非観測 DARK_RED
 *
 *   act 行 (サーボ実状態):
 *     - 常にサーボ current_deg から算出した bit (1=OPEN / 0=CLOSE)
 *     - 観測可能 ORANGE / 非観測 DARK_ORANGE
 *
 * 描画は sprite framebuffer + 行単位 diff で「変化があった行だけ」blit。
 * 何も変わってなければ SPI 送信ゼロ。
 */

#define TFT_INF_DASH_CHAR '-'

static void tft_draw_labels_once(void)
{
    if (s_tft.spi == NULL) return;
    static bool s_labels_drawn = false;
    if (s_labels_drawn) return;
    /* size=2 のラベル "inf" / "act" を 5 桁数字行 (size=5, 高さ 35) の左に
     * 縦中央寄せで描く。1 回描いたら以降不変なので flag で保護。 */
    const uint16_t py = 40;
    const uint16_t ty = py + 7u * 5u + 25u;
    const uint16_t label_y_off = (35u - 7u * 2u) / 2u;   /* (行高 35 - 文字高 14) / 2 */
    (void)ili9341_draw_string(&s_tft, 6, (uint16_t)(py + label_y_off), "inf",
                              ILI9341_WHITE, ILI9341_BLACK, 2);
    (void)ili9341_draw_string(&s_tft, 6, (uint16_t)(ty + label_y_off), "act",
                              ILI9341_WHITE, ILI9341_BLACK, 2);
    s_labels_drawn = true;
}

static void tft_show_state(void)
{
    if (s_tft.spi == NULL) return;
    tft_draw_labels_once();

    const uint8_t  sz   = 5;
    const uint16_t cw   = 6u * sz;            /* 文字送り 30 px */
    const uint16_t bx   = (uint16_t)((320u - 5u * cw) / 2u);  /* 数字 5 桁分の中央寄せ */
    const uint16_t py   = 40;                 /* inf 行 y */
    const uint16_t ty   = py + 7u * sz + 25u; /* act 行 y */
    const uint16_t row_w = 5u * cw;
    const uint16_t row_h = 7u * sz;

    const uint16_t DARK_GREEN  = 0x03E0u;
    const uint16_t DARK_RED    = 0x7800u;
    const uint16_t DARK_ORANGE = 0x7A80u;

    /* 表示順: 物理空間の左→右 = [c, BC, b, AB, a] */
    const uint8_t order[5] = {2, 4, 1, 3, 0};

    /* === 現サーボ位置 → bit === */
    const servo_config_t *cfg = servo_config_get();
    uint8_t actual[5];
    for (uint8_t i = 0; i < 5; i++) {
        float cur = s_state.current_deg[i];
        float dop = fabsf(cur - cfg->open_deg[i]);
        float dho = fabsf(cur - cfg->home_deg[i]);
        actual[i] = (dop < dho) ? 1u : 0u;
    }

    /* === pred_valid を auto-invalidate ===
     * 推論時の物理状態 (pred_servo_at) と現在 (actual) が違ったら推論結果は無効化。
     * これでトグル切替・PC OPEN/CLOSE 等の全経路で「結果が古くなる」のを検出できる。 */
    if (s_state.pred_valid) {
        for (uint8_t i = 0; i < 5; i++) {
            if (s_state.pred_servo_at[i] != actual[i]) {
                s_state.pred_valid = false;
                break;
            }
        }
    }

    /* === 観測可能性 (実 AB/BC ベース、cls14 縮約と同じロジック) === */
    bool obs[5];
    obs[0] = true;
    obs[1] = (actual[3] == 1u);
    obs[2] = (actual[3] == 1u) && (actual[4] == 1u);
    obs[3] = true;
    obs[4] = (actual[3] == 1u);

    /* 行単位 diff 用の last state */
    static bool     s_tft_inited      = false;
    static char     s_last_inf_ch[5]  = {0};
    static uint16_t s_last_inf_fg[5]  = {0};
    static uint8_t  s_last_act_ch[5]  = {0};
    static uint16_t s_last_act_fg[5]  = {0};

    /* === inf 行 === */
    char     inf_ch[5];
    uint16_t inf_fg[5];
    for (uint8_t k = 0; k < 5; k++) {
        uint8_t i = order[k];
        if (s_state.pred_valid) {
            inf_ch[k] = (char)('0' + s_state.pred_bits[i]);
            bool correct = (s_state.pred_bits[i] == actual[i]);
            if (correct) inf_fg[k] = obs[i] ? ILI9341_GREEN : DARK_GREEN;
            else         inf_fg[k] = obs[i] ? ILI9341_RED   : DARK_RED;
        } else {
            inf_ch[k] = TFT_INF_DASH_CHAR;
            inf_fg[k] = ILI9341_GREY;
        }
    }
    bool inf_changed = !s_tft_inited;
    for (uint8_t k = 0; !inf_changed && k < 5; k++) {
        if (s_last_inf_ch[k] != inf_ch[k] || s_last_inf_fg[k] != inf_fg[k]) {
            inf_changed = true;
        }
    }
    if (inf_changed) {
        fb_fill(ILI9341_BLACK, (uint32_t)row_w * row_h);
        for (uint8_t k = 0; k < 5; k++) {
            fb_draw_char(row_w, row_h,
                          (uint16_t)(k * cw), 0,
                          inf_ch[k],
                          inf_fg[k], ILI9341_BLACK, sz);
            s_last_inf_ch[k] = inf_ch[k];
            s_last_inf_fg[k] = inf_fg[k];
        }
        fb_flush(bx, py, row_w, row_h);
    }

    /* === act 行 === */
    uint8_t  act_ch[5];
    uint16_t act_fg[5];
    for (uint8_t k = 0; k < 5; k++) {
        uint8_t i = order[k];
        act_ch[k] = actual[i];
        act_fg[k] = obs[i] ? ILI9341_ORANGE : DARK_ORANGE;
    }
    bool act_changed = !s_tft_inited;
    for (uint8_t k = 0; !act_changed && k < 5; k++) {
        if (s_last_act_ch[k] != act_ch[k] || s_last_act_fg[k] != act_fg[k]) {
            act_changed = true;
        }
    }
    if (act_changed) {
        fb_fill(ILI9341_BLACK, (uint32_t)row_w * row_h);
        for (uint8_t k = 0; k < 5; k++) {
            fb_draw_char(row_w, row_h,
                          (uint16_t)(k * cw), 0,
                          (char)('0' + act_ch[k]),
                          act_fg[k], ILI9341_BLACK, sz);
            s_last_act_ch[k] = act_ch[k];
            s_last_act_fg[k] = act_fg[k];
        }
        fb_flush(bx, ty, row_w, row_h);
    }

    /* === 推論結果評価バナー (下段) ===
     * トグル真状態 (actual) と推論結果 (pred_bits) を比較して 3 段階で評価:
     *   32cls 一致 (全 5 bit 一致)   → "Complete Success"    (青)
     *   14cls 等価クラスのみ一致      → "Conditional Success" (緑)
     *   14cls も不一致               → "Failure"             (赤)
     *   推論結果が無効 (pred_valid=false) のときはバナーを消す。
     * 矩形は塗りつぶし、文字は白。fb スプライト (5250px) には収まらない大きさ
     * なので ili9341 直接描画 (fill_rect + draw_string)。verdict 変化時のみ再描画。 */
    {
        const uint16_t BANNER_X = 4;
        const uint16_t BANNER_Y = 175;
        const uint16_t BANNER_W = 312;
        const uint16_t BANNER_H = 46;

        /* verdict: 0=none(無効), 1=complete, 2=conditional, 3=failure */
        uint8_t verdict = 0u;
        if (s_state.pred_valid) {
            uint8_t pred_idx = 0u, act_idx = 0u;
            for (uint8_t i = 0; i < 5; i++) {
                pred_idx |= (uint8_t)((s_state.pred_bits[i] & 1u) << i);
                act_idx  |= (uint8_t)((actual[i] & 1u) << i);
            }
            if (pred_idx == act_idx) {
                verdict = 1u;   /* 32cls exact */
            } else {
                verdict = (strcmp(class_of_14(pred_idx),
                                  class_of_14(act_idx)) == 0) ? 2u : 3u;
            }
        }

        static uint8_t s_last_verdict = 0xFFu;
        if (verdict != s_last_verdict) {
            uint16_t    bg;
            const char *msg;
            switch (verdict) {
                case 1u:  bg = ILI9341_BLUE;  msg = "Complete Success";    break;
                case 2u:  bg = ILI9341_GREEN; msg = "Conditional Success"; break;
                case 3u:  bg = ILI9341_RED;   msg = "Failure";             break;
                default:  bg = ILI9341_BLACK; msg = NULL;                  break;
            }
            (void)ili9341_fill_rect(&s_tft, BANNER_X, BANNER_Y,
                                    BANNER_W, BANNER_H, bg);
            if (msg != NULL) {
                const uint8_t tsz = 2u;
                uint16_t tw  = (uint16_t)(strlen(msg) * 6u * tsz);
                uint16_t tx  = (tw < BANNER_W)
                                 ? (uint16_t)(BANNER_X + (BANNER_W - tw) / 2u)
                                 : BANNER_X;
                uint16_t tyb = (uint16_t)(BANNER_Y + (BANNER_H - 7u * tsz) / 2u);
                (void)ili9341_draw_string(&s_tft, tx, tyb, msg,
                                          ILI9341_WHITE, bg, tsz);
            }
            s_last_verdict = verdict;
        }
    }

    s_tft_inited = true;
}

/* トグルの bit を 1 個だけ強制的に servo に反映する (i = 0..4)。 */
static void apply_toggle_to_servo(uint8_t i, uint8_t new_bit)
{
    const servo_config_t *cfg = servo_config_get();
    const float target = (new_bit == 1u) ? cfg->open_deg[i] : cfg->home_deg[i];
    (void)servo_set_deg(&s_servo, i, target);
    s_state.current_deg[i] = target;
    delay_ms(INF_NAMED_MOVE_MS);
    (void)servo_set_off(&s_servo, i);
    uart_printf("INFO TGL %s %s deg=%d",
                ICHP_SERVO_NAMES[i],
                (new_bit == 1u) ? "OPEN" : "CLOSE",
                (int)target);
}

/* ---- main ---- */

int main(void)
{
    BOARD_InitHardware();
    systick_init_1ms();
    uart_init_bidi();

    uart_write_line("INFO BOOT IchiPing 10_inference starting");
    uart_printf("INFO BOOT build " __DATE__ " " __TIME__);

    pattern_lib_init();
    spk_eq_init();

    /* Audio */
    sai_mic_config_t mcfg = {
        .sai_base = BOARD_MIC_SAI_BASE, .sai_clk_hz = BOARD_MIC_SAI_CLK_FREQ,
        .sample_rate_hz = INF_SAMPLE_RATE, .bit_depth = 16,
    };
    sai_speaker_config_t scfg = {
        .sai_base = BOARD_SPK_SAI_BASE, .sai_clk_hz = BOARD_SPK_SAI_CLK_FREQ,
        .sample_rate_hz = INF_SAMPLE_RATE,
    };
    if (sai_mic_init(&s_mic, &mcfg) != kStatus_Success) {
        uart_write_line("ERR BOOT SAI mic -- halting"); for(;;) __WFI();
    }
    if (sai_speaker_init(&s_spk, &scfg) != kStatus_Success) {
        uart_write_line("ERR BOOT SAI speaker -- halting"); for(;;) __WFI();
    }
    uart_printf("INFO BOOT SAI OK rate=%uHz", (unsigned)INF_SAMPLE_RATE);

    /* I2C + servo
     *
     * 起動直後はサーボがどこにいるか分からない (前回セッションの mid-open 状態の
     * まま電源断したかもしれない) ので、論理的に「全閉」を信用するために物理的にも
     * 全閉まで駆動して current_deg[] を home に合わせる。並列駆動だと 5 個 SG90 の
     * 突入電流で位置が飛ぶことがあるので、09 と同じ逐次 CLOSE ALL を使う。 */
    i2c_init();
    {
        status_t s = servo_init(&s_servo, INF_I2C_BASE,
                                SERVO_DEFAULT_ADDR, SERVO_DEFAULT_FREQ_HZ);
        if (s != kStatus_Success) {
            uart_printf("WARN BOOT servo init status=%ld (continuing headless)", (long)s);
        } else {
            uart_printf("INFO BOOT servo OK addr=0x%02X", (unsigned)SERVO_DEFAULT_ADDR);
            (void)servo_config_init();
            uart_write_line("INFO BOOT servo CLOSE ALL (BC->AB->c->b->a sequential)");
            drive_all_seq(false);   /* CLOSE ALL — 完了後 current_deg は home に */
            uart_write_line("INFO BOOT servo home reached, PWM released");
        }
    }

    /* TFT */
    {
        status_t s = tft_init();
        if (s == kStatus_Success)
            uart_write_line("INFO BOOT TFT OK 240x320");
        else
            uart_printf("WARN BOOT TFT not detected status=%ld (headless)", (long)s);
    }

    /* Features + TFLite Micro 起動 */
    if (ichp_features_init(&s_feat, &s_rfft, s_hann_window,
                           s_seg_buf, s_fft_out, s_accum_power) != 0) {
        uart_write_line("ERR BOOT features init -- halting"); for(;;) __WFI();
    }
    uart_printf("INFO BOOT features OK (nfft=%u nbins=%u nseg=%u)",
                (unsigned)ICHP_FEAT_NFFT, (unsigned)ICHP_FEAT_N_BINS,
                (unsigned)ICHP_FEAT_N_SEGMENTS);

    ichp_tflite_status_t ts = ichp_tflite_init(ichp_model_data,
                                               ICHP_MODEL_DATA_LEN,
                                               s_tflite_arena,
                                               sizeof(s_tflite_arena));
    if (ts != ICHP_TFLITE_OK) {
        uart_printf("ERR BOOT tflite init status=%d -- halting", (int)ts);
        for(;;) __WFI();
    }
    float in_s, out_s; int32_t in_zp, out_zp;
    ichp_tflite_input_qparams(&in_s, &in_zp);
    ichp_tflite_output_qparams(&out_s, &out_zp);
    uart_printf("INFO BOOT tflite OK model=%u B arena=%u B in_q=(%d/1e6,zp=%d) out_q=(%d/1e6,zp=%d)",
                (unsigned)ICHP_MODEL_DATA_LEN,
                (unsigned)sizeof(s_tflite_arena),
                (int)lroundf(in_s * 1e6f),  (int)in_zp,
                (int)lroundf(out_s * 1e6f), (int)out_zp);

    /* baseline preload (factory) */
    (void)ichp_baseline_factory();
    uart_write_line("INFO BOOT baseline=factory (noise_low hardcoded)");

    /* --- UI GPIO 初期化 (5 トグル + EXEC ボタン + 推論中 LED) --- */
    ui_gpio_init();
    uart_write_line("INFO BOOT UI GPIO (toggles a/b/c/AB/BC + EXEC btn + LED) ready");

    /* --- 自動キャリブレーション用 default pattern push ---
     * PAT NOISE w_2000 2000 30 0 (= 2 s PRBS noise, vol 30%) を 0 番にセット。
     * これがないと BL CALIBRATE / INFER の capture_logmag が no_pattern で fail する。
     * PC コマンドから PAT_NOISE/PAT_SELECT で後で上書き可能。 */
    if (pattern_lib_add_noise("w_2000", 2000u, 30u, 0u)) {
        (void)pattern_lib_select(0u);
        uart_write_line("INFO BOOT default pattern push (PAT NOISE w_2000 2000 30 0)");
    } else {
        uart_write_line("WARN BOOT default pattern push failed");
    }

    /* --- 自動 BL CALIBRATE: 起動直後の "全閉" 状態を baseline として 10 frame 平均 --- */
    if (s_tft.spi != NULL) {
        (void)ili9341_fill_rect(&s_tft, 0, 32, 320, 208, ILI9341_BLACK);
        (void)ili9341_draw_string(&s_tft, 6, 100, "CALIBRATING...",
                                  ILI9341_YELLOW, ILI9341_BLACK, 3);
    }
    uart_write_line("INFO BOOT auto BL CALIBRATE 10 starting");
    ichp_cmd_lbuf_t boot_lb;
    ichp_cmd_lbuf_reset(&boot_lb);
    do_bl_calibrate(10, &boot_lb);
    /* do_bl_calibrate は自動で BL_MODE_LIVE に切替済。 */
    if (s_tft.spi != NULL) {
        (void)ili9341_fill_rect(&s_tft, 0, 32, 320, 208, ILI9341_BLACK);
    }

    /* --- 起動時トグル状態にサーボ同期 --- */
    {
        uint8_t init_tgl[5];
        ui_read_toggles(init_tgl);
        uart_printf("INFO BOOT toggle initial state a=%u b=%u c=%u AB=%u BC=%u",
                    (unsigned)init_tgl[0], (unsigned)init_tgl[1], (unsigned)init_tgl[2],
                    (unsigned)init_tgl[3], (unsigned)init_tgl[4]);
        const servo_config_t *cfg = servo_config_get();
        for (uint8_t i = 0; i < 5; i++) {
            /* 現在 current_deg は home (CLOSE) のはず。トグルが OPEN なら個別に駆動。 */
            if (init_tgl[i] == 1u && s_state.current_deg[i] != cfg->open_deg[i]) {
                apply_toggle_to_servo(i, 1u);
            }
        }
    }

    /* 初期 TFT: inf 行は "-----"、act 行は現サーボ状態 (boot sync 直後の値)。 */
    tft_show_state();

    uart_write_line("INFO IchiPing 10_inference ready (auto-calibrated, toggle-driven)");
    uart_write_line("INFO send PING / BL STATUS / INFER / INFER STREAM <N> / BL CALIBRATE [N]");
    uart_write_line("INFO local: toggle switches drive servos, EXEC btn triggers INFER");

    ichp_cmd_lbuf_t lb;
    ichp_cmd_lbuf_reset(&lb);
    /* ローカル UI 状態 (トグル前回値 + ポーリング時刻) */
    uint8_t  last_tgl[5];
    ui_read_toggles(last_tgl);   /* boot 後の同期で current_deg と一致しているはず */
    uint32_t last_poll_ms = s_uptime_ms;

    for (;;) {
        /* === UART 優先 (PC コマンド) === */
        if (LPUART_GetStatusFlags(INF_UART_BASE) & kLPUART_RxDataRegFullFlag) {
            uint8_t c = LPUART_ReadByte(INF_UART_BASE);
            if (ichp_cmd_lbuf_feed(&lb, (char)c)) {
                if (lb.overflow) {
                    uart_write_line("ERR LINE_TOO_LONG");
                    ichp_cmd_lbuf_reset(&lb); continue;
                }
                ichp_cmd_t cmd;
                const char *et = NULL, *ea = NULL;
                if (ichp_cmd_parse(lb.buf, &cmd, &et, &ea)) {
                    apply_cmd(&cmd, &lb);
                    /* PC からサーボ操作が来た可能性 → トグル基準値も最新の物理状態に
                     * 引き直して "PC vs トグル の不一致" 検出をリセット。 */
                    ui_read_toggles(last_tgl);
                } else {
                    uart_printf("ERR %s %s", et ? et : "PARSE", ea ? ea : "?");
                }
                ichp_cmd_lbuf_reset(&lb);
            }
        }

        /* === ローカル UI (トグル / EXEC ボタン) を一定間隔でポーリング ===
         * INFER STREAM 中はトグル / ボタン とも無視 (二重起動・サーボ駆動衝突回避)。 */
        if (!s_state.in_infer_stream
            && (s_uptime_ms - last_poll_ms) >= UI_POLL_INTERVAL_MS) {
            last_poll_ms = s_uptime_ms;

            /* トグル変化検出 */
            uint8_t now_tgl[5];
            ui_read_toggles(now_tgl);
            for (uint8_t i = 0; i < 5; i++) {
                if (now_tgl[i] != last_tgl[i]) {
                    apply_toggle_to_servo(i, now_tgl[i]);
                    last_tgl[i] = now_tgl[i];
                }
            }

            /* EXEC ボタン押下エッジ → 1 回推論 */
            if (ui_read_exec_button_edge()) {
                uart_write_line("INFO EXEC button pressed → INFER 1");
                do_infer_once();
            }

            /* TFT を再描画 (各種 servo 経路の変化を吸収、行単位 diff で
             * 何も変わってなければ実 SPI 送信ゼロ)。 */
            tft_show_state();
        }
    }
}
