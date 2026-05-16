/*
 * IchiPing — servo home/open store + display coordinate conversion.
 *
 * Two coordinate systems coexist:
 *
 *   mechanical_deg  : raw PWM angle sent to PCA9685 (0..180), depends on
 *                     how the SG90 horn was mounted on each axle. This is
 *                     what gets calibrated.
 *   logical_deg     : "closed = 0, open direction = positive" display /
 *                     log angle. Computed as sign * (mech - home_deg)
 *                     where sign = +1 if open_deg > home_deg, else -1.
 *
 * Per servo we keep three flash-resident values:
 *   home_deg[i]  : mechanical angle for the "closed" position.
 *   open_deg[i]  : mechanical angle for the "fully open" position.
 *   kind[i]      : WINDOW or DOOR. Affects the *expected* logical range
 *                  used by the display:
 *                    WINDOW : logical_max = 75 deg
 *                    DOOR   : logical_max = 90 deg
 *                  Window mechanics on the IchiPing model clear at ~75deg
 *                  before the sash binds; doors swing the full 90deg.
 *
 * The actual mechanical span (open - home) should equal logical_max
 * after good calibration, but discrepancies are tolerated and visible on
 * the display (bar overshoots / undershoots).
 *
 * Persistence (flash) is currently a stub — see servo_config_save_flash.
 * MVP workflow: tune via SET HOME + SET OPEN, read with GET HOME / GET
 * OPEN, hand-paste into SERVO_CONFIG_DEFAULTS, rebuild.
 *
 * Full coordinate-system rationale: docs/servo_coords.html
 */

#ifndef SERVO_CONFIG_H_
#define SERVO_CONFIG_H_

#include <stdint.h>
#include <stdbool.h>

#include "ichp_cmd.h"   /* ICHP_SERVO_COUNT */

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    ICHP_SERVO_KIND_WINDOW = 0,
    ICHP_SERVO_KIND_DOOR   = 1,
} ichp_servo_kind_t;

#define ICHP_LOGICAL_MAX_WINDOW   75.0f
#define ICHP_LOGICAL_MAX_DOOR     90.0f

typedef struct {
    float             home_deg[ICHP_SERVO_COUNT];   /* "closed" mechanical angle */
    float             open_deg[ICHP_SERVO_COUNT];   /* "fully open" mechanical angle */
    ichp_servo_kind_t kind    [ICHP_SERVO_COUNT];   /* WINDOW or DOOR (display + range hint) */
} servo_config_t;

/* Compiled-in defaults — used at boot when no valid flash copy exists.
 * Update these after mechanical calibration (see header comment). */
extern const servo_config_t SERVO_CONFIG_DEFAULTS;

/* Initialise the RAM copy. Tries flash load first; falls back to
 * SERVO_CONFIG_DEFAULTS. Returns true if loaded from flash. */
bool servo_config_init(void);

/* Read current RAM copy. */
const servo_config_t *servo_config_get(void);

/* Update one home angle in RAM. Returns false on bad servo_idx. */
bool servo_config_set_home(uint8_t servo_idx, float mech_deg);
bool servo_config_set_open(uint8_t servo_idx, float mech_deg);

/* Persist current RAM copy to flash. Returns 0 on write, -1 if no
 * flash backend is built. */
int  servo_config_save_flash(void);

/* ---- Coordinate conversion ---- */

/* Logical (display) angle from a mechanical PCA9685 angle, using the
 * configured home / open for this servo. Output is in [-eps, logical_max +eps]
 * for in-spec moves; outside that range when over-driven. */
float servo_config_to_logical(uint8_t servo_idx, float mech_deg);

/* Inverse: logical (closed=0, open=+) -> mechanical. */
float servo_config_to_mechanical(uint8_t servo_idx, float logical_deg);

/* Expected logical maximum for this servo (75 for WINDOW, 90 for DOOR). */
float servo_config_logical_max(uint8_t servo_idx);

#ifdef __cplusplus
}
#endif

#endif /* SERVO_CONFIG_H_ */
