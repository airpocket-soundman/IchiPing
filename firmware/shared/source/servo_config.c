/*
 * IchiPing — servo home/open store + coordinate conversion.
 * See servo_config.h for the public contract and docs/servo_coords.html
 * for the rationale behind the dual coordinate system.
 */

#include "servo_config.h"

#include <math.h>
#include <string.h>

/* Initial guess only — REPLACE these after running calibration in 09_collector.
 *
 * Mechanical defaults assume SG90 horns mounted so that home (closed) is
 * at 0deg and the opening swing is +. After calibration the typical drift
 * is +-10deg per servo on home, with open_deg = home + 75 (windows) or
 * home + 90 (doors). */
const servo_config_t SERVO_CONFIG_DEFAULTS = {
    .home_deg = {  0.0f,  0.0f,  0.0f,  0.0f,  0.0f },
    .open_deg = { 75.0f, 75.0f, 75.0f, 90.0f, 90.0f },
    .kind     = {
        ICHP_SERVO_KIND_WINDOW,   /* window_a */
        ICHP_SERVO_KIND_WINDOW,   /* window_b */
        ICHP_SERVO_KIND_WINDOW,   /* window_c */
        ICHP_SERVO_KIND_DOOR,     /* door_AB  */
        ICHP_SERVO_KIND_DOOR,     /* door_BC  */
    },
};

static servo_config_t s_cfg;
static bool           s_inited = false;

bool servo_config_init(void)
{
    s_cfg    = SERVO_CONFIG_DEFAULTS;
    s_inited = true;
    return false;   /* no flash backend yet */
}

const servo_config_t *servo_config_get(void)
{
    if (!s_inited) (void)servo_config_init();
    return &s_cfg;
}

bool servo_config_set_home(uint8_t servo_idx, float mech_deg)
{
    if (!s_inited) (void)servo_config_init();
    if (servo_idx >= ICHP_SERVO_COUNT) return false;
    s_cfg.home_deg[servo_idx] = mech_deg;
    return true;
}

bool servo_config_set_open(uint8_t servo_idx, float mech_deg)
{
    if (!s_inited) (void)servo_config_init();
    if (servo_idx >= ICHP_SERVO_COUNT) return false;
    s_cfg.open_deg[servo_idx] = mech_deg;
    return true;
}

int servo_config_save_flash(void)
{
    /* TODO: implement against MCXN947 IAP. Sentinel -1 = NOT_IMPL. */
    return -1;
}

float servo_config_logical_max(uint8_t servo_idx)
{
    if (!s_inited) (void)servo_config_init();
    if (servo_idx >= ICHP_SERVO_COUNT) return 0.0f;
    return (s_cfg.kind[servo_idx] == ICHP_SERVO_KIND_DOOR)
         ? ICHP_LOGICAL_MAX_DOOR
         : ICHP_LOGICAL_MAX_WINDOW;
}

float servo_config_to_logical(uint8_t servo_idx, float mech_deg)
{
    if (!s_inited) (void)servo_config_init();
    if (servo_idx >= ICHP_SERVO_COUNT) return 0.0f;
    const float home = s_cfg.home_deg[servo_idx];
    const float open = s_cfg.open_deg[servo_idx];
    const float sign = (open >= home) ? 1.0f : -1.0f;
    return sign * (mech_deg - home);
}

float servo_config_to_mechanical(uint8_t servo_idx, float logical_deg)
{
    if (!s_inited) (void)servo_config_init();
    if (servo_idx >= ICHP_SERVO_COUNT) return 0.0f;
    const float home = s_cfg.home_deg[servo_idx];
    const float open = s_cfg.open_deg[servo_idx];
    const float sign = (open >= home) ? 1.0f : -1.0f;
    return home + sign * logical_deg;
}
