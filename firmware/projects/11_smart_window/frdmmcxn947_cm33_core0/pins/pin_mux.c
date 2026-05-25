/*
 * Copyright 2022-2024 NXP / 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Pin routing for 11_smart_window — 10_inference の周辺に加えて、
 * 雨センサ (GPIO IN) と ESP32 (M5Stamp Pico) UART を追加した本番ファーム。
 *
 * Pin map (Arduino header → P-port → peripheral):
 *
 *   D11 (J2.8)   = P0_24  → LPSPI1 SDO  (Alt2)   ILI9341 SDI
 *   D12 (J2.10)  = P0_26  → LPSPI1 SDI  (Alt2)   ILI9341 SDO (n/c)
 *   D13 (J2.12)  = P0_25  → LPSPI1 SCK  (Alt2)
 *   A2  (J4.6)   = P0_14  → GPIO out                ILI CS
 *   A3  (J4.8)   = P0_22  → GPIO out                ILI RESET
 *   A4  (J4.10)  = P0_15  → GPIO out                ILI DC   (SJ8 default 1-2)
 *   A5  (J4.12)  = P0_23  → GPIO out                ILI BL   (SJ9 default 1-2)
 *   D18 (J2.18)  = P4_0   → LP_FLEXCOMM2 P0 (SDA) Alt2   PCA9685 SDA
 *   D19 (J2.20)  = P4_1   → LP_FLEXCOMM2 P1 (SCL) Alt2   PCA9685 SCL
 *   J1.1         = P3_16  → SAI1_TX_BCLK Alt10          INMP441 SCK + MAX98357A BCLK
 *   J1.11        = P3_17  → SAI1_TX_FS   Alt10          INMP441 WS  + MAX98357A LRC
 *   J1.5         = P3_20  → SAI1_TXD0    Alt10          MAX98357A DIN
 *   J1.15        = P3_21  → SAI1_RXD0    Alt10          INMP441 SD
 *   on-board SW3 = P0_6   → GPIO in Alt0
 *
 *   --- 11_smart_window 追加 ---
 *   D9  (J2.20 隣) = P3_4  → GPIO IN Alt0、内蔵プルアップ        雨センサ YL-83 (HIGH=乾燥 / LOW=雨)
 *   P1_16 (TBD)    → LP_FLEXCOMM5 P0 (LPUART5_TXD) Alt2          ESP32 RX
 *   P1_17 (TBD)    → LP_FLEXCOMM5 P1 (LPUART5_RXD) Alt2          ESP32 TX
 *
 * 注: ESP UART のピン (P1_16/17) は暫定。FRDM-MCXN947 datasheet で FC5 の
 * 物理ピン Alt 設定を確認してから wiring.md と一緒に確定する。
 * 当面はジャンパで配線する前提で driver init だけ通せばよい。
 */

#include "fsl_common.h"
#include "fsl_port.h"
#include "pin_mux.h"

void BOARD_InitBootPins(void)
{
    BOARD_InitPins();
    SAI1_InitPins();
    LPI2C2_InitPins();
    LPSPI1_InitPins();
    ILI9341_GPIO_InitPins();
    SW3_InitPins();
    LPUART5_InitPins();   /* ESP32 (M5Stamp Pico) UART */
    RAIN_SENSOR_InitPins(); /* YL-83 rain sensor */
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
    PORT_SetPinConfig(PORT3, 16U, &sai_cfg);   /* TX_BCLK shared TX/RX */
    PORT_SetPinConfig(PORT3, 17U, &sai_cfg);   /* TX_FS   shared TX/RX */
    PORT_SetPinConfig(PORT3, 20U, &sai_cfg);   /* TXD0 → MAX98357A     */
    PORT_SetPinConfig(PORT3, 21U, &sai_cfg);   /* RXD0 ← INMP441       */
}

void LPI2C2_InitPins(void)
{
    /* FC2 on Arduino D18/D19. Internal pull-ups sufficient for one slave
     * (PCA9685) at 100 kHz — same as 02_servo_test. */
    CLOCK_EnableClock(kCLOCK_Port4);
    const port_pin_config_t i2c_cfg = {
        kPORT_PullUp, kPORT_LowPullResistor, kPORT_FastSlewRate,
        kPORT_PassiveFilterDisable, kPORT_OpenDrainDisable, kPORT_LowDriveStrength,
        kPORT_MuxAlt2,
        kPORT_InputBufferEnable, kPORT_InputNormal, kPORT_UnlockRegister,
    };
    PORT_SetPinConfig(PORT4, 0U, &i2c_cfg);    /* SDA = ARD_D18 */
    PORT_SetPinConfig(PORT4, 1U, &i2c_cfg);    /* SCL = ARD_D19 */
}

void LPSPI1_InitPins(void)
{
    /* FC1 on Arduino D11/D12/D13. ILI9341 is write-only so D12 (SDI) is
     * muxed but unused. Same as 03_ili9341_test. */
    CLOCK_EnableClock(kCLOCK_Port0);
    const port_pin_config_t spi_cfg = {
        kPORT_PullUp, kPORT_LowPullResistor, kPORT_SlowSlewRate,
        kPORT_PassiveFilterDisable, kPORT_OpenDrainDisable, kPORT_LowDriveStrength,
        kPORT_MuxAlt2,
        kPORT_InputBufferEnable, kPORT_InputNormal, kPORT_UnlockRegister,
    };
    PORT_SetPinConfig(PORT0, 24U, &spi_cfg);   /* SDO (D11) */
    PORT_SetPinConfig(PORT0, 25U, &spi_cfg);   /* SCK (D13) */
    PORT_SetPinConfig(PORT0, 26U, &spi_cfg);   /* SDI (D12) unused */
}

void ILI9341_GPIO_InitPins(void)
{
    /* A2..A5 → CS / RESET / DC / BL. All on PORT0 (shared with SPI pins
     * — single clock enable above is sufficient, but call this again for
     * clarity and to keep the function self-contained). */
    CLOCK_EnableClock(kCLOCK_Port0);
    const port_pin_config_t gpio_out_cfg = {
        kPORT_PullDisable, kPORT_LowPullResistor, kPORT_FastSlewRate,
        kPORT_PassiveFilterDisable, kPORT_OpenDrainDisable, kPORT_LowDriveStrength,
        kPORT_MuxAlt0,
        kPORT_InputBufferEnable, kPORT_InputNormal, kPORT_UnlockRegister,
    };
    PORT_SetPinConfig(PORT0, 14U, &gpio_out_cfg);  /* A2 → CS    */
    PORT_SetPinConfig(PORT0, 22U, &gpio_out_cfg);  /* A3 → RESET */
    PORT_SetPinConfig(PORT0, 15U, &gpio_out_cfg);  /* A4 → DC    */
    PORT_SetPinConfig(PORT0, 23U, &gpio_out_cfg);  /* A5 → BL    */
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

void LPUART5_InitPins(void)
{
    /* ESP32 (M5Stamp Pico) と話す LPUART5 (FC5)。配線 spec で当初 LPUART2/FC2
     * を想定していたが、それは servo I²C と FlexComm が衝突するため FC5 に移動。
     * 物理ピンは P1_16/P1_17 想定 (FRDM-MCXN947 datasheet で要確認)。
     * 配線見直し時は wiring.md と本ファイルを同時更新。 */
    CLOCK_EnableClock(kCLOCK_Port1);
    const port_pin_config_t uart5_cfg = {
        kPORT_PullDisable, kPORT_LowPullResistor, kPORT_FastSlewRate,
        kPORT_PassiveFilterDisable, kPORT_OpenDrainDisable, kPORT_HighDriveStrength,
        kPORT_MuxAlt2,                /* FC5 Alt2 想定。要 datasheet 確認 */
        kPORT_InputBufferEnable, kPORT_InputNormal, kPORT_UnlockRegister,
    };
    PORT_SetPinConfig(PORT1, 16U, &uart5_cfg);   /* LPUART5_TXD → ESP RX */
    PORT_SetPinConfig(PORT1, 17U, &uart5_cfg);   /* LPUART5_RXD ← ESP TX */
}

void RAIN_SENSOR_InitPins(void)
{
    /* YL-83 雨センサ。乾燥時 HIGH、雨検出で LOW にプルダウン。
     * MCU 側は内蔵プルアップ + 入力モードで読む。Arduino header D9 = P3_4 想定。
     * GPIO 設定 (input direction) は main.c 側で行う。 */
    CLOCK_EnableClock(kCLOCK_Port3);
    const port_pin_config_t rain_cfg = {
        kPORT_PullUp, kPORT_LowPullResistor, kPORT_FastSlewRate,
        kPORT_PassiveFilterEnable, kPORT_OpenDrainDisable, kPORT_LowDriveStrength,
        kPORT_MuxAlt0,                /* GPIO mode */
        kPORT_InputBufferEnable, kPORT_InputNormal, kPORT_UnlockRegister,
    };
    PORT_SetPinConfig(PORT3, 4U, &rain_cfg);
}
