/*
 * IchiPing — Hardcoded SPK EQ default coefficients.
 *
 * Initial values are all-stage identity (passthrough) so EQ ENABLE without
 * SET commands does nothing visible. After SPK/mic free-field calibration
 * (docs/probe_sound.html §3.A), regenerate this file with the measured
 * emphasis filter so factory boots already have a sensible EQ baked in.
 *
 * Each stage is one biquad in Direct Form I, normalized so a0 = 1:
 *   y[n] = b0*x[n] + b1*x[n-1] + b2*x[n-2] - a1*y[n-1] - a2*y[n-2]
 * Identity: b0=1, b1=0, b2=0, a1=0, a2=0
 */

#ifndef SPK_EQ_DEFAULTS_H_
#define SPK_EQ_DEFAULTS_H_

#include "spk_eq.h"

#define SPK_EQ_STAGE_IDENTITY { 1.0f, 0.0f, 0.0f, 0.0f, 0.0f }

static const spk_eq_stage_coefs_t SPK_EQ_DEFAULTS[SPK_EQ_NUM_STAGES] = {
    SPK_EQ_STAGE_IDENTITY,
    SPK_EQ_STAGE_IDENTITY,
    SPK_EQ_STAGE_IDENTITY,
    SPK_EQ_STAGE_IDENTITY,
    SPK_EQ_STAGE_IDENTITY,
    SPK_EQ_STAGE_IDENTITY,
    SPK_EQ_STAGE_IDENTITY,
    SPK_EQ_STAGE_IDENTITY,
};

#endif /* SPK_EQ_DEFAULTS_H_ */
