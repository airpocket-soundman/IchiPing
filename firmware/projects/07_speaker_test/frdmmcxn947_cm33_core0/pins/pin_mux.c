/*
 * Copyright 2022-2024 NXP / 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Pin routing for 07_speaker_test, confirmed against the FRDM-MCXN947
 * SDK SAI EDMA example:
 *
 *   - PIO1_8 / PIO1_9    → LP_FLEXCOMM4 (OpenSDA debug UART), Alt2
 *   - PIO3_16 SAI1_TX_BCLK → MAX98357A BCLK  (Alt10)
 *   - PIO3_17 SAI1_TX_FS   → MAX98357A LRC   (Alt10)
 *   - PIO3_20 SAI1_TXD0    → MAX98357A DIN   (Alt10)
 *
 * NOTE: the SAI1 instance is shared with the on-board codec and (in our
 * setup) the INMP441 mic. The two cannot both be configured as master at
 * the same time — only 08_mic_speaker_test / 10_collector run full-duplex.
 */

#include "fsl_common.h"
#include "fsl_port.h"
#include "pin_mux.h"

void BOARD_InitBootPins(void)
{
    BOARD_InitPins();
    SAI1_TX_InitPins();
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

void SAI1_TX_InitPins(void)
{
    CLOCK_EnableClock(kCLOCK_Port3);
    const port_pin_config_t sai_cfg = {
        kPORT_PullDisable, kPORT_LowPullResistor, kPORT_FastSlewRate,
        kPORT_PassiveFilterDisable, kPORT_OpenDrainDisable, kPORT_HighDriveStrength,
        kPORT_MuxAlt10,   /* SAI1 function */
        kPORT_InputBufferEnable, kPORT_InputNormal, kPORT_UnlockRegister,
    };
    PORT_SetPinConfig(PORT3, 16U, &sai_cfg);   /* SAI1_TX_BCLK → MAX98357A BCLK */
    PORT_SetPinConfig(PORT3, 17U, &sai_cfg);   /* SAI1_TX_FS   → MAX98357A LRC  */
    PORT_SetPinConfig(PORT3, 20U, &sai_cfg);   /* SAI1_TXD0    → MAX98357A DIN  */
}

/* On-board user button SW3 = PORT0 pin 6 (FRDM-MCXN947 schematic). Active-low,
 * with an external pull-up on the dev board; we also enable the internal
 * pull-up for redundancy and turn on the passive filter for hardware
 * debouncing. SW2 (PORT0 pin 23) is intentionally NOT used here because
 * IchiPing reserves PORT0_23 for the ILI9341 backlight (A5). */
void SW3_InitPins(void)
{
    CLOCK_EnableClock(kCLOCK_Port0);
    const port_pin_config_t btn_cfg = {
        kPORT_PullUp, kPORT_HighPullResistor, kPORT_FastSlewRate,
        kPORT_PassiveFilterEnable, kPORT_OpenDrainDisable, kPORT_LowDriveStrength,
        kPORT_MuxAlt0,                /* GPIO function */
        kPORT_InputBufferEnable, kPORT_InputNormal, kPORT_UnlockRegister,
    };
    PORT_SetPinConfig(PORT0, 6U, &btn_cfg);
}
