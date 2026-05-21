/*
 * IchiPing — Speaker EQ filter (see spk_eq.h).
 */

#include "spk_eq.h"
#include "spk_eq_defaults.h"

#include <string.h>

typedef struct {
    float x1;
    float x2;
    float y1;
    float y2;
} spk_eq_stage_state_t;

static spk_eq_stage_coefs_t s_coefs[SPK_EQ_NUM_STAGES];
static spk_eq_stage_state_t s_state[SPK_EQ_NUM_STAGES];
static bool                 s_enabled;

static void clear_state(void)
{
    memset(s_state, 0, sizeof(s_state));
}

void spk_eq_init(void)
{
    memcpy(s_coefs, SPK_EQ_DEFAULTS, sizeof(s_coefs));
    clear_state();
    s_enabled = false;        /* off at boot — pre-EQ behavior preserved */
}

void spk_eq_reset(void)
{
    memcpy(s_coefs, SPK_EQ_DEFAULTS, sizeof(s_coefs));
    clear_state();
}

bool spk_eq_set_stage(uint8_t stage,
                      float b0, float b1, float b2,
                      float a1, float a2)
{
    if (stage >= SPK_EQ_NUM_STAGES) return false;
    s_coefs[stage].b0 = b0;
    s_coefs[stage].b1 = b1;
    s_coefs[stage].b2 = b2;
    s_coefs[stage].a1 = a1;
    s_coefs[stage].a2 = a2;
    clear_state();    /* coefficient change invalidates filter history */
    return true;
}

bool spk_eq_get_stage(uint8_t stage, spk_eq_stage_coefs_t *out)
{
    if (stage >= SPK_EQ_NUM_STAGES || out == NULL) return false;
    *out = s_coefs[stage];
    return true;
}

void spk_eq_enable(bool enabled)
{
    if (enabled && !s_enabled) {
        clear_state();    /* fresh history when (re)enabling */
    }
    s_enabled = enabled;
}

bool spk_eq_is_enabled(void)
{
    return s_enabled;
}

void spk_eq_apply(int16_t *pcm, uint32_t n_samples)
{
    if (!s_enabled || pcm == NULL || n_samples == 0u) return;

    /* Fresh history at start of each buffer so successive emissions stay
     * independent. The 8-stage transient on the leading samples is the
     * cost; downstream analysis should skip a few ms when EQ is active. */
    clear_state();

    for (uint32_t i = 0; i < n_samples; i++) {
        float x = (float)pcm[i];
        for (uint8_t s = 0; s < SPK_EQ_NUM_STAGES; s++) {
            const spk_eq_stage_coefs_t *c  = &s_coefs[s];
            spk_eq_stage_state_t       *st = &s_state[s];
            const float y = c->b0 * x + c->b1 * st->x1 + c->b2 * st->x2
                            - c->a1 * st->y1 - c->a2 * st->y2;
            st->x2 = st->x1;
            st->x1 = x;
            st->y2 = st->y1;
            st->y1 = y;
            x = y;        /* feed cascaded stage */
        }
        if (x >  32767.0f) x =  32767.0f;
        if (x < -32768.0f) x = -32768.0f;
        pcm[i] = (int16_t)x;
    }
}
