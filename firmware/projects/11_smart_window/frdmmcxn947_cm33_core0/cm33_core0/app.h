/*
 * Copyright 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * 09_collector — merged peripheral defines for SAI1 (audio) + LPI2C2
 * (PCA9685 servos) + LPSPI1 (ILI9341 TFT) + on-board SW3.
 */
#ifndef _APP_H_
#define _APP_H_

/* ---- SAI1 (audio, full-duplex; see 08) ---- */
#define BOARD_SAI_BASE           SAI1
#define BOARD_SAI_CLK_ATTACH     kFRO_HF_to_SAI1
#define BOARD_SAI_CLK_DIV        kCLOCK_DivSai1Clk
#define BOARD_SAI_CLK_FREQ       CLOCK_GetSaiClkFreq(1u)

#define BOARD_MIC_SAI_BASE       BOARD_SAI_BASE
#define BOARD_MIC_SAI_CLK_ATTACH BOARD_SAI_CLK_ATTACH
#define BOARD_MIC_SAI_CLK_DIV    BOARD_SAI_CLK_DIV
#define BOARD_MIC_SAI_CLK_FREQ   BOARD_SAI_CLK_FREQ
#define BOARD_SPK_SAI_BASE       BOARD_SAI_BASE
#define BOARD_SPK_SAI_CLK_ATTACH BOARD_SAI_CLK_ATTACH
#define BOARD_SPK_SAI_CLK_DIV    BOARD_SAI_CLK_DIV
#define BOARD_SPK_SAI_CLK_FREQ   BOARD_SAI_CLK_FREQ

/* ---- LPI2C2 (PCA9685 servo driver; see 02) ---- */
#define BOARD_SERVO_I2C_BASEADDR   LPI2C2
#define BOARD_SERVO_I2C_CLK_ATTACH kFRO12M_to_FLEXCOMM2
#define BOARD_SERVO_I2C_CLK_DIV    kCLOCK_DivFlexcom2Clk

/* ---- LPSPI1 (ILI9341; see 03) ---- */
#define BOARD_ILI_SPI_BASE         LPSPI1
#define BOARD_ILI_SPI_CLK_ATTACH   kFRO12M_to_FLEXCOMM1
#define BOARD_ILI_SPI_CLK_DIV      kCLOCK_DivFlexcom1Clk
#define BOARD_ILI_SPI_CLK_FREQ     CLOCK_GetLPFlexCommClkFreq(1u)

#define BOARD_ILI_CS_GPIO          GPIO0
#define BOARD_ILI_CS_PIN           14U     /* A2 = P0_14 */
#define BOARD_ILI_RES_GPIO         GPIO0
#define BOARD_ILI_RES_PIN          22U     /* A3 = P0_22 */
#define BOARD_ILI_DC_GPIO          GPIO0
#define BOARD_ILI_DC_PIN           15U     /* A4 = P0_15 */
#define BOARD_ILI_BL_GPIO          GPIO0
#define BOARD_ILI_BL_PIN           23U     /* A5 = P0_23 */

/* ---- On-board SW3 ---- */
#define BOARD_USER_BUTTON_GPIO     GPIO0
#define BOARD_USER_BUTTON_PIN      6U
#define BOARD_USER_BUTTON_NAME     "SW3"

/* ---- LPUART5 (ESP32 / M5Stamp Pico UART; see pin_mux LPUART5_InitPins)
 * 配線 spec の LPUART2/FC2 は servo I²C と衝突するため FC5 を使う。 */
#define BOARD_ESP_UART_BASEADDR     LPUART5
#define BOARD_ESP_UART_CLK_ATTACH   kFRO12M_to_FLEXCOMM5
#define BOARD_ESP_UART_CLK_DIV      kCLOCK_DivFlexcom5Clk
#define BOARD_ESP_UART_CLK_FREQ     CLOCK_GetLPFlexCommClkFreq(5u)

/* ---- Rain sensor YL-83 (HIGH=dry / LOW=wet)
 * Arduino D9 = P3_4 想定。pin_mux で内蔵プルアップ済。
 * GPIO direction (input) は main.c で設定。 */
#define BOARD_RAIN_SENSOR_GPIO      GPIO3
#define BOARD_RAIN_SENSOR_PIN       4U
#define BOARD_RAIN_SENSOR_WET_LEVEL 0u   /* 0 = wet (active low) */

#endif /* _APP_H_ */
