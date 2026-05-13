#ifndef _APP_H_
#define _APP_H_

#define BOARD_SPK_SAI_BASE       I2S0
#define BOARD_SPK_SAI_CLK_ATTACH kFRO_HF_to_SAI0
#define BOARD_SPK_SAI_CLK_DIV    kCLOCK_DivSai0Clk
#define BOARD_SPK_SAI_CLK_FREQ   CLOCK_GetSaiClkFreq(0u)

#endif
