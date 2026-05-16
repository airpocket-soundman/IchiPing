/*
 * Copyright 2022-2024 NXP / 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Pin routing for 08_mic_speaker_test — SAI1 full-duplex + SW3 gate:
 *
 *   - PIO1_8 / PIO1_9    → LP_FLEXCOMM4 (OpenSDA debug UART), Alt2
 *   - PIO3_16 SAI1_TX_BCLK → both INMP441 SCK and MAX98357A BCLK (Alt10)
 *   - PIO3_17 SAI1_TX_FS   → both INMP441 WS  and MAX98357A LRC  (Alt10)
 *   - PIO3_20 SAI1_TXD0    → MAX98357A DIN                       (Alt10)
 *   - PIO3_21 SAI1_RXD0    ← INMP441 SD                          (Alt10)
 *   - PIO0_6  SW3 (user btn, active-low)                          (Alt0 GPIO)
 *
 * Higher drive strength on the SAI pins matches 06/07 — jumper-wired BCLK
 * was dropping below CMOS VIH at LowDriveStrength (see 06 BRINGUP_NOTES).
 *
 * SAI1 carries both directions sample-locked, which is what we need for
 * impulse-response capture. SW3 gates the chirp cycle so the board does
 * not blast audio on power-up — same UX as 07_speaker_test.
 */

#include "fsl_common.h"
#include "fsl_port.h"
#include "pin_mux.h"

void BOARD_InitBootPins(void)
{
    BOARD_InitPins();
    SAI1_InitPins();
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
    PORT_SetPinConfig(PORT3, 16U, &sai_cfg);   /* TX_BCLK   */
    PORT_SetPinConfig(PORT3, 17U, &sai_cfg);   /* TX_FS     */
    PORT_SetPinConfig(PORT3, 20U, &sai_cfg);   /* TXD0 → MAX98357A */
    PORT_SetPinConfig(PORT3, 21U, &sai_cfg);   /* RXD0 ← INMP441   */
}

/* On-board user button SW3 = PORT0 pin 6 (FRDM-MCXN947 schematic). Active-low,
 * with an external pull-up; we also enable the internal pull-up for
 * redundancy and turn on the passive filter for hardware debouncing.
 * Identical setup to 07_speaker_test so the SW3 toggle behaviour matches. */
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
