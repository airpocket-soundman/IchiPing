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
 *   ILI9341         FRDM-MCXN947     MCU pin        Note
 *   ---------       ---------------  -------------  -------------
 *   VCC             3V3              -              -
 *   GND             GND              -              -
 *   CS              A2 (J4.6)        P0_14, GPIO    -
 *   RESET           A3 (J4.8)        P0_22, GPIO    -
 *   DC              A4 (J4.10)       P0_15, GPIO    SJ8 default 1-2
 *   SDI / MOSI      D11 (J2.8)       P0_24, LPSPI1  Alt2, SJ7 default 1-2
 *   SCK             D13 (J2.12)      P0_25, LPSPI1  Alt2
 *   LED / BL        A5 (J4.12)       P0_23, GPIO    SJ9 default 1-2
 *   SDO / MISO      D12 (J2.10) n/c  P0_26, LPSPI1  Alt2, ILI write-only
 *   T_*  (touch)    n/c              -              -
 *
 * Pin map verified against the FRDM-MCXN947 Board User Manual Tables 18
 * and 20 (docs/pdf/FRDM-MCXN947BoardUserManual.pdf) and the SDK example
 * driver_examples/lpspi/polling_b2b_transfer (master/pin_mux.c). Earlier
 * comments here said "FC3" — that was wrong; D10..D13 actually go to FC1.
 *
 * Build:
 *   1) Import projects/03_ili9341_test/ via MCUXpresso for VS Code.
 *   2) configure preset = debug → build → run.
 * No Pins tool tweaks required; pin_mux.c here matches the BUM tables.
 */

#include "pin_mux.h"
#include "clock_config.h"
#include "board.h"
#include "fsl_debug_console.h"
#include "fsl_lpspi.h"
#include "fsl_gpio.h"
#include "app.h"

#include "ili9341.h"

/* ----- SPI / GPIO configuration -----
 * Defaults come from app.h (BOARD_ILI_SPI_BASE = LPSPI1, etc.). Override
 * with -D in CMakeLists if you need to retarget. */

#ifndef ILI_SPI_BASE
#define ILI_SPI_BASE        BOARD_ILI_SPI_BASE
#endif
#ifndef ILI_SPI_CLK_FREQ
#define ILI_SPI_CLK_FREQ    BOARD_ILI_SPI_CLK_FREQ
#endif
#define ILI_SPI_BAUD_HZ     20000000U              /* 20 MHz — comfortable headroom */

/* GPIO routings come from app.h, which is verified against the
 * FRDM-MCXN947 Board User Manual Table 20. */
#ifndef ILI_CS_GPIO
#define ILI_CS_GPIO         BOARD_ILI_CS_GPIO
#define ILI_CS_PIN          BOARD_ILI_CS_PIN
#endif
#ifndef ILI_RES_GPIO
#define ILI_RES_GPIO        BOARD_ILI_RES_GPIO
#define ILI_RES_PIN         BOARD_ILI_RES_PIN
#endif
#ifndef ILI_DC_GPIO
#define ILI_DC_GPIO         BOARD_ILI_DC_GPIO
#define ILI_DC_PIN          BOARD_ILI_DC_PIN
#endif
#ifndef ILI_BL_GPIO
#define ILI_BL_GPIO         BOARD_ILI_BL_GPIO
#define ILI_BL_PIN          BOARD_ILI_BL_PIN
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
