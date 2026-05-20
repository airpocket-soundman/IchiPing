/*
 * IchiPing — collector status display.
 * See collector_display.h for the panel layout.
 *
 * Uses the built-in 5x7 font from ili9341.c at scale 2 (10x14 effective)
 * for body rows and scale 3 (15x21) for the header. Layout is sized for
 * portrait 240x320 — adjust LINE_H / panel constants if you switch
 * rotation in the caller.
 */

#include "collector_display.h"
#include "servo_config.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

/* ---- Layout constants (portrait 240x320) ---- */

#define PANEL_W              240
#define PANEL_H              320

#define HEADER_H              28
#define FOOTER_H              48
#define ROW_H                 36
#define ROW_TOP               (HEADER_H + 4)
#define LABEL_X                4
#define VALUE_X              100
#define BAR_X                100
#define BAR_W                100
#define BAR_H                 10
#define STATE_X              204

#define COL_BG               ILI9341_BLACK
#define COL_FG               ILI9341_WHITE
#define COL_HEADER_BG        ILI9341_NAVY
#define COL_DIVIDER          ILI9341_GREY
#define COL_BAR_BG           ILI9341_GREY
#define COL_CLOSED           ILI9341_GREEN
#define COL_OPEN             ILI9341_ORANGE
#define COL_MID              ILI9341_YELLOW

#define CLOSED_THRESHOLD_DEG  3.0f   /* logical_deg <= 3 -> CLOSED */
#define OPEN_THRESHOLD_FRAC   0.95f  /* logical_deg >= 95% of max -> OPEN */

typedef enum {
    STATE_CLOSED = 0,
    STATE_OPEN,
    STATE_MID,
} state_kind_t;

static state_kind_t classify(uint8_t servo_idx, float logical_deg)
{
    if (logical_deg <= CLOSED_THRESHOLD_DEG) return STATE_CLOSED;
    float max = servo_config_logical_max(servo_idx);
    if (max > 0.0f && logical_deg >= OPEN_THRESHOLD_FRAC * max) return STATE_OPEN;
    return STATE_MID;
}

static uint16_t state_color(state_kind_t s)
{
    switch (s) {
        case STATE_CLOSED: return COL_CLOSED;
        case STATE_OPEN:   return COL_OPEN;
        default:           return COL_MID;
    }
}

static const char *state_label(state_kind_t s)
{
    switch (s) {
        case STATE_CLOSED: return "CLOSED";
        case STATE_OPEN:   return "OPEN  ";
        default:           return "MID   ";
    }
}

static void draw_header(collector_display_t *d)
{
    (void)ili9341_fill_rect(d->tft, 0, 0, PANEL_W, HEADER_H, COL_HEADER_BG);
    (void)ili9341_draw_string(d->tft, 6, 7, "IchiPing collector",
                              COL_FG, COL_HEADER_BG, 2);
    (void)ili9341_fill_rect(d->tft, 0, HEADER_H, PANEL_W, 2, COL_DIVIDER);
}

static void draw_footer_static(collector_display_t *d)
{
    int y = HEADER_H + 4 + ROW_H * ICHP_SERVO_COUNT;
    (void)ili9341_fill_rect(d->tft, 0, y, PANEL_W, 2, COL_DIVIDER);
}

static void draw_row(collector_display_t *d, uint8_t i, float mech_deg)
{
    const int y = ROW_TOP + ROW_H * (int)i;
    const float logical = servo_config_to_logical(i, mech_deg);
    const float lmax    = servo_config_logical_max(i);
    const state_kind_t st = classify(i, logical);

    /* Clear row */
    (void)ili9341_fill_rect(d->tft, 0, y, PANEL_W, ROW_H - 2, COL_BG);

    /* Label */
    (void)ili9341_draw_string(d->tft, LABEL_X, y + 4, ICHP_SERVO_NAMES[i],
                              COL_FG, COL_BG, 2);

    /* Value:  "+45° / +75°" */
    char val[24];
    int la = (int)lroundf(logical);
    int lm = (int)lroundf(lmax);
    snprintf(val, sizeof(val), "%+3d/%+3d", la, lm);
    (void)ili9341_draw_string(d->tft, VALUE_X, y + 4, val,
                              state_color(st), COL_BG, 2);

    /* Bar */
    int bar_y = y + 22;
    (void)ili9341_fill_rect(d->tft, BAR_X, bar_y, BAR_W, BAR_H, COL_BAR_BG);
    float frac = (lmax > 0.0f) ? (logical / lmax) : 0.0f;
    if (frac < 0.0f) frac = 0.0f;
    if (frac > 1.0f) frac = 1.0f;
    int fill_w = (int)(frac * (float)BAR_W);
    if (fill_w > 0) {
        (void)ili9341_fill_rect(d->tft, BAR_X, bar_y, (uint16_t)fill_w, BAR_H,
                                state_color(st));
    }

    /* State badge */
    (void)ili9341_draw_string(d->tft, STATE_X, y + 4, state_label(st),
                              state_color(st), COL_BG, 1);
}

void collector_display_init(collector_display_t *d, ili9341_t *tft)
{
    memset(d, 0, sizeof(*d));
    d->tft = tft;
    for (int i = 0; i < ICHP_SERVO_COUNT; i++) {
        d->last_mech[i] = NAN;
    }
    d->last_volume_pct = -1;
    d->last_trial   = -1;
    d->last_repeats = -1;
    d->last_excitation[0] = '\0';

    (void)ili9341_fill_screen(tft, COL_BG);
    draw_header(d);
    /* Pre-paint rows from current config (home position) so the layout is
     * visible before the first SERVO command. */
    const servo_config_t *cfg = servo_config_get();
    for (uint8_t i = 0; i < ICHP_SERVO_COUNT; i++) {
        draw_row(d, i, cfg->home_deg[i]);
        d->last_mech[i] = cfg->home_deg[i];
    }
    draw_footer_static(d);
    d->header_drawn = true;
}

void collector_display_set_servo(collector_display_t *d,
                                 uint8_t servo_idx, float mech_deg)
{
    if (!d || !d->header_drawn || servo_idx >= ICHP_SERVO_COUNT) return;
    if (!isnan(d->last_mech[servo_idx]) &&
        fabsf(mech_deg - d->last_mech[servo_idx]) < 0.05f) {
        return;  /* no visible change */
    }
    draw_row(d, servo_idx, mech_deg);
    d->last_mech[servo_idx] = mech_deg;
}

void collector_display_set_pattern(collector_display_t *d,
                                   const float mech_deg[ICHP_SERVO_COUNT])
{
    if (!d || !mech_deg) return;
    for (uint8_t i = 0; i < ICHP_SERVO_COUNT; i++) {
        collector_display_set_servo(d, i, mech_deg[i]);
    }
}

void collector_display_set_footer(collector_display_t *d,
                                  const char *excitation, int32_t volume_pct,
                                  int32_t trial, int32_t repeats)
{
    if (!d || !d->header_drawn) return;
    const int y0 = ROW_TOP + ROW_H * ICHP_SERVO_COUNT + 6;

    bool need = false;
    if (excitation && strncmp(excitation, d->last_excitation, sizeof(d->last_excitation) - 1) != 0) {
        strncpy(d->last_excitation, excitation, sizeof(d->last_excitation) - 1);
        d->last_excitation[sizeof(d->last_excitation) - 1] = '\0';
        need = true;
    }
    if (volume_pct >= 0 && volume_pct != d->last_volume_pct) {
        d->last_volume_pct = volume_pct;
        need = true;
    }
    if (trial >= 0 && trial != d->last_trial) {
        d->last_trial = trial;
        need = true;
    }
    if (repeats >= 0 && repeats != d->last_repeats) {
        d->last_repeats = repeats;
        need = true;
    }
    if (!need) return;

    (void)ili9341_fill_rect(d->tft, 0, y0, PANEL_W, FOOTER_H - 4, COL_BG);
    char line1[40], line2[40];
    snprintf(line1, sizeof(line1), "%s vol %d%%",
             d->last_excitation[0] ? d->last_excitation : "----",
             (int)(d->last_volume_pct < 0 ? 0 : d->last_volume_pct));
    snprintf(line2, sizeof(line2), "trial %3d/%3d",
             (int)((d->last_trial < 0) ? 0 : d->last_trial),
             (int)((d->last_repeats < 0) ? 0 : d->last_repeats));
    (void)ili9341_draw_string(d->tft, 4, y0,      line1, COL_FG, COL_BG, 2);
    (void)ili9341_draw_string(d->tft, 4, y0 + 22, line2, COL_FG, COL_BG, 2);
}
