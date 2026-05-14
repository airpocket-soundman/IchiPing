/*
 * Copyright 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * 03_ili9341_test: attaches FlexComm 4 (debug UART) + FlexComm 1 (LPSPI1
 * for ILI9341). FC1 confirmed against the FRDM-MCXN947 BUM Table 18.
 */

#include "pin_mux.h"
#include "clock_config.h"
#include "board.h"
#include "app.h"

void BOARD_InitHardware(void)
{
    /* FRO 12 MHz → FLEXCOMM4 (OpenSDA debug UART) */
    CLOCK_SetClkDiv(kCLOCK_DivFlexcom4Clk, 1u);
    CLOCK_AttachClk(BOARD_DEBUG_UART_CLK_ATTACH);

    /* FRO 12 MHz → FLEXCOMM1 (LPSPI1 for ILI9341) */
    CLOCK_SetClkDiv(BOARD_ILI_SPI_CLK_DIV, 1u);
    CLOCK_AttachClk(BOARD_ILI_SPI_CLK_ATTACH);

    BOARD_InitBootPins();
    BOARD_InitBootClocks();
    BOARD_InitDebugConsole();
}
