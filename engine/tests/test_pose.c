/* Tests of the skeleton helpers (src/core/pose.c): values in, normalization, history window with hole repair. */
#include <math.h>
#include <string.h>

#include "pose.h"
#include "test_util.h"

/* A standing person: shoulders at y=100, hips at y=200 (torso 100 px), x centred on 110, nose above. */
static Pose standing(float dx, float dy)
{
    Pose p;
    memset(&p, 0, sizeof(p));
    for (int k = 0; k < POSE_KEYPOINTS; k++) {
        p.kp[k].x = 110 + dx;
        p.kp[k].y = 150 + dy;
        p.kp[k].v = 0.9f;
    }
    p.kp[KP_NOSE].y = 80 + dy;
    p.kp[KP_LEFT_SHOULDER].x = 100 + dx;  p.kp[KP_LEFT_SHOULDER].y = 100 + dy;
    p.kp[KP_RIGHT_SHOULDER].x = 120 + dx; p.kp[KP_RIGHT_SHOULDER].y = 100 + dy;
    p.kp[KP_LEFT_HIP].x = 100 + dx;       p.kp[KP_LEFT_HIP].y = 200 + dy;
    p.kp[KP_RIGHT_HIP].x = 120 + dx;      p.kp[KP_RIGHT_HIP].y = 200 + dy;
    return p;
}

void test_pose_from_values_rejects_non_finite_numbers(void)
{
    float v[POSE_VALUES];
    for (int i = 0; i < POSE_VALUES; i++)
        v[i] = (float)i;
    Pose p;
    memset(&p, 0, sizeof(p));
    CHECK(pose_from_values(v, &p) == 0);
    CHECK_NEAR(p.kp[2].x, 6, 1e-6);       /* values 6, 7, 8 = keypoint 2 */
    CHECK_NEAR(p.kp[2].y, 7, 1e-6);
    CHECK_NEAR(p.kp[2].v, 8, 1e-6);
    v[10] = NAN;
    Pose q = standing(0, 0);
    CHECK(pose_from_values(v, &q) == -1);
    CHECK_NEAR(q.kp[KP_NOSE].y, 80, 1e-6);   /* untouched */
}

void test_pose_normalize_centres_on_the_hips_and_scales_by_the_torso(void)
{
    Pose p = standing(500, 300);              /* far from the origin: must not matter */
    NormPose n;
    CHECK(pose_normalize(&p, POSE_VISIBILITY_MIN, 999, &n) == 0);
    CHECK_NEAR(n.x[KP_LEFT_HIP], -0.1, 1e-5);       /* hip centre is the origin, hips 10 px away, torso 100 px */
    CHECK_NEAR(n.y[KP_LEFT_HIP], 0, 1e-5);
    CHECK_NEAR(n.y[KP_LEFT_SHOULDER], -1.0, 1e-5);  /* a torso length above the hips (y points down) */
    CHECK_NEAR(n.y[KP_NOSE], -1.2, 1e-5);
    CHECK(n.valid[KP_NOSE] && n.valid[KP_RIGHT_ANKLE]);

    /* the same person twice as far away (half the size) gives the same numbers */
    Pose half = standing(0, 0);
    for (int k = 0; k < POSE_KEYPOINTS; k++) {
        half.kp[k].x *= 0.5f;
        half.kp[k].y *= 0.5f;
    }
    NormPose m;
    CHECK(pose_normalize(&half, POSE_VISIBILITY_MIN, 999, &m) == 0);
    CHECK_NEAR(m.y[KP_NOSE], n.y[KP_NOSE], 1e-5);
    CHECK_NEAR(m.x[KP_LEFT_SHOULDER], n.x[KP_LEFT_SHOULDER], 1e-5);
}

void test_pose_normalize_handles_missing_points(void)
{
    Pose p = standing(0, 0);
    NormPose n;

    p.kp[KP_NOSE].v = 0.2f;                    /* not seen */
    CHECK(pose_normalize(&p, POSE_VISIBILITY_MIN, 999, &n) == 0);
    CHECK(!n.valid[KP_NOSE] && n.x[KP_NOSE] == 0 && n.y[KP_NOSE] == 0);

    /* shoulders gone: the scale comes from the size hint (0.3 * 300 = 90 px) and the hips are the origin */
    p = standing(0, 0);
    p.kp[KP_LEFT_SHOULDER].v = 0;
    p.kp[KP_RIGHT_SHOULDER].v = 0;
    CHECK(pose_normalize(&p, POSE_VISIBILITY_MIN, 300, &n) == 0);
    CHECK_NEAR(n.y[KP_NOSE], (80 - 200) / 90.0, 1e-5);

    /* one hip only: that hip is the origin */
    p = standing(0, 0);
    p.kp[KP_RIGHT_HIP].v = 0;
    CHECK(pose_normalize(&p, POSE_VISIBILITY_MIN, 300, &n) == 0);
    CHECK_NEAR(n.x[KP_LEFT_HIP], 0, 1e-5);

    /* neither hips nor shoulders, or no usable scale */
    p = standing(0, 0);
    p.kp[KP_LEFT_HIP].v = p.kp[KP_RIGHT_HIP].v = p.kp[KP_LEFT_SHOULDER].v = p.kp[KP_RIGHT_SHOULDER].v = 0;
    CHECK(pose_normalize(&p, POSE_VISIBILITY_MIN, 300, &n) == -1);
    p = standing(0, 0);
    p.kp[KP_LEFT_SHOULDER].v = 0;
    p.kp[KP_RIGHT_SHOULDER].v = 0;
    CHECK(pose_normalize(&p, POSE_VISIBILITY_MIN, 0, &n) == -1);     /* no torso, no size hint */
}

void test_pose_history_window_repairs_short_holes_only(void)
{
    PoseHistory *h = pose_history_new();
    CHECK(h != NULL);
    if (h == NULL)
        return;
    CHECK(pose_history_last_frame(h) == -1);
    NormPose w[10];
    unsigned char present[10];
    CHECK(pose_history_window(h, 5, POSE_VISIBILITY_MIN, 3, w, present) == -1);   /* empty */

    /* frames 0..9; the track is missing in 4 and 5 (a hole of 2); the nose walks 80 -> 90 px down in frames 0..9 */
    for (int f = 0; f < 10; f++) {
        if (f == 4 || f == 5)
            continue;
        Pose p = standing(0, 0);
        p.kp[KP_NOSE].y = 80.0f + (float)f * (10.0f / 9.0f);
        CHECK(pose_history_push(h, f, &p, 300) == 0);
    }
    CHECK(pose_history_last_frame(h) == 9);

    CHECK(pose_history_window(h, 10, POSE_VISIBILITY_MIN, 3, w, present) == 8);   /* 8 frames had a skeleton */
    CHECK(present[3] == 1 && present[4] == 0 && present[5] == 0 && present[6] == 1);
    CHECK(w[4].valid[KP_NOSE] && w[5].valid[KP_NOSE]);                             /* repaired */
    CHECK_NEAR(w[4].y[KP_NOSE], w[3].y[KP_NOSE] + (w[6].y[KP_NOSE] - w[3].y[KP_NOSE]) / 3.0, 1e-5);

    /* a hole longer than max_gap stays a hole */
    CHECK(pose_history_window(h, 10, POSE_VISIBILITY_MIN, 1, w, present) == 8);
    CHECK(!w[4].valid[KP_NOSE] && !w[5].valid[KP_NOSE]);

    /* a window that starts before the first frame: those frames are empty, and are not "repaired" */
    NormPose big[20];
    CHECK(pose_history_window(h, 20, POSE_VISIBILITY_MIN, 3, big, NULL) == 8);
    CHECK(!big[0].valid[KP_NOSE] && !big[9].valid[KP_NOSE] && big[10].valid[KP_NOSE]);

    CHECK(pose_history_window(h, 0, POSE_VISIBILITY_MIN, 3, w, NULL) == -1);
    CHECK(pose_history_window(h, POSE_HISTORY_FRAMES + 1, POSE_VISIBILITY_MIN, 3, w, NULL) == -1);
    pose_history_free(h);
}

void test_pose_history_ring_wraps_and_ignores_old_frames(void)
{
    PoseHistory *h = pose_history_new();
    if (h == NULL) {
        CHECK(h != NULL);
        return;
    }
    Pose p = standing(0, 0);
    for (int f = 0; f < 300; f++)             /* more than two turns of the ring */
        CHECK(pose_history_push(h, f, &p, 300) == 0);
    CHECK(pose_history_push(h, 299, &p, 300) == -1);   /* repeated */
    CHECK(pose_history_push(h, 100, &p, 300) == -1);   /* older */
    CHECK(pose_history_push(h, -3, &p, 300) == -1);
    CHECK(pose_history_last_frame(h) == 299);

    NormPose w[POSE_HISTORY_FRAMES];
    unsigned char present[POSE_HISTORY_FRAMES];
    CHECK(pose_history_window(h, POSE_HISTORY_FRAMES, POSE_VISIBILITY_MIN, 0, w, present) == POSE_HISTORY_FRAMES);

    /* a jump of a whole ring forgets everything older */
    CHECK(pose_history_push(h, 299 + POSE_HISTORY_FRAMES, &p, 300) == 0);
    NormPose v[10];
    unsigned char pv[10];
    CHECK(pose_history_window(h, 10, POSE_VISIBILITY_MIN, 0, v, pv) == 1);
    CHECK(pv[9] == 1 && pv[8] == 0);
    pose_history_free(h);
}
