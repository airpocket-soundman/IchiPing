/*
 * Copyright 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Project 04_lvgl_test -- same FRDM-MCXN947 pin map as 03_ili9341_test
 * (LPSPI1 / FC1 on Arduino D11/D13 + 4 GPIOs on A2..A5). LVGL is layered
 * on top of the same ili9341 driver, so the board macros below are
 * identical to 03/app.h.
 *
 * Verified against Board User Manual Tables 18 and 20.
 */
#ifndef _APP_H_
#define _APP_H_

#define BOARD_ILI_SPI_BASE          LPSPI1
#define BOARD_ILI_SPI_CLK_ATTACH    kFRO12M_to_FLEXCOMM1
#define BOARD_ILI_SPI_CLK_DIV       kCLOCK_DivFlexcom1Clk
#define BOARD_ILI_SPI_CLK_FREQ      CLOCK_GetLPFlexCommClkFreq(1u)

#define BOARD_ILI_CS_GPIO   GPIO0
#define BOARD_ILI_CS_PIN    14U     /* A2 = P0_14 */
#define BOARD_ILI_RES_GPIO  GPIO0
#define BOARD_ILI_RES_PIN   22U     /* A3 = P0_22 */
#define BOARD_ILI_DC_GPIO   GPIO0
#define BOARD_ILI_DC_PIN    15U     /* A4 = P0_15 (SJ8 default 1-2) */
#define BOARD_ILI_BL_GPIO   GPIO0
#define BOARD_ILI_BL_PIN    23U     /* A5 = P0_23 (SJ9 default 1-2) */

#endif /* _APP_H_ */
