/* See pose.h. */
#include "pose.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

int pose_from_values(const float values[POSE_VALUES], Pose *out)
{
    for (int i = 0; i < POSE_VALUES; i++) {
        if (!isfinite(values[i]))
            return -1;
    }
    for (int k = 0; k < POSE_KEYPOINTS; k++) {
        out->kp[k].x = values[3 * k];
        out->kp[k].y = values[3 * k + 1];
        out->kp[k].v = values[3 * k + 2];
    }
    return 0;
}

/* Centre of the visible ones among keypoints a and b. Returns 0 when neither is visible. */
static int centre_of(const Pose *p, int a, int b, float vis_min, float *x, float *y)
{
    int n = 0;
    float sx = 0.0f, sy = 0.0f;
    const int ids[2] = { a, b };
    for (int i = 0; i < 2; i++) {
        const Keypoint *k = &p->kp[ids[i]];
        if (k->v >= vis_min) {
            sx += k->x;
            sy += k->y;
            n++;
        }
    }
    if (n == 0)
        return 0;
    *x = sx / (float)n;
    *y = sy / (float)n;
    return 1;
}

int pose_normalize(const Pose *in, float vis_min, float size_hint, NormPose *out)
{
    memset(out, 0, sizeof(*out));

    float hx = 0, hy = 0, sx = 0, sy = 0;
    const int has_hips = centre_of(in, KP_LEFT_HIP, KP_RIGHT_HIP, vis_min, &hx, &hy);
    const int has_shoulders = centre_of(in, KP_LEFT_SHOULDER, KP_RIGHT_SHOULDER, vis_min, &sx, &sy);
    if (!has_hips && !has_shoulders)
        return -1;

    const float ox = has_hips ? hx : sx;
    const float oy = has_hips ? hy : sy;
    float scale = 0.0f;
    if (has_hips && has_shoulders)
        scale = hypotf(sx - hx, sy - hy);
    if (!(scale > 1e-3f))
        scale = 0.3f * size_hint;
    if (!(scale > 1e-3f) || !isfinite(scale))
        return -1;

    for (int k = 0; k < POSE_KEYPOINTS; k++) {
        if (in->kp[k].v >= vis_min) {
            out->x[k] = (in->kp[k].x - ox) / scale;
            out->y[k] = (in->kp[k].y - oy) / scale;
            out->valid[k] = 1;
        }
    }
    return 0;
}

struct PoseHistory {
    int64_t newest;   /* -1 when empty */
    int64_t frame[POSE_HISTORY_FRAMES];   /* the frame number stored in each slot, -1 = empty */
    Pose pose[POSE_HISTORY_FRAMES];
    float size_hint[POSE_HISTORY_FRAMES];
};

PoseHistory *pose_history_new(void)
{
    PoseHistory *h = malloc(sizeof(*h));
    if (h == NULL)
        return NULL;
    h->newest = -1;
    for (int i = 0; i < POSE_HISTORY_FRAMES; i++)
        h->frame[i] = -1;
    return h;
}

void pose_history_free(PoseHistory *h)
{
    free(h);
}

int pose_history_push(PoseHistory *h, int64_t frame, const Pose *pose, float size_hint)
{
    if (frame < 0 || frame <= h->newest)
        return -1;
    if (h->newest >= 0 && frame - h->newest >= POSE_HISTORY_FRAMES) {
        for (int i = 0; i < POSE_HISTORY_FRAMES; i++)
            h->frame[i] = -1;
    }
    const int slot = (int)(frame % POSE_HISTORY_FRAMES);
    h->frame[slot] = frame;
    h->pose[slot] = *pose;
    h->size_hint[slot] = size_hint;
    h->newest = frame;
    return 0;
}

int64_t pose_history_last_frame(const PoseHistory *h)
{
    return h->newest;
}

int pose_history_window(const PoseHistory *h, unsigned n, float vis_min, unsigned max_gap, NormPose *out,
                        unsigned char *present)
{
    if (n == 0 || n > POSE_HISTORY_FRAMES || h->newest < 0)
        return -1;

    const int64_t first = h->newest - (int64_t)n + 1;
    int usable = 0;
    for (unsigned i = 0; i < n; i++) {
        const int64_t f = first + (int64_t)i;
        int ok = 0;
        memset(&out[i], 0, sizeof(out[i]));
        if (f >= 0) {
            const int slot = (int)(f % POSE_HISTORY_FRAMES);
            if (h->frame[slot] == f)
                ok = pose_normalize(&h->pose[slot], vis_min, h->size_hint[slot], &out[i]) == 0;
        }
        if (present != NULL)
            present[i] = (unsigned char)ok;
        usable += ok;
    }

    /* repair short holes, keypoint by keypoint */
    for (int k = 0; k < POSE_KEYPOINTS; k++) {
        int last_valid = -1;
        for (unsigned i = 0; i < n; i++) {
            if (!out[i].valid[k])
                continue;
            if (last_valid >= 0 && i - (unsigned)last_valid > 1 && i - (unsigned)last_valid - 1 <= max_gap) {
                const float span = (float)(i - (unsigned)last_valid);
                for (unsigned j = (unsigned)last_valid + 1; j < i; j++) {
                    const float t = (float)(j - (unsigned)last_valid) / span;
                    out[j].x[k] = out[last_valid].x[k] + t * (out[i].x[k] - out[last_valid].x[k]);
                    out[j].y[k] = out[last_valid].y[k] + t * (out[i].y[k] - out[last_valid].y[k]);
                    out[j].valid[k] = 1;
                }
            }
            last_valid = (int)i;
        }
    }
    return usable;
}
