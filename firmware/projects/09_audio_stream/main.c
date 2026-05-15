/*
 * IchiPing — continuous audio stream firmware.
 *
 * Captures real audio from the INMP441 (SAI1 RX) and streams ICHP frames
 * over the OpenSDA UART at 921600 bps. Same wire format as
 * 01_dummy_emitter, so pc/receiver.py works unchanged — the difference is
 * that the int16 payload is the actual microphone signal instead of the
 * synthesized chirp.
 *
 * This is the building block for v0.2 ("real audio, no speaker yet").
 * Servo angles in the frame are left at 0 — they are populated when this
 * is plugged into the full pipeline later (v0.5+).
 *
 * Default cadence is one frame every 500 ms (2 s window @ 16 kHz =
 * 64 KB of int16). At 921600 bps each frame takes ~560 ms on the wire, so
 * the loop effectively transmits continuously back-to-back. Switch to
 * 05_usb_cdc_emitter when you need real cadence headroom.
 */

#include "pin_mux.h"
#include "clock_config.h"
#include "board.h"
#include "fsl_debug_console.h"
#include "fsl_lpuart.h"
#include "fsl_sai.h"                /* for SAI1 peripheral instance macro */

#include "sai_mic.h"
#include "ichiping_frame.h"
#include "app.h"

extern void BOARD_InitHardware(void);   /* defined in cm33_core0/hardware_init.c */

#define STREAM_SAMPLE_RATE   16000u
#define STREAM_WINDOW_SAMP   32000u    /* 2 s */
#define STREAM_BAUD          921600u

static int16_t s_audio[STREAM_WINDOW_SAMP];
static uint8_t s_tx_buf[ICHP_HEADER_SIZE
                        + STREAM_WINDOW_SAMP * sizeof(int16_t)
                        + ICHP_CRC_SIZE];

static volatile uint32_t s_uptime_ms = 0;
void SysTick_Handler(void) { s_uptime_ms++; }
static void systick_init_1ms(void) { (void)SysTick_Config(SystemCoreClock / 1000u); }

static void uart_init_binary(void)
{
    lpuart_config_t cfg;
    LPUART_GetDefaultConfig(&cfg);
    cfg.baudRate_Bps = STREAM_BAUD;
    cfg.enableTx     = true;
    cfg.enableRx     = false;
    LPUART_Init(BOARD_DEBUG_UART_BASEADDR, &cfg, BOARD_DEBUG_UART_CLK_FREQ);
}

int main(void)
{
    BOARD_InitHardware();
    systick_init_1ms();

    PRINTF("\r\nIchiPing 09_audio_stream  --  INMP441 → ICHP frames @ %u bps\r\n",
           (unsigned)STREAM_BAUD);

    sai_mic_t mic;
    sai_mic_config_t mcfg = {
        .sai_base       = BOARD_MIC_SAI_BASE,
        .sai_clk_hz     = BOARD_MIC_SAI_CLK_FREQ,
        .sample_rate_hz = STREAM_SAMPLE_RATE,
        .bit_depth      = 16,
    };
    if (sai_mic_init(&mic, &mcfg) != kStatus_Success) {
        PRINTF("FAIL: sai_mic_init\r\n");
        for (;;) { __WFI(); }
    }
    uart_init_binary();

    uint16_t seq = 0;
    for (;;) {
        seq++;
        if (sai_mic_record_blocking(&mic, s_audio, STREAM_WINDOW_SAMP) != kStatus_Success) {
            continue;
        }
        float servo_deg[5] = {0.0f, 0.0f, 0.0f, 0.0f, 0.0f};
        size_t framed = ichp_pack_frame(s_tx_buf, sizeof(s_tx_buf),
                                        seq, s_uptime_ms,
                                        STREAM_SAMPLE_RATE,
                                        STREAM_WINDOW_SAMP,
                                        servo_deg, s_audio);
        if (framed > 0) {
            LPUART_WriteBlocking(BOARD_DEBUG_UART_BASEADDR, s_tx_buf, framed);
        }
    }
}
