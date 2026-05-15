/*
 * IchiPing — MAX98357A speaker test firmware.
 *
 * Plays five repeating test signals on SAI0 TX:
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
 *   LRC          SAI0_FS
 *   BCLK         SAI0_BCLK
 *   DIN          SAI0_TXD
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

#include "sai_speaker.h"
#include "app.h"

#include <math.h>

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

    PRINTF("\r\nIchiPing 07_speaker_test  --  MAX98357A on SAI0 TX @ %u Hz\r\n",
           (unsigned)SPK_SAMPLE_RATE);

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
        cycle++;
        PRINTF("\r\n[cycle %u]\r\n", (unsigned)cycle);

        PRINTF("  200 Hz tone\r\n");
        render_pure_tone(s_buffer, SPK_TONE_SAMP, SPK_SAMPLE_RATE, 200.0f, 0.6f);
        sai_speaker_play_blocking(&spk, s_buffer, SPK_TONE_SAMP);

        PRINTF("  1 kHz tone\r\n");
        render_pure_tone(s_buffer, SPK_TONE_SAMP, SPK_SAMPLE_RATE, 1000.0f, 0.6f);
        sai_speaker_play_blocking(&spk, s_buffer, SPK_TONE_SAMP);

        PRINTF("  5 kHz tone\r\n");
        render_pure_tone(s_buffer, SPK_TONE_SAMP, SPK_SAMPLE_RATE, 5000.0f, 0.4f);
        sai_speaker_play_blocking(&spk, s_buffer, SPK_TONE_SAMP);

        PRINTF("  chirp 200->6k (clean linear, 5 ms fade)\r\n");
        render_chirp(s_buffer, SPK_CHIRP_SAMP, SPK_SAMPLE_RATE, 200.0f, 6000.0f, 0.6f);
        sai_speaker_play_blocking(&spk, s_buffer, SPK_CHIRP_SAMP);

        PRINTF("  silence 1 s\r\n");
        render_silence(s_buffer, SPK_TONE_SAMP);
        sai_speaker_play_blocking(&spk, s_buffer, SPK_TONE_SAMP);

        delay_ms(500);
    }
}
