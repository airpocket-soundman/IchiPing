/*
 * Pin routing for 07_speaker_test:
 *   - PIO1_8 / PIO1_9  → FlexComm 4 OpenSDA debug UART
 *   - SAI0 BCLK/FS/TXD → MAX98357A BCLK / LRC / DIN
 *
 * SAI0 pin assignment must be confirmed against the MCUXpresso Pins tool.
 */

#include "fsl_common.h"
#include "fsl_port.h"
#include "pin_mux.h"

void BOARD_InitBootPins(void)
{
    BOARD_InitPins();
    SAI0_TX_InitPins();
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

void SAI0_TX_InitPins(void)
{
    /* TODO: confirm SAI0_TX_BCLK / SAI0_TX_SYNC / SAI0_TX_DATA pin mux on
     *       FRDM-MCXN947. PIO4 group is a common SAI0 routing target. */
    CLOCK_EnableClock(kCLOCK_Port4);
    const port_pin_config_t sai_cfg = {
        kPORT_PullDisable, kPORT_LowPullResistor, kPORT_FastSlewRate,
        kPORT_PassiveFilterDisable, kPORT_OpenDrainDisable, kPORT_HighDriveStrength,
        kPORT_MuxAlt8,
        kPORT_InputBufferEnable, kPORT_InputNormal, kPORT_UnlockRegister,
    };
    PORT_SetPinConfig(PORT4, 0U, &sai_cfg);    /* SAI0_TX_BCLK */
    PORT_SetPinConfig(PORT4, 1U, &sai_cfg);    /* SAI0_TX_SYNC */
    PORT_SetPinConfig(PORT4, 2U, &sai_cfg);    /* SAI0_TX_DATA */
}
