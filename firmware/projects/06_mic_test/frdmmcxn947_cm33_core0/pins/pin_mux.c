/*
 * Copyright 2022-2024 NXP / 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Pin routing for 06_mic_test, confirmed against the FRDM-MCXN947 SDK
 * SAI EDMA example (driver_examples/sai/edma_transfer):
 *
 *   - PIO1_8 / PIO1_9    → LP_FLEXCOMM4 (OpenSDA debug UART), Alt2
 *   - PIO3_16 SAI1_TX_BCLK → J1.1 via SJ11=1-2   (Alt10, kept as fallback)
 *   - PIO1_0  SAI1_TX_BCLK → J5.6 / J2.17 直結    (Alt10, primary BCLK route —
 *                            bypasses SJ11 to follow the same "no jumper"
 *                            philosophy as SAI1_TX_FS via J1.11)
 *   - PIO3_17 SAI1_TX_FS   → INMP441 WS / LRCLK  (Alt10, FS master out, J1.11)
 *   - PIO3_21 SAI1_RXD0    ← INMP441 SD (data)   (Alt10, J1.15)
 *
 * Even though only the RX side is used, the BCLK/FS are sourced from the
 * TX framer (master) and shared internally. INMP441's L/R pin must be
 * tied to GND to select the left channel.
 */

#include "fsl_common.h"
#include "fsl_port.h"
#include "pin_mux.h"

void BOARD_InitBootPins(void)
{
    BOARD_InitPins();
    SAI1_RX_InitPins();
}

void BOARD_InitPins(void)
{
    CLOCK_EnableClock(kCLOCK_Port1);
    const port_pin_config_t uart_cfg = {
        kPORT_PullDisable, kPORT_LowPullResistor, kPORT_FastSlewRate,
        kPORT_PassiveFilterDisable, kPORT_OpenDrainDisable, kPORT_LowDriveStrength,
        kPORT_MuxAlt2, kPORT_InputBufferEnable, kPORT_InputNormal, kPORT_UnlockRegister,
    };
    PORT_SetPinConfig(PORT1, 8U, &uart_cfg);
    PORT_SetPinConfig(PORT1, 9U, &uart_cfg);
}

void SAI1_RX_InitPins(void)
{
    CLOCK_EnableClock(kCLOCK_Port3);
    /* High drive strength on the BCLK pin is needed because 1 MHz BCLK with
     * jumper-wire + INMP441 input + scope probe loading attenuates a
     * low-drive output below the 3.3V CMOS VIH threshold (observed ~1.1 Vpp
     * sine instead of clean rail-to-rail). FS at 17 kHz is fine with low
     * drive, but we use the same cfg for all SAI pins for simplicity. */
    const port_pin_config_t sai_cfg = {
        kPORT_PullDisable, kPORT_LowPullResistor, kPORT_FastSlewRate,
        kPORT_PassiveFilterDisable, kPORT_OpenDrainDisable, kPORT_HighDriveStrength,
        kPORT_MuxAlt10,   /* SAI1 function */
        kPORT_InputBufferEnable, kPORT_InputNormal, kPORT_UnlockRegister,
    };
    PORT_SetPinConfig(PORT3, 16U, &sai_cfg);   /* SAI1_TX_BCLK → J1.1 (via SJ11) */
    PORT_SetPinConfig(PORT1,  0U, &sai_cfg);   /* SAI1_TX_BCLK → J5.6 / J2.17 (direct, no SJ) */
    PORT_SetPinConfig(PORT3, 17U, &sai_cfg);   /* SAI1_TX_FS   → J1.11 (direct) → INMP441 WS  */
    PORT_SetPinConfig(PORT3, 21U, &sai_cfg);   /* SAI1_RXD0    ← J1.15 → INMP441 SD  */
}
