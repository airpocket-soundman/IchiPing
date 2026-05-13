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
} sai_mic_t;

status_t sai_mic_init(sai_mic_t *mic, const sai_mic_config_t *cfg);

/* Block until n_samples mono int16 samples are captured. */
status_t sai_mic_record_blocking(sai_mic_t *mic, int16_t *out, size_t n_samples);

/* EDMA-based continuous capture. Caller provides a ring buffer; the driver
 * delivers a callback (or sets a flag) when each half fills. The
 * implementation details live in sai_mic.c. */
status_t sai_mic_start_streaming(sai_mic_t *mic, int16_t *ring, size_t ring_samples);
status_t sai_mic_stop(sai_mic_t *mic);

#ifdef __cplusplus
}
#endif
#endif /* ICHIPING_SAI_MIC_H_ */
