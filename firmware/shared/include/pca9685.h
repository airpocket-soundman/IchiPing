/*
 * PCA9685 — 16-channel 12-bit PWM driver, minimal subset for SG90 servos.
 *
 * Datasheet ref:
 *   - PCA9685 NXP — internal oscillator 25 MHz, 12-bit (4096) per cycle.
 *   - PRE_SCALE = round(25e6 / (4096 * pwm_freq_hz)) - 1
 *     → at 50 Hz this is 121 (per data sheet §7.3.5).
 *
 * Wiring on IchiPing (see hardware/wiring.md §2.5):
 *   I²C bus  : FC4 (LPI2C4 on FRDM-MCXN947, shared with SSD1306 / BMP585)
 *   Address  : 0x40 (default A0..A5 grounded)
 *   PWM0..4  : window a, window b, window c, door AB, door BC
 *
 * SG90 calibration is conservative — adjust SG90_MIN_TICK / SG90_MAX_TICK
 * after measuring real servos. Out-of-range values bend the horn arm.
 */

#ifndef PCA9685_H_
#define PCA9685_H_

#include <stdint.h>
#include <stddef.h>

#include "fsl_common.h"
#include "fsl_lpi2c.h"

#define PCA9685_DEFAULT_ADDR   0x40u
#define PCA9685_NUM_CHANNELS   16u

/* 12-bit PWM ticks @ 50 Hz (one frame = 4096 ticks ≈ 20 ms).
 *   tick = pulse_width_ms / (20 ms / 4096)
 *   0 deg  ≈ 0.5 ms  → 102
 *   180 deg ≈ 2.5 ms → 512
 * We pad slightly inwards to avoid mechanical stall on SG90 endpoints. */
#define PCA9685_SG90_MIN_TICK  130u
#define PCA9685_SG90_MAX_TICK  510u

typedef struct {
    LPI2C_Type *base;
    uint8_t     addr;          /* 7-bit I²C address */
} pca9685_t;

/* Wake the device and configure PWM frequency (Hz). Returns kStatus_Success
 * on I²C ACK. After init the auto-increment flag is set so set_pwm() can
 * write 4 consecutive registers per channel in one transaction. */
status_t pca9685_init(pca9685_t *dev,
                      LPI2C_Type *base, uint8_t addr,
                      float pwm_freq_hz);

/* Write the raw ON/OFF tick pair for one channel (0..15).
 * on_tick / off_tick are 12-bit (0..4095). For a normal servo pulse the
 * convention is on_tick=0 and off_tick = pulse_width_in_ticks. */
status_t pca9685_set_pwm(pca9685_t *dev,
                         uint8_t ch,
                         uint16_t on_tick, uint16_t off_tick);

/* Set one SG90 channel to an angle in [0..180]. Values outside the range
 * are clamped to avoid driving the servo into its mechanical stops. */
status_t pca9685_set_servo_deg(pca9685_t *dev, uint8_t ch, float deg);

/* Convenience: update all 5 IchiPing servos at once. The internal channel
 * order matches §2.5 wiring: window a / b / c, door AB, door BC. */
status_t pca9685_set_all_servo_deg(pca9685_t *dev, const float deg[5]);

/* Park: send 0% duty on all channels (servo coast, low current draw). */
status_t pca9685_all_off(pca9685_t *dev);

#endif /* PCA9685_H_ */
