/*
 * detection.h - one tracked person in one frame, as plain values.
 *
 * Pure C, no DeepStream or GLib: ds_engine.c copies the values out of NvDs*Meta into this struct
 * inside the pad probe (never keep NvDs*Meta pointers), and everything downstream (the
 * EventManager of Phase 2) works on Detection only. That also lets `make test-core` test it.
 */
#ifndef DETECTION_H
#define DETECTION_H

#include <stdint.h>

typedef struct {
    uint32_t source_id;   /* index of the source inside the batch */
    uint64_t track_id;    /* unique only inside source_id, so the real key is (source_id, track_id) */
    float confidence;     /* detector score, 0..1 */
    float bbox[4];        /* x, y, w, h, each relative to the frame (0..1) */
    uint64_t pts;         /* presentation time of the frame inside its own stream, in ns */
} Detection;

/*
 * Convert a box in pixels (left, top, width, height) of a frame_w x frame_h frame to the relative
 * x, y, w, h of Detection.bbox, clipped to the frame. Returns 1 on success, 0 when the frame size
 * is not positive or nothing of the box is left inside the frame (out is then untouched).
 */
int detection_bbox_normalize(float left, float top, float width, float height,
                             float frame_w, float frame_h, float out[4]);

#endif /* DETECTION_H */
