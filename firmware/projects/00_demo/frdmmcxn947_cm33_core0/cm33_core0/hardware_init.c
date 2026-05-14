/*
 * Copyright 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * 00_demo: attaches FlexComm 4 (OpenSDA debug UART + 921600 binary frame
 * stream), FlexComm 1 (LPSPI1 for ILI9341 TFT), and FlexComm 2 (LPI2C2
 * for PCA9685 / LU9685 servo driver) before BOARD_InitBootPins runs.
 *
 * Skipping this step causes LP_FLEXCOMM_Init() inside each driver to fail
 * the PSELID readback (the FlexComm has no functional clock), the per-
 * peripheral register layout never gets selected, and the first transfer
 * returns kStatus_InvalidArgument (=4). 03_ili9341_test hit exactly that
 * symptom before this file was wired into main.
 */

#include "pin_mux.h"
#include "clock_config.h"
#include "board.h"
#include "app.h"

void BOARD_InitHardware(void)
{
    /* FRO 12 MHz -> FLEXCOMM4 (OpenSDA debug UART + binary frame TX) */
    CLOCK_SetClkDiv(kCLOCK_DivFlexcom4Clk, 1u);
    CLOCK_AttachClk(BOARD_DEBUG_UART_CLK_ATTACH);

    /* FRO 12 MHz -> FLEXCOMM1 (LPSPI1 for ILI9341 TFT) */
    CLOCK_SetClkDiv(BOARD_ILI_SPI_CLK_DIV, 1u);
    CLOCK_AttachClk(BOARD_ILI_SPI_CLK_ATTACH);

    /* FRO 12 MHz -> FLEXCOMM2 (LPI2C2 for servo driver) */
    CLOCK_SetClkDiv(BOARD_SERVO_I2C_CLK_DIV, 1u);
    CLOCK_AttachClk(BOARD_SERVO_I2C_CLK_ATTACH);

    BOARD_InitBootPins();
    BOARD_InitBootClocks();
    BOARD_InitDebugConsole();
}
