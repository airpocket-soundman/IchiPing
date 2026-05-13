/*
 * Copyright 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * 06_mic_test — INMP441 on SAI1 RX (PIO3_16/17/21, Alt10) + OpenSDA UART.
 */
#ifndef _APP_H_
#define _APP_H_

#define BOARD_MIC_SAI_BASE       I2S1                 /* SAI1 in fsl_sai.h */
#define BOARD_MIC_SAI_CLK_ATTACH kAUDIO_PLL_to_SAI1   /* verify in clock_config.c */
#define BOARD_MIC_SAI_CLK_DIV    kCLOCK_DivSai1Clk
#define BOARD_MIC_SAI_CLK_FREQ   CLOCK_GetSaiClkFreq(1u)

#endif /* _APP_H_ */
