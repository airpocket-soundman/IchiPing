#include "pca9685.h"

/* ---- PCA9685 register map (data sheet §7.3) ---- */
#define REG_MODE1        0x00u
#define REG_MODE2        0x01u
#define REG_PRESCALE     0xFEu
#define REG_LED0_ON_L    0x06u
#define REG_ALL_LED_ON_L 0xFAu

/* MODE1 bits */
#define MODE1_RESTART    0x80u
#define MODE1_EXTCLK     0x40u
#define MODE1_AI         0x20u   /* auto-increment for burst writes */
#define MODE1_SLEEP      0x10u

/* Crude busy-wait. Used only twice during init for the 500 µs oscillator
 * stabilisation called for in the data sheet (§7.3.1.1). We pick this over
 * SysTick to keep pca9685.c independent of board timer setup. */
static void busy_wait_us(uint32_t us) {
    /* Approximate: SystemCoreClock ticks per us, divided by ~4 cycles/iter. */
    volatile uint32_t i;
    uint32_t loops = (SystemCoreClock / 1000000u) * us / 4u;
    if (loops == 0u) loops = 1u;
    for (i = 0; i < loops; i++) {
        __asm volatile ("nop");
    }
}

static status_t i2c_write_buf(pca9685_t *dev,
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

static status_t i2c_write_reg(pca9685_t *dev, uint8_t reg, uint8_t val) {
    return i2c_write_buf(dev, reg, &val, 1u);
}

status_t pca9685_init(pca9685_t *dev,
                      LPI2C_Type *base, uint8_t addr,
                      float pwm_freq_hz) {
    if (dev == NULL || base == NULL) return kStatus_InvalidArgument;
    if (pwm_freq_hz < 24.0f || pwm_freq_hz > 1526.0f) {
        /* Outside the PCA9685 supported range. */
        return kStatus_InvalidArgument;
    }
    dev->base = base;
    dev->addr = addr;

    /* prescale = round(25 MHz / (4096 * freq)) - 1.
     * Clamp to 8-bit range per data sheet §7.3.5. */
    float prescale_f = 25000000.0f / (4096.0f * pwm_freq_hz) - 1.0f;
    int   prescale_i = (int)(prescale_f + 0.5f);
    if (prescale_i < 3)   prescale_i = 3;
    if (prescale_i > 255) prescale_i = 255;
    uint8_t prescale = (uint8_t)prescale_i;

    /* §7.3.1.1: PRE_SCALE can only be set while SLEEP=1. */
    status_t s;
    s = i2c_write_reg(dev, REG_MODE1, MODE1_SLEEP);
    if (s != kStatus_Success) return s;

    s = i2c_write_reg(dev, REG_PRESCALE, prescale);
    if (s != kStatus_Success) return s;

    /* Clear SLEEP, enable auto-increment so set_pwm() can write 4 bytes
     * in one transaction (LED_ON_L..LED_OFF_H). */
    s = i2c_write_reg(dev, REG_MODE1, MODE1_AI);
    if (s != kStatus_Success) return s;

    /* Wait for the internal oscillator to stabilise (≥ 500 µs). */
    busy_wait_us(500);

    /* Toggle RESTART to clear any pending duty-cycle freeze. */
    s = i2c_write_reg(dev, REG_MODE1, MODE1_AI | MODE1_RESTART);
    return s;
}

status_t pca9685_set_pwm(pca9685_t *dev,
                         uint8_t ch,
                         uint16_t on_tick, uint16_t off_tick) {
    if (dev == NULL) return kStatus_InvalidArgument;
    if (ch >= PCA9685_NUM_CHANNELS) return kStatus_InvalidArgument;
    if (on_tick > 4095u || off_tick > 4095u) return kStatus_InvalidArgument;

    uint8_t buf[4] = {
        (uint8_t)( on_tick        & 0xFFu),
        (uint8_t)((on_tick  >> 8) & 0x0Fu),
        (uint8_t)( off_tick       & 0xFFu),
        (uint8_t)((off_tick >> 8) & 0x0Fu),
    };
    uint8_t reg = (uint8_t)(REG_LED0_ON_L + ch * 4u);
    return i2c_write_buf(dev, reg, buf, sizeof(buf));
}

status_t pca9685_set_servo_deg(pca9685_t *dev, uint8_t ch, float deg) {
    if (deg < 0.0f)   deg = 0.0f;
    if (deg > 180.0f) deg = 180.0f;
    float span = (float)(PCA9685_SG90_MAX_TICK - PCA9685_SG90_MIN_TICK);
    float t    = (float)PCA9685_SG90_MIN_TICK + (deg / 180.0f) * span;
    uint16_t off_tick = (uint16_t)(t + 0.5f);
    return pca9685_set_pwm(dev, ch, 0u, off_tick);
}

status_t pca9685_set_all_servo_deg(pca9685_t *dev, const float deg[5]) {
    if (dev == NULL || deg == NULL) return kStatus_InvalidArgument;
    for (uint8_t i = 0; i < 5u; i++) {
        status_t s = pca9685_set_servo_deg(dev, i, deg[i]);
        if (s != kStatus_Success) return s;
    }
    return kStatus_Success;
}

status_t pca9685_all_off(pca9685_t *dev) {
    /* ALL_LED_* registers broadcast the same on/off pair to every channel.
     * Setting off-tick bit 4 (full-OFF) gives true 0% duty.
     * Data sheet §7.3.3, fig 12. */
    uint8_t buf[4] = {0x00u, 0x00u, 0x00u, 0x10u};
    return i2c_write_buf(dev, REG_ALL_LED_ON_L, buf, sizeof(buf));
}
