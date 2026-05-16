/*
 * INMP441 → SAI RX wrapper for IchiPing.
 *
 * Captures mono 16-bit PCM at a configurable sample rate. The INMP441 puts
 * 24-bit signed samples on the I²S bus left-aligned in a 32-bit slot; the
 * driver right-shifts down to int16 before delivering frames.
 *
 * Two transfer styles are supported:
 *   - sai_mic_record_blocking()   one-shot synchronous capture
 *   - sai_mic_start_streaming()   continuous EDMA into a ring buffer
 *
 * The MCU-side L/R pin of the INMP441 is assumed tied to GND, so only the
 * left I²S channel is captured.
 */

#ifndef ICHIPING_SAI_MIC_H_
#define ICHIPING_SAI_MIC_H_

#include <stdint.h>
#include <stddef.h>

#include "fsl_common.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    void    *sai_base;          /* I2S0..I2S7 — pointer to NXP fsl_sai I2S_Type */
    uint32_t sai_clk_hz;        /* MCLK input frequency, e.g. 24 MHz */
    uint32_t sample_rate_hz;    /* desired Fs, e.g. 16000 */
    uint8_t  bit_depth;         /* 16 only; INMP441 is internally 24, we drop LSBs */
} sai_mic_config_t;

typedef struct {
    sai_mic_config_t cfg;
    int               initialised;
    /* Right-shift applied to the raw 32-bit SAI word before truncation to
     * int16. Writable at any time between record calls to scale gain on the
     * fly (larger = quieter / more headroom). Default initialised in
     * sai_mic_init(): 12 for the MSM261S4030H0 clone (24-bit right-justified),
     * use 16 for genuine INMP441 (24-bit left-justified). */
    uint8_t           gain_shift;
} sai_mic_t;

/* Convenience accessor — equivalent to writing mic->gain_shift directly.
 * Provided so callers can adjust gain without touching the struct internals. */
static inline void sai_mic_set_gain_shift(sai_mic_t *mic, uint8_t shift)
{
    if (mic) mic->gain_shift = shift;
}

status_t sai_mic_init(sai_mic_t *mic, const sai_mic_config_t *cfg);

/* Block until n_samples mono int16 samples are captured. Uses
 * mic->gain_shift to scale the raw 32-bit FIFO word into int16. Subject to
 * saturation when audio exceeds the chosen shift window. */
status_t sai_mic_record_blocking(sai_mic_t *mic, int16_t *out, size_t n_samples);

/* Block until n_samples mono float32 samples are captured, normalised to
 * the range [-1.0f, +1.0f). The raw 32-bit FIFO word is reinterpreted as
 * int32 and divided by 2^31, so the full hardware dynamic range is
 * preserved end-to-end — no shift, no saturation knob to tune. For
 * MSM261/INMP441 24-bit audio (always within ±2^23), float32's 24-bit
 * mantissa is bit-exact; only data exceeding ±2^24 incurs precision
 * loss, which proper I²S MEMS mics do not produce. */
status_t sai_mic_record_blocking_f32(sai_mic_t *mic, float *out, size_t n_samples);

/* EDMA-based continuous capture. Caller provides a ring buffer; the driver
 * delivers a callback (or sets a flag) when each half fills. The
 * implementation details live in sai_mic.c. */
status_t sai_mic_start_streaming(sai_mic_t *mic, int16_t *ring, size_t ring_samples);
status_t sai_mic_stop(sai_mic_t *mic);

#ifdef __cplusplus
}
#endif
#endif /* ICHIPING_SAI_MIC_H_ */
