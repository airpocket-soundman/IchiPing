/*
 * Copyright 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * 05_usb_cdc_emitter: FlexComm 4 (debug UART) + USB1 (FS device).
 * USB clock setup is handled by BOARD_InitBootClocks() / SDK
 * USB_DeviceClockInit() — keep this file minimal.
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

    BOARD_InitBootPins();
    BOARD_InitBootClocks();
    BOARD_InitDebugConsole();

    /* USB device clock + power gating is set up by USB_DeviceClockInit() in
     * the SDK usb_device_cdc_vcom example (linked into this project). */
}
