/*
 * IchiPing -- 00_demo: integration demo combining the bring-up projects
 * that have already been individually verified on the FRDM-MCXN947.
 *
 *   Subsystem        Source of truth        Status
 *   ---------------  ---------------------  -------------------------
 *   ILI9341 TFT      03_ili9341_test        proven (this session)
 *   PCA9685/LU9685   02_servo_test          proven on LU9685 backend
 *   921600 frame TX  01_dummy_emitter       proven via pc/receiver.py
 *
 * The screen mocks the training-tool UI that v0.4 will need: 5 room-state
 * tiles (window a/b/c, door AB/BC), a status line, and a frame counter.
 * Each demo step:
 *
 *   1. Advance every tile through CLOSED -> HALF -> OPEN (offset per tile
 *      so the screen never looks static).
 *   2. Convert each state to a servo angle (0/90/180 deg) and push the 5
 *      angles to the LU9685 in one bulk write.
 *   3. Generate a dummy chirp+reverb audio buffer, pack it into an ichp
 *      frame tagged with the current servo angles, and ship it out the
 *      OpenSDA UART at 921600 baud. pc/receiver.py picks it up.
 *
 * As real INMP441 capture, ML inference, button input etc. land, replace
 * the corresponding stub with the real path -- the surrounding scaffolding
 * (clocks, pin mux, UI, frame protocol) doesn't have to change.
 */

#include <stdbool.h>
#include <stdio.h>

#include "pin_mux.h"
#include "clock_config.h"
#include "board.h"
#include "fsl_debug_console.h"
#include "fsl_lpspi.h"
#include "fsl_lpi2c.h"
#include "fsl_lpuart.h"
#include "fsl_gpio.h"
#include "app.h"

#include "ili9341.h"
#include "servo_driver.h"
#include "ichiping_frame.h"
#include "dummy_audio.h"

/* hardware_init.c -- FRO12M -> FC1/FC2/FC4 attaches must run before any
 * LPSPI/LPI2C/LPUART register access. */
extern void BOARD_InitHardware(void);

/* ----- Tunables ----- */

#define ILI_SPI_BAUD_HZ        20000000U     /* 20 MHz works on 03_ili9341_test */
#define SERVO_I2C_BAUD         100000U
#define DEMO_FRAME_PERIOD_MS   3000U          /* one ichp frame every 3 s */
#define DEMO_UI_REFRESH_MS     500U           /* sub-frame UI tick */

#define DEMO_SAMPLE_RATE       16000U
#define DEMO_SAMPLE_COUNT      32000U         /* 2.0 s @ 16 kHz */

#define ROOM_COUNT             5
#define STATE_COUNT            3

/* ----- SysTick uptime ----- */

static volatile uint32_t s_uptime_ms = 0;
void SysTick_Handler(void) { s_uptime_ms++; }

static void systick_init_1ms(void) {
    (void)SysTick_Config(SystemCoreClock / 1000u);
}

static uint32_t now_ms(void) { return s_uptime_ms; }

/* Kept around for future blocking pauses (e.g. servo settle, panel reset
 * outside ili9341_init) -- marked unused so -Werror=unused-function lets
 * the build pass until the first caller appears. */
static void delay_ms(uint32_t ms) __attribute__((unused));
static void delay_ms(uint32_t ms) {
    uint32_t end = now_ms() + ms;
    while ((int32_t)(now_ms() - end) < 0) { __WFI(); }
}

/* ----- Subsystem bring-up ----- */

static void ili_gpio_outputs_init(void) {
    gpio_pin_config_t out = { kGPIO_DigitalOutput, 1 };
    GPIO_PinInit(BOARD_ILI_CS_GPIO,  BOARD_ILI_CS_PIN,  &out);
    GPIO_PinInit(BOARD_ILI_RES_GPIO, BOARD_ILI_RES_PIN, &out);
    GPIO_PinInit(BOARD_ILI_DC_GPIO,  BOARD_ILI_DC_PIN,  &out);
    GPIO_PinInit(BOARD_ILI_BL_GPIO,  BOARD_ILI_BL_PIN,  &out);
}

static void servo_i2c_init(void) {
    lpi2c_master_config_t cfg;
    LPI2C_MasterGetDefaultConfig(&cfg);
    cfg.baudRate_Hz = SERVO_I2C_BAUD;
    LPI2C_MasterInit(BOARD_SERVO_I2C_BASEADDR, &cfg, BOARD_SERVO_I2C_CLK_FREQ);
}

/* Re-init LPUART4 at 921600 8N1 TX-only so binary frames go out at full
 * speed. The debug console driver was just bound to 115200; this stomps
 * its baud rate, but PRINTF after this point still calls into the same
 * LPUART so the bytes simply leave at 921600. pc/receiver.py opens the
 * port at 921600 and accepts the mixed traffic (banner bytes precede the
 * first 'ICHP' magic and are skipped). */
static void frame_uart_init(void) {
    lpuart_config_t cfg;
    LPUART_GetDefaultConfig(&cfg);
    cfg.baudRate_Bps = BOARD_FRAME_UART_BAUD;
    cfg.enableTx     = true;
    cfg.enableRx     = false;
    LPUART_Init(BOARD_FRAME_UART_BASE, &cfg, BOARD_FRAME_UART_CLK_FREQ);
}

/* ----- Training-tool UI state ----- */

typedef enum { ST_CLOSED = 0, ST_HALF = 1, ST_OPEN = 2 } room_state_t;

static const char *const ROOM_NAMES[ROOM_COUNT] = {
    "WIN a", "WIN b", "WIN c", "DOOR AB", "DOOR BC",
};

static const char *state_label(room_state_t s) {
    switch (s) {
        case ST_CLOSED: return "CLOSED";
        case ST_HALF:   return "HALF";
        case ST_OPEN:   return "OPEN";
    }
    return "?";
}

static uint16_t state_color(room_state_t s) {
    switch (s) {
        case ST_CLOSED: return ILI9341_GREEN;
        case ST_HALF:   return ILI9341_ORANGE;
        case ST_OPEN:   return ILI9341_RED;
    }
    return ILI9341_GREY;
}

static float state_to_deg(room_state_t s) {
    /* Map CLOSED/HALF/OPEN -> 0/90/180 deg so the bench horns trace the
     * full sweep visibly. Real units (window slat angle) come in v0.4. */
    switch (s) {
        case ST_CLOSED: return 0.0f;
        case ST_HALF:   return 90.0f;
        case ST_OPEN:   return 180.0f;
    }
    return 0.0f;
}

/* ----- UI rendering primitives ----- */

#define UI_BG       0x18C3u    /* dark slate */
#define UI_FG       ILI9341_WHITE
#define UI_DIM      ILI9341_GREY
#define UI_ACCENT   ILI9341_CYAN

static void ui_clear(ili9341_t *d) {
    (void)ili9341_fill_screen(d, UI_BG);
}

static void ui_draw_title(ili9341_t *d) {
    (void)ili9341_fill_rect(d, 0, 0, d->width, 28, ILI9341_NAVY);
    (void)ili9341_draw_string(d, 6, 6, "IchiPing Training Tool",
                              UI_FG, ILI9341_NAVY, 2);
    (void)ili9341_draw_string(d, (uint16_t)(d->width - 70u), 10,
                              "v0.1 demo", UI_ACCENT, ILI9341_NAVY, 1);
}

static void ui_draw_tile(ili9341_t *d,
                         uint16_t x, uint16_t y, uint16_t w, uint16_t h,
                         const char *name, room_state_t st, float deg) {
    uint16_t color = state_color(st);
    (void)ili9341_fill_rect(d, x, y, w, h, color);
    (void)ili9341_fill_rect(d, x, y, w, 2, ILI9341_BLACK);
    (void)ili9341_fill_rect(d, x, (uint16_t)(y + h - 2u), w, 2, ILI9341_BLACK);
    (void)ili9341_draw_string(d, (uint16_t)(x + 4), (uint16_t)(y + 4),
                              name, ILI9341_BLACK, color, 1);
    (void)ili9341_draw_string(d, (uint16_t)(x + 4), (uint16_t)(y + 18),
                              state_label(st), ILI9341_BLACK, color, 2);
    char buf[8];
    snprintf(buf, sizeof(buf), "%3d deg", (int)deg);
    (void)ili9341_draw_string(d, (uint16_t)(x + 4),
                              (uint16_t)(y + h - 14u),
                              buf, ILI9341_BLACK, color, 1);
}

static void ui_draw_tiles(ili9341_t *d, const room_state_t st[ROOM_COUNT]) {
    const uint16_t margin = 6u;
    const uint16_t row_y  = 38u;
    const uint16_t row_h  = 86u;
    const uint16_t tile_w =
        (uint16_t)((d->width - margin * (ROOM_COUNT + 1)) / ROOM_COUNT);
    for (uint16_t i = 0; i < ROOM_COUNT; i++) {
        uint16_t x = (uint16_t)(margin + i * (tile_w + margin));
        ui_draw_tile(d, x, row_y, tile_w, row_h,
                     ROOM_NAMES[i], st[i], state_to_deg(st[i]));
    }
}

static void ui_draw_footer(ili9341_t *d,
                           const char *status_text,
                           uint16_t status_color,
                           uint32_t frames_sent,
                           uint32_t uptime_ms) {
    const uint16_t y = (uint16_t)(d->height - 60u);
    (void)ili9341_fill_rect(d, 0, y, d->width, 60, UI_BG);
    (void)ili9341_fill_rect(d, 0, y, d->width, 1, UI_DIM);

    (void)ili9341_draw_string(d, 6, (uint16_t)(y + 6),
                              "Status:", UI_DIM, UI_BG, 1);
    (void)ili9341_draw_string(d, 56, (uint16_t)(y + 4),
                              status_text, status_color, UI_BG, 2);

    char buf[40];
    snprintf(buf, sizeof(buf), "Frames sent: %lu",
             (unsigned long)frames_sent);
    (void)ili9341_draw_string(d, 6, (uint16_t)(y + 24),
                              buf, UI_FG, UI_BG, 1);

    snprintf(buf, sizeof(buf), "Uptime: %lu.%03lus",
             (unsigned long)(uptime_ms / 1000u),
             (unsigned long)(uptime_ms % 1000u));
    (void)ili9341_draw_string(d, (uint16_t)(d->width - 130u),
                              (uint16_t)(y + 24),
                              buf, UI_FG, UI_BG, 1);

    (void)ili9341_draw_string(d, 6, (uint16_t)(y + 40),
                              "UART:921600  I2C:100k  SPI:20M",
                              UI_DIM, UI_BG, 1);
}

/* ----- Buffers (file scope to keep main() stack small) ----- */

static int16_t s_audio_buf[DEMO_SAMPLE_COUNT];
static uint8_t s_tx_buf[ICHP_HEADER_SIZE
                        + DEMO_SAMPLE_COUNT * sizeof(int16_t)
                        + ICHP_CRC_SIZE];

/* ----- Application ----- */

int main(void) {
    BOARD_InitHardware();
    systick_init_1ms();

    ili_gpio_outputs_init();
    servo_i2c_init();

    PRINTF("\r\nIchiPing 00_demo  (debug pre-baud-switch)\r\n");
    PRINTF("  TFT  : LPSPI1 @ %u Hz, %ux%u\r\n",
           (unsigned)ILI_SPI_BAUD_HZ, 320u, 240u);
    PRINTF("  Servo: %s on LPI2C2 @ %u Hz (addr 0x%02X)\r\n",
           SERVO_BACKEND_NAME, (unsigned)SERVO_I2C_BAUD,
           (unsigned)SERVO_DEFAULT_ADDR);
    PRINTF("  Frame: LPUART4 -> %u baud after init\r\n",
           (unsigned)BOARD_FRAME_UART_BAUD);

    /* TFT bring-up */
    ili9341_t lcd = {
        .spi          = BOARD_ILI_SPI_BASE,
        .spi_clk_hz   = BOARD_ILI_SPI_CLK_FREQ,
        .spi_baud_hz  = ILI_SPI_BAUD_HZ,
        .cs_gpio      = BOARD_ILI_CS_GPIO,  .cs_pin  = BOARD_ILI_CS_PIN,
        .res_gpio     = BOARD_ILI_RES_GPIO, .res_pin = BOARD_ILI_RES_PIN,
        .dc_gpio      = BOARD_ILI_DC_GPIO,  .dc_pin  = BOARD_ILI_DC_PIN,
        .bl_gpio      = BOARD_ILI_BL_GPIO,  .bl_pin  = BOARD_ILI_BL_PIN,
        .rotation     = ILI9341_ROT_LANDSCAPE,
    };
    if (ili9341_init(&lcd) != kStatus_Success) {
        PRINTF("FAIL: ili9341_init\r\n");
        for (;;) { __WFI(); }
    }
    PRINTF("OK: ILI9341 initialised (%ux%u)\r\n",
           (unsigned)lcd.width, (unsigned)lcd.height);

    ui_clear(&lcd);
    ui_draw_title(&lcd);

    /* Servo bring-up */
    servo_driver_t servo;
    bool servo_ok = false;
    if (servo_init(&servo, BOARD_SERVO_I2C_BASEADDR,
                   SERVO_DEFAULT_ADDR, SERVO_DEFAULT_FREQ_HZ)
        == kStatus_Success) {
        servo_ok = true;
        PRINTF("OK: %s initialised (addr 0x%02X)\r\n",
               SERVO_BACKEND_NAME, (unsigned)SERVO_DEFAULT_ADDR);
        float park[ROOM_COUNT] = {0.0f, 0.0f, 0.0f, 0.0f, 0.0f};
        (void)servo_set_first_n_deg(&servo, park, ROOM_COUNT);
    } else {
        PRINTF("WARN: servo not detected on the bus; UI / frames continue\r\n");
    }

    /* Frame stream + audio seed */
    dummy_audio_seed(0xC4C4C4C4u);
    frame_uart_init();          /* PRINTF now runs at 921600 */

    /* ----- Main loop -----
     *
     *   tick every DEMO_UI_REFRESH_MS to update the tile colours / labels
     *   tick every DEMO_FRAME_PERIOD_MS to push servos + emit one frame
     */
    uint16_t   seq             = 0;
    uint32_t   step            = 0;
    uint32_t   frames_sent     = 0;
    uint32_t   next_frame_ms   = 0;
    uint32_t   next_ui_ms      = 0;
    room_state_t room_st[ROOM_COUNT] = {0};
    const char *status_text    = "BOOT";
    uint16_t   status_color    = UI_ACCENT;

    for (;;) {
        uint32_t t = now_ms();

        /* Demo state advance + servo + frame, every DEMO_FRAME_PERIOD_MS */
        if ((int32_t)(t - next_frame_ms) >= 0) {
            for (int i = 0; i < ROOM_COUNT; i++) {
                room_st[i] = (room_state_t)((step + (uint32_t)i) % STATE_COUNT);
            }

            float servo_deg[ROOM_COUNT];
            for (int i = 0; i < ROOM_COUNT; i++) {
                servo_deg[i] = state_to_deg(room_st[i]);
            }

            status_text  = "MOVING";
            status_color = ILI9341_YELLOW;
            ui_draw_footer(&lcd, status_text, status_color,
                           frames_sent, t);

            if (servo_ok) {
                status_t s = servo_set_first_n_deg(&servo, servo_deg,
                                                   ROOM_COUNT);
                if (s != kStatus_Success) {
                    PRINTF("WARN: servo I2C write status=%d\r\n", (int)s);
                }
            }

            status_text  = "EMIT";
            status_color = UI_ACCENT;
            ui_draw_footer(&lcd, status_text, status_color,
                           frames_sent, t);

            dummy_audio_generate(s_audio_buf, DEMO_SAMPLE_COUNT,
                                 DEMO_SAMPLE_RATE);
            size_t n = ichp_pack_frame(
                s_tx_buf, sizeof(s_tx_buf),
                seq, t,
                DEMO_SAMPLE_RATE, DEMO_SAMPLE_COUNT,
                servo_deg, s_audio_buf);
            if (n > 0) {
                LPUART_WriteBlocking(BOARD_FRAME_UART_BASE,
                                     s_tx_buf, n);
                frames_sent++;
            }

            status_text  = "READY";
            status_color = ILI9341_GREEN;
            seq++;
            step++;
            next_frame_ms = t + DEMO_FRAME_PERIOD_MS;
        }

        /* UI refresh -- always redraw tiles in case anything else mutates
         * the screen later (e.g. button events, FFT overlay). */
        if ((int32_t)(t - next_ui_ms) >= 0) {
            ui_draw_tiles(&lcd, room_st);
            ui_draw_footer(&lcd, status_text, status_color,
                           frames_sent, t);
            next_ui_ms = t + DEMO_UI_REFRESH_MS;
        }

        /* WFI brings us back on the next SysTick (1 ms). */
        __WFI();
    }
}
