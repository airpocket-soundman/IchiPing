/*
 * Copyright 2022-2023 NXP / 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Pin routing for 02_servo_test:
 *   - PIO1_8 / PIO1_9 → FlexComm 4 (OpenSDA debug UART)
 *   - PIO? / PIO?     → FlexComm 2 (LPI2C2, Arduino D14 SDA / D15 SCL)
 *
 * The exact Arduino-D14/D15 → PIO mapping depends on FRDM-MCXN947
 * silkscreen routing. PIO1_2 / PIO1_3 (FC2 alt mux) is a common pick on
 * this board; CONFIRM in MCUXpresso Pins tool against the schematic
 * before powering external servos.
 *
 * Edit with MCUXpresso Config Tools to regenerate.
 */

#include "fsl_common.h"
#include "fsl_port.h"
#include "pin_mux.h"

void BOARD_InitBootPins(void)
{
    BOARD_InitPins();
    LPI2C2_InitPins();
}

void BOARD_InitPins(void)
{
    /* OpenSDA debug UART (FC4) — PIO1_8 RX, PIO1_9 TX */
    CLOCK_EnableClock(kCLOCK_Port1);

    const port_pin_config_t uart_cfg = {
        kPORT_PullDisable,           kPORT_LowPullResistor,
        kPORT_FastSlewRate,          kPORT_PassiveFilterDisable,
        kPORT_OpenDrainDisable,      kPORT_LowDriveStrength,
        kPORT_MuxAlt2,               /* FC4 */
        kPORT_InputBufferEnable,     kPORT_InputNormal,
        kPORT_UnlockRegister,
    };
    PORT_SetPinConfig(PORT1, 8U, &uart_cfg);
    PORT_SetPinConfig(PORT1, 9U, &uart_cfg);
}

void LPI2C2_InitPins(void)
{
    /* LPI2C2 SDA / SCL → Arduino D14 / D15.
     * TODO: confirm port/pin against FRDM-MCXN947 schematic; PIO1_2 / PIO1_3
     *       routed to LP_FLEXCOMM2 (Alt5) is the common assignment.
     */
    CLOCK_EnableClock(kCLOCK_Port1);

    const port_pin_config_t i2c_cfg = {
        kPORT_PullUp,                kPORT_LowPullResistor,
        kPORT_SlowSlewRate,          kPORT_PassiveFilterDisable,
        kPORT_OpenDrainEnable,       kPORT_LowDriveStrength,
        kPORT_MuxAlt5,               /* FC2 — VERIFY in Pins tool */
        kPORT_InputBufferEnable,     kPORT_InputNormal,
        kPORT_UnlockRegister,
    };
    PORT_SetPinConfig(PORT1, 2U, &i2c_cfg);  /* SDA — confirm */
    PORT_SetPinConfig(PORT1, 3U, &i2c_cfg);  /* SCL — confirm */
}
