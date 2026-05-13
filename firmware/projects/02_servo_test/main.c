/*
 * IchiPing — Servo test firmware (MCUXpresso project skeleton)
 *
 * Sweeps 5 SG90 servos through whichever supported driver is selected at
 * build time:
 *
 *   -D SERVO_BACKEND_PCA9685     (default; 16-ch, NXP chip, I²C 0x40)
 *   -D SERVO_BACKEND_LU9685_I2C  (20-ch LU9685-20CU, I²C 0x00..0x1F)
 *
 * Sequence per cycle:
 *
 *   Phase 1 — synchronised waypoints
 *     all → 0°  →  hold 1.0 s
 *     all → 90° →  hold 1.0 s
 *     all → 180°→  hold 1.0 s
 *     all → 0°  →  hold 0.5 s
 *
 *   Phase 2 — solo sweep (each channel in isolation)
 *     for ch in {a, b, c, AB, BC}:
 *         ch → 180° hold 1.0 s
 *         ch → 0°   hold 0.5 s
 *
 *   (repeat from phase 1)
 *
 * Channel map (matches hardware/wiring.md §2.5):
 *   ch0 = window a   ch1 = window b   ch2 = window c
 *   ch3 = door AB    ch4 = door BC
 *
 * Target board: FRDM-MCXN947
 * Toolchain  : MCUXpresso IDE 11.9+ / MCUXpresso for VS Code
 *
 * Wiring (hardware/wiring.md):
 *   D14 SDA          → driver SDA
 *   D15 SCL          → driver SCL          (4.7 kΩ pull-ups, one set)
 *   3V3              → driver VCC          (logic)
 *   GND              → driver GND          (must tie servo external GND here)
 *   external 5V rail → driver V+           (NOT the FRDM 3V3 — use 5V)
 *   PWM0..4          → SG90 signal lines
 *
 * Build:
 *   1) New > Import SDK example > frdmmcxn947 >
 *        driver_examples/lpi2c/polling_master into a fresh workspace.
 *   2) Replace source/main.c with this file (or add and exclude original).
 *   3) Depending on the backend, add ONE of:
 *        - source/pca9685.c   (when SERVO_BACKEND_PCA9685, default)
 *        - source/lu9685.c    (when SERVO_BACKEND_LU9685_I2C)
 *      Add ../firmware/include to "C/C++ Build > Settings > Includes".
 *   4) If using the LU9685 backend, add the symbol
 *        SERVO_BACKEND_LU9685_I2C
 *      under "Project > Properties > C/C++ Build > Settings > Preprocessor
 *      > Defined symbols" so the abstraction in servo_driver.h picks it.
 *   5) Pin Mux: confirm D14/D15 are routed to LPI2C4_SDA / LPI2C4_SCL.
 *   6) Build & flash via OpenSDA; observe at 115200 8N1 on the OpenSDA VCP.
 *
 * Safety:
 *   - Driver V+ must be a dedicated 5 V rail with ≥ 1000 µF bulk cap near
 *     the chip. SG90 inrush spikes will brown out the MCU otherwise.
 *   - On reset every PWM channel parks to its idle state (servo coasts).
 */

#include "pin_mux.h"
#include "clock_config.h"
#include "board.h"
#include "fsl_debug_console.h"
#include "fsl_lpi2c.h"

#include "servo_driver.h"

extern void BOARD_InitHardware(void);

/* ----- Configuration ----- */

#ifndef SERVO_I2C_BASE
#define SERVO_I2C_BASE     LPI2C4                       /* FC4 = Arduino D14/D15 */
#endif
#ifndef SERVO_I2C_CLK_FREQ
#define SERVO_I2C_CLK_FREQ CLOCK_GetLPFlexCommClkFreq(4) /* adjust if SDK API name differs */
#endif
#define SERVO_I2C_BAUD     100000U
#define SERVO_CH_COUNT     5

/* ----- SysTick uptime ----- */

static volatile uint32_t s_uptime_ms = 0;
void SysTick_Handler(void) { s_uptime_ms++; }

static void systick_init_1ms(void) {
    (void)SysTick_Config(SystemCoreClock / 1000u);
}

static void delay_ms(uint32_t ms) {
    uint32_t end = s_uptime_ms + ms;
    while ((int32_t)(s_uptime_ms - end) < 0) { __WFI(); }
}

/* ----- I²C bring-up ----- */

static void i2c_master_init(void) {
    lpi2c_master_config_t cfg;
    LPI2C_MasterGetDefaultConfig(&cfg);
    cfg.baudRate_Hz = SERVO_I2C_BAUD;
    LPI2C_MasterInit(SERVO_I2C_BASE, &cfg, SERVO_I2C_CLK_FREQ);
}

/* ----- Application ----- */

static const char *const SERVO_NAMES[SERVO_CH_COUNT] = {
    "window a", "window b", "window c", "door AB", "door BC",
};

int main(void) {
    BOARD_InitHardware();
    systick_init_1ms();
    i2c_master_init();

    PRINTF("\r\nIchiPing servo test  ─  backend=%s  addr=0x%02X  freq=%uHz  chans=%u\r\n",
           SERVO_BACKEND_NAME,
           (unsigned)SERVO_DEFAULT_ADDR,
           (unsigned)SERVO_DEFAULT_FREQ_HZ,
           (unsigned)SERVO_CH_COUNT);

    servo_driver_t servo;
    status_t s = servo_init(&servo, SERVO_I2C_BASE,
                            SERVO_DEFAULT_ADDR, SERVO_DEFAULT_FREQ_HZ);
    if (s != kStatus_Success) {
        PRINTF("FAIL: servo_init returned status=%d\r\n", (int)s);
        PRINTF("Check: I²C wiring, pull-ups, V+ 5V rail, slave address.\r\n");
        (void)servo_all_off(&servo);
        for (;;) { __WFI(); }
    }
    PRINTF("OK: %s initialised\r\n", SERVO_BACKEND_NAME);

    /* Park servos before the first move so the horn does not jump. */
    float park[SERVO_CH_COUNT] = {0.0f, 0.0f, 0.0f, 0.0f, 0.0f};
    (void)servo_set_first_n_deg(&servo, park, SERVO_CH_COUNT);
    delay_ms(500);

    uint32_t cycle = 0;
    for (;;) {
        cycle++;
        PRINTF("\r\n[cycle %u] phase 1: synchronised waypoints\r\n",
               (unsigned)cycle);

        float all0[SERVO_CH_COUNT]   = {  0.0f,   0.0f,   0.0f,   0.0f,   0.0f};
        float all90[SERVO_CH_COUNT]  = { 90.0f,  90.0f,  90.0f,  90.0f,  90.0f};
        float all180[SERVO_CH_COUNT] = {180.0f, 180.0f, 180.0f, 180.0f, 180.0f};

        (void)servo_set_first_n_deg(&servo, all0,   SERVO_CH_COUNT);
        PRINTF("  all -> 0°\r\n");
        delay_ms(1000);

        (void)servo_set_first_n_deg(&servo, all90,  SERVO_CH_COUNT);
        PRINTF("  all -> 90°\r\n");
        delay_ms(1000);

        (void)servo_set_first_n_deg(&servo, all180, SERVO_CH_COUNT);
        PRINTF("  all -> 180°\r\n");
        delay_ms(1000);

        (void)servo_set_first_n_deg(&servo, all0,   SERVO_CH_COUNT);
        PRINTF("  all -> 0°  (rest)\r\n");
        delay_ms(500);

        PRINTF("[cycle %u] phase 2: solo sweep\r\n", (unsigned)cycle);
        for (int ch = 0; ch < SERVO_CH_COUNT; ch++) {
            PRINTF("  ch%d (%s) -> 180°\r\n", ch, SERVO_NAMES[ch]);
            (void)servo_set_deg(&servo, (uint8_t)ch, 180.0f);
            delay_ms(1000);

            PRINTF("  ch%d (%s) -> 0°\r\n", ch, SERVO_NAMES[ch]);
            (void)servo_set_deg(&servo, (uint8_t)ch, 0.0f);
            delay_ms(500);
        }
    }
}
