/*
 * IchiPing — 10_inference firmware (NN inference demo).
 *
 * The on-device counterpart of pc/training/. Drops the servo / random
 * pattern parts of 09_collector and instead runs:
 *
 *     [boot]
 *        → SAI1 full-duplex (same as 08): fire multiband click,
 *          capture 2 s window into RAM
 *        → features.c: matched filter + log-magnitude spectrum
 *        → model_stub.c: trivial classifier (replace with real eIQ /
 *          CMSIS-NN inference once pc/training/ produces an INT8 model)
 *        → ILI9341 TFT: show {class_id, probability, raw spectrum bar}
 *        → loop every cycle_ms; gated by SW3 like 08
 *
 * v0.5 build target: prove the end-to-end inference plumbing works
 * (capture -> features -> model -> display) using a stub model that
 * returns a deterministic-but-trivial output. Once the PC trainer
 * produces best.onnx → eIQ → INT8 C array, drop that into model_stub.c
 * (or replace the module entirely) and the inference loop is live.
 *
 * Why a separate project from 09: 09 is bidirectional (PC drives the
 * test), 10 is autonomous (the device classifies on its own and lights
 * up the display). Different demo audience. Sharing happens via
 * firmware/shared/source/{sai_*,ichiping_frame,ichp_cmd_optional,...}
 *
 * STATUS: skeleton. main loop + display update wired; the model and
 * feature extractor are MINIMAL STUBS. See README §TODO for the rest.
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
#include "ili9341.h"
#include "ichiping_frame.h"   /* shared int16 layout */
#include "app.h"

#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>

extern void BOARD_InitHardware(void);

/* ---- Audio constants (mirror 09_collector defaults) ---- */
#define INF_SAMPLE_RATE       16000u
#define INF_WINDOW_MS         2000u
#define INF_WINDOW_SAMP       ((INF_SAMPLE_RATE * INF_WINDOW_MS) / 1000u)
#define INF_TFT_SPI_BAUD      1000000U      /* matches 03_ili9341_test verified value; raise to 20 MHz after 10 bring-up */
#define INF_CYCLE_MS          3000u

/* ---- Stub model ----
 *
 * Output classes intended to match pc/training/dataset.py once a model is
 * trained. Update INF_N_CLASSES + INF_CLASS_NAMES to match the real one.
 */
#define INF_N_CLASSES         4

static const char *const INF_CLASS_NAMES[INF_N_CLASSES] = {
    "door_closed",
    "door_half",
    "door_open",
    "amb_silence",
};

/* ---- Buffers ---- */

static int16_t s_excite[INF_WINDOW_SAMP];
static int16_t s_audio [INF_WINDOW_SAMP];
/* spectrum_bins is the feature passed to the model; INT8 quantised in
 * the real eIQ path. Here we keep float32 for clarity. */
#define INF_N_BINS            128u
static float   s_spectrum[INF_N_BINS];

/* ---- Hardware ---- */

static sai_mic_t      s_mic;
static sai_speaker_t  s_spk;
static ili9341_t      s_tft;

/* ---- SysTick ---- */

static volatile uint32_t s_uptime_ms = 0;
void SysTick_Handler(void) { s_uptime_ms++; }
static void systick_init_1ms(void) { (void)SysTick_Config(SystemCoreClock / 1000u); }
static void delay_ms(uint32_t ms) {
    uint32_t end = s_uptime_ms + ms;
    while ((int32_t)(s_uptime_ms - end) < 0) { __WFI(); }
}

/* ---- SW3 gate (boot PAUSED, button toggles) ---- */

static volatile bool     s_running     = false;
static volatile bool     s_button_evt  = false;
static volatile uint32_t s_btn_last_ms = 0;

void GPIO00_IRQHandler(void)
{
    uint32_t flags = GPIO_GpioGetInterruptFlags(BOARD_USER_BUTTON_GPIO);
    if (flags & (1u << BOARD_USER_BUTTON_PIN)) {
        GPIO_GpioClearInterruptFlags(BOARD_USER_BUTTON_GPIO,
                                     1u << BOARD_USER_BUTTON_PIN);
        if ((s_uptime_ms - s_btn_last_ms) > 200u) {
            s_button_evt   = true;
            s_btn_last_ms  = s_uptime_ms;
        }
    }
    SDK_ISR_EXIT_BARRIER;
}

/* ---- Excitation: simple multiband click train (mirrors 09_collector) ---- */

#define INF_MB_N_BANDS        6u
#define INF_MB_N_CYCLES       6u
static const float INF_MB_FREQS_HZ[INF_MB_N_BANDS] = {
    2000.0f, 3000.0f, 4000.0f, 5000.0f, 6000.0f, 7000.0f,
};
#define INF_MB_BURST_MS       0.7f
#define INF_MB_FADE_MS        0.2f
#define INF_MB_BURST_GAP_MS   50.0f
#define INF_MB_SAMP_PER_SLOT  ((uint32_t)((INF_MB_BURST_GAP_MS * INF_SAMPLE_RATE) / 1000.0f))
#define INF_MB_VOLUME         0.05f

static void render_multiband(int16_t *out, size_t n_total)
{
    memset(out, 0, n_total * sizeof(int16_t));
    const float two_pi   = 6.28318530718f;
    const uint32_t burst_n = (uint32_t)((INF_MB_BURST_MS * INF_SAMPLE_RATE) / 1000.0f);
    const uint32_t fade_n  = (uint32_t)((INF_MB_FADE_MS  * INF_SAMPLE_RATE) / 1000.0f);

    for (uint32_t c = 0; c < INF_MB_N_CYCLES; c++) {
        for (uint32_t b = 0; b < INF_MB_N_BANDS; b++) {
            const uint32_t start = c * (INF_MB_N_BANDS * INF_MB_SAMP_PER_SLOT)
                                 + b * INF_MB_SAMP_PER_SLOT;
            if (start + burst_n > n_total) return;
            const float f = INF_MB_FREQS_HZ[b];
            for (uint32_t i = 0; i < burst_n; i++) {
                float t = (float)i / (float)INF_SAMPLE_RATE;
                float env = 1.0f;
                if (i < fade_n)               env = 0.5f * (1.0f - cosf(3.14159265f * (float)i / (float)fade_n));
                else if (i > burst_n - fade_n) env = 0.5f * (1.0f - cosf(3.14159265f * (float)(burst_n - i) / (float)fade_n));
                float s = env * sinf(two_pi * f * t);
                out[start + i] = (int16_t)(s * 30000.0f * INF_MB_VOLUME);
            }
        }
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

/* ---- Feature extraction (STUB) ----
 *
 * Real implementation will: (1) deconvolve via matched filter against the
 * known excitation, (2) take 1024-pt rFFT of the RIR window, (3) log-mag,
 * (4) decimate to INF_N_BINS for the model. PowerQuad FFT covers (2).
 *
 * The stub here just computes per-band RMS energy of the captured signal
 * — enough to demonstrate the spectrum bar moves with mic input. */
static void extract_features(const int16_t *audio, size_t n, float *spec, size_t nb)
{
    const size_t chunk = n / nb;
    for (size_t b = 0; b < nb; b++) {
        const int16_t *p = audio + b * chunk;
        int64_t sumsq = 0;
        for (size_t i = 0; i < chunk; i++) {
            int32_t v = p[i];
            sumsq += (int64_t)v * v;
        }
        spec[b] = sqrtf((float)sumsq / (float)chunk) / 32768.0f;
    }
}

/* ---- Model inference (STUB) ----
 *
 * Real implementation will be a CMSIS-NN INT8 model exported from eIQ.
 * This stub picks the bin with max energy and maps to a class. Good
 * enough to verify the display + control flow before the trained model
 * lands. */
static void infer(const float *spec, size_t nb,
                  uint8_t *class_out, float *prob_out)
{
    /* Pick band of peak energy as a proxy for class. */
    size_t peak = 0;
    float pmax = spec[0];
    for (size_t i = 1; i < nb; i++) {
        if (spec[i] > pmax) { pmax = spec[i]; peak = i; }
    }
    *class_out = (uint8_t)(peak * INF_N_CLASSES / nb);
    if (*class_out >= INF_N_CLASSES) *class_out = INF_N_CLASSES - 1;
    /* Pseudo-probability from peak/sum. */
    float sum = 0.0f;
    for (size_t i = 0; i < nb; i++) sum += spec[i];
    *prob_out = (sum > 0.0f) ? (pmax / sum) * (float)nb / (float)INF_N_CLASSES : 0.0f;
    if (*prob_out > 1.0f) *prob_out = 1.0f;
}

/* ---- Display ---- */

static void tft_init(void)
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
        .cs_gpio      = BOARD_ILI_CS_GPIO,  .cs_pin  = BOARD_ILI_CS_PIN,
        .dc_gpio      = BOARD_ILI_DC_GPIO,  .dc_pin  = BOARD_ILI_DC_PIN,
        .res_gpio     = BOARD_ILI_RES_GPIO, .res_pin = BOARD_ILI_RES_PIN,
        .bl_gpio      = BOARD_ILI_BL_GPIO,  .bl_pin  = BOARD_ILI_BL_PIN,
        .rotation     = ILI9341_ROT_PORTRAIT,
    };
    (void)ili9341_init(&s_tft);
    (void)ili9341_fill_screen(&s_tft, ILI9341_BLACK);
    (void)ili9341_fill_rect(&s_tft, 0, 0, 240, 28, ILI9341_NAVY);
    (void)ili9341_draw_string(&s_tft, 6, 7, "IchiPing inference",
                              ILI9341_WHITE, ILI9341_NAVY, 2);
}

static void display_result(uint16_t seq, uint8_t class_id, float prob,
                           const float *spec, size_t nb)
{
    /* Header redraw not needed; class panel below header. */
    (void)ili9341_fill_rect(&s_tft, 0, 32, 240, 80, ILI9341_BLACK);

    char line[40];
    snprintf(line, sizeof(line), "seq %4u", (unsigned)seq);
    (void)ili9341_draw_string(&s_tft, 6, 34, line, ILI9341_GREY, ILI9341_BLACK, 2);

    snprintf(line, sizeof(line), "%s", INF_CLASS_NAMES[class_id]);
    (void)ili9341_draw_string(&s_tft, 6, 58, line, ILI9341_ORANGE, ILI9341_BLACK, 3);

    snprintf(line, sizeof(line), "p = %.2f", (double)prob);
    (void)ili9341_draw_string(&s_tft, 6, 88, line, ILI9341_GREEN, ILI9341_BLACK, 2);

    /* Spectrum strip: simple vertical bars at bottom. */
    const uint16_t bar_top = 130, bar_h = 180, bar_w = 240 / (uint16_t)nb;
    (void)ili9341_fill_rect(&s_tft, 0, bar_top, 240, bar_h, ILI9341_BLACK);
    for (size_t i = 0; i < nb; i++) {
        float v = spec[i] * 4.0f;            /* visual gain */
        if (v > 1.0f) v = 1.0f;
        uint16_t h = (uint16_t)(v * (float)bar_h);
        (void)ili9341_fill_rect(&s_tft,
                                (uint16_t)(i * bar_w),
                                (uint16_t)(bar_top + bar_h - h),
                                bar_w - 1, h,
                                ILI9341_CYAN);
    }
}

/* ---- main ---- */

int main(void)
{
    BOARD_InitHardware();
    systick_init_1ms();

    /* SW3 same as 08. */
    const gpio_pin_config_t btn = { kGPIO_DigitalInput, 0 };
    GPIO_PinInit(BOARD_USER_BUTTON_GPIO, BOARD_USER_BUTTON_PIN, &btn);
    GPIO_SetPinInterruptConfig(BOARD_USER_BUTTON_GPIO, BOARD_USER_BUTTON_PIN,
                               kGPIO_InterruptFallingEdge);
    EnableIRQ(GPIO00_IRQn);

    sai_mic_config_t mcfg = {
        .sai_base = BOARD_MIC_SAI_BASE, .sai_clk_hz = BOARD_MIC_SAI_CLK_FREQ,
        .sample_rate_hz = INF_SAMPLE_RATE, .bit_depth = 16,
    };
    sai_speaker_config_t scfg = {
        .sai_base = BOARD_SPK_SAI_BASE, .sai_clk_hz = BOARD_SPK_SAI_CLK_FREQ,
        .sample_rate_hz = INF_SAMPLE_RATE,
    };
    (void)sai_mic_init(&s_mic, &mcfg);
    (void)sai_speaker_init(&s_spk, &scfg);

    tft_init();
    render_multiband(s_excite, INF_WINDOW_SAMP);

    (void)ili9341_draw_string(&s_tft, 6, 120, "press SW3 to start",
                              ILI9341_WHITE, ILI9341_BLACK, 2);

    /* PC-side companion: pc/inference_client.py listens to these lines.
     * Banner identifies the firmware build so the client can sanity-check
     * the connection. */
    PRINTF("\r\nINFO IchiPing 10_inference build " __DATE__ " " __TIME__ "\r\n");
    PRINTF("INFO classes:");
    for (int i = 0; i < INF_N_CLASSES; i++) PRINTF(" %s", INF_CLASS_NAMES[i]);
    PRINTF("\r\nINFO press SW3 to start\r\n");

    uint16_t seq = 0;
    for (;;) {
        if (s_button_evt) {
            s_button_evt = false;
            s_running = !s_running;
            PRINTF("INFO state=%s\r\n", s_running ? "RUNNING" : "PAUSED");
        }
        if (!s_running) { __WFI(); continue; }

        uint32_t cycle_start = s_uptime_ms;
        seq++;

        play_and_capture(s_excite, s_audio, INF_WINDOW_SAMP);
        uint32_t t_cap = s_uptime_ms;
        extract_features(s_audio, INF_WINDOW_SAMP, s_spectrum, INF_N_BINS);
        uint8_t  class_id;
        float    prob;
        infer(s_spectrum, INF_N_BINS, &class_id, &prob);
        uint32_t t_inf = s_uptime_ms;
        display_result(seq, class_id, prob, s_spectrum, INF_N_BINS);

        /* Single machine-readable line per inference for the PC client.
         * Format: RESULT seq=<n> class=<name> prob=<0..1> cap_ms=<n> infer_ms=<n> */
        PRINTF("RESULT seq=%u class=%s prob=%.3f cap_ms=%u infer_ms=%u\r\n",
               (unsigned)seq, INF_CLASS_NAMES[class_id], (double)prob,
               (unsigned)(t_cap - cycle_start),
               (unsigned)(t_inf - t_cap));

        uint32_t elapsed = s_uptime_ms - cycle_start;
        if (elapsed < INF_CYCLE_MS) delay_ms(INF_CYCLE_MS - elapsed);
    }
}
