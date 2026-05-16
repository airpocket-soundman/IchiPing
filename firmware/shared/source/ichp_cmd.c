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

const char *const ICHP_EXCITATION_NAMES[ICHP_EXCITE__COUNT] = {
    "chirp",
    "multiband",
    "silence",
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

int ichp_excitation_lookup(const char *name)
{
    if (!name) return -1;
    for (int i = 0; i < (int)ICHP_EXCITE__COUNT; i++) {
        if (strcasecmp_local(name, ICHP_EXCITATION_NAMES[i]) == 0) {
            return i;
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
        if (strcmp(what, "EXCITATION") == 0) {
            char *v = next_token(&p);
            int e = ichp_excitation_lookup(v);
            if (e < 0) { if (err_token) *err_token = "OUT_OF_RANGE"; if (err_arg) *err_arg = v; return false; }
            out->kind = ICHP_CMD_SET_EXCITATION;
            out->excite = (ichp_excitation_t)e;
            return true;
        }
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
        /* Normal: SERVO <name> <deg> */
        int idx = ichp_servo_lookup(first);
        if (idx < 0) { if (err_token) *err_token = "BAD_SERVO"; if (err_arg) *err_arg = first; return false; }
        char *v = next_token(&p);
        if (!v) { if (err_token) *err_token = "BAD_ARGS"; if (err_arg) *err_arg = "SERVO"; return false; }
        float d = strtof(v, NULL);
        if (d < 0.0f || d > 180.0f) { if (err_token) *err_token = "OUT_OF_RANGE"; if (err_arg) *err_arg = v; return false; }
        out->kind = ICHP_CMD_SERVO;
        out->servo_idx = (uint8_t)idx;
        out->deg = d;
        return true;
    }

    if (err_token) *err_token = "BAD_VERB";
    if (err_arg)   *err_arg   = verb;
    return false;
}
