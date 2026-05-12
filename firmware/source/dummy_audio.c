#include "dummy_audio.h"
#include <math.h>

static uint32_t s_rng_state = 0xA5A5A5A5u;

static inline uint32_t xorshift32(void) {
    uint32_t x = s_rng_state;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    s_rng_state = x;
    return x;
}

void dummy_audio_seed(uint32_t seed) {
    s_rng_state = (seed != 0u) ? seed : 0xA5A5A5A5u;
}

void dummy_audio_generate(int16_t *out, size_t n_samples, uint16_t rate_hz) {
    const float fs        = (float)rate_hz;
    const float chirp_dur = 0.30f;
    const float f0        = 200.0f;
    const float f1        = 8000.0f;
    const float amp       = 0.45f * 32767.0f;
    const float tau       = 0.60f;
    const float two_pi    = 6.28318530718f;

    size_t n_chirp = (size_t)(chirp_dur * fs);
    if (n_chirp > n_samples) n_chirp = n_samples;

    for (size_t i = 0; i < n_chirp; i++) {
        float t = (float)i / fs;
        float phase = two_pi * (f0 * t + 0.5f * (f1 - f0) * t * t / chirp_dur);
        out[i] = (int16_t)(amp * sinf(phase));
    }

    for (size_t i = n_chirp; i < n_samples; i++) {
        float t = (float)(i - n_chirp) / fs;
        float env = expf(-t / tau);
        float noise = ((float)xorshift32() / (float)0xFFFFFFFFu) * 2.0f - 1.0f;
        out[i] = (int16_t)(amp * env * noise);
    }
}
