/*
 * Synthetic chirp + reverb-tail generator for IchiPing PoC.
 *
 * Mimics the §3 pipeline output without real I²S MIC hardware:
 *   - 300 ms linear chirp 200 Hz -> 8 kHz
 *   - decaying white-noise tail (1/e ~ 0.6 s)
 *
 * Used until the real INMP441 capture path is wired up.
 */

#ifndef DUMMY_AUDIO_H_
#define DUMMY_AUDIO_H_

#include <stdint.h>
#include <stddef.h>

void dummy_audio_seed(uint32_t seed);

void dummy_audio_generate(int16_t *out,
                          size_t   n_samples,
                          uint16_t rate_hz);

#endif /* DUMMY_AUDIO_H_ */
