#include "heartbeat_record.h"

#include <stdio.h>
#include <string.h>

/* Largest value printed; keeps the integer arithmetic below far from overflow. */
#define NUMBER_CLAMP 1e9

typedef struct {
    char *buf;
    size_t cap;
    size_t len;
    int overflow;
} Writer;

static void put_raw(Writer *w, const char *s, size_t n)
{
    if (w->overflow || n >= w->cap - w->len) {   /* keep one byte for the NUL */
        w->overflow = 1;
        return;
    }
    memcpy(w->buf + w->len, s, n);
    w->len += n;
}

static void put(Writer *w, const char *s)
{
    put_raw(w, s, strlen(s));
}

/* A JSON string literal; control characters, '"' and '\' are escaped. */
static void put_string(Writer *w, const char *s)
{
    put(w, "\"");
    for (; *s != '\0'; s++) {
        unsigned char c = (unsigned char)*s;
        if (c == '"' || c == '\\') {
            char esc[2] = { '\\', (char)c };
            put_raw(w, esc, 2);
        } else if (c < 0x20) {
            char esc[7];
            snprintf(esc, sizeof(esc), "\\u%04x", c);
            put_raw(w, esc, 6);
        } else {
            put_raw(w, (const char *)&c, 1);
        }
    }
    put(w, "\"");
}

/* v >= 0 with `decimals` decimals (1 or 3), rounded; NaN and negatives print as 0. */
static void put_fixed(Writer *w, double v, int decimals)
{
    if (!(v > 0.0))   /* also NaN */
        v = 0.0;
    if (v > NUMBER_CLAMP)
        v = NUMBER_CLAMP;
    unsigned long long scale = decimals == 1 ? 10ull : 1000ull;
    unsigned long long scaled = (unsigned long long)(v * (double)scale + 0.5);
    char tmp[40];
    int n = decimals == 1
        ? snprintf(tmp, sizeof(tmp), "%llu.%01llu", scaled / scale, scaled % scale)
        : snprintf(tmp, sizeof(tmp), "%llu.%03llu", scaled / scale, scaled % scale);
    put_raw(w, tmp, (size_t)n);
}

int heartbeat_to_json(const char *run_id, const HeartbeatCamera *cameras, size_t count, char *out,
                      size_t cap)
{
    Writer w = { out, cap, 0, 0 };
    char tmp[48];

    if (cap == 0)
        return -1;
    put(&w, "{\"schema_version\":1,\"run_id\":");
    put_string(&w, run_id);
    put(&w, ",\"cameras\":[");
    for (size_t i = 0; i < count; i++) {
        const HeartbeatCamera *c = &cameras[i];
        if (i > 0)
            put(&w, ",");
        snprintf(tmp, sizeof(tmp), "{\"camera_id\":%d,\"state\":", (int)c->camera_id);
        put(&w, tmp);
        put_string(&w, c->state);
        put(&w, ",\"fps\":");
        put_fixed(&w, c->fps, 1);
        put(&w, ",\"last_frame_age\":");
        put_fixed(&w, c->last_frame_age, 3);
        put(&w, "}");
    }
    put(&w, "]}");

    if (w.overflow) {
        out[0] = '\0';
        return -1;
    }
    out[w.len] = '\0';
    return (int)w.len;
}
