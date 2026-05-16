/*
 * IchiPing — INMP441 / MSM261 mic test firmware.
 *
 * Captures 0.5 s windows continuously on SAI1 RX and prints simple
 * statistics (peak, RMS, dc-bias, zero-crossings) over the OpenSDA
 * debug UART so you can confirm the mic + I²S routing without needing
 * PC tools beyond a serial terminal.
 *
 * Float pipeline: the driver normalises each 32-bit FIFO word to
 * [-1.0f, +1.0f) so the full hardware dynamic range survives the
 * capture stage. Stats are printed back in scaled int form (multiplied
 * by 32768) so they stay readable on a small serial terminal and
 * comparable with INT16-era logs in BRINGUP_NOTES.md.
 *
 * Wiring (FRDM-MCXN947 Arduino headers, see hardware/wiring.md §2):
 *   MSM261       FRDM-MCXN947
 *   V  (VDD)     3V3
 *   G  (GND)     GND
 *   LR (L/R)     GND  (selects the active output mode on this clone)
 *   WS           SAI1_TX_FS   (J1.11 / P3_17 Alt10)
 *   CK (SCK)     SAI1_TX_BCLK (J1.1  / P3_16 Alt10 — via SJ11=1-2)
 *   DA (SD)      SAI1_RXD0    (J1.15 / P3_21 Alt10)
 */

#include "pin_mux.h"
#include "clock_config.h"
#include "board.h"
#include "fsl_debug_console.h"

#include "sai_mic.h"
#include "app.h"

#include <math.h>

extern void BOARD_InitHardware(void);   /* defined in cm33_core0/hardware_init.c */

#define MIC_SAMPLE_RATE   16000u
#define MIC_WINDOW_MS     500u
#define MIC_WINDOW_SAMP   ((MIC_SAMPLE_RATE * MIC_WINDOW_MS) / 1000u)

static float s_window[MIC_WINDOW_SAMP];

static volatile uint32_t s_uptime_ms = 0;
void SysTick_Handler(void) { s_uptime_ms++; }
static void systick_init_1ms(void) { (void)SysTick_Config(SystemCoreClock / 1000u); }

/* Print stats in int16-equivalent scale (×32768) so the magnitudes are
 * recognisable: a full-scale ±1.0f signal shows as ±32768, matching the
 * legacy int16 log format used earlier in BRINGUP_NOTES.md. */
static void print_stats(uint32_t cycle, const float *x, size_t n)
{
    float peak  = 0.0f;
    float sum   = 0.0f;
    float sumsq = 0.0f;
    uint32_t zc = 0;

    float prev = x[0];
    for (size_t i = 0; i < n; i++) {
        float v = x[i];
        float a = v < 0.0f ? -v : v;
        if (a > peak) peak = a;
        sum   += v;
        sumsq += v * v;
        if ((prev < 0.0f && v >= 0.0f) || (prev >= 0.0f && v < 0.0f)) zc++;
        prev = v;
    }

    float mean = sum / (float)n;
    float rms  = sqrtf(sumsq / (float)n);
    uint32_t zcr = (uint32_t)((zc * MIC_SAMPLE_RATE) / n);

    int peak_i = (int)(peak * 32768.0f);
    int rms_i  = (int)(rms  * 32768.0f);
    int mean_i = (int)(mean * 32768.0f);

    /* Manual sign for dc — MCUXpresso lite PRINTF prints negative ints as
     * unsigned (e.g. -82 → 4294967214), so we split the sign character. */
    char mean_sign = mean_i < 0 ? '-' : ' ';
    int  mean_abs  = mean_i < 0 ? -mean_i : mean_i;

    PRINTF("[%4u] peak=%6d  rms=%5d  dc=%c%4d  zcr=%5uHz\r\n",
           (unsigned)cycle, peak_i, rms_i, mean_sign, mean_abs, (unsigned)zcr);
}

int main(void)
{
    BOARD_InitHardware();
    systick_init_1ms();

    PRINTF("\r\nIchiPing 06_mic_test  --  MSM261/INMP441 on SAI1 RX @ %u Hz [float pipeline]\r\n",
           (unsigned)MIC_SAMPLE_RATE);

    sai_mic_t mic;
    sai_mic_config_t cfg = {
        .sai_base       = BOARD_MIC_SAI_BASE,
        .sai_clk_hz     = BOARD_MIC_SAI_CLK_FREQ,
        .sample_rate_hz = MIC_SAMPLE_RATE,
        .bit_depth      = 16,
    };
    status_t s = sai_mic_init(&mic, &cfg);
    if (s != kStatus_Success) {
        PRINTF("FAIL: sai_mic_init = %d\r\n", (int)s);
        for (;;) { __WFI(); }
    }

    uint32_t cycle = 0;
    for (;;) {
        cycle++;
        s = sai_mic_record_blocking_f32(&mic, s_window, MIC_WINDOW_SAMP);
        if (s != kStatus_Success) {
            PRINTF("  capture error %d\r\n", (int)s);
            continue;
        }
        print_stats(cycle, s_window, MIC_WINDOW_SAMP);
    }
}
