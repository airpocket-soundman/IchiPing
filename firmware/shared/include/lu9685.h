/*
 * LU9685 (LU9685-20CU) — 20-channel servo controller, I²C variant.
 *
 * Protocol (reverse-engineered from ESPEasy P178 source):
 *   I²C address       : 0x00 by default; hardware-configurable in 0x00..0x1F
 *                       via on-board jumpers / solder pads. Note that 0x00 is
 *                       the I²C General Call address — most masters tolerate
 *                       it but some libraries reject it; consult the seller's
 *                       sheet and reassign with the jumpers if needed.
 *   Channel count     : 20 (vs 16 on the PCA9685)
 *   Reg 0xFC, 16-bit  : PWM frequency in Hz (big-endian, 20..300)
 *   Reg 0xFD, 20 bytes: bulk write all channel angles in one transaction
 *   Reg = pin (0..19) : write 1 byte = angle 0..180 (or 0xFF to disable)
 *
 * The LU9685 maps the angle byte (0..180°) to a 0.5–2.5 ms pulse internally,
 * so no per-servo MIN/MAX tick calibration is required on the host side —
 * one of the few practical wins it has over the PCA9685.
 *
 * IchiPing only uses the first 5 channels (window a/b/c, door AB, door BC);
 * the remaining 15 are left disabled in bulk-write mode.
 */

#ifndef LU9685_H_
#define LU9685_H_

#include <stdint.h>
#include <stddef.h>

#include "fsl_common.h"
#include "fsl_lpi2c.h"

#define LU9685_DEFAULT_ADDR    0x00u
#define LU9685_NUM_CHANNELS    20u
#define LU9685_DISABLED        0xFFu

#define LU9685_REG_FREQ        0xFCu
#define LU9685_REG_BULK        0xFDu
/* Per-pin register = pin number (0..19). */

#define LU9685_DEFAULT_FREQ_HZ 50u
#define LU9685_MIN_FREQ_HZ     20u
#define LU9685_MAX_FREQ_HZ     300u

typedef struct {
    LPI2C_Type *base;
    uint8_t     addr;
} lu9685_t;

/* Initialise the controller and program the PWM frequency. Returns
 * kStatus_Success on I²C ACK. */
status_t lu9685_init(lu9685_t *dev,
                     LPI2C_Type *base, uint8_t addr,
                     float freq_hz);

/* Set one channel (0..19) to an angle 0..180°. Values out of range clamp. */
status_t lu9685_set_servo_deg(lu9685_t *dev, uint8_t ch, float deg);

/* Update the first `n` channels in a single 20-byte bulk write. Channels
 * beyond `n` are sent as LU9685_DISABLED (no PWM). */
status_t lu9685_set_all_servo_deg(lu9685_t *dev, const float *deg, uint8_t n);

/* Disable PWM on all 20 channels (servo coasts, no holding torque). */
status_t lu9685_all_off(lu9685_t *dev);

#endif /* LU9685_H_ */
