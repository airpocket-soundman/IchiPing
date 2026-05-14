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

    I2S_Type *base = (I2S_Type *)mic->cfg.sai_base;

    /* TX framer drives BCLK + FS on the SAI1_TX_BCLK / SAI1_TX_FS pins
     * (PIO3_16 / PIO3_17 in our pin_mux). We never feed data into TXFIFO
     * — only the clocks are needed so INMP441 has something to latch on.
     * The shared-master pattern matches 07_speaker_test and 08_mic_speaker_test
     * so the same wiring works across all three projects. */
    sai_transceiver_t txConfig;
    SAI_GetClassicI2SConfig(&txConfig,
                            kSAI_WordWidth16bits,
                            kSAI_MonoLeft,
                            kSAI_Channel0Mask);
    txConfig.syncMode    = kSAI_ModeAsync;
    txConfig.masterSlave = kSAI_Master;
    SAI_TxSetConfig(base, &txConfig);
    SAI_TxSetBitClockRate(base,
                          mic->cfg.sai_clk_hz,
                          mic->cfg.sample_rate_hz,
                          /* bit width */ 32U,
                          /* channels  */ 2U);

    /* RX framer in sync mode picks BCLK + FS from the TX framer internally
     * — we don't need SAI1_RX_BCLK / SAI1_RX_FS pins muxed at all. INMP441's
     * data line comes in on SAI1_RXD0 (PIO3_21). */
    sai_transceiver_t rxConfig;
    SAI_GetClassicI2SConfig(&rxConfig,
                            kSAI_WordWidth16bits,
                            kSAI_MonoLeft,
                            kSAI_Channel0Mask);
    rxConfig.syncMode    = kSAI_ModeSync;
    rxConfig.masterSlave = kSAI_Slave;
    SAI_RxSetConfig(base, &rxConfig);

    /* Start the TX framer so BCLK / FS run continuously. RX is then enabled
     * per-capture inside sai_mic_record_blocking(). */
    SAI_TxEnable(base, true);

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
