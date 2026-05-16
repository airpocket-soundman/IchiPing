/*
 * Copyright 2022-2024 NXP / 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Pin routing for 10_inference — the autonomous inference demo. Same as
 * 09_collector minus the servo I²C bus (no PCA9685 needed):
 *
 *   D11/D12/D13 = LPSPI1 (FC1) → ILI9341 TFT
 *   A2/A3/A4/A5 = GPIO          → ILI CS/RESET/DC/BL
 *   J1.1/.11/.5/.15 = SAI1      → INMP441 + MAX98357A
 *   PIO1_8/9 = LP_FLEXCOMM4     → OpenSDA debug UART
 *   PIO0_6   = GPIO0 in         → SW3 (start/stop gate)
 */

#include "fsl_common.h"
#include "fsl_port.h"
#include "pin_mux.h"

void BOARD_InitBootPins(void)
{
    BOARD_InitPins();
    SAI1_InitPins();
    LPSPI1_InitPins();
    ILI9341_GPIO_InitPins();
    SW3_InitPins();
}

void BOARD_InitPins(void)
{
    CLOCK_EnableClock(kCLOCK_Port1);
    const port_pin_config_t uart_cfg = {
        kPORT_PullDisable, kPORT_LowPullResistor, kPORT_FastSlewRate,
        kPORT_PassiveFilterDisable, kPORT_OpenDrainDisable, kPORT_HighDriveStrength,
        kPORT_MuxAlt2, kPORT_InputBufferEnable, kPORT_InputNormal, kPORT_UnlockRegister,
    };
    PORT_SetPinConfig(PORT1, 8U, &uart_cfg);
    PORT_SetPinConfig(PORT1, 9U, &uart_cfg);
}

void SAI1_InitPins(void)
{
    CLOCK_EnableClock(kCLOCK_Port3);
    const port_pin_config_t sai_cfg = {
        kPORT_PullDisable, kPORT_LowPullResistor, kPORT_FastSlewRate,
        kPORT_PassiveFilterDisable, kPORT_OpenDrainDisable, kPORT_HighDriveStrength,
        kPORT_MuxAlt10,
        kPORT_InputBufferEnable, kPORT_InputNormal, kPORT_UnlockRegister,
    };
    PORT_SetPinConfig(PORT3, 16U, &sai_cfg);   /* TX_BCLK */
    PORT_SetPinConfig(PORT3, 17U, &sai_cfg);   /* TX_FS   */
    PORT_SetPinConfig(PORT3, 20U, &sai_cfg);   /* TXD0    */
    PORT_SetPinConfig(PORT3, 21U, &sai_cfg);   /* RXD0    */
}

void LPSPI1_InitPins(void)
{
    CLOCK_EnableClock(kCLOCK_Port0);
    const port_pin_config_t spi_cfg = {
        kPORT_PullUp, kPORT_LowPullResistor, kPORT_SlowSlewRate,
        kPORT_PassiveFilterDisable, kPORT_OpenDrainDisable, kPORT_LowDriveStrength,
        kPORT_MuxAlt2,
        kPORT_InputBufferEnable, kPORT_InputNormal, kPORT_UnlockRegister,
    };
    PORT_SetPinConfig(PORT0, 24U, &spi_cfg);
    PORT_SetPinConfig(PORT0, 25U, &spi_cfg);
    PORT_SetPinConfig(PORT0, 26U, &spi_cfg);
}

void ILI9341_GPIO_InitPins(void)
{
    CLOCK_EnableClock(kCLOCK_Port0);
    const port_pin_config_t gpio_out_cfg = {
        kPORT_PullDisable, kPORT_LowPullResistor, kPORT_FastSlewRate,
        kPORT_PassiveFilterDisable, kPORT_OpenDrainDisable, kPORT_LowDriveStrength,
        kPORT_MuxAlt0,
        kPORT_InputBufferEnable, kPORT_InputNormal, kPORT_UnlockRegister,
    };
    PORT_SetPinConfig(PORT0, 14U, &gpio_out_cfg);  /* CS    */
    PORT_SetPinConfig(PORT0, 22U, &gpio_out_cfg);  /* RESET */
    PORT_SetPinConfig(PORT0, 15U, &gpio_out_cfg);  /* DC    */
    PORT_SetPinConfig(PORT0, 23U, &gpio_out_cfg);  /* BL    */
}

void SW3_InitPins(void)
{
    CLOCK_EnableClock(kCLOCK_Port0);
    const port_pin_config_t btn_cfg = {
        kPORT_PullUp, kPORT_HighPullResistor, kPORT_FastSlewRate,
        kPORT_PassiveFilterEnable, kPORT_OpenDrainDisable, kPORT_LowDriveStrength,
        kPORT_MuxAlt0,
        kPORT_InputBufferEnable, kPORT_InputNormal, kPORT_UnlockRegister,
    };
    PORT_SetPinConfig(PORT0, 6U, &btn_cfg);
}
