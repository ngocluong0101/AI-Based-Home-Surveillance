/* Tests for event_record.c: the JSON of event schema v1 (ROADMAP 2.3). */
#include <stdio.h>
#include <string.h>

#include "event_record.h"
#include "json_mini.h"
#include "test_util.h"

#define ID_A "123e4567-e89b-42d3-a456-426614174000"
#define ID_B "0f8fad5b-d9cb-469f-a165-70867728950e"

static EventRecord sample(void)
{
    EventRecord r;
    memset(&r, 0, sizeof(r));
    strcpy(r.event_id, ID_A);
    strcpy(r.pipeline_run_id, ID_B);
    r.camera_id = 1;
    r.track_id = 12;
    r.confidence = 0.91f;
    r.timestamp_ns = 1767268800123456789LL;   /* 2026-01-01T12:00:00.123456789Z */
    r.bbox[0] = 0.1f;
    r.bbox[1] = 0.2f;
    r.bbox[2] = 0.3f;
    r.bbox[3] = 0.4f;
    return r;
}

/* The timestamp field of the JSON of a record with this clock. */
static void timestamp_of(int64_t ns, char out[32])
{
    EventRecord r = sample();
    char json[EVENT_JSON_MAX];
    r.timestamp_ns = ns;
    out[0] = '\0';
    if (event_record_to_json(&r, json, sizeof(json)) < 0)
        return;
    const char *p = strstr(json, "\"timestamp\":\"");
    if (p == NULL)
        return;
    p += strlen("\"timestamp\":\"");
    memcpy(out, p, 24);
    out[24] = '\0';
}

void test_event_record_golden_json(void)
{
    EventRecord r = sample();
    char json[EVENT_JSON_MAX];

    int n = event_record_to_json(&r, json, sizeof(json));
    CHECK(n > 0 && (size_t)n == strlen(json));
    CHECK(strcmp(json,
                 "{\"schema_version\":1,\"event_id\":\"" ID_A "\",\"camera_id\":1,"
                 "\"pipeline_run_id\":\"" ID_B "\",\"type\":\"PERSON_DETECTED\",\"track_id\":12,"
                 "\"confidence\":0.9100,\"timestamp\":\"2026-01-01T12:00:00.123Z\","
                 "\"bbox\":[0.1000,0.2000,0.3000,0.4000],\"snapshot\":null}") == 0);

    strcpy(r.snapshot, "PERSON_DETECTED_cam1_id12_20260101T120000_123e4567.jpg");
    n = event_record_to_json(&r, json, sizeof(json));
    CHECK(n > 0);
    CHECK(strstr(json, "\"snapshot\":\"PERSON_DETECTED_cam1_id12_20260101T120000_123e4567.jpg\"}") != NULL);
}

void test_event_record_json_parses_back(void)
{
    EventRecord r = sample();
    char json[EVENT_JSON_MAX];
    r.track_id = 18446744073709551615ULL;   /* beyond int64: written exactly, as text */
    CHECK(event_record_to_json(&r, json, sizeof(json)) > 0);
    CHECK(strstr(json, "\"track_id\":18446744073709551615,") != NULL);

    /* (json_mini reads integers into int64, so parse a smaller ID back) */
    r.track_id = 1ULL << 40;
    r.camera_id = 0;
    strcpy(r.snapshot, "a\"b\\c\n.jpg");    /* hostile characters must be escaped */

    CHECK(event_record_to_json(&r, json, sizeof(json)) > 0);
    CHECK(strchr(json, '\n') == NULL);        /* one line, so it can be a JSON-lines record */
    CHECK(strstr(json, "\"snapshot\":\"a\\\"b\\\\c\\u000a.jpg\"}") != NULL);

    /* (json_mini turns \uXXXX into '?', so the parse-back uses only quote and backslash) */
    strcpy(r.snapshot, "a\"b\\c.jpg");
    CHECK(event_record_to_json(&r, json, sizeof(json)) > 0);

    JsonValue *doc = json_parse(json);
    CHECK(doc != NULL);
    if (doc == NULL)
        return;
    CHECK(json_get(doc, "schema_version")->integer == 1);
    CHECK(strcmp(json_get(doc, "event_id")->string, ID_A) == 0);
    CHECK(json_get(doc, "camera_id")->integer == 0);
    CHECK(json_get(doc, "track_id")->integer == (1LL << 40));
    CHECK(strcmp(json_get(doc, "type")->string, "PERSON_DETECTED") == 0);
    CHECK(json_get(doc, "bbox")->count == 4);
    CHECK(strcmp(json_get(doc, "snapshot")->string, "a\"b\\c.jpg") == 0);
    json_free(doc);
}

void test_event_record_timestamp_is_utc(void)
{
    char ts[32];

    timestamp_of(0, ts);
    CHECK(strcmp(ts, "1970-01-01T00:00:00.000Z") == 0);
    timestamp_of(-1000000, ts);                       /* before the epoch rounds down, not toward 0 */
    CHECK(strcmp(ts, "1969-12-31T23:59:59.999Z") == 0);
    timestamp_of(1709210096LL * 1000000000LL + 999999999LL, ts);   /* leap day, ms truncated */
    CHECK(strcmp(ts, "2024-02-29T12:34:56.999Z") == 0);
    timestamp_of(951782400LL * 1000000000LL, ts);     /* 2000 is a leap year (divisible by 400) */
    CHECK(strcmp(ts, "2000-02-29T00:00:00.000Z") == 0);
    timestamp_of(4102444799LL * 1000000000LL, ts);
    CHECK(strcmp(ts, "2099-12-31T23:59:59.000Z") == 0);
    timestamp_of(4102444800LL * 1000000000LL, ts);
    CHECK(strcmp(ts, "2100-01-01T00:00:00.000Z") == 0);
}

void test_event_record_numbers_are_clamped_and_rounded(void)
{
    EventRecord r = sample();
    char json[EVENT_JSON_MAX];

    r.confidence = 1.5f;
    r.bbox[0] = -0.25f;
    r.bbox[1] = 0.0f / 0.0f;     /* NaN */
    r.bbox[2] = 0.99996f;        /* rounds up to 1.0000 */
    r.bbox[3] = 0.00004f;        /* rounds down to 0.0000 */
    CHECK(event_record_to_json(&r, json, sizeof(json)) > 0);
    CHECK(strstr(json, "\"confidence\":1.0000,") != NULL);
    CHECK(strstr(json, "\"bbox\":[0.0000,0.0000,1.0000,0.0000]") != NULL);
}

void test_event_record_small_buffer_fails_cleanly(void)
{
    EventRecord r = sample();
    char json[EVENT_JSON_MAX];
    int full = event_record_to_json(&r, json, sizeof(json));
    char small[EVENT_JSON_MAX];

    CHECK(full > 0);
    CHECK(event_record_to_json(&r, small, 0) == -1);
    CHECK(event_record_to_json(&r, small, 1) == -1);
    CHECK(event_record_to_json(&r, small, (size_t)full) == -1);        /* no room for the NUL */
    CHECK(event_record_to_json(&r, small, (size_t)full + 1) == full);  /* exactly enough */
    CHECK(strcmp(small, json) == 0);

    /* the worst case still fits EVENT_JSON_MAX: a longest name made only of escaped characters */
    memset(r.snapshot, '\n', sizeof(r.snapshot) - 1);
    r.snapshot[sizeof(r.snapshot) - 1] = '\0';
    CHECK(event_record_to_json(&r, json, sizeof(json)) > 0);
}
