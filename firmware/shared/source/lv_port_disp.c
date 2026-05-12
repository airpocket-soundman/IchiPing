/*
 * LVGL display-driver glue for ILI9341.
 *
 * Implements LVGL's "draw_buf + flush_cb" contract using a small line
 * buffer (40 lines × screen_width × 2 B = ~25 KB at 320 px wide). The
 * buffer is statically allocated in SRAM — way under the 320 KB on
 * MCXN947.
 *
 * Tested against LVGL v9.x; the entry-point names match the v9 API
 * (lv_display_create / lv_display_set_flush_cb). For v8 swap to
 * lv_disp_drv_init / lv_disp_drv_register — see comments inline.
 */

#include "lv_port.h"

#if __has_include("lvgl.h")
#  include "lvgl.h"
#else
#  include "lvgl/lvgl.h"
#endif

/* Flush region width × this height = SPI burst size. 40 rows keeps the
 * RAM under 32 KB even at 320 px wide and gives LVGL enough room to keep
 * the screen busy. */
#define LV_PORT_BUF_LINES   40

static ili9341_t        *s_lcd;
static lv_color_t        s_buf1[LV_HOR_RES_MAX * LV_PORT_BUF_LINES];
static lv_color_t        s_buf2[LV_HOR_RES_MAX * LV_PORT_BUF_LINES];

static void lv_port_flush_cb(lv_display_t *disp,
                             const lv_area_t *area,
                             uint8_t *px_map) {
    /* LVGL v9: px_map carries little-endian RGB565 bytes when
     * LV_COLOR_16_SWAP == 0. Our ili9341 driver expects native uint16_t
     * pixels and handles the byte-swap to big-endian on the wire. */
    uint16_t x0 = (uint16_t)area->x1;
    uint16_t y0 = (uint16_t)area->y1;
    uint16_t x1 = (uint16_t)area->x2;
    uint16_t y1 = (uint16_t)area->y2;

    (void)ili9341_set_window(s_lcd, x0, y0, x1, y1);

    size_t n_pixels = (size_t)(x1 - x0 + 1) * (size_t)(y1 - y0 + 1);
    (void)ili9341_blit(s_lcd, (const uint16_t *)px_map, n_pixels);

    lv_display_flush_ready(disp);
}

void lv_port_disp_init(ili9341_t *lcd) {
    s_lcd = lcd;
    lv_init();

    /* --- LVGL v9 path --- */
    lv_display_t *disp = lv_display_create(lcd->width, lcd->height);
    lv_display_set_flush_cb(disp, lv_port_flush_cb);
    lv_display_set_buffers(disp, s_buf1, s_buf2,
                           sizeof(s_buf1),
                           LV_DISPLAY_RENDER_MODE_PARTIAL);

    /*
     * --- LVGL v8 equivalent, if you're on the older API ---
     *
     * static lv_disp_draw_buf_t draw_buf;
     * lv_disp_draw_buf_init(&draw_buf, s_buf1, s_buf2,
     *                       lcd->width * LV_PORT_BUF_LINES);
     *
     * static lv_disp_drv_t disp_drv;
     * lv_disp_drv_init(&disp_drv);
     * disp_drv.hor_res  = lcd->width;
     * disp_drv.ver_res  = lcd->height;
     * disp_drv.flush_cb = lv_port_flush_cb_v8;   // signature differs
     * disp_drv.draw_buf = &draw_buf;
     * lv_disp_drv_register(&disp_drv);
     */
}

/* Tick handler — must be called every millisecond. The host project
 * usually wires this from SysTick_Handler():
 *
 *   void SysTick_Handler(void) { lv_tick_inc(1); }
 *
 * To keep this glue self-contained we expose the same call as a function
 * so the application can choose where to invoke it. */
void lv_port_tick_1ms(void) { lv_tick_inc(1); }

void lv_port_handle_loop(uint32_t loop_period_ms) {
    uint32_t until = lv_tick_get() + loop_period_ms;
    while ((int32_t)(lv_tick_get() - until) < 0) {
        lv_timer_handler();
    }
}
