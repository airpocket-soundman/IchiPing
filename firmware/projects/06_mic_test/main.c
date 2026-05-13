/*
 * IchiPing — INMP441 mic test firmware.
 *
 * Captures 0.5 s windows continuously on SAI1 RX and prints simple
 * statistics (peak, RMS, dc-bias) over the OpenSDA debug UART so you can
 * confirm the mic + I²S routing without needing PC tools beyond a serial
 * terminal.
 *
 * Sanity checks performed each window:
 *   - peak absolute value (should track ambient noise / a tap on the mic)
 *   - RMS                  (should ≥ ~10 in a quiet room, much higher when
 *                           you speak / tap)
 *   - DC offset            (should be near 0; INMP441 is AC-coupled)
 *   - zero-crossings/sec   (rough sanity of audio band content)
 *
 * Wiring (FRDM-MCXN947 Arduino headers, see hardware/wiring.md §2):
 *   INMP441      FRDM-MCXN947
 *   VDD          3V3
 *   GND          GND
 *   L/R          GND  (selects the left channel)
 *   WS           SAI1_FS   (frame select / LRCLK)
 *   SCK          SAI1_BCLK
 *   SD           SAI1_RXD
 *
 * Build:
 *   - Import projects/06_mic_test/ as an MCUXpresso for VS Code project.
 *   - Verify the SAI1 pin mux in MCUXpresso Pins tool (default assignment
 *     in pin_mux.c is a placeholder, see the TODO inside).
 *   - Open the OpenSDA virtual COM at 115200 8N1 and observe the print.
 */

#include "pin_mux.h"
#include "clock_config.h"
#include "board.h"
#include "fsl_debug_console.h"

#include "sai_mic.h"
#include "app.h"

#define MIC_SAMPLE_RATE   16000u
#define MIC_WINDOW_MS     500u
#define MIC_WINDOW_SAMP   ((MIC_SAMPLE_RATE * MIC_WINDOW_MS) / 1000u)

static int16_t s_window[MIC_WINDOW_SAMP];

static volatile uint32_t s_uptime_ms = 0;
void SysTick_Handler(void) { s_uptime_ms++; }
static void systick_init_1ms(void) { (void)SysTick_Config(SystemCoreClock / 1000u); }

static void print_stats(uint32_t cycle, const int16_t *x, size_t n)
{
    int32_t  peak = 0;
    int64_t  sum  = 0;
    int64_t  sumsq = 0;
    uint32_t zc   = 0;

    int16_t prev = x[0];
    for (size_t i = 0; i < n; i++) {
        int32_t v = x[i];
        int32_t a = v < 0 ? -v : v;
        if (a > peak) peak = a;
        sum   += v;
        sumsq += (int64_t)v * (int64_t)v;
        if ((prev < 0 && v >= 0) || (prev >= 0 && v < 0)) zc++;
        prev = (int16_t)v;
    }

    int32_t mean = (int32_t)(sum / (int64_t)n);
    /* integer sqrt good enough for a sanity print */
    int64_t rms2 = sumsq / (int64_t)n;
    int64_t r = 1; while (r * r <= rms2) r++; r--;
    uint32_t zcr = (uint32_t)((zc * MIC_SAMPLE_RATE) / n);

    PRINTF("[%4u] peak=%6d  rms=%5d  dc=%+5d  zcr=%5uHz\r\n",
           (unsigned)cycle, (int)peak, (int)r, (int)mean, (unsigned)zcr);
}

int main(void)
{
    BOARD_InitHardware();
    systick_init_1ms();

    PRINTF("\r\nIchiPing 06_mic_test  ─  INMP441 on SAI1 RX @ %u Hz\r\n",
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
        s = sai_mic_record_blocking(&mic, s_window, MIC_WINDOW_SAMP);
        if (s != kStatus_Success) {
            PRINTF("  capture error %d\r\n", (int)s);
            continue;
        }
        print_stats(cycle, s_window, MIC_WINDOW_SAMP);
    }
}
