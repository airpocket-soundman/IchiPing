/*
 * Copyright 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * 10_collector — SAI1 full-duplex (PIO3_16/17/20/21, Alt10).
 *
 * Identical SAI1 layout to 08_mic_speaker_test. The collector firmware
 * adds the PC-bidirectional ASCII command channel on top, but the audio
 * data path (chirp out, mic capture, ICHP framing) is the same.
 *
 * Clock source: FRO HF (48 MHz). `kAUDIO_PLL_to_SAI1` is not a defined
 * enum on MCXN947 — see 06_mic_test/app.h for the rationale.
 */
#ifndef _APP_H_
#define _APP_H_

#define BOARD_SAI_BASE       I2S1
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
