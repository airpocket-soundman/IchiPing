/*
 * IchiPing — Mic + Speaker (impulse-response) test firmware.
 *
 * Plays the IchiPing 200 -> 6 kHz chirp through MAX98357A (SAI1 TX) AND
 * records the response through INMP441/MSM261 (SAI1 RX, sync mode — shares
 * the TX framer's BCLK/FS so samples are clock-locked, required for a
 * meaningful impulse response). The captured window is shipped to the PC
 * as a single ICHP audio frame (re-using pc/receiver.py) tagged with
 * seq 1, 2, 3...
 *
 * This is the closest end-to-end test before bringing up real inference —
 * the room impulse response stored here is what the 1D-CNN will eventually
 * learn from.
 *
 * Cadence:
 *   - One chirp every IRTEST_CYCLE_MS (default 5 s)
 *   - Chirp itself is 2 s (200 Hz -> 6 kHz linear sweep, 5 ms raised-
 *     cosine fade at each end to suppress click artefacts)
 *   - Record window is 2.5 s (chirp + 500 ms tail for late reflections)
 *   - Gated by SW3: boots in PAUSED state, SW3 toggles RUNNING/PAUSED
 *     so the board never blasts audio unexpectedly on power-up
 *
 * Duplex strategy:
 *   The SAI1 TX framer drives BCLK/FS; the RX framer is in sync mode and
 *   reads the same clocks. chirp_and_capture() interleaves "write next TX
 *   sample" and "read next RX sample" in one tight CPU loop, so the chirp
 *   and the captured response are sample-locked to within the TX FIFO
 *   depth (~8 samples / ~500 us at 16 kHz) — small enough that the
 *   recovered IR sits well inside the 500 ms tail window. EDMA streaming
 *   would tighten this further but is not required for v0.1.
 *
 * Wiring is the union of 06_mic_test and 07_speaker_test (same J1 pins,
 * no rewiring needed when stepping up from 06/07 -> 08):
 *   INMP441 SCK / WS / SD  <- J1.1 (P3_16) / J1.11 (P3_17) / J1.15 (P3_21)
 *   MAX98357A BCLK / LRC / DIN <- J1.1 (P3_16) / J1.11 (P3_17) / J1.5 (P3_20)
 * BCLK and LRC are shared between INMP441 and MAX98357A — both driven by
 * SAI1's TX framer.
 *
 * PC side:
 *   python receiver.py --port COMx --baud 921600 --out captures/08
 */

#include "pin_mux.h"
#include "clock_config.h"
#include "board.h"
#include "fsl_debug_console.h"
#include "fsl_lpuart.h"
#include "fsl_sai.h"
#include "fsl_gpio.h"

#include "sai_mic.h"
#include "sai_speaker.h"
#include "ichiping_frame.h"
#include "app.h"

#include <math.h>
#include <stdbool.h>

extern void BOARD_InitHardware(void);

#define IRTEST_SAMPLE_RATE   16000u
#define IRTEST_CHIRP_MS      2000u
#define IRTEST_TOTAL_MS      2500u
#define IRTEST_CHIRP_SAMP    ((IRTEST_SAMPLE_RATE * IRTEST_CHIRP_MS) / 1000u)
#define IRTEST_TOTAL_SAMP    ((IRTEST_SAMPLE_RATE * IRTEST_TOTAL_MS) / 1000u)
#define IRTEST_CYCLE_MS      5000u

#define IRTEST_CHIRP_F0_HZ   200.0f
#define IRTEST_CHIRP_F1_HZ   6000.0f   /* keep below Fs/2 — going to Nyquist sounds gritty (07 lesson) */
#define IRTEST_CHIRP_AMP     0.6f
#define IRTEST_SPK_GAIN      0.15f     /* software attenuation ~-16 dB; matches 07_speaker_test */

#ifndef IRTEST_UART_BAUD
#define IRTEST_UART_BAUD     921600u
#endif

/* board.h casts BOARD_DEBUG_UART_BASEADDR to uint32_t (for the SDK's
 * debug-console init that wants a numeric base), but LPUART_Init /
 * LPUART_WriteBlocking want an LPUART_Type *. Use the SDK macro
 * directly here — same workaround as 01_dummy_emitter. */
#ifndef IRTEST_UART_BASE
#define IRTEST_UART_BASE     LPUART4
#endif

/* Memory layout note:
 *   m_data on MCXN947 cm33_core0 is 312 KB. The chirp + record buffers
 *   plus the ICHP frame would not fit if we kept an intermediate f32
 *   record buffer (40000 * 4 = 160 KB) and a separate int16 mirror
 *   (40000 * 2 = 80 KB) on top of s_tx_buf — total ~375 KB, link-time
 *   .bss overflow. So we capture int16 directly into the s_tx_buf
 *   payload region and skip ichp_pack_frame's internal memcpy. The
 *   header and CRC are written in-place. f32 capture (dynamic range
 *   benefit) can come back once 09_audio_stream introduces EDMA ring
 *   buffers that work on small chunks instead of the whole window. */
static int16_t s_chirp [IRTEST_CHIRP_SAMP];
static uint8_t s_tx_buf[ICHP_HEADER_SIZE
                        + IRTEST_TOTAL_SAMP * sizeof(int16_t)
                        + ICHP_CRC_SIZE];

static volatile uint32_t s_uptime_ms = 0;
void SysTick_Handler(void) { s_uptime_ms++; }
static void systick_init_1ms(void) { (void)SysTick_Config(SystemCoreClock / 1000u); }
static void delay_ms(uint32_t ms) {
    uint32_t end = s_uptime_ms + ms;
    while ((int32_t)(s_uptime_ms - end) < 0) { __WFI(); }
}

/* SW3 toggles the cycle on/off. Boot state is PAUSED so the board never
 * blasts audio unexpectedly when power-cycled. Same falling-edge ISR
 * pattern (with 200 ms software debounce) as 07_speaker_test — using an
 * interrupt means presses register even while we're inside the duplex
 * loop busy-waiting on TX/RX FIFO flags. */
static volatile bool     s_cycle_running   = false;
static volatile bool     s_button_event    = false;
static volatile uint32_t s_last_button_ms  = 0;

void GPIO00_IRQHandler(void)
{
    uint32_t flags = GPIO_GpioGetInterruptFlags(BOARD_USER_BUTTON_GPIO);
    if (flags & (1u << BOARD_USER_BUTTON_PIN)) {
        GPIO_GpioClearInterruptFlags(BOARD_USER_BUTTON_GPIO,
                                     1u << BOARD_USER_BUTTON_PIN);
        if ((s_uptime_ms - s_last_button_ms) > 200u) {
            s_button_event   = true;
            s_last_button_ms = s_uptime_ms;
        }
    }
    SDK_ISR_EXIT_BARRIER;
}

static void consume_button_toggle(void)
{
    if (s_button_event) {
        s_button_event  = false;
        s_cycle_running = !s_cycle_running;
        PRINTF("[" BOARD_USER_BUTTON_NAME "] cycle %s\r\n",
               s_cycle_running ? "RUNNING" : "PAUSED");
    }
}

static void uart_init_binary(void)
{
    lpuart_config_t cfg;
    LPUART_GetDefaultConfig(&cfg);
    cfg.baudRate_Bps = IRTEST_UART_BAUD;
    cfg.enableTx     = true;
    cfg.enableRx     = false;
    LPUART_Init(IRTEST_UART_BASE, &cfg, BOARD_DEBUG_UART_CLK_FREQ);
}

/* Clean linear chirp f0 -> f1 with raised-cosine fade-in/out. Lifted from
 * 07_speaker_test::render_chirp — the fade suppresses the "click" you get
 * from a hard start/stop on a sinusoid. Output is int16 scaled by the
 * software gain so MAX98357A stays in a polite SPL range with the GAIN
 * pin tied to GND (3 dB hardware gain). */
static void render_chirp(int16_t *out, size_t n, uint32_t fs,
                         float f0_hz, float f1_hz, float amp)
{
    const float two_pi = 6.28318530718f;
    const float dur    = (float)n / (float)fs;
    const float k      = (f1_hz - f0_hz) / dur;
    const size_t fade  = (size_t)(0.005f * (float)fs);   /* 5 ms */

    for (size_t i = 0; i < n; i++) {
        float t     = (float)i / (float)fs;
        float phase = two_pi * (f0_hz * t + 0.5f * k * t * t);
        float env   = 1.0f;
        if (i < fade)             env = 0.5f * (1.0f - cosf(3.14159265f * (float)i / (float)fade));
        else if (i > n - fade)    env = 0.5f * (1.0f - cosf(3.14159265f * (float)(n - i) / (float)fade));
        float s = amp * env * sinf(phase);
        out[i] = (int16_t)(s * 30000.0f * IRTEST_SPK_GAIN);
    }
}

/* Convert one raw 32-bit SAI FIFO word into int16 using the mic's
 * configured gain_shift. Mirrors shared/source/sai_mic.c::sai_word_to_int16
 * but inlined here so the duplex loop stays tight (no function-call
 * overhead per sample at 16 kHz). */
static inline int16_t mic_word_to_int16(uint32_t w, uint8_t shift)
{
    int32_t s = (int32_t)w;
    s >>= shift;
    if (s >  INT16_MAX) s =  INT16_MAX;
    if (s <  INT16_MIN) s =  INT16_MIN;
    return (int16_t)s;
}

/* Full-duplex chirp playback + capture in a single CPU loop.
 *
 * The SAI TX framer is already running (FCONT=1 keeps BCLK/FS alive from
 * init onwards, outputting zeros from FIFO underflow). We:
 *   1. Flush any stale samples out of the RX FIFO
 *   2. Enable the RX framer (kSAI_ModeSync, picks up the live TX clocks)
 *   3. Loop: feed next TX word (chirp samples, then zeros after chirp ends
 *      so the speaker stays silent during the reflection tail);
 *             read next RX word into the f32 buffer
 *   4. Disable the RX framer so the FIFO does not keep filling between
 *      cycles (TX keeps clocking — class-D mute pops if we toggle it)
 *
 * Alignment: TX FIFO depth is 8, so the TX sample we just wrote is heard
 * ~8 sample periods (~500 us at 16 kHz) after our write. RX FIFO
 * watermark is 1, so each RX read returns "fresh" data. The recovered IR
 * therefore has an absolute offset of ~8 samples between the rendered
 * chirp and the captured response — well inside the 500 ms tail window
 * and easily corrected in PC post-processing if needed.
 */
static void chirp_and_capture(sai_mic_t *mic,
                              const int16_t *chirp, size_t n_chirp,
                              int16_t *rec, size_t n_total)
{
    I2S_Type *base = (I2S_Type *)mic->cfg.sai_base;
    const uint8_t shift = mic->gain_shift;

    /* Drain any stale RX samples queued while we were idle (FCONT=1 keeps
     * the framer clocking, so the FIFO can have a few left-overs). */
    while (SAI_RxGetStatusFlag(base) & kSAI_FIFORequestFlag) {
        (void)SAI_ReadData(base, 0u);
    }

    SAI_RxEnable(base, true);

    size_t tx_i = 0;
    size_t rx_i = 0;
    while (rx_i < n_total) {
        if (tx_i < n_total &&
            (SAI_TxGetStatusFlag(base) & kSAI_FIFORequestFlag)) {
            uint32_t w = 0;
            if (tx_i < n_chirp) {
                /* MAX98357A reads the upper 16 bits of the 32-bit slot.
                 * Cast through int32 first so the sign extends correctly. */
                w = ((uint32_t)(int32_t)chirp[tx_i]) << 16;
            }
            SAI_WriteData(base, 0u, w);
            tx_i++;
        }
        if (SAI_RxGetStatusFlag(base) & kSAI_FIFORequestFlag) {
            rec[rx_i++] = mic_word_to_int16(SAI_ReadData(base, 0u), shift);
        }
    }

    SAI_RxEnable(base, false);
}

static void summarise_capture(const int16_t *rec, size_t n, int *peak_out, int *rms_out)
{
    int32_t peak = 0;
    int64_t sumsq = 0;
    for (size_t i = 0; i < n; i++) {
        int32_t v = rec[i];
        int32_t a = v < 0 ? -v : v;
        if (a > peak) peak = a;
        sumsq += (int64_t)v * v;
    }
    *peak_out = (int)peak;
    *rms_out  = (int)sqrtf((float)sumsq / (float)n);
}

int main(void)
{
    BOARD_InitHardware();
    systick_init_1ms();

    /* SW3 GPIO direction + falling-edge interrupt (boot state PAUSED). */
    const gpio_pin_config_t btn_in = { kGPIO_DigitalInput, 0 };
    GPIO_PinInit(BOARD_USER_BUTTON_GPIO, BOARD_USER_BUTTON_PIN, &btn_in);
    GPIO_SetPinInterruptConfig(BOARD_USER_BUTTON_GPIO, BOARD_USER_BUTTON_PIN,
                               kGPIO_InterruptFallingEdge);
    EnableIRQ(GPIO00_IRQn);

    PRINTF("\r\nIchiPing 08_mic_speaker_test  --  SAI1 TX chirp + SAI1 RX capture\r\n");
    PRINTF("  Fs=%u Hz, chirp=%u samp (%u..%u Hz), window=%u samp, cycle=%u ms\r\n",
           (unsigned)IRTEST_SAMPLE_RATE,
           (unsigned)IRTEST_CHIRP_SAMP,
           (unsigned)IRTEST_CHIRP_F0_HZ, (unsigned)IRTEST_CHIRP_F1_HZ,
           (unsigned)IRTEST_TOTAL_SAMP,
           (unsigned)IRTEST_CYCLE_MS);
    PRINTF("Press " BOARD_USER_BUTTON_NAME " to start the cycle; press again to pause.\r\n");

    /* Pre-render the chirp once. Clean 200 -> 6 kHz with 5 ms raised-cosine
     * fade so MAX98357A does not see a hard step at start/stop. */
    render_chirp(s_chirp, IRTEST_CHIRP_SAMP, IRTEST_SAMPLE_RATE,
                 IRTEST_CHIRP_F0_HZ, IRTEST_CHIRP_F1_HZ, IRTEST_CHIRP_AMP);

    sai_mic_t      mic;
    sai_speaker_t  spk;
    sai_mic_config_t mcfg = {
        .sai_base       = BOARD_MIC_SAI_BASE,
        .sai_clk_hz     = BOARD_MIC_SAI_CLK_FREQ,
        .sample_rate_hz = IRTEST_SAMPLE_RATE,
        .bit_depth      = 16,
    };
    sai_speaker_config_t scfg = {
        .sai_base       = BOARD_SPK_SAI_BASE,
        .sai_clk_hz     = BOARD_SPK_SAI_CLK_FREQ,
        .sample_rate_hz = IRTEST_SAMPLE_RATE,
    };
    /* Init order matters: sai_mic_init sets up the TX framer (master,
     * BCLK/FS source) AND the RX framer (sync). sai_speaker_init then
     * idempotently re-configures the TX framer for the same Fs (no
     * functional change — both helpers configure WordWidth32 / MonoLeft).
     * Calling speaker_init second leaves us with a known TX state and
     * MAX98357A unmuted via the FCONT primer. */
    if (sai_mic_init(&mic, &mcfg) != kStatus_Success ||
        sai_speaker_init(&spk, &scfg) != kStatus_Success) {
        PRINTF("FAIL: SAI init\r\n");
        for (;;) { __WFI(); }
    }
    uart_init_binary();

    uint16_t seq = 0;
    for (;;) {
        /* Idle until SW3 toggles us on. BCLK/FS keep running (FCONT=1)
         * so MAX98357A stays unmuted and we avoid start-of-cycle pops. */
        while (!s_cycle_running) {
            consume_button_toggle();
            __WFI();
        }

        seq++;
        uint32_t cycle_start = s_uptime_ms;
        PRINTF("[%4u] firing chirp + capturing %u samples...\r\n",
               (unsigned)seq, (unsigned)IRTEST_TOTAL_SAMP);

        /* Capture int16 samples directly into the s_tx_buf payload slot.
         * Avoids a separate 80 kB int16 buffer (and a 160 kB f32 buffer)
         * that would push us over m_data's 312 kB limit. */
        int16_t *rec_payload = (int16_t *)(s_tx_buf + ICHP_HEADER_SIZE);
        chirp_and_capture(&mic, s_chirp, IRTEST_CHIRP_SAMP,
                          rec_payload, IRTEST_TOTAL_SAMP);

        int peak_i, rms_i;
        summarise_capture(rec_payload, IRTEST_TOTAL_SAMP, &peak_i, &rms_i);

        /* In-place ICHP frame build. Mirrors ichp_pack_frame() but without
         * the internal memcpy(out + header, samples, ...) since samples
         * already live in the payload region of out. */
        const size_t payload_bytes = (size_t)IRTEST_TOTAL_SAMP * sizeof(int16_t);
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
        h->n_samples   = IRTEST_TOTAL_SAMP;
        h->rate_hz     = IRTEST_SAMPLE_RATE;
        for (int i = 0; i < 5; i++) { h->servo_deg[i] = 0.0f; }

        const uint16_t crc = ichp_crc16_ccitt(s_tx_buf,
                                              ICHP_HEADER_SIZE + payload_bytes);
        s_tx_buf[framed - 2] = (uint8_t)(crc & 0xFFu);
        s_tx_buf[framed - 1] = (uint8_t)((crc >> 8) & 0xFFu);

        /* Label deliberately avoids the literal substring "ICHP" — the
         * binary frame magic is "ICHP" and the receiver scans byte-by-byte
         * for it, so any plain-text occurrence on the same UART would
         * false-sync the parser and trash the next frame. */
        PRINTF("  [frame] seq=%u bytes=%u crc=0x%04x peak=%d rms=%d\r\n",
               (unsigned)seq, (unsigned)framed, (unsigned)crc, peak_i, rms_i);
        LPUART_WriteBlocking(IRTEST_UART_BASE, s_tx_buf, framed);

        consume_button_toggle();

        /* Sleep the remainder of the cycle. If work (capture + UART send)
         * overran IRTEST_CYCLE_MS we just continue immediately — no busy
         * wait — and the effective cadence drops to whatever the work takes. */
        uint32_t elapsed = s_uptime_ms - cycle_start;
        if (elapsed < IRTEST_CYCLE_MS) {
            delay_ms(IRTEST_CYCLE_MS - elapsed);
        }
    }
}
