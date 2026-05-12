#include "lu9685.h"

static status_t i2c_write_buf(lu9685_t *dev,
                              uint8_t reg, const uint8_t *buf, size_t n) {
    lpi2c_master_transfer_t xfer = {
        .flags          = (uint32_t)kLPI2C_TransferDefaultFlag,
        .slaveAddress   = (uint16_t)dev->addr,
        .direction      = kLPI2C_Write,
        .subaddress     = (uint32_t)reg,
        .subaddressSize = 1u,
        .data           = (uint8_t *)buf,
        .dataSize       = n,
    };
    return LPI2C_MasterTransferBlocking(dev->base, &xfer);
}

static status_t i2c_write_reg8(lu9685_t *dev, uint8_t reg, uint8_t val) {
    return i2c_write_buf(dev, reg, &val, 1u);
}

static uint8_t deg_to_byte(float deg) {
    if (deg < 0.0f)   return 0u;
    if (deg > 180.0f) return 180u;
    return (uint8_t)(deg + 0.5f);
}

status_t lu9685_init(lu9685_t *dev,
                     LPI2C_Type *base, uint8_t addr,
                     float freq_hz) {
    if (dev == NULL || base == NULL) return kStatus_InvalidArgument;

    int f = (int)(freq_hz + 0.5f);
    if (f < (int)LU9685_MIN_FREQ_HZ) f = (int)LU9685_MIN_FREQ_HZ;
    if (f > (int)LU9685_MAX_FREQ_HZ) f = (int)LU9685_MAX_FREQ_HZ;

    dev->base = base;
    dev->addr = addr;

    /* Big-endian 16-bit frequency to register 0xFC. ESPEasy uses the
     * "I2C_write16_reg" helper which transmits the high byte first. */
    uint8_t buf[2] = {
        (uint8_t)((f >> 8) & 0xFFu),
        (uint8_t)( f       & 0xFFu),
    };
    return i2c_write_buf(dev, LU9685_REG_FREQ, buf, sizeof(buf));
}

status_t lu9685_set_servo_deg(lu9685_t *dev, uint8_t ch, float deg) {
    if (dev == NULL) return kStatus_InvalidArgument;
    if (ch >= LU9685_NUM_CHANNELS) return kStatus_InvalidArgument;
    return i2c_write_reg8(dev, ch, deg_to_byte(deg));
}

status_t lu9685_set_all_servo_deg(lu9685_t *dev,
                                  const float *deg, uint8_t n) {
    if (dev == NULL || deg == NULL) return kStatus_InvalidArgument;
    if (n > LU9685_NUM_CHANNELS) n = (uint8_t)LU9685_NUM_CHANNELS;

    uint8_t buf[LU9685_NUM_CHANNELS];
    for (uint8_t i = 0; i < n; i++)                   buf[i] = deg_to_byte(deg[i]);
    for (uint8_t i = n; i < LU9685_NUM_CHANNELS; i++) buf[i] = LU9685_DISABLED;
    return i2c_write_buf(dev, LU9685_REG_BULK, buf, (size_t)LU9685_NUM_CHANNELS);
}

status_t lu9685_all_off(lu9685_t *dev) {
    if (dev == NULL) return kStatus_InvalidArgument;
    uint8_t buf[LU9685_NUM_CHANNELS];
    for (uint8_t i = 0; i < LU9685_NUM_CHANNELS; i++) buf[i] = LU9685_DISABLED;
    return i2c_write_buf(dev, LU9685_REG_BULK, buf, (size_t)LU9685_NUM_CHANNELS);
}
