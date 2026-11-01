#include "event_record.h"

#include <stdio.h>
#include <string.h>

typedef struct {
    char *buf;
    size_t cap;
    size_t len;
    int overflow;
} Writer;

static void put_raw(Writer *w, const char *s, size_t n)
{
    if (w->overflow || n >= w->cap - w->len) {  /* keep one byte for the NUL */
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

/* Integers only (%d, %llu): never a float, so the locale cannot change the output. */
static void put_u64(Writer *w, uint64_t v)
{
    char tmp[24];
    int n = snprintf(tmp, sizeof(tmp), "%llu", (unsigned long long)v);
    put_raw(w, tmp, (size_t)n);
}

static void put_i64(Writer *w, int64_t v)
{
    char tmp[24];
    int n = snprintf(tmp, sizeof(tmp), "%lld", (long long)v);
    put_raw(w, tmp, (size_t)n);
}

/* A JSON string literal; control characters, '"' and '\' are escaped. Bytes >= 0x80 pass through
 * (UTF-8 is valid JSON). */
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

/* v in 0..1 as "0.9100" (4 decimals, rounded); NaN -> 0, out of range is clamped. */
static void put_unit_float(Writer *w, float v)
{
    double d = (double)v;
    if (!(d > 0.0))   /* also NaN */
        d = 0.0;
    if (d > 1.0)
        d = 1.0;
    unsigned ten_thousandths = (unsigned)(d * 10000.0 + 0.5);
    char tmp[16];
    int n = snprintf(tmp, sizeof(tmp), "%u.%04u", ten_thousandths / 10000, ten_thousandths % 10000);
    put_raw(w, tmp, (size_t)n);
}

/* floor(a / b) for b > 0, also for negative a. */
static int64_t floor_div(int64_t a, int64_t b)
{
    int64_t q = a / b;
    return (a % b < 0) ? q - 1 : q;
}

typedef struct {
    int year, month, day, hour, minute, second, millisecond;
} Civil;

/* ns since the Unix epoch -> UTC calendar fields. Civil-from-days after H. Hinnant. */
static void civil_from_ns(int64_t ns, Civil *c)
{
    int64_t ms_total = floor_div(ns, 1000000);
    int64_t secs = floor_div(ms_total, 1000);
    int64_t days = floor_div(secs, 86400);
    int sod = (int)(secs - days * 86400);

    int64_t z = days + 719468;
    int64_t era = floor_div(z, 146097);
    int64_t doe = z - era * 146097;
    int64_t yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    int64_t y = yoe + era * 400;
    int64_t doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    int64_t mp = (5 * doy + 2) / 153;
    c->day = (int)(doy - (153 * mp + 2) / 5 + 1);
    c->month = (int)(mp < 10 ? mp + 3 : mp - 9);
    c->year = (int)(c->month <= 2 ? y + 1 : y);
    c->hour = sod / 3600;
    c->minute = sod % 3600 / 60;
    c->second = sod % 60;
    c->millisecond = (int)(ms_total - secs * 1000);
}

/* "YYYY-MM-DDTHH:MM:SS.mmmZ" (UTC). */
static void put_timestamp(Writer *w, int64_t ns)
{
    Civil c;
    civil_from_ns(ns, &c);
    char tmp[40];
    int n = snprintf(tmp, sizeof(tmp), "%04d-%02d-%02dT%02d:%02d:%02d.%03dZ", c.year, c.month, c.day,
                     c.hour, c.minute, c.second, c.millisecond);
    put_raw(w, tmp, (size_t)n);
}

int event_snapshot_name(const EventRecord *r, char *out, size_t cap)
{
    Civil c;
    civil_from_ns(r->timestamp_ns, &c);

    /* Only the first 8 characters of the event ID, and only characters that are safe in a file name. */
    char id8[9] = { 0 };
    for (int i = 0; i < 8 && r->event_id[i] != '\0'; i++) {
        char ch = r->event_id[i];
        int ok = (ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z');
        id8[i] = ok ? ch : '_';
    }

    int n = snprintf(out, cap, "PERSON_DETECTED_cam%d_id%llu_%04d%02d%02dT%02d%02d%02d_%s.jpg",
                     (int)r->camera_id, (unsigned long long)r->track_id, c.year, c.month, c.day,
                     c.hour, c.minute, c.second, id8);
    if (n < 0 || (size_t)n >= cap) {
        if (cap > 0)
            out[0] = '\0';
        return -1;
    }
    return n;
}

int event_record_to_json(const EventRecord *r, char *out, size_t cap)
{
    if (cap == 0)
        return -1;
    Writer w = { out, cap, 0, 0 };

    put(&w, "{\"schema_version\":");
    put_u64(&w, EVENT_RECORD_SCHEMA_VERSION);
    put(&w, ",\"event_id\":");
    put_string(&w, r->event_id);
    put(&w, ",\"camera_id\":");
    put_i64(&w, r->camera_id);
    put(&w, ",\"pipeline_run_id\":");
    put_string(&w, r->pipeline_run_id);
    put(&w, ",\"type\":\"PERSON_DETECTED\",\"track_id\":");
    put_u64(&w, r->track_id);
    put(&w, ",\"confidence\":");
    put_unit_float(&w, r->confidence);
    put(&w, ",\"timestamp\":\"");
    put_timestamp(&w, r->timestamp_ns);
    put(&w, "\",\"bbox\":[");
    for (int i = 0; i < 4; i++) {
        if (i > 0)
            put(&w, ",");
        put_unit_float(&w, r->bbox[i]);
    }
    put(&w, "],\"snapshot\":");
    if (r->snapshot[0] == '\0')
        put(&w, "null");
    else
        put_string(&w, r->snapshot);
    put(&w, "}");

    if (w.overflow)
        return -1;
    out[w.len] = '\0';
    return (int)w.len;
}
