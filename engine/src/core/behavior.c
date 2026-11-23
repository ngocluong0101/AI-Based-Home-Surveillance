/*
 * @author: Ngoc Luong <ngocluongg0101@gmail.com>
 */

/* See behavior.h. A line-by-line port of src/events/behavior.py; keep the two in step. */
#include "behavior.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

#define NS 1000000000.0
#define NHITTERS 4

enum { NOSE, L_EYE, R_EYE, L_EAR, R_EAR, L_SHOULDER, R_SHOULDER, L_ELBOW, R_ELBOW, L_WRIST, R_WRIST,
       L_HIP, R_HIP, L_KNEE, R_KNEE, L_ANKLE, R_ANKLE };
static const int HITTERS[NHITTERS] = { L_WRIST, R_WRIST, L_ANKLE, R_ANKLE };

typedef enum { FALL_UNKNOWN, FALL_UPRIGHT, FALL_DOWN, FALL_LYING, FALL_FIRED } FallMode;

typedef struct {
    FallMode mode;
    int64_t last_ns, t_up;
    double sy_ref, hx_ref, hy_ref, torso_ref;
    int64_t t_down;
    double hx_down, hy_down, max_angle;
    double max_moved, last_hx, last_hy;
    int has_t_moved;
    int64_t t_moved;
    int has_up_since;
    int64_t up_since, cooldown_until;
} FallState;

typedef struct {
    int run;                  /* scores in a row at or above the threshold */
    int64_t first_ns;         /* the end of the window of the first of them */
} NetState;

typedef struct {
    int has_inside_since, has_out_since, fired;
    int64_t inside_since, out_since;
} ZoneState;

typedef struct {
    uint64_t id;
    int64_t last_seen;
    FallState fall;
    NetState net;
    ZoneState zones[BEHAVIOR_MAX_ZONES];    /* by position in Behavior.zones */
    int has_prev;
    BehaviorKeypoint prev_kp[BEHAVIOR_KEYPOINTS];
    int64_t prev_ns;
    int has_speeds;
    int speed_valid[NHITTERS];
    double speed[NHITTERS];                  /* torso lengths per second */
} Track;

typedef struct {
    uint64_t a, b;                           /* a < b */
    int64_t last_ns, last_seen;
    double bucket;
    int64_t since_ns;
    int fired;
    int64_t cooldown_until;
} Pair;

struct Behavior {
    BehaviorConfig cfg;
    double w, h;
    BehaviorZone zones[BEHAVIOR_MAX_ZONES];
    size_t num_zones;
    Track *tracks;
    size_t num_tracks, cap_tracks;
    Pair *pairs;
    size_t num_pairs, cap_pairs;
};

/* int(x * S) of the Python version: truncation toward zero */
static int64_t ns_of(double seconds)
{
    return (int64_t)(seconds * NS);
}

void behavior_config_default(BehaviorConfig *c)
{
    memset(c, 0, sizeof(*c));
    c->vis_min = 0.5;
    c->forget_s = 5.0;
    c->fall_down_deg = 60.0;
    c->fall_up_deg = 40.0;
    c->fall_drop_window_s = 1.5;
    c->fall_hip_drop_min = 0.5;
    c->fall_still_s = 2.0;
    c->fall_settle_s = 1.0;
    c->fall_hip_window_s = 0.8;
    c->fall_move_max = 1.0;
    c->fall_lying_aspect = 0.9;
    c->fall_stand_back_s = 0.5;
    c->fall_lost_s = 1.0;
    c->fall_cooldown_s = 10.0;
    c->fall_min_torso_px = 20.0;
    c->fall_net = 0;
    c->fall_net_thr = 0.9;
    c->fall_net_k = 3;
    c->fight_speed = 3.0;
    c->fight_decay_s = 2.0;
    c->fight_threshold_s = 0.4;
    c->fight_rearm = 0.1;
    c->fight_cooldown_s = 10.0;
    c->fight_max_dt_s = 0.5;
    c->zone_dwell_s = 3.0;
    c->zone_exit_s = 1.0;
    c->zone_rearm_s = 10.0;
}

Behavior *behavior_new(const BehaviorConfig *cfg, double frame_w, double frame_h)
{
    Behavior *b = calloc(1, sizeof(*b));
    if (b == NULL)
        return NULL;
    b->cfg = *cfg;
    b->w = frame_w;
    b->h = frame_h;
    return b;
}

void behavior_free(Behavior *b)
{
    if (b == NULL)
        return;
    free(b->tracks);
    free(b->pairs);
    free(b);
}

void behavior_set_config(Behavior *b, const BehaviorConfig *cfg)
{
    b->cfg = *cfg;
}

int behavior_set_zones(Behavior *b, const BehaviorZone *zones, size_t n)
{
    if (n > BEHAVIOR_MAX_ZONES)
        return -1;
    for (size_t i = 0; i < n; i++)
        b->zones[i] = zones[i];
    b->num_zones = n;
    for (size_t t = 0; t < b->num_tracks; t++)
        memset(b->tracks[t].zones, 0, sizeof(b->tracks[t].zones));
    return 0;
}

size_t behavior_track_count(const Behavior *b)
{
    return b->num_tracks;
}

int behavior_point_in_polygon(double px, double py, const double (*points)[2], size_t n, double w, double h)
{
    int inside = 0;
    if (n == 0)
        return 0;
    size_t j = n - 1;
    for (size_t i = 0; i < n; i++) {
        double xi = points[i][0] * w, yi = points[i][1] * h;
        double xj = points[j][0] * w, yj = points[j][1] * h;
        if ((yi > py) != (yj > py) && px < (xj - xi) * (py - yi) / (yj - yi) + xi)
            inside = !inside;
        j = i;
    }
    return inside;
}

static int in_zone(const Behavior *b, const BehaviorZone *z, double x, double y)
{
    if (z->num_points < 3)
        return 0;   /* the Python version gives False for a degenerate polygon too (no ray crosses it twice) */
    return behavior_point_in_polygon(x, y, z->points, z->num_points, b->w, b->h);
}

static int in_any(const Behavior *b, BehaviorZoneKind kind, double x, double y)
{
    for (size_t i = 0; i < b->num_zones; i++)
        if (b->zones[i].kind == kind && in_zone(b, &b->zones[i], x, y))
            return 1;
    return 0;
}

/* centre of the visible ones among keypoints a and c; 0 when none is visible */
static int centre_of(const BehaviorKeypoint *kp, int a, int c, double vis_min, double *x, double *y)
{
    double sx = 0.0, sy = 0.0;
    int n = 0;
    const int ids[2] = { a, c };
    for (int i = 0; i < 2; i++) {
        if (kp[ids[i]].v >= vis_min) {
            sx += kp[ids[i]].x;
            sy += kp[ids[i]].y;
            n++;
        }
    }
    if (n == 0)
        return 0;
    *x = sx / n;
    *y = sy / n;
    return 1;
}

typedef struct {
    double angle, length, sx, sy, hx, hy;
} Torso;

static int torso_of(const BehaviorKeypoint *kp, double vis_min, Torso *t)
{
    if (!centre_of(kp, L_SHOULDER, R_SHOULDER, vis_min, &t->sx, &t->sy) || !centre_of(kp, L_HIP, R_HIP, vis_min, &t->hx, &t->hy))
        return 0;
    t->length = hypot(t->sx - t->hx, t->sy - t->hy);
    if (t->length <= 0.0)
        return 0;
    t->angle = atan2(fabs(t->sx - t->hx), t->hy - t->sy) * (180.0 / M_PI);
    return 1;
}

static void foot_of(const BehaviorPerson *p, double vis_min, double *x, double *y)
{
    if (p->has_pose && centre_of(p->kp, L_ANKLE, R_ANKLE, vis_min, x, y))
        return;
    *x = p->box[0] + p->box[2] / 2.0;
    *y = p->box[1] + p->box[3];
}

static Track *find_track(Behavior *b, uint64_t id)
{
    for (size_t i = 0; i < b->num_tracks; i++)
        if (b->tracks[i].id == id)
            return &b->tracks[i];
    return NULL;
}

/* A track by ID, made when it is new. NULL when memory ran out. */
static Track *track_for(Behavior *b, uint64_t id)
{
    Track *t = find_track(b, id);
    if (t != NULL)
        return t;
    if (b->num_tracks == b->cap_tracks) {
        size_t cap = b->cap_tracks ? b->cap_tracks * 2 : 16;
        Track *bigger = realloc(b->tracks, cap * sizeof(*bigger));
        if (bigger == NULL)
            return NULL;
        b->tracks = bigger;
        b->cap_tracks = cap;
    }
    t = &b->tracks[b->num_tracks++];
    memset(t, 0, sizeof(*t));
    t->id = id;
    return t;
}

static void push_event(BehaviorEvent *out, size_t cap, int *count, BehaviorType type, uint64_t a, uint64_t c, int ntracks,
                       double score, int zone_id, int64_t occurred, int64_t now)
{
    if ((size_t)*count < cap) {
        BehaviorEvent *e = &out[*count];
        e->type = type;
        e->track_ids[0] = a;
        e->track_ids[1] = c;
        e->num_tracks = ntracks;
        e->score = score;
        e->zone_id = zone_id;
        e->occurred_ns = occurred;
        e->now_ns = now;
    }
    (*count)++;
}

/* ---- restricted areas ---- */
static void zone_rule(Behavior *b, Track *tr, const BehaviorPerson *p, double fx, double fy, int64_t now, BehaviorEvent *out,
                      size_t cap, int *count)
{
    const BehaviorConfig *cfg = &b->cfg;
    for (size_t i = 0; i < b->num_zones; i++) {
        const BehaviorZone *z = &b->zones[i];
        if (z->kind != BEHAVIOR_ZONE_RESTRICTED)
            continue;
        ZoneState *st = &tr->zones[i];
        if (in_zone(b, z, fx, fy)) {
            st->has_out_since = 0;
            if (!st->has_inside_since) {
                st->has_inside_since = 1;
                st->inside_since = now;
            }
            if (!st->fired && now - st->inside_since >= ns_of(cfg->zone_dwell_s)) {
                st->fired = 1;
                push_event(out, cap, count, BEHAVIOR_RESTRICTED_AREA, p->track_id, 0, 1, 1.0, z->zone_id, st->inside_since, now);
            }
        } else {
            if (!st->has_inside_since)
                continue;
            if (!st->has_out_since) {
                st->has_out_since = 1;
                st->out_since = now;
            }
            int64_t out_for = now - st->out_since;
            if (out_for >= ns_of(cfg->zone_exit_s) && !st->fired) {
                st->has_inside_since = 0;
                st->has_out_since = 0;
            } else if (st->fired && out_for >= ns_of(cfg->zone_rearm_s)) {
                st->has_inside_since = 0;
                st->has_out_since = 0;
                st->fired = 0;
            }
        }
    }
}

/* ---- falls ---- */

/* The person is down: follow the hips while they settle, and report the fall once they have been still long enough. */
static void fall_down_step(Behavior *b, FallState *st, const BehaviorPerson *p, int64_t now, double hx, double hy,
                           double max_angle, int measured, BehaviorEvent *out, size_t cap, int *count)
{
    const BehaviorConfig *cfg = &b->cfg;
    int64_t since = now - st->t_down;
    if (since < ns_of(cfg->fall_settle_s)) {
        if (measured) {
            st->hx_down = hx;
            st->hy_down = hy;
            double moved = hypot(hx - st->hx_ref, hy - st->hy_ref) / st->torso_ref;
            if (moved > st->max_moved)
                st->max_moved = moved;
            if (!st->has_t_moved && moved >= cfg->fall_hip_drop_min) {
                st->has_t_moved = 1;
                st->t_moved = now;
            }
        }
        return;
    }
    if (since < ns_of(cfg->fall_still_s))
        return;
    int still = hypot(hx - st->hx_down, hy - st->hy_down) / st->torso_ref <= cfg->fall_move_max;
    int came_down = st->has_t_moved && st->t_moved - st->t_up <= ns_of(cfg->fall_hip_window_s);
    if (!came_down || !still) {
        st->mode = FALL_LYING;
    } else if (in_any(b, BEHAVIOR_ZONE_LIE_OK, hx, hy)) {
        st->mode = FALL_FIRED;
        st->cooldown_until = now;
    } else {
        double k = (max_angle - cfg->fall_down_deg) / (90.0 - cfg->fall_down_deg);
        k = k < 0.0 ? 0.0 : (k > 1.0 ? 1.0 : k);
        push_event(out, cap, count, BEHAVIOR_FALL, p->track_id, 0, 1, 0.5 + 0.5 * k, -1, st->t_down, now);
        st->mode = FALL_FIRED;
        st->cooldown_until = now + ns_of(cfg->fall_cooldown_s);
    }
}

/* The skeleton network says fall_net_k scores in a row at or above fall_net_thr: a fall, reported now, with the end of the first window as
 * the moment it began. Shares the cooldown with the rules; hips in a lie_ok zone are never reported. */
static void net_rule(Behavior *b, Track *tr, const BehaviorPerson *p, const BehaviorScore *scores, size_t ns, int64_t now,
                     BehaviorEvent *out, size_t cap, int *count)
{
    const BehaviorConfig *cfg = &b->cfg;
    FallState *st = &tr->fall;
    NetState *net = &tr->net;
    for (size_t i = 0; i < ns; i++) {
        if (scores[i].track_id != p->track_id)
            continue;
        if (scores[i].prob < cfg->fall_net_thr) {
            net->run = 0;
            continue;
        }
        net->run++;
        if (net->run == 1)
            net->first_ns = scores[i].end_ns;
        if (net->run < (int)cfg->fall_net_k || now < st->cooldown_until)
            continue;
        Torso m;
        double hx, hy;
        if (p->has_pose && torso_of(p->kp, cfg->vis_min, &m)) {
            hx = m.hx;
            hy = m.hy;
        } else {                                  /* no usable skeleton: the middle of the box */
            hx = p->box[0] + p->box[2] / 2.0;
            hy = p->box[1] + p->box[3] / 2.0;
        }
        net->run = 0;
        if (in_any(b, BEHAVIOR_ZONE_LIE_OK, hx, hy))
            continue;
        double prob = scores[i].prob < 0.0 ? 0.0 : (scores[i].prob > 1.0 ? 1.0 : scores[i].prob);
        push_event(out, cap, count, BEHAVIOR_FALL, p->track_id, 0, 1, prob, -1, net->first_ns, now);
        st->mode = FALL_FIRED;
        st->cooldown_until = now + ns_of(cfg->fall_cooldown_s);
    }
}

static void fall_rule(Behavior *b, Track *tr, const BehaviorPerson *p, int64_t now, BehaviorEvent *out, size_t cap, int *count)
{
    const BehaviorConfig *cfg = &b->cfg;
    FallState *st = &tr->fall;
    Torso m;
    int have = p->has_pose && torso_of(p->kp, cfg->vis_min, &m);
    if (have && m.length < cfg->fall_min_torso_px)
        return;
    if (!have) {
        /* No usable skeleton: a person who is down stays down while the box is wide; the last hips stand in. */
        if (st->mode == FALL_DOWN && p->box[2] >= cfg->fall_lying_aspect * p->box[3]) {
            st->last_ns = now;
            fall_down_step(b, st, p, now, st->last_hx, st->last_hy, st->max_angle, 0, out, cap, count);
        }
        return;
    }
    st->last_ns = now;
    st->last_hx = m.hx;
    st->last_hy = m.hy;
    const int up = m.angle < cfg->fall_up_deg;
    const int64_t stand_back = ns_of(cfg->fall_stand_back_s);

#define SET_REFS()                  \
    do {                            \
        st->t_up = now;             \
        st->sy_ref = m.sy;          \
        st->hx_ref = m.hx;          \
        st->hy_ref = m.hy;          \
        st->torso_ref = m.length;   \
    } while (0)

    switch (st->mode) {
    case FALL_UNKNOWN:
        if (up) {
            st->mode = FALL_UPRIGHT;
            SET_REFS();
        } else if (m.angle >= cfg->fall_down_deg) {
            st->mode = FALL_LYING;
        }
        break;
    case FALL_UPRIGHT:
        if (up) {
            SET_REFS();
        } else if (m.angle >= cfg->fall_down_deg) {
            if (now - st->t_up <= ns_of(cfg->fall_drop_window_s)) {
                st->mode = FALL_DOWN;
                st->t_down = now;
                st->hx_down = m.hx;
                st->hy_down = m.hy;
                st->max_angle = m.angle;
                st->max_moved = hypot(m.hx - st->hx_ref, m.hy - st->hy_ref) / st->torso_ref;
                st->has_t_moved = st->max_moved >= cfg->fall_hip_drop_min;
                st->t_moved = now;
                st->has_up_since = 0;
            } else {
                st->mode = FALL_LYING;
                st->has_up_since = 0;
            }
        }
        break;
    case FALL_LYING:
    case FALL_FIRED:
        if (up) {
            if (!st->has_up_since) {
                st->has_up_since = 1;
                st->up_since = now;
            }
            if (now - st->up_since >= stand_back && now >= st->cooldown_until) {
                st->mode = FALL_UPRIGHT;
                st->has_up_since = 0;
                SET_REFS();
            }
        } else {
            st->has_up_since = 0;
        }
        break;
    case FALL_DOWN:
        if (m.angle > st->max_angle)
            st->max_angle = m.angle;
        if (up) {
            if (!st->has_up_since) {
                st->has_up_since = 1;
                st->up_since = now;
            }
            if (now - st->up_since >= stand_back) {
                st->mode = FALL_UPRIGHT;
                st->has_up_since = 0;
                SET_REFS();
            }
            return;
        }
        st->has_up_since = 0;
        fall_down_step(b, st, p, now, m.hx, m.hy, st->max_angle, 1, out, cap, count);
        break;
    }
#undef SET_REFS
}

static void fall_lost(Behavior *b, int64_t now)
{
    const int64_t lost = ns_of(b->cfg.fall_lost_s);
    for (size_t i = 0; i < b->num_tracks; i++) {
        FallState *st = &b->tracks[i].fall;
        if (st->mode == FALL_DOWN && now - st->last_ns > lost)
            st->mode = FALL_UNKNOWN;
    }
}

/* ---- fights ---- */
static void speeds_of(const Behavior *b, Track *tr, const BehaviorPerson *p, int64_t now)
{
    tr->has_speeds = 0;
    if (!p->has_pose) {
        tr->has_prev = 0;
        return;
    }
    Torso m;
    const int have_torso = torso_of(p->kp, b->cfg.vis_min, &m);
    if (tr->has_prev && have_torso) {
        double dt = (double)(now - tr->prev_ns) / NS;
        if (dt > 0.0 && dt <= b->cfg.fight_max_dt_s) {
            tr->has_speeds = 1;
            for (int i = 0; i < NHITTERS; i++) {
                const BehaviorKeypoint *a = &p->kp[HITTERS[i]], *pv = &tr->prev_kp[HITTERS[i]];
                if (a->v >= b->cfg.vis_min && pv->v >= b->cfg.vis_min) {
                    tr->speed_valid[i] = 1;
                    tr->speed[i] = hypot(a->x - pv->x, a->y - pv->y) / (m.length * dt);
                } else {
                    tr->speed_valid[i] = 0;
                }
            }
        }
    }
    memcpy(tr->prev_kp, p->kp, sizeof(tr->prev_kp));
    tr->has_prev = 1;
    tr->prev_ns = now;
}

static int contact(const Behavior *b, const BehaviorPerson *a, const Track *ta, const BehaviorPerson *other)
{
    if (!ta->has_speeds || !a->has_pose)
        return 0;
    for (int i = 0; i < NHITTERS; i++) {
        if (!ta->speed_valid[i] || ta->speed[i] < b->cfg.fight_speed)
            continue;
        const BehaviorKeypoint *k = &a->kp[HITTERS[i]];
        if (other->box[0] < k->x && k->x < other->box[0] + other->box[2] && other->box[1] < k->y &&
            k->y < other->box[1] + other->box[3])
            return 1;
    }
    return 0;
}

static Pair *pair_for(Behavior *b, uint64_t x, uint64_t y, int64_t now, int *oom)
{
    uint64_t lo = x < y ? x : y, hi = x < y ? y : x;
    for (size_t i = 0; i < b->num_pairs; i++)
        if (b->pairs[i].a == lo && b->pairs[i].b == hi)
            return &b->pairs[i];
    if (b->num_pairs == b->cap_pairs) {
        size_t cap = b->cap_pairs ? b->cap_pairs * 2 : 16;
        Pair *bigger = realloc(b->pairs, cap * sizeof(*bigger));
        if (bigger == NULL) {
            *oom = 1;
            return NULL;
        }
        b->pairs = bigger;
        b->cap_pairs = cap;
    }
    Pair *p = &b->pairs[b->num_pairs++];
    memset(p, 0, sizeof(*p));
    p->a = lo;
    p->b = hi;
    p->last_ns = now;
    p->last_seen = now;
    return p;
}

static void fight_rule(Behavior *b, const BehaviorPerson *const *ppl, const Track *const *trk, size_t n, int64_t now,
                       BehaviorEvent *out, size_t cap, int *count, int *oom)
{
    const BehaviorConfig *cfg = &b->cfg;
    for (size_t i = 0; i < n; i++) {
        for (size_t j = i + 1; j < n; j++) {
            Pair *pr = pair_for(b, ppl[i]->track_id, ppl[j]->track_id, now, oom);
            if (pr == NULL)
                return;
            double dt = (double)(now - pr->last_ns) / NS;
            int c = contact(b, ppl[i], trk[i], ppl[j]) || contact(b, ppl[j], trk[j], ppl[i]);
            if (dt > 0.0) {
                pr->bucket = pr->bucket * exp(-dt / cfg->fight_decay_s);
                if (c) {
                    double step = dt < cfg->fight_max_dt_s ? dt : cfg->fight_max_dt_s;
                    if (pr->bucket < cfg->fight_rearm)
                        pr->since_ns = now - (int64_t)(step * NS);
                    pr->bucket += step;
                }
            }
            pr->last_ns = now;
            pr->last_seen = now;
            if (!pr->fired && pr->bucket >= cfg->fight_threshold_s) {
                double score = 0.5 + 0.5 * (pr->bucket / (2.0 * cfg->fight_threshold_s) < 1.0 ? pr->bucket / (2.0 * cfg->fight_threshold_s) : 1.0);
                if (score > 1.0)
                    score = 1.0;
                push_event(out, cap, count, BEHAVIOR_FIGHT, ppl[i]->track_id, ppl[j]->track_id, 2, score, -1, pr->since_ns, now);
                pr->fired = 1;
                pr->cooldown_until = now + ns_of(cfg->fight_cooldown_s);
            } else if (pr->fired && pr->bucket < cfg->fight_rearm && now >= pr->cooldown_until) {
                pr->fired = 0;
            }
        }
    }
}

static void forget(Behavior *b, int64_t now)
{
    const int64_t limit = ns_of(b->cfg.forget_s);
    size_t w = 0;
    for (size_t i = 0; i < b->num_tracks; i++)
        if (!(now - b->tracks[i].last_seen > limit))
            b->tracks[w++] = b->tracks[i];
    b->num_tracks = w;
    w = 0;
    for (size_t i = 0; i < b->num_pairs; i++)
        if (!(now - b->pairs[i].last_seen > limit))
            b->pairs[w++] = b->pairs[i];
    b->num_pairs = w;
}

int behavior_process(Behavior *b, const BehaviorPerson *people, size_t n, int64_t now_ns, BehaviorEvent *out, size_t out_cap)
{
    return behavior_process_scored(b, people, n, NULL, 0, now_ns, out, out_cap);
}

int behavior_process_scored(Behavior *b, const BehaviorPerson *people, size_t n, const BehaviorScore *scores, size_t num_scores,
                            int64_t now_ns, BehaviorEvent *out, size_t out_cap)
{
    const BehaviorConfig *cfg = &b->cfg;
    int count = 0, oom = 0;

    /* the people an "ignore" zone does not hide */
    const BehaviorPerson **visible = n ? malloc(n * sizeof(*visible)) : NULL;
    if (n && visible == NULL)
        return -1;
    size_t nv = 0;
    for (size_t i = 0; i < n; i++) {
        double fx, fy;
        foot_of(&people[i], cfg->vis_min, &fx, &fy);
        if (!in_any(b, BEHAVIOR_ZONE_IGNORE, fx, fy))
            visible[nv++] = &people[i];
    }

    for (size_t i = 0; i < nv; i++) {
        const BehaviorPerson *p = visible[i];
        Track *tr = track_for(b, p->track_id);
        if (tr == NULL) {
            oom = 1;
            continue;
        }
        tr->last_seen = now_ns;
        double fx, fy;
        foot_of(p, cfg->vis_min, &fx, &fy);
        if (cfg->zones)
            zone_rule(b, tr, p, fx, fy, now_ns, out, out_cap, &count);
        if (cfg->fall) {
            if (cfg->fall_net != 1.0)
                fall_rule(b, tr, p, now_ns, out, out_cap, &count);
            if (cfg->fall_net != 0.0)
                net_rule(b, tr, p, scores, num_scores, now_ns, out, out_cap, &count);
        }
        speeds_of(b, tr, p, now_ns);
    }

    if (cfg->fight && nv > 0) {
        /* the first BEHAVIOR_MAX_PEOPLE_FOR_PAIRS people with a skeleton; tracks are looked up again here because
         * track_for() may have moved the array */
        const BehaviorPerson *pp[BEHAVIOR_MAX_PEOPLE_FOR_PAIRS];
        const Track *tt[BEHAVIOR_MAX_PEOPLE_FOR_PAIRS];
        size_t np = 0;
        for (size_t i = 0; i < nv && np < BEHAVIOR_MAX_PEOPLE_FOR_PAIRS; i++) {
            if (!visible[i]->has_pose)
                continue;
            const Track *tr = find_track(b, visible[i]->track_id);
            if (tr == NULL)
                continue;
            pp[np] = visible[i];
            tt[np] = tr;
            np++;
        }
        /* pair_for() may move the pairs array, never the tracks, so the pointers stay valid */
        fight_rule(b, pp, tt, np, now_ns, out, out_cap, &count, &oom);
    }

    free(visible);
    forget(b, now_ns);
    fall_lost(b, now_ns);
    if (oom)
        return -1;
    return count < 0 ? 0 : (size_t)count > out_cap ? (int)out_cap : count;
}
