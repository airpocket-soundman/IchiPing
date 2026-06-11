/*
 * IchiPing — 09_collector firmware.
 *
 * Data acquisition station for v0.5 NN training. Integrates:
 *
 *   - SAI1 full-duplex audio (multiband click train TX + INMP441 RX)
 *     same hardware as 08_mic_speaker_test
 *   - PCA9685 + 5x SG90 servos (window_a/b/c, door_AB/BC)
 *     same hardware as 02_servo_test
 *   - LPUART4 (OpenSDA) bidirectional: ASCII commands inbound,
 *     ASCII responses + INFO lines + ICHP binary frames outbound
 *     (multiplexed; receiver scans for "ICHP" magic to find frame
 *     boundaries — see firmware/shared/include/ichp_cmd.h)
 *
 * Operating model
 * ---------------
 *   Boot               : load servo config (RAM default if no flash),
 *                        drive every servo to its home_deg, print PING-style
 *                        banner, then enter the command loop.
 *   Command loop       : poll LPUART4 RX byte-by-byte, accumulate lines,
 *                        parse with ichp_cmd_parse(), dispatch.
 *   RUN                : for i in 0..repeats-1:
 *                          - build pattern for trial i (SET PIN overrides
 *                            where present, otherwise the last commanded
 *                            mechanical angle from SERVO/OPEN/CLOSE; no
 *                            randomisation any more — the PC client owns
 *                            the state machine);
 *                          - drive servos, wait settle;
 *                          - fire excitation, capture audio;
 *                          - pack ICHP frame (servo_deg[] = actual angles set
 *                            this trial), write to UART.
 *                        STOP between trials aborts gracefully.
 *
 * UART note
 * ---------
 *   The OpenSDA bridge mostly cares about TX direction. To accept
 *   commands we enable LPUART RX as well and poll the kLPUART_RxDataReg-
 *   FullFlag in the main loop. No interrupt — keeps things simple, and the
 *   command path is not latency-sensitive.
 *
 * Build
 * -----
 *   This project shares the board config of 08_mic_speaker_test (same J1
 *   pinout) plus the I2C bus of 02_servo_test (LPI2C2 on D18/D19). To
 *   import in MCUXpresso for VS Code:
 *     1. Copy frdmmcxn947_cm33_core0/ from 08_mic_speaker_test into this
 *        project; add LPI2C2 to the pin_mux + clock_config (D18=P4_0
 *        Alt2, D19=P4_1 Alt2 — see hardware/wiring.md).
 *     2. Add to CMake sources: shared/source/{ichiping_frame, ichp_cmd,
 *        servo_config, sai_mic, sai_speaker, pca9685}.c + this main.c.
 *     3. Build, flash, then open pc/collector_client.py.
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
#include "collector_display.h"
#include "app.h"

#include <math.h>
#include <stdbool.h>
#include <string.h>
#include <stdio.h>

extern void BOARD_InitHardware(void);

/* ---- Audio constants ---- */

#define COL_SAMPLE_RATE       16000u
#define COL_WINDOW_MAX_MS     2000u            /* upper bound on per-frame recording window */
#define COL_WINDOW_SAMP       ((COL_SAMPLE_RATE * COL_WINDOW_MAX_MS) / 1000u)

/* Excitation waveform now comes from pattern_lib (pushed via PAT_* commands
 * from pc/patterns.yaml). The per-trial window length equals the selected
 * pattern's total_samples; it must fit in COL_WINDOW_SAMP. */

#define COL_DEFAULT_VOLUME    5                /* integer percent (0..100); small box, 5% ≈ -26 dB */
#define COL_DEFAULT_REPEATS   30
#define COL_SERVO_SETTLE_MS   400u             /* SG90 worst-case 60deg ~= 400 ms */
/* PWM hold time per servo move (servo_set_deg → off). */
#define COL_NAMED_MOVE_MS    500u

#ifndef COL_UART_BAUD
#define COL_UART_BAUD         921600u
#endif
#ifndef COL_UART_BASE
#define COL_UART_BASE         LPUART4
#endif

/* PCA9685 / LPI2C2 bus (matches 02_servo_test channel mapping). */
#ifndef COL_I2C_BASE
#define COL_I2C_BASE          LPI2C2
#endif
#ifndef COL_I2C_CLK_FREQ
#define COL_I2C_CLK_FREQ      CLOCK_GetLPFlexCommClkFreq(2)
#endif
#define COL_I2C_BAUD          100000U

/* ILI9341 TFT (matches 03_ili9341_test; macros resolve via app.h).
 * SPI baud kept at 1 MHz to match the value 03 has verified on real
 * hardware. Once 09 bring-up confirms display works end-to-end, this
 * can be raised to 20 MHz like 04_lvgl_test attempted (which is frozen
 * and therefore unverified at that speed). */
#define COL_TFT_SPI_BAUD      20000000U   /* 20 MHz — ILI9341 通常上限。1 MHz だと CLOSE ALL 内の TFT 更新 5 回で ~10 秒掛かり client が timeout する */

/* ---- Buffers ---- */

static int16_t s_excite[COL_WINDOW_SAMP];                 /* TX waveform, pre-rendered */
static uint8_t s_tx_buf[ICHP_HEADER_SIZE
                        + COL_WINDOW_SAMP * sizeof(int16_t)
                        + ICHP_CRC_SIZE];                 /* RX-into-payload + frame */

/* ---- Runtime state ---- */

typedef struct {
    int32_t           volume_pct;             /* 0..100 software gain on TX (integer percent) */
    int32_t           repeats;
    bool              pin_present[ICHP_SERVO_COUNT];
    float             pin_deg[ICHP_SERVO_COUNT];
    /* Last mechanical angle commanded for each servo. Used by RUN to
     * decide where to drive any servo without an explicit SET PIN — the
     * earlier randomised fill is gone; RUN now reproduces whatever the
     * operator (or plan client) last asked for. Initialised from
     * SERVO_CONFIG_DEFAULTS at boot and replaced by the home-drive on
     * startup; thereafter every SERVO/OPEN/CLOSE/SERVO_ALL_OFF-ish
     * dispatcher path and RUN's per-trial apply update it. */
    float             current_deg[ICHP_SERVO_COUNT];
    bool              stop_requested;
} col_state_t;

static col_state_t s_state = {
    .volume_pct     = COL_DEFAULT_VOLUME,
    .repeats        = COL_DEFAULT_REPEATS,
    .pin_present    = { false, false, false, false, false },
    .pin_deg        = { 0.0f, 0.0f, 0.0f, 0.0f, 0.0f },
    .current_deg    = { 0.0f, 0.0f, 0.0f, 0.0f, 0.0f },
    .stop_requested = false,
};

static servo_driver_t        s_servo;
static sai_mic_t             s_mic;
static sai_speaker_t         s_spk;
static ili9341_t             s_tft;
static collector_display_t   s_disp;

/* ---- SysTick ---- */

static volatile uint32_t s_uptime_ms = 0;
void SysTick_Handler(void) { s_uptime_ms++; }
static void systick_init_1ms(void) { (void)SysTick_Config(SystemCoreClock / 1000u); }
static void delay_ms(uint32_t ms) {
    uint32_t end = s_uptime_ms + ms;
    while ((int32_t)(s_uptime_ms - end) < 0) { __WFI(); }
}

/* ---- UART helpers ---- */

static void uart_init_bidi(void)
{
    lpuart_config_t cfg;
    LPUART_GetDefaultConfig(&cfg);
    cfg.baudRate_Bps = COL_UART_BAUD;
    cfg.enableTx     = true;
    cfg.enableRx     = true;
    LPUART_Init(COL_UART_BASE, &cfg, BOARD_DEBUG_UART_CLK_FREQ);
}

/* Write a single ASCII line + CR-LF, never producing the literal
 * "ICHP" 4-byte sequence (callers must use safe wording). */
static void uart_write_line(const char *s)
{
    LPUART_WriteBlocking(COL_UART_BASE, (const uint8_t *)s, strlen(s));
    static const uint8_t crlf[2] = { '\r', '\n' };
    LPUART_WriteBlocking(COL_UART_BASE, crlf, 2);
}

/* printf-style line writer using a stack buffer. */
static void uart_printf(const char *fmt, ...)
{
    char buf[160];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (n > 0) {
        if ((size_t)n >= sizeof(buf)) n = sizeof(buf) - 1;
        LPUART_WriteBlocking(COL_UART_BASE, (const uint8_t *)buf, (size_t)n);
        static const uint8_t crlf[2] = { '\r', '\n' };
        LPUART_WriteBlocking(COL_UART_BASE, crlf, 2);
    }
}

/* Format a float as "[+-]D.DDDDDD" using integer math only.
 * newlib-nano's default printf drops %f, so we can't use snprintf("%f", ...).
 * Clamps |f| to ~9999.999999 for safety. */
static void fmt_float_q6(char *buf, size_t cap, float f)
{
    if (cap == 0u) return;
    int negative = (f < 0.0f);
    if (negative) f = -f;
    if (f > 9999.999999f) f = 9999.999999f;
    uint32_t scaled = (uint32_t)(f * 1000000.0f + 0.5f);
    uint32_t whole  = scaled / 1000000u;
    uint32_t frac   = scaled % 1000000u;
    (void)snprintf(buf, cap, "%s%u.%06u",
                   negative ? "-" : "", (unsigned)whole, (unsigned)frac);
}

/* ---- Full-duplex play + capture (08 lift) ---- */

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

/* ---- Pattern + servo control ---- */


/* Fill `target_deg[5]` for one trial given pin state and home/open config. */
static void build_trial_pattern(float target_deg[ICHP_SERVO_COUNT])
{
    /* RUN no longer randomises unpinned channels — it just reproduces the
     * last commanded mechanical angle (the PC client is now responsible
     * for sequencing OPEN/CLOSE/SERVO commands before RUN to put the
     * model into the right state). SET PIN still overrides per channel
     * for the rare case where you want RUN to drive a specific raw angle
     * without going through OPEN/CLOSE. */
    for (uint8_t i = 0; i < ICHP_SERVO_COUNT; i++) {
        target_deg[i] = s_state.pin_present[i]
            ? s_state.pin_deg[i]
            : s_state.current_deg[i];
    }
}

/* ---- Command dispatch ---- */

static void say_config(void)
{
    const pattern_t *p = pattern_lib_get(g_pattern_lib.selected);
    uart_printf("OK CONFIG rate=%u max_window=%u pattern=%s sel_idx=%u count=%u volume=%d repeats=%d",
                (unsigned)COL_SAMPLE_RATE,
                (unsigned)COL_WINDOW_SAMP,
                p ? p->name : "(none)",
                (unsigned)g_pattern_lib.selected,
                (unsigned)g_pattern_lib.count,
                (int)s_state.volume_pct,
                (int)s_state.repeats);
}

static void say_pat_info(void)
{
    uart_printf("OK PAT count=%u selected=%u",
                (unsigned)g_pattern_lib.count, (unsigned)g_pattern_lib.selected);
    for (uint8_t i = 0; i < g_pattern_lib.count; i++) {
        const pattern_t *p = &g_pattern_lib.entries[i];
        uint32_t samp = pattern_total_samples(p, COL_SAMPLE_RATE);
        uint32_t ms   = (samp * 1000u) / COL_SAMPLE_RATE;
        if (p->kind == PATTERN_KIND_PULSE) {
            uart_printf("  [%u] pulse name=%s tones=%u repeat=%u dur=%ums",
                        (unsigned)i, p->name,
                        (unsigned)p->pulse.n_tones,
                        (unsigned)p->pulse.repeat,
                        (unsigned)ms);
        } else if (p->kind == PATTERN_KIND_SWEEP) {
            uart_printf("  [%u] sweep name=%s %u..%uHz sweep=%ums silence=%ums dur=%ums",
                        (unsigned)i, p->name,
                        (unsigned)p->sweep.start_hz, (unsigned)p->sweep.end_hz,
                        (unsigned)p->sweep.sweep_ms, (unsigned)p->sweep.silence_ms,
                        (unsigned)ms);
        }
    }
}

/* Servo angles are emitted as integer degrees: newlib-nano's default
 * printf drops %f formatting, and 1° precision is enough for servo
 * control. If sub-degree precision is ever needed, add -u _printf_float
 * to the linker flags and switch these back to %.1f. */

static void say_home(void)
{
    const servo_config_t *cfg = servo_config_get();
    uart_printf("OK HOME %s=%d %s=%d %s=%d %s=%d %s=%d",
                ICHP_SERVO_NAMES[0], (int)cfg->home_deg[0],
                ICHP_SERVO_NAMES[1], (int)cfg->home_deg[1],
                ICHP_SERVO_NAMES[2], (int)cfg->home_deg[2],
                ICHP_SERVO_NAMES[3], (int)cfg->home_deg[3],
                ICHP_SERVO_NAMES[4], (int)cfg->home_deg[4]);
}

static void say_open(void)
{
    const servo_config_t *cfg = servo_config_get();
    uart_printf("OK OPEN %s=%d %s=%d %s=%d %s=%d %s=%d",
                ICHP_SERVO_NAMES[0], (int)cfg->open_deg[0],
                ICHP_SERVO_NAMES[1], (int)cfg->open_deg[1],
                ICHP_SERVO_NAMES[2], (int)cfg->open_deg[2],
                ICHP_SERVO_NAMES[3], (int)cfg->open_deg[3],
                ICHP_SERVO_NAMES[4], (int)cfg->open_deg[4]);
}

static void say_pins(void)
{
    char buf[160];
    size_t off = (size_t)snprintf(buf, sizeof(buf), "OK PINS");
    for (uint8_t i = 0; i < ICHP_SERVO_COUNT; i++) {
        int n;
        if (s_state.pin_present[i]) {
            n = snprintf(buf + off, sizeof(buf) - off, " %s=%d",
                         ICHP_SERVO_NAMES[i], (int)s_state.pin_deg[i]);
        } else {
            n = snprintf(buf + off, sizeof(buf) - off, " %s=free",
                         ICHP_SERVO_NAMES[i]);
        }
        if (n <= 0 || (size_t)n >= sizeof(buf) - off) break;
        off += (size_t)n;
    }
    uart_write_line(buf);
}

/* Build + ship one ICHP audio frame. servo_deg holds the actual angles
 * applied this trial. n_samples is variable per pattern. */
static void send_frame(uint16_t seq, const float servo_deg[ICHP_SERVO_COUNT],
                       const int16_t *rec_payload, uint16_t n_samples)
{
    const size_t payload_bytes = (size_t)n_samples * sizeof(int16_t);
    const size_t framed = ICHP_HEADER_SIZE + payload_bytes + ICHP_CRC_SIZE;

    ichp_frame_header_t *h = (ichp_frame_header_t *)s_tx_buf;
    h->magic[0]    = ICHP_MAGIC_0;
    h->magic[1]    = ICHP_MAGIC_1;
    h->magic[2]    = ICHP_MAGIC_2;
    h->magic[3]    = ICHP_MAGIC_3;
    h->type        = ICHP_TYPE_AUDIO;
    h->reserved    = 0;
    h->seq         = seq;
    h->timestamp_ms = s_uptime_ms;
    h->n_samples   = n_samples;
    h->rate_hz     = COL_SAMPLE_RATE;
    for (int i = 0; i < ICHP_SERVO_COUNT; i++) { h->servo_deg[i] = servo_deg[i]; }

    /* rec_payload already sits at &s_tx_buf[ICHP_HEADER_SIZE]; no copy needed. */
    (void)rec_payload;

    const uint16_t crc = ichp_crc16_ccitt(s_tx_buf, ICHP_HEADER_SIZE + payload_bytes);
    s_tx_buf[framed - 2] = (uint8_t)(crc & 0xFFu);
    s_tx_buf[framed - 1] = (uint8_t)((crc >> 8) & 0xFFu);

    LPUART_WriteBlocking(COL_UART_BASE, s_tx_buf, framed);
}

/* Drain pending RX bytes briefly to check for STOP during a long RUN.
 * Non-blocking — only consumes what's already in the FIFO. */
static void poll_for_stop_only(ichp_cmd_lbuf_t *lb)
{
    while (LPUART_GetStatusFlags(COL_UART_BASE) & kLPUART_RxDataRegFullFlag) {
        uint8_t c = LPUART_ReadByte(COL_UART_BASE);
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

static void do_run(ichp_cmd_lbuf_t *lb)
{
    s_state.stop_requested = false;

    const pattern_t *p = pattern_lib_get(g_pattern_lib.selected);
    if (!p) {
        uart_write_line("ERR RUN no_pattern (push patterns first)");
        return;
    }
    uint32_t n_samp = pattern_render(p, s_excite, COL_WINDOW_SAMP,
                                     COL_SAMPLE_RATE, s_state.volume_pct);
    if (n_samp == 0u) {
        uart_write_line("ERR RUN render_failed");
        return;
    }
    if (n_samp > COL_WINDOW_SAMP) n_samp = COL_WINDOW_SAMP;

    /* Apply speaker EQ in-place (no-op when disabled, which is the default).
     * Sits between pattern_render and play_and_capture so all pattern kinds
     * get the same correction. EQ DISABLE before measurements that need raw
     * SPK/mic response (e.g. free-field calibration). */
    spk_eq_apply(s_excite, n_samp);

    uart_printf("OK RUN started repeats=%d pattern=%s samples=%u",
                (int)s_state.repeats, p->name, (unsigned)n_samp);

    int32_t frames = 0;
    for (int32_t i = 0; i < s_state.repeats && !s_state.stop_requested; i++) {
        float target_deg[ICHP_SERVO_COUNT];
        build_trial_pattern(target_deg);
        /* Diff-based per-channel move: only re-position the servos whose
         * target differs from the last commanded angle. Each moved ch gets
         * settle time then PWM release (same pattern as OPEN/CLOSE). When
         * the plan keeps the same door state across all repeats, every
         * trial after the first does zero servo writes and goes straight
         * to capture — PWM stays off, no hum, no current draw, no I²C
         * traffic during audio recording. */
        for (uint8_t ch = 0; ch < ICHP_SERVO_COUNT; ch++) {
            if (target_deg[ch] == s_state.current_deg[ch]) continue;
            (void)servo_set_deg(&s_servo, ch, target_deg[ch]);
            collector_display_set_servo(&s_disp, ch, target_deg[ch]);
            delay_ms(COL_NAMED_MOVE_MS);
            (void)servo_set_off(&s_servo, ch);
            s_state.current_deg[ch] = target_deg[ch];
        }
        collector_display_set_footer(&s_disp,
                                     p->name,
                                     s_state.volume_pct,
                                     i + 1, s_state.repeats);

        int16_t *rec_payload = (int16_t *)(s_tx_buf + ICHP_HEADER_SIZE);
        play_and_capture(s_excite, rec_payload, n_samp);

        send_frame((uint16_t)(i + 1), target_deg, rec_payload, (uint16_t)n_samp);
        frames++;

        /* Watch for STOP between trials only — the play/capture loop is
         * tight and can't be interrupted cleanly. */
        poll_for_stop_only(lb);
    }

    if (s_state.stop_requested) {
        uart_printf("OK RUN aborted frames=%d", (int)frames);
    } else {
        uart_printf("OK RUN done frames=%d", (int)frames);
    }
}

/* EMIT <idx>: play a pattern once, no recording, no servo movement.
 * Useful for testing the speaker / verifying a YAML edit before RUN.
 *
 * Uses sai_speaker_play_blocking (TX-only) rather than play_and_capture
 * (TX+RX) — same pattern as 07_speaker_test. Avoids any dependency on
 * the mic side: if INMP441 is mis-wired or RX FIFO stalls, EMIT still
 * works and we get sound proof that the speaker path is alive. */
static void do_emit(int32_t index)
{
    if (index < 0 || index >= (int32_t)g_pattern_lib.count) {
        uart_printf("ERR EMIT index_out_of_range %d (count=%u)",
                    (int)index, (unsigned)g_pattern_lib.count);
        return;
    }
    const pattern_t *p = pattern_lib_get((uint8_t)index);
    uint32_t n_samp = pattern_render(p, s_excite, COL_WINDOW_SAMP,
                                     COL_SAMPLE_RATE, s_state.volume_pct);
    if (n_samp == 0u) {
        uart_write_line("ERR EMIT render_failed");
        return;
    }
    /* Apply speaker EQ before emission (no-op when disabled). */
    spk_eq_apply(s_excite, n_samp);
    status_t s = sai_speaker_play_blocking(&s_spk, s_excite, (size_t)n_samp);
    if (s != kStatus_Success) {
        uart_printf("ERR EMIT speaker status=%ld", (long)s);
        return;
    }
    uart_printf("OK EMIT idx=%d name=%s samples=%u",
                (int)index, p->name, (unsigned)n_samp);
}

static void apply_cmd(const ichp_cmd_t *cmd, ichp_cmd_lbuf_t *lb)
{
    switch (cmd->kind) {
        case ICHP_CMD_COMMENT:
            /* INFO line from the PC side — accepted silently so it doesn't
             * pollute the trace with ERR BAD_VERB. No reply, no side
             * effects; PC uses it as a wire-trace marker only. */
            break;
        case ICHP_CMD_PING:
            uart_write_line("OK PONG " __DATE__ " " __TIME__);
            break;
        case ICHP_CMD_GET_CONFIG:    say_config(); break;
        case ICHP_CMD_GET_HOME:      say_home();   break;
        case ICHP_CMD_GET_OPEN:      say_open();   break;
        case ICHP_CMD_GET_PINS:      say_pins();   break;
        case ICHP_CMD_SET_VOLUME:
            s_state.volume_pct = cmd->volume_pct;
            uart_printf("OK VOLUME %d", (int)cmd->volume_pct);
            break;
        case ICHP_CMD_SET_REPEATS:
            s_state.repeats = cmd->repeats;
            uart_printf("OK REPEATS %d", (int)cmd->repeats);
            break;
        case ICHP_CMD_SET_PIN:
            s_state.pin_present[cmd->servo_idx] = true;
            s_state.pin_deg[cmd->servo_idx]     = cmd->deg;
            uart_printf("OK PIN %s %d", ICHP_SERVO_NAMES[cmd->servo_idx], (int)cmd->deg);
            break;
        case ICHP_CMD_CLEAR_PIN:
            s_state.pin_present[cmd->servo_idx] = false;
            uart_printf("OK PIN %s free", ICHP_SERVO_NAMES[cmd->servo_idx]);
            break;
        case ICHP_CMD_CLEAR_PINS:
            for (uint8_t i = 0; i < ICHP_SERVO_COUNT; i++) s_state.pin_present[i] = false;
            uart_write_line("OK PINS cleared");
            break;
        case ICHP_CMD_SET_HOME:
            (void)servo_config_set_home(cmd->servo_idx, cmd->deg);
            uart_printf("OK HOME %s %d", ICHP_SERVO_NAMES[cmd->servo_idx], (int)cmd->deg);
            break;
        case ICHP_CMD_SET_OPEN:
            (void)servo_config_set_open(cmd->servo_idx, cmd->deg);
            uart_printf("OK OPEN %s %d", ICHP_SERVO_NAMES[cmd->servo_idx], (int)cmd->deg);
            break;
        case ICHP_CMD_SAVE_HOME: {
            int r = servo_config_save_flash();
            if (r == 0) uart_write_line("OK HOME saved");
            else        uart_printf("ERR SAVE_HOME code=%d", r);
            break;
        }
        case ICHP_CMD_SERVO: {
            status_t s = servo_set_deg(&s_servo, cmd->servo_idx, cmd->deg);
            if (s != kStatus_Success) {
                uart_printf("ERR SERVO_I2C %s status=%ld",
                            ICHP_SERVO_NAMES[cmd->servo_idx], (long)s);
                break;
            }
            s_state.current_deg[cmd->servo_idx] = cmd->deg;
            collector_display_set_servo(&s_disp, cmd->servo_idx, cmd->deg);
            /* Hold long enough for the SG90 to settle, then release PWM so
             * the channel stops drawing holding current and humming. Same
             * pattern as OPEN/CLOSE. RUN drives servos via a separate
             * code path that keeps them energised through capture. */
            delay_ms(COL_NAMED_MOVE_MS);
            (void)servo_set_off(&s_servo, cmd->servo_idx);
            uart_printf("OK SERVO %s deg=%d",
                        ICHP_SERVO_NAMES[cmd->servo_idx], (int)cmd->deg);
            break;
        }
        case ICHP_CMD_SERVO_OFF: {
            status_t s = servo_set_off(&s_servo, cmd->servo_idx);
            if (s != kStatus_Success) {
                uart_printf("ERR SERVO_I2C %s off status=%ld",
                            ICHP_SERVO_NAMES[cmd->servo_idx], (long)s);
            } else {
                uart_printf("OK SERVO %s off", ICHP_SERVO_NAMES[cmd->servo_idx]);
            }
            break;
        }
        case ICHP_CMD_SERVO_ALL_OFF: {
            status_t s = servo_all_off(&s_servo);
            if (s != kStatus_Success) {
                uart_printf("ERR SERVO_I2C all off status=%ld", (long)s);
            } else {
                uart_write_line("OK SERVO all off");
            }
            break;
        }
        case ICHP_CMD_OPEN:
        case ICHP_CMD_CLOSE: {
            const servo_config_t *cfg = servo_config_get();
            const bool   is_open = (cmd->kind == ICHP_CMD_OPEN);
            const float  target  = is_open ? cfg->open_deg[cmd->servo_idx]
                                           : cfg->home_deg[cmd->servo_idx];
            const char  *verb    = is_open ? "OPEN" : "CLOSE";
            status_t s = servo_set_deg(&s_servo, cmd->servo_idx, target);
            if (s != kStatus_Success) {
                uart_printf("ERR SERVO_I2C %s %s status=%ld",
                            verb, ICHP_SERVO_NAMES[cmd->servo_idx], (long)s);
                break;
            }
            s_state.current_deg[cmd->servo_idx] = target;
            collector_display_set_servo(&s_disp, cmd->servo_idx, target);
            /* Hold PWM long enough for the SG90 to traverse the full swing,
             * then release the channel so it stops drawing holding current
             * and humming. Display keeps showing the commanded angle. */
            delay_ms(COL_NAMED_MOVE_MS);
            (void)servo_set_off(&s_servo, cmd->servo_idx);
            uart_printf("OK %s %s deg=%d",
                        verb, ICHP_SERVO_NAMES[cmd->servo_idx], (int)target);
            break;
        }
        case ICHP_CMD_OPEN_ALL:
        case ICHP_CMD_CLOSE_ALL: {
            const servo_config_t *cfg = servo_config_get();
            const bool  is_open = (cmd->kind == ICHP_CMD_OPEN_ALL);
            const float *targets = is_open ? cfg->open_deg : cfg->home_deg;
            const char  *verb    = is_open ? "OPEN" : "CLOSE";
            /* Sequential drive, one channel at a time, to spread the SG90
             * inrush over time instead of triggering a 5-way V+ sag. The
             * order matters for the IchiPing model:
             *   OPEN  : a → b → c → AB → BC  (windows first, then doors,
             *                                  so the room is ventilated
             *                                  before the doors swing)
             *   CLOSE : BC → AB → c → b → a  (doors first, then windows,
             *                                  airlock-style: the outermost
             *                                  panels close before the
             *                                  inner ones)
             * Total time = ICHP_SERVO_COUNT × COL_NAMED_MOVE_MS. */
            bool failed = false;
            for (uint8_t step = 0; step < ICHP_SERVO_COUNT; step++) {
                uint8_t i = is_open
                    ? step
                    : (uint8_t)(ICHP_SERVO_COUNT - 1u - step);
                status_t s = servo_set_deg(&s_servo, i, targets[i]);
                if (s != kStatus_Success) {
                    uart_printf("ERR SERVO_I2C %s %s status=%ld",
                                verb, ICHP_SERVO_NAMES[i], (long)s);
                    /* Best-effort cleanup: release whatever is still
                     * energised so we don't leave channels mid-move. */
                    (void)servo_all_off(&s_servo);
                    failed = true;
                    break;
                }
                s_state.current_deg[i] = targets[i];
                collector_display_set_servo(&s_disp, i, targets[i]);
                delay_ms(COL_NAMED_MOVE_MS);
                (void)servo_set_off(&s_servo, i);
            }
            if (!failed) {
                uart_printf("OK %s all", verb);
            }
            break;
        }
        case ICHP_CMD_RUN:           do_run(lb);   break;
        case ICHP_CMD_STOP:
            /* If we get STOP outside a RUN, just acknowledge. */
            uart_write_line("OK STOP idle");
            break;
        case ICHP_CMD_PAT_CLEAR:
            pattern_lib_clear();
            uart_write_line("OK PAT cleared");
            break;
        case ICHP_CMD_PAT_PULSE_BEGIN:
            if (pattern_lib_pulse_begin(cmd->pat_name)) {
                uart_printf("OK PAT pulse begin name=%s", cmd->pat_name);
            } else {
                uart_write_line("ERR PAT lib_full");
            }
            break;
        case ICHP_CMD_PAT_TONE:
            if (pattern_lib_pulse_add_tone(cmd->pat_a, cmd->pat_b, cmd->pat_c)) {
                uart_printf("OK PAT tone hz=%u on=%u off=%u",
                            (unsigned)cmd->pat_a, (unsigned)cmd->pat_b, (unsigned)cmd->pat_c);
            } else {
                uart_write_line("ERR PAT not_building_or_tone_full");
            }
            break;
        case ICHP_CMD_PAT_PULSE_END: {
            uint8_t rep = (cmd->pat_i < 1) ? 1u : (uint8_t)cmd->pat_i;
            if (pattern_lib_pulse_end(rep)) {
                uart_printf("OK PAT pulse end count=%u",
                            (unsigned)g_pattern_lib.count);
            } else {
                uart_write_line("ERR PAT pulse_end_failed");
            }
            break;
        }
        case ICHP_CMD_PAT_SWEEP:
            if (pattern_lib_add_sweep(cmd->pat_name, cmd->pat_a, cmd->pat_b,
                                      cmd->pat_c, cmd->pat_d)) {
                uart_printf("OK PAT sweep name=%s start=%u end=%u sweep=%u silence=%u",
                            cmd->pat_name,
                            (unsigned)cmd->pat_a, (unsigned)cmd->pat_b,
                            (unsigned)cmd->pat_c, (unsigned)cmd->pat_d);
            } else {
                uart_write_line("ERR PAT lib_full");
            }
            break;
        case ICHP_CMD_PAT_NOISE:
            if (pattern_lib_add_noise(cmd->pat_name,
                                      cmd->pat_a,                  /* duration_ms */
                                      (uint16_t)cmd->pat_b,        /* volume_pct */
                                      (uint8_t)cmd->pat_c)) {      /* shape */
                uart_printf("OK PAT noise name=%s dur=%u vol=%u shape=%u",
                            cmd->pat_name,
                            (unsigned)cmd->pat_a,
                            (unsigned)cmd->pat_b,
                            (unsigned)cmd->pat_c);
            } else {
                uart_write_line("ERR PAT lib_full");
            }
            break;
        case ICHP_CMD_PAT_INFO:
            say_pat_info();
            break;
        case ICHP_CMD_PAT_SELECT:
            if (cmd->pat_i < 0 || (uint8_t)cmd->pat_i >= g_pattern_lib.count) {
                uart_printf("ERR PAT index_out_of_range %d (count=%u)",
                            (int)cmd->pat_i, (unsigned)g_pattern_lib.count);
            } else if (pattern_lib_select((uint8_t)cmd->pat_i)) {
                const pattern_t *p = pattern_lib_get((uint8_t)cmd->pat_i);
                uart_printf("OK PAT select idx=%d name=%s",
                            (int)cmd->pat_i, p ? p->name : "?");
            } else {
                uart_write_line("ERR PAT select_failed");
            }
            break;
        case ICHP_CMD_EMIT:
            do_emit(cmd->pat_i);
            break;
        case ICHP_CMD_EQ_ENABLE:
            spk_eq_enable(true);
            uart_write_line("OK EQ enabled");
            break;
        case ICHP_CMD_EQ_DISABLE:
            spk_eq_enable(false);
            uart_write_line("OK EQ disabled");
            break;
        case ICHP_CMD_EQ_RESET:
            spk_eq_reset();
            uart_write_line("OK EQ reset (defaults reloaded, state cleared)");
            break;
        case ICHP_CMD_EQ_SET:
            if (spk_eq_set_stage(cmd->eq_stage,
                                 cmd->eq_b0, cmd->eq_b1, cmd->eq_b2,
                                 cmd->eq_a1, cmd->eq_a2)) {
                uart_printf("OK EQ set stage=%u", (unsigned)cmd->eq_stage);
            } else {
                uart_printf("ERR EQ stage_out_of_range %u (max=%u)",
                            (unsigned)cmd->eq_stage, (unsigned)(SPK_EQ_NUM_STAGES - 1u));
            }
            break;
        case ICHP_CMD_EQ_GET: {
            spk_eq_stage_coefs_t c;
            char b0s[20], b1s[20], b2s[20], a1s[20], a2s[20];
            for (uint8_t i = 0; i < SPK_EQ_NUM_STAGES; i++) {
                if (spk_eq_get_stage(i, &c)) {
                    fmt_float_q6(b0s, sizeof(b0s), c.b0);
                    fmt_float_q6(b1s, sizeof(b1s), c.b1);
                    fmt_float_q6(b2s, sizeof(b2s), c.b2);
                    fmt_float_q6(a1s, sizeof(a1s), c.a1);
                    fmt_float_q6(a2s, sizeof(a2s), c.a2);
                    uart_printf("OK EQ stage=%u b0=%s b1=%s b2=%s a1=%s a2=%s",
                                (unsigned)i, b0s, b1s, b2s, a1s, a2s);
                }
            }
            break;
        }
        case ICHP_CMD_EQ_STATE:
            uart_printf("OK EQ state=%s stages=%u",
                        spk_eq_is_enabled() ? "ENABLED" : "DISABLED",
                        (unsigned)SPK_EQ_NUM_STAGES);
            break;
        default:
            uart_write_line("ERR BAD_VERB");
            break;
    }
}

/* ---- I2C init (for PCA9685 / LU9685) ---- */

static void i2c_init(void)
{
    lpi2c_master_config_t i2c;
    LPI2C_MasterGetDefaultConfig(&i2c);
    i2c.baudRate_Hz = COL_I2C_BAUD;
    LPI2C_MasterInit(COL_I2C_BASE, &i2c, COL_I2C_CLK_FREQ);
}

/* Probe every 7-bit I²C address (1..119) by issuing a zero-byte write
 * and reporting which ones ACK. Mirrors 02_servo_test::i2c_scan so the
 * same diagnostic is available without an external tool. */
static void i2c_scan_print(void)
{
    int found = 0;
    char line[80];
    int len = snprintf(line, sizeof(line), "INFO BOOT I2C scan:");
    for (uint8_t a = 1u; a < 0x78u; a++) {
        lpi2c_master_transfer_t xfer = {
            .flags          = (uint32_t)kLPI2C_TransferDefaultFlag,
            .slaveAddress   = (uint16_t)a,
            .direction      = kLPI2C_Write,
            .subaddress     = 0u,
            .subaddressSize = 0u,
            .data           = NULL,
            .dataSize       = 0u,
        };
        if (LPI2C_MasterTransferBlocking(COL_I2C_BASE, &xfer) == kStatus_Success) {
            int n = snprintf(line + len, sizeof(line) - (size_t)len,
                             " 0x%02X", (unsigned)a);
            if (n > 0 && (size_t)(len + n) < sizeof(line)) len += n;
            found++;
        }
    }
    if (found == 0) {
        uart_write_line("INFO BOOT I2C scan: no devices ACK");
        uart_write_line("INFO check pull-ups on D18/D19, 5V on V+, and ground");
    } else {
        uart_write_line(line);
    }
}

/* ---- TFT init (for ILI9341 status display) ---- */

static status_t tft_init(void)
{
    /* GPIOs for CS / RES / DC / BL are driven by hardware_init.c (copy
     * from 03_ili9341_test). Mark them as outputs idle-high. */
    gpio_pin_config_t out = { kGPIO_DigitalOutput, 1 };
    GPIO_PinInit(BOARD_ILI_CS_GPIO,  BOARD_ILI_CS_PIN,  &out);
    GPIO_PinInit(BOARD_ILI_RES_GPIO, BOARD_ILI_RES_PIN, &out);
    GPIO_PinInit(BOARD_ILI_DC_GPIO,  BOARD_ILI_DC_PIN,  &out);
    GPIO_PinInit(BOARD_ILI_BL_GPIO,  BOARD_ILI_BL_PIN,  &out);

    s_tft = (ili9341_t){
        .spi          = BOARD_ILI_SPI_BASE,
        .spi_clk_hz   = BOARD_ILI_SPI_CLK_FREQ,
        .spi_baud_hz  = COL_TFT_SPI_BAUD,
        .cs_gpio      = BOARD_ILI_CS_GPIO,  .cs_pin  = BOARD_ILI_CS_PIN,
        .dc_gpio      = BOARD_ILI_DC_GPIO,  .dc_pin  = BOARD_ILI_DC_PIN,
        .res_gpio     = BOARD_ILI_RES_GPIO, .res_pin = BOARD_ILI_RES_PIN,
        .bl_gpio      = BOARD_ILI_BL_GPIO,  .bl_pin  = BOARD_ILI_BL_PIN,
        .rotation     = ILI9341_ROT_PORTRAIT,
    };
    status_t s = ili9341_init(&s_tft);
    if (s == kStatus_Success) {
        collector_display_init(&s_disp, &s_tft);
    }
    /* If init fails (likely cause: TFT not wired), the collector still
     * runs headless — caller logs it and carries on. */
    return s;
}

/* ---- main ---- */

int main(void)
{
    BOARD_InitHardware();
    systick_init_1ms();

    uart_init_bidi();

    /* Boot banner first so the operator can match diagnostics against the
     * build they actually have on the board. */
    uart_write_line("INFO BOOT IchiPing 09_collector starting");
    uart_printf("INFO BOOT build " __DATE__ " " __TIME__);

    /* Pattern library: empty at boot. PC client pushes pc/patterns.yaml
     * via PAT_* commands once the connection comes up. */
    pattern_lib_init();
    uart_printf("INFO BOOT pattern_lib ready (max %u patterns x %u tones)",
                (unsigned)PATTERN_LIB_MAX_PATTERNS, (unsigned)PATTERN_MAX_TONES);

    /* Speaker EQ: identity defaults + disabled. Out of the box the TX
     * signal path is bit-for-bit unchanged from pre-EQ firmware. Host
     * must EQ SET ... + EQ ENABLE to activate filtering. */
    spk_eq_init();
    uart_write_line("INFO BOOT spk_eq ready (disabled, identity defaults)");

    /* ---- Audio bring-up (same init order as 08_mic_speaker_test) ---- */

    sai_mic_config_t mcfg = {
        .sai_base       = BOARD_MIC_SAI_BASE,
        .sai_clk_hz     = BOARD_MIC_SAI_CLK_FREQ,
        .sample_rate_hz = COL_SAMPLE_RATE,
        .bit_depth      = 16,
    };
    sai_speaker_config_t scfg = {
        .sai_base       = BOARD_SPK_SAI_BASE,
        .sai_clk_hz     = BOARD_SPK_SAI_CLK_FREQ,
        .sample_rate_hz = COL_SAMPLE_RATE,
    };
    if (sai_mic_init(&s_mic, &mcfg) != kStatus_Success) {
        uart_write_line("ERR BOOT SAI mic init -- halting");
        for (;;) { __WFI(); }
    }
    uart_printf("INFO BOOT SAI mic OK rate=%uHz", (unsigned)COL_SAMPLE_RATE);

    if (sai_speaker_init(&s_spk, &scfg) != kStatus_Success) {
        uart_write_line("ERR BOOT SAI speaker init -- halting");
        for (;;) { __WFI(); }
    }
    uart_printf("INFO BOOT SAI speaker OK rate=%uHz", (unsigned)COL_SAMPLE_RATE);

    /* ---- Servo bring-up (I2C + PCA9685 / LU9685) ---- */

    i2c_init();
    uart_printf("INFO BOOT I2C OK base=LPI2C2 baud=%uHz", (unsigned)COL_I2C_BAUD);

    /* Scan first so the operator can see if the LU9685 (or PCA9685) is
     * actually where we think it is — saves a lot of jumper-fiddling
     * when the address differs from the firmware default. */
    i2c_scan_print();

    {
        status_t s = servo_init(&s_servo, COL_I2C_BASE,
                                SERVO_DEFAULT_ADDR, SERVO_DEFAULT_FREQ_HZ);
        if (s != kStatus_Success) {
            uart_printf("ERR BOOT %s init addr=0x%02X status=%ld -- halting",
                        SERVO_BACKEND_NAME, (unsigned)SERVO_DEFAULT_ADDR, (long)s);
            uart_write_line("INFO check 5V on V+, D18/D19 wiring, and addr jumpers");
            for (;;) { __WFI(); }
        }
        uart_printf("INFO BOOT %s OK addr=0x%02X freq=%uHz",
                    SERVO_BACKEND_NAME, (unsigned)SERVO_DEFAULT_ADDR,
                    (unsigned)SERVO_DEFAULT_FREQ_HZ);
    }

    /* Load home/open config (RAM defaults for now), drive servos to home,
     * then release PWM so the chassis is silent at idle. Servos hold
     * position by friction at low load; subsequent OPEN/CLOSE/SERVO
     * commands re-energise the relevant channel. */
    (void)servo_config_init();
    {
        const servo_config_t *cfg = servo_config_get();
        status_t s = servo_set_first_n_deg(&s_servo, cfg->home_deg, ICHP_SERVO_COUNT);
        if (s == kStatus_Success) {
            /* Use %d (int) for the angles — newlib-nano's default printf
             * drops %f unless -u _printf_float is in LD flags. Integer
             * degrees are sufficient for the boot sanity check. */
            uart_printf("INFO BOOT servo home OK (%u ch -> ch0=%d ch%u=%d deg)",
                        (unsigned)ICHP_SERVO_COUNT,
                        (int)cfg->home_deg[0],
                        (unsigned)(ICHP_SERVO_COUNT - 1),
                        (int)cfg->home_deg[ICHP_SERVO_COUNT - 1]);
        } else {
            uart_printf("WARN BOOT servo home write status=%ld (chip may be unresponsive)",
                        (long)s);
        }
        /* Seed RUN's "current commanded angle" tracker so its first trial
         * (if no OPEN/CLOSE has run since boot) reproduces the home pose
         * instead of whatever the array was initialised to (zeros). */
        for (uint8_t i = 0; i < ICHP_SERVO_COUNT; i++) {
            s_state.current_deg[i] = cfg->home_deg[i];
        }
    }
    /* Give servos time to swing from arbitrary boot positions (worst case
     * full 180 deg) before releasing PWM. */
    delay_ms(COL_NAMED_MOVE_MS);
    (void)servo_all_off(&s_servo);
    uart_write_line("INFO BOOT servo PWM released (idle)");

    /* ---- TFT bring-up (SPI + ILI9341). Optional: collector runs headless
     * if the panel isn't wired. ---- */

    {
        status_t s = tft_init();
        if (s == kStatus_Success) {
            uart_write_line("INFO BOOT TFT ILI9341 OK 240x320");
            const servo_config_t *cfg = servo_config_get();
            collector_display_set_pattern(&s_disp, cfg->home_deg);
            /* Pattern library is empty at boot — PC client pushes patterns
             * after connecting, then RUN footer shows the actual name. */
            collector_display_set_footer(&s_disp, "(no pattern)",
                                         s_state.volume_pct, 0, s_state.repeats);
        } else {
            uart_printf("WARN BOOT TFT not detected status=%ld (running headless)",
                        (long)s);
        }
    }

    uart_write_line("INFO IchiPing 09_collector ready");
    uart_write_line("INFO send PING to test, GET CONFIG for state, RUN to collect");

    /* Command loop. Busy-poll RX rather than __WFI: at 921600 baud the
     * SysTick wake interval (~1 ms) is far longer than the LPUART RX FIFO
     * (~8 entries) can buffer, so any line longer than the FIFO would
     * overflow between wake-ups and the CR/LF would get dropped before
     * line completion fired. Busy-polling drains the FIFO in real time. */
    ichp_cmd_lbuf_t lb;
    ichp_cmd_lbuf_reset(&lb);

    for (;;) {
        if (LPUART_GetStatusFlags(COL_UART_BASE) & kLPUART_RxDataRegFullFlag) {
            uint8_t c = LPUART_ReadByte(COL_UART_BASE);
            if (ichp_cmd_lbuf_feed(&lb, (char)c)) {
                if (lb.overflow) {
                    uart_write_line("ERR LINE_TOO_LONG");
                    ichp_cmd_lbuf_reset(&lb);
                    continue;
                }
                ichp_cmd_t cmd;
                const char *et = NULL, *ea = NULL;
                if (ichp_cmd_parse(lb.buf, &cmd, &et, &ea)) {
                    apply_cmd(&cmd, &lb);
                } else {
                    uart_printf("ERR %s %s", et ? et : "PARSE", ea ? ea : "?");
                }
                ichp_cmd_lbuf_reset(&lb);
            }
        }
    }
}
