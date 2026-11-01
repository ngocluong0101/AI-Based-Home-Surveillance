/*
 * Tests for event_manager.c: the shared vectors (tests/vectors/event_manager_*.json, the same files
 * tests/test_event_manager_vectors.py replays on the Python specification), plus the few things that
 * exist only in the C version (capacity of the output array, destroy, several instances).
 */
#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "event_manager.h"
#include "json_mini.h"
#include "test_util.h"

/* Directory of the vectors relative to engine/, where `make test-core` runs; set by the Makefile. */
#ifndef VECTOR_DIR
#define VECTOR_DIR "../tests/vectors"
#endif

/* A failing check inside a vector says which file and step it was. */
#define VCHECK(cond, name, step)                                                          \
    do {                                                                                  \
        if (!(cond)) {                                                                    \
            fprintf(stderr, "FAIL %s:%d: %s [vector %s, step %zu]\n", __FILE__, __LINE__, \
                    #cond, (name), (size_t)(step));                                       \
            g_failures++;                                                                 \
        }                                                                                 \
    } while (0)

static char *read_file(const char *path)
{
    FILE *f = fopen(path, "rb");
    if (f == NULL)
        return NULL;

    size_t cap = 4096, len = 0;
    char *buf = malloc(cap);
    while (buf != NULL) {
        size_t n = fread(buf + len, 1, cap - len - 1, f);
        len += n;
        if (n == 0)
            break;
        if (len + 1 >= cap) {
            char *bigger = realloc(buf, cap * 2);
            if (bigger == NULL) {
                free(buf);
                buf = NULL;
                break;
            }
            buf = bigger;
            cap *= 2;
        }
    }
    fclose(f);
    if (buf != NULL)
        buf[len] = '\0';
    return buf;
}

static int near_float(double a, double b)
{
    double d = a > b ? a - b : b - a;
    return d < 1e-6;
}

/* Run one vector file on a fresh EventManager. Returns 0 if it could be read and had steps. */
static int run_vector(const char *path, const char *name)
{
    char *text = read_file(path);
    JsonValue *doc = (text != NULL) ? json_parse(text) : NULL;
    free(text);
    const JsonValue *timeout = json_get(doc, "absence_timeout_s");
    const JsonValue *steps = json_get(doc, "steps");
    if (doc == NULL || timeout == NULL || steps == NULL || steps->type != JSON_ARRAY ||
        steps->count == 0) {
        fprintf(stderr, "FAIL cannot read vector %s\n", path);
        g_failures++;
        json_free(doc);
        return -1;
    }

    EventManager em;
    event_manager_init(&em, timeout->number);

    for (size_t s = 0; s < steps->count; s++) {
        const JsonValue *step = &steps->items[s];
        const JsonValue *now = json_get(step, "now_ns");
        const JsonValue *dets_json = json_get(step, "detections");
        const JsonValue *expected = json_get(step, "expected_events");
        const JsonValue *tracks_after = json_get(step, "tracks_after");
        if (now == NULL || !now->is_integer || dets_json == NULL || expected == NULL ||
            tracks_after == NULL) {
            VCHECK(0 && "malformed step", name, s);
            continue;
        }

        size_t n = dets_json->count;
        Detection *dets = calloc(n + 1, sizeof(*dets));
        PersonDetectedEvent *events = calloc(n + 1, sizeof(*events));
        VCHECK(dets != NULL && events != NULL, name, s);
        if (dets == NULL || events == NULL) {
            free(dets);
            free(events);
            break;
        }
        for (size_t i = 0; i < n; i++) {
            const JsonValue *d = &dets_json->items[i];
            dets[i].source_id = 7;  /* arbitrary: must come back unchanged in the events */
            dets[i].track_id = (uint64_t)json_get(d, "track_id")->integer;
            dets[i].confidence = (float)json_get(d, "conf")->number;
        }

        int got = event_manager_process(&em, dets, n, now->integer, events, n);

        VCHECK(got >= 0 && (size_t)got == expected->count, name, s);
        for (size_t i = 0; got >= 0 && i < (size_t)got && i < expected->count; i++) {
            const JsonValue *e = &expected->items[i];
            VCHECK(events[i].track_id == (uint64_t)json_get(e, "track_id")->integer, name, s);
            VCHECK(near_float(events[i].confidence, json_get(e, "confidence")->number), name, s);
            VCHECK(events[i].timestamp_ns == now->integer, name, s);
            VCHECK(events[i].source_id == 7, name, s);
        }
        VCHECK((int64_t)event_manager_track_count(&em) == tracks_after->integer, name, s);

        free(dets);
        free(events);
    }

    event_manager_destroy(&em);
    json_free(doc);
    return 0;
}

void test_event_manager_shared_vectors(void)
{
    DIR *dir = opendir(VECTOR_DIR);
    if (dir == NULL) {
        fprintf(stderr, "FAIL cannot open vector directory %s (run make from engine/)\n", VECTOR_DIR);
        g_failures++;
        return;
    }

    int ran = 0;
    for (struct dirent *ent = readdir(dir); ent != NULL; ent = readdir(dir)) {
        size_t len = strlen(ent->d_name);
        if (strncmp(ent->d_name, "event_manager_", 14) != 0 || len < 5 ||
            strcmp(ent->d_name + len - 5, ".json") != 0)
            continue;

        char path[1024];
        snprintf(path, sizeof(path), "%s/%s", VECTOR_DIR, ent->d_name);
        if (run_vector(path, ent->d_name) == 0)
            ran++;
    }
    closedir(dir);

    printf("     %d vector files\n", ran);
    CHECK(ran > 0);
}

static Detection make_det(uint32_t source_id, uint64_t track_id, float conf)
{
    Detection d = { 0 };
    d.source_id = source_id;
    d.track_id = track_id;
    d.confidence = conf;
    return d;
}

void test_event_manager_output_capacity(void)
{
    EventManager em;
    Detection dets[3] = { make_det(0, 1, 0.9f), make_det(0, 2, 0.8f), make_det(0, 3, 0.7f) };
    PersonDetectedEvent out[3];

    event_manager_init(&em, 10.0);

    /* room for 2 of the 3 events: the first two are written, the third dropped, all 3 remembered */
    CHECK(event_manager_process(&em, dets, 3, 0, out, 2) == 2);
    CHECK(out[0].track_id == 1);
    CHECK(out[1].track_id == 2);
    CHECK(event_manager_track_count(&em) == 3);
    /* the dropped one is not "new" any more */
    CHECK(event_manager_process(&em, dets, 3, 1000, out, 3) == 0);

    /* nothing to say and nothing to write to */
    CHECK(event_manager_process(&em, NULL, 0, 2000, NULL, 0) == 0);

    event_manager_destroy(&em);
}

void test_event_manager_destroy_is_safe(void)
{
    EventManager em;
    Detection d = make_det(0, 1, 0.9f);
    PersonDetectedEvent out[1];

    event_manager_init(&em, 10.0);
    event_manager_destroy(&em);                 /* never used */
    event_manager_destroy(&em);                 /* twice */

    event_manager_init(&em, 10.0);              /* reused after destroy */
    CHECK(event_manager_process(&em, &d, 1, 0, out, 1) == 1);
    CHECK(event_manager_track_count(&em) == 1);
    event_manager_destroy(&em);
    CHECK(event_manager_track_count(&em) == 0);
}

void test_event_manager_instances_are_independent(void)
{
    /* One manager per source: the same track ID in two sources is two different people. */
    EventManager a, b;
    Detection da = make_det(0, 1, 0.9f), db = make_det(1, 1, 0.8f);
    PersonDetectedEvent out[1];

    event_manager_init(&a, 10.0);
    event_manager_init(&b, 10.0);

    CHECK(event_manager_process(&a, &da, 1, 0, out, 1) == 1);
    CHECK(out[0].source_id == 0);
    CHECK(event_manager_process(&b, &db, 1, 0, out, 1) == 1);
    CHECK(out[0].source_id == 1);
    CHECK(event_manager_process(&a, &da, 1, 1000, out, 1) == 0);
    CHECK(event_manager_process(&b, &db, 1, 1000, out, 1) == 0);

    event_manager_destroy(&a);
    event_manager_destroy(&b);
}
