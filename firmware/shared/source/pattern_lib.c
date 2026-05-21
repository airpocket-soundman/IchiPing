/*
 * IchiPing — in-RAM excitation pattern library (see pattern_lib.h).
 *
 * Memory budget (single global g_pattern_lib):
 *   pattern_t entries[16]      ≈ 13 KB
 *   builder scratch (build_*)  ≈   0.8 KB
 *   ----                         ----
 *   total                      ≈  14 KB
 *
 * All numeric inputs from the protocol are uint32 milliseconds / Hz; conversion
 * to samples happens at render time so the same library survives a sample-rate
 * change without re-pushing patterns.
 */

#include "pattern_lib.h"

#include <math.h>
#include <string.h>

pattern_lib_t g_pattern_lib;

void pattern_lib_init(void)
{
    memset(&g_pattern_lib, 0, sizeof(g_pattern_lib));
}

void pattern_lib_clear(void)
{
    g_pattern_lib.count         = 0;
    g_pattern_lib.selected      = 0;
    g_pattern_lib.building      = false;
    g_pattern_lib.build_n_tones = 0;
    g_pattern_lib.build_name[0] = '\0';
    /* Leave entries[] / build_tones[] zeroing implicit via count/n_tones. */
}

static void copy_name(char dst[PATTERN_NAME_LEN], const char *src)
{
    if (src == NULL) {
        dst[0] = '\0';
        return;
    }
    size_t n = 0;
    while (n < (size_t)(PATTERN_NAME_LEN - 1u) && src[n] != '\0') {
        dst[n] = src[n];
        n++;
    }
    dst[n] = '\0';
}

bool pattern_lib_pulse_begin(const char *name)
{
    if (g_pattern_lib.count >= PATTERN_LIB_MAX_PATTERNS) return false;
    g_pattern_lib.building      = true;
    g_pattern_lib.build_n_tones = 0;
    copy_name(g_pattern_lib.build_name, name);
    return true;
}

bool pattern_lib_pulse_add_tone(uint32_t freq_hz, uint32_t on_ms, uint32_t off_ms)
{
    if (!g_pattern_lib.building) return false;
    if (g_pattern_lib.build_n_tones >= PATTERN_MAX_TONES) return false;
    pulse_tone_t *t = &g_pattern_lib.build_tones[g_pattern_lib.build_n_tones++];
    t->freq_hz = freq_hz;
    t->on_ms   = on_ms;
    t->off_ms  = off_ms;
    return true;
}

bool pattern_lib_pulse_end(uint8_t repeat)
{
    if (!g_pattern_lib.building) return false;
    if (g_pattern_lib.count >= PATTERN_LIB_MAX_PATTERNS) {
        g_pattern_lib.building = false;
        return false;
    }
    pattern_t *p = &g_pattern_lib.entries[g_pattern_lib.count++];
    p->kind = PATTERN_KIND_PULSE;
    copy_name(p->name, g_pattern_lib.build_name);
    p->pulse.n_tones = g_pattern_lib.build_n_tones;
    p->pulse.repeat  = (repeat < 1u) ? 1u : repeat;
    memcpy(p->pulse.tones, g_pattern_lib.build_tones,
           (size_t)g_pattern_lib.build_n_tones * sizeof(pulse_tone_t));

    g_pattern_lib.building      = false;
    g_pattern_lib.build_n_tones = 0;
    return true;
}

bool pattern_lib_add_sweep(const char *name,
                           uint32_t start_hz, uint32_t end_hz,
                           uint32_t sweep_ms, uint32_t silence_ms)
{
    if (g_pattern_lib.count >= PATTERN_LIB_MAX_PATTERNS) return false;
    pattern_t *p = &g_pattern_lib.entries[g_pattern_lib.count++];
    p->kind = PATTERN_KIND_SWEEP;
    copy_name(p->name, name);
    p->sweep.start_hz   = start_hz;
    p->sweep.end_hz     = end_hz;
    p->sweep.sweep_ms   = sweep_ms;
    p->sweep.silence_ms = silence_ms;
    return true;
}

bool pattern_lib_add_noise(const char *name,
                           uint32_t duration_ms,
                           uint16_t volume_pct,
                           uint8_t  shape)
{
    if (g_pattern_lib.count >= PATTERN_LIB_MAX_PATTERNS) return false;
    if (volume_pct > 100u) volume_pct = 100u;
    pattern_t *p = &g_pattern_lib.entries[g_pattern_lib.count++];
    p->kind = PATTERN_KIND_NOISE;
    copy_name(p->name, name);
    p->noise.duration_ms = duration_ms;
    p->noise.volume_pct  = volume_pct;
    p->noise.shape       = shape;
    p->noise._pad        = 0u;
    return true;
}

bool pattern_lib_select(uint8_t index)
{
    if (index >= g_pattern_lib.count) return false;
    g_pattern_lib.selected = index;
    return true;
}

const pattern_t *pattern_lib_get(uint8_t index)
{
    if (index >= g_pattern_lib.count) return NULL;
    return &g_pattern_lib.entries[index];
}

uint32_t pattern_total_samples(const pattern_t *p, uint32_t sample_rate_hz)
{
    if (p == NULL) return 0u;
    uint32_t total_ms = 0u;
    if (p->kind == PATTERN_KIND_PULSE) {
        for (uint8_t i = 0; i < p->pulse.n_tones; i++) {
            total_ms += p->pulse.tones[i].on_ms + p->pulse.tones[i].off_ms;
        }
        total_ms *= (uint32_t)p->pulse.repeat;
    } else if (p->kind == PATTERN_KIND_SWEEP) {
        total_ms = p->sweep.sweep_ms + p->sweep.silence_ms;
    } else if (p->kind == PATTERN_KIND_NOISE) {
        total_ms = p->noise.duration_ms;
    } else {
        return 0u;
    }
    return (sample_rate_hz * total_ms) / 1000u;
}

/* ---- Rendering ---- */

static int16_t scale_sample(float s, int32_t volume_pct)
{
    float v = (float)volume_pct / 100.0f;
    if (v < 0.0f) v = 0.0f;
    if (v > 1.0f) v = 1.0f;
    float scaled = s * 30000.0f * v;
    if (scaled >  32767.0f) scaled =  32767.0f;
    if (scaled < -32768.0f) scaled = -32768.0f;
    return (int16_t)scaled;
}

static void render_burst(int16_t *out, uint32_t start, uint32_t on_n,
                         uint32_t fade_n, float freq_hz,
                         uint32_t sample_rate_hz, int32_t volume_pct)
{
    if (on_n == 0u || freq_hz <= 0.0f) return;
    const float two_pi = 6.28318530718f;
    for (uint32_t i = 0; i < on_n; i++) {
        float t = (float)i / (float)sample_rate_hz;
        float env = 1.0f;
        if (fade_n > 0u) {
            if (i < fade_n) {
                env = 0.5f * (1.0f - cosf(3.14159265f * (float)i / (float)fade_n));
            } else if (i + fade_n > on_n) {
                env = 0.5f * (1.0f - cosf(3.14159265f * (float)(on_n - i) / (float)fade_n));
            }
        }
        float s = env * sinf(two_pi * freq_hz * t);
        out[start + i] = scale_sample(s, volume_pct);
    }
}

static uint32_t render_pulse(const pattern_t *p, int16_t *out, uint32_t cap,
                             uint32_t sample_rate_hz, int32_t volume_pct)
{
    uint32_t total = pattern_total_samples(p, sample_rate_hz);
    if (total > cap) total = cap;
    memset(out, 0, (size_t)total * sizeof(int16_t));

    /* 0.2 ms raised-cosine fade in/out per burst — matches the old
     * multiband renderer so existing data stays comparable. */
    uint32_t fade_n = (sample_rate_hz * 2u) / 10000u;   /* 0.2 ms */

    uint32_t cursor = 0u;
    for (uint8_t r = 0; r < p->pulse.repeat; r++) {
        for (uint8_t i = 0; i < p->pulse.n_tones; i++) {
            const pulse_tone_t *tn = &p->pulse.tones[i];
            uint32_t on_n  = (sample_rate_hz * tn->on_ms ) / 1000u;
            uint32_t off_n = (sample_rate_hz * tn->off_ms) / 1000u;
            if (cursor + on_n > total) on_n = (total > cursor) ? (total - cursor) : 0u;
            render_burst(out, cursor, on_n, fade_n, (float)tn->freq_hz,
                         sample_rate_hz, volume_pct);
            cursor += on_n + off_n;
            if (cursor >= total) return total;
        }
    }
    return total;
}

static uint32_t render_sweep(const pattern_t *p, int16_t *out, uint32_t cap,
                             uint32_t sample_rate_hz, int32_t volume_pct)
{
    uint32_t total = pattern_total_samples(p, sample_rate_hz);
    if (total > cap) total = cap;
    memset(out, 0, (size_t)total * sizeof(int16_t));

    uint32_t sweep_n = (sample_rate_hz * p->sweep.sweep_ms) / 1000u;
    if (sweep_n == 0u) return total;
    if (sweep_n > total) sweep_n = total;

    const float two_pi = 6.28318530718f;
    float f0  = (float)p->sweep.start_hz;
    float f1  = (float)p->sweep.end_hz;
    float dur = (float)sweep_n / (float)sample_rate_hz;
    float k   = (f1 - f0) / dur;
    uint32_t fade_n = (sample_rate_hz * 5u) / 1000u;    /* 5 ms */

    for (uint32_t i = 0; i < sweep_n; i++) {
        float t = (float)i / (float)sample_rate_hz;
        float phase = two_pi * (f0 * t + 0.5f * k * t * t);
        float env = 1.0f;
        if (fade_n > 0u) {
            if (i < fade_n) {
                env = 0.5f * (1.0f - cosf(3.14159265f * (float)i / (float)fade_n));
            } else if (i + fade_n > sweep_n) {
                env = 0.5f * (1.0f - cosf(3.14159265f * (float)(sweep_n - i) / (float)fade_n));
            }
        }
        float s = 0.6f * env * sinf(phase);
        out[i] = scale_sample(s, volume_pct);
    }
    return total;
}

/* xorshift32 — fast statistically-good PRNG. Sufficient for white-noise
 * excitation; not cryptographic. Seeded per call from the pattern address
 * so successive emissions of the same pattern give different waveforms. */
static uint32_t xorshift32(uint32_t *s)
{
    uint32_t x = *s;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    *s = x;
    return x;
}

static uint32_t render_noise(const pattern_t *p, int16_t *out, uint32_t cap,
                             uint32_t sample_rate_hz, int32_t volume_pct)
{
    uint32_t total = pattern_total_samples(p, sample_rate_hz);
    if (total > cap) total = cap;
    if (total == 0u) return 0u;

    /* User-set scaling: g_volume_pct × pattern's own volume_pct. */
    float user_v = (float)volume_pct / 100.0f;
    float pat_v  = (float)p->noise.volume_pct / 100.0f;
    if (user_v < 0.0f) user_v = 0.0f;
    if (user_v > 1.0f) user_v = 1.0f;
    if (pat_v  < 0.0f) pat_v  = 0.0f;
    if (pat_v  > 1.0f) pat_v  = 1.0f;
    float v = user_v * pat_v;

    /* Seed: mix pattern pointer + duration so repeated emissions of the
     * same entry produce different noise but deterministic per build. */
    uint32_t seed = (uint32_t)((uintptr_t)p ^ p->noise.duration_ms);
    if (seed == 0u) seed = 0xC0FFEEu;

    if (p->noise.shape == PATTERN_NOISE_SHAPE_PRBS) {
        /* ±1 binary noise, scaled to 30000 × v. crest factor 0 dB. */
        int16_t hi = (int16_t)(30000.0f * v);
        int16_t lo = (int16_t)(-30000.0f * v);
        for (uint32_t i = 0; i < total; i++) {
            out[i] = (xorshift32(&seed) & 0x80000000u) ? hi : lo;
        }
    } else {
        /* Uniform int16 noise scaled by v. crest factor ~4.8 dB. */
        for (uint32_t i = 0; i < total; i++) {
            int32_t r = (int32_t)(xorshift32(&seed) >> 16);  /* 0..65535 */
            r -= 32768;                                       /* -32768..32767 */
            float s = (float)r * v;
            if (s >  32767.0f) s =  32767.0f;
            if (s < -32768.0f) s = -32768.0f;
            out[i] = (int16_t)s;
        }
    }
    return total;
}

uint32_t pattern_render(const pattern_t *p,
                        int16_t *out, uint32_t out_capacity,
                        uint32_t sample_rate_hz, int32_t volume_pct)
{
    if (p == NULL || out == NULL || out_capacity == 0u) return 0u;
    if (p->kind == PATTERN_KIND_PULSE) return render_pulse(p, out, out_capacity, sample_rate_hz, volume_pct);
    if (p->kind == PATTERN_KIND_SWEEP) return render_sweep(p, out, out_capacity, sample_rate_hz, volume_pct);
    if (p->kind == PATTERN_KIND_NOISE) return render_noise(p, out, out_capacity, sample_rate_hz, volume_pct);
    return 0u;
}
