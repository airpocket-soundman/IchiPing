/*
 * IchiPing command-line parser — see ichp_cmd.h.
 *
 * No stdio / malloc usage, safe to call from non-FreeRTOS bare-metal code.
 */

#include "ichp_cmd.h"

#include <string.h>
#include <stdlib.h>

#ifndef __BUILD_TIMESTAMP__
#  define __BUILD_TIMESTAMP__ __DATE__ " " __TIME__
#endif

void ichp_cmd_init_defaults(ichp_cmd_state_t *st)
{
    if (!st) return;
    st->window_samples  = 32000U;          /* 2 s @ 16 kHz */
    st->sample_rate_hz  = 16000U;
    st->tone            = ICHP_TONE_CHIRP;
    st->repeats         = 1U;
    st->label[0]        = '\0';
    st->start_requested = 0;
    st->stop_requested  = 0;
}

/* ---- tiny string helpers (no libc dependency beyond memchr/strcmp) ---- */

static int str_eq(const char *a, const char *b)
{
    while (*a && *b) { if (*a++ != *b++) return 0; }
    return *a == *b;
}

static const char *skip_spaces(const char *p)
{
    while (*p == ' ' || *p == '\t') p++;
    return p;
}

static const char *next_token(const char *p)
{
    while (*p && *p != ' ' && *p != '\t') p++;
    return skip_spaces(p);
}

static void str_copy(char *dst, size_t cap, const char *src, size_t n)
{
    size_t i = 0;
    if (cap == 0) return;
    while (i + 1 < cap && i < n && src[i] && src[i] != ' ' && src[i] != '\t') {
        dst[i] = src[i];
        i++;
    }
    dst[i] = '\0';
}

/* ---- per-line static buffer ---- */
static char s_line[ICHP_CMD_LINE_MAX];
static size_t s_len = 0;

static void put_str(char *dst, const char *src)
{
    size_t i = 0;
    while (src[i] && i < ICHP_CMD_LINE_MAX - 1) { dst[i] = src[i]; i++; }
    dst[i] = '\0';
}

static void put_concat(char *dst, const char *a, const char *b)
{
    size_t i = 0;
    while (*a && i < ICHP_CMD_LINE_MAX - 1) { dst[i++] = *a++; }
    while (*b && i < ICHP_CMD_LINE_MAX - 1) { dst[i++] = *b++; }
    dst[i] = '\0';
}

static void put_dec(char *dst, const char *prefix, uint32_t value)
{
    size_t i = 0;
    while (*prefix && i < ICHP_CMD_LINE_MAX - 1) { dst[i++] = *prefix++; }
    char buf[12]; int n = 0;
    if (value == 0) { buf[n++] = '0'; }
    else { uint32_t v = value; char tmp[12]; int t = 0;
        while (v) { tmp[t++] = (char)('0' + (v % 10)); v /= 10; }
        while (t > 0) buf[n++] = tmp[--t];
    }
    for (int k = 0; k < n && i < ICHP_CMD_LINE_MAX - 1; k++) dst[i++] = buf[k];
    dst[i] = '\0';
}

/* ---- per-line handler ---- */

static void handle_line(ichp_cmd_state_t *st, const char *line, char *reply)
{
    line = skip_spaces(line);
    reply[0] = '\0';

    if (line[0] == '\0') return;  /* empty line: no reply */

    if (str_eq(line, "PING") || (line[0]=='P'&&line[1]=='I'&&line[2]=='N'&&line[3]=='G'&&(line[4]==0||line[4]==' '))) {
        put_concat(reply, "OK PONG ", __BUILD_TIMESTAMP__);
        return;
    }
    if (line[0]=='S'&&line[1]=='T'&&line[2]=='A'&&line[3]=='R'&&line[4]=='T') {
        st->start_requested = 1;
        put_str(reply, "OK START");
        return;
    }
    if (line[0]=='S'&&line[1]=='T'&&line[2]=='O'&&line[3]=='P') {
        st->stop_requested = 1;
        put_str(reply, "OK STOP");
        return;
    }
    if (line[0]=='G'&&line[1]=='E'&&line[2]=='T') {
        /* Caller is responsible for emitting multi-line GET replies; we
         * return only the first field here. The main loop calls back with
         * additional ASCII lines per key. */
        put_dec(reply, "OK window=", st->window_samples);
        return;
    }
    if (line[0]=='S'&&line[1]=='E'&&line[2]=='T'&&line[3]==' ') {
        const char *p = skip_spaces(line + 3);
        const char *key = p;
        const char *val = next_token(p);
        if (!*val) { put_str(reply, "ERR SET needs key and value"); return; }

        if (key[0]=='w'&&key[1]=='i'&&key[2]=='n'&&key[3]=='d'&&key[4]=='o'&&key[5]=='w') {
            st->window_samples = (uint32_t)atoi(val);
            put_dec(reply, "OK window=", st->window_samples); return;
        }
        if (key[0]=='r'&&key[1]=='a'&&key[2]=='t'&&key[3]=='e') {
            st->sample_rate_hz = (uint32_t)atoi(val);
            put_dec(reply, "OK rate=", st->sample_rate_hz); return;
        }
        if (key[0]=='r'&&key[1]=='e'&&key[2]=='p'&&key[3]=='e'&&key[4]=='a'&&key[5]=='t'&&key[6]=='s') {
            st->repeats = (uint32_t)atoi(val);
            put_dec(reply, "OK repeats=", st->repeats); return;
        }
        if (key[0]=='t'&&key[1]=='o'&&key[2]=='n'&&key[3]=='e') {
            ichp_tone_t t = ICHP_TONE_CHIRP;
            if      (val[0]=='c') t = ICHP_TONE_CHIRP;
            else if (val[0]=='t' && val[4]=='2') t = ICHP_TONE_200HZ;
            else if (val[0]=='t' && val[4]=='1') t = ICHP_TONE_1KHZ;
            else if (val[0]=='t' && val[4]=='5') t = ICHP_TONE_5KHZ;
            else if (val[0]=='s') t = ICHP_TONE_SILENCE;
            else { put_str(reply, "ERR tone must be chirp|tone200|tone1k|tone5k|silence"); return; }
            st->tone = t;
            put_str(reply, "OK tone set");
            return;
        }
        if (key[0]=='l'&&key[1]=='a'&&key[2]=='b'&&key[3]=='e'&&key[4]=='l') {
            str_copy(st->label, ICHP_CMD_LABEL_MAX, val, ICHP_CMD_LABEL_MAX);
            put_concat(reply, "OK label=", st->label);
            return;
        }
        put_str(reply, "ERR unknown key");
        return;
    }

    put_str(reply, "ERR unknown command");
}

int ichp_cmd_feed_byte(ichp_cmd_state_t *st, uint8_t b,
                       char reply[ICHP_CMD_LINE_MAX])
{
    if (!st || !reply) return 0;
    reply[0] = '\0';

    if (b == '\r') return 0;
    if (b != '\n') {
        if (s_len < ICHP_CMD_LINE_MAX - 1) {
            s_line[s_len++] = (char)b;
            s_line[s_len]    = '\0';
        }
        return 0;
    }

    s_line[s_len] = '\0';
    handle_line(st, s_line, reply);
    s_len = 0;
    s_line[0] = '\0';
    return 1;
}
