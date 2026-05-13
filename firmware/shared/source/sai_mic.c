/*
 * INMP441 SAI RX driver — see sai_mic.h.
 *
 * This is a skeleton that captures the MCUXpresso fsl_sai driver pattern.
 * Concrete register-level numbers (clock divider, slot length, sync mode)
 * must be verified against your SDK version and the FRDM-MCXN947 SAI mux.
 *
 * The INMP441 produces:
 *   - 24-bit signed sample left-justified in a 32-bit I²S slot
 *   - Stereo bus, but L/R pin to GND selects the left channel only
 *   - Standard Philips I²S framing
 *
 * Drop the 8 LSBs and the top 8 sign bits to land in int16 range (i.e.
 * shift the 32-bit slot right by 8 then truncate to int16).
 */

#include "sai_mic.h"
#include "fsl_sai.h"

#include <string.h>

/* Internal: convert one 32-bit SAI word from INMP441 to int16. */
static inline int16_t inmp441_word_to_int16(uint32_t w)
{
    int32_t s = (int32_t)w;          /* sign-extend the 32-bit container */
    s >>= 16;                         /* keep the top 16 bits of the 24-bit MSB */
    if (s > INT16_MAX) s = INT16_MAX;
    if (s < INT16_MIN) s = INT16_MIN;
    return (int16_t)s;
}

status_t sai_mic_init(sai_mic_t *mic, const sai_mic_config_t *cfg)
{
    if (!mic || !cfg) return kStatus_InvalidArgument;
    mic->cfg = *cfg;

    sai_transceiver_t saiConfig;
    SAI_GetClassicI2SConfig(&saiConfig,
                            kSAI_WordWidth16bits,
                            kSAI_MonoLeft,
                            kSAI_Channel0Mask);
    saiConfig.syncMode    = kSAI_ModeAsync;
    saiConfig.masterSlave = kSAI_Master;
    SAI_RxSetConfig((I2S_Type *)mic->cfg.sai_base, &saiConfig);

    SAI_RxSetBitClockRate((I2S_Type *)mic->cfg.sai_base,
                          mic->cfg.sai_clk_hz,
                          mic->cfg.sample_rate_hz,
                          /* bit width */ 32U,
                          /* channels  */ 2U);

    mic->initialised = 1;
    return kStatus_Success;
}

status_t sai_mic_record_blocking(sai_mic_t *mic, int16_t *out, size_t n_samples)
{
    if (!mic || !mic->initialised || !out) return kStatus_InvalidArgument;

    I2S_Type *base = (I2S_Type *)mic->cfg.sai_base;
    SAI_RxEnable(base, true);

    /* INMP441 emits 32-bit slots; we receive into a stack-temp 32-bit buffer
     * one stereo pair at a time, then keep the left word. */
    for (size_t i = 0; i < n_samples; i++) {
        uint32_t stereo[2];
        sai_transfer_t xfer = {
            .data     = (uint8_t *)stereo,
            .dataSize = sizeof(stereo),
        };
        status_t s = SAI_TransferReceiveBlocking(base, &xfer);
        if (s != kStatus_Success) {
            SAI_RxEnable(base, false);
            return s;
        }
        out[i] = inmp441_word_to_int16(stereo[0]);
    }

    SAI_RxEnable(base, false);
    return kStatus_Success;
}

status_t sai_mic_start_streaming(sai_mic_t *mic, int16_t *ring, size_t ring_samples)
{
    /* EDMA streaming is implementation-heavy; this is a placeholder that
     * lets the project build. Add fsl_sai_edma.h based ring transfer here
     * when 09_audio_stream / 10_collector graduate from blocking I/O. */
    (void)mic; (void)ring; (void)ring_samples;
    return kStatus_Fail;
}

status_t sai_mic_stop(sai_mic_t *mic)
{
    if (!mic || !mic->initialised) return kStatus_InvalidArgument;
    SAI_RxEnable((I2S_Type *)mic->cfg.sai_base, false);
    return kStatus_Success;
}
