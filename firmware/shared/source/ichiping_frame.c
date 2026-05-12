#include "ichiping_frame.h"
#include <string.h>

uint16_t ichp_crc16_ccitt(const uint8_t *data, size_t len) {
    uint16_t crc = 0xFFFFu;
    for (size_t i = 0; i < len; i++) {
        crc ^= (uint16_t)data[i] << 8;
        for (int b = 0; b < 8; b++) {
            if (crc & 0x8000u) {
                crc = (uint16_t)((crc << 1) ^ 0x1021u);
            } else {
                crc = (uint16_t)(crc << 1);
            }
        }
    }
    return crc;
}

size_t ichp_pack_frame(uint8_t *out, size_t out_size,
                       uint16_t seq,
                       uint32_t timestamp_ms,
                       uint16_t rate_hz,
                       uint16_t n_samples,
                       const float servo_deg[5],
                       const int16_t *samples)
{
    const size_t payload_bytes = (size_t)n_samples * sizeof(int16_t);
    const size_t total = ICHP_HEADER_SIZE + payload_bytes + ICHP_CRC_SIZE;
    if (out_size < total) {
        return 0;
    }

    ichp_frame_header_t *h = (ichp_frame_header_t *)out;
    h->magic[0]    = ICHP_MAGIC_0;
    h->magic[1]    = ICHP_MAGIC_1;
    h->magic[2]    = ICHP_MAGIC_2;
    h->magic[3]    = ICHP_MAGIC_3;
    h->type        = ICHP_TYPE_AUDIO;
    h->reserved    = 0;
    h->seq         = seq;
    h->timestamp_ms = timestamp_ms;
    h->n_samples   = n_samples;
    h->rate_hz     = rate_hz;
    for (int i = 0; i < 5; i++) {
        h->servo_deg[i] = servo_deg[i];
    }

    memcpy(out + ICHP_HEADER_SIZE, samples, payload_bytes);

    const uint16_t crc = ichp_crc16_ccitt(out, ICHP_HEADER_SIZE + payload_bytes);
    out[total - 2] = (uint8_t)(crc & 0xFFu);
    out[total - 1] = (uint8_t)((crc >> 8) & 0xFFu);

    return total;
}
