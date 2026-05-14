/*
 * Copyright 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * 06_mic_test — INMP441 on SAI1 RX (PIO3_16/17/21, Alt10) + OpenSDA UART.
 *
 * Clock source: FRO HF (48 MHz). Matches the SDK reference example
 * sai/edma_transfer for FRDM-MCXN947. No audio PLL needed — the SAI
 * bit-clock divider trims 48 MHz down to BCLK inside the driver.
 * (`kAUDIO_PLL_to_SAI1` was a prior typo; that enum does not exist on
 * MCXN947's fsl_clock.h.)
 */
#ifndef _APP_H_
#define _APP_H_

#define BOARD_MIC_SAI_BASE       I2S1                 /* SAI1 in fsl_sai.h */
#define BOARD_MIC_SAI_CLK_ATTACH kFRO_HF_to_SAI1
#define BOARD_MIC_SAI_CLK_DIV    kCLOCK_DivSai1Clk
#define BOARD_MIC_SAI_CLK_FREQ   CLOCK_GetSaiClkFreq(1u)

#endif /* _APP_H_ */
