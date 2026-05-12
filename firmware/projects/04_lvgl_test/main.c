/*
 * IchiPing — LVGL display test firmware (MCUXpresso skeleton).
 *
 * Brings up LVGL on top of the ILI9341 + FRDM-MCXN947. Renders an
 * IchiPing-themed sample UI:
 *
 *   - Top bar with title "IchiPing v0.1" and a current-phase label
 *   - 5 colour tiles for window a/b/c + door AB/BC, updating their
 *     state every ~1.5 s from a fake oscillator
 *   - Confidence bar that fills/drains over the cycle
 *   - Performance monitor (lower-right) showing FPS + CPU%
 *
 * The point of test 2 is not the UI itself — it is to prove the LVGL
 * port (flush_cb + tick + dual-buffer partial render) holds together
 * before we plug in the real inference + servo signals in v0.5.
 *
 * LVGL itself is NOT bundled with this repo. Pull v9.x from one of:
 *   - MCUXpresso SDK middleware/lvgl  (preferred)
 *   - https://github.com/lvgl/lvgl    (clone to firmware/third_party/lvgl)
 * and add the LVGL sources + your custom lv_conf.h to the same MCUXpresso
 * project as this file.
 *
 * Required project additions:
 *   - firmware/source/ili9341.c            (driver)
 *   - firmware/source/lv_port_disp.c       (LVGL glue)
 *   - firmware/source/main_lvgl_test.c     (this file)
 *   - LVGL source tree
 *   - your lv_conf.h with LV_COLOR_DEPTH=16, partial-render-friendly mem
 *
 * Wiring is identical to test 1 (see main_ili9341_test.c / wiring.html).
 */

#include "pin_mux.h"
#include "clock_config.h"
#include "board.h"
#include "fsl_debug_console.h"
#include "fsl_lpspi.h"
#include "fsl_gpio.h"

#if __has_include("lvgl.h")
#  include "lvgl.h"
#else
#  include "lvgl/lvgl.h"
#endif

#include "ili9341.h"
#include "lv_port.h"

/* ----- Pin / SPI config (must match main_ili9341_test.c) ----- */
#ifndef ILI_SPI_BASE
#define ILI_SPI_BASE        LPSPI3
#endif
#ifndef ILI_SPI_CLK_FREQ
#define ILI_SPI_CLK_FREQ    CLOCK_GetLPFlexCommClkFreq(3)
#endif
#define ILI_SPI_BAUD_HZ     20000000U

#ifndef ILI_CS_GPIO
#define ILI_CS_GPIO  GPIO0
#define ILI_CS_PIN   24U
#define ILI_RES_GPIO GPIO0
#define ILI_RES_PIN  25U
#define ILI_DC_GPIO  GPIO0
#define ILI_DC_PIN   26U
#define ILI_BL_GPIO  GPIO0
#define ILI_BL_PIN   27U
#endif

/* ----- SysTick: feed LVGL ----- */

void SysTick_Handler(void) {
    /* Provided by lv_port_disp.c. Safe to call from an ISR per LVGL API. */
    extern void lv_port_tick_1ms(void);
    lv_port_tick_1ms();
}

static void systick_init_1ms(void) { (void)SysTick_Config(SystemCoreClock / 1000u); }

/* ----- GPIO ----- */
static void gpio_outputs_init(void) {
    gpio_pin_config_t out = { kGPIO_DigitalOutput, 1 };
    GPIO_PinInit(ILI_CS_GPIO,  ILI_CS_PIN,  &out);
    GPIO_PinInit(ILI_RES_GPIO, ILI_RES_PIN, &out);
    GPIO_PinInit(ILI_DC_GPIO,  ILI_DC_PIN,  &out);
    GPIO_PinInit(ILI_BL_GPIO,  ILI_BL_PIN,  &out);
}

/* ----- UI: build once, mutate over time ----- */

typedef enum { ST_CLOSED, ST_HALF, ST_OPEN } room_state_t;

static lv_obj_t *s_title;
static lv_obj_t *s_phase;
static lv_obj_t *s_tiles[5];
static lv_obj_t *s_tile_label[5];
static lv_obj_t *s_bar;

static const char *const ROOM_NAMES[5] = {
    "WIN a", "WIN b", "WIN c", "DOOR AB", "DOOR BC",
};

static lv_color_t state_color(room_state_t s) {
    switch (s) {
        case ST_CLOSED: return lv_color_hex(0x2EBF55);   /* green */
        case ST_HALF:   return lv_color_hex(0xFFB454);   /* orange */
        case ST_OPEN:   return lv_color_hex(0xFF5555);   /* red */
    }
    return lv_color_hex(0x8B95A7);
}
static const char *state_text(room_state_t s) {
    switch (s) {
        case ST_CLOSED: return "CLOSED";
        case ST_HALF:   return "HALF";
        case ST_OPEN:   return "OPEN";
    }
    return "?";
}

static void build_ui(void) {
    lv_obj_t *scr = lv_screen_active();
    lv_obj_set_style_bg_color(scr, lv_color_hex(0x11141A), 0);

    /* Title */
    s_title = lv_label_create(scr);
    lv_label_set_text(s_title, "IchiPing v0.1");
    lv_obj_set_style_text_color(s_title, lv_color_hex(0x7BE0A4), 0);
    lv_obj_set_style_text_font(s_title, &lv_font_montserrat_24, 0);
    lv_obj_align(s_title, LV_ALIGN_TOP_LEFT, 8, 4);

    /* Phase indicator (top-right) */
    s_phase = lv_label_create(scr);
    lv_label_set_text(s_phase, "DEMO");
    lv_obj_set_style_text_color(s_phase, lv_color_hex(0x6EA8FE), 0);
    lv_obj_align(s_phase, LV_ALIGN_TOP_RIGHT, -8, 8);

    /* 5 room tiles */
    int32_t tile_w = (lv_display_get_horizontal_resolution(NULL) - 6 * 8) / 5;
    int32_t tile_y = 60;
    int32_t tile_h = 70;
    for (int i = 0; i < 5; i++) {
        s_tiles[i] = lv_obj_create(scr);
        lv_obj_set_size(s_tiles[i], tile_w, tile_h);
        lv_obj_set_pos(s_tiles[i], 8 + i * (tile_w + 8), tile_y);
        lv_obj_set_style_radius(s_tiles[i], 6, 0);
        lv_obj_set_style_border_width(s_tiles[i], 0, 0);
        lv_obj_set_style_bg_color(s_tiles[i], state_color(ST_CLOSED), 0);

        lv_obj_t *name = lv_label_create(s_tiles[i]);
        lv_label_set_text(name, ROOM_NAMES[i]);
        lv_obj_set_style_text_color(name, lv_color_hex(0x11141A), 0);
        lv_obj_align(name, LV_ALIGN_TOP_LEFT, 4, 4);

        s_tile_label[i] = lv_label_create(s_tiles[i]);
        lv_label_set_text(s_tile_label[i], state_text(ST_CLOSED));
        lv_obj_set_style_text_color(s_tile_label[i], lv_color_hex(0x11141A), 0);
        lv_obj_align(s_tile_label[i], LV_ALIGN_BOTTOM_LEFT, 4, -4);
    }

    /* Confidence bar */
    s_bar = lv_bar_create(scr);
    lv_obj_set_size(s_bar,
                    lv_display_get_horizontal_resolution(NULL) - 16, 18);
    lv_obj_align(s_bar, LV_ALIGN_BOTTOM_LEFT, 8, -10);
    lv_bar_set_range(s_bar, 0, 100);
    lv_bar_set_value(s_bar, 30, LV_ANIM_OFF);
    lv_obj_set_style_bg_color(s_bar, lv_color_hex(0x1F242D), 0);
    lv_obj_set_style_bg_color(s_bar, lv_color_hex(0x6EA8FE),
                              LV_PART_INDICATOR);

    lv_obj_t *bar_label = lv_label_create(scr);
    lv_label_set_text(bar_label, "any_open confidence");
    lv_obj_set_style_text_color(bar_label, lv_color_hex(0x8B95A7), 0);
    lv_obj_align_to(bar_label, s_bar, LV_ALIGN_OUT_TOP_LEFT, 0, -2);
}

/* ----- Demo animation: cycle through states + confidence ----- */
static void demo_step(uint32_t step) {
    /* Cycle each tile through closed → half → open every 3 steps,
     * offset by tile index so the screen never looks static. */
    for (int i = 0; i < 5; i++) {
        room_state_t st = (room_state_t)((step + i) % 3);
        lv_obj_set_style_bg_color(s_tiles[i], state_color(st), 0);
        lv_label_set_text(s_tile_label[i], state_text(st));
    }
    /* Confidence bar: sinusoidal-ish 30..95 sweep */
    int32_t v = 30 + (int32_t)((step * 13) % 65);
    lv_bar_set_value(s_bar, v, LV_ANIM_ON);

    char buf[24];
    snprintf(buf, sizeof(buf), "step %lu", (unsigned long)step);
    lv_label_set_text(s_phase, buf);
}

/* ----- Application ----- */

int main(void) {
    BOARD_InitBootPins();
    BOARD_InitBootClocks();
    BOARD_InitDebugConsole();
    gpio_outputs_init();

    PRINTF("\r\nIchiPing LVGL test  ─  ILI9341 + LVGL v9 partial render\r\n");

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
    if (ili9341_init(&lcd) != kStatus_Success) {
        PRINTF("FAIL: ili9341_init\r\n");
        for (;;) { __WFI(); }
    }

    lv_port_disp_init(&lcd);
    systick_init_1ms();   /* now start ticks so LVGL sees them */

    build_ui();
    PRINTF("OK: UI built\r\n");

    uint32_t next_step_ms = 0;
    uint32_t step = 0;
    for (;;) {
        lv_timer_handler();
        if (lv_tick_get() >= next_step_ms) {
            demo_step(step++);
            next_step_ms = lv_tick_get() + 1500u;   /* 1.5 s/step */
        }
        /* Tiny sleep keeps the WFI-friendly idle path active. */
        for (volatile int i = 0; i < 5000; i++) { __asm volatile ("nop"); }
    }
}
