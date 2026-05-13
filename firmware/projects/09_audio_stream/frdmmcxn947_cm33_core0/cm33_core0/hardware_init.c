/*
 * Copyright 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * 06_mic_test: FlexComm 4 (debug UART) + SAI1 (INMP441 RX).
 */

#include "pin_mux.h"
#include "clock_config.h"
#include "board.h"
#include "app.h"

void BOARD_InitHardware(void)
{
    CLOCK_SetClkDiv(kCLOCK_DivFlexcom4Clk, 1u);
    CLOCK_AttachClk(BOARD_DEBUG_UART_CLK_ATTACH);

    /* SAI1 master clock — exact source name depends on the SDK version.
     * BOARD_MIC_SAI_CLK_ATTACH should resolve to a sufficiently fast PLL
     * tap (typically PLL0_OUT0 or FRO_HF divided down). */
    CLOCK_SetClkDiv(BOARD_MIC_SAI_CLK_DIV, 1u);
    CLOCK_AttachClk(BOARD_MIC_SAI_CLK_ATTACH);

    BOARD_InitBootPins();
    BOARD_InitBootClocks();
    BOARD_InitDebugConsole();
}
