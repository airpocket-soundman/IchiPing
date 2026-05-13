/*
 * Copyright 2022-2023 NXP / 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Pin routing for project 01_dummy_emitter:
 *   - OpenSDA debug UART on FlexComm 4 (PIO1_8 = RX, PIO1_9 = TX).
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
    /* Enable clock for PORT1 (OpenSDA UART pins live here) */
    CLOCK_EnableClock(kCLOCK_Port1);

    const port_pin_config_t uart_rx_cfg = {
        kPORT_PullDisable,           kPORT_LowPullResistor,
        kPORT_FastSlewRate,          kPORT_PassiveFilterDisable,
        kPORT_OpenDrainDisable,      kPORT_LowDriveStrength,
        kPORT_MuxAlt2,               /* FC4_P0 */
        kPORT_InputBufferEnable,     kPORT_InputNormal,
        kPORT_UnlockRegister,
    };
    PORT_SetPinConfig(PORT1, 8U, &uart_rx_cfg);

    const port_pin_config_t uart_tx_cfg = {
        kPORT_PullDisable,           kPORT_LowPullResistor,
        kPORT_FastSlewRate,          kPORT_PassiveFilterDisable,
        kPORT_OpenDrainDisable,      kPORT_LowDriveStrength,
        kPORT_MuxAlt2,               /* FC4_P1 */
        kPORT_InputBufferEnable,     kPORT_InputNormal,
        kPORT_UnlockRegister,
    };
    PORT_SetPinConfig(PORT1, 9U, &uart_tx_cfg);
}
