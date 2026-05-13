/*
 * Copyright 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * 01_dummy_emitter: only the debug LPUART (FlexComm 4) is attached.
 */

#include "pin_mux.h"
#include "clock_config.h"
#include "board.h"

void BOARD_InitHardware(void)
{
    /* FRO 12 MHz → FLEXCOMM4 (OpenSDA debug UART) */
    CLOCK_SetClkDiv(kCLOCK_DivFlexcom4Clk, 1u);
    CLOCK_AttachClk(BOARD_DEBUG_UART_CLK_ATTACH);

    BOARD_InitBootPins();
    BOARD_InitBootClocks();
    BOARD_InitDebugConsole();
}
