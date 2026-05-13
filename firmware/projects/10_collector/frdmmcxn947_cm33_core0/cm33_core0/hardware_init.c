#include "pin_mux.h"
#include "clock_config.h"
#include "board.h"
#include "app.h"

void BOARD_InitHardware(void)
{
    CLOCK_SetClkDiv(kCLOCK_DivFlexcom4Clk, 1u);
    CLOCK_AttachClk(BOARD_DEBUG_UART_CLK_ATTACH);

    CLOCK_SetClkDiv(BOARD_SPK_SAI_CLK_DIV, 1u);
    CLOCK_AttachClk(BOARD_SPK_SAI_CLK_ATTACH);

    CLOCK_SetClkDiv(BOARD_MIC_SAI_CLK_DIV, 1u);
    CLOCK_AttachClk(BOARD_MIC_SAI_CLK_ATTACH);

    BOARD_InitBootPins();
    BOARD_InitBootClocks();
    BOARD_InitDebugConsole();
}
