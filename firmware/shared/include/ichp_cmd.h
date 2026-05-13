/*
 * IchiPing PC → MCU command protocol (ASCII, line-oriented).
 *
 * Used by 10_collector to receive collection parameters from a PC client.
 * Kept ASCII so it is debuggable from a plain serial terminal — the
 * downstream MCU → PC data path stays binary ICHP frames.
 *
 * Wire format:
 *   <CMD> SP <arg1> SP <arg2> ... LF
 *
 * Each line is terminated by '\n'. CR is tolerated. Maximum 96 chars per
 * line, lines longer than that are silently truncated.
 *
 * Recognised commands:
 *   SET <key> <value>
 *       key ∈ {window, rate, tone, repeats, label}
 *           window  = number of samples per capture (e.g. 32000)
 *           rate    = sample rate in Hz (e.g. 16000)
 *           tone    = chirp | tone200 | tone1k | tone5k | silence
 *           repeats = how many captures to perform when START is sent
 *           label   = printable string ≤ 19 chars, becomes label in frame
 *   START                begin a capture run with the current SET values
 *   STOP                 abort the current run (if any)
 *   PING                 MCU replies with "PONG <build-time>\n"
 *   GET                  MCU prints the current config one key per line
 *
 * Replies are also ASCII lines, prefixed with one of:
 *   "OK ", "ERR ", "INFO " — easy to parse on the PC side.
 * Binary ICHP frames returned during a capture run interleave with these
 * ASCII lines; the PC client de-frames by scanning for the "ICHP" magic.
 */

#ifndef ICHIPING_CMD_H_
#define ICHIPING_CMD_H_

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

#define ICHP_CMD_LINE_MAX     96
#define ICHP_CMD_LABEL_MAX    20

typedef enum {
    ICHP_TONE_CHIRP   = 0,
    ICHP_TONE_200HZ   = 1,
    ICHP_TONE_1KHZ    = 2,
    ICHP_TONE_5KHZ    = 3,
    ICHP_TONE_SILENCE = 4,
} ichp_tone_t;

typedef struct {
    uint32_t   window_samples;
    uint32_t   sample_rate_hz;
    ichp_tone_t tone;
    uint32_t   repeats;
    char       label[ICHP_CMD_LABEL_MAX];
    /* runtime flag set by parser when a START line has been consumed */
    int        start_requested;
    int        stop_requested;
} ichp_cmd_state_t;

void ichp_cmd_init_defaults(ichp_cmd_state_t *st);

/* Feed one byte at a time. Returns 1 if a full line was consumed (then
 * 'reply' carries the ASCII line to send back, NUL-terminated). */
int ichp_cmd_feed_byte(ichp_cmd_state_t *st, uint8_t b,
                       char reply[ICHP_CMD_LINE_MAX]);

#ifdef __cplusplus
}
#endif
#endif /* ICHIPING_CMD_H_ */
