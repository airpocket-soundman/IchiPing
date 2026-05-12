/*
 * IchiPing — ILI9341 single-display test firmware (MCUXpresso skeleton).
 *
 * Goal: prove the ILI9341 board + the SPI wiring + the rotation register
 * all behave before bringing up LVGL. The test cycles through 5 phases:
 *
 *   1. Fill the whole screen with each of black/red/green/blue/white —
 *      visually confirms RGB565 byte order and that MADCTL is consistent
 *      with the rotation we asked for.
 *   2. Concentric rectangles in cyan / magenta / yellow — confirms
 *      windowing arithmetic (CASET / PASET) at non-zero offsets.
 *   3. Centred title "IchiPing" + version line + tiny "BL test" pulse —
 *      confirms the 5x7 font path, multi-size scaling, and backlight.
 *   4. Five "room state" tiles in green/orange/red — mock of the v0.1
 *      OLED layout (window a / b / c, door AB / BC). Confirms multi-tile
 *      drawing at typical UI scale.
 *   5. Pixel sweep — paints rows of distinct colours top → bottom to
 *      catch any partial-blit / window-rollover bugs.
 *
 * Each phase prints to the OpenSDA UART @ 115200 8N1 so you can follow
 * along on a serial terminal.
 *
 * Wiring on FRDM-MCXN947 (Arduino headers, see hardware/wiring.md §2.3):
 *
 *   ILI9341         FRDM-MCXN947
 *   ---------       ------------
 *   VCC             3V3
 *   GND             GND
 *   CS              A2  (GPIO out)
 *   RESET           A3  (GPIO out)
 *   DC              A4  (GPIO out)
 *   SDI / MOSI      D11 (FC3_SPI_MOSI)
 *   SCK             D13 (FC3_SPI_SCK)
 *   LED / BL        A5  (GPIO out, optional PWM)
 *   SDO / MISO      n/c
 *   T_*  (touch)    n/c
 *
 * Build (MCUXpresso IDE 11.9+ or MCUXpresso for VS Code):
 *   1) Import an SDK example based on `driver_examples/lpspi/polling_b2b`
 *      (or any LPSPI master polling sample) for `frdmmcxn947`.
 *   2) Replace main.c with this file, add ../firmware/source/ili9341.c,
 *      include ../firmware/include in C/C++ Build > Settings > Includes.
 *   3) In Config Tools / Pins:
 *        D11 → LPSPI3 SDO, D13 → LPSPI3 SCK
 *        A2..A5 → GPIO output, push-pull, no pull
 *   4) Build → flash via OpenSDA.
 *
 * The actual GPIO ports/pins below need to match what pin_mux.c assigns
 * for A2..A5. Adjust the BOARD_*_PORT / _PIN macros if your routing
 * differs from the example numbers below.
 */

#include "pin_mux.h"
#include "clock_config.h"
#include "board.h"
#include "fsl_debug_console.h"
#include "fsl_lpspi.h"
#include "fsl_gpio.h"

#include "ili9341.h"

/* ----- SPI / GPIO configuration ----- */

#ifndef ILI_SPI_BASE
#define ILI_SPI_BASE        LPSPI3                 /* FC3 on FRDM-MCXN947 */
#endif
#ifndef ILI_SPI_CLK_FREQ
#define ILI_SPI_CLK_FREQ    CLOCK_GetLPFlexCommClkFreq(3)
#endif
#define ILI_SPI_BAUD_HZ     20000000U              /* 20 MHz — comfortable headroom */

/* These four GPIO routings depend on pin_mux.c. Update to match your
 * Config Tools output. Defaults assume the A2..A5 Arduino pins are
 * mapped to GPIO0 P0_24..P0_27 (one of several valid routings on the
 * FRDM-MCXN947); change to suit. */
#ifndef ILI_CS_GPIO
#define ILI_CS_GPIO         GPIO0
#define ILI_CS_PIN          24U
#endif
#ifndef ILI_RES_GPIO
#define ILI_RES_GPIO        GPIO0
#define ILI_RES_PIN         25U
#endif
#ifndef ILI_DC_GPIO
#define ILI_DC_GPIO         GPIO0
#define ILI_DC_PIN          26U
#endif
#ifndef ILI_BL_GPIO
#define ILI_BL_GPIO         GPIO0
#define ILI_BL_PIN          27U
#endif

/* ----- SysTick ----- */
static volatile uint32_t s_uptime_ms = 0;
void SysTick_Handler(void) { s_uptime_ms++; }
static void systick_init_1ms(void) { (void)SysTick_Config(SystemCoreClock / 1000u); }
static void delay_ms(uint32_t ms) {
    uint32_t end = s_uptime_ms + ms;
    while ((int32_t)(s_uptime_ms - end) < 0) { __WFI(); }
}

/* ----- GPIO ----- */
static void gpio_outputs_init(void) {
    gpio_pin_config_t out = { kGPIO_DigitalOutput, 1 };
    GPIO_PinInit(ILI_CS_GPIO,  ILI_CS_PIN,  &out);
    GPIO_PinInit(ILI_RES_GPIO, ILI_RES_PIN, &out);
    GPIO_PinInit(ILI_DC_GPIO,  ILI_DC_PIN,  &out);
    GPIO_PinInit(ILI_BL_GPIO,  ILI_BL_PIN,  &out);
}

/* ----- Phases ----- */

static void phase_solid_fill(ili9341_t *d) {
    static const struct { uint16_t c; const char *name; } stripes[] = {
        {ILI9341_BLACK,  "black"}, {ILI9341_RED,    "red"  },
        {ILI9341_GREEN,  "green"}, {ILI9341_BLUE,   "blue" },
        {ILI9341_WHITE,  "white"},
    };
    for (size_t i = 0; i < sizeof(stripes)/sizeof(stripes[0]); i++) {
        PRINTF("  fill %s\r\n", stripes[i].name);
        (void)ili9341_fill_screen(d, stripes[i].c);
        delay_ms(500);
    }
}

static void phase_nested_rects(ili9341_t *d) {
    (void)ili9341_fill_screen(d, ILI9341_BLACK);
    static const uint16_t cols[] = {
        ILI9341_CYAN, ILI9341_MAGENTA, ILI9341_YELLOW, ILI9341_WHITE,
    };
    uint16_t cx = d->width / 2u, cy = d->height / 2u;
    for (uint8_t i = 0; i < (uint8_t)(sizeof(cols)/sizeof(cols[0])); i++) {
        uint16_t inset = (uint16_t)(20u + i * 20u);
        if (inset * 2u >= d->width || inset * 2u >= d->height) break;
        (void)ili9341_fill_rect(d,
                                (uint16_t)(inset), (uint16_t)(inset),
                                (uint16_t)(d->width  - inset * 2u),
                                (uint16_t)(d->height - inset * 2u),
                                cols[i]);
    }
    (void)cx; (void)cy;
    delay_ms(1500);
}

static void phase_text(ili9341_t *d) {
    (void)ili9341_fill_screen(d, ILI9341_NAVY);
    (void)ili9341_draw_string(d, 20, 30, "IchiPing", ILI9341_WHITE, ILI9341_NAVY, 4);
    (void)ili9341_draw_string(d, 20, 80, "v0.1 - serial pipeline",
                              ILI9341_CYAN, ILI9341_NAVY, 2);
    (void)ili9341_draw_string(d, 20, 110, "ILI9341 display test",
                              ILI9341_YELLOW, ILI9341_NAVY, 2);

    /* Pulse backlight twice as a smoke test on BL wiring. */
    for (int i = 0; i < 2; i++) {
        (void)ili9341_set_backlight(d, 0u);
        delay_ms(200);
        (void)ili9341_set_backlight(d, 100u);
        delay_ms(200);
    }
    delay_ms(1000);
}

static void phase_room_tiles(ili9341_t *d) {
    /* 5 colour tiles arranged horizontally, mock of the v1 status UI:
     *   window a / window b / window c / door AB / door BC
     * Colours: green = closed, orange = half, red = full open. */
    (void)ili9341_fill_screen(d, ILI9341_BLACK);
    static const struct { const char *name; uint16_t color; const char *state; } tiles[] = {
        {"WIN a",  ILI9341_GREEN,  "CLOSED"},
        {"WIN b",  ILI9341_ORANGE, "HALF"  },
        {"WIN c",  ILI9341_RED,    "OPEN"  },
        {"DOOR AB",ILI9341_GREEN,  "CLOSED"},
        {"DOOR BC",ILI9341_ORANGE, "HALF"  },
    };
    const uint16_t margin = 10u;
    const uint16_t tile_w = (uint16_t)((d->width - margin * 6u) / 5u);
    const uint16_t tile_h = 80u;
    const uint16_t tile_y = (uint16_t)((d->height - tile_h) / 2u);

    for (uint16_t i = 0; i < 5u; i++) {
        uint16_t tx = (uint16_t)(margin + i * (tile_w + margin));
        (void)ili9341_fill_rect(d, tx, tile_y, tile_w, tile_h, tiles[i].color);
        (void)ili9341_draw_string(d, (uint16_t)(tx + 4u),
                                  (uint16_t)(tile_y + 6u),
                                  tiles[i].name, ILI9341_BLACK, tiles[i].color, 1);
        (void)ili9341_draw_string(d, (uint16_t)(tx + 4u),
                                  (uint16_t)(tile_y + 18u),
                                  tiles[i].state, ILI9341_BLACK, tiles[i].color, 1);
    }
    (void)ili9341_draw_string(d, margin, 10u,
                              "IchiPing room state (mock)",
                              ILI9341_WHITE, ILI9341_BLACK, 2);
    delay_ms(2500);
}

static void phase_pixel_sweep(ili9341_t *d) {
    (void)ili9341_fill_screen(d, ILI9341_BLACK);
    for (uint16_t y = 0; y < d->height; y += 4u) {
        uint16_t hue = (uint16_t)((y * 0xFFFF) / d->height);
        (void)ili9341_fill_rect(d, 0, y, d->width, 4u, hue);
    }
    delay_ms(2000);
}

/* ----- Application ----- */

int main(void) {
    BOARD_InitBootPins();
    BOARD_InitBootClocks();
    BOARD_InitDebugConsole();
    systick_init_1ms();
    gpio_outputs_init();

    PRINTF("\r\nIchiPing ILI9341 test  ─  240x320 RGB565 @ %u Hz SPI\r\n",
           (unsigned)ILI_SPI_BAUD_HZ);

    ili9341_t lcd = {
        .spi          = ILI_SPI_BASE,
        .spi_clk_hz   = ILI_SPI_CLK_FREQ,
        .spi_baud_hz  = ILI_SPI_BAUD_HZ,
        .cs_gpio      = ILI_CS_GPIO,  .cs_pin  = ILI_CS_PIN,
        .res_gpio     = ILI_RES_GPIO, .res_pin = ILI_RES_PIN,
        .dc_gpio      = ILI_DC_GPIO,  .dc_pin  = ILI_DC_PIN,
        .bl_gpio      = ILI_BL_GPIO,  .bl_pin  = ILI_BL_PIN,
        .rotation     = ILI9341_ROT_LANDSCAPE,
    };
    status_t s = ili9341_init(&lcd);
    if (s != kStatus_Success) {
        PRINTF("FAIL: ili9341_init returned status=%d\r\n", (int)s);
        for (;;) { __WFI(); }
    }
    PRINTF("OK: ILI9341 initialised (%ux%u)\r\n",
           (unsigned)lcd.width, (unsigned)lcd.height);

    uint32_t cycle = 0;
    for (;;) {
        cycle++;
        PRINTF("\r\n[cycle %u] phase 1: solid fills\r\n", (unsigned)cycle); phase_solid_fill(&lcd);
        PRINTF("[cycle %u] phase 2: nested rects\r\n",   (unsigned)cycle); phase_nested_rects(&lcd);
        PRINTF("[cycle %u] phase 3: text + BL pulse\r\n",(unsigned)cycle); phase_text(&lcd);
        PRINTF("[cycle %u] phase 4: room tiles\r\n",     (unsigned)cycle); phase_room_tiles(&lcd);
        PRINTF("[cycle %u] phase 5: pixel sweep\r\n",    (unsigned)cycle); phase_pixel_sweep(&lcd);
    }
}
