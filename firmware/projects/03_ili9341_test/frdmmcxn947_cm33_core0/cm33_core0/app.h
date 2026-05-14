/*
 * Copyright 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Project 03_ili9341_test — LPSPI1 (FC1) to the ILI9341 + 4 GPIO control
 * lines on the Arduino A2..A5 header pins.
 *
 * SPI peripheral confirmed against the FRDM-MCXN947 Board User Manual
 * Table 18 (Arduino J2 pinout) and the SDK example
 * driver_examples/lpspi/polling_b2b_transfer/master/pin_mux.c:
 *
 *   ARD_D10 = P0_27 = FC1_SPI_PCS  (Alt2)  — not used here (manual CS on A2)
 *   ARD_D11 = P0_24 = FC1_SPI_SDO  (Alt2)  — MOSI to ILI9341 SDI
 *   ARD_D12 = P0_26 = FC1_SPI_SDI  (Alt2)  — MISO, ILI9341 SDO (n/c)
 *   ARD_D13 = P0_25 = FC1_SPI_SCK  (Alt2)  — SCK
 *
 * Earlier app.h pointed at LPSPI3; that was a wrong inference from the
 * Arduino UNO R3 convention. The actual board routes D10..D13 to FC1.
 */
#ifndef _APP_H_
#define _APP_H_

#define BOARD_ILI_SPI_BASE          LPSPI1
#define BOARD_ILI_SPI_CLK_ATTACH    kFRO12M_to_FLEXCOMM1
#define BOARD_ILI_SPI_CLK_DIV       kCLOCK_DivFlexcom1Clk
#define BOARD_ILI_SPI_CLK_FREQ      CLOCK_GetLPFlexCommClkFreq(1u)

/* GPIO control lines on Arduino A2..A5 (J4.6 / J4.8 / J4.10 / J4.12).
 * Source: Board User Manual Table 20 (J4 pinout). Each maps to a single
 * PORT0 pin so the ILI driver only needs GPIO0 and four bit numbers. */
#define BOARD_ILI_CS_GPIO   GPIO0
#define BOARD_ILI_CS_PIN    14U     /* A2 = P0_14 */
#define BOARD_ILI_RES_GPIO  GPIO0
#define BOARD_ILI_RES_PIN   22U     /* A3 = P0_22 */
#define BOARD_ILI_DC_GPIO   GPIO0
#define BOARD_ILI_DC_PIN    15U     /* A4 = P0_15 (SJ8 default 1-2) */
#define BOARD_ILI_BL_GPIO   GPIO0
#define BOARD_ILI_BL_PIN    23U     /* A5 = P0_23 (SJ9 default 1-2) */

#endif /* _APP_H_ */
