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
 *                          - build pattern for trial i (pinned values, else
 *                            random binary choice between home and open per
 *                            servo using an LFSR);
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

/* ---- Audio constants (same as 08, knob-able from PC at runtime) ---- */

#define COL_SAMPLE_RATE       16000u
#define COL_WINDOW_MS         2000u            /* 2 s per trial — multiband click fits with margin */
#define COL_WINDOW_SAMP       ((COL_SAMPLE_RATE * COL_WINDOW_MS) / 1000u)

#define COL_CHIRP_MS          2000u            /* legacy chirp path (08 parity) */
#define COL_CHIRP_SAMP        ((COL_SAMPLE_RATE * COL_CHIRP_MS) / 1000u)
#define COL_CHIRP_F0_HZ       200.0f
#define COL_CHIRP_F1_HZ       6000.0f

/* Multiband click train — see docs discussion: 6 freq x 6 cycles in 2 s,
 * 300 ms per cycle, 0.7 ms burst at each of {2,3,4,5,6,7} kHz with 0.2 ms
 * raised-cosine fades. Pre-rendered into s_excite at boot.
 *
 * Sample-level layout (constant offsets in s_excite buffer):
 *   cycle c in 0..N_CYCLES-1, burst b in 0..N_BANDS-1:
 *     start_samp = c * SAMP_PER_CYCLE + b * SAMP_PER_BURST_SLOT
 *     burst spans SAMP_PER_BURST samples; rest of slot is silent.
 */
#define COL_MB_N_BANDS        6u
#define COL_MB_N_CYCLES       6u
#define COL_MB_BURST_MS       0.7f
#define COL_MB_FADE_MS        0.2f
#define COL_MB_BURST_GAP_MS   50.0f                /* burst-to-burst within cycle */
#define COL_MB_CYCLE_GAP_MS   0.0f                 /* cycle slot is N_BANDS*GAP, no extra */
#define COL_MB_SAMP_PER_BURST_SLOT  ((uint32_t)((COL_MB_BURST_GAP_MS * COL_SAMPLE_RATE) / 1000.0f))
#define COL_MB_SAMP_PER_CYCLE       (COL_MB_N_BANDS * COL_MB_SAMP_PER_BURST_SLOT)
#define COL_MB_TOTAL_SAMP           (COL_MB_N_CYCLES * COL_MB_SAMP_PER_CYCLE)

static const float COL_MB_FREQS_HZ[COL_MB_N_BANDS] = {
    2000.0f, 3000.0f, 4000.0f, 5000.0f, 6000.0f, 7000.0f,
};

#define COL_DEFAULT_VOLUME    0.05f            /* small box concentrates SPL, -26 dB plenty */
#define COL_DEFAULT_REPEATS   30
#define COL_SERVO_SETTLE_MS   400u             /* SG90 worst-case 60deg ~= 400 ms */

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

/* ILI9341 TFT (matches 03_ili9341_test; macros resolve via app.h). */
#define COL_TFT_SPI_BAUD      20000000U     /* 20 MHz once init proves stable */

/* ---- Buffers ---- */

static int16_t s_excite[COL_WINDOW_SAMP];                 /* TX waveform, pre-rendered */
static uint8_t s_tx_buf[ICHP_HEADER_SIZE
                        + COL_WINDOW_SAMP * sizeof(int16_t)
                        + ICHP_CRC_SIZE];                 /* RX-into-payload + frame */

/* ---- Runtime state ---- */

typedef struct {
    float             volume;                 /* 0..1 software gain on TX */
    ichp_excitation_t excitation;
    int32_t           repeats;
    bool              pin_present[ICHP_SERVO_COUNT];
    float             pin_deg[ICHP_SERVO_COUNT];
    bool              stop_requested;
} col_state_t;

static col_state_t s_state = {
    .volume       = COL_DEFAULT_VOLUME,
    .excitation   = ICHP_EXCITE_MULTIBAND,
    .repeats      = COL_DEFAULT_REPEATS,
    .pin_present  = { false, false, false, false, false },
    .pin_deg      = { 0.0f, 0.0f, 0.0f, 0.0f, 0.0f },
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

/* ---- Excitation rendering ---- */

static void render_chirp_into(int16_t *out, size_t n_total, float volume)
{
    const float two_pi = 6.28318530718f;
    const float dur    = (float)COL_CHIRP_SAMP / (float)COL_SAMPLE_RATE;
    const float k      = (COL_CHIRP_F1_HZ - COL_CHIRP_F0_HZ) / dur;
    const size_t fade  = (size_t)(0.005f * (float)COL_SAMPLE_RATE);

    for (size_t i = 0; i < n_total; i++) {
        if (i >= COL_CHIRP_SAMP) { out[i] = 0; continue; }
        float t     = (float)i / (float)COL_SAMPLE_RATE;
        float phase = two_pi * (COL_CHIRP_F0_HZ * t + 0.5f * k * t * t);
        float env   = 1.0f;
        if (i < fade)                       env = 0.5f * (1.0f - cosf(3.14159265f * (float)i / (float)fade));
        else if (i > COL_CHIRP_SAMP - fade) env = 0.5f * (1.0f - cosf(3.14159265f * (float)(COL_CHIRP_SAMP - i) / (float)fade));
        float s = 0.6f * env * sinf(phase);
        out[i]  = (int16_t)(s * 30000.0f * volume);
    }
}

static void render_multiband_into(int16_t *out, size_t n_total, float volume)
{
    /* Zero everything first, then overwrite each burst region. Lets
     * subsequent gaps stay silent without explicit zeroing. */
    memset(out, 0, n_total * sizeof(int16_t));

    const float two_pi   = 6.28318530718f;
    const uint32_t burst_n = (uint32_t)((COL_MB_BURST_MS * COL_SAMPLE_RATE) / 1000.0f);
    const uint32_t fade_n  = (uint32_t)((COL_MB_FADE_MS  * COL_SAMPLE_RATE) / 1000.0f);

    for (uint32_t c = 0; c < COL_MB_N_CYCLES; c++) {
        for (uint32_t b = 0; b < COL_MB_N_BANDS; b++) {
            const uint32_t start = c * COL_MB_SAMP_PER_CYCLE
                                 + b * COL_MB_SAMP_PER_BURST_SLOT;
            if (start + burst_n > n_total) return;
            const float f = COL_MB_FREQS_HZ[b];
            for (uint32_t i = 0; i < burst_n; i++) {
                float t = (float)i / (float)COL_SAMPLE_RATE;
                float env = 1.0f;
                if (i < fade_n)              env = 0.5f * (1.0f - cosf(3.14159265f * (float)i / (float)fade_n));
                else if (i > burst_n - fade_n) env = 0.5f * (1.0f - cosf(3.14159265f * (float)(burst_n - i) / (float)fade_n));
                float s = env * sinf(two_pi * f * t);
                out[start + i] = (int16_t)(s * 30000.0f * volume);
            }
        }
    }
}

static void render_excitation(ichp_excitation_t kind, float volume)
{
    switch (kind) {
        case ICHP_EXCITE_CHIRP:     render_chirp_into(s_excite, COL_WINDOW_SAMP, volume); break;
        case ICHP_EXCITE_MULTIBAND: render_multiband_into(s_excite, COL_WINDOW_SAMP, volume); break;
        case ICHP_EXCITE_SILENCE:
        default:                    memset(s_excite, 0, COL_WINDOW_SAMP * sizeof(int16_t)); break;
    }
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

/* xorshift32 LFSR — small, MCU-friendly, good enough for binary choice
 * across trials. Seed from SysTick at first use. */
static uint32_t s_rng = 0;
static uint32_t rng_next(void)
{
    if (s_rng == 0) { s_rng = s_uptime_ms | 0xA5A5A5A5u; }
    uint32_t x = s_rng;
    x ^= x << 13; x ^= x >> 17; x ^= x << 5;
    s_rng = x;
    return x;
}

/* Fill `target_deg[5]` for one trial given pin state and home/open config. */
static void build_trial_pattern(float target_deg[ICHP_SERVO_COUNT])
{
    const servo_config_t *cfg = servo_config_get();
    for (uint8_t i = 0; i < ICHP_SERVO_COUNT; i++) {
        if (s_state.pin_present[i]) {
            target_deg[i] = s_state.pin_deg[i];
        } else {
            target_deg[i] = (rng_next() & 1u) ? cfg->open_deg[i] : cfg->home_deg[i];
        }
    }
}

static void servo_apply_pattern(const float target_deg[ICHP_SERVO_COUNT])
{
    (void)servo_set_first_n_deg(&s_servo, target_deg, ICHP_SERVO_COUNT);
    collector_display_set_pattern(&s_disp, target_deg);
}

/* ---- Command dispatch ---- */

static void say_config(void)
{
    uart_printf("OK CONFIG rate=%u window=%u excitation=%s volume=%.3f repeats=%d",
                (unsigned)COL_SAMPLE_RATE,
                (unsigned)COL_WINDOW_SAMP,
                ICHP_EXCITATION_NAMES[s_state.excitation],
                (double)s_state.volume,
                (int)s_state.repeats);
}

static void say_home(void)
{
    const servo_config_t *cfg = servo_config_get();
    uart_printf("OK HOME %s=%.1f %s=%.1f %s=%.1f %s=%.1f %s=%.1f",
                ICHP_SERVO_NAMES[0], (double)cfg->home_deg[0],
                ICHP_SERVO_NAMES[1], (double)cfg->home_deg[1],
                ICHP_SERVO_NAMES[2], (double)cfg->home_deg[2],
                ICHP_SERVO_NAMES[3], (double)cfg->home_deg[3],
                ICHP_SERVO_NAMES[4], (double)cfg->home_deg[4]);
}

static void say_open(void)
{
    const servo_config_t *cfg = servo_config_get();
    uart_printf("OK OPEN %s=%.1f %s=%.1f %s=%.1f %s=%.1f %s=%.1f",
                ICHP_SERVO_NAMES[0], (double)cfg->open_deg[0],
                ICHP_SERVO_NAMES[1], (double)cfg->open_deg[1],
                ICHP_SERVO_NAMES[2], (double)cfg->open_deg[2],
                ICHP_SERVO_NAMES[3], (double)cfg->open_deg[3],
                ICHP_SERVO_NAMES[4], (double)cfg->open_deg[4]);
}

static void say_pins(void)
{
    char buf[160];
    size_t off = (size_t)snprintf(buf, sizeof(buf), "OK PINS");
    for (uint8_t i = 0; i < ICHP_SERVO_COUNT; i++) {
        int n;
        if (s_state.pin_present[i]) {
            n = snprintf(buf + off, sizeof(buf) - off, " %s=%.1f",
                         ICHP_SERVO_NAMES[i], (double)s_state.pin_deg[i]);
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
 * applied this trial. */
static void send_frame(uint16_t seq, const float servo_deg[ICHP_SERVO_COUNT],
                       const int16_t *rec_payload)
{
    const size_t payload_bytes = (size_t)COL_WINDOW_SAMP * sizeof(int16_t);
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
    h->n_samples   = COL_WINDOW_SAMP;
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
    uart_printf("OK RUN started repeats=%d excitation=%s",
                (int)s_state.repeats, ICHP_EXCITATION_NAMES[s_state.excitation]);

    render_excitation(s_state.excitation, s_state.volume);

    int32_t frames = 0;
    for (int32_t i = 0; i < s_state.repeats && !s_state.stop_requested; i++) {
        float target_deg[ICHP_SERVO_COUNT];
        build_trial_pattern(target_deg);
        servo_apply_pattern(target_deg);
        collector_display_set_footer(&s_disp,
                                     ICHP_EXCITATION_NAMES[s_state.excitation],
                                     s_state.volume,
                                     i + 1, s_state.repeats);
        delay_ms(COL_SERVO_SETTLE_MS);

        int16_t *rec_payload = (int16_t *)(s_tx_buf + ICHP_HEADER_SIZE);
        play_and_capture(s_excite, rec_payload, COL_WINDOW_SAMP);

        send_frame((uint16_t)(i + 1), target_deg, rec_payload);
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

static void apply_cmd(const ichp_cmd_t *cmd, ichp_cmd_lbuf_t *lb)
{
    switch (cmd->kind) {
        case ICHP_CMD_PING:
            uart_write_line("OK PONG " __DATE__ " " __TIME__);
            break;
        case ICHP_CMD_GET_CONFIG:    say_config(); break;
        case ICHP_CMD_GET_HOME:      say_home();   break;
        case ICHP_CMD_GET_OPEN:      say_open();   break;
        case ICHP_CMD_GET_PINS:      say_pins();   break;
        case ICHP_CMD_SET_VOLUME:
            s_state.volume = cmd->volume;
            uart_printf("OK VOLUME %.3f", (double)cmd->volume);
            break;
        case ICHP_CMD_SET_EXCITATION:
            s_state.excitation = cmd->excite;
            uart_printf("OK EXCITATION %s", ICHP_EXCITATION_NAMES[cmd->excite]);
            break;
        case ICHP_CMD_SET_REPEATS:
            s_state.repeats = cmd->repeats;
            uart_printf("OK REPEATS %d", (int)cmd->repeats);
            break;
        case ICHP_CMD_SET_PIN:
            s_state.pin_present[cmd->servo_idx] = true;
            s_state.pin_deg[cmd->servo_idx]     = cmd->deg;
            uart_printf("OK PIN %s %.1f", ICHP_SERVO_NAMES[cmd->servo_idx], (double)cmd->deg);
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
            uart_printf("OK HOME %s %.1f", ICHP_SERVO_NAMES[cmd->servo_idx], (double)cmd->deg);
            break;
        case ICHP_CMD_SET_OPEN:
            (void)servo_config_set_open(cmd->servo_idx, cmd->deg);
            uart_printf("OK OPEN %s %.1f", ICHP_SERVO_NAMES[cmd->servo_idx], (double)cmd->deg);
            break;
        case ICHP_CMD_SAVE_HOME: {
            int r = servo_config_save_flash();
            if (r == 0) uart_write_line("OK HOME saved");
            else        uart_write_line("ERR NOT_IMPL SAVE_HOME (rebuild with SERVO_CONFIG_DEFAULTS updated to GET HOME values)");
            break;
        }
        case ICHP_CMD_SERVO:
            (void)servo_set_deg(&s_servo, cmd->servo_idx, cmd->deg);
            collector_display_set_servo(&s_disp, cmd->servo_idx, cmd->deg);
            uart_printf("OK SERVO %s %.1f", ICHP_SERVO_NAMES[cmd->servo_idx], (double)cmd->deg);
            break;
        case ICHP_CMD_SERVO_ALL_OFF:
            (void)servo_all_off(&s_servo);
            uart_write_line("OK SERVO all off");
            break;
        case ICHP_CMD_RUN:           do_run(lb);   break;
        case ICHP_CMD_STOP:
            /* If we get STOP outside a RUN, just acknowledge. */
            uart_write_line("OK STOP idle");
            break;
        default:
            uart_write_line("ERR BAD_VERB");
            break;
    }
}

/* ---- I2C init (for PCA9685) ---- */

static void i2c_init(void)
{
    lpi2c_master_config_t i2c;
    LPI2C_MasterGetDefaultConfig(&i2c);
    i2c.baudRate_Hz = COL_I2C_BAUD;
    LPI2C_MasterInit(COL_I2C_BASE, &i2c, COL_I2C_CLK_FREQ);
}

/* ---- TFT init (for ILI9341 status display) ---- */

static void tft_init(void)
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
    if (ili9341_init(&s_tft) == kStatus_Success) {
        collector_display_init(&s_disp, &s_tft);
    }
    /* If init fails (likely cause: TFT not wired), the collector still
     * runs headless — log it and carry on. */
}

/* ---- main ---- */

int main(void)
{
    BOARD_InitHardware();
    systick_init_1ms();

    uart_init_bidi();

    /* Audio bring-up — same init order as 08_mic_speaker_test. */
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
    if (sai_mic_init(&s_mic, &mcfg) != kStatus_Success ||
        sai_speaker_init(&s_spk, &scfg) != kStatus_Success) {
        uart_write_line("ERR INIT sai");
        for (;;) { __WFI(); }
    }

    /* Servo bring-up. */
    i2c_init();
    if (servo_init(&s_servo, COL_I2C_BASE, SERVO_DEFAULT_ADDR, SERVO_DEFAULT_FREQ_HZ) != kStatus_Success) {
        uart_write_line("ERR INIT servo");
        for (;;) { __WFI(); }
    }

    /* Load home/open config (RAM defaults for now), drive servos to home. */
    (void)servo_config_init();
    {
        const servo_config_t *cfg = servo_config_get();
        (void)servo_set_first_n_deg(&s_servo, cfg->home_deg, ICHP_SERVO_COUNT);
    }
    delay_ms(COL_SERVO_SETTLE_MS);

    /* TFT bring-up. Optional — collector runs headless if the panel
     * isn't wired (s_disp stays zero-inited, display_set_* are no-ops). */
    tft_init();
    {
        const servo_config_t *cfg = servo_config_get();
        collector_display_set_pattern(&s_disp, cfg->home_deg);
        collector_display_set_footer(&s_disp,
                                     ICHP_EXCITATION_NAMES[s_state.excitation],
                                     s_state.volume, 0, s_state.repeats);
    }

    uart_write_line("INFO IchiPing 09_collector ready");
    uart_printf("INFO build " __DATE__ " " __TIME__);
    uart_write_line("INFO send PING to test, GET CONFIG for state, RUN to collect");

    /* Command loop. */
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
        } else {
            __WFI();
        }
    }
}
