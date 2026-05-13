/*
 * Pin routing for 08_mic_speaker_test:
 *   - PIO1_8 / PIO1_9 → FlexComm 4 OpenSDA debug UART (also doubles as the
 *                       921600 bps binary frame TX path)
 *   - SAI0 TX → MAX98357A (BCLK / LRC / DIN)
 *   - SAI1 RX → INMP441   (BCLK / WS  / SD)
 *
 * The SAI0 TX BCLK / FS and SAI1 RX BCLK / FS may be driven by separate
 * internal clock trees, or you can tie them so the mic + speaker run
 * sample-locked (recommended for impulse response work). Verify against
 * the MCXN947 reference manual SAI chapter.
 */

#include "fsl_common.h"
#include "fsl_port.h"
#include "pin_mux.h"

void BOARD_InitBootPins(void)
{
    BOARD_InitPins();
    SAI0_TX_InitPins();
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

void SAI0_TX_InitPins(void)
{
    CLOCK_EnableClock(kCLOCK_Port4);
    const port_pin_config_t sai_cfg = {
        kPORT_PullDisable, kPORT_LowPullResistor, kPORT_FastSlewRate,
        kPORT_PassiveFilterDisable, kPORT_OpenDrainDisable, kPORT_HighDriveStrength,
        kPORT_MuxAlt8, kPORT_InputBufferEnable, kPORT_InputNormal, kPORT_UnlockRegister,
    };
    PORT_SetPinConfig(PORT4, 0U, &sai_cfg);
    PORT_SetPinConfig(PORT4, 1U, &sai_cfg);
    PORT_SetPinConfig(PORT4, 2U, &sai_cfg);
}

void SAI1_RX_InitPins(void)
{
    CLOCK_EnableClock(kCLOCK_Port0);
    const port_pin_config_t sai_cfg = {
        kPORT_PullDisable, kPORT_LowPullResistor, kPORT_FastSlewRate,
        kPORT_PassiveFilterDisable, kPORT_OpenDrainDisable, kPORT_HighDriveStrength,
        kPORT_MuxAlt8, kPORT_InputBufferEnable, kPORT_InputNormal, kPORT_UnlockRegister,
    };
    PORT_SetPinConfig(PORT0, 0U, &sai_cfg);
    PORT_SetPinConfig(PORT0, 1U, &sai_cfg);
    PORT_SetPinConfig(PORT0, 2U, &sai_cfg);
}
