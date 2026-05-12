/*
 * IchiPing — Servo driver abstraction.
 *
 * Pick one backend at build time:
 *
 *     -D SERVO_BACKEND_PCA9685       (default; NXP chip, Adafruit-815 etc.)
 *     -D SERVO_BACKEND_LU9685_I2C    (LU9685-20CU, 20 channels, ESPEasy-style)
 *
 * In MCUXpresso for VS Code add the symbol to
 *     Project > Properties > C/C++ Build > Settings > Preprocessor > Defined symbols.
 *
 * Application code (e.g. main_servo_test.c) only calls the small common API
 * below; the underlying chip-specific module is selected here. Both backends
 * use the same FRDM-MCXN947 FC4 LPI2C bus, so no other code changes are
 * needed when switching.
 *
 * Differences worth noting (do NOT abstract these away):
 *   - PCA9685 : 16 channels, I²C 0x40, per-channel min/max tick calibration.
 *   - LU9685  : 20 channels, I²C 0x00..0x1F (jumper), no calibration needed —
 *               the chip internally maps angle byte → 0.5..2.5 ms pulse.
 */

#ifndef SERVO_DRIVER_H_
#define SERVO_DRIVER_H_

#include <stdint.h>

#include "fsl_common.h"
#include "fsl_lpi2c.h"

/* ---- Backend selection ---- */

#if !defined(SERVO_BACKEND_PCA9685) && !defined(SERVO_BACKEND_LU9685_I2C)
#  define SERVO_BACKEND_PCA9685 1   /* default: matches the original BOM */
#endif

#if defined(SERVO_BACKEND_PCA9685) && defined(SERVO_BACKEND_LU9685_I2C)
#  error "Define exactly one of SERVO_BACKEND_PCA9685 / SERVO_BACKEND_LU9685_I2C"
#endif

#if defined(SERVO_BACKEND_PCA9685)
#  include "pca9685.h"
   typedef pca9685_t servo_driver_t;
#  define SERVO_BACKEND_NAME      "PCA9685"
#  define SERVO_CHANNEL_COUNT     16u
#  define SERVO_DEFAULT_ADDR      PCA9685_DEFAULT_ADDR
#  define SERVO_DEFAULT_FREQ_HZ   50.0f
#elif defined(SERVO_BACKEND_LU9685_I2C)
#  include "lu9685.h"
   typedef lu9685_t servo_driver_t;
#  define SERVO_BACKEND_NAME      "LU9685"
#  define SERVO_CHANNEL_COUNT     20u
#  define SERVO_DEFAULT_ADDR      LU9685_DEFAULT_ADDR
#  define SERVO_DEFAULT_FREQ_HZ   50.0f
#endif

/* ---- Common API ---- */

static inline status_t servo_init(servo_driver_t *dev,
                                  LPI2C_Type *base, uint8_t addr,
                                  float freq_hz) {
#if defined(SERVO_BACKEND_PCA9685)
    return pca9685_init(dev, base, addr, freq_hz);
#else
    return lu9685_init(dev, base, addr, freq_hz);
#endif
}

static inline status_t servo_set_deg(servo_driver_t *dev,
                                     uint8_t ch, float deg) {
#if defined(SERVO_BACKEND_PCA9685)
    return pca9685_set_servo_deg(dev, ch, deg);
#else
    return lu9685_set_servo_deg(dev, ch, deg);
#endif
}

/* Set the first `n` channels in a single transaction where supported
 * (LU9685 uses one 21-byte bulk write; PCA9685 falls back to n separate
 * 4-byte writes). IchiPing typically calls this with n=5. */
static inline status_t servo_set_first_n_deg(servo_driver_t *dev,
                                             const float *deg, uint8_t n) {
#if defined(SERVO_BACKEND_PCA9685)
    /* pca9685_set_all_servo_deg fixed at 5 channels; loop for arbitrary n. */
    for (uint8_t i = 0; i < n; i++) {
        status_t s = pca9685_set_servo_deg(dev, i, deg[i]);
        if (s != kStatus_Success) return s;
    }
    return kStatus_Success;
#else
    return lu9685_set_all_servo_deg(dev, deg, n);
#endif
}

static inline status_t servo_all_off(servo_driver_t *dev) {
#if defined(SERVO_BACKEND_PCA9685)
    return pca9685_all_off(dev);
#else
    return lu9685_all_off(dev);
#endif
}

#endif /* SERVO_DRIVER_H_ */
