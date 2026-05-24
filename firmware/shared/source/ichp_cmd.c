/*
 * IchiPing — ASCII command parser (collector protocol).
 * Implements the API declared in firmware/shared/include/ichp_cmd.h.
 *
 * Kept SDK-free (only <string.h>, <stdlib.h>, <ctype.h>) so the same
 * source can be reused in host-side ctypes tests if we ever want to
 * round-trip parse-tested commands.
 */

#include "ichp_cmd.h"

#include <string.h>
#include <stdlib.h>
#include <ctype.h>

/* Short physical-mount names — match the labels written on the model.
 * Index = servo slot in ICHP frame servo_deg[] and PCA9685 / LU9685 PWM
 * channel. Lookup is case-insensitive (see ichp_servo_lookup), so
 * operators can type "AB" / "ab" / "Ab" interchangeably. */
const char *const ICHP_SERVO_NAMES[ICHP_SERVO_COUNT] = {
    "a",       /* window a  (PWM ch 0) */
    "b",       /* window b  (PWM ch 1) */
    "c",       /* window c  (PWM ch 2) */
    "AB",      /* door AB   (PWM ch 3) */
    "BC",      /* door BC   (PWM ch 4) */
};

static int strcasecmp_local(const char *a, const char *b)
{
    while (*a && *b) {
        int ca = tolower((unsigned char)*a);
        int cb = tolower((unsigned char)*b);
        if (ca != cb) return ca - cb;
        a++; b++;
    }
    return (unsigned char)*a - (unsigned char)*b;
}

int ichp_servo_lookup(const char *name)
{
    if (!name) return -1;
    for (uint8_t i = 0; i < ICHP_SERVO_COUNT; i++) {
        if (strcasecmp_local(name, ICHP_SERVO_NAMES[i]) == 0) {
            return (int)i;
        }
    }
    return -1;
}

/* In-place tokeniser. Returns pointer to next token start, advances *p
 * to one past the token's terminator. Returns NULL when no more tokens. */
static char *next_token(char **p)
{
    if (!p || !*p) return NULL;
    char *s = *p;
    while (*s && isspace((unsigned char)*s)) s++;
    if (!*s) { *p = s; return NULL; }
    char *start = s;
    while (*s && !isspace((unsigned char)*s)) s++;
    if (*s) { *s = '\0'; s++; }
    *p = s;
    return start;
}

bool ichp_cmd_lbuf_feed(ichp_cmd_lbuf_t *lb, char c)
{
    if (c == '\r' || c == '\n') {
        if (lb->len == 0) {
            /* empty line — ignore but do not signal a complete line */
            return false;
        }
        lb->buf[lb->len] = '\0';
        return true;
    }
    if (lb->len >= ICHP_CMD_LINE_MAX) {
        lb->overflow = true;
        return false;
    }
    lb->buf[lb->len++] = c;
    return false;
}

bool ichp_cmd_parse(char *line, ichp_cmd_t *out, const char **err_token,
                    const char **err_arg)
{
    if (!line || !out) return false;
    memset(out, 0, sizeof(*out));
    if (err_token) *err_token = NULL;
    if (err_arg)   *err_arg   = NULL;

    char *p = line;
    char *verb = next_token(&p);
    if (!verb) {
        if (err_token) *err_token = "EMPTY";
        return false;
    }

    /* Uppercase verb for case-insensitive matching. */
    for (char *q = verb; *q; q++) *q = (char)toupper((unsigned char)*q);

    if (strcmp(verb, "PING") == 0) {
        out->kind = ICHP_CMD_PING;
        return true;
    }
    if (strcmp(verb, "RUN") == 0) {
        out->kind = ICHP_CMD_RUN;
        return true;
    }
    if (strcmp(verb, "STOP") == 0) {
        out->kind = ICHP_CMD_STOP;
        return true;
    }
    /* INFO is normally MCU → PC for human-readable annotations, but the
     * collector_client.py plan loop also sends "INFO label=<name>" to
     * stamp the wire trace. Accept it as a no-op comment so it does not
     * produce ERR BAD_VERB INFO noise. The dispatcher ignores it. */
    if (strcmp(verb, "INFO") == 0) {
        out->kind = ICHP_CMD_COMMENT;
        return true;
    }

    if (strcmp(verb, "OPEN") == 0 || strcmp(verb, "CLOSE") == 0) {
        char *sname = next_token(&p);
        if (!sname) { if (err_token) *err_token = "BAD_ARGS"; if (err_arg) *err_arg = verb; return false; }
        /* Special form: OPEN ALL / CLOSE ALL — drive every servo. */
        char upper[8] = {0};
        for (size_t k = 0; sname[k] && k < sizeof(upper) - 1; k++) {
            upper[k] = (char)toupper((unsigned char)sname[k]);
        }
        if (strcmp(upper, "ALL") == 0) {
            out->kind = (verb[0] == 'O') ? ICHP_CMD_OPEN_ALL : ICHP_CMD_CLOSE_ALL;
            return true;
        }
        int idx = ichp_servo_lookup(sname);
        if (idx < 0) { if (err_token) *err_token = "BAD_SERVO"; if (err_arg) *err_arg = sname; return false; }
        out->kind = (verb[0] == 'O') ? ICHP_CMD_OPEN : ICHP_CMD_CLOSE;
        out->servo_idx = (uint8_t)idx;
        return true;
    }

    if (strcmp(verb, "GET") == 0) {
        char *what = next_token(&p);
        if (!what) { if (err_token) *err_token = "BAD_ARGS"; if (err_arg) *err_arg = "GET"; return false; }
        for (char *q = what; *q; q++) *q = (char)toupper((unsigned char)*q);
        if (strcmp(what, "CONFIG") == 0) { out->kind = ICHP_CMD_GET_CONFIG; return true; }
        if (strcmp(what, "HOME")   == 0) { out->kind = ICHP_CMD_GET_HOME;   return true; }
        if (strcmp(what, "OPEN")   == 0) { out->kind = ICHP_CMD_GET_OPEN;   return true; }
        if (strcmp(what, "PINS")   == 0) { out->kind = ICHP_CMD_GET_PINS;   return true; }
        if (err_token) *err_token = "BAD_ARGS";
        if (err_arg)   *err_arg   = what;
        return false;
    }

    if (strcmp(verb, "CLEAR") == 0) {
        char *what = next_token(&p);
        if (!what) { if (err_token) *err_token = "BAD_ARGS"; if (err_arg) *err_arg = "CLEAR"; return false; }
        for (char *q = what; *q; q++) *q = (char)toupper((unsigned char)*q);
        if (strcmp(what, "PINS") == 0) { out->kind = ICHP_CMD_CLEAR_PINS; return true; }
        if (strcmp(what, "PIN")  == 0) {
            char *sname = next_token(&p);
            int idx = ichp_servo_lookup(sname);
            if (idx < 0) { if (err_token) *err_token = "BAD_SERVO"; if (err_arg) *err_arg = sname; return false; }
            out->kind = ICHP_CMD_CLEAR_PIN;
            out->servo_idx = (uint8_t)idx;
            return true;
        }
        if (err_token) *err_token = "BAD_ARGS";
        if (err_arg)   *err_arg   = what;
        return false;
    }

    if (strcmp(verb, "SET") == 0) {
        char *what = next_token(&p);
        if (!what) { if (err_token) *err_token = "BAD_ARGS"; if (err_arg) *err_arg = "SET"; return false; }
        for (char *q = what; *q; q++) *q = (char)toupper((unsigned char)*q);

        if (strcmp(what, "VOLUME") == 0) {
            char *v = next_token(&p);
            if (!v) { if (err_token) *err_token = "BAD_ARGS"; if (err_arg) *err_arg = "VOLUME"; return false; }
            /* Integer 0..100 percent. Reject decimal input explicitly so a
             * caller who still types "0.05" gets a clear error instead of
             * being silently parsed as 0 (mute). */
            if (strchr(v, '.') != NULL) {
                if (err_token) *err_token = "OUT_OF_RANGE";
                if (err_arg)   *err_arg   = v;
                return false;
            }
            int32_t n = (int32_t)strtol(v, NULL, 10);
            if (n < 0 || n > 100) { if (err_token) *err_token = "OUT_OF_RANGE"; if (err_arg) *err_arg = v; return false; }
            out->kind = ICHP_CMD_SET_VOLUME;
            out->volume_pct = n;
            return true;
        }
        /* SET EXCITATION was retired — the chirp/multiband/silence built-ins
         * now live in pc/patterns.yaml and are pushed to the MCU at startup.
         * Use PAT SELECT <idx> / EMIT <idx> instead. */
        if (strcmp(what, "REPEATS") == 0) {
            char *v = next_token(&p);
            if (!v) { if (err_token) *err_token = "BAD_ARGS"; if (err_arg) *err_arg = "REPEATS"; return false; }
            int32_t n = (int32_t)strtol(v, NULL, 10);
            if (n < 1 || n > 10000) { if (err_token) *err_token = "OUT_OF_RANGE"; if (err_arg) *err_arg = v; return false; }
            out->kind = ICHP_CMD_SET_REPEATS;
            out->repeats = n;
            return true;
        }
        if (strcmp(what, "PIN") == 0) {
            char *sname = next_token(&p);
            char *v     = next_token(&p);
            int idx = ichp_servo_lookup(sname);
            if (idx < 0) { if (err_token) *err_token = "BAD_SERVO"; if (err_arg) *err_arg = sname; return false; }
            if (!v) { if (err_token) *err_token = "BAD_ARGS"; if (err_arg) *err_arg = "PIN"; return false; }
            float d = strtof(v, NULL);
            if (d < 0.0f || d > 180.0f) { if (err_token) *err_token = "OUT_OF_RANGE"; if (err_arg) *err_arg = v; return false; }
            out->kind = ICHP_CMD_SET_PIN;
            out->servo_idx = (uint8_t)idx;
            out->deg = d;
            return true;
        }
        if (strcmp(what, "HOME") == 0 || strcmp(what, "OPEN") == 0) {
            char *sname = next_token(&p);
            char *v     = next_token(&p);
            int idx = ichp_servo_lookup(sname);
            if (idx < 0) { if (err_token) *err_token = "BAD_SERVO"; if (err_arg) *err_arg = sname; return false; }
            if (!v) { if (err_token) *err_token = "BAD_ARGS"; if (err_arg) *err_arg = what; return false; }
            float d = strtof(v, NULL);
            if (d < 0.0f || d > 180.0f) { if (err_token) *err_token = "OUT_OF_RANGE"; if (err_arg) *err_arg = v; return false; }
            out->kind = (strcmp(what, "HOME") == 0) ? ICHP_CMD_SET_HOME : ICHP_CMD_SET_OPEN;
            out->servo_idx = (uint8_t)idx;
            out->deg = d;
            return true;
        }
        if (err_token) *err_token = "BAD_ARGS";
        if (err_arg)   *err_arg   = what;
        return false;
    }

    if (strcmp(verb, "SAVE") == 0) {
        char *what = next_token(&p);
        if (!what) { if (err_token) *err_token = "BAD_ARGS"; if (err_arg) *err_arg = "SAVE"; return false; }
        for (char *q = what; *q; q++) *q = (char)toupper((unsigned char)*q);
        if (strcmp(what, "HOME") == 0) { out->kind = ICHP_CMD_SAVE_HOME; return true; }
        if (err_token) *err_token = "BAD_ARGS";
        if (err_arg)   *err_arg   = what;
        return false;
    }

    if (strcmp(verb, "SERVO") == 0) {
        char *first = next_token(&p);
        if (!first) { if (err_token) *err_token = "BAD_ARGS"; if (err_arg) *err_arg = "SERVO"; return false; }
        /* Special form: SERVO ALL OFF */
        char upper[16];
        size_t i = 0;
        while (first[i] && i < sizeof(upper) - 1) {
            upper[i] = (char)toupper((unsigned char)first[i]); i++;
        }
        upper[i] = '\0';
        if (strcmp(upper, "ALL") == 0) {
            char *off = next_token(&p);
            char upper2[8] = {0};
            if (off) {
                size_t j = 0;
                while (off[j] && j < sizeof(upper2) - 1) {
                    upper2[j] = (char)toupper((unsigned char)off[j]); j++;
                }
            }
            if (strcmp(upper2, "OFF") != 0) { if (err_token) *err_token = "BAD_ARGS"; if (err_arg) *err_arg = "SERVO ALL"; return false; }
            out->kind = ICHP_CMD_SERVO_ALL_OFF;
            return true;
        }
        /* Normal: SERVO <name> <deg>   or   SERVO <name> OFF */
        int idx = ichp_servo_lookup(first);
        if (idx < 0) { if (err_token) *err_token = "BAD_SERVO"; if (err_arg) *err_arg = first; return false; }
        char *v = next_token(&p);
        if (!v) { if (err_token) *err_token = "BAD_ARGS"; if (err_arg) *err_arg = "SERVO"; return false; }
        /* OFF form: release just this channel. Case-insensitive. */
        char vupper[8] = {0};
        for (size_t k = 0; v[k] && k < sizeof(vupper) - 1; k++) {
            vupper[k] = (char)toupper((unsigned char)v[k]);
        }
        if (strcmp(vupper, "OFF") == 0) {
            out->kind = ICHP_CMD_SERVO_OFF;
            out->servo_idx = (uint8_t)idx;
            return true;
        }
        float d = strtof(v, NULL);
        if (d < 0.0f || d > 180.0f) { if (err_token) *err_token = "OUT_OF_RANGE"; if (err_arg) *err_arg = v; return false; }
        out->kind = ICHP_CMD_SERVO;
        out->servo_idx = (uint8_t)idx;
        out->deg = d;
        return true;
    }

    /* ---- PAT verb (pattern library management) ---- */
    if (strcmp(verb, "PAT") == 0) {
        char *sub = next_token(&p);
        if (!sub) { if (err_token) *err_token = "BAD_ARGS"; if (err_arg) *err_arg = "PAT"; return false; }
        for (char *q = sub; *q; q++) *q = (char)toupper((unsigned char)*q);

        if (strcmp(sub, "CLEAR") == 0) {
            out->kind = ICHP_CMD_PAT_CLEAR;
            return true;
        }
        if (strcmp(sub, "INFO") == 0) {
            out->kind = ICHP_CMD_PAT_INFO;
            return true;
        }
        if (strcmp(sub, "SELECT") == 0) {
            char *v = next_token(&p);
            if (!v) { if (err_token) *err_token = "BAD_ARGS"; if (err_arg) *err_arg = "PAT SELECT"; return false; }
            out->kind = ICHP_CMD_PAT_SELECT;
            out->pat_i = (int32_t)strtol(v, NULL, 10);
            return true;
        }
        if (strcmp(sub, "TONE") == 0) {
            char *vhz  = next_token(&p);
            char *von  = next_token(&p);
            char *voff = next_token(&p);
            if (!vhz || !von || !voff) {
                if (err_token) *err_token = "BAD_ARGS";
                if (err_arg)   *err_arg   = "PAT TONE";
                return false;
            }
            out->kind  = ICHP_CMD_PAT_TONE;
            out->pat_a = (uint32_t)strtoul(vhz,  NULL, 10);
            out->pat_b = (uint32_t)strtoul(von,  NULL, 10);
            out->pat_c = (uint32_t)strtoul(voff, NULL, 10);
            return true;
        }
        if (strcmp(sub, "PULSE") == 0) {
            char *p2 = next_token(&p);
            if (!p2) { if (err_token) *err_token = "BAD_ARGS"; if (err_arg) *err_arg = "PAT PULSE"; return false; }
            for (char *q = p2; *q; q++) *q = (char)toupper((unsigned char)*q);
            if (strcmp(p2, "BEGIN") == 0) {
                char *name = next_token(&p);
                out->kind = ICHP_CMD_PAT_PULSE_BEGIN;
                out->pat_name[0] = '\0';
                if (name) {
                    size_t i;
                    for (i = 0; i < ICHP_PAT_NAME_LEN - 1u && name[i]; i++) out->pat_name[i] = name[i];
                    out->pat_name[i] = '\0';
                }
                return true;
            }
            if (strcmp(p2, "END") == 0) {
                char *r = next_token(&p);
                out->kind  = ICHP_CMD_PAT_PULSE_END;
                out->pat_i = r ? (int32_t)strtol(r, NULL, 10) : 1;
                return true;
            }
            if (err_token) *err_token = "BAD_ARGS";
            if (err_arg)   *err_arg   = p2;
            return false;
        }
        if (strcmp(sub, "SWEEP") == 0) {
            char *name     = next_token(&p);
            char *vstart   = next_token(&p);
            char *vend     = next_token(&p);
            char *vsweep   = next_token(&p);
            char *vsilence = next_token(&p);
            if (!name || !vstart || !vend || !vsweep || !vsilence) {
                if (err_token) *err_token = "BAD_ARGS";
                if (err_arg)   *err_arg   = "PAT SWEEP";
                return false;
            }
            out->kind = ICHP_CMD_PAT_SWEEP;
            {
                size_t i;
                for (i = 0; i < ICHP_PAT_NAME_LEN - 1u && name[i]; i++) out->pat_name[i] = name[i];
                out->pat_name[i] = '\0';
            }
            out->pat_a = (uint32_t)strtoul(vstart,   NULL, 10);
            out->pat_b = (uint32_t)strtoul(vend,     NULL, 10);
            out->pat_c = (uint32_t)strtoul(vsweep,   NULL, 10);
            out->pat_d = (uint32_t)strtoul(vsilence, NULL, 10);
            return true;
        }
        if (strcmp(sub, "NOISE") == 0) {
            /* PAT NOISE <name> <duration_ms> [volume_pct] [shape]
             *   shape: 0 = PRBS (default), 1 = uniform */
            char *name = next_token(&p);
            char *vdur = next_token(&p);
            char *vvol = next_token(&p);
            char *vsh  = next_token(&p);
            if (!name || !vdur) {
                if (err_token) *err_token = "BAD_ARGS";
                if (err_arg)   *err_arg   = "PAT NOISE";
                return false;
            }
            out->kind = ICHP_CMD_PAT_NOISE;
            {
                size_t i;
                for (i = 0; i < ICHP_PAT_NAME_LEN - 1u && name[i]; i++) out->pat_name[i] = name[i];
                out->pat_name[i] = '\0';
            }
            out->pat_a = (uint32_t)strtoul(vdur, NULL, 10);          /* duration_ms */
            out->pat_b = vvol ? (uint32_t)strtoul(vvol, NULL, 10) : 30u;  /* volume_pct */
            out->pat_c = vsh  ? (uint32_t)strtoul(vsh,  NULL, 10) : 0u;   /* shape */
            return true;
        }
        if (err_token) *err_token = "BAD_ARGS";
        if (err_arg)   *err_arg   = sub;
        return false;
    }

    /* ---- EQ verb (speaker EQ filter, 8-stage biquad cascade) ---- */
    if (strcmp(verb, "EQ") == 0) {
        char *sub = next_token(&p);
        if (!sub) { if (err_token) *err_token = "BAD_ARGS"; if (err_arg) *err_arg = "EQ"; return false; }
        for (char *q = sub; *q; q++) *q = (char)toupper((unsigned char)*q);

        if (strcmp(sub, "ENABLE")  == 0) { out->kind = ICHP_CMD_EQ_ENABLE;  return true; }
        if (strcmp(sub, "DISABLE") == 0) { out->kind = ICHP_CMD_EQ_DISABLE; return true; }
        if (strcmp(sub, "RESET")   == 0) { out->kind = ICHP_CMD_EQ_RESET;   return true; }
        if (strcmp(sub, "GET")     == 0) { out->kind = ICHP_CMD_EQ_GET;     return true; }
        if (strcmp(sub, "STATE")   == 0) { out->kind = ICHP_CMD_EQ_STATE;   return true; }
        if (strcmp(sub, "SET") == 0) {
            /* EQ SET <stage> <b0> <b1> <b2> <a1> <a2>  (floats, space-separated) */
            char *vst = next_token(&p);
            char *vb0 = next_token(&p);
            char *vb1 = next_token(&p);
            char *vb2 = next_token(&p);
            char *va1 = next_token(&p);
            char *va2 = next_token(&p);
            if (!vst || !vb0 || !vb1 || !vb2 || !va1 || !va2) {
                if (err_token) *err_token = "BAD_ARGS";
                if (err_arg)   *err_arg   = "EQ SET";
                return false;
            }
            long stg = strtol(vst, NULL, 10);
            if (stg < 0 || stg > 255) {
                if (err_token) *err_token = "OUT_OF_RANGE";
                if (err_arg)   *err_arg   = "EQ SET stage";
                return false;
            }
            out->kind     = ICHP_CMD_EQ_SET;
            out->eq_stage = (uint8_t)stg;
            out->eq_b0    = strtof(vb0, NULL);
            out->eq_b1    = strtof(vb1, NULL);
            out->eq_b2    = strtof(vb2, NULL);
            out->eq_a1    = strtof(va1, NULL);
            out->eq_a2    = strtof(va2, NULL);
            return true;
        }
        if (err_token) *err_token = "BAD_ARGS";
        if (err_arg)   *err_arg   = sub;
        return false;
    }

    if (strcmp(verb, "EMIT") == 0) {
        char *v = next_token(&p);
        if (!v) { if (err_token) *err_token = "BAD_ARGS"; if (err_arg) *err_arg = "EMIT"; return false; }
        out->kind  = ICHP_CMD_EMIT;
        out->pat_i = (int32_t)strtol(v, NULL, 10);
        return true;
    }

    /* ---- INFER verb (10_inference) ---- */
    if (strcmp(verb, "INFER") == 0) {
        char *sub = next_token(&p);
        if (!sub) {
            out->kind = ICHP_CMD_INFER;
            return true;
        }
        for (char *q = sub; *q; q++) *q = (char)toupper((unsigned char)*q);
        if (strcmp(sub, "STREAM") == 0) {
            char *v = next_token(&p);
            if (!v) { if (err_token) *err_token = "BAD_ARGS"; if (err_arg) *err_arg = "INFER STREAM"; return false; }
            int32_t n = (int32_t)strtol(v, NULL, 10);
            if (n < 1 || n > 10000) { if (err_token) *err_token = "OUT_OF_RANGE"; if (err_arg) *err_arg = v; return false; }
            out->kind    = ICHP_CMD_INFER_STREAM;
            out->infer_n = n;
            return true;
        }
        if (err_token) *err_token = "BAD_ARGS";
        if (err_arg)   *err_arg   = sub;
        return false;
    }

    /* ---- BL verb (10_inference: baseline 管理) ---- */
    if (strcmp(verb, "BL") == 0) {
        char *sub = next_token(&p);
        if (!sub) { if (err_token) *err_token = "BAD_ARGS"; if (err_arg) *err_arg = "BL"; return false; }
        for (char *q = sub; *q; q++) *q = (char)toupper((unsigned char)*q);

        if (strcmp(sub, "STATUS")    == 0) { out->kind = ICHP_CMD_BL_STATUS;    return true; }
        if (strcmp(sub, "FACTORY")   == 0) { out->kind = ICHP_CMD_BL_FACTORY;   return true; }
        if (strcmp(sub, "LIVE")      == 0) { out->kind = ICHP_CMD_BL_LIVE;      return true; }
        if (strcmp(sub, "CLEAR")     == 0) { out->kind = ICHP_CMD_BL_CLEAR;     return true; }
        if (strcmp(sub, "CALIBRATE") == 0) {
            char *v = next_token(&p);
            int32_t n = v ? (int32_t)strtol(v, NULL, 10) : 10;
            if (n < 1 || n > 200) { if (err_token) *err_token = "OUT_OF_RANGE"; if (err_arg) *err_arg = v ? v : "CALIBRATE"; return false; }
            out->kind    = ICHP_CMD_BL_CALIBRATE;
            out->infer_n = n;
            return true;
        }
        if (err_token) *err_token = "BAD_ARGS";
        if (err_arg)   *err_arg   = sub;
        return false;
    }

    if (err_token) *err_token = "BAD_VERB";
    if (err_arg)   *err_arg   = verb;
    return false;
}
