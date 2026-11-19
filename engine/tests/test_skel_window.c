/*
 * pose_history_skeleton_window() (src/core/pose.c) against the vectors of tests/vectors/skeleton_windows.json, which come from the Python
 * specification eval/train/skeleton.py (the code the network was trained with). If these differ the model is fed something else at run time.
 */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "json_mini.h"
#include "pose.h"
#include "test_util.h"

#ifndef VECTOR_DIR
#define VECTOR_DIR "../tests/vectors"
#endif

static char *slurp(const char *path)
{
    FILE *f = fopen(path, "rb");
    if (f == NULL)
        return NULL;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *buf = malloc((size_t)n + 1);
    if (buf == NULL || fread(buf, 1, (size_t)n, f) != (size_t)n) {
        fclose(f);
        free(buf);
        return NULL;
    }
    buf[n] = '\0';
    fclose(f);
    return buf;
}

void test_skeleton_window_matches_the_python_specification(void)
{
    char *text = slurp(VECTOR_DIR "/skeleton_windows.json");
    CHECK(text != NULL);
    if (text == NULL)
        return;
    JsonValue *root = json_parse(text);
    free(text);
    CHECK(root != NULL && root->count >= 10);
    if (root == NULL)
        return;
    int with_window = 0, without = 0;
    for (size_t c = 0; c < root->count; c++) {
        const JsonValue *cs = &root->items[c];
        const char *name = json_get(cs, "name")->string;
        const unsigned T = (unsigned)json_get(cs, "T")->integer;
        const int64_t end = json_get(cs, "end")->integer;
        const JsonValue *frames = json_get(cs, "frames"), *expected = json_get(cs, "expected");
        PoseHistory *h = pose_history_new();
        for (size_t i = 0; i < frames->count; i++) {
            const JsonValue *fr = &frames->items[i];
            const JsonValue *kp = json_get(fr, "kp");
            float values[POSE_VALUES];
            for (int k = 0; k < POSE_VALUES; k++)
                values[k] = (float)kp->items[k].number;
            Pose p;
            CHECK(pose_from_values(values, &p) == 0);
            /* the history ends at the newest pushed frame; a vector asking for an earlier end is cut there */
            if (json_get(fr, "frame")->integer <= end)
                CHECK(pose_history_push(h, json_get(fr, "frame")->integer, &p, (float)json_get(fr, "size")->number) == 0);
        }
        float *got = malloc(sizeof(float) * 3 * T * POSE_KEYPOINTS);
        int rc = pose_history_skeleton_window(h, end, T, POSE_VISIBILITY_MIN, got);
        if (expected->type == JSON_NULL) {
            if (rc != -1)
                fprintf(stderr, "vector %s: expected no window\n", name);
            CHECK(rc == -1);
            without++;
        } else {
            if (rc != 0)
                fprintf(stderr, "vector %s: the C version found no window\n", name);
            CHECK(rc == 0);
            if (rc == 0) {
                CHECK(expected->count == 3 * T * POSE_KEYPOINTS);
                double worst = 0;
                for (size_t i = 0; i < expected->count; i++)
                    worst = fmax(worst, fabs((double)got[i] - expected->items[i].number));
                if (worst > 2e-4)
                    fprintf(stderr, "vector %s: largest difference %g\n", name, worst);
                CHECK(worst <= 2e-4);
            }
            with_window++;
        }
        free(got);
        pose_history_free(h);
    }
    CHECK(with_window >= 8 && without >= 2);
    json_free(root);
}

void test_skeleton_window_rejects_bad_arguments_and_an_empty_history(void)
{
    PoseHistory *h = pose_history_new();
    float out[3 * 8 * POSE_KEYPOINTS];
    CHECK(pose_history_skeleton_window(h, 20, 8, POSE_VISIBILITY_MIN, out) == -1);      /* empty */
    Pose p;
    memset(&p, 0, sizeof(p));
    for (int k = 0; k < POSE_KEYPOINTS; k++) { p.kp[k].x = (float)(10 * k); p.kp[k].y = (float)(20 * k); p.kp[k].v = 0.9f; }
    for (int64_t f = 0; f <= 20; f++)                    /* enough frames (SKEL_MIN_FRAMES) for a window */
        CHECK(pose_history_push(h, f, &p, 200) == 0);
    CHECK(pose_history_skeleton_window(h, 20, 1, POSE_VISIBILITY_MIN, out) == -1);       /* too short */
    CHECK(pose_history_skeleton_window(h, 20, POSE_HISTORY_FRAMES + 1, POSE_VISIBILITY_MIN, out) == -1);
    CHECK(pose_history_skeleton_window(h, 21, 8, POSE_VISIBILITY_MIN, out) == -1);      /* no skeleton in the frame asked for */
    CHECK(pose_history_skeleton_window(h, 20, 8, POSE_VISIBILITY_MIN, out) == 0);
    CHECK_NEAR(out[(2 * 8 + 7) * POSE_KEYPOINTS + 3], 1.0, 0);                      /* the only frame, the last one, is valid */
    CHECK_NEAR(out[(2 * 8 + 0) * POSE_KEYPOINTS + 3], 1.0, 0);
    pose_history_free(h);
}
