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
 */
#ifndef _APP_H_
#define _APP_H_

#define BOARD_SPK_SAI_BASE       I2S1
#define BOARD_SPK_SAI_CLK_ATTACH kAUDIO_PLL_to_SAI1
#define BOARD_SPK_SAI_CLK_DIV    kCLOCK_DivSai1Clk
#define BOARD_SPK_SAI_CLK_FREQ   CLOCK_GetSaiClkFreq(1u)

#endif /* _APP_H_ */
