#include "ili9341.h"

/* ---- ILI9341 commands (subset) ---- */
#define CMD_SWRESET     0x01u
#define CMD_SLPOUT      0x11u
#define CMD_DISPOFF     0x28u
#define CMD_DISPON      0x29u
#define CMD_CASET       0x2Au
#define CMD_PASET       0x2Bu
#define CMD_RAMWR       0x2Cu
#define CMD_MADCTL      0x36u
#define CMD_PIXFMT      0x3Au

/* MADCTL bits for rotation (datasheet §8.12). */
#define MADCTL_MY  0x80u
#define MADCTL_MX  0x40u
#define MADCTL_MV  0x20u
#define MADCTL_BGR 0x08u

#define PIXFMT_RGB565 0x55u

/* ---- Built-in 5x7 ASCII font (0x20..0x7E) ----
 *
 * Five bytes per glyph, each byte = a vertical column of 7 pixels
 * (LSB on top). Glyph for space starts at offset 0, character C is at
 * offset (C - 0x20) * 5. Standard "Adafruit / GFX classic" layout — many
 * ports of this font exist; this is the one used in ChrisAaa/Eldur etc.
 */
static const uint8_t s_font5x7[] = {
    0x00,0x00,0x00,0x00,0x00, /*   */ 0x00,0x00,0x5F,0x00,0x00, /* ! */
    0x00,0x07,0x00,0x07,0x00, /* " */ 0x14,0x7F,0x14,0x7F,0x14, /* # */
    0x24,0x2A,0x7F,0x2A,0x12, /* $ */ 0x23,0x13,0x08,0x64,0x62, /* % */
    0x36,0x49,0x55,0x22,0x50, /* & */ 0x00,0x05,0x03,0x00,0x00, /* ' */
    0x00,0x1C,0x22,0x41,0x00, /* ( */ 0x00,0x41,0x22,0x1C,0x00, /* ) */
    0x14,0x08,0x3E,0x08,0x14, /* * */ 0x08,0x08,0x3E,0x08,0x08, /* + */
    0x00,0x50,0x30,0x00,0x00, /* , */ 0x08,0x08,0x08,0x08,0x08, /* - */
    0x00,0x60,0x60,0x00,0x00, /* . */ 0x20,0x10,0x08,0x04,0x02, /* / */
    0x3E,0x51,0x49,0x45,0x3E, /* 0 */ 0x00,0x42,0x7F,0x40,0x00, /* 1 */
    0x42,0x61,0x51,0x49,0x46, /* 2 */ 0x21,0x41,0x45,0x4B,0x31, /* 3 */
    0x18,0x14,0x12,0x7F,0x10, /* 4 */ 0x27,0x45,0x45,0x45,0x39, /* 5 */
    0x3C,0x4A,0x49,0x49,0x30, /* 6 */ 0x01,0x71,0x09,0x05,0x03, /* 7 */
    0x36,0x49,0x49,0x49,0x36, /* 8 */ 0x06,0x49,0x49,0x29,0x1E, /* 9 */
    0x00,0x36,0x36,0x00,0x00, /* : */ 0x00,0x56,0x36,0x00,0x00, /* ; */
    0x08,0x14,0x22,0x41,0x00, /* < */ 0x14,0x14,0x14,0x14,0x14, /* = */
    0x00,0x41,0x22,0x14,0x08, /* > */ 0x02,0x01,0x51,0x09,0x06, /* ? */
    0x32,0x49,0x79,0x41,0x3E, /* @ */ 0x7E,0x11,0x11,0x11,0x7E, /* A */
    0x7F,0x49,0x49,0x49,0x36, /* B */ 0x3E,0x41,0x41,0x41,0x22, /* C */
    0x7F,0x41,0x41,0x22,0x1C, /* D */ 0x7F,0x49,0x49,0x49,0x41, /* E */
    0x7F,0x09,0x09,0x09,0x01, /* F */ 0x3E,0x41,0x49,0x49,0x7A, /* G */
    0x7F,0x08,0x08,0x08,0x7F, /* H */ 0x00,0x41,0x7F,0x41,0x00, /* I */
    0x20,0x40,0x41,0x3F,0x01, /* J */ 0x7F,0x08,0x14,0x22,0x41, /* K */
    0x7F,0x40,0x40,0x40,0x40, /* L */ 0x7F,0x02,0x0C,0x02,0x7F, /* M */
    0x7F,0x04,0x08,0x10,0x7F, /* N */ 0x3E,0x41,0x41,0x41,0x3E, /* O */
    0x7F,0x09,0x09,0x09,0x06, /* P */ 0x3E,0x41,0x51,0x21,0x5E, /* Q */
    0x7F,0x09,0x19,0x29,0x46, /* R */ 0x46,0x49,0x49,0x49,0x31, /* S */
    0x01,0x01,0x7F,0x01,0x01, /* T */ 0x3F,0x40,0x40,0x40,0x3F, /* U */
    0x1F,0x20,0x40,0x20,0x1F, /* V */ 0x3F,0x40,0x38,0x40,0x3F, /* W */
    0x63,0x14,0x08,0x14,0x63, /* X */ 0x07,0x08,0x70,0x08,0x07, /* Y */
    0x61,0x51,0x49,0x45,0x43, /* Z */ 0x00,0x7F,0x41,0x41,0x00, /* [ */
    0x02,0x04,0x08,0x10,0x20, /* \ */ 0x00,0x41,0x41,0x7F,0x00, /* ] */
    0x04,0x02,0x01,0x02,0x04, /* ^ */ 0x40,0x40,0x40,0x40,0x40, /* _ */
    0x00,0x01,0x02,0x04,0x00, /* ` */ 0x20,0x54,0x54,0x54,0x78, /* a */
    0x7F,0x48,0x44,0x44,0x38, /* b */ 0x38,0x44,0x44,0x44,0x20, /* c */
    0x38,0x44,0x44,0x48,0x7F, /* d */ 0x38,0x54,0x54,0x54,0x18, /* e */
    0x08,0x7E,0x09,0x01,0x02, /* f */ 0x0C,0x52,0x52,0x52,0x3E, /* g */
    0x7F,0x08,0x04,0x04,0x78, /* h */ 0x00,0x44,0x7D,0x40,0x00, /* i */
    0x20,0x40,0x44,0x3D,0x00, /* j */ 0x7F,0x10,0x28,0x44,0x00, /* k */
    0x00,0x41,0x7F,0x40,0x00, /* l */ 0x7C,0x04,0x18,0x04,0x78, /* m */
    0x7C,0x08,0x04,0x04,0x78, /* n */ 0x38,0x44,0x44,0x44,0x38, /* o */
    0x7C,0x14,0x14,0x14,0x08, /* p */ 0x08,0x14,0x14,0x18,0x7C, /* q */
    0x7C,0x08,0x04,0x04,0x08, /* r */ 0x48,0x54,0x54,0x54,0x20, /* s */
    0x04,0x3F,0x44,0x40,0x20, /* t */ 0x3C,0x40,0x40,0x20,0x7C, /* u */
    0x1C,0x20,0x40,0x20,0x1C, /* v */ 0x3C,0x40,0x30,0x40,0x3C, /* w */
    0x44,0x28,0x10,0x28,0x44, /* x */ 0x0C,0x50,0x50,0x50,0x3C, /* y */
    0x44,0x64,0x54,0x4C,0x44, /* z */ 0x00,0x08,0x36,0x41,0x00, /* { */
    0x00,0x00,0x7F,0x00,0x00, /* | */ 0x00,0x41,0x36,0x08,0x00, /* } */
    0x08,0x08,0x2A,0x1C,0x08,                                  /* ~ */
};

/* ---- GPIO helpers ---- */
static inline void cs_low(ili9341_t *d)  { GPIO_PinWrite(d->cs_gpio,  d->cs_pin,  0u); }
static inline void cs_high(ili9341_t *d) { GPIO_PinWrite(d->cs_gpio,  d->cs_pin,  1u); }
static inline void dc_cmd(ili9341_t *d)  { GPIO_PinWrite(d->dc_gpio,  d->dc_pin,  0u); }
static inline void dc_dat(ili9341_t *d)  { GPIO_PinWrite(d->dc_gpio,  d->dc_pin,  1u); }
static inline void res_lo(ili9341_t *d)  { GPIO_PinWrite(d->res_gpio, d->res_pin, 0u); }
static inline void res_hi(ili9341_t *d)  { GPIO_PinWrite(d->res_gpio, d->res_pin, 1u); }

static void busy_wait_us(uint32_t us) {
    volatile uint32_t i;
    uint32_t loops = (SystemCoreClock / 1000000u) * us / 4u;
    if (loops == 0u) loops = 1u;
    for (i = 0; i < loops; i++) { __asm volatile ("nop"); }
}
static inline void busy_wait_ms(uint32_t ms) { busy_wait_us(ms * 1000u); }

/* ---- SPI primitives ---- */
static status_t spi_tx(ili9341_t *d, const uint8_t *buf, size_t n) {
    lpspi_transfer_t xfer = {
        .txData      = (uint8_t *)buf,
        .rxData      = NULL,
        .dataSize    = n,
        .configFlags = (uint32_t)kLPSPI_MasterPcs0 | (uint32_t)kLPSPI_MasterPcsContinuous,
    };
    /* We manage CS by GPIO ourselves; the driver's PCS isn't routed to the
     * display CS line, so configFlags above are mostly cosmetic. */
    return LPSPI_MasterTransferBlocking(d->spi, &xfer);
}

static status_t write_cmd(ili9341_t *d, uint8_t cmd) {
    dc_cmd(d);
    cs_low(d);
    status_t s = spi_tx(d, &cmd, 1u);
    cs_high(d);
    return s;
}

static status_t write_data(ili9341_t *d, const uint8_t *buf, size_t n) {
    dc_dat(d);
    cs_low(d);
    status_t s = spi_tx(d, buf, n);
    cs_high(d);
    return s;
}

static status_t write_data_u8(ili9341_t *d, uint8_t v) {
    return write_data(d, &v, 1u);
}

static status_t write_cmd_with_data(ili9341_t *d, uint8_t cmd,
                                    const uint8_t *data, size_t n) {
    status_t s = write_cmd(d, cmd);
    if (s != kStatus_Success) return s;
    if (n > 0u) return write_data(d, data, n);
    return kStatus_Success;
}

/* ---- Reset + init sequence ---- */
static status_t spi_master_init(ili9341_t *d) {
    lpspi_master_config_t cfg;
    LPSPI_MasterGetDefaultConfig(&cfg);
    cfg.baudRate      = d->spi_baud_hz;
    cfg.bitsPerFrame  = 8u;
    cfg.cpol          = kLPSPI_ClockPolarityActiveHigh;
    cfg.cpha          = kLPSPI_ClockPhaseFirstEdge;
    cfg.direction     = kLPSPI_MsbFirst;
    LPSPI_MasterInit(d->spi, &cfg, d->spi_clk_hz);
    return kStatus_Success;
}

static uint8_t madctl_for_rotation(ili9341_rot_t rot) {
    switch (rot) {
        case ILI9341_ROT_PORTRAIT:       return MADCTL_MX | MADCTL_BGR;
        case ILI9341_ROT_LANDSCAPE:      return MADCTL_MV | MADCTL_BGR;
        case ILI9341_ROT_PORTRAIT_FLIP:  return MADCTL_MY | MADCTL_BGR;
        case ILI9341_ROT_LANDSCAPE_FLIP: return MADCTL_MX | MADCTL_MY | MADCTL_MV | MADCTL_BGR;
    }
    return MADCTL_MX | MADCTL_BGR;
}

status_t ili9341_init(ili9341_t *d) {
    if (d == NULL || d->spi == NULL) return kStatus_InvalidArgument;

    /* Hardware reset: pulse RES low ≥ 10 us, then wait ≥ 120 ms. */
    res_hi(d); busy_wait_ms(5);
    res_lo(d); busy_wait_ms(20);
    res_hi(d); busy_wait_ms(150);

    cs_high(d);
    dc_dat(d);

    status_t s = spi_master_init(d);
    if (s != kStatus_Success) return s;

    s = write_cmd(d, CMD_SWRESET); if (s != kStatus_Success) return s;
    busy_wait_ms(120);
    s = write_cmd(d, CMD_SLPOUT);  if (s != kStatus_Success) return s;
    busy_wait_ms(120);

    s = write_cmd_with_data(d, CMD_PIXFMT, (const uint8_t[]){PIXFMT_RGB565}, 1u);
    if (s != kStatus_Success) return s;

    s = write_cmd_with_data(d, CMD_MADCTL,
                            (const uint8_t[]){madctl_for_rotation(d->rotation)}, 1u);
    if (s != kStatus_Success) return s;

    s = write_cmd(d, CMD_DISPON);
    if (s != kStatus_Success) return s;

    if (d->rotation == ILI9341_ROT_PORTRAIT || d->rotation == ILI9341_ROT_PORTRAIT_FLIP) {
        d->width  = 240u; d->height = 320u;
    } else {
        d->width  = 320u; d->height = 240u;
    }

    /* Turn the backlight on if a GPIO is wired. */
    (void)ili9341_set_backlight(d, 100u);
    return kStatus_Success;
}

/* ---- Drawing primitives ---- */

status_t ili9341_set_window(ili9341_t *d,
                            uint16_t x0, uint16_t y0,
                            uint16_t x1, uint16_t y1) {
    uint8_t buf[4];
    buf[0] = (uint8_t)(x0 >> 8); buf[1] = (uint8_t)(x0 & 0xFFu);
    buf[2] = (uint8_t)(x1 >> 8); buf[3] = (uint8_t)(x1 & 0xFFu);
    status_t s = write_cmd_with_data(d, CMD_CASET, buf, 4u);
    if (s != kStatus_Success) return s;

    buf[0] = (uint8_t)(y0 >> 8); buf[1] = (uint8_t)(y0 & 0xFFu);
    buf[2] = (uint8_t)(y1 >> 8); buf[3] = (uint8_t)(y1 & 0xFFu);
    s = write_cmd_with_data(d, CMD_PASET, buf, 4u);
    if (s != kStatus_Success) return s;

    return write_cmd(d, CMD_RAMWR);
}

status_t ili9341_blit(ili9341_t *d, const uint16_t *pixels, size_t n_pixels) {
    /* Pixels are RGB565 with high byte first on the wire (ILI9341 PIXFMT
     * 0x55 = 16-bit, MSB first). Convert byte order on the fly via a
     * small stack buffer so the caller can keep native uint16_t arrays. */
    enum { BATCH = 256 };
    uint8_t batch[BATCH * 2];
    dc_dat(d);
    cs_low(d);
    while (n_pixels > 0) {
        size_t n = (n_pixels > BATCH) ? BATCH : n_pixels;
        for (size_t i = 0; i < n; i++) {
            uint16_t p = pixels[i];
            batch[i * 2]     = (uint8_t)(p >> 8);
            batch[i * 2 + 1] = (uint8_t)(p & 0xFFu);
        }
        status_t s = spi_tx(d, batch, n * 2u);
        if (s != kStatus_Success) { cs_high(d); return s; }
        pixels   += n;
        n_pixels -= n;
    }
    cs_high(d);
    return kStatus_Success;
}

status_t ili9341_fill_rect(ili9341_t *d,
                           uint16_t x, uint16_t y, uint16_t w, uint16_t h,
                           uint16_t color) {
    if (w == 0u || h == 0u) return kStatus_Success;
    if (x >= d->width || y >= d->height) return kStatus_InvalidArgument;
    if (x + w > d->width)  w = d->width  - x;
    if (y + h > d->height) h = d->height - y;

    status_t s = ili9341_set_window(d, x, y, (uint16_t)(x + w - 1u), (uint16_t)(y + h - 1u));
    if (s != kStatus_Success) return s;

    /* Tile a small same-colour buffer to avoid one SPI call per pixel. */
    enum { BATCH = 256 };
    uint8_t batch[BATCH * 2];
    for (size_t i = 0; i < BATCH; i++) {
        batch[i * 2]     = (uint8_t)(color >> 8);
        batch[i * 2 + 1] = (uint8_t)(color & 0xFFu);
    }
    size_t remaining = (size_t)w * (size_t)h;
    dc_dat(d);
    cs_low(d);
    while (remaining > 0) {
        size_t n = (remaining > BATCH) ? BATCH : remaining;
        s = spi_tx(d, batch, n * 2u);
        if (s != kStatus_Success) { cs_high(d); return s; }
        remaining -= n;
    }
    cs_high(d);
    return kStatus_Success;
}

status_t ili9341_fill_screen(ili9341_t *d, uint16_t color) {
    return ili9341_fill_rect(d, 0, 0, d->width, d->height, color);
}

status_t ili9341_draw_pixel(ili9341_t *d, uint16_t x, uint16_t y, uint16_t color) {
    if (x >= d->width || y >= d->height) return kStatus_InvalidArgument;
    status_t s = ili9341_set_window(d, x, y, x, y);
    if (s != kStatus_Success) return s;
    uint8_t buf[2] = { (uint8_t)(color >> 8), (uint8_t)(color & 0xFFu) };
    return write_data(d, buf, 2u);
}

status_t ili9341_set_backlight(ili9341_t *d, uint8_t pct) {
    if (d->bl_gpio == NULL) return kStatus_Success;
    GPIO_PinWrite(d->bl_gpio, d->bl_pin, (pct > 0u) ? 1u : 0u);
    return kStatus_Success;
}

/* ---- Font drawing ---- */

status_t ili9341_draw_char(ili9341_t *d,
                           uint16_t x, uint16_t y, char c,
                           uint16_t fg, uint16_t bg, uint8_t size) {
    if (c < 0x20 || c > 0x7E) c = '?';
    if (size == 0u) size = 1u;
    const uint8_t *glyph = &s_font5x7[(uint8_t)(c - 0x20) * 5u];

    for (uint8_t col = 0; col < 5u; col++) {
        uint8_t bits = glyph[col];
        for (uint8_t row = 0; row < 7u; row++) {
            uint16_t color = (bits & (1u << row)) ? fg : bg;
            (void)ili9341_fill_rect(d,
                                    (uint16_t)(x + col * size),
                                    (uint16_t)(y + row * size),
                                    size, size, color);
        }
    }
    /* Pad one column of background on the right for letter spacing. */
    (void)ili9341_fill_rect(d, (uint16_t)(x + 5u * size), y,
                            size, (uint16_t)(7u * size), bg);
    return kStatus_Success;
}

status_t ili9341_draw_string(ili9341_t *d,
                             uint16_t x, uint16_t y, const char *s,
                             uint16_t fg, uint16_t bg, uint8_t size) {
    if (s == NULL) return kStatus_InvalidArgument;
    if (size == 0u) size = 1u;
    while (*s) {
        if (x + 6u * size > d->width) {
            x = 0;
            y = (uint16_t)(y + 8u * size);
            if (y >= d->height) return kStatus_Success;
        }
        (void)ili9341_draw_char(d, x, y, *s, fg, bg, size);
        x = (uint16_t)(x + 6u * size);
        s++;
    }
    return kStatus_Success;
}
