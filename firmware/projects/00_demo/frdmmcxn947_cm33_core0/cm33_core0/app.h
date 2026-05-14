/*
 * Copyright 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Project 00_demo -- integration demo that bundles every subsystem that has
 * been individually proven so far. As features bring up, their macros come
 * here and the matching pins go into pin_mux.c.
 *
 *   - LPSPI1  (FC1, Arduino D11/D13)   -> ILI9341 TFT  (from 03_ili9341_test)
 *   - LPI2C2  (FC2, Arduino D18/D19)   -> PCA9685 / LU9685 servo driver
 *                                          (from 02_servo_test). FC4 was
 *                                          unusable for I2C because the
 *                                          OpenSDA debug UART already lives
 *                                          on it.
 *   - LPUART4 (FC4, OpenSDA VCP)       -> 921600 8N1 ichp frame stream
 *                                          (from 01_dummy_emitter).
 *
 * Pin allocations verified against the FRDM-MCXN947 Board User Manual
 * Tables 18 and 20.
 */
#ifndef _APP_H_
#define _APP_H_

/* ---- ILI9341 TFT on LPSPI1 (FC1) + 4 GPIOs on PORT0 ---- */
#define BOARD_ILI_SPI_BASE          LPSPI1
#define BOARD_ILI_SPI_CLK_ATTACH    kFRO12M_to_FLEXCOMM1
#define BOARD_ILI_SPI_CLK_DIV       kCLOCK_DivFlexcom1Clk
#define BOARD_ILI_SPI_CLK_FREQ      CLOCK_GetLPFlexCommClkFreq(1u)

#define BOARD_ILI_CS_GPIO           GPIO0
#define BOARD_ILI_CS_PIN            14U     /* A2 = P0_14 */
#define BOARD_ILI_RES_GPIO          GPIO0
#define BOARD_ILI_RES_PIN           22U     /* A3 = P0_22 */
#define BOARD_ILI_DC_GPIO           GPIO0
#define BOARD_ILI_DC_PIN            15U     /* A4 = P0_15 (SJ8 default 1-2) */
#define BOARD_ILI_BL_GPIO           GPIO0
#define BOARD_ILI_BL_PIN            23U     /* A5 = P0_23 (SJ9 default 1-2) */

/* ---- Servo driver on LPI2C2 (FC2) ---- */
#define BOARD_SERVO_I2C_BASEADDR    LPI2C2
#define BOARD_SERVO_I2C_CLK_ATTACH  kFRO12M_to_FLEXCOMM2
#define BOARD_SERVO_I2C_CLK_DIV     kCLOCK_DivFlexcom2Clk
#define BOARD_SERVO_I2C_CLK_FREQ    CLOCK_GetLPFlexCommClkFreq(2u)

/* ---- High-speed frame stream on the OpenSDA UART (FC4 / LPUART4) ----
 * Same physical pins as the debug console; we re-init the LPUART to 921600
 * 8N1 TX-only after BOARD_InitDebugConsole has done its bootstrap. PRINTF
 * keeps working but at the elevated baud, which the FRDM OpenSDA bridge
 * supports (verified in 01_dummy_emitter). */
#define BOARD_FRAME_UART_BASE       LPUART4
#define BOARD_FRAME_UART_CLK_FREQ   BOARD_DEBUG_UART_CLK_FREQ
#define BOARD_FRAME_UART_BAUD       921600u

#endif /* _APP_H_ */
