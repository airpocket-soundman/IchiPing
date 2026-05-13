/*
 * Copyright 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * 06_mic_test — SAI1 RX from INMP441 + OpenSDA debug UART.
 */
#ifndef _APP_H_
#define _APP_H_

#define BOARD_MIC_SAI_BASE       I2S1            /* SAI1 — verify in SDK header */
#define BOARD_MIC_SAI_CLK_ATTACH kFRO_HF_to_SAI1
#define BOARD_MIC_SAI_CLK_DIV    kCLOCK_DivSai1Clk
#define BOARD_MIC_SAI_CLK_FREQ   CLOCK_GetSaiClkFreq(1u)

#endif /* _APP_H_ */
