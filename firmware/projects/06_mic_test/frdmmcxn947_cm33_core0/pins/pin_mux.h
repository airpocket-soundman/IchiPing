#ifndef _PIN_MUX_H_
#define _PIN_MUX_H_

#if defined(__cplusplus)
extern "C" {
#endif

void BOARD_InitBootPins(void);
void BOARD_InitPins(void);
void SAI1_RX_InitPins(void);

#if defined(__cplusplus)
}
#endif
#endif
