/*
 * IchiPing — MAX98357A speaker test firmware.
 *
 * Plays five repeating test signals on SAI1 TX:
 *   1. 200 Hz pure tone (1.0 s)            — low-end driver check
 *   2. 1 kHz pure tone   (1.0 s)            — mid-band reference
 *   3. 5 kHz pure tone   (1.0 s)            — high-end / aliasing check
 *   4. Linear chirp 200 Hz → 8 kHz (2.0 s)  — same signal used by IchiPing
 *                                            inference, listenable canary
 *   5. Silence           (1.0 s)            — confirms class-D shutdown
 *
 * Validates SAI TX wiring + MAX98357A behaviour without any PC tooling
 * (it is a literal listening test).
 *
 * Wiring (FRDM-MCXN947 Arduino headers, see hardware/wiring.md §2):
 *   MAX98357A    FRDM-MCXN947
 *   VIN          5V external (NOT 3V3 — needs the 5 V rail for full SPL)
 *   GND          GND
 *   LRC          SAI1_TX_FS   (J1.11 / P3_17 Alt10)
 *   BCLK         SAI1_TX_BCLK (J1.1  / P3_16 Alt10)
 *   DIN          SAI1_TXD0    (J1.5  / P3_20 Alt10)
 *   GAIN         floating (default 9 dB) or tied per datasheet table
 *   SD           VIN (always on); pull low to mute via GPIO if desired
 *
 * Build flow is identical to 06_mic_test, but using the SAI TX side.
 */

#include "pin_mux.h"
#include "clock_config.h"
#include "board.h"
#include "fsl_debug_console.h"
#include "fsl_sai.h"                /* for SAI1 peripheral instance macro */
#include "fsl_gpio.h"               /* SW3 GPIO + interrupt (MCXN947 keeps the
                                     * IRQC bits on GPIO, not PORT) */

#include "sai_speaker.h"
#include "app.h"

#include <math.h>
#include <stdbool.h>

extern void BOARD_InitHardware(void);   /* defined in cm33_core0/hardware_init.c */

#define SPK_SAMPLE_RATE   16000u
#define SPK_GAIN          0.15f   /* master software attenuation (~-16 dB from full scale).
                                   * Going much lower exposes MAX98357A class-D idle noise
                                   * (gritty hiss). For further quietness, use the GAIN pin. */
#define SPK_TONE_MS       1000u
#define SPK_TONE_SAMP     ((SPK_SAMPLE_RATE * SPK_TONE_MS) / 1000u)
#define SPK_CHIRP_MS      2000u
#define SPK_CHIRP_SAMP    ((SPK_SAMPLE_RATE * SPK_CHIRP_MS) / 1000u)

static int16_t s_buffer[SPK_CHIRP_SAMP];   /* big enough for the longest phase */

static volatile uint32_t s_uptime_ms = 0;
void SysTick_Handler(void) { s_uptime_ms++; }
static void systick_init_1ms(void) { (void)SysTick_Config(SystemCoreClock / 1000u); }
static void delay_ms(uint32_t ms) {
    uint32_t end = s_uptime_ms + ms;
    while ((int32_t)(s_uptime_ms - end) < 0) { __WFI(); }
}

static void render_pure_tone(int16_t *out, size_t n, uint32_t fs, float freq_hz, float amp)
{
    const float two_pi_dt = 6.28318530718f * freq_hz / (float)fs;
    for (size_t i = 0; i < n; i++) {
        float s = amp * sinf(two_pi_dt * (float)i);
        if (s > 1.0f) { s = 1.0f; }
        if (s < -1.0f) { s = -1.0f; }
        out[i] = (int16_t)(s * 30000.0f * SPK_GAIN);
    }
}

static void render_silence(int16_t *out, size_t n)
{
    for (size_t i = 0; i < n; i++) out[i] = 0;
}

/* SW3 toggles the cycle on/off. The PORT interrupt fires on every falling
 * edge of the active-low button line; the ISR stamps a "press pending"
 * flag with 200 ms software debounce (the on-board hardware passive
 * filter on PORT0 only takes the edge of mechanical noise — multi-bounce
 * still slips through occasionally without this). Boot state is
 * "stopped" so the board never blasts audio unexpectedly when
 * power-cycled. Using an interrupt instead of polling means presses are
 * captured even while sai_speaker_play_blocking is busy-waiting on
 * the TX FIFO. */
static volatile bool     s_cycle_running   = false;
static volatile bool     s_button_event    = false;
static volatile uint32_t s_last_button_ms  = 0;

void GPIO00_IRQHandler(void)
{
    uint32_t flags = GPIO_GpioGetInterruptFlags(BOARD_USER_BUTTON_GPIO);
    if (flags & (1u << BOARD_USER_BUTTON_PIN)) {
        GPIO_GpioClearInterruptFlags(BOARD_USER_BUTTON_GPIO,
                                     1u << BOARD_USER_BUTTON_PIN);
        if ((s_uptime_ms - s_last_button_ms) > 200u) {
            s_button_event   = true;
            s_last_button_ms = s_uptime_ms;
        }
    }
    SDK_ISR_EXIT_BARRIER;
}

static void consume_button_toggle(void)
{
    if (s_button_event) {
        s_button_event  = false;
        s_cycle_running = !s_cycle_running;
        PRINTF("[" BOARD_USER_BUTTON_NAME "] cycle %s\r\n",
               s_cycle_running ? "RUNNING" : "PAUSED");
    }
}

/* Clean linear chirp f0 -> f1 across the whole buffer, with short
 * raised-cosine fade-in/out to suppress click artefacts. Keep f1 well
 * below Fs/2 (here 6 kHz at 16 kHz sample rate) to avoid aliasing at
 * the upper end — going right to Nyquist sounds gritty. */
static void render_chirp(int16_t *out, size_t n, uint32_t fs,
                         float f0_hz, float f1_hz, float amp)
{
    const float two_pi = 6.28318530718f;
    const float dur    = (float)n / (float)fs;
    const float k      = (f1_hz - f0_hz) / dur;
    const size_t fade  = (size_t)(0.005f * (float)fs);   /* 5 ms */

    for (size_t i = 0; i < n; i++) {
        float t     = (float)i / (float)fs;
        float phase = two_pi * (f0_hz * t + 0.5f * k * t * t);
        float env   = 1.0f;
        if (i < fade)       env = 0.5f * (1.0f - cosf(3.14159265f * (float)i / (float)fade));
        else if (i > n - fade) env = 0.5f * (1.0f - cosf(3.14159265f * (float)(n - i) / (float)fade));
        float s = amp * env * sinf(phase);
        out[i] = (int16_t)(s * 30000.0f * SPK_GAIN);
    }
}

int main(void)
{
    BOARD_InitHardware();
    systick_init_1ms();

    /* SW3 GPIO direction = input. Pin mux + internal pull-up + passive
     * filter were set in BOARD_InitBootPins() / SW3_InitPins(). */
    const gpio_pin_config_t btn_in = { kGPIO_DigitalInput, 0 };
    GPIO_PinInit(BOARD_USER_BUTTON_GPIO, BOARD_USER_BUTTON_PIN, &btn_in);

    /* Wire SW3 as a falling-edge interrupt on GPIO0 pin 6 and unmask
     * the shared GPIO0 vector. On MCXN947 the IRQC config sits on the
     * GPIO register block (ICR[pin]), not on the PORT block. Handler
     * below is GPIO00_IRQHandler. */
    GPIO_SetPinInterruptConfig(BOARD_USER_BUTTON_GPIO, BOARD_USER_BUTTON_PIN,
                               kGPIO_InterruptFallingEdge);
    EnableIRQ(GPIO00_IRQn);

    PRINTF("\r\nIchiPing 07_speaker_test  --  MAX98357A on SAI1 TX @ %u Hz\r\n",
           (unsigned)SPK_SAMPLE_RATE);
    PRINTF("Press " BOARD_USER_BUTTON_NAME " to start the cycle; press again to pause.\r\n");

    sai_speaker_t spk;
    sai_speaker_config_t cfg = {
        .sai_base       = BOARD_SPK_SAI_BASE,
        .sai_clk_hz     = BOARD_SPK_SAI_CLK_FREQ,
        .sample_rate_hz = SPK_SAMPLE_RATE,
    };
    status_t s = sai_speaker_init(&spk, &cfg);
    if (s != kStatus_Success) {
        PRINTF("FAIL: sai_speaker_init = %d\r\n", (int)s);
        for (;;) { __WFI(); }
    }

    uint32_t cycle = 0;
    for (;;) {
        /* Idle until SW3 toggles us on. The SAI framer keeps clocking
         * BCLK/FS continuously (FCONT=1) with TX FIFO drained to zeros,
         * so the MAX98357A just plays silence while we wait — no pops on
         * the start/stop transitions. */
        while (!s_cycle_running) {
            consume_button_toggle();
            __WFI();   /* wake on next interrupt (SysTick or SW3 falling edge) */
        }

        cycle++;
        PRINTF("\r\n[cycle %u]\r\n", (unsigned)cycle);

        PRINTF("  200 Hz tone\r\n");
        render_pure_tone(s_buffer, SPK_TONE_SAMP, SPK_SAMPLE_RATE, 200.0f, 0.6f);
        sai_speaker_play_blocking(&spk, s_buffer, SPK_TONE_SAMP);
        consume_button_toggle();
        if (!s_cycle_running) { continue; }

        PRINTF("  1 kHz tone\r\n");
        render_pure_tone(s_buffer, SPK_TONE_SAMP, SPK_SAMPLE_RATE, 1000.0f, 0.6f);
        sai_speaker_play_blocking(&spk, s_buffer, SPK_TONE_SAMP);
        consume_button_toggle();
        if (!s_cycle_running) { continue; }

        PRINTF("  5 kHz tone\r\n");
        render_pure_tone(s_buffer, SPK_TONE_SAMP, SPK_SAMPLE_RATE, 5000.0f, 0.4f);
        sai_speaker_play_blocking(&spk, s_buffer, SPK_TONE_SAMP);
        consume_button_toggle();
        if (!s_cycle_running) { continue; }

        PRINTF("  chirp 200->6k (clean linear, 5 ms fade)\r\n");
        render_chirp(s_buffer, SPK_CHIRP_SAMP, SPK_SAMPLE_RATE, 200.0f, 6000.0f, 0.6f);
        sai_speaker_play_blocking(&spk, s_buffer, SPK_CHIRP_SAMP);
        consume_button_toggle();
        if (!s_cycle_running) { continue; }

        PRINTF("  silence 1 s\r\n");
        render_silence(s_buffer, SPK_TONE_SAMP);
        sai_speaker_play_blocking(&spk, s_buffer, SPK_TONE_SAMP);
        consume_button_toggle();

        delay_ms(500);
    }
}
