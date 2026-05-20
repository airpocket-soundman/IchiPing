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
 *   tick = pulse_width_ms / (20 ms / 4096)   (1 tick ≈ 4.88 µs)
 *   0 deg   ≈ 0.5 ms  → 102   (2.49 % duty)
 *   180 deg ≈ 2.7 ms  → 553   (13.50 % duty)
 * Lower bound matches the SG90 datasheet. Upper bound is widened past
 * the datasheet 2.5 ms to reach the mechanical end-stop on the actual
 * SG90 units used in this project (datasheet is conservative; real
 * units accept 2.6–2.8 ms). 1 deg ≈ 12.5 µs at this slope.
 * If your specific servo grinds at an endpoint, narrow that side by
 * 20–30 ticks. */
#define PCA9685_SG90_MIN_TICK  102u
#define PCA9685_SG90_MAX_TICK  553u

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

/* Set one SG90 channel to an angle in [0..180]. The full range maps to
 * SG90_MIN..MAX_TICK = 0.5..2.5 ms (full SG90 spec). Values out of range
 * are clamped. */
status_t pca9685_set_servo_deg(pca9685_t *dev, uint8_t ch, float deg);

/* Convenience: update all 5 IchiPing servos at once. The internal channel
 * order matches §2.5 wiring: window a / b / c, door AB, door BC. */
status_t pca9685_set_all_servo_deg(pca9685_t *dev, const float deg[5]);

/* Park one channel: assert the full-OFF bit so the output sits at 0% duty
 * (servo coasts, no holding torque). Data sheet §7.3.3 fig 12. */
status_t pca9685_set_off(pca9685_t *dev, uint8_t ch);

/* Park: send 0% duty on all channels (servo coast, low current draw). */
status_t pca9685_all_off(pca9685_t *dev);

#endif /* PCA9685_H_ */
