/*
 * IchiPing — in-RAM excitation pattern library.
 *
 * Mirrors pc/patterns.yaml on the MCU side. The PC client parses the YAML
 * at startup, pushes each entry via PAT_* commands, and references them
 * later by index for EMIT (one-shot test) or RUN (record N frames).
 *
 * Two pattern kinds:
 *   PULSE — a list of {freq_hz, on_ms, off_ms} tones, optionally repeated.
 *           Total duration = sum(on+off across tones) × repeat.
 *   SWEEP — a linear chirp from start_hz to end_hz over sweep_ms, then
 *           silence_ms of trailing silence.
 *           Total duration = sweep_ms + silence_ms.
 *
 * The recording window in RUN equals the pattern's total duration (variable
 * per pattern). PC ↔ MCU agree on this implicitly: the firmware writes
 * n_samples into each ICHP frame header from pattern_total_samples().
 */

#ifndef PATTERN_LIB_H_
#define PATTERN_LIB_H_

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

#define PATTERN_LIB_MAX_PATTERNS   16u
#define PATTERN_MAX_TONES          64u
#define PATTERN_NAME_LEN           32u

typedef enum {
    PATTERN_KIND_NONE  = 0,
    PATTERN_KIND_PULSE = 1,
    PATTERN_KIND_SWEEP = 2,
    PATTERN_KIND_NOISE = 3,
} pattern_kind_t;

/* Noise shape selector for PATTERN_KIND_NOISE. */
typedef enum {
    PATTERN_NOISE_SHAPE_PRBS    = 0,  /* ±1 binary, crest factor 0 dB (default) */
    PATTERN_NOISE_SHAPE_UNIFORM = 1,  /* uniform int16, crest ~4.8 dB           */
} pattern_noise_shape_t;

typedef struct {
    uint32_t freq_hz;
    uint32_t on_ms;
    uint32_t off_ms;
} pulse_tone_t;

typedef struct {
    pattern_kind_t kind;
    char           name[PATTERN_NAME_LEN];
    union {
        struct {
            uint8_t      n_tones;
            uint8_t      repeat;
            pulse_tone_t tones[PATTERN_MAX_TONES];
        } pulse;
        struct {
            uint32_t start_hz;
            uint32_t end_hz;
            uint32_t sweep_ms;
            uint32_t silence_ms;
        } sweep;
        struct {
            uint32_t duration_ms;
            uint16_t volume_pct;    /* 0..100, amplitude scaling           */
            uint8_t  shape;         /* pattern_noise_shape_t                */
            uint8_t  _pad;
        } noise;
    };
} pattern_t;

typedef struct {
    uint8_t   count;
    uint8_t   selected;     /* RUN uses this index */
    pattern_t entries[PATTERN_LIB_MAX_PATTERNS];

    /* Builder state for PAT PULSE BEGIN ... TONE ... PULSE END. */
    bool         building;
    char         build_name[PATTERN_NAME_LEN];
    uint8_t      build_n_tones;
    pulse_tone_t build_tones[PATTERN_MAX_TONES];
} pattern_lib_t;

/* Global library instance — single source of truth. */
extern pattern_lib_t g_pattern_lib;

void pattern_lib_init(void);
void pattern_lib_clear(void);

/* Pulse builder. begin -> add_tone* -> end. Returns true on success. */
bool pattern_lib_pulse_begin(const char *name);
bool pattern_lib_pulse_add_tone(uint32_t freq_hz, uint32_t on_ms, uint32_t off_ms);
bool pattern_lib_pulse_end(uint8_t repeat);

/* Sweep is atomic — all params at once. */
bool pattern_lib_add_sweep(const char *name,
                           uint32_t start_hz, uint32_t end_hz,
                           uint32_t sweep_ms, uint32_t silence_ms);

/* Noise pattern. shape uses pattern_noise_shape_t. Atomic add. */
bool pattern_lib_add_noise(const char *name,
                           uint32_t duration_ms,
                           uint16_t volume_pct,
                           uint8_t  shape);

bool             pattern_lib_select(uint8_t index);
const pattern_t *pattern_lib_get(uint8_t index);

/* Number of int16 PCM samples needed to render the pattern at sample_rate_hz.
 * Returns 0 for PATTERN_KIND_NONE. */
uint32_t pattern_total_samples(const pattern_t *p, uint32_t sample_rate_hz);

/* Render PCM into `out` (capacity = out_capacity samples).
 * volume_pct in 0..100 scales the amplitude.
 * Returns the number of samples actually written (= min(total_samples, capacity)). */
uint32_t pattern_render(const pattern_t *p,
                        int16_t *out, uint32_t out_capacity,
                        uint32_t sample_rate_hz, int32_t volume_pct);

#ifdef __cplusplus
}
#endif

#endif /* PATTERN_LIB_H_ */
