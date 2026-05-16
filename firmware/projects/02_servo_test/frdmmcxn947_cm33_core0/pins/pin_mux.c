/*
 * Copyright 2022-2024 NXP / 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Pin routing for 02_servo_test (confirmed against the FRDM-MCXN947
 * SDK pin map and the working VL53L0X demo project):
 *
 *   - PIO1_8 / PIO1_9 → LP_FLEXCOMM4 (OpenSDA debug UART), Alt2
 *   - PIO4_0 / PIO4_1 → LP_FLEXCOMM2 (LPI2C2 master), Alt2
 *
 * PIO4_0 (BGA P1) and PIO4_1 (BGA P2) are the Arduino-shield I²C pads on
 * J2 pins 18/20, labelled ARD_D18 (SDA) and ARD_D19 (SCL) on the FRDM
 * silkscreen. The earlier wiring.md "D14/D15" notation was wrong — the
 * physical board pins are D18/D19.
 *
 * Internal pull-ups are enabled. The on-board sensor / Arduino-shield
 * traces are short enough that the ~50 kΩ internal pull-ups are sufficient
 * for 100 kHz operation (confirmed working in FRDM-MCXN947_demo/
 * 41_lpi2c_vl53l0x_my). If you add multiple devices to the bus or push
 * to 400 kHz, add a single external 4.7 kΩ × 2 pair.
 */

#include "fsl_common.h"
#include "fsl_port.h"
#include "pin_mux.h"

void BOARD_InitBootPins(void)
{
    BOARD_InitPins();
    LPI2C2_InitPins();
    SW3_InitPins();
}

void BOARD_InitPins(void)
{
    /* OpenSDA debug UART (FC4) — PIO1_8 RX, PIO1_9 TX */
    CLOCK_EnableClock(kCLOCK_Port1);

    const port_pin_config_t uart_cfg = {
        kPORT_PullDisable,           kPORT_LowPullResistor,
        kPORT_FastSlewRate,          kPORT_PassiveFilterDisable,
        kPORT_OpenDrainDisable,      kPORT_LowDriveStrength,
        kPORT_MuxAlt2,               /* FC4_P0 / FC4_P1 */
        kPORT_InputBufferEnable,     kPORT_InputNormal,
        kPORT_UnlockRegister,
    };
    PORT_SetPinConfig(PORT1, 8U, &uart_cfg);
    PORT_SetPinConfig(PORT1, 9U, &uart_cfg);
}

void LPI2C2_InitPins(void)
{
    /* LPI2C2 SDA / SCL → Arduino D18 / D19 on J2 pins 18 / 20.
     * Internal pull-up enabled — sufficient for short Arduino-shield traces
     * at 100 kHz with one slave device (PCA9685 or LU9685). */
    CLOCK_EnableClock(kCLOCK_Port4);

    const port_pin_config_t i2c_cfg = {
        kPORT_PullUp,                kPORT_LowPullResistor,
        kPORT_FastSlewRate,          kPORT_PassiveFilterDisable,
        kPORT_OpenDrainDisable,      kPORT_LowDriveStrength,
        kPORT_MuxAlt2,               /* FC2_P0 / FC2_P1 */
        kPORT_InputBufferEnable,     kPORT_InputNormal,
        kPORT_UnlockRegister,
    };
    PORT_SetPinConfig(PORT4, 0U, &i2c_cfg);  /* SDA = ARD_D18 (J2 pin 18) */
    PORT_SetPinConfig(PORT4, 1U, &i2c_cfg);  /* SCL = ARD_D19 (J2 pin 20) */
}

/* On-board user button SW3 = PORT0 pin 6 (FRDM-MCXN947 schematic).
 * Active-low with an external pull-up on the dev board; we additionally
 * enable the internal pull-up for redundancy and the passive filter
 * for hardware debouncing. Used by main.c to start/stop the demo. */
void SW3_InitPins(void)
{
    CLOCK_EnableClock(kCLOCK_Port0);

    const port_pin_config_t btn_cfg = {
        kPORT_PullUp,                kPORT_HighPullResistor,
        kPORT_FastSlewRate,          kPORT_PassiveFilterEnable,
        kPORT_OpenDrainDisable,      kPORT_LowDriveStrength,
        kPORT_MuxAlt0,               /* GPIO function */
        kPORT_InputBufferEnable,     kPORT_InputNormal,
        kPORT_UnlockRegister,
    };
    PORT_SetPinConfig(PORT0, 6U, &btn_cfg);
}
