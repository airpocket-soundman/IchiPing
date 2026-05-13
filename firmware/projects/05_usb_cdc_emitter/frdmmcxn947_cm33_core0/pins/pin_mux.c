/*
 * Copyright 2022-2023 NXP / 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Pin routing for 05_usb_cdc_emitter:
 *   - PIO1_8 / PIO1_9 → FlexComm 4 (OpenSDA debug UART)
 *   - USB1 D+ / D-     → fixed-function pads on MCXN947, no mux needed
 *
 * Edit with MCUXpresso Config Tools / Pins tool to regenerate.
 */

#include "fsl_common.h"
#include "fsl_port.h"
#include "pin_mux.h"

void BOARD_InitBootPins(void)
{
    BOARD_InitPins();
}

void BOARD_InitPins(void)
{
    /* OpenSDA debug UART on FC4 — PIO1_8 RX, PIO1_9 TX */
    CLOCK_EnableClock(kCLOCK_Port1);

    const port_pin_config_t uart_cfg = {
        kPORT_PullDisable,           kPORT_LowPullResistor,
        kPORT_FastSlewRate,          kPORT_PassiveFilterDisable,
        kPORT_OpenDrainDisable,      kPORT_LowDriveStrength,
        kPORT_MuxAlt2,
        kPORT_InputBufferEnable,     kPORT_InputNormal,
        kPORT_UnlockRegister,
    };
    PORT_SetPinConfig(PORT1, 8U, &uart_cfg);
    PORT_SetPinConfig(PORT1, 9U, &uart_cfg);
}
