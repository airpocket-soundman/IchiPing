/*
 * LVGL platform glue for IchiPing.
 *
 * Owns the bridge between the LVGL graphics library and our ILI9341
 * driver. This header is the public face — the implementation lives in
 * lv_port_disp.c.
 *
 * The LVGL library itself is NOT included in this repo. Pull it from
 * MCUXpresso SDK middleware/lvgl (v9.x recommended) and add it to your
 * MCUXpresso project alongside this glue. Alternatively clone LVGL v9
 * from https://github.com/lvgl/lvgl/ into firmware/third_party/lvgl/.
 *
 * Required lv_conf.h settings (or use the lv_conf_template.h shipped with
 * LVGL and override these):
 *
 *     #define LV_COLOR_DEPTH         16
 *     #define LV_COLOR_16_SWAP       0    // ILI9341 byte order handled in driver
 *     #define LV_USE_PERF_MONITOR    1    // optional but nice for the demo
 *     #define LV_TICK_CUSTOM         0    // we call lv_tick_inc from SysTick
 *     #define LV_MEM_SIZE            (48 * 1024)
 */

#ifndef LV_PORT_H_
#define LV_PORT_H_

#include <stdint.h>
#include "ili9341.h"

/* Wire the LVGL display layer to the given (already initialised) ILI9341
 * instance. Call once after ili9341_init() and before any lv_* API. */
void lv_port_disp_init(ili9341_t *lcd);

/* Convenience: combine timer_handler dispatch + delay_ms in one call to
 * keep the application main loop short. */
void lv_port_handle_loop(uint32_t loop_period_ms);

#endif /* LV_PORT_H_ */
