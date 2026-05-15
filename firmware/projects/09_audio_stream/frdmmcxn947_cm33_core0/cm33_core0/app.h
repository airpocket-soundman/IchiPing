/*
 * Copyright 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * 09_audio_stream — INMP441 on SAI1 RX (PIO3_16/17/21, Alt10) + OpenSDA UART
 * binary stream out at 921600 bps. Same SAI1 routing as 06_mic_test.
 *
 * Clock source: FRO HF (48 MHz). `kAUDIO_PLL_to_SAI1` is not a defined
 * enum on MCXN947's fsl_clock.h — see 06_mic_test/app.h for the full
 * rationale. The TX framer is master (drives BCLK/FS on P3_16/P3_17) and
 * the RX framer runs in kSAI_ModeSync to share those clocks.
 */
#ifndef _APP_H_
#define _APP_H_

#define BOARD_MIC_SAI_BASE       SAI1                 /* MCXN947 peripheral instance (typed as I2S_Type *) */
#define BOARD_MIC_SAI_CLK_ATTACH kFRO_HF_to_SAI1
#define BOARD_MIC_SAI_CLK_DIV    kCLOCK_DivSai1Clk
#define BOARD_MIC_SAI_CLK_FREQ   CLOCK_GetSaiClkFreq(1u)

#endif /* _APP_H_ */
