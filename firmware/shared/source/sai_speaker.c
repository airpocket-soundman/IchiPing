/*
 * MAX98357A SAI TX driver — see sai_speaker.h.
 *
 * MAX98357A specifics:
 *   - I²S Philips framing, 16/24/32-bit, no MCLK required
 *   - Auto-detects Fs from BCLK ratio (typical: 32×Fs or 64×Fs)
 *   - SD pin tri-state selects gain (default 9 dB when HiZ / floating)
 *   - GAIN pin gives 3 dB / 6 dB / 9 dB / 12 dB / 15 dB options
 *
 * We always send a 32-bit slot with the 16-bit sample left-shifted by 16
 * into the MSBs (right-zero-pad). The amp ignores the LSBs.
 */

#include "sai_speaker.h"
#include "fsl_sai.h"

status_t sai_speaker_init(sai_speaker_t *spk, const sai_speaker_config_t *cfg)
{
    if (!spk || !cfg) return kStatus_InvalidArgument;
    spk->cfg = *cfg;

    I2S_Type *base = (I2S_Type *)spk->cfg.sai_base;

    /* Enable SAI peripheral clock + release reset. Safe to call twice
     * (08_mic_speaker_test inits both mic and speaker on the same SAI). */
    SAI_Init(base);

    /* WordWidth = 32 bits to match the bit-clock divider (Fs × 32 × 2).
     * MAX98357A samples 32-bit slots and uses the upper 16 bits as the
     * audio sample. MonoLeft sends our buffer to the left half-frame only;
     * the amp picks it up on its only output channel. */
    sai_transceiver_t saiConfig;
    SAI_GetClassicI2SConfig(&saiConfig,
                            kSAI_WordWidth32bits,
                            kSAI_MonoLeft,
                            kSAI_Channel0Mask);
    saiConfig.syncMode    = kSAI_ModeAsync;
    saiConfig.masterSlave = kSAI_Master;
    SAI_TxSetConfig(base, &saiConfig);

    SAI_TxSetBitClockRate(base,
                          spk->cfg.sai_clk_hz,
                          spk->cfg.sample_rate_hz,
                          /* bit width */ 32U,
                          /* channels  */ 2U);

    /* Enable the internal MCLK divider (MCR.MOE=1). Default MOE=0 puts the
     * MCLK pin in input mode and the BCLK divider's MSEL=01 source waits for
     * an external MCLK that we don't wire — BCLK never toggles. See sai_mic.c
     * for the full diagnosis. mclkHz == mclkSourceClkHz → 1:1 passthrough. */
    sai_master_clock_t mclk_cfg = {
        .mclkOutputEnable = true,
        .mclkHz           = spk->cfg.sai_clk_hz,
        .mclkSourceClkHz  = spk->cfg.sai_clk_hz,
    };
    SAI_SetMasterClockConfig(base, &mclk_cfg);

    spk->initialised = 1;
    return kStatus_Success;
}

status_t sai_speaker_play_blocking(sai_speaker_t *spk,
                                   const int16_t *samples, size_t n_samples)
{
    if (!spk || !spk->initialised || !samples) return kStatus_InvalidArgument;

    I2S_Type *base = (I2S_Type *)spk->cfg.sai_base;
    SAI_TxEnable(base, true);

    /* With kSAI_MonoLeft the FIFO consumes one 32-bit word per left-channel
     * sample (the framer mirrors / silences the right half-frame
     * automatically). Pack each int16 into the upper half of a 32-bit slot
     * so MAX98357A picks it up as the audio sample. */
    for (size_t i = 0; i < n_samples; i++) {
        uint32_t left = ((uint32_t)(int32_t)samples[i]) << 16;
        SAI_WriteBlocking(base,
                          /* channel */ 0u,
                          /* bitWidth */ 32u,
                          (uint8_t *)&left,
                          (uint32_t)sizeof(left));
    }

    /* Flush the FIFO so the last few samples actually clock out before this
     * call returns. We *do not* SAI_TxEnable(false) here: in 08_mic_speaker_test
     * the mic uses RX-sync mode and depends on TX-side BCLK/FS continuing to
     * run for sai_mic_record_blocking to pick up samples. The TX framer
     * outputs zeros (FIFO underflow) between play calls; that just yields
     * silence on the speaker, which is the desired idle state. Explicit
     * shutdown is done via sai_speaker_stop(). */
    while (!(SAI_TxGetStatusFlag(base) & kSAI_FIFOEmptyFlag)) { __NOP(); }
    return kStatus_Success;
}

status_t sai_speaker_start_streaming(sai_speaker_t *spk,
                                     int16_t *ring, size_t ring_samples)
{
    /* EDMA-based continuous TX placeholder. */
    (void)spk; (void)ring; (void)ring_samples;
    return kStatus_Fail;
}

status_t sai_speaker_stop(sai_speaker_t *spk)
{
    if (!spk || !spk->initialised) return kStatus_InvalidArgument;
    SAI_TxEnable((I2S_Type *)spk->cfg.sai_base, false);
    return kStatus_Success;
}
