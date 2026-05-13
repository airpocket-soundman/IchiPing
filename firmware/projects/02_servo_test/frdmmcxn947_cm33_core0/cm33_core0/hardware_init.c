/*
 * Copyright 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * 02_servo_test: attaches FlexComm 4 (debug UART) + FlexComm 2 (servo I²C).
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

    BOARD_InitBootPins();
    BOARD_InitBootClocks();
    BOARD_InitDebugConsole();
}
