/*
 * Copyright 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * 09_collector: attaches all four FlexComm clocks needed by the merged
 * peripheral set — FC4 (debug UART), FC2 (LPI2C2 servos), FC1 (LPSPI1
 * TFT), plus the audio PLL → SAI1 (audio).
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

    /* FRO 12 MHz → FLEXCOMM2 (servo I²C) */
    CLOCK_SetClkDiv(BOARD_SERVO_I2C_CLK_DIV, 1u);
    CLOCK_AttachClk(BOARD_SERVO_I2C_CLK_ATTACH);

    /* FRO 12 MHz → FLEXCOMM1 (LPSPI1 for ILI9341) */
    CLOCK_SetClkDiv(BOARD_ILI_SPI_CLK_DIV, 1u);
    CLOCK_AttachClk(BOARD_ILI_SPI_CLK_ATTACH);

    /* Audio PLL → SAI1 (single source for both TX and RX framers) */
    CLOCK_SetClkDiv(BOARD_SAI_CLK_DIV, 1u);
    CLOCK_AttachClk(BOARD_SAI_CLK_ATTACH);

    BOARD_InitBootPins();
    BOARD_InitBootClocks();
    BOARD_InitDebugConsole();
}
