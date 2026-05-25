/*
 * IchiPing — 11_smart_window firmware (本実装版)。
 *
 * 10_inference をベースに、本番運用向けに次の 2 つを追加した雨検知連動の
 * 推論ファーム。
 *
 *   1. ESP32 (M5Stamp Pico) 用 LPUART5 双方向チャネル
 *      OpenSDA debug UART (LPUART4 / 921600) と並行して LPUART5 (115200)
 *      も ichp_cmd を受け付け、応答は受信元の UART に返す。RAIN_DETECTED
 *      等の自発 event は両 UART へ broadcast。
 *      配線 spec の LPUART2/FC2 は servo I2C と衝突するため LPUART5/FC5
 *      に移動 (pin_mux.c 参照)。
 *
 *   2. YL-83 雨センサ (P3_4) ポーリング + 自動 INFER 発火
 *      乾燥=HIGH / 湿潤=LOW を内蔵プルアップで読む。乾→湿エッジが 3 連続
 *      確定したら自動的に INFER を 1 回回し、結果を両 UART に broadcast +
 *      TFT 表示。連発抑制のため 5 秒クールダウン。
 *      通知や自動 OPEN/CLOSE はこのファームでは行わない (ESP 側でやる想定)。
 *
 * 推論フロー・コマンド体系・サーボ駆動・pattern_lib は 10_inference と同一。
 * 差分は ESP UART 入力ハンドラ・雨センサ・broadcast 経路・TFT 状態ストリップのみ。
 */

#include "pin_mux.h"
#include "clock_config.h"
#include "board.h"
#include "fsl_debug_console.h"
#include "fsl_lpuart.h"
#include "fsl_lpi2c.h"
#include "fsl_sai.h"
#include "fsl_gpio.h"

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

/* ESP32 (M5Stamp Pico) 側は MicroPython REPL や Arduino IDE の標準である
 * 115200 bps を採用。MCU 側 LPUART5 (FC5) は app.h で base/clk_attach 済。 */
#ifndef INF_ESP_UART_BAUD
#define INF_ESP_UART_BAUD     115200u
#endif

#ifndef INF_I2C_BASE
#define INF_I2C_BASE          LPI2C2
#endif
#ifndef INF_I2C_CLK_FREQ
#define INF_I2C_CLK_FREQ      CLOCK_GetLPFlexCommClkFreq(2)
#endif
#define INF_I2C_BAUD          100000U
#define INF_TFT_SPI_BAUD      1000000U

/* ---- 雨センサ debounce / 自動 INFER ----
 * 100 ms 周期 polling × 3 連続一致でエッジ確定 (≈300 ms debounce)。
 * 一度発火したら 5 秒間は再発火しない (連打抑制 + INFER 実行時間の確保)。 */
#define RAIN_POLL_INTERVAL_MS   100u
#define RAIN_DEBOUNCE_N         3u
#define RAIN_COOLDOWN_MS        5000u

/* ---- TFLite Micro tensor arena ----
 * Neutron XL (108 KB model) の実効 arena は ~32 KB だが、念のため余裕を持つ。
 * 不足なら ichp_tflite_init が ERR_ALLOC を返すので増やす。 */
#define INF_TFLITE_ARENA_BYTES  (64u * 1024u)
__attribute__((aligned(16)))
static uint8_t s_tflite_arena[INF_TFLITE_ARENA_BYTES];

/* ---- Welch / features 用バッファ ---- */
#include "arm_math.h"
static arm_rfft_fast_instance_f32 s_rfft;
static float    s_hann_window[ICHP_FEAT_NFFT];
static float    s_seg_buf    [ICHP_FEAT_NFFT];
static float    s_fft_out    [ICHP_FEAT_NFFT];
static float    s_accum_power[ICHP_FEAT_NFFT/2u + 1u];
static float    s_logmag     [ICHP_FEAT_N_BINS];
static int8_t   s_input_int8 [ICHP_FEAT_N_BINS];
static int8_t   s_output_int8[ICHP_TFLITE_OUTPUT_LEN];

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
    uint16_t         result_seq;
    float            current_deg[ICHP_SERVO_COUNT];
} inf_state_t;

static inf_state_t s_state = {
    .volume_pct      = INF_DEFAULT_VOLUME,
    .bl_mode         = BL_MODE_FACTORY,
    .stop_requested  = false,
    .result_seq      = 0,
    .current_deg     = { 0.0f, 0.0f, 0.0f, 0.0f, 0.0f },
};

static servo_driver_t s_servo;
static sai_mic_t      s_mic;
static sai_speaker_t  s_spk;
static ili9341_t      s_tft;

/* ---- ESP UART / rain sensor 状態 ---- */
static uint32_t s_esp_rx_bytes        = 0;
static uint32_t s_esp_tx_bytes        = 0;
static bool     s_rain_is_wet         = false;
static bool     s_rain_pending        = false;
static uint8_t  s_rain_pending_count  = 0;
static uint32_t s_rain_event_count    = 0;
static uint32_t s_rain_last_event_ms  = 0;
static uint32_t s_rain_last_poll_ms   = 0;
static uint32_t s_tft_last_status_ms  = 0;

/* INFER STREAM / BL CALIBRATE 中に二重で INFER を回さないためのガード。
 * 雨検知トリガは inference busy 中は単にスキップする。 */
static volatile bool s_infer_busy = false;

/* 二つの UART それぞれ独立に行バッファを持つ。両方とも完全な ichp_cmd
 * チャネルとして対称に扱う (priority 差なし)。 */
static ichp_cmd_lbuf_t s_lb_dbg;
static ichp_cmd_lbuf_t s_lb_esp;

/* ---- SysTick / delay ---- */

static volatile uint32_t s_uptime_ms = 0;
void SysTick_Handler(void) { s_uptime_ms++; }
static void systick_init_1ms(void) { (void)SysTick_Config(SystemCoreClock / 1000u); }
static void delay_ms(uint32_t ms) {
    uint32_t end = s_uptime_ms + ms;
    while ((int32_t)(s_uptime_ms - end) < 0) { __WFI(); }
}

/* ---- UART low-level (バイト書き出しを per-UART で分離してカウンタを付ける) ---- */

static void dbg_uart_write_bytes(const uint8_t *p, size_t n)
{
    if (n == 0u) return;
    LPUART_WriteBlocking(INF_UART_BASE, p, n);
}

static void esp_uart_write_bytes(const uint8_t *p, size_t n)
{
    if (n == 0u) return;
    LPUART_WriteBlocking(BOARD_ESP_UART_BASEADDR, p, n);
    s_esp_tx_bytes += (uint32_t)n;
}

/* ---- Reply / broadcast helpers ----
 * REPLY_DEBUG : OpenSDA debug UART のみ
 * REPLY_ESP   : ESP32 LPUART5 のみ
 * REPLY_BCAST : 両方 (自発 event 用)
 *
 * コマンド応答は基本「受信した方の UART に返す」(REPLY_DEBUG / REPLY_ESP)。
 * 雨検知のような自発 event だけ REPLY_BCAST を使う。 */
typedef enum {
    REPLY_DEBUG = 0,
    REPLY_ESP   = 1,
    REPLY_BCAST = 2,
} reply_sink_t;

static void sink_write_buf(reply_sink_t s, const uint8_t *p, size_t n)
{
    if (s == REPLY_DEBUG || s == REPLY_BCAST) dbg_uart_write_bytes(p, n);
    if (s == REPLY_ESP   || s == REPLY_BCAST) esp_uart_write_bytes(p, n);
}

static void reply_line(reply_sink_t s, const char *msg)
{
    sink_write_buf(s, (const uint8_t *)msg, strlen(msg));
    static const uint8_t crlf[2] = { '\r', '\n' };
    sink_write_buf(s, crlf, 2u);
}

static void reply_printf(reply_sink_t s, const char *fmt, ...)
{
    char buf[200];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (n > 0) {
        if ((size_t)n >= sizeof(buf)) n = sizeof(buf) - 1;
        sink_write_buf(s, (const uint8_t *)buf, (size_t)n);
        static const uint8_t crlf[2] = { '\r', '\n' };
        sink_write_buf(s, crlf, 2u);
    }
}

/* boot/diag 用 — ESP UART 初期化前に呼ぶので debug 専用。 */
static void boot_line(const char *s) { reply_line(REPLY_DEBUG, s); }
static void boot_printf(const char *fmt, ...)
{
    char buf[200];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (n > 0) {
        if ((size_t)n >= sizeof(buf)) n = sizeof(buf) - 1;
        dbg_uart_write_bytes((const uint8_t *)buf, (size_t)n);
        static const uint8_t crlf[2] = { '\r', '\n' };
        dbg_uart_write_bytes(crlf, 2u);
    }
}

/* ---- UART init ---- */

static void dbg_uart_init(void)
{
    lpuart_config_t cfg;
    LPUART_GetDefaultConfig(&cfg);
    cfg.baudRate_Bps = INF_UART_BAUD;
    cfg.enableTx = true;
    cfg.enableRx = true;
    LPUART_Init(INF_UART_BASE, &cfg, BOARD_DEBUG_UART_CLK_FREQ);
}

static void esp_uart_init(void)
{
    lpuart_config_t cfg;
    LPUART_GetDefaultConfig(&cfg);
    cfg.baudRate_Bps = INF_ESP_UART_BAUD;
    cfg.enableTx = true;
    cfg.enableRx = true;
    LPUART_Init(BOARD_ESP_UART_BASEADDR, &cfg, BOARD_ESP_UART_CLK_FREQ);
}

/* ---- Full-duplex play + capture ---- */

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

/* ---- 5-bit door 状態 decode ---- */

static void decode_doors(uint8_t state_idx, uint8_t bits_out[5])
{
    for (uint8_t k = 0; k < 5; k++) {
        bits_out[k] = (state_idx >> k) & 1u;
    }
}

/* 14 等価クラス分類 (pc/training/dataset.py class_of と完全一致) */
static const char *class_of_14(uint8_t state_idx)
{
    uint8_t bits[5]; decode_doors(state_idx, bits);
    const uint8_t a = bits[0], b = bits[1], c = bits[2];
    const uint8_t ab = bits[3], bc = bits[4];
    if (ab == 0u) return (a == 0u) ? "A1" : "A2";
    if (bc == 0u) {
        static const char *table[4] = { "B1", "B2", "B3", "B4" };
        return table[(a | (b << 1)) & 0x3u];
    }
    static const char *ctable[8] = { "C1","C2","C3","C4","C5","C6","C7","C8" };
    const unsigned cidx = (unsigned)(a + 2u*b + 4u*c);
    return ctable[cidx];
}

/* ---- 逐次サーボ駆動 ---- */
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

/* ---- Baseline 選択 ---- */

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

static bool capture_logmag(uint32_t *cap_ms_out)
{
    const pattern_t *p = pattern_lib_get(g_pattern_lib.selected);
    if (!p) return false;
    uint32_t n = pattern_render(p, s_excite, INF_WINDOW_SAMP,
                                INF_SAMPLE_RATE, s_state.volume_pct);
    if (n == 0u) return false;
    if (n > INF_WINDOW_SAMP) n = INF_WINDOW_SAMP;
    spk_eq_apply(s_excite, n);

    uint32_t t0 = s_uptime_ms;
    play_and_capture(s_excite, s_audio, INF_WINDOW_SAMP);
    if (cap_ms_out) *cap_ms_out = s_uptime_ms - t0;

    ichp_features_logmag_psd(&s_feat, s_audio, s_logmag);
    return true;
}

/* TFT 下部 status strip (rain / ESP counters) を描き直す。INFER の主表示
 * (y=32..232) には触らない。s_tft 未初期化なら no-op。 */
static void tft_update_status(void)
{
    if (s_tft.spi == NULL) return;
    char line[40];
    (void)ili9341_fill_rect(&s_tft, 0, 232, 240, 88, ILI9341_BLACK);

    /* RAIN ラベル + 状態 */
    (void)ili9341_draw_string(&s_tft, 6, 236, "RAIN", ILI9341_GREY, ILI9341_BLACK, 2);
    if (s_rain_event_count > 0u) {
        uint32_t secs = (s_uptime_ms - s_rain_last_event_ms) / 1000u;
        snprintf(line, sizeof(line), "%s  %us ago  (#%u)",
                 s_rain_is_wet ? "WET" : "DRY",
                 (unsigned)secs, (unsigned)s_rain_event_count);
    } else {
        snprintf(line, sizeof(line), "%s  (no event)",
                 s_rain_is_wet ? "WET" : "DRY");
    }
    (void)ili9341_draw_string(&s_tft, 6, 256, line,
                              s_rain_is_wet ? ILI9341_CYAN : ILI9341_GREEN,
                              ILI9341_BLACK, 2);

    /* ESP UART カウンタ */
    (void)ili9341_draw_string(&s_tft, 6, 280, "ESP UART", ILI9341_GREY, ILI9341_BLACK, 2);
    snprintf(line, sizeof(line), "rx=%lu  tx=%lu",
             (unsigned long)s_esp_rx_bytes, (unsigned long)s_esp_tx_bytes);
    (void)ili9341_draw_string(&s_tft, 6, 300, line,
                              ILI9341_WHITE, ILI9341_BLACK, 2);

    s_tft_last_status_ms = s_uptime_ms;
}

/* ---- INFER 1 回 ---- */

static void do_infer_once(reply_sink_t sink)
{
    s_infer_busy = true;
    s_state.result_seq++;
    uint32_t cap_ms = 0;
    if (!capture_logmag(&cap_ms)) {
        reply_line(sink, "ERR INFER no_pattern (push patterns via PAT_* first)");
        s_infer_busy = false;
        return;
    }

    const float *bl = current_baseline();
    ichp_features_subtract_baseline(s_logmag, bl);

    float scale; int32_t zp;
    ichp_tflite_input_qparams(&scale, &zp);
    ichp_features_quantize_int8(s_logmag, scale, zp, s_input_int8);

    ichp_tflite_result_t r;
    ichp_tflite_status_t st = ichp_tflite_invoke(s_input_int8, s_output_int8, &r);
    if (st != ICHP_TFLITE_OK) {
        reply_printf(sink, "ERR INFER tflite status=%d", (int)st);
        s_infer_busy = false;
        return;
    }

    uint8_t bits[5];  decode_doors(r.argmax_idx, bits);
    uint8_t bits2[5]; decode_doors(r.second_idx, bits2);

    reply_printf(sink, "RESULT seq=%u "
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

    /* TFT 主表示 (y=32..232) */
    if (s_tft.spi != NULL) {
        char line[40];
        (void)ili9341_fill_rect(&s_tft, 0, 32, 240, 200, ILI9341_BLACK);
        snprintf(line, sizeof(line), "seq %5u", (unsigned)s_state.result_seq);
        (void)ili9341_draw_string(&s_tft, 6, 40, line, ILI9341_GREY, ILI9341_BLACK, 2);
        snprintf(line, sizeof(line), "s%u%u%u%u%u",
                 (unsigned)bits[0], (unsigned)bits[1], (unsigned)bits[2],
                 (unsigned)bits[3], (unsigned)bits[4]);
        (void)ili9341_draw_string(&s_tft, 6, 70, line, ILI9341_ORANGE, ILI9341_BLACK, 4);
        snprintf(line, sizeof(line), "idx=%u/32", (unsigned)r.argmax_idx);
        (void)ili9341_draw_string(&s_tft, 6, 115, line, ILI9341_GREY, ILI9341_BLACK, 2);
        snprintf(line, sizeof(line), "cls14=%s", class_of_14(r.argmax_idx));
        (void)ili9341_draw_string(&s_tft, 6, 140, line, ILI9341_GREEN, ILI9341_BLACK, 2);
        snprintf(line, sizeof(line), "%s baseline", current_baseline_name());
        (void)ili9341_draw_string(&s_tft, 6, 170, line, ILI9341_CYAN, ILI9341_BLACK, 2);
        snprintf(line, sizeof(line), "%u us", (unsigned)r.invoke_us);
        (void)ili9341_draw_string(&s_tft, 6, 195, line, ILI9341_GREY, ILI9341_BLACK, 2);
        /* INFER 完了で status strip も更新 */
        tft_update_status();
    }
    s_infer_busy = false;
}

/* ---- STOP polling: 両 UART 共に STOP を見張る ----
 *
 * INFER STREAM / BL CALIBRATE 途中で STOP を受けたい。debug でも ESP でも
 * 受信元は問わないので両 UART を走査する。STOP 以外の verb はループ完了
 * までは捨てる (バッファに溜めるのは複雑になるため割り切り)。 */
static void poll_stop_both(void)
{
    while (LPUART_GetStatusFlags(INF_UART_BASE) & kLPUART_RxDataRegFullFlag) {
        uint8_t c = LPUART_ReadByte(INF_UART_BASE);
        if (ichp_cmd_lbuf_feed(&s_lb_dbg, (char)c)) {
            ichp_cmd_t cmd;
            const char *et = NULL, *ea = NULL;
            if (ichp_cmd_parse(s_lb_dbg.buf, &cmd, &et, &ea)) {
                if (cmd.kind == ICHP_CMD_STOP) {
                    s_state.stop_requested = true;
                    reply_line(REPLY_DEBUG, "OK STOP requested");
                }
            }
            ichp_cmd_lbuf_reset(&s_lb_dbg);
        }
    }
    while (LPUART_GetStatusFlags(BOARD_ESP_UART_BASEADDR) & kLPUART_RxDataRegFullFlag) {
        uint8_t c = LPUART_ReadByte(BOARD_ESP_UART_BASEADDR);
        s_esp_rx_bytes++;
        if (ichp_cmd_lbuf_feed(&s_lb_esp, (char)c)) {
            ichp_cmd_t cmd;
            const char *et = NULL, *ea = NULL;
            if (ichp_cmd_parse(s_lb_esp.buf, &cmd, &et, &ea)) {
                if (cmd.kind == ICHP_CMD_STOP) {
                    s_state.stop_requested = true;
                    reply_line(REPLY_ESP, "OK STOP requested");
                }
            }
            ichp_cmd_lbuf_reset(&s_lb_esp);
        }
    }
}

static void do_infer_stream(reply_sink_t sink, int32_t n)
{
    s_state.stop_requested = false;
    reply_printf(sink, "OK INFER started count=%d", (int)n);
    int32_t done = 0;
    for (int32_t i = 0; i < n && !s_state.stop_requested; i++) {
        do_infer_once(sink);
        done++;
        poll_stop_both();
    }
    if (s_state.stop_requested) {
        reply_printf(sink, "OK INFER aborted done=%d", (int)done);
    } else {
        reply_printf(sink, "OK INFER done count=%d", (int)done);
    }
}

/* ---- BL CALIBRATE ---- */

static void do_bl_calibrate(reply_sink_t sink, int32_t n)
{
    s_state.stop_requested = false;
    s_infer_busy = true;
    reply_printf(sink, "OK BL calibrating frames=%d", (int)n);
    ichp_baseline_live_begin();
    int32_t collected = 0;
    for (int32_t i = 0; i < n && !s_state.stop_requested; i++) {
        uint32_t cap_ms = 0;
        if (!capture_logmag(&cap_ms)) {
            reply_line(sink, "ERR BL no_pattern");
            s_infer_busy = false;
            return;
        }
        ichp_baseline_live_accumulate(s_logmag);
        collected++;
        reply_printf(sink, "INFO BL frame %d/%d cap_ms=%u",
                     (int)collected, (int)n, (unsigned)cap_ms);
        poll_stop_both();
    }
    if (collected == 0) {
        reply_line(sink, "ERR BL no_frames");
        s_infer_busy = false;
        return;
    }
    ichp_baseline_live_commit((uint16_t)collected);
    s_state.bl_mode = BL_MODE_LIVE;
    const float *bl = ichp_baseline_live();
    float lo = bl[0], hi = bl[0], sum = 0.0f;
    for (uint32_t k = 0; k < ICHP_FEAT_N_BINS; k++) {
        if (bl[k] < lo) lo = bl[k];
        if (bl[k] > hi) hi = bl[k];
        sum += bl[k];
    }
    const float mean = sum / (float)ICHP_FEAT_N_BINS;
    reply_printf(sink, "OK BL calibrated frames=%d mode=live "
                 "bl_min=%d bl_max=%d bl_mean=%d (dB, int rounded)",
                 (int)collected, (int)lroundf(lo), (int)lroundf(hi),
                 (int)lroundf(mean));
    s_infer_busy = false;
}

/* ---- Status reply helpers ---- */

static void say_config(reply_sink_t sink)
{
    const pattern_t *p = pattern_lib_get(g_pattern_lib.selected);
    float scale; int32_t zp;
    ichp_tflite_input_qparams(&scale, &zp);
    reply_printf(sink, "OK CONFIG rate=%u window=%u pattern=%s sel_idx=%u count=%u "
                 "volume=%d baseline=%s in_scale_x1e6=%d in_zp=%d "
                 "n_invokes=%u last_us=%u esp_rx=%lu esp_tx=%lu "
                 "rain=%s rain_events=%u",
                 (unsigned)INF_SAMPLE_RATE, (unsigned)INF_WINDOW_SAMP,
                 p ? p->name : "(none)",
                 (unsigned)g_pattern_lib.selected,
                 (unsigned)g_pattern_lib.count,
                 (int)s_state.volume_pct,
                 current_baseline_name(),
                 (int)lroundf(scale * 1e6f), (int)zp,
                 (unsigned)ichp_tflite_total_invokes(),
                 (unsigned)ichp_tflite_last_invoke_us(),
                 (unsigned long)s_esp_rx_bytes, (unsigned long)s_esp_tx_bytes,
                 s_rain_is_wet ? "wet" : "dry",
                 (unsigned)s_rain_event_count);
}

static void say_home(reply_sink_t sink)
{
    const servo_config_t *cfg = servo_config_get();
    reply_printf(sink, "OK HOME a=%d b=%d c=%d AB=%d BC=%d",
                 (int)cfg->home_deg[0], (int)cfg->home_deg[1], (int)cfg->home_deg[2],
                 (int)cfg->home_deg[3], (int)cfg->home_deg[4]);
}
static void say_open(reply_sink_t sink)
{
    const servo_config_t *cfg = servo_config_get();
    reply_printf(sink, "OK OPEN a=%d b=%d c=%d AB=%d BC=%d",
                 (int)cfg->open_deg[0], (int)cfg->open_deg[1], (int)cfg->open_deg[2],
                 (int)cfg->open_deg[3], (int)cfg->open_deg[4]);
}

static void say_bl_status(reply_sink_t sink)
{
    reply_printf(sink, "OK BL mode=%s factory_available=1 live_calibrated=%d live_frames=%u",
                 current_baseline_name(),
                 (int)ichp_baseline_live_calibrated(),
                 (unsigned)ichp_baseline_live_frames());
}

/* ---- Command dispatch ----
 * sink: 受信した UART (REPLY_DEBUG or REPLY_ESP) を渡す。
 * 応答はすべて sink に返し、broadcast はしない (event のみ broadcast)。 */
static void apply_cmd(const ichp_cmd_t *cmd, reply_sink_t sink)
{
    switch (cmd->kind) {
        case ICHP_CMD_COMMENT: break;
        case ICHP_CMD_PING:        reply_line(sink, "OK PONG " __DATE__ " " __TIME__); break;
        case ICHP_CMD_GET_CONFIG:  say_config(sink); break;
        case ICHP_CMD_GET_HOME:    say_home(sink);   break;
        case ICHP_CMD_GET_OPEN:    say_open(sink);   break;
        case ICHP_CMD_SET_VOLUME:
            s_state.volume_pct = cmd->volume_pct;
            reply_printf(sink, "OK VOLUME %d", (int)cmd->volume_pct); break;
        case ICHP_CMD_SET_HOME:
            (void)servo_config_set_home(cmd->servo_idx, cmd->deg);
            reply_printf(sink, "OK HOME %s %d", ICHP_SERVO_NAMES[cmd->servo_idx], (int)cmd->deg); break;
        case ICHP_CMD_SET_OPEN:
            (void)servo_config_set_open(cmd->servo_idx, cmd->deg);
            reply_printf(sink, "OK OPEN %s %d", ICHP_SERVO_NAMES[cmd->servo_idx], (int)cmd->deg); break;

        /* Servo direct */
        case ICHP_CMD_SERVO: {
            status_t s = servo_set_deg(&s_servo, cmd->servo_idx, cmd->deg);
            if (s != kStatus_Success) {
                reply_printf(sink, "ERR SERVO_I2C %s status=%ld",
                             ICHP_SERVO_NAMES[cmd->servo_idx], (long)s); break;
            }
            s_state.current_deg[cmd->servo_idx] = cmd->deg;
            delay_ms(INF_NAMED_MOVE_MS);
            (void)servo_set_off(&s_servo, cmd->servo_idx);
            reply_printf(sink, "OK SERVO %s deg=%d",
                         ICHP_SERVO_NAMES[cmd->servo_idx], (int)cmd->deg);
            break;
        }
        case ICHP_CMD_SERVO_OFF:
            (void)servo_set_off(&s_servo, cmd->servo_idx);
            reply_printf(sink, "OK SERVO %s off", ICHP_SERVO_NAMES[cmd->servo_idx]); break;
        case ICHP_CMD_SERVO_ALL_OFF:
            (void)servo_all_off(&s_servo);
            reply_line(sink, "OK SERVO all off"); break;
        case ICHP_CMD_OPEN:
        case ICHP_CMD_CLOSE: {
            const servo_config_t *cfg = servo_config_get();
            const bool  is_open = (cmd->kind == ICHP_CMD_OPEN);
            const float target  = is_open ? cfg->open_deg[cmd->servo_idx]
                                          : cfg->home_deg[cmd->servo_idx];
            const char *verb    = is_open ? "OPEN" : "CLOSE";
            status_t s = servo_set_deg(&s_servo, cmd->servo_idx, target);
            if (s != kStatus_Success) {
                reply_printf(sink, "ERR SERVO_I2C %s %s status=%ld",
                             verb, ICHP_SERVO_NAMES[cmd->servo_idx], (long)s); break;
            }
            s_state.current_deg[cmd->servo_idx] = target;
            delay_ms(INF_NAMED_MOVE_MS);
            (void)servo_set_off(&s_servo, cmd->servo_idx);
            reply_printf(sink, "OK %s %s deg=%d", verb, ICHP_SERVO_NAMES[cmd->servo_idx], (int)target);
            break;
        }
        case ICHP_CMD_OPEN_ALL:
        case ICHP_CMD_CLOSE_ALL: {
            const bool is_open = (cmd->kind == ICHP_CMD_OPEN_ALL);
            const char *verb   = is_open ? "OPEN" : "CLOSE";
            drive_all_seq(is_open);
            reply_printf(sink, "OK %s all", verb); break;
        }

        /* Pattern */
        case ICHP_CMD_PAT_CLEAR:
            pattern_lib_clear();
            reply_line(sink, "OK PAT cleared"); break;
        case ICHP_CMD_PAT_PULSE_BEGIN:
            if (pattern_lib_pulse_begin(cmd->pat_name))
                reply_printf(sink, "OK PAT pulse begin name=%s", cmd->pat_name);
            else reply_line(sink, "ERR PAT lib_full");
            break;
        case ICHP_CMD_PAT_TONE:
            if (pattern_lib_pulse_add_tone(cmd->pat_a, cmd->pat_b, cmd->pat_c))
                reply_printf(sink, "OK PAT tone hz=%u on=%u off=%u",
                             (unsigned)cmd->pat_a, (unsigned)cmd->pat_b, (unsigned)cmd->pat_c);
            else reply_line(sink, "ERR PAT not_building_or_tone_full");
            break;
        case ICHP_CMD_PAT_PULSE_END: {
            uint8_t rep = (cmd->pat_i < 1) ? 1u : (uint8_t)cmd->pat_i;
            if (pattern_lib_pulse_end(rep))
                reply_printf(sink, "OK PAT pulse end count=%u", (unsigned)g_pattern_lib.count);
            else reply_line(sink, "ERR PAT pulse_end_failed");
            break;
        }
        case ICHP_CMD_PAT_SWEEP:
            if (pattern_lib_add_sweep(cmd->pat_name, cmd->pat_a, cmd->pat_b,
                                      cmd->pat_c, cmd->pat_d))
                reply_printf(sink, "OK PAT sweep name=%s", cmd->pat_name);
            else reply_line(sink, "ERR PAT lib_full");
            break;
        case ICHP_CMD_PAT_NOISE:
            if (pattern_lib_add_noise(cmd->pat_name, cmd->pat_a,
                                      (uint16_t)cmd->pat_b, (uint8_t)cmd->pat_c))
                reply_printf(sink, "OK PAT noise name=%s dur=%u vol=%u shape=%u",
                             cmd->pat_name, (unsigned)cmd->pat_a,
                             (unsigned)cmd->pat_b, (unsigned)cmd->pat_c);
            else reply_line(sink, "ERR PAT lib_full");
            break;
        case ICHP_CMD_PAT_INFO:
            reply_printf(sink, "OK PAT count=%u selected=%u",
                         (unsigned)g_pattern_lib.count, (unsigned)g_pattern_lib.selected);
            break;
        case ICHP_CMD_PAT_SELECT:
            if (cmd->pat_i < 0 || (uint8_t)cmd->pat_i >= g_pattern_lib.count) {
                reply_printf(sink, "ERR PAT index_out_of_range %d", (int)cmd->pat_i);
            } else if (pattern_lib_select((uint8_t)cmd->pat_i)) {
                const pattern_t *p = pattern_lib_get((uint8_t)cmd->pat_i);
                reply_printf(sink, "OK PAT select idx=%d name=%s",
                             (int)cmd->pat_i, p ? p->name : "?");
            } else reply_line(sink, "ERR PAT select_failed");
            break;

        /* EQ */
        case ICHP_CMD_EQ_ENABLE:  spk_eq_enable(true);  reply_line(sink, "OK EQ enabled"); break;
        case ICHP_CMD_EQ_DISABLE: spk_eq_enable(false); reply_line(sink, "OK EQ disabled"); break;
        case ICHP_CMD_EQ_RESET:   spk_eq_reset();       reply_line(sink, "OK EQ reset"); break;
        case ICHP_CMD_EQ_STATE:
            reply_printf(sink, "OK EQ state=%s",
                         spk_eq_is_enabled() ? "ENABLED" : "DISABLED"); break;

        /* INFER */
        case ICHP_CMD_INFER:           do_infer_once(sink); break;
        case ICHP_CMD_INFER_STREAM:    do_infer_stream(sink, cmd->infer_n); break;
        case ICHP_CMD_STOP:
            reply_line(sink, "OK STOP idle"); break;

        /* Baseline */
        case ICHP_CMD_BL_STATUS:  say_bl_status(sink); break;
        case ICHP_CMD_BL_FACTORY:
            s_state.bl_mode = BL_MODE_FACTORY;
            reply_line(sink, "OK BL mode=factory"); break;
        case ICHP_CMD_BL_LIVE:
            if (!ichp_baseline_live_calibrated()) {
                reply_line(sink, "ERR BL live_not_calibrated (run BL CALIBRATE first)");
            } else {
                s_state.bl_mode = BL_MODE_LIVE;
                reply_line(sink, "OK BL mode=live");
            }
            break;
        case ICHP_CMD_BL_CALIBRATE: do_bl_calibrate(sink, cmd->infer_n); break;
        case ICHP_CMD_BL_CLEAR:
            ichp_baseline_live_clear();
            s_state.bl_mode = BL_MODE_FACTORY;
            reply_line(sink, "OK BL cleared mode=factory"); break;

        case ICHP_CMD_EMIT: {
            int32_t idx = cmd->pat_i;
            if (idx < 0 || idx >= (int32_t)g_pattern_lib.count) {
                reply_printf(sink, "ERR EMIT index_out_of_range %d", (int)idx); break;
            }
            const pattern_t *p = pattern_lib_get((uint8_t)idx);
            uint32_t n = pattern_render(p, s_excite, INF_WINDOW_SAMP,
                                        INF_SAMPLE_RATE, s_state.volume_pct);
            spk_eq_apply(s_excite, n);
            (void)sai_speaker_play_blocking(&s_spk, s_excite, (size_t)n);
            reply_printf(sink, "OK EMIT idx=%d name=%s samples=%u",
                         (int)idx, p->name, (unsigned)n);
            break;
        }

        case ICHP_CMD_RUN:
            reply_line(sink, "ERR BAD_VERB RUN_not_supported_use_INFER"); break;
        case ICHP_CMD_SET_PIN:
        case ICHP_CMD_CLEAR_PIN:
        case ICHP_CMD_CLEAR_PINS:
        case ICHP_CMD_GET_PINS:
            reply_line(sink, "ERR BAD_VERB PIN_not_supported_in_inference_fw"); break;
        case ICHP_CMD_SET_REPEATS:
            reply_line(sink, "ERR BAD_VERB use_INFER_STREAM_count"); break;

        default:
            reply_line(sink, "ERR BAD_VERB"); break;
    }
}

/* ---- I2C / TFT init ---- */

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
        .rotation = ILI9341_ROT_PORTRAIT,
    };
    status_t s = ili9341_init(&s_tft);
    if (s == kStatus_Success) {
        (void)ili9341_fill_screen(&s_tft, ILI9341_BLACK);
        (void)ili9341_fill_rect(&s_tft, 0, 0, 240, 28, ILI9341_NAVY);
        (void)ili9341_draw_string(&s_tft, 6, 7, "IchiPing smart_win",
                                  ILI9341_WHITE, ILI9341_NAVY, 2);
    }
    return s;
}

/* ---- 雨センサ ----
 *
 * pin_mux.c で内蔵プルアップ + GPIO mode は設定済。direction (入力) のみ
 * ここで GPIO_PinInit する。読み出しは GPIO_PinRead で 0=wet / 1=dry。
 * 起動直後の状態 (例: 既に雨で湿っている) は event として扱わない。
 * 「乾→湿」の遷移だけがトリガで、5 秒クールダウン付き。 */
static bool rain_read_raw(void)
{
    return GPIO_PinRead(BOARD_RAIN_SENSOR_GPIO, BOARD_RAIN_SENSOR_PIN)
           == BOARD_RAIN_SENSOR_WET_LEVEL;
}

static void rain_init(void)
{
    gpio_pin_config_t in = { kGPIO_DigitalInput, 0 };
    GPIO_PinInit(BOARD_RAIN_SENSOR_GPIO, BOARD_RAIN_SENSOR_PIN, &in);

    /* 起動時 sampling: 5 回多数決で初期状態を決める (debounce 不要) */
    delay_ms(10);
    uint8_t wet = 0;
    for (uint8_t i = 0; i < 5; i++) {
        if (rain_read_raw()) wet++;
        delay_ms(2);
    }
    s_rain_is_wet         = (wet >= 3);
    s_rain_pending        = s_rain_is_wet;
    s_rain_pending_count  = RAIN_DEBOUNCE_N;  /* 即座に確定済とみなす */
    s_rain_last_poll_ms   = s_uptime_ms;
}

/* 100 ms 周期で 1 回サンプル。3 連続一致でエッジ確定。
 * 「dry→wet」確定時のみ true を返す (wet→dry は静かに状態更新するだけ)。 */
static bool rain_poll_edge(void)
{
    if ((s_uptime_ms - s_rain_last_poll_ms) < RAIN_POLL_INTERVAL_MS) return false;
    s_rain_last_poll_ms = s_uptime_ms;

    bool raw = rain_read_raw();
    if (raw == s_rain_pending) {
        if (s_rain_pending_count < 255u) s_rain_pending_count++;
    } else {
        s_rain_pending       = raw;
        s_rain_pending_count = 1;
    }
    if (s_rain_pending_count < RAIN_DEBOUNCE_N) return false;
    if (s_rain_pending == s_rain_is_wet) return false;

    /* state 変化確定 */
    s_rain_is_wet = s_rain_pending;
    if (!s_rain_is_wet) return false;   /* wet→dry はトリガ対象外 */

    /* dry→wet エッジ。クールダウン中ならスキップ。 */
    if (s_rain_event_count > 0u
        && (s_uptime_ms - s_rain_last_event_ms) < RAIN_COOLDOWN_MS) {
        return false;
    }
    s_rain_event_count++;
    s_rain_last_event_ms = s_uptime_ms;
    return true;
}

/* ---- main ---- */

int main(void)
{
    BOARD_InitHardware();
    systick_init_1ms();
    dbg_uart_init();
    esp_uart_init();

    boot_line("INFO BOOT IchiPing 11_smart_window starting");
    boot_printf("INFO BOOT build " __DATE__ " " __TIME__);
    boot_printf("INFO BOOT debug_uart=LPUART4@%u esp_uart=LPUART5@%u",
                (unsigned)INF_UART_BAUD, (unsigned)INF_ESP_UART_BAUD);

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
        boot_line("ERR BOOT SAI mic -- halting"); for(;;) __WFI();
    }
    if (sai_speaker_init(&s_spk, &scfg) != kStatus_Success) {
        boot_line("ERR BOOT SAI speaker -- halting"); for(;;) __WFI();
    }
    boot_printf("INFO BOOT SAI OK rate=%uHz", (unsigned)INF_SAMPLE_RATE);

    /* I2C + servo (10 と同様、起動時に CLOSE ALL で物理状態を確定) */
    i2c_init();
    {
        status_t s = servo_init(&s_servo, INF_I2C_BASE,
                                SERVO_DEFAULT_ADDR, SERVO_DEFAULT_FREQ_HZ);
        if (s != kStatus_Success) {
            boot_printf("WARN BOOT servo init status=%ld (continuing headless)", (long)s);
        } else {
            boot_printf("INFO BOOT servo OK addr=0x%02X", (unsigned)SERVO_DEFAULT_ADDR);
            (void)servo_config_init();
            boot_line("INFO BOOT servo CLOSE ALL (BC->AB->c->b->a sequential)");
            drive_all_seq(false);
            boot_line("INFO BOOT servo home reached, PWM released");
        }
    }

    /* TFT */
    {
        status_t s = tft_init();
        if (s == kStatus_Success)
            boot_line("INFO BOOT TFT OK 240x320");
        else
            boot_printf("WARN BOOT TFT not detected status=%ld (headless)", (long)s);
    }

    /* Features + TFLite Micro */
    if (ichp_features_init(&s_feat, &s_rfft, s_hann_window,
                           s_seg_buf, s_fft_out, s_accum_power) != 0) {
        boot_line("ERR BOOT features init -- halting"); for(;;) __WFI();
    }
    boot_printf("INFO BOOT features OK (nfft=%u nbins=%u nseg=%u)",
                (unsigned)ICHP_FEAT_NFFT, (unsigned)ICHP_FEAT_N_BINS,
                (unsigned)ICHP_FEAT_N_SEGMENTS);

    ichp_tflite_status_t ts = ichp_tflite_init(ichp_model_data,
                                               ICHP_MODEL_DATA_LEN,
                                               s_tflite_arena,
                                               sizeof(s_tflite_arena));
    if (ts != ICHP_TFLITE_OK) {
        boot_printf("ERR BOOT tflite init status=%d -- halting", (int)ts);
        for(;;) __WFI();
    }
    float in_s, out_s; int32_t in_zp, out_zp;
    ichp_tflite_input_qparams(&in_s, &in_zp);
    ichp_tflite_output_qparams(&out_s, &out_zp);
    boot_printf("INFO BOOT tflite OK model=%u B arena=%u B in_q=(%d/1e6,zp=%d) out_q=(%d/1e6,zp=%d)",
                (unsigned)ICHP_MODEL_DATA_LEN,
                (unsigned)sizeof(s_tflite_arena),
                (int)lroundf(in_s * 1e6f),  (int)in_zp,
                (int)lroundf(out_s * 1e6f), (int)out_zp);

    /* baseline preload */
    (void)ichp_baseline_factory();
    boot_line("INFO BOOT baseline=factory (noise_low hardcoded)");

    /* Rain sensor */
    rain_init();
    boot_printf("INFO BOOT rain sensor init initial=%s",
                s_rain_is_wet ? "wet" : "dry");

    boot_line("INFO IchiPing 11_smart_window ready");
    boot_line("INFO debug + esp UART both accept ichp_cmd; rain edge auto-INFER");

    /* ESP 側にも「起動しました」を投げて疎通の最初の手がかりを残す。
     * (debug は OpenSDA で常時繋がってる前提だが ESP は配線確認が要る) */
    reply_line(REPLY_ESP, "INFO IchiPing 11_smart_window ready (esp uart)");

    if (s_tft.spi != NULL) tft_update_status();

    ichp_cmd_lbuf_reset(&s_lb_dbg);
    ichp_cmd_lbuf_reset(&s_lb_esp);

    for (;;) {
        /* ---- debug UART ---- */
        if (LPUART_GetStatusFlags(INF_UART_BASE) & kLPUART_RxDataRegFullFlag) {
            uint8_t c = LPUART_ReadByte(INF_UART_BASE);
            if (ichp_cmd_lbuf_feed(&s_lb_dbg, (char)c)) {
                if (s_lb_dbg.overflow) {
                    reply_line(REPLY_DEBUG, "ERR LINE_TOO_LONG");
                    ichp_cmd_lbuf_reset(&s_lb_dbg);
                } else {
                    ichp_cmd_t cmd;
                    const char *et = NULL, *ea = NULL;
                    if (ichp_cmd_parse(s_lb_dbg.buf, &cmd, &et, &ea)) {
                        apply_cmd(&cmd, REPLY_DEBUG);
                    } else {
                        reply_printf(REPLY_DEBUG, "ERR %s %s",
                                     et ? et : "PARSE", ea ? ea : "?");
                    }
                    ichp_cmd_lbuf_reset(&s_lb_dbg);
                }
            }
        }

        /* ---- ESP UART ---- */
        if (LPUART_GetStatusFlags(BOARD_ESP_UART_BASEADDR) & kLPUART_RxDataRegFullFlag) {
            uint8_t c = LPUART_ReadByte(BOARD_ESP_UART_BASEADDR);
            s_esp_rx_bytes++;
            if (ichp_cmd_lbuf_feed(&s_lb_esp, (char)c)) {
                if (s_lb_esp.overflow) {
                    reply_line(REPLY_ESP, "ERR LINE_TOO_LONG");
                    ichp_cmd_lbuf_reset(&s_lb_esp);
                } else {
                    ichp_cmd_t cmd;
                    const char *et = NULL, *ea = NULL;
                    if (ichp_cmd_parse(s_lb_esp.buf, &cmd, &et, &ea)) {
                        apply_cmd(&cmd, REPLY_ESP);
                    } else {
                        reply_printf(REPLY_ESP, "ERR %s %s",
                                     et ? et : "PARSE", ea ? ea : "?");
                    }
                    ichp_cmd_lbuf_reset(&s_lb_esp);
                }
            }
        }

        /* ---- 雨センサ ----
         * INFER 中 (s_infer_busy) は polling 自体は続けるが新規トリガは出さない
         * (do_infer_once が再帰する形になるので)。クールダウンと相俟って ok。 */
        if (!s_infer_busy && rain_poll_edge()) {
            reply_line(REPLY_BCAST, "EVENT RAIN_DETECTED");
            tft_update_status();
            do_infer_once(REPLY_BCAST);
        }

        /* ---- TFT status strip を 1 秒に 1 回再描画 ----
         * ESP UART rx/tx カウンタ更新と「N 秒前」表示の更新用。
         * INFER 中は do_infer_once 側で tft_update_status を呼ぶので
         * 重複描画にならないよう s_infer_busy はスキップ。 */
        if (!s_infer_busy && s_tft.spi != NULL
            && (s_uptime_ms - s_tft_last_status_ms) >= 1000u) {
            tft_update_status();
        }
    }
}
