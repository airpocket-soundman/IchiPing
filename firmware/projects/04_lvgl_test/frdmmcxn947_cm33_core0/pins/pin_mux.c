/*
 * Copyright 2022-2024 NXP / 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Pin routing for 04_lvgl_test (same hardware as 03_ili9341_test):
 *   - PIO1_8 / PIO1_9 → LP_FLEXCOMM4 (OpenSDA debug UART), Alt2  ← confirmed
 *   - LPSPI on Arduino D11 (MOSI) / D13 (SCK)                    ← TODO
 *   - 4× GPIO on Arduino A2..A5 for CS / RES / DC / BL           ← TODO
 *
 * See 03_ili9341_test/pins/pin_mux.c for the longer note. The Arduino
 * SPI pin mapping is NOT yet confirmed against MCUXpresso Pins tool —
 * the values below are placeholders that compile but will most likely
 * need to be re-routed.
 */

#include "fsl_common.h"
#include "fsl_port.h"
#include "pin_mux.h"

void BOARD_InitBootPins(void)
{
    BOARD_InitPins();
    LPSPI3_InitPins();
    ILI9341_GPIO_InitPins();
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

void LPSPI3_InitPins(void)
{
    /* LPSPI3 SCK / SOUT on FlexComm 3.
     * TODO: confirm port/pin for Arduino D11 (MOSI) and D13 (SCK) on the
     *       FRDM-MCXN947 schematic — values below assume FC3 mux Alt2 on
     *       PORT0 pins similar to the demo's FC1 routing.
     */
    CLOCK_EnableClock(kCLOCK_Port0);

    const port_pin_config_t spi_cfg = {
        kPORT_PullUp,                kPORT_LowPullResistor,
        kPORT_SlowSlewRate,          kPORT_PassiveFilterDisable,
        kPORT_OpenDrainDisable,      kPORT_LowDriveStrength,
        kPORT_MuxAlt2,               /* FC3 — VERIFY in Pins tool */
        kPORT_InputBufferEnable,     kPORT_InputNormal,
        kPORT_UnlockRegister,
    };
    /* Placeholder pins — Config Tools should overwrite once routed: */
    PORT_SetPinConfig(PORT0, 16U, &spi_cfg);  /* SCK  (D13) */
    PORT_SetPinConfig(PORT0, 17U, &spi_cfg);  /* SOUT (D11) */
}

void ILI9341_GPIO_InitPins(void)
{
    /* GPIO0 P24..27 = CS / RESET / DC / BL for the ILI9341.
     * Matches main.c #ifndef defaults (PORT0_24..27, MuxAlt0 = GPIO).
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
    PORT_SetPinConfig(PORT0, 24U, &gpio_out_cfg);  /* CS */
    PORT_SetPinConfig(PORT0, 25U, &gpio_out_cfg);  /* RESET */
    PORT_SetPinConfig(PORT0, 26U, &gpio_out_cfg);  /* DC */
    PORT_SetPinConfig(PORT0, 27U, &gpio_out_cfg);  /* BL */
}
