#ifndef _PIN_MUX_H_
#define _PIN_MUX_H_

#if defined(__cplusplus)
extern "C" {
#endif

void BOARD_InitBootPins(void);
void BOARD_InitPins(void);
void SAI1_InitPins(void);
void LPI2C2_InitPins(void);
void LPSPI1_InitPins(void);
void ILI9341_GPIO_InitPins(void);
void SW3_InitPins(void);
void LPUART5_InitPins(void);     /* ESP32 (M5Stamp Pico) UART */
void RAIN_SENSOR_InitPins(void); /* YL-83 rain sensor on D9 (P3_4) */

#if defined(__cplusplus)
}
#endif
#endif
