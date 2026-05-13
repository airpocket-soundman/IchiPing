/*
 * Copyright 2022-2023 NXP / 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Pin routing for 03_ili9341_test.
 * Edit with MCUXpresso Config Tools / Pins tool to regenerate.
 */

#ifndef _PIN_MUX_H_
#define _PIN_MUX_H_

#if defined(__cplusplus)
extern "C" {
#endif

void BOARD_InitBootPins(void);
void BOARD_InitPins(void);
void LPSPI3_InitPins(void);
void ILI9341_GPIO_InitPins(void);

#if defined(__cplusplus)
}
#endif

#endif /* _PIN_MUX_H_ */
