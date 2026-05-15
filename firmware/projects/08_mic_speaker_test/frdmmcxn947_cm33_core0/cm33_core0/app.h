/*
 * Copyright 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * 08_mic_speaker_test — SAI1 full-duplex (PIO3_16/17/20/21, Alt10).
 *
 * Both INMP441 (RX) and MAX98357A (TX) share the same SAI1 BCLK/FS,
 * which means sample clocks are inherently aligned — required for
 * meaningful impulse-response capture.
 */
#ifndef _APP_H_
#define _APP_H_

#define BOARD_SAI_BASE       SAI1   /* MCXN947 peripheral instance (typed as I2S_Type *) */
#define BOARD_SAI_CLK_ATTACH kFRO_HF_to_SAI1   /* 48 MHz FRO, divided inside SAI driver */
#define BOARD_SAI_CLK_DIV    kCLOCK_DivSai1Clk
#define BOARD_SAI_CLK_FREQ   CLOCK_GetSaiClkFreq(1u)

/* Backwards-compatible aliases for the unchanged main.c code. */
#define BOARD_MIC_SAI_BASE       BOARD_SAI_BASE
#define BOARD_MIC_SAI_CLK_ATTACH BOARD_SAI_CLK_ATTACH
#define BOARD_MIC_SAI_CLK_DIV    BOARD_SAI_CLK_DIV
#define BOARD_MIC_SAI_CLK_FREQ   BOARD_SAI_CLK_FREQ
#define BOARD_SPK_SAI_BASE       BOARD_SAI_BASE
#define BOARD_SPK_SAI_CLK_ATTACH BOARD_SAI_CLK_ATTACH
#define BOARD_SPK_SAI_CLK_DIV    BOARD_SAI_CLK_DIV
#define BOARD_SPK_SAI_CLK_FREQ   BOARD_SAI_CLK_FREQ

#endif /* _APP_H_ */
