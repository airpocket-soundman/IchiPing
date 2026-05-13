/*
 * Copyright 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Project 03_ili9341_test — LPSPI3 to the ILI9341 + 4 GPIO control lines.
 * Macros below feed main.c's #ifndef defaults so changes here propagate
 * without editing main.c.
 */
#ifndef _APP_H_
#define _APP_H_

#define BOARD_ILI_SPI_BASE          LPSPI3
#define BOARD_ILI_SPI_CLK_ATTACH    kFRO12M_to_FLEXCOMM3
#define BOARD_ILI_SPI_CLK_DIV       kCLOCK_DivFlexcom3Clk
#define BOARD_ILI_SPI_CLK_FREQ      CLOCK_GetLPFlexCommClkFreq(3u)

/* GPIO pins for CS / RESET / DC / BL — match pin_mux.c.
 * Defaults below mirror main.c's #ifndef block (PORT0_24..27). */
#define BOARD_ILI_CS_GPIO   GPIO0
#define BOARD_ILI_CS_PIN    24U
#define BOARD_ILI_RES_GPIO  GPIO0
#define BOARD_ILI_RES_PIN   25U
#define BOARD_ILI_DC_GPIO   GPIO0
#define BOARD_ILI_DC_PIN    26U
#define BOARD_ILI_BL_GPIO   GPIO0
#define BOARD_ILI_BL_PIN    27U

#endif /* _APP_H_ */
