/*
 * @author: Ngoc Luong <ngocluongg0101@gmail.com>
 */

/*
 * pose.h - a person's skeleton (17 COCO keypoints) and what the behaviour logic needs from it (V2-2): a scale- and position-free
 * version of one skeleton, and a short history per track that can be cut into a fixed-length window (rules of V2-3, ST-GCN of
 * V2-5). Pure C: the probe copies keypoints out of the metadata into a Pose. Coordinates are pixels of the nvstreammux frame, y DOWN.
 */
#ifndef POSE_H
#define POSE_H

#include <stdint.h>

#define POSE_KEYPOINTS 17
#define POSE_VALUES (POSE_KEYPOINTS * 3)   /* x, y, visibility per keypoint */

/* The COCO order the YOLOv8-pose model uses. */
enum {
    KP_NOSE, KP_LEFT_EYE, KP_RIGHT_EYE, KP_LEFT_EAR, KP_RIGHT_EAR,
    KP_LEFT_SHOULDER, KP_RIGHT_SHOULDER, KP_LEFT_ELBOW, KP_RIGHT_ELBOW, KP_LEFT_WRIST, KP_RIGHT_WRIST,
    KP_LEFT_HIP, KP_RIGHT_HIP, KP_LEFT_KNEE, KP_RIGHT_KNEE, KP_LEFT_ANKLE, KP_RIGHT_ANKLE
};

/* The model's visibility is a probability; a keypoint under this is treated as not seen. */
#define POSE_VISIBILITY_MIN 0.5f

typedef struct {
    float x, y;
    float v;   /* visibility, 0..1 */
} Keypoint;

typedef struct {
    Keypoint kp[POSE_KEYPOINTS];
} Pose;

/* values = 17 x (x, y, visibility). Returns 0, or -1 if a value is not a finite number (nothing written). */
int pose_from_values(const float values[POSE_VALUES], Pose *out);

/* One skeleton moved to the hip centre and divided by the torso length, so that a person standing 5 m away and
 * one standing 1 m away look the same, and a person walking across the frame stays at the origin. */
typedef struct {
    float x[POSE_KEYPOINTS], y[POSE_KEYPOINTS];
    unsigned char valid[POSE_KEYPOINTS];   /* 0: not seen (x, y are 0 then) */
} NormPose;

/*
 * Origin = middle of the visible hips (else shoulders). Scale = shoulder-centre to hip-centre distance (the torso); when one is
 * not visible, 0.3 * size_hint (the larger side of the person's box). A keypoint is valid when visibility >= vis_min. Returns 0,
 * or -1 when neither hips nor shoulders are visible or the scale is not positive (out is zeroed).
 */
int pose_normalize(const Pose *in, float vis_min, float size_hint, NormPose *out);

/* The last POSE_HISTORY_FRAMES frames of one track. */
#define POSE_HISTORY_FRAMES 128

typedef struct PoseHistory PoseHistory;

PoseHistory *pose_history_new(void);
void pose_history_free(PoseHistory *h);

/* Add the skeleton of frame number `frame` (the camera's own frame counter). Frames must increase: an older or
 * repeated frame number is ignored (returns -1). A jump of POSE_HISTORY_FRAMES or more forgets everything older. */
int pose_history_push(PoseHistory *h, int64_t frame, const Pose *pose, float size_hint);

/* Frame number of the newest skeleton, or -1 when empty. */
int64_t pose_history_last_frame(const PoseHistory *h);

/*
 * The n newest frames (n <= POSE_HISTORY_FRAMES), oldest first, normalized with pose_normalize(). A frame with no usable skeleton
 * has no valid keypoints. Each keypoint is then repaired: a stretch of at most max_gap invalid frames with a valid value on both
 * sides is filled by a straight line; longer stretches and the two ends stay invalid. present[i] (may be NULL) is 1 when frame i had
 * a usable skeleton before the repair. Returns the number of such frames, or -1 on bad arguments.
 */
int pose_history_window(const PoseHistory *h, unsigned n, float vis_min, unsigned max_gap, NormPose *out,
                        unsigned char *present);

/*
 * The input of the skeleton classifier (V2-5; eval/train/skeleton.py is the specification): the last n frames ending at
 * `end_frame` (a track with no skeleton in it gets no window), as (3, n, 17) floats. Channels 0/1 = x, y of each keypoint relative
 * to the window's origin (hip centre of the first frame that has one, else shoulder centre) divided by the window's scale (median
 * torso length, else 0.3 x median larger box side); channel 2 = 1 when the keypoint is usable, else 0 with x, y = 0. The origin is
 * fixed for the whole window, so a falling person shows as hips moving down. `out` has room for 3 * n * 17 floats. Returns 0, or -1
 * (nothing written) when end_frame has no skeleton, the window cannot be scaled, fewer than min(n, SKEL_MIN_FRAMES) frames have a
 * skeleton, or the arguments are bad. A window reaches back at most POSE_HISTORY_FRAMES frames.
 */
#define SKEL_MAX_GAP 5
#define SKEL_MIN_FRAMES 16   /* fewer frames with a skeleton in the window: no window */
int pose_history_skeleton_window(const PoseHistory *h, int64_t end_frame, unsigned n, float vis_min, float *out);

#endif /* POSE_H */
