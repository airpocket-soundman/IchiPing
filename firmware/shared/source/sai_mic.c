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

    /* MANDATORY: enable SAI peripheral clock + release reset before any
     * register write. Without this, SAI_TxSetConfig / SAI_TxEnable etc.
     * silently no-op (the module clock stays gated) and the framer never
     * outputs BCLK/FS — which leaves SAI_ReadBlocking hung in
     * sai_mic_record_blocking, waiting for RX FIFO data that never comes. */
    SAI_Init(base);

    /* Soft-reset both framers so we start from a known register state even
     * if the SAI was left in an odd configuration by a previous run. */
    SAI_TxReset(base);
    SAI_RxReset(base);

    /* TX framer drives BCLK + FS on the SAI1_TX_BCLK / SAI1_TX_FS pins
     * (PIO3_16 / PIO3_17 in our pin_mux). We never feed data into TXFIFO
     * — only the clocks are needed so INMP441 has something to latch on.
     * The shared-master pattern matches 07_speaker_test and 08_mic_speaker_test
     * so the same wiring works across all three projects. */
    /* Word width = 32 bits to match the bit-clock rate (Fs × 32 × 2).
     * INMP441 outputs 24-bit data left-justified in a 32-bit slot, so the
     * framer needs to clock 32 bits per half-frame. Using 16-bit word width
     * with a 32-bit BCLK divider creates framing mismatch — the framer only
     * samples the first 16 bits and gets out of sync, leaving RX FIFO empty.
     *
     * MonoLeft + Channel0Mask: SAI only loads the left-half-frame slot into
     * FIFO. INMP441 with L/R tied to GND drives data only during the left
     * half-frame (right half is high-Z) so this matches the wire format. */
    sai_transceiver_t txConfig;
    SAI_GetClassicI2SConfig(&txConfig,
                            kSAI_WordWidth32bits,
                            kSAI_MonoLeft,
                            kSAI_Channel0Mask);
    txConfig.syncMode    = kSAI_ModeAsync;
    txConfig.masterSlave = kSAI_Master;

    /* Set the TX frame-sync direction explicitly to "output" and make the
     * frame continuous. Without frameContinue the TX framer halts after
     * FIFO underflow on some Kinetis-derived SAI IPs (the symptom we hit:
     * BCLK/FS stop running once the 8 primed zeros are consumed → RX in
     * sync mode loses its clocks → RFR0 stays at 0 forever). */
    txConfig.frameSync.frameSyncEarly       = true;
    txConfig.frameSync.frameSyncPolarity    = kSAI_PolarityActiveLow;
    txConfig.frameSync.frameSyncGenerateOnDemand = false;

    SAI_TxSetConfig(base, &txConfig);
    SAI_TxSetBitClockRate(base,
                          mic->cfg.sai_clk_hz,
                          mic->cfg.sample_rate_hz,
                          /* bit width */ 32U,
                          /* channels  */ 2U);

    /* CRITICAL: enable the internal MCLK divider (MCR.MOE=1). On MCXN947 SAI
     * the default is MOE=0 which configures the MCLK pin as INPUT — meaning
     * the BCLK source selector (TCR2.MSEL=01 "MCLK option 1") then waits for
     * an external MCLK signal from the MCLK pin. We never wire MCLK out, so
     * BCLK silently stays at 0 V and the framer never clocks anything,
     * leaving RFR0=0 and WSF=0 forever. Setting MOE=1 switches MCLK to an
     * internal source (our attached SAI peripheral clock) which is what we
     * actually want for an embedded master. mclkHz == mclkSourceClkHz makes
     * the SDK leave DIVEN=0 (1:1 passthrough; no extra divide). */
    sai_master_clock_t mclk_cfg = {
        .mclkOutputEnable = true,
        .mclkHz           = mic->cfg.sai_clk_hz,
        .mclkSourceClkHz  = mic->cfg.sai_clk_hz,
    };
    SAI_SetMasterClockConfig(base, &mclk_cfg);

    /* Force TCR4.FCONT (Frame Continue) so the TX framer keeps running
     * even when the FIFO is empty (it will repeat the last sample / zero).
     * That keeps BCLK + FS alive for the RX-in-sync framer regardless of
     * whether we feed TX FIFO. SDK's SAI_TxSetConfig does not expose
     * this bit through sai_transceiver_t, so we set it manually. */
    base->TCR4 |= (1u << 28);   /* TCR4.FCONT */

    /* RX framer in sync mode picks BCLK + FS from the TX framer internally
     * — we don't need SAI1_RX_BCLK / SAI1_RX_FS pins muxed at all. INMP441's
     * data line comes in on SAI1_RXD0 (PIO3_21). */
    sai_transceiver_t rxConfig;
    SAI_GetClassicI2SConfig(&rxConfig,
                            kSAI_WordWidth32bits,
                            kSAI_MonoLeft,
                            kSAI_Channel0Mask);
    rxConfig.syncMode    = kSAI_ModeSync;
    rxConfig.masterSlave = kSAI_Slave;
    SAI_RxSetConfig(base, &rxConfig);

    /* Prime the TX FIFO with a few zero stereo words BEFORE enabling the
     * framer. Some SAI implementations refuse to start clocking BCLK/FS
     * until there is at least one word in TX FIFO — without this primer
     * the framer can stay silent and RX-in-sync-mode never receives data.
     * We use SAI_WriteData (raw FIFO write, no flow control) so this does
     * not block. The TX FIFO is 8 entries deep on MCXN947's SAI. */
    for (int i = 0; i < 8; i++) {
        SAI_WriteData(base, /* channel */ 0u, 0u);
    }

    /* Now start the TX framer so BCLK / FS run continuously. The primed
     * zeros will be clocked out (silent on the speaker, which is the
     * desired idle state). RX is enabled per-capture inside
     * sai_mic_record_blocking(). */
    SAI_TxEnable(base, true);

    mic->initialised = 1;
    return kStatus_Success;
}

status_t sai_mic_record_blocking(sai_mic_t *mic, int16_t *out, size_t n_samples)
{
    if (!mic || !mic->initialised || !out) return kStatus_InvalidArgument;

    I2S_Type *base = (I2S_Type *)mic->cfg.sai_base;
    SAI_RxEnable(base, true);

    /* With kSAI_MonoLeft, only the left half-frame data lands in the RX
     * FIFO — one 32-bit word per sample. SAI_ReadBlocking reads `size`
     * bytes from the FIFO on the given data channel, polling until they
     * arrive. We pull 4 bytes (= 1 word = 1 mono left sample) per loop. */
    for (size_t i = 0; i < n_samples; i++) {
        uint32_t left;
        SAI_ReadBlocking(base,
                         /* channel */ 0u,
                         /* bitWidth */ 32u,
                         (uint8_t *)&left,
                         (uint32_t)sizeof(left));
        out[i] = inmp441_word_to_int16(left);
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
