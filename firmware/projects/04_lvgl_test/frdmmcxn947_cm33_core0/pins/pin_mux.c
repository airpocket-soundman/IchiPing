/*
 * Copyright 2022-2023 NXP / 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Pin routing for 03_ili9341_test:
 *   - PIO1_8 / PIO1_9   → FlexComm 4 (OpenSDA debug UART)
 *   - LPSPI3 MOSI/SCK   → Arduino D11 / D13 (FC3)
 *   - PIO0_24..27       → 4× GPIO outputs for ILI9341 CS / RES / DC / BL
 *
 * The MISO line (D12) is unused — the ILI9341 is write-only on this design.
 * Exact Arduino-pin ↔ PIO mapping for D11/D13 depends on the FRDM-MCXN947
 * schematic; verify in MCUXpresso Pins tool before powering the display.
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
