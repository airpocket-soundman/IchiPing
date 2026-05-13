/*
 * Pin routing for 06_mic_test:
 *   - PIO1_8 / PIO1_9  → FlexComm 4 OpenSDA debug UART
 *   - SAI1 BCLK/FS/RXD → INMP441
 *
 * Exact PIO numbers for SAI1 on the FRDM-MCXN947 Arduino headers are NOT
 * fixed in stone — pick three pins routed to SAI1 (Alt depends on MCU)
 * with the help of MCUXpresso Pins tool. Placeholders below assume PIO0
 * pins similar to the LPSPI demo.
 */

#include "fsl_common.h"
#include "fsl_port.h"
#include "pin_mux.h"

void BOARD_InitBootPins(void)
{
    BOARD_InitPins();
    SAI1_RX_InitPins();
}

void BOARD_InitPins(void)
{
    CLOCK_EnableClock(kCLOCK_Port1);
    const port_pin_config_t uart_cfg = {
        kPORT_PullDisable, kPORT_LowPullResistor, kPORT_FastSlewRate,
        kPORT_PassiveFilterDisable, kPORT_OpenDrainDisable, kPORT_LowDriveStrength,
        kPORT_MuxAlt2, kPORT_InputBufferEnable, kPORT_InputNormal, kPORT_UnlockRegister,
    };
    PORT_SetPinConfig(PORT1, 8U, &uart_cfg);
    PORT_SetPinConfig(PORT1, 9U, &uart_cfg);
}

void SAI1_RX_InitPins(void)
{
    /* TODO: confirm SAI1_TX_BCLK / SAI1_TX_SYNC / SAI1_RX_DATA pin mux on
     *       FRDM-MCXN947 via the MCUXpresso Pins tool. INMP441 needs all
     *       three signals; clock pins are typically shared with TX block. */
    CLOCK_EnableClock(kCLOCK_Port0);
    const port_pin_config_t sai_cfg = {
        kPORT_PullDisable, kPORT_LowPullResistor, kPORT_FastSlewRate,
        kPORT_PassiveFilterDisable, kPORT_OpenDrainDisable, kPORT_HighDriveStrength,
        kPORT_MuxAlt8,  /* SAI alt — VERIFY against MCXN947 reference manual */
        kPORT_InputBufferEnable, kPORT_InputNormal, kPORT_UnlockRegister,
    };
    PORT_SetPinConfig(PORT0, 0U,  &sai_cfg);   /* SAI1_TX_BCLK  → INMP441 SCK */
    PORT_SetPinConfig(PORT0, 1U,  &sai_cfg);   /* SAI1_TX_SYNC  → INMP441 WS  */
    PORT_SetPinConfig(PORT0, 2U,  &sai_cfg);   /* SAI1_RX_DATA  ← INMP441 SD  */
}
