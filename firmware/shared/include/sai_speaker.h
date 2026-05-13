/*
 * MAX98357A → SAI TX wrapper for IchiPing.
 *
 * MAX98357A is a class-D mono amp that ingests I²S at any standard sample
 * rate (8..96 kHz) and auto-derives Fs from BCLK. It accepts 16-bit Philips
 * I²S framing on a 32-bit slot, mono-mixed from the L channel by default.
 *
 * Public surface mirrors sai_mic.h: a blocking play call plus an EDMA
 * streaming pair for continuous output.
 */

#ifndef ICHIPING_SAI_SPEAKER_H_
#define ICHIPING_SAI_SPEAKER_H_

#include <stdint.h>
#include <stddef.h>

#include "fsl_common.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    void    *sai_base;
    uint32_t sai_clk_hz;
    uint32_t sample_rate_hz;
} sai_speaker_config_t;

typedef struct {
    sai_speaker_config_t cfg;
    int                   initialised;
} sai_speaker_t;

status_t sai_speaker_init(sai_speaker_t *spk, const sai_speaker_config_t *cfg);

/* Block until n_samples int16 mono PCM samples have been clocked out. */
status_t sai_speaker_play_blocking(sai_speaker_t *spk,
                                   const int16_t *samples, size_t n_samples);

status_t sai_speaker_start_streaming(sai_speaker_t *spk,
                                     int16_t *ring, size_t ring_samples);
status_t sai_speaker_stop(sai_speaker_t *spk);

#ifdef __cplusplus
}
#endif
#endif /* ICHIPING_SAI_SPEAKER_H_ */
