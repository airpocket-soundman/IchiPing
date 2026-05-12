/*
 * ILI9341 240x320 16-bit RGB565 TFT LCD driver (write-only, SPI).
 *
 * Targets the FRDM-MCXN947 FC3 LPSPI bus shared with microSD. The display
 * is selected by a separate CS pin so the two devices can coexist on the
 * same MOSI/SCK lines.
 *
 * Scope of this driver:
 *   - Hard reset + init sequence (RGB565, MADCTL rotation, BL ON)
 *   - Window + pixel transfer for line / region blits (used by LVGL)
 *   - Fill / fill_rect / draw_pixel
 *   - Built-in 5x7 ASCII font for status text (no external font ROM)
 *
 * Intentionally NOT supported here:
 *   - Read-back over MISO (touchscreen / register dump). Add later if
 *     you wire SDO and need 0xD3 ILI9341 ID readout.
 *   - DMA transfer. The polling LPSPI path is fast enough for line-by-
 *     line LVGL flush at typical UI rates. Wire eDMA when needed.
 */

#ifndef ILI9341_H_
#define ILI9341_H_

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

#include "fsl_common.h"
#include "fsl_lpspi.h"
#include "fsl_gpio.h"

/* ---- 16-bit RGB565 helpers ---- */
static inline uint16_t ili9341_rgb(uint8_t r, uint8_t g, uint8_t b) {
    return (uint16_t)(((r & 0xF8u) << 8) | ((g & 0xFCu) << 3) | (b >> 3));
}
#define ILI9341_BLACK   0x0000u
#define ILI9341_WHITE   0xFFFFu
#define ILI9341_RED     0xF800u
#define ILI9341_GREEN   0x07E0u
#define ILI9341_BLUE    0x001Fu
#define ILI9341_YELLOW  0xFFE0u
#define ILI9341_ORANGE  0xFD20u
#define ILI9341_CYAN    0x07FFu
#define ILI9341_MAGENTA 0xF81Fu
#define ILI9341_GREY    0x8410u
#define ILI9341_NAVY    0x000Fu

/* ---- Display configuration ---- */
typedef enum {
    ILI9341_ROT_PORTRAIT       = 0,
    ILI9341_ROT_LANDSCAPE      = 1,
    ILI9341_ROT_PORTRAIT_FLIP  = 2,
    ILI9341_ROT_LANDSCAPE_FLIP = 3,
} ili9341_rot_t;

typedef struct {
    LPSPI_Type *spi;            /* e.g. LPSPI3 (FC3 on FRDM-MCXN947) */
    uint32_t    spi_clk_hz;     /* source clock for prescale calc */
    uint32_t    spi_baud_hz;    /* target SPI baud, e.g. 20 000 000 */

    GPIO_Type  *cs_gpio;        /* GPIO bank for CS */
    uint32_t    cs_pin;
    GPIO_Type  *dc_gpio;        /* GPIO bank for DC */
    uint32_t    dc_pin;
    GPIO_Type  *res_gpio;       /* GPIO bank for RESET */
    uint32_t    res_pin;
    GPIO_Type  *bl_gpio;        /* GPIO bank for backlight (NULL → tie to 3V3) */
    uint32_t    bl_pin;

    ili9341_rot_t rotation;     /* set before init */

    /* Updated by ili9341_init() to reflect the chosen rotation. */
    uint16_t    width;
    uint16_t    height;
} ili9341_t;

/* ---- API ---- */

/* Bring the panel out of reset, push the standard init sequence, turn the
 * backlight on. Returns kStatus_Success on completion. */
status_t ili9341_init(ili9341_t *d);

/* Define the active drawing rectangle for the next pixel writes.
 * Coordinates are inclusive on both ends. */
status_t ili9341_set_window(ili9341_t *d,
                            uint16_t x0, uint16_t y0,
                            uint16_t x1, uint16_t y1);

/* Stream pre-rendered RGB565 pixels into the previously-set window.
 * Pixels are big-endian on the wire (high byte first), but the driver
 * handles that — pass native uint16_t. */
status_t ili9341_blit(ili9341_t *d, const uint16_t *pixels, size_t n_pixels);

/* Solid-colour fill of a rectangle (window + repeated colour). */
status_t ili9341_fill_rect(ili9341_t *d,
                           uint16_t x, uint16_t y, uint16_t w, uint16_t h,
                           uint16_t color);

/* Convenience: whole screen. */
status_t ili9341_fill_screen(ili9341_t *d, uint16_t color);

/* Single-pixel draw (avoid in tight loops — use blit or fill_rect). */
status_t ili9341_draw_pixel(ili9341_t *d, uint16_t x, uint16_t y, uint16_t color);

/* Backlight: 0..100 % when a BL GPIO is wired; otherwise no-op.
 * The simple driver uses on/off only; for true PWM dimming wire BL to a
 * timer pin and replace the implementation with a duty-cycle write. */
status_t ili9341_set_backlight(ili9341_t *d, uint8_t pct);

/* ---- Built-in 5x7 ASCII font (0x20..0x7E) ----
 *
 * One character occupies 5x7 = 35 cells. `size` lets the caller draw the
 * character at NxN pixel scale. The font lives in pca-flash-friendly
 * const memory (~480 B). */
status_t ili9341_draw_char(ili9341_t *d,
                           uint16_t x, uint16_t y, char c,
                           uint16_t fg, uint16_t bg, uint8_t size);
status_t ili9341_draw_string(ili9341_t *d,
                             uint16_t x, uint16_t y, const char *s,
                             uint16_t fg, uint16_t bg, uint8_t size);

#endif /* ILI9341_H_ */
