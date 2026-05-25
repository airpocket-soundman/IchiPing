/*
 * Copyright 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * 11_smart_window: 10_inference の周辺一式 (FC4 debug UART / FC2 servo I²C /
 * FC1 LPSPI1 TFT / Audio PLL SAI1) に加えて、ESP32 (M5Stamp Pico) 用 UART を
 * 別 FlexComm に追加。設計上 LPUART2 (FC2) はサーボ I²C と競合するため、
 * ESP は LPUART5 (FC5) を使用。物理ピンは pin_mux.c 側で確定 (要 datasheet 確認)。
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

    /* FRO 12 MHz → FLEXCOMM5 (ESP32 / M5Stamp Pico UART)
     * LPUART2/FC2 は既に servo I²C で使用済みのため、配線 spec とは異なるが
     * 競合回避のため LPUART5/FC5 を割り当てる。物理ピンは pin_mux.c 参照。 */
    CLOCK_SetClkDiv(kCLOCK_DivFlexcom5Clk, 1u);
    CLOCK_AttachClk(kFRO12M_to_FLEXCOMM5);

    /* Audio PLL → SAI1 (single source for both TX and RX framers) */
    CLOCK_SetClkDiv(BOARD_SAI_CLK_DIV, 1u);
    CLOCK_AttachClk(BOARD_SAI_CLK_ATTACH);

    BOARD_InitBootPins();
    BOARD_InitBootClocks();
    BOARD_InitDebugConsole();
}
