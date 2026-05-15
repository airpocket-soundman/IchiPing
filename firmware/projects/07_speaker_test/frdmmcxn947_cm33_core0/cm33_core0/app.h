/*
 * Copyright 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * 07_speaker_test — MAX98357A on SAI1 TX (PIO3_16/17/20, Alt10).
 *
 * Use SAI1 (not SAI0). SAI1 is the audio peripheral the FRDM-MCXN947
 * SDK examples target; SAI0 has different pin alts and is not exposed
 * conveniently on the board headers.
 *
 * Clock source: FRO HF (48 MHz). Matches the SDK reference example
 * sai/edma_transfer for FRDM-MCXN947. No audio PLL is required for the
 * 16 kHz / 32×Fs / 16-bit framing MAX98357A wants — the SAI bit-clock
 * divider trims 48 MHz down to BCLK in firmware. (`kAUDIO_PLL_to_SAI1`
 * was a prior typo; that enum does not exist on MCXN947's fsl_clock.h.)
 */
#ifndef _APP_H_
#define _APP_H_

#define BOARD_SPK_SAI_BASE       SAI1     /* MCXN947 peripheral instance (typed as I2S_Type *) */
#define BOARD_SPK_SAI_CLK_ATTACH kFRO_HF_to_SAI1
#define BOARD_SPK_SAI_CLK_DIV    kCLOCK_DivSai1Clk
#define BOARD_SPK_SAI_CLK_FREQ   CLOCK_GetSaiClkFreq(1u)

#endif /* _APP_H_ */
