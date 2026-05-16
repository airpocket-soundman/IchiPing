/*
 * IchiPing — ASCII command protocol for the collector firmware.
 *
 * Shared between firmware/projects/09_collector and pc/collector_client.py.
 *
 * Wire format
 * -----------
 *   Each command is a single line terminated by "\r\n" (CR-LF) or "\n".
 *   Each response is a single line terminated by "\r\n".
 *   Tokens are whitespace-separated; the verb is uppercase.
 *
 * Multiplexing
 * ------------
 *   The same UART carries:
 *     - ASCII command/response lines (this header's domain)
 *     - ICHP binary frames (ichiping_frame.h, 36 B header + N x 2 B + CRC)
 *   The receiver scans byte-by-byte for the literal "ICHP" magic to detect
 *   frame boundaries. ASCII tokens deliberately avoid producing "ICHP" as
 *   a 4-byte substring (no command label includes that exact sequence).
 *
 * Servo channel naming
 * --------------------
 *   The 5 servos on the IchiPing model are named in commands using the
 *   labels below (matching hardware/wiring.md §2.5):
 *
 *     window_a, window_b, window_c, door_AB, door_BC
 *
 *   In ICHP frame metadata they map to servo_deg[0..4] in that order.
 *
 * Verbs
 * -----
 *   PING                              -> OK PONG <build_time_iso>
 *   GET CONFIG                        -> OK CONFIG rate=<Hz> window=<samp> excitation=<name>
 *                                                  volume=<0..1> repeats=<N>
 *   GET HOME                          -> OK HOME window_a=<deg> ... door_BC=<deg>
 *   GET OPEN                          -> OK OPEN window_a=<deg> ... door_BC=<deg>
 *   GET PINS                          -> OK PINS window_a=<deg|free> ... door_BC=<deg|free>
 *
 *   SET VOLUME <0..1>                 -> OK VOLUME <value>
 *   SET EXCITATION <name>             -> OK EXCITATION <name>
 *       name in {chirp, multiband, silence}
 *   SET REPEATS <N>                   -> OK REPEATS <N>
 *   SET PIN <servo> <deg>             -> OK PIN <servo> <deg>
 *   CLEAR PIN <servo>                 -> OK PIN <servo> free
 *   CLEAR PINS                        -> OK PINS cleared
 *   SET HOME <servo> <deg>            -> OK HOME <servo> <deg>
 *   SET OPEN <servo> <deg>            -> OK OPEN <servo> <deg>
 *   SAVE HOME                         -> OK HOME saved
 *                                     or ERR NOT_IMPL <reason>
 *
 *   SERVO <servo> <deg>               -> OK SERVO <servo> <deg>     (manual move)
 *   SERVO ALL OFF                     -> OK SERVO all off           (release PWM)
 *
 *   RUN                               -> OK RUN started repeats=<N>
 *       then N ICHP frames, then OK RUN done frames=<N>
 *   STOP                              -> OK STOP requested
 *                                     and a final OK RUN aborted frames=<N>
 *
 * Errors
 * ------
 *   ERR BAD_VERB <token>
 *   ERR BAD_ARGS <verb>
 *   ERR BAD_SERVO <token>
 *   ERR OUT_OF_RANGE <verb> <token>
 *   ERR BUSY <verb>                   (e.g. STOP/SERVO during RUN)
 *   ERR NOT_IMPL <verb>               (e.g. SAVE HOME without flash backend)
 */

#ifndef ICHP_CMD_H_
#define ICHP_CMD_H_

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Maximum number of servos addressable by name. Matches IchiPing v1. */
#define ICHP_SERVO_COUNT    5u

/* Single source of truth for the servo names that appear in commands.
 * Index matches ICHP frame servo_deg[] slot and PCA9685 channel. */
extern const char *const ICHP_SERVO_NAMES[ICHP_SERVO_COUNT];

/* Excitation kinds — values are the indices used by firmware's excite
 * dispatcher and embedded in ICHP frame servo_deg[2] when running under
 * the data-collection protocol. */
typedef enum {
    ICHP_EXCITE_CHIRP     = 0,
    ICHP_EXCITE_MULTIBAND = 1,
    ICHP_EXCITE_SILENCE   = 2,
    ICHP_EXCITE__COUNT
} ichp_excitation_t;

extern const char *const ICHP_EXCITATION_NAMES[ICHP_EXCITE__COUNT];

/* Parsed command. The verb plus a small union of args. The parser fills
 * one of the variant fields based on verb. Pointers into the original
 * buffer must not outlive that buffer (the parser does not copy). */
typedef enum {
    ICHP_CMD_NONE = 0,
    ICHP_CMD_PING,
    ICHP_CMD_GET_CONFIG,
    ICHP_CMD_GET_HOME,
    ICHP_CMD_GET_OPEN,
    ICHP_CMD_GET_PINS,
    ICHP_CMD_SET_VOLUME,
    ICHP_CMD_SET_EXCITATION,
    ICHP_CMD_SET_REPEATS,
    ICHP_CMD_SET_PIN,
    ICHP_CMD_CLEAR_PIN,
    ICHP_CMD_CLEAR_PINS,
    ICHP_CMD_SET_HOME,
    ICHP_CMD_SET_OPEN,
    ICHP_CMD_SAVE_HOME,
    ICHP_CMD_SERVO,
    ICHP_CMD_SERVO_ALL_OFF,
    ICHP_CMD_RUN,
    ICHP_CMD_STOP,
} ichp_cmd_kind_t;

typedef struct {
    ichp_cmd_kind_t kind;
    /* Common: servo index resolved by name lookup (0..ICHP_SERVO_COUNT-1).
     * Valid for SET_PIN, CLEAR_PIN, SET_HOME, SERVO. */
    uint8_t  servo_idx;
    /* Common: numeric argument. Meaning depends on verb. */
    float    deg;
    float    volume;            /* SET_VOLUME            */
    int32_t  repeats;           /* SET_REPEATS           */
    ichp_excitation_t excite;   /* SET_EXCITATION        */
    /* For SERVO_ALL_OFF: no extra fields. */
} ichp_cmd_t;

/* Parse one line. `line` is NUL-terminated, in/out. Returns true on a
 * recognised verb (out->kind set accordingly). On parse error returns
 * false and fills *err_token with a short error code string suitable for
 * reporting back to the host (e.g. "BAD_VERB").
 *
 * Side effect: tokenisation may write NUL bytes into `line` (strtok-like).
 * Callers should hand in a private writable buffer. */
bool ichp_cmd_parse(char *line, ichp_cmd_t *out, const char **err_token,
                    const char **err_arg);

/* Convenience: lookup a servo by name (case-insensitive on the well-known
 * names "window_a" ... "door_BC"). Returns -1 on no match. */
int  ichp_servo_lookup(const char *name);

/* Convenience: lookup an excitation name. Returns -1 on no match. */
int  ichp_excitation_lookup(const char *name);

/* ---- Byte-oriented line buffer ----
 *
 * The MCU reads UART bytes via interrupt or polling; we accumulate them
 * into a line and dispatch when a CR or LF is seen. Lines longer than
 * ICHP_CMD_LINE_MAX are silently truncated and an ERR LINE_TOO_LONG is
 * reported when the line terminates.
 */

#define ICHP_CMD_LINE_MAX   128u

typedef struct {
    char     buf[ICHP_CMD_LINE_MAX + 1];   /* +1 for NUL terminator */
    uint16_t len;
    bool     overflow;
} ichp_cmd_lbuf_t;

static inline void ichp_cmd_lbuf_reset(ichp_cmd_lbuf_t *lb) {
    lb->len = 0;
    lb->overflow = false;
    lb->buf[0] = '\0';
}

/* Feed one byte. Returns true when the buffer holds a complete line
 * (terminated and NUL'd in place at lb->buf). The caller should then
 * parse, then call ichp_cmd_lbuf_reset() before feeding the next line. */
bool ichp_cmd_lbuf_feed(ichp_cmd_lbuf_t *lb, char c);

#ifdef __cplusplus
}
#endif

#endif /* ICHP_CMD_H_ */
