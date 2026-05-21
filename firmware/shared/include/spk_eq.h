/*
 * IchiPing — Speaker EQ filter (8-stage biquad cascade, Direct Form I).
 *
 * Sits between pattern_render() and SAI TX in the 09_collector pipeline.
 * Applied in-place to the int16 PCM excitation buffer regardless of the
 * pattern type (PULSE / SWEEP / NOISE / future kinds), so a single call
 * after pattern_render() filters everything.
 *
 * Default behavior is **disabled + identity coefficients (passthrough)**.
 * Out of the box the firmware behaves exactly as before the EQ was added.
 * To activate filtering, the host must explicitly:
 *   1. EQ SET <stage> <b0> <b1> <b2> <a1> <a2>   (per stage; 0..7)
 *   2. EQ ENABLE
 *
 * Hardcoded defaults live in spk_eq_defaults.h. Initially all-identity;
 * after SPK/mic free-field calibration, regenerate that header with
 * measured emphasis coefficients (see docs/probe_sound.html §3.A).
 *
 * Coefficient/state representation: float32, normalized so a0 = 1.
 *   y[n] = b0*x[n] + b1*x[n-1] + b2*x[n-2] - a1*y[n-1] - a2*y[n-2]
 *
 * CPU cost @ 16 kHz, 8 stages, Cortex-M33 + FPU:
 *   ~1.3 M float ops/s — well under 1 % of 150 MHz.
 */

#ifndef SPK_EQ_H_
#define SPK_EQ_H_

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

#define SPK_EQ_NUM_STAGES 8u

typedef struct {
    float b0;
    float b1;
    float b2;
    float a1;
    float a2;
} spk_eq_stage_coefs_t;

/* Initialise to hardcoded defaults from spk_eq_defaults.h, clear state,
 * and leave the filter **disabled**. Call once at boot. */
void spk_eq_init(void);

/* Restore hardcoded defaults and clear state. Does NOT change the
 * enabled/disabled flag. Use this to recover from a bad EQ SET. */
void spk_eq_reset(void);

/* Replace one stage's coefficients. Returns false if stage >= 8.
 * Clears all-stage state buffers (coef change invalidates history). */
bool spk_eq_set_stage(uint8_t stage,
                      float b0, float b1, float b2,
                      float a1, float a2);

/* Read back one stage's coefficients. Returns false if stage out of range. */
bool spk_eq_get_stage(uint8_t stage, spk_eq_stage_coefs_t *out);

/* Toggle the filter on/off. Disabled: spk_eq_apply() returns immediately. */
void spk_eq_enable(bool enabled);
bool spk_eq_is_enabled(void);

/* Apply the filter in-place to an int16 PCM buffer.
 * - No-op when disabled (default state).
 * - Clears state at function entry so successive patterns don't bleed.
 * - Saturates to [INT16_MIN, INT16_MAX].
 *
 * Safe to call after every pattern_render(); when EQ is disabled it
 * is effectively free (single bool test). */
void spk_eq_apply(int16_t *pcm, uint32_t n_samples);

#ifdef __cplusplus
}
#endif

#endif /* SPK_EQ_H_ */
