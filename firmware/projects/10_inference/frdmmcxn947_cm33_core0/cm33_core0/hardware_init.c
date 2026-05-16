/*
 * Copyright 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * 10_inference: attaches FC4 (debug UART) + FC1 (LPSPI1 TFT) + audio PLL
 * → SAI1. No I²C bus (no servos).
 */

#include "pin_mux.h"
#include "clock_config.h"
#include "board.h"
#include "app.h"

void BOARD_InitHardware(void)
{
    CLOCK_SetClkDiv(kCLOCK_DivFlexcom4Clk, 1u);
    CLOCK_AttachClk(BOARD_DEBUG_UART_CLK_ATTACH);

    CLOCK_SetClkDiv(BOARD_ILI_SPI_CLK_DIV, 1u);
    CLOCK_AttachClk(BOARD_ILI_SPI_CLK_ATTACH);

    CLOCK_SetClkDiv(BOARD_SAI_CLK_DIV, 1u);
    CLOCK_AttachClk(BOARD_SAI_CLK_ATTACH);

    BOARD_InitBootPins();
    BOARD_InitBootClocks();
    BOARD_InitDebugConsole();
}
