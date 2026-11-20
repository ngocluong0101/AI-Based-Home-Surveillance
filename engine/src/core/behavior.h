/*
 * behavior.h - the behaviour rules of this project (ROADMAP_V2 V2-3): falls, fights and people in restricted areas, from the
 * skeletons and boxes of the people a tracker follows. C port of src/events/behavior.py, which is the specification and
 * explains every rule; both are run against the same vectors (tests/vectors/behavior_*.json).
 *
 * Pure C, no DeepStream or GLib, no clock of its own: the caller passes `now_ns` (in the engine stream_start + the PTS of
 * the frame). One Behavior per camera, used from one thread (the pad probe). Numbers are doubles on purpose: the vectors are
 * compared with the Python version, which computes in doubles.
 */
#ifndef BEHAVIOR_H
#define BEHAVIOR_H

#include <stddef.h>
#include <stdint.h>

#define BEHAVIOR_KEYPOINTS 17
#define BEHAVIOR_MAX_ZONES 8
#define BEHAVIOR_MAX_ZONE_POINTS 32
#define BEHAVIOR_MAX_PEOPLE_FOR_PAIRS 16

typedef enum { BEHAVIOR_FALL, BEHAVIOR_FIGHT, BEHAVIOR_RESTRICTED_AREA } BehaviorType;

/* Same names and defaults as BehaviorConfig in the Python specification. */
typedef struct {
    int fall, fight, zones;      /* which rules run (restricted-area events need zones; lie_ok / ignore act whenever they exist) */
    double vis_min, forget_s;
    double fall_down_deg, fall_up_deg, fall_drop_window_s, fall_hip_drop_min, fall_still_s, fall_settle_s, fall_hip_window_s,
        fall_move_max, fall_lying_aspect, fall_stand_back_s, fall_lost_s, fall_cooldown_s, fall_min_torso_px;
    /* the skeleton network (V2-5), see fall_net in the Python specification: 0 rules only, 1 network only, 2 either; the numbers are doubles
     * so that the per-camera settings table can hold them like the others */
    double fall_net, fall_net_thr, fall_net_k;
    double fight_speed, fight_decay_s, fight_threshold_s, fight_rearm, fight_cooldown_s, fight_max_dt_s;
    double zone_dwell_s, zone_exit_s, zone_rearm_s;
} BehaviorConfig;

void behavior_config_default(BehaviorConfig *cfg);

typedef enum { BEHAVIOR_ZONE_RESTRICTED, BEHAVIOR_ZONE_LIE_OK, BEHAVIOR_ZONE_IGNORE } BehaviorZoneKind;

/* A polygon in coordinates relative to the frame (0..1). */
typedef struct {
    BehaviorZoneKind kind;
    int zone_id;                                 /* reported in events of a restricted zone */
    size_t num_points;                           /* 3..BEHAVIOR_MAX_ZONE_POINTS; fewer than 3 = nothing is inside */
    double points[BEHAVIOR_MAX_ZONE_POINTS][2];
} BehaviorZone;

typedef struct {
    double x, y, v;                              /* pixels; visibility 0..1 */
} BehaviorKeypoint;

typedef struct {
    uint64_t track_id;
    double box[4];                               /* x, y, w, h in pixels */
    int has_pose;                                /* 0: the model gave no skeleton (kp is ignored) */
    BehaviorKeypoint kp[BEHAVIOR_KEYPOINTS];     /* COCO order of YOLOv8-pose */
} BehaviorPerson;

typedef struct {
    BehaviorType type;
    uint64_t track_ids[2];                       /* FIGHT: the pair; otherwise only [0] */
    int num_tracks;
    double score;                                /* 0..1 */
    int zone_id;                                 /* -1 when the event has no zone */
    int64_t occurred_ns;                         /* when the behaviour began (a fall: the impact) */
    int64_t now_ns;                              /* when it was emitted */
} BehaviorEvent;

/* A fall score the skeleton network produced for a track: the window ended at end_ns (the same clock as now_ns). */
typedef struct {
    uint64_t track_id;
    double prob;
    int64_t end_ns;
} BehaviorScore;

typedef struct Behavior Behavior;

/* NULL when memory runs out. frame_w / frame_h (pixels) map the relative zones onto the picture. */
Behavior *behavior_new(const BehaviorConfig *cfg, double frame_w, double frame_h);
void behavior_free(Behavior *b);   /* NULL is fine */

/* Replace the thresholds of a Behavior in use (a camera edited while the engine runs). What it remembers about the
 * people is kept. */
void behavior_set_config(Behavior *b, const BehaviorConfig *cfg);

/* Replace the zones (copied). What the engine remembered about people and zones is cleared, because a zone state belongs
 * to the zone it was counted for. Returns 0, or -1 for more than BEHAVIOR_MAX_ZONES (nothing changes). */
int behavior_set_zones(Behavior *b, const BehaviorZone *zones, size_t n);

/*
 * Process one frame (the people in the order they should be reported) at now_ns and write the events it raises to out
 * (out_cap places; the rest are dropped but the state is still updated). Returns the number written, or -1 when memory ran
 * out (the state of that frame may be partly updated; the next call goes on).
 * Order of the events: for each person its restricted-area events, then its fall; then the fights, by pairs (i < j).
 */
int behavior_process(Behavior *b, const BehaviorPerson *people, size_t n, int64_t now_ns, BehaviorEvent *out, size_t out_cap);

/* The same with the scores that arrived since the last call (oldest first), which the fall_net setting turns into fall events. */
int behavior_process_scored(Behavior *b, const BehaviorPerson *people, size_t n, const BehaviorScore *scores, size_t num_scores,
                            int64_t now_ns, BehaviorEvent *out, size_t out_cap);

size_t behavior_track_count(const Behavior *b);

/* Ray casting, exposed for the tests. points are relative to the frame; the polygon is scaled by w and h. */
int behavior_point_in_polygon(double px, double py, const double (*points)[2], size_t n, double w, double h);

#endif /* BEHAVIOR_H */
