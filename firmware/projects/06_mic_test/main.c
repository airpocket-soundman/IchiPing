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
#include "fsl_sai.h"                /* for SAI1 peripheral instance macro */

#include "sai_mic.h"
#include "app.h"

extern void BOARD_InitHardware(void);   /* defined in cm33_core0/hardware_init.c */

#define MIC_SAMPLE_RATE   16000u
#define MIC_WINDOW_MS     500u
#define MIC_WINDOW_SAMP   ((MIC_SAMPLE_RATE * MIC_WINDOW_MS) / 1000u)

/* NOTE: this build of main.c is a DIAGNOSTIC variant — it does its own
 * polling FIFO read and dumps SAI registers. The usual capture buffer
 * (s_window) and the print_stats helper are removed for the duration of
 * the diagnostic phase to keep -Werror=unused-* quiet. Once the SAI is
 * verified to actually deliver samples, we restore them along with the
 * sai_mic_record_blocking call site. */

static volatile uint32_t s_uptime_ms = 0;
void SysTick_Handler(void) { s_uptime_ms++; }
static void systick_init_1ms(void) { (void)SysTick_Config(SystemCoreClock / 1000u); }

int main(void)
{
    BOARD_InitHardware();
    systick_init_1ms();

    PRINTF("\r\nIchiPing 06_mic_test  --  INMP441 on SAI1 RX @ %u Hz\r\n",
           (unsigned)MIC_SAMPLE_RATE);

    /* Print the SAI clock we plan to feed into the bit-clock divider so a
     * wrong CLOCK_GetSaiClkFreq result (0 or wildly off) is visible before
     * we hang on a FIFO read that will never satisfy. */
    PRINTF("  sai_clk_hz = %u  (expect ~48000000 for FRO HF)\r\n",
           (unsigned)BOARD_MIC_SAI_CLK_FREQ);
    PRINTF("  init SAI...\r\n");

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
    PRINTF("  SAI init OK\r\n");

    /* --- SAI register dump (read once, right after init) --- */
    I2S_Type *sai = (I2S_Type *)BOARD_MIC_SAI_BASE;
    PRINTF("\r\n--- SAI register state ---\r\n");
    PRINTF("  TCSR=0x%08X  TCR1=0x%08X  TCR2=0x%08X  TCR3=0x%08X\r\n",
           (unsigned)sai->TCSR, (unsigned)sai->TCR1,
           (unsigned)sai->TCR2, (unsigned)sai->TCR3);
    PRINTF("  TCR4=0x%08X  TCR5=0x%08X  TMR =0x%08X\r\n",
           (unsigned)sai->TCR4, (unsigned)sai->TCR5, (unsigned)sai->TMR);
    PRINTF("  RCSR=0x%08X  RCR1=0x%08X  RCR2=0x%08X  RCR3=0x%08X\r\n",
           (unsigned)sai->RCSR, (unsigned)sai->RCR1,
           (unsigned)sai->RCR2, (unsigned)sai->RCR3);
    PRINTF("  RCR4=0x%08X  RCR5=0x%08X  RMR =0x%08X\r\n",
           (unsigned)sai->RCR4, (unsigned)sai->RCR5, (unsigned)sai->RMR);
    PRINTF("  MCR =0x%08X  (MOE bit30 must be 1 to use internal MCLK)\r\n",
           (unsigned)sai->MCR);
    PRINTF("  TCSR.TE (bit31) = %u   RCSR.RE (bit31) = %u\r\n",
           (unsigned)((sai->TCSR >> 31) & 1u),
           (unsigned)((sai->RCSR >> 31) & 1u));
    PRINTF("  MCR.MOE (bit30) = %u   TCR2.MSEL (bits 27:26) = %u\r\n",
           (unsigned)((sai->MCR >> 30) & 1u),
           (unsigned)((sai->TCR2 >> 26) & 0x3u));
    PRINTF("  TCR2.DIV (bits 7:0) = %u  (BCLK = sai_clk / (2*(DIV+1)))\r\n",
           (unsigned)(sai->TCR2 & 0xFFu));
    PRINTF("\r\n");

    /* --- Polling capture with periodic status print ---
     * Replaces sai_mic_record_blocking so we can see RX FIFO state every
     * 200 ms even if no data ever arrives. Status bits in RCSR:
     *   bit 31 RE  = receive enabled
     *   bit 19 WSF = word start flag (sees FS edge)
     *   bit 18 SEF = sync error
     *   bit 17 FEF = FIFO error (underrun/overrun)
     *   bit 16 FWF = FIFO warning
     * RFR0 (= sai->RFR[0]):
     *   bits 31:16 read pointer, bits 15:0 write pointer; if they differ
     *   the FIFO has data. If write pointer stays at 0, no data ever
     *   reached the SAI RX FIFO. */
    SAI_RxEnable(sai, true);

    PRINTF("Polling RX FIFO (200 ms cadence)...\r\n");
    uint32_t last_status_ms = s_uptime_ms;
    uint32_t samples_got    = 0;
    int16_t  last_left      = 0;
    int32_t  peak           = 0;
    while (samples_got < MIC_WINDOW_SAMP) {
        uint32_t rcsr = sai->RCSR;
        uint32_t rfr  = sai->RFR[0];
        uint16_t wp   = (uint16_t)(rfr & 0xFFFFu);
        uint16_t rp   = (uint16_t)((rfr >> 16) & 0xFFFFu);
        if (wp != rp) {
            /* FIFO has data — read one word */
            uint32_t left = sai->RDR[0];
            int32_t  s32  = (int32_t)left >> 16;
            int32_t  a    = s32 < 0 ? -s32 : s32;
            if (a > peak) peak = a;
            last_left = (int16_t)s32;
            samples_got++;
        }
        uint32_t now = s_uptime_ms;
        if ((uint32_t)(now - last_status_ms) >= 200u) {
            last_status_ms = now;
            PRINTF("  t=%5u  RCSR=0x%08X  RFR0=0x%08X  got=%5u  last=%6d  peak=%5d\r\n",
                   (unsigned)now, (unsigned)rcsr, (unsigned)rfr,
                   (unsigned)samples_got, (int)last_left, (int)peak);
        }
    }
    PRINTF("Captured %u samples, exiting.\r\n", (unsigned)samples_got);
    for (;;) { __WFI(); }
}
