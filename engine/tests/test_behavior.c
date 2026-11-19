/*
 * Tests for behavior.c: the shared vectors (tests/vectors/behavior_*.json, the same files tests/test_behavior_vectors.py replays
 * on the Python specification), plus the few things that exist only in the C version (output capacity, set_zones limits,
 * changing the config of a running instance, memory freed twice).
 */
#include <dirent.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "behavior.h"
#include "json_mini.h"
#include "test_util.h"

#ifndef VECTOR_DIR
#define VECTOR_DIR "../tests/vectors"
#endif

#define VCHECK(cond, name, step)                                                                  \
    do {                                                                                          \
        if (!(cond)) {                                                                            \
            fprintf(stderr, "FAIL %s:%d: %s [vector %s, step %zu]\n", __FILE__, __LINE__, #cond, \
                    (name), (size_t)(step));                                                      \
            g_failures++;                                                                         \
        }                                                                                         \
    } while (0)

static char *read_file(const char *path)
{
    FILE *f = fopen(path, "rb");
    if (f == NULL)
        return NULL;
    size_t cap = 1 << 16, len = 0;
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

/* The fields of BehaviorConfig by their JSON names. */
static void apply_config(BehaviorConfig *c, const JsonValue *obj)
{
    struct { const char *name; int *flag; double *num; } fields[] = {
        { "fall", &c->fall, NULL }, { "fight", &c->fight, NULL }, { "zones", &c->zones, NULL },
        { "vis_min", NULL, &c->vis_min }, { "forget_s", NULL, &c->forget_s },
        { "fall_down_deg", NULL, &c->fall_down_deg }, { "fall_up_deg", NULL, &c->fall_up_deg },
        { "fall_drop_window_s", NULL, &c->fall_drop_window_s }, { "fall_hip_drop_min", NULL, &c->fall_hip_drop_min },
        { "fall_still_s", NULL, &c->fall_still_s }, { "fall_settle_s", NULL, &c->fall_settle_s },
        { "fall_hip_window_s", NULL, &c->fall_hip_window_s }, { "fall_move_max", NULL, &c->fall_move_max },
        { "fall_lying_aspect", NULL, &c->fall_lying_aspect },
        { "fall_stand_back_s", NULL, &c->fall_stand_back_s }, { "fall_lost_s", NULL, &c->fall_lost_s },
        { "fall_cooldown_s", NULL, &c->fall_cooldown_s }, { "fall_min_torso_px", NULL, &c->fall_min_torso_px },
        { "fall_net", NULL, &c->fall_net }, { "fall_net_thr", NULL, &c->fall_net_thr }, { "fall_net_k", NULL, &c->fall_net_k },
        { "fight_speed", NULL, &c->fight_speed }, { "fight_decay_s", NULL, &c->fight_decay_s },
        { "fight_threshold_s", NULL, &c->fight_threshold_s }, { "fight_rearm", NULL, &c->fight_rearm },
        { "fight_cooldown_s", NULL, &c->fight_cooldown_s }, { "fight_max_dt_s", NULL, &c->fight_max_dt_s },
        { "zone_dwell_s", NULL, &c->zone_dwell_s }, { "zone_exit_s", NULL, &c->zone_exit_s },
        { "zone_rearm_s", NULL, &c->zone_rearm_s },
    };
    for (size_t i = 0; i < sizeof(fields) / sizeof(fields[0]); i++) {
        const JsonValue *v = json_get(obj, fields[i].name);
        if (v == NULL)
            continue;
        if (fields[i].flag != NULL)
            *fields[i].flag = v->number != 0.0;
        else
            *fields[i].num = v->number;
    }
}

static BehaviorType type_of(const char *name)
{
    if (strcmp(name, "FALL") == 0)
        return BEHAVIOR_FALL;
    if (strcmp(name, "FIGHT") == 0)
        return BEHAVIOR_FIGHT;
    return BEHAVIOR_RESTRICTED_AREA;
}

/* Run one vector file on a fresh Behavior. Returns 0 if it could be read and had steps. */
static int run_vector(const char *path, const char *name)
{
    char *text = read_file(path);
    JsonValue *doc = text ? json_parse(text) : NULL;
    free(text);
    if (doc == NULL)
        return -1;
    const JsonValue *steps = json_get(doc, "steps");
    const JsonValue *frame = json_get(doc, "frame");
    const JsonValue *zones = json_get(doc, "zones");
    if (steps == NULL || frame == NULL || zones == NULL || steps->count == 0) {
        json_free(doc);
        return -1;
    }

    BehaviorConfig cfg;
    behavior_config_default(&cfg);
    apply_config(&cfg, json_get(doc, "config"));
    Behavior *b = behavior_new(&cfg, frame->items[0].number, frame->items[1].number);
    VCHECK(b != NULL, name, 0);

    BehaviorZone zs[BEHAVIOR_MAX_ZONES];
    memset(zs, 0, sizeof(zs));
    for (size_t i = 0; i < zones->count && i < BEHAVIOR_MAX_ZONES; i++) {
        const JsonValue *z = &zones->items[i];
        const char *kind = json_get(z, "kind")->string;
        zs[i].kind = strcmp(kind, "restricted") == 0 ? BEHAVIOR_ZONE_RESTRICTED
                     : strcmp(kind, "lie_ok") == 0 ? BEHAVIOR_ZONE_LIE_OK : BEHAVIOR_ZONE_IGNORE;
        zs[i].zone_id = (int)json_get(z, "zone_id")->integer;
        const JsonValue *pts = json_get(z, "points");
        zs[i].num_points = pts->count;
        for (size_t k = 0; k < pts->count && k < BEHAVIOR_MAX_ZONE_POINTS; k++) {
            zs[i].points[k][0] = pts->items[k].items[0].number;
            zs[i].points[k][1] = pts->items[k].items[1].number;
        }
    }
    VCHECK(behavior_set_zones(b, zs, zones->count) == 0, name, 0);

    BehaviorEvent got[16];
    for (size_t s = 0; s < steps->count; s++) {
        const JsonValue *step = &steps->items[s];
        const JsonValue *people = json_get(step, "people");
        BehaviorPerson ppl[16];
        memset(ppl, 0, sizeof(ppl));
        size_t np = people->count < 16 ? people->count : 16;
        for (size_t i = 0; i < np; i++) {
            const JsonValue *pj = &people->items[i];
            ppl[i].track_id = (uint64_t)json_get(pj, "track_id")->integer;
            const JsonValue *box = json_get(pj, "box");
            for (int k = 0; k < 4; k++)
                ppl[i].box[k] = box->items[k].number;
            const JsonValue *kps = json_get(pj, "keypoints");
            ppl[i].has_pose = kps->type == JSON_ARRAY;
            if (ppl[i].has_pose) {
                for (int k = 0; k < BEHAVIOR_KEYPOINTS; k++) {
                    ppl[i].kp[k].x = kps->items[k].items[0].number;
                    ppl[i].kp[k].y = kps->items[k].items[1].number;
                    ppl[i].kp[k].v = kps->items[k].items[2].number;
                }
            }
        }
        int64_t now = json_get(step, "now_ns")->integer;
        const JsonValue *sj = json_get(step, "scores");
        BehaviorScore sc[16];
        size_t nsc = 0;
        for (size_t k = 0; sj != NULL && k < sj->count && k < 16; k++, nsc++) {
            sc[k].track_id = (uint64_t)sj->items[k].items[0].integer;
            sc[k].prob = sj->items[k].items[1].number;
            sc[k].end_ns = sj->items[k].items[2].integer;
        }
        int n = behavior_process_scored(b, ppl, np, sc, nsc, now, got, 16);
        const JsonValue *want = json_get(step, "expected_events");
        VCHECK(n == (int)want->count, name, s);
        for (int e = 0; e < n && (size_t)e < want->count; e++) {
            const JsonValue *w = &want->items[e];
            VCHECK(got[e].type == type_of(json_get(w, "type")->string), name, s);
            const JsonValue *ids = json_get(w, "track_ids");
            VCHECK(got[e].num_tracks == (int)ids->count, name, s);
            for (size_t k = 0; k < ids->count && k < 2; k++)
                VCHECK(got[e].track_ids[k] == (uint64_t)ids->items[k].integer, name, s);
            VCHECK(got[e].zone_id == (int)json_get(w, "zone_id")->integer, name, s);
            VCHECK(got[e].occurred_ns == json_get(w, "occurred_ns")->integer, name, s);
            VCHECK(got[e].now_ns == now, name, s);
            VCHECK(fabs(got[e].score - json_get(w, "score")->number) < 1e-5, name, s);
        }
        VCHECK(behavior_track_count(b) == (size_t)json_get(step, "tracks_after")->integer, name, s);
    }
    behavior_free(b);
    json_free(doc);
    return 0;
}

void test_behavior_shared_vectors(void)
{
    DIR *d = opendir(VECTOR_DIR);
    CHECK(d != NULL);
    if (d == NULL)
        return;
    int files = 0;
    struct dirent *ent;
    while ((ent = readdir(d)) != NULL) {
        if (strncmp(ent->d_name, "behavior_", 9) != 0 || strstr(ent->d_name, ".json") == NULL)
            continue;
        char path[512];
        snprintf(path, sizeof(path), "%s/%s", VECTOR_DIR, ent->d_name);
        CHECK(run_vector(path, ent->d_name) == 0);
        files++;
    }
    closedir(d);
    CHECK(files >= 15);   /* the point of the vectors is lost if they silently go missing */
}

void test_behavior_point_in_polygon(void)
{
    const double square[4][2] = { { 0, 0 }, { 1, 0 }, { 1, 1 }, { 0, 1 } };
    CHECK(behavior_point_in_polygon(50, 50, square, 4, 100, 100));
    CHECK(!behavior_point_in_polygon(150, 50, square, 4, 100, 100));
    const double notch[5][2] = { { 0, 0 }, { 1, 0 }, { 1, 1 }, { 0.5, 0.4 }, { 0, 1 } };
    CHECK(behavior_point_in_polygon(20, 20, notch, 5, 100, 100));
    CHECK(!behavior_point_in_polygon(50, 80, notch, 5, 100, 100));
    CHECK(!behavior_point_in_polygon(1, 1, square, 0, 100, 100));
}

static BehaviorPerson idle_person(uint64_t id, double x)
{
    BehaviorPerson p;
    memset(&p, 0, sizeof(p));
    p.track_id = id;
    p.box[0] = x;
    p.box[1] = 100;
    p.box[2] = 50;
    p.box[3] = 200;
    return p;
}

void test_behavior_output_capacity_and_zone_limits(void)
{
    BehaviorConfig cfg;
    behavior_config_default(&cfg);
    cfg.zones = 1;
    cfg.zone_dwell_s = 0.0;
    Behavior *b = behavior_new(&cfg, 1000, 1000);
    CHECK(b != NULL);
    if (b == NULL)
        return;
    BehaviorZone z;
    memset(&z, 0, sizeof(z));
    z.kind = BEHAVIOR_ZONE_RESTRICTED;
    z.zone_id = 7;
    z.num_points = 4;
    const double sq[4][2] = { { 0, 0 }, { 1, 0 }, { 1, 1 }, { 0, 1 } };
    memcpy(z.points, sq, sizeof(sq));
    CHECK(behavior_set_zones(b, &z, 1) == 0);

    BehaviorPerson ppl[3] = { idle_person(1, 100), idle_person(2, 300), idle_person(3, 500) };
    BehaviorEvent out[1];
    CHECK(behavior_process(b, ppl, 3, 1000000000LL, out, 1) == 1);   /* three events, room for one: one written */
    CHECK(out[0].track_ids[0] == 1 && out[0].zone_id == 7);
    CHECK(behavior_process(b, ppl, 3, 2000000000LL, out, 1) == 0);   /* the state was updated: no repeat */

    BehaviorZone nine[BEHAVIOR_MAX_ZONES + 1];
    memset(nine, 0, sizeof(nine));
    CHECK(behavior_set_zones(b, nine, BEHAVIOR_MAX_ZONES + 1) == -1);   /* refused, the old zone is still there */
    CHECK(behavior_process(b, ppl, 3, 3000000000LL, out, 1) == 0);

    CHECK(behavior_set_zones(b, &z, 1) == 0);                        /* new zones: what was remembered is cleared */
    CHECK(behavior_process(b, ppl, 3, 4000000000LL, out, 1) == 1);
    behavior_free(b);
    behavior_free(NULL);   /* harmless */
}

void test_behavior_config_can_change_while_running(void)
{
    BehaviorConfig cfg;
    behavior_config_default(&cfg);
    cfg.zones = 0;                       /* the zone rule is off: standing in the zone raises nothing */
    Behavior *b = behavior_new(&cfg, 1000, 1000);
    CHECK(b != NULL);
    if (b == NULL)
        return;
    BehaviorZone z;
    memset(&z, 0, sizeof(z));
    z.kind = BEHAVIOR_ZONE_RESTRICTED;
    z.num_points = 4;
    const double sq[4][2] = { { 0, 0 }, { 1, 0 }, { 1, 1 }, { 0, 1 } };
    memcpy(z.points, sq, sizeof(sq));
    behavior_set_zones(b, &z, 1);

    BehaviorPerson p = idle_person(1, 100);
    BehaviorEvent out[2];
    CHECK(behavior_process(b, &p, 1, 1000000000LL, out, 2) == 0);
    CHECK(behavior_process(b, &p, 1, 9000000000LL, out, 2) == 0);
    cfg.zones = 1;
    behavior_set_config(b, &cfg);
    CHECK(behavior_process(b, &p, 1, 10000000000LL, out, 2) == 0);   /* dwell starts now */
    CHECK(behavior_process(b, &p, 1, 13000000000LL, out, 2) == 1);   /* zone_dwell_s = 3 s later */
    CHECK(out[0].occurred_ns == 10000000000LL);
    behavior_free(b);
}
