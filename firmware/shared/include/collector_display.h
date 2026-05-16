/*
 * IchiPing — status display helper for 09_collector / 10_inference.
 *
 * Renders a 5-row servo status panel on the ILI9341 TFT (240x320):
 *
 *   ┌──────────────────────────────────────────┐
 *   │ IchiPing 09_collector                    │   header
 *   ├──────────────────────────────────────────┤
 *   │ window_a  +45° / +75°  [====    ]  MID   │
 *   │ window_b   +0° / +75°  [        ]  CLOSED│
 *   │ window_c  +75° / +75°  [========]  OPEN  │
 *   │ door_AB   +90° / +90°  [========]  OPEN  │
 *   │ door_BC   +45° / +90°  [====    ]  MID   │
 *   ├──────────────────────────────────────────┤
 *   │ excitation: multiband  vol: 0.05         │   footer
 *   │ trial:  7 / 30                           │
 *   └──────────────────────────────────────────┘
 *
 * Each servo row is colour-coded:
 *   CLOSED (logical ~0)         = green
 *   OPEN   (logical ~max)       = orange
 *   MID    (everything else)    = grey
 *
 * Coordinates: logical_deg = servo_config_to_logical(mech). Closed maps
 * to 0, open direction to positive, expected max = 75 (window) or 90 (door).
 * See docs/servo_coords.html for the full coordinate-system spec.
 *
 * This module owns the panel layout; the caller owns the ILI9341 init.
 * Pass a pointer to an already-initialised ili9341_t.
 */

#ifndef COLLECTOR_DISPLAY_H_
#define COLLECTOR_DISPLAY_H_

#include <stdint.h>

#include "ili9341.h"
#include "ichp_cmd.h"        /* ICHP_SERVO_COUNT */

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    ili9341_t *tft;
    /* Cached mechanical angles last drawn — used to skip redraws. */
    float      last_mech[ICHP_SERVO_COUNT];
    /* Footer state */
    char       last_excitation[16];
    int32_t    last_volume_pct;
    int32_t    last_trial;
    int32_t    last_repeats;
    /* Dirty bits */
    bool       header_drawn;
} collector_display_t;

/* Paint the static frame (header, dividers, labels). Call once after
 * ili9341_init succeeds and after servo_config_init. */
void collector_display_init(collector_display_t *d, ili9341_t *tft);

/* Update one servo row. Idempotent on no-change. */
void collector_display_set_servo(collector_display_t *d,
                                 uint8_t servo_idx, float mech_deg);

/* Update all 5 rows from a target pattern. */
void collector_display_set_pattern(collector_display_t *d,
                                   const float mech_deg[ICHP_SERVO_COUNT]);

/* Footer text. Use ""/-1 to skip a field. volume_pct is 0..100. */
void collector_display_set_footer(collector_display_t *d,
                                  const char *excitation, int32_t volume_pct,
                                  int32_t trial, int32_t repeats);

#ifdef __cplusplus
}
#endif

#endif /* COLLECTOR_DISPLAY_H_ */
