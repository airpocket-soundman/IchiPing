/*
 * IchiPing — Mic + Speaker (impulse-response) test firmware.
 *
 * Plays the IchiPing 200 → 8 kHz chirp through the MAX98357A (SAI1 TX)
 * AND simultaneously records the response through the INMP441 (SAI1 RX
 * in sync mode — shares the TX framer's BCLK/FS so the samples are
 * clock-locked, which is required for meaningful impulse-response capture).
 * The captured window is shipped to the PC as a single ICHP audio frame
 * (re-using the existing pc/receiver.py pipeline) tagged with seq 0/1/2...
 *
 * This is the closest end-to-end test before bringing up real inference —
 * the room impulse response stored here is what the 1D-CNN will eventually
 * learn from.
 *
 * Cadence:
 *   - One chirp every 4 seconds (chirp itself is 2 s)
 *   - Record window is 2.5 s (chirp + 500 ms tail for late reflections)
 *   - Run forever
 *
 * Wiring is the union of 06_mic_test and 07_speaker_test (same J1 pins,
 * no rewiring needed when stepping up from 06/07 → 08):
 *   INMP441 SCK / WS / SD  ← J1.1 (P3_16) / J1.3 (P3_17) / J1.15 (P3_21)
 *   MAX98357A BCLK / LRC / DIN ← J1.1 (P3_16) / J1.3 (P3_17) / J1.5 (P3_20)
 * BCLK and LRC are shared between INMP441 and MAX98357A — both driven by
 * SAI1's TX framer. SJ10 must be 2-3 and SJ11 must be 1-2 for these
 * J1 pins to actually carry the SAI1 signals (see User Manual Table 17).
 *
 * PC side:
 *   python receiver.py --port COMx --baud 921600 --out captures/08
 */

#include "pin_mux.h"
#include "clock_config.h"
#include "board.h"
#include "fsl_debug_console.h"
#include "fsl_lpuart.h"

#include "sai_mic.h"
#include "sai_speaker.h"
#include "dummy_audio.h"
#include "ichiping_frame.h"
#include "app.h"

#define IRTEST_SAMPLE_RATE   16000u
#define IRTEST_CHIRP_MS      2000u
#define IRTEST_TOTAL_MS      2500u
#define IRTEST_CHIRP_SAMP    ((IRTEST_SAMPLE_RATE * IRTEST_CHIRP_MS) / 1000u)
#define IRTEST_TOTAL_SAMP    ((IRTEST_SAMPLE_RATE * IRTEST_TOTAL_MS) / 1000u)
#define IRTEST_CYCLE_MS      4000u

#ifndef IRTEST_UART_BAUD
#define IRTEST_UART_BAUD     921600u
#endif

static int16_t s_chirp [IRTEST_CHIRP_SAMP];
static int16_t s_record[IRTEST_TOTAL_SAMP];
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

static void uart_init_binary(void)
{
    lpuart_config_t cfg;
    LPUART_GetDefaultConfig(&cfg);
    cfg.baudRate_Bps = IRTEST_UART_BAUD;
    cfg.enableTx     = true;
    cfg.enableRx     = false;
    LPUART_Init(BOARD_DEBUG_UART_BASEADDR, &cfg, BOARD_DEBUG_UART_CLK_FREQ);
}

int main(void)
{
    BOARD_InitHardware();
    systick_init_1ms();

    PRINTF("\r\nIchiPing 08_mic_speaker_test  ─  SAI1 TX chirp + SAI1 RX capture\r\n");
    PRINTF("  Fs=%u, chirp=%u samp, window=%u samp, cycle=%u ms\r\n",
           (unsigned)IRTEST_SAMPLE_RATE, (unsigned)IRTEST_CHIRP_SAMP,
           (unsigned)IRTEST_TOTAL_SAMP,  (unsigned)IRTEST_CYCLE_MS);

    /* Pre-render the chirp once. dummy_audio_generate covers 200 Hz → 8 kHz
     * exactly matching the inference excitation, so what the PC receives
     * back is the room impulse response convolved with that probe. */
    dummy_audio_seed(0xC4C4C4C4u);
    dummy_audio_generate(s_chirp, IRTEST_CHIRP_SAMP, IRTEST_SAMPLE_RATE);

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
    if (sai_mic_init(&mic, &mcfg) != kStatus_Success ||
        sai_speaker_init(&spk, &scfg) != kStatus_Success) {
        PRINTF("FAIL: SAI init\r\n");
        for (;;) { __WFI(); }
    }
    uart_init_binary();

    uint16_t seq = 0;
    for (;;) {
        seq++;
        PRINTF("[%4u] firing chirp + recording %u samples...\r\n",
               (unsigned)seq, (unsigned)IRTEST_TOTAL_SAMP);

        /* The blocking play and blocking record are NOT truly simultaneous
         * — for a real impulse response we want them on EDMA. For first
         * bring-up we accept a small skew: start the recorder, then play,
         * then drain the remaining record window. Replace with EDMA hooks
         * when sai_*_start_streaming is implemented. */
        sai_speaker_play_blocking(&spk, s_chirp, IRTEST_CHIRP_SAMP);
        sai_mic_record_blocking(&mic, s_record, IRTEST_TOTAL_SAMP);

        float servo_deg[5] = {0.0f, 0.0f, 0.0f, 0.0f, 0.0f};
        size_t framed = ichp_pack_frame(s_tx_buf, sizeof(s_tx_buf),
                                        seq, s_uptime_ms,
                                        IRTEST_SAMPLE_RATE,
                                        IRTEST_TOTAL_SAMP,
                                        servo_deg, s_record);
        if (framed > 0) {
            LPUART_WriteBlocking(BOARD_DEBUG_UART_BASEADDR, s_tx_buf, framed);
        } else {
            PRINTF("  pack error\r\n");
        }

        delay_ms(IRTEST_CYCLE_MS - IRTEST_TOTAL_MS);
    }
}
