/*
 * IchiPing — dummy serial emitter (MCUXpresso project skeleton)
 *
 * Generates synthetic chirp + reverb frames and pushes them out over the
 * debug UART (OpenSDA CDC virtual COM). PC-side receiver is in pc/receiver.py.
 *
 * Target board: FRDM-MCXN947
 * Toolchain   : MCUXpresso IDE 11.9+ / MCUXpresso SDK for MCXN947
 *
 * Build:
 *   1) New > Import SDK example > frdmmcxn947 > driver_examples/lpuart/polling
 *      (or any lpuart-based template) into a fresh workspace.
 *   2) Rename to "ichiping_dummy" and replace source/main.c with this file.
 *   3) Add ../firmware/source/{ichiping_frame.c, dummy_audio.c} as project
 *      sources (link or copy). Add ../firmware/include as Include path.
 *   4) Build & flash via OpenSDA. Open pc/receiver.py against the COM port.
 */

#include "pin_mux.h"
#include "clock_config.h"
#include "board.h"
#include "fsl_debug_console.h"
#include "fsl_lpuart.h"
#include "fsl_gpio.h"

#include "ichiping_frame.h"
#include "dummy_audio.h"

/* ----- Configuration ----- */

/* Use the OpenSDA debug UART for first bring-up.
 * For high-throughput swap to USB CDC later (see TODO in README).
 * Note: board.h casts BOARD_DEBUG_UART_BASEADDR to uint32_t, but the LPUART
 * driver wants an LPUART_Type *. Use the SDK LPUART4 macro directly. */
#ifndef ICHP_UART_BASE
#define ICHP_UART_BASE   LPUART4
#endif
#ifndef ICHP_UART_CLOCK
#define ICHP_UART_CLOCK  BOARD_DEBUG_UART_CLK_FREQ
#endif
#ifndef ICHP_UART_BAUD
#define ICHP_UART_BAUD   921600u                      /* OpenSDA tolerates this on MCX */
#endif

#define ICHP_SAMPLE_RATE   16000u
#define ICHP_SAMPLE_COUNT  32000u                     /* 2.0 s @ 16 kHz */
#define ICHP_FRAME_PERIOD_MS  3000u                   /* one frame every 3 s */

/* ----- Buffers (place in regular RAM; FRDM-MCXN947 has > 64 KB SRAM) ----- */

static int16_t s_audio_buf[ICHP_SAMPLE_COUNT];
static uint8_t s_tx_buf[ICHP_HEADER_SIZE + ICHP_SAMPLE_COUNT * sizeof(int16_t) + ICHP_CRC_SIZE];

/* ----- Tiny uptime counter using SysTick (1 ms tick) ----- */

static volatile uint32_t s_uptime_ms = 0;
void SysTick_Handler(void) { s_uptime_ms++; }

static void systick_init_1ms(void) {
    SysTick_Config(SystemCoreClock / 1000u);
}

static void delay_ms(uint32_t ms) {
    uint32_t end = s_uptime_ms + ms;
    while ((int32_t)(s_uptime_ms - end) < 0) {
        __WFI();
    }
}

/* ----- Inference-busy LED on Arduino A1 (see hardware/wiring.md §2.7) -----
 * Replace BOARD_LED_xx with the macros from pin_mux.h once the LED pin is
 * configured in MCUXpresso Config Tools. */
static inline void infer_led_on(void)  { /* GPIO_PinWrite(BOARD_LED_INFER_GPIO, BOARD_LED_INFER_PIN, 1u); */ }
static inline void infer_led_off(void) { /* GPIO_PinWrite(BOARD_LED_INFER_GPIO, BOARD_LED_INFER_PIN, 0u); */ }

/* ----- Application ----- */

static void uart_init_binary(void) {
    lpuart_config_t cfg;
    LPUART_GetDefaultConfig(&cfg);
    cfg.baudRate_Bps = ICHP_UART_BAUD;
    cfg.enableTx = true;
    cfg.enableRx = false;
    LPUART_Init(ICHP_UART_BASE, &cfg, ICHP_UART_CLOCK);
}

static void random_servo_angles(uint16_t seq, float out[5]) {
    /* Deterministic but varied per-frame angles for PoC */
    for (int i = 0; i < 5; i++) {
        uint32_t x = (uint32_t)seq * 2654435761u + (uint32_t)i * 0x9E3779B1u;
        out[i] = (float)(x % 91u);   /* 0..90 deg */
    }
}

int main(void) {
    BOARD_InitBootPins();
    BOARD_InitBootClocks();
    BOARD_InitDebugConsole();   /* used for human-readable status only */

    uart_init_binary();
    systick_init_1ms();
    dummy_audio_seed(0xC4C4C4C4u);

    PRINTF("IchiPing dummy emitter: %u Hz, %u samples/frame, %u baud\r\n",
           (unsigned)ICHP_SAMPLE_RATE,
           (unsigned)ICHP_SAMPLE_COUNT,
           (unsigned)ICHP_UART_BAUD);

    uint16_t seq = 0;
    for (;;) {
        infer_led_on();

        float servo_deg[5];
        random_servo_angles(seq, servo_deg);

        dummy_audio_generate(s_audio_buf, ICHP_SAMPLE_COUNT, ICHP_SAMPLE_RATE);

        size_t frame_size = ichp_pack_frame(
            s_tx_buf, sizeof(s_tx_buf),
            seq,
            s_uptime_ms,
            ICHP_SAMPLE_RATE,
            ICHP_SAMPLE_COUNT,
            servo_deg,
            s_audio_buf);

        if (frame_size > 0) {
            LPUART_WriteBlocking(ICHP_UART_BASE, s_tx_buf, frame_size);
        }

        infer_led_off();
        seq++;
        delay_ms(ICHP_FRAME_PERIOD_MS);
    }
}
