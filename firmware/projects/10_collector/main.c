/*
 * IchiPing — labelled training-data collector firmware.
 *
 * Bidirectional protocol on the OpenSDA UART (921600 8N1):
 *
 *   PC → MCU    ASCII command lines (LF-terminated)
 *                  SET window 32000
 *                  SET rate 16000
 *                  SET tone chirp        (or tone200|tone1k|tone5k|silence)
 *                  SET repeats 20
 *                  SET label door_open
 *                  GET
 *                  START
 *                  STOP
 *                  PING
 *
 *   MCU → PC    ASCII reply lines prefixed "OK "/"ERR "/"INFO " for every
 *               command, plus binary ICHP frames during a run. The PC
 *               distinguishes by scanning for the "ICHP" magic.
 *
 * The frame header servo_deg[5] field is repurposed for collection meta:
 *     servo_deg[0] = window_samples (cast to float)
 *     servo_deg[1] = rate_hz
 *     servo_deg[2] = tone enum
 *     servo_deg[3] = repeat index (0..repeats-1)
 *     servo_deg[4] = repeats total
 * (servo angles are not relevant for data collection — labels are sent as
 * ASCII "INFO label=..." lines just before each START.)
 *
 * See firmware/shared/include/ichp_cmd.h for the parser and
 * pc/collector_client.py for the matching PC driver.
 */

#include "pin_mux.h"
#include "clock_config.h"
#include "board.h"
#include "fsl_debug_console.h"
#include "fsl_lpuart.h"
#include "fsl_sai.h"                /* for SAI1 peripheral instance macro */

#include "sai_mic.h"
#include "sai_speaker.h"
#include "dummy_audio.h"
#include "ichiping_frame.h"
#include "ichp_cmd.h"
#include "app.h"

extern void BOARD_InitHardware(void);   /* defined in cm33_core0/hardware_init.c */

#include <math.h>
#include <string.h>

#define COLL_MAX_WINDOW   48000u   /* 3 s @ 16 kHz upper bound */
#define COLL_BAUD         921600u

static int16_t s_audio  [COLL_MAX_WINDOW];
static int16_t s_excite [COLL_MAX_WINDOW];
static uint8_t s_tx_buf [ICHP_HEADER_SIZE
                         + COLL_MAX_WINDOW * sizeof(int16_t)
                         + ICHP_CRC_SIZE];

static volatile uint32_t s_uptime_ms = 0;
void SysTick_Handler(void) { s_uptime_ms++; }
static void systick_init_1ms(void) { (void)SysTick_Config(SystemCoreClock / 1000u); }
static void delay_ms(uint32_t ms) {
    uint32_t end = s_uptime_ms + ms;
    while ((int32_t)(s_uptime_ms - end) < 0) { __WFI(); }
}

static void uart_io_init(void)
{
    lpuart_config_t cfg;
    LPUART_GetDefaultConfig(&cfg);
    cfg.baudRate_Bps = COLL_BAUD;
    cfg.enableTx     = true;
    cfg.enableRx     = true;       /* required: this is the bidirectional path */
    LPUART_Init(BOARD_DEBUG_UART_BASEADDR, &cfg, BOARD_DEBUG_UART_CLK_FREQ);
}

static void uart_send_line(const char *s)
{
    while (*s) {
        LPUART_WriteByteBlocking(BOARD_DEBUG_UART_BASEADDR, (uint8_t)*s++);
    }
    LPUART_WriteByteBlocking(BOARD_DEBUG_UART_BASEADDR, (uint8_t)'\r');
    LPUART_WriteByteBlocking(BOARD_DEBUG_UART_BASEADDR, (uint8_t)'\n');
}

static int uart_try_read(uint8_t *b)
{
    if ((LPUART_GetStatusFlags(BOARD_DEBUG_UART_BASEADDR) & kLPUART_RxDataRegFullFlag) != 0u) {
        *b = LPUART_ReadByte(BOARD_DEBUG_UART_BASEADDR);
        return 1;
    }
    return 0;
}

static void render_excitation(int16_t *out, size_t n, uint32_t fs, ichp_tone_t tone)
{
    switch (tone) {
    case ICHP_TONE_CHIRP:
        dummy_audio_seed(0xC4C4C4C4u);
        dummy_audio_generate(out, n, fs);
        break;
    case ICHP_TONE_SILENCE:
        for (size_t i = 0; i < n; i++) out[i] = 0;
        break;
    case ICHP_TONE_200HZ:
    case ICHP_TONE_1KHZ:
    case ICHP_TONE_5KHZ: {
        float f =  200.0f;
        if (tone == ICHP_TONE_1KHZ) f = 1000.0f;
        if (tone == ICHP_TONE_5KHZ) f = 5000.0f;
        const float w = 6.28318530718f * f / (float)fs;
        for (size_t i = 0; i < n; i++)
            out[i] = (int16_t)(20000.0f * sinf(w * (float)i));
        break;
    }
    }
}

static void run_one(sai_mic_t *mic, sai_speaker_t *spk,
                    const ichp_cmd_state_t *cfg,
                    uint16_t seq, uint32_t idx, uint32_t total)
{
    /* Excite (optional) → record. Both blocking. */
    render_excitation(s_excite, cfg->window_samples, cfg->sample_rate_hz, cfg->tone);
    if (cfg->tone != ICHP_TONE_SILENCE) {
        sai_speaker_play_blocking(spk, s_excite, cfg->window_samples);
    }
    sai_mic_record_blocking(mic, s_audio, cfg->window_samples);

    float meta[5];
    meta[0] = (float)cfg->window_samples;
    meta[1] = (float)cfg->sample_rate_hz;
    meta[2] = (float)cfg->tone;
    meta[3] = (float)idx;
    meta[4] = (float)total;

    size_t framed = ichp_pack_frame(
        s_tx_buf, sizeof(s_tx_buf),
        seq, s_uptime_ms,
        (uint16_t)cfg->sample_rate_hz,
        (uint16_t)cfg->window_samples,
        meta, s_audio);
    if (framed > 0) {
        LPUART_WriteBlocking(BOARD_DEBUG_UART_BASEADDR, s_tx_buf, framed);
    }
}

int main(void)
{
    BOARD_InitHardware();
    systick_init_1ms();
    uart_io_init();

    /* Init SAI for both directions; tone=silence simply skips speaker. */
    sai_mic_t mic;     sai_mic_config_t mcfg = {
        .sai_base = BOARD_MIC_SAI_BASE, .sai_clk_hz = BOARD_MIC_SAI_CLK_FREQ,
        .sample_rate_hz = 16000u, .bit_depth = 16,
    };
    sai_speaker_t spk; sai_speaker_config_t scfg = {
        .sai_base = BOARD_SPK_SAI_BASE, .sai_clk_hz = BOARD_SPK_SAI_CLK_FREQ,
        .sample_rate_hz = 16000u,
    };
    sai_mic_init(&mic, &mcfg);
    sai_speaker_init(&spk, &scfg);

    ichp_cmd_state_t cfg;
    ichp_cmd_init_defaults(&cfg);

    uart_send_line("INFO ichiping 10_collector ready");
    uart_send_line("INFO send 'PING' to verify link, 'GET' for current config");

    char reply[ICHP_CMD_LINE_MAX];
    uint16_t seq = 0;
    for (;;) {
        uint8_t b;
        if (uart_try_read(&b)) {
            if (ichp_cmd_feed_byte(&cfg, b, reply)) {
                if (reply[0]) uart_send_line(reply);
            }
        }

        if (cfg.start_requested) {
            cfg.start_requested = 0;
            cfg.stop_requested  = 0;
            /* Re-init SAI in case rate changed. */
            mcfg.sample_rate_hz = cfg.sample_rate_hz;
            scfg.sample_rate_hz = cfg.sample_rate_hz;
            sai_mic_init(&mic, &mcfg);
            sai_speaker_init(&spk, &scfg);

            uart_send_line("INFO START");
            char tmp[ICHP_CMD_LINE_MAX];
            const char *l = cfg.label[0] ? cfg.label : "(no label)";
            size_t i = 0;
            const char *p = "INFO label=";
            while (*p && i < ICHP_CMD_LINE_MAX-1) tmp[i++] = *p++;
            while (*l && i < ICHP_CMD_LINE_MAX-1) tmp[i++] = *l++;
            tmp[i] = 0;
            uart_send_line(tmp);

            for (uint32_t k = 0; k < cfg.repeats; k++) {
                /* Allow STOP mid-run by polling RX. */
                while (uart_try_read(&b)) {
                    if (ichp_cmd_feed_byte(&cfg, b, reply) && reply[0]) {
                        uart_send_line(reply);
                    }
                }
                if (cfg.stop_requested) break;
                seq++;
                run_one(&mic, &spk, &cfg, seq, k, cfg.repeats);
                delay_ms(300);
            }
            uart_send_line(cfg.stop_requested ? "INFO ABORTED" : "INFO DONE");
            cfg.stop_requested = 0;
        }
    }
}
