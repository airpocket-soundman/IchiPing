/*
 * Copyright 2022-2024 NXP / 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Pin routing for 03_ili9341_test, confirmed against the FRDM-MCXN947
 * Board User Manual (docs/pdf/FRDM-MCXN947BoardUserManual.pdf) Table 18
 * (J2 Arduino header) and Table 20 (J4 analog header):
 *
 *   - PIO1_8 / PIO1_9   → LP_FLEXCOMM4 (OpenSDA debug UART), Alt2
 *   - PIO0_24 (D11 = J2.8)  → LPSPI1 SDO, Alt2  (SJ7 default 1-2)
 *   - PIO0_25 (D13 = J2.12) → LPSPI1 SCK, Alt2
 *   - PIO0_26 (D12 = J2.10) → LPSPI1 SDI, Alt2  (unused, ILI write-only)
 *   - PIO0_14 (A2 = J4.6)   → GPIO out, ILI CS
 *   - PIO0_22 (A3 = J4.8)   → GPIO out, ILI RESET
 *   - PIO0_15 (A4 = J4.10)  → GPIO out, ILI DC    (SJ8 default 1-2)
 *   - PIO0_23 (A5 = J4.12)  → GPIO out, ILI BL    (SJ9 default 1-2)
 *
 * D10 (P0_27 / FC1_PCS) is intentionally not muxed for SPI — kept on its
 * SJ6 default 1-2 so LED_GREEN stays accessible and microSD can be added
 * later (sharing D11/D12/D13 with a separate GPIO CS). ILI uses manual
 * CS on A2 so no hardware PCS line is needed.
 *
 * MISO is wired but unused — the ILI9341 is write-only here. Leaving the
 * pad muxed for FC1_SDI lets you swap the chip for a read-capable display
 * (or add microSD) without changing pin_mux.c.
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

void LPSPI1_InitPins(void)
{
    /* LPSPI1 master on Arduino J2 (FC1). Verified against the FRDM-MCXN947
     * Board User Manual Table 18 and the SDK reference example
     * driver_examples/lpspi/polling_b2b_transfer/master/pin_mux.c.
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
    /* LPSPI1 master on Arduino J2 (FC1). Source: Board User Manual
     * Table 18 + SDK lpspi/polling_b2b_transfer pin_mux.c.
     *   D11 = P0_24 = FC1_P0 = SDO (MOSI) — SJ7 must be 1-2 (default)
     *   D13 = P0_25 = FC1_P1 = SCK
     *   D12 = P0_26 = FC1_P2 = SDI (MISO) — not used by write-only ILI
     * D10 (P0_27 / FC1_PCS) is left as GPIO so we can keep LED_GREEN
     * (SJ6 in its default 1-2 position), and use manual CS on A2 instead. */
    PORT_SetPinConfig(PORT0, 24U, &spi_cfg);  /* SDO   (D11) */
    PORT_SetPinConfig(PORT0, 25U, &spi_cfg);  /* SCK   (D13) */
    PORT_SetPinConfig(PORT0, 26U, &spi_cfg);  /* SDI   (D12, unused) */
}

void ILI9341_GPIO_InitPins(void)
{
    /* GPIO control for ILI9341 on Arduino A2..A5 (J4 header, PORT0).
     * Source: Board User Manual Table 20.
     *   A2 = P0_14 → CS
     *   A3 = P0_22 → RESET
     *   A4 = P0_15 → DC     (SJ8 default 1-2)
     *   A5 = P0_23 → BL     (SJ9 default 1-2; otherwise routes to Wakeup)
     * All Alt0 = digital GPIO output.
     *
     * Note: keeping the SPI pins (D11/D12/D13) on PORT0 24..26 means the
     * ILI control pins on A2..A5 conveniently sit on the same PORT0,
     * so a single CLOCK_EnableClock(kCLOCK_Port0) here covers both. */
    CLOCK_EnableClock(kCLOCK_Port0);

    const port_pin_config_t gpio_out_cfg = {
        kPORT_PullDisable,           kPORT_LowPullResistor,
        kPORT_FastSlewRate,          kPORT_PassiveFilterDisable,
        kPORT_OpenDrainDisable,      kPORT_LowDriveStrength,
        kPORT_MuxAlt0,               /* GPIO */
        kPORT_InputBufferEnable,     kPORT_InputNormal,
        kPORT_UnlockRegister,
    };
    PORT_SetPinConfig(PORT0, 14U, &gpio_out_cfg);  /* A2 → CS    */
    PORT_SetPinConfig(PORT0, 22U, &gpio_out_cfg);  /* A3 → RESET */
    PORT_SetPinConfig(PORT0, 15U, &gpio_out_cfg);  /* A4 → DC    */
    PORT_SetPinConfig(PORT0, 23U, &gpio_out_cfg);  /* A5 → BL    */
}
