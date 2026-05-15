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
#include "fsl_clock.h"
#include "fsl_debug_console.h"

status_t sai_speaker_init(sai_speaker_t *spk, const sai_speaker_config_t *cfg)
{
    if (!spk || !cfg) return kStatus_InvalidArgument;
    spk->cfg = *cfg;

    I2S_Type *base = (I2S_Type *)spk->cfg.sai_base;

    /* Enable SAI peripheral clock + release reset. Safe to call twice
     * (08_mic_speaker_test inits both mic and speaker on the same SAI). */
    SAI_Init(base);
    SAI_TxReset(base);

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
    /* Match the I²S Philips frame the MAX98357A expects (data MSB lands on
     * the first BCLK after FS edge, FS active low). Same framing used by
     * 06_mic_test so 08_mic_speaker_test shares the same TX framer. */
    saiConfig.frameSync.frameSyncEarly           = true;
    saiConfig.frameSync.frameSyncPolarity        = kSAI_PolarityActiveLow;
    saiConfig.frameSync.frameSyncGenerateOnDemand = false;
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

    /* Force TCR4.FCONT (Frame Continue) so the framer keeps emitting BCLK/FS
     * even when the TX FIFO underflows. Without this, on this Kinetis-derived
     * SAI IP the framer halts on the first underflow and BCLK stops — the pin
     * shows only stray coupling on a scope (~1 V mid-rail "fuzz") and no
     * audio reaches the MAX98357A. SDK's SAI_TxSetConfig does not expose
     * this bit through sai_transceiver_t so we set it manually. */
    base->TCR4 |= (1u << 28);   /* TCR4.FCONT */

    /* Prime TX FIFO with zeros BEFORE enabling the framer. Some SAI IPs
     * refuse to clock BCLK/FS until at least one word is queued. The MCXN947
     * SAI TX FIFO is 8 entries deep. SAI_WriteData is the raw register write
     * (no flow control) so this never blocks at init time. */
    for (int i = 0; i < 8; i++) {
        SAI_WriteData(base, /* channel */ 0u, 0u);
    }

    /* Start the framer now so BCLK/FS run continuously from init onward.
     * MAX98357A auto-detects Fs from BCLK ratio and starts unmuted within
     * a few ms — keeping clocks live across play calls avoids re-mute pops. */
    SAI_TxEnable(base, true);

    /* Diagnostic dump so the user can compare against the expected values
     * in 06_mic_test/BRINGUP_NOTES.md. If TCSR=0x9017xxxx, TCR4 bit28 set,
     * MCR bit30 set, the SAI half is healthy and any silence is on the
     * MAX98357A wiring side (VIN/SD/GAIN/speaker). */
    PRINTF("[SAI] sai_clk=%u Hz TCSR=0x%08x TCR2=0x%08x TCR4=0x%08x MCR=0x%08x\r\n",
           (unsigned)spk->cfg.sai_clk_hz,
           (unsigned)base->TCSR,
           (unsigned)base->TCR2,
           (unsigned)base->TCR4,
           (unsigned)base->MCR);

    spk->initialised = 1;
    return kStatus_Success;
}

status_t sai_speaker_play_blocking(sai_speaker_t *spk,
                                   const int16_t *samples, size_t n_samples)
{
    if (!spk || !spk->initialised || !samples) return kStatus_InvalidArgument;

    I2S_Type *base = (I2S_Type *)spk->cfg.sai_base;
    /* Framer was enabled in sai_speaker_init() and stays running (FCONT) so
     * BCLK/FS are already active. Do not toggle SAI_TxEnable here — that
     * would re-trigger MAX98357A's mute/unmute sequence and pop audibly. */

    /* With kSAI_MonoLeft the FIFO consumes one 32-bit word per left-channel
     * sample (the framer mirrors / silences the right half-frame
     * automatically). Pack each int16 into the upper half of a 32-bit slot
     * so MAX98357A picks it up as the audio sample.
     *
     * NOTE: do NOT use SAI_WriteBlocking() with a single uint32_t — its
     * internal burst length is (FIFO_DEPTH - watermark) * (bitWidth/8),
     * typically 28 bytes, so it would read 24 bytes of stack garbage past
     * the 4-byte local. Wait for FIFO room (FRF = below watermark) and
     * write exactly one word per sample with SAI_WriteData(). */
    for (size_t i = 0; i < n_samples; i++) {
        while (!(SAI_TxGetStatusFlag(base) & kSAI_FIFORequestFlag)) { /* spin */ }
        SAI_WriteData(base, 0u, ((uint32_t)(int32_t)samples[i]) << 16);
    }

    /* Flush the FIFO so the last few samples actually clock out before this
     * call returns. We *do not* SAI_TxEnable(false) here: in 08_mic_speaker_test
     * the mic uses RX-sync mode and depends on TX-side BCLK/FS continuing to
     * run for sai_mic_record_blocking to pick up samples. The TX framer
     * outputs zeros (FIFO underflow) between play calls; that just yields
     * silence on the speaker, which is the desired idle state. Explicit
     * shutdown is done via sai_speaker_stop(). */
    /* TCSR[FWF] is set when the TX FIFO has drained; the MCXN947 SAI driver
     * exposes it as kSAI_FIFOWarningFlag (no separate "FIFO empty" flag). */
    while (!(SAI_TxGetStatusFlag(base) & kSAI_FIFOWarningFlag)) { __NOP(); }
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
