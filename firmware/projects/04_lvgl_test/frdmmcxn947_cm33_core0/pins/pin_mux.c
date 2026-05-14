/*
 * Copyright 2022-2024 NXP / 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Pin routing for 04_lvgl_test -- same hardware as 03_ili9341_test:
 *   - PIO1_8 / PIO1_9   -> LP_FLEXCOMM4 (OpenSDA debug UART), Alt2
 *   - LPSPI1 (FC1) on D11/D12/D13 (P0_24/26/25), Alt2
 *   - GPIO on A2/A3/A4/A5 (P0_14/22/15/23), Alt0 -> CS/RES/DC/BL
 *
 * Verified against FRDM-MCXN947 Board User Manual Tables 18 (J2) and 20
 * (J4) plus the SDK reference example
 * driver_examples/lpspi/polling_b2b_transfer/master/pin_mux.c. See
 * 03_ili9341_test/pin_mux.c for the longer note on SJ6/SJ7 (SPI bus
 * routing) and SJ8/SJ9 (A4/A5 default ADC/Wakeup routes).
 */

#include "fsl_common.h"
#include "fsl_port.h"
#include "pin_mux.h"

void BOARD_InitBootPins(void)
{
    BOARD_InitPins();
    LPSPI1_InitPins();
    ILI9341_GPIO_InitPins();
}

void BOARD_InitPins(void)
{
    /* OpenSDA debug UART on FC4 -- PIO1_8 RX, PIO1_9 TX */
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
     *   D11 = P0_24 = FC1_P0 = SDO (MOSI), Alt2
     *   D13 = P0_25 = FC1_P1 = SCK,        Alt2
     *   D12 = P0_26 = FC1_P2 = SDI (MISO), Alt2 -- unused by write-only ILI
     */
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

void ILI9341_GPIO_InitPins(void)
{
    /* GPIO control for ILI9341 on Arduino A2..A5 (J4 header, PORT0):
     *   A2 = P0_14 -> CS
     *   A3 = P0_22 -> RESET
     *   A4 = P0_15 -> DC    (SJ8 default 1-2)
     *   A5 = P0_23 -> BL    (SJ9 default 1-2; otherwise routes to Wakeup)
     */
    CLOCK_EnableClock(kCLOCK_Port0);

    const port_pin_config_t gpio_out_cfg = {
        kPORT_PullDisable,           kPORT_LowPullResistor,
        kPORT_FastSlewRate,          kPORT_PassiveFilterDisable,
        kPORT_OpenDrainDisable,      kPORT_LowDriveStrength,
        kPORT_MuxAlt0,               /* GPIO */
        kPORT_InputBufferEnable,     kPORT_InputNormal,
        kPORT_UnlockRegister,
    };
    PORT_SetPinConfig(PORT0, 14U, &gpio_out_cfg);  /* A2 -> CS    */
    PORT_SetPinConfig(PORT0, 22U, &gpio_out_cfg);  /* A3 -> RESET */
    PORT_SetPinConfig(PORT0, 15U, &gpio_out_cfg);  /* A4 -> DC    */
    PORT_SetPinConfig(PORT0, 23U, &gpio_out_cfg);  /* A5 -> BL    */
}
