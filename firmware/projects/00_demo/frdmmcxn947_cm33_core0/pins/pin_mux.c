/*
 * Copyright 2022-2024 NXP / 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Pin routing for 00_demo. Bundles the muxes proven in the per-feature
 * bring-up projects:
 *
 *   - PIO1_8 / PIO1_9   -> LP_FLEXCOMM4 (OpenSDA debug UART AND 921600
 *                          binary frame TX), Alt2  (from 01 / 02 / 03)
 *   - PIO0_24/25/26     -> LPSPI1 SDO/SCK/SDI on Arduino D11/D13/D12, Alt2
 *                          (from 03_ili9341_test)
 *   - PIO0_14/22/15/23  -> GPIO out, ILI9341 CS/RES/DC/BL on A2..A5, Alt0
 *                          (from 03_ili9341_test)
 *   - PIO4_0 / PIO4_1   -> LPI2C2 SDA/SCL on Arduino D18/D19, Alt2
 *                          (from 02_servo_test)
 *
 * Verified against the FRDM-MCXN947 Board User Manual Tables 18 (J2) and
 * 20 (J4). See 03_ili9341_test/pins/pin_mux.c and 02_servo_test/pins/
 * pin_mux.c for the longer per-feature commentary on the same pins.
 */

#include "fsl_common.h"
#include "fsl_port.h"
#include "pin_mux.h"

void BOARD_InitBootPins(void)
{
    BOARD_InitPins();
    LPSPI1_InitPins();
    LPI2C2_InitPins();
    ILI9341_GPIO_InitPins();
}

void BOARD_InitPins(void)
{
    /* OpenSDA debug UART (FC4) -- PIO1_8 RX, PIO1_9 TX. Same pads carry
     * both the boot-time printf and the 921600 ichp frame stream. */
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

void LPSPI1_InitPins(void)
{
    /* LPSPI1 master on Arduino J2 (FC1):
     *   D11 = P0_24 = FC1_P0 = SDO (MOSI), Alt2 -- SJ7 default 1-2
     *   D13 = P0_25 = FC1_P1 = SCK,        Alt2
     *   D12 = P0_26 = FC1_P2 = SDI (MISO), Alt2 -- unused by write-only ILI
     * D10 (P0_27 / FC1_PCS) stays GPIO so SJ6 default 1-2 keeps LED_GREEN
     * accessible; ILI uses manual GPIO CS on A2. */
    CLOCK_EnableClock(kCLOCK_Port0);

    const port_pin_config_t spi_cfg = {
        kPORT_PullUp,                kPORT_LowPullResistor,
        kPORT_SlowSlewRate,          kPORT_PassiveFilterDisable,
        kPORT_OpenDrainDisable,      kPORT_LowDriveStrength,
        kPORT_MuxAlt2,               /* FC1 (= LP_FLEXCOMM1) */
        kPORT_InputBufferEnable,     kPORT_InputNormal,
        kPORT_UnlockRegister,
    };
    PORT_SetPinConfig(PORT0, 24U, &spi_cfg);  /* SDO (D11) */
    PORT_SetPinConfig(PORT0, 25U, &spi_cfg);  /* SCK (D13) */
    PORT_SetPinConfig(PORT0, 26U, &spi_cfg);  /* SDI (D12, unused) */
}

void LPI2C2_InitPins(void)
{
    /* LPI2C2 master on Arduino J2 pins 18/20 (FC2):
     *   D18 = P4_0 = FC2_P0 = SDA, Alt2
     *   D19 = P4_1 = FC2_P1 = SCL, Alt2
     * Internal pull-up enabled -- proven adequate at 100 kHz with a single
     * PCA9685 / LU9685 slave on the FRDM-MCXN947 demo trace lengths. Add
     * external 4.7 kΩ × 2 if you push the bus to 400 kHz or hang another
     * device off it. */
    CLOCK_EnableClock(kCLOCK_Port4);

    const port_pin_config_t i2c_cfg = {
        kPORT_PullUp,                kPORT_LowPullResistor,
        kPORT_FastSlewRate,          kPORT_PassiveFilterDisable,
        kPORT_OpenDrainDisable,      kPORT_LowDriveStrength,
        kPORT_MuxAlt2,               /* FC2 (= LP_FLEXCOMM2) */
        kPORT_InputBufferEnable,     kPORT_InputNormal,
        kPORT_UnlockRegister,
    };
    PORT_SetPinConfig(PORT4, 0U, &i2c_cfg);
    PORT_SetPinConfig(PORT4, 1U, &i2c_cfg);
}

void ILI9341_GPIO_InitPins(void)
{
    /* ILI9341 control lines on the J4 analog header (PORT0):
     *   A2 = P0_14 -> CS
     *   A3 = P0_22 -> RESET
     *   A4 = P0_15 -> DC    (SJ8 default 1-2)
     *   A5 = P0_23 -> BL    (SJ9 default 1-2)
     * All Alt0 = digital GPIO. PORT0 clock already enabled by LPSPI1
     * setup above but a second EnableClock is idempotent. */
    CLOCK_EnableClock(kCLOCK_Port0);

    const port_pin_config_t gpio_out_cfg = {
        kPORT_PullDisable,           kPORT_LowPullResistor,
        kPORT_FastSlewRate,          kPORT_PassiveFilterDisable,
        kPORT_OpenDrainDisable,      kPORT_LowDriveStrength,
        kPORT_MuxAlt0,               /* GPIO */
        kPORT_InputBufferEnable,     kPORT_InputNormal,
        kPORT_UnlockRegister,
    };
    PORT_SetPinConfig(PORT0, 14U, &gpio_out_cfg);  /* CS    */
    PORT_SetPinConfig(PORT0, 22U, &gpio_out_cfg);  /* RESET */
    PORT_SetPinConfig(PORT0, 15U, &gpio_out_cfg);  /* DC    */
    PORT_SetPinConfig(PORT0, 23U, &gpio_out_cfg);  /* BL    */
}
