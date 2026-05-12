/*
 * IchiPing serial frame format.
 *
 * Binary framing used by both MCU firmware and PC receiver:
 *
 *   +-------------------+
 *   | magic[4]  "ICHP"  |
 *   | type      1B      |  0x01 = audio frame
 *   | reserved  1B      |
 *   | seq       2B      |  little-endian uint16
 *   | timestamp 4B      |  little-endian uint32, ms since boot
 *   | n_samples 2B      |  number of int16 samples in payload
 *   | rate_hz   2B      |  sample rate, Hz
 *   | servo_deg 5*4B    |  servo angles a,b,c,AB,BC (float32)
 *   +-------------------+   total header = 36 B
 *   | int16 samples * N |   little-endian
 *   +-------------------+
 *   | crc16     2B      |  CRC-16/CCITT-FALSE over header+payload
 *   +-------------------+
 */

#ifndef ICHIPING_FRAME_H_
#define ICHIPING_FRAME_H_

#include <stdint.h>
#include <stddef.h>

#define ICHP_MAGIC_0 'I'
#define ICHP_MAGIC_1 'C'
#define ICHP_MAGIC_2 'H'
#define ICHP_MAGIC_3 'P'

#define ICHP_TYPE_AUDIO 0x01

typedef struct __attribute__((packed)) {
    uint8_t  magic[4];
    uint8_t  type;
    uint8_t  reserved;
    uint16_t seq;
    uint32_t timestamp_ms;
    uint16_t n_samples;
    uint16_t rate_hz;
    float    servo_deg[5];
} ichp_frame_header_t;

#define ICHP_HEADER_SIZE  ((size_t)sizeof(ichp_frame_header_t))   /* 36 */
#define ICHP_CRC_SIZE     ((size_t)2)

uint16_t ichp_crc16_ccitt(const uint8_t *data, size_t len);

/* Pack a complete frame (header + payload + CRC) into 'out'.
 * Returns total bytes written, or 0 if out_size is insufficient.
 */
size_t ichp_pack_frame(uint8_t *out, size_t out_size,
                       uint16_t seq,
                       uint32_t timestamp_ms,
                       uint16_t rate_hz,
                       uint16_t n_samples,
                       const float servo_deg[5],
                       const int16_t *samples);

#endif /* ICHIPING_FRAME_H_ */
