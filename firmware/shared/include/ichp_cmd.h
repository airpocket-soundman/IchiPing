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
 *   short physical-mount labels printed on the model:
 *
 *     a, b, c    — windows (PWM ch 0, 1, 2)
 *     AB, BC     — inter-room doors (PWM ch 3, 4)
 *
 *   Case-insensitive in commands ("ab" matches door AB; "C" matches
 *   window c). In ICHP frame metadata they map to servo_deg[0..4] in
 *   the order listed above. See hardware/wiring.md §2.5.
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
 *   SET VOLUME <0..100>               -> OK VOLUME <pct>   (integer percent)
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
 *   SERVO <servo> <deg>               -> OK SERVO <servo> <deg>     (move to deg 0..180, wait, release)
 *   SERVO <servo> OFF                 -> OK SERVO <servo> off       (release one ch immediately)
 *   SERVO ALL OFF                     -> OK SERVO all off           (release all PWM)
 *   OPEN <servo>                      -> OK OPEN <servo> deg=<n>    (move to open_deg, wait, release)
 *   CLOSE <servo>                     -> OK CLOSE <servo> deg=<n>   (move to home_deg, wait, release)
 *   OPEN ALL                          -> OK OPEN all                (sequential a→b→c→AB→BC to open_deg, distance-scaled settle)
 *   CLOSE ALL                         -> OK CLOSE all               (sequential BC→AB→c→b→a to home_deg, distance-scaled settle — reverse, airlock)
 *
 *   SERVO / OPEN / CLOSE all share the same "move → distance-scaled settle →
 *   release PWM on that channel" pattern so the chassis stays silent at
 *   idle (SG90 hum). RUN drives servos via a separate path that keeps
 *   them energised through capture.
 *
 *   RUN                               -> OK RUN started repeats=<N>
 *       then N ICHP frames, then OK RUN done frames=<N>
 *   STOP                              -> OK STOP requested
 *                                     and a final OK RUN aborted frames=<N>
 *
 *   PAT NOISE <name> <dur_ms> [vol_pct] [shape]
 *                                     -> OK PAT noise name=<name> dur=<ms> vol=<pct> shape=<n>
 *       shape: 0 = PRBS (default), 1 = uniform int16
 *
 *   EQ ENABLE                         -> OK EQ enabled
 *   EQ DISABLE                        -> OK EQ disabled   (default at boot)
 *   EQ RESET                          -> OK EQ reset      (coefs back to identity defaults)
 *   EQ SET <stage> <b0> <b1> <b2> <a1> <a2>
 *                                     -> OK EQ set stage=<n>
 *       8-stage biquad cascade (DF1, a0=1 normalised). stage in 0..7.
 *   EQ GET                            -> 8 x OK EQ stage=<n> b0=... b1=... b2=... a1=... a2=...
 *   EQ STATE                          -> OK EQ state=<ENABLED|DISABLED> stages=8
 *
 *   --- 10_inference 専用 ---
 *   INFER                             -> RESULT seq=<n> state=sABCDE state_idx=<0..31>
 *                                                  cls14=<A1..C8> baseline=<factory|live>
 *                                                  argmax_q=<int8> infer_ms=<n> cap_ms=<n>
 *                                                  doors=<a=0/1 b=... AB=... BC=...>
 *   INFER STREAM <N>                  -> N x RESULT lines, then OK INFER done
 *   STOP                              -> abort active INFER STREAM
 *
 *   BL STATUS                         -> OK BL mode=<factory|live> calibrated=<0|1>
 *                                                cal_frames=<n> scale=... offset=...
 *   BL FACTORY                        -> OK BL mode=factory
 *   BL LIVE                           -> OK BL mode=live           (ERR if not calibrated)
 *   BL CALIBRATE [N]                  -> capture N frames (default 10) -> live baseline
 *                                        OK BL calibrated frames=<n> scale=... offset=...
 *   BL CLEAR                          -> OK BL cleared (mode reverts to factory)
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

/* Excitation patterns no longer live in a fixed enum on the firmware
 * side; pc/patterns.yaml is the source of truth, and the host pushes
 * each entry via PAT_* commands into the in-RAM pattern_lib at startup
 * (see firmware/shared/include/pattern_lib.h). */

/* Parsed command. The verb plus a small union of args. The parser fills
 * one of the variant fields based on verb. Pointers into the original
 * buffer must not outlive that buffer (the parser does not copy). */
typedef enum {
    ICHP_CMD_NONE = 0,
    ICHP_CMD_COMMENT,                  /* INFO ... — accepted as no-op */
    ICHP_CMD_PING,
    ICHP_CMD_GET_CONFIG,
    ICHP_CMD_GET_HOME,
    ICHP_CMD_GET_OPEN,
    ICHP_CMD_GET_PINS,
    ICHP_CMD_SET_VOLUME,
    ICHP_CMD_SET_REPEATS,
    ICHP_CMD_SET_PIN,
    ICHP_CMD_CLEAR_PIN,
    ICHP_CMD_CLEAR_PINS,
    ICHP_CMD_SET_HOME,
    ICHP_CMD_SET_OPEN,
    ICHP_CMD_SAVE_HOME,
    ICHP_CMD_SERVO,
    ICHP_CMD_SERVO_OFF,
    ICHP_CMD_SERVO_ALL_OFF,
    ICHP_CMD_OPEN,
    ICHP_CMD_CLOSE,
    ICHP_CMD_OPEN_ALL,
    ICHP_CMD_CLOSE_ALL,
    ICHP_CMD_RUN,
    ICHP_CMD_STOP,
    /* Pattern library — see firmware/shared/include/pattern_lib.h and
     * pc/patterns.yaml. Built-in chirp/multiband excitations were retired
     * in favour of host-defined patterns pushed at startup. */
    ICHP_CMD_PAT_CLEAR,
    ICHP_CMD_PAT_PULSE_BEGIN,
    ICHP_CMD_PAT_TONE,
    ICHP_CMD_PAT_PULSE_END,
    ICHP_CMD_PAT_SWEEP,
    ICHP_CMD_PAT_NOISE,
    ICHP_CMD_PAT_INFO,
    ICHP_CMD_PAT_SELECT,
    ICHP_CMD_EMIT,
    /* Speaker EQ (8-stage biquad cascade). Default: disabled + identity.
     * See firmware/shared/include/spk_eq.h for the math. */
    ICHP_CMD_EQ_ENABLE,
    ICHP_CMD_EQ_DISABLE,
    ICHP_CMD_EQ_RESET,
    ICHP_CMD_EQ_SET,
    ICHP_CMD_EQ_GET,
    ICHP_CMD_EQ_STATE,
    /* 10_inference 専用: 推論実行と baseline 管理。
     *   INFER                  : 1 回推論 → RESULT line
     *   INFER STREAM <N>       : N 回連続推論 (途中で STOP 可)
     *   BL STATUS              : 現在 baseline モード (factory/live) 表示
     *   BL FACTORY             : factory_baseline.h に切替
     *   BL LIVE                : RAM 上 live baseline に切替 (要 CALIBRATE 済)
     *   BL CALIBRATE [N]       : 静粛時 N frame (default 10) 録音して live baseline 算出
     *   BL CLEAR               : live baseline 破棄 (factory に戻す)
     */
    ICHP_CMD_INFER,
    ICHP_CMD_INFER_STREAM,
    ICHP_CMD_BL_STATUS,
    ICHP_CMD_BL_FACTORY,
    ICHP_CMD_BL_LIVE,
    ICHP_CMD_BL_CALIBRATE,
    ICHP_CMD_BL_CLEAR,
} ichp_cmd_kind_t;

#define ICHP_PAT_NAME_LEN  32u

typedef struct {
    ichp_cmd_kind_t kind;
    /* Common: servo index resolved by name lookup (0..ICHP_SERVO_COUNT-1).
     * Valid for SET_PIN, CLEAR_PIN, SET_HOME, SERVO. */
    uint8_t  servo_idx;
    /* Common: numeric argument. Meaning depends on verb. */
    float    deg;
    int32_t  volume_pct;        /* SET_VOLUME (0..100 integer percent) */
    int32_t  repeats;           /* SET_REPEATS                         */
    /* Pattern command fields. Only the subset relevant to each kind is
     * valid; the dispatcher reads accordingly.
     *   PAT_PULSE_BEGIN : pat_name
     *   PAT_TONE        : pat_a = freq_hz, pat_b = on_ms, pat_c = off_ms
     *   PAT_PULSE_END   : pat_i = repeat (>=1)
     *   PAT_SWEEP       : pat_name, pat_a = start_hz, pat_b = end_hz,
     *                     pat_c = sweep_ms, pat_d = silence_ms
     *   PAT_SELECT      : pat_i = index
     *   EMIT            : pat_i = index
     */
    char     pat_name[ICHP_PAT_NAME_LEN];
    uint32_t pat_a, pat_b, pat_c, pat_d;
    int32_t  pat_i;
    /* EQ command fields.
     *   EQ_SET : eq_stage = 0..7, eq_b0..eq_a2 = float coefficients */
    uint8_t  eq_stage;
    float    eq_b0, eq_b1, eq_b2, eq_a1, eq_a2;
    /* INFER STREAM / BL CALIBRATE が共有する繰り返し回数 (1..10000)。
     * INFER STREAM では推論回数、BL CALIBRATE では平均する frame 数。 */
    int32_t  infer_n;
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
