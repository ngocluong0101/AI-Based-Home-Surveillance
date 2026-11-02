/* Tests for heartbeat_record.c: the JSON of the heartbeat (ROADMAP 3.6). */
#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "heartbeat_record.h"
#include "json_mini.h"
#include "test_util.h"

#define RUN_ID "0f8fad5b-d9cb-469f-a165-70867728950e"

static HeartbeatCamera cam(int id, const char *state, double fps, double age)
{
    HeartbeatCamera c;
    memset(&c, 0, sizeof(c));
    c.camera_id = id;
    strcpy(c.state, state);
    c.fps = fps;
    c.last_frame_age = age;
    return c;
}

void test_heartbeat_golden_json(void)
{
    HeartbeatCamera cams[2] = { cam(1, "RUNNING", 28.43, 0.03), cam(2, "RECONNECTING", 0.0, 12.5) };
    char json[512];

    int n = heartbeat_to_json(RUN_ID, cams, 2, json, sizeof(json));
    CHECK(n > 0 && (size_t)n == strlen(json));
    CHECK(strcmp(json,
                 "{\"schema_version\":1,\"run_id\":\"" RUN_ID "\",\"cameras\":["
                 "{\"camera_id\":1,\"state\":\"RUNNING\",\"fps\":28.4,\"last_frame_age\":0.030},"
                 "{\"camera_id\":2,\"state\":\"RECONNECTING\",\"fps\":0.0,\"last_frame_age\":12.500}]}") == 0);

    /* no camera at all is still a valid heartbeat */
    n = heartbeat_to_json(RUN_ID, NULL, 0, json, sizeof(json));
    CHECK(n > 0);
    CHECK(strcmp(json, "{\"schema_version\":1,\"run_id\":\"" RUN_ID "\",\"cameras\":[]}") == 0);
}

void test_heartbeat_json_parses_back(void)
{
    HeartbeatCamera cams[3] = { cam(0, "RUNNING", 30.0, 0.0), cam(7, "RUNNING", 14.96, 1.0005),
                                cam(2147483647, "RECONNECTING", 0.0, 99.0) };
    char json[512];
    CHECK(heartbeat_to_json(RUN_ID, cams, 3, json, sizeof(json)) > 0);
    CHECK(strchr(json, '\n') == NULL);   /* one line */

    JsonValue *doc = json_parse(json);
    CHECK(doc != NULL);
    if (doc == NULL)
        return;
    CHECK(json_get(doc, "schema_version")->integer == 1);
    CHECK(strcmp(json_get(doc, "run_id")->string, RUN_ID) == 0);
    const JsonValue *list = json_get(doc, "cameras");
    CHECK(list != NULL && list->count == 3);
    if (list != NULL && list->count == 3) {
        CHECK(json_get(&list->items[1], "camera_id")->integer == 7);
        CHECK_NEAR(json_get(&list->items[1], "fps")->number, 15.0, 1e-9);   /* 14.96 -> 1 decimal */
        CHECK_NEAR(json_get(&list->items[1], "last_frame_age")->number, 1.001, 1e-9);
        CHECK(json_get(&list->items[2], "camera_id")->integer == 2147483647);
        CHECK(strcmp(json_get(&list->items[2], "state")->string, "RECONNECTING") == 0);
    }
    json_free(doc);
}

void test_heartbeat_numbers_are_always_valid_json(void)
{
    HeartbeatCamera cams[3] = { cam(1, "RUNNING", NAN, -5.0), cam(2, "RUNNING", INFINITY, 1e300),
                                cam(3, "RUNNING", -0.0, 1e9) };
    char json[512];
    CHECK(heartbeat_to_json(RUN_ID, cams, 3, json, sizeof(json)) > 0);
    CHECK(strstr(json, "\"fps\":0.0,\"last_frame_age\":0.000}") != NULL);          /* NaN, negative */
    CHECK(strstr(json, "\"fps\":1000000000.0,\"last_frame_age\":1000000000.000}") != NULL);   /* clamped */
    CHECK(strstr(json, "nan") == NULL && strstr(json, "inf") == NULL);

    JsonValue *doc = json_parse(json);
    CHECK(doc != NULL);
    json_free(doc);
}

void test_heartbeat_escapes_the_run_id(void)
{
    HeartbeatCamera c = cam(1, "RUNNING", 1.0, 1.0);
    char json[256];
    CHECK(heartbeat_to_json("a\"b\\c\n", &c, 1, json, sizeof(json)) > 0);
    CHECK(strchr(json, '\n') == NULL);
    CHECK(strstr(json, "\"run_id\":\"a\\\"b\\\\c\\u000a\"") != NULL);
}

void test_heartbeat_capacity_formula_suffices(void)
{
    enum { COUNT = 64 };
    HeartbeatCamera cams[COUNT];
    for (int i = 0; i < COUNT; i++)
        cams[i] = cam(2147483647 - i, "RECONNECTING", 999999999.9, 999999999.999);

    size_t cap = heartbeat_json_capacity(COUNT);
    char *json = malloc(cap);
    CHECK(json != NULL);
    if (json == NULL)
        return;
    int n = heartbeat_to_json(RUN_ID, cams, COUNT, json, cap);
    CHECK(n > 0 && (size_t)n == strlen(json));
    free(json);
}

void test_heartbeat_small_buffer_fails_cleanly(void)
{
    HeartbeatCamera c = cam(1, "RUNNING", 1.0, 1.0);
    char json[512];
    int full = heartbeat_to_json(RUN_ID, &c, 1, json, sizeof(json));
    CHECK(full > 0);

    for (size_t cap = 0; cap <= (size_t)full; cap++) {   /* one byte short of the NUL and below */
        char small[512];
        memset(small, 'x', sizeof(small));
        CHECK(heartbeat_to_json(RUN_ID, &c, 1, small, cap) == -1);
        if (cap > 0)
            CHECK(small[0] == '\0');
        CHECK(small[cap] == 'x');   /* nothing written past cap */
    }
    CHECK(heartbeat_to_json(RUN_ID, &c, 1, json, (size_t)full + 1) == full);
}
