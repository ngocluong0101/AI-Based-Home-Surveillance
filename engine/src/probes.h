/*
 * @author: Ngoc Luong <ngocluongg0101@gmail.com>
 */

/*
 * probes.h - the pad probes: they run on GStreamer streaming threads and must return fast (no network, no
 * heavy disk work); they only copy values out of the DeepStream metadata and hand them on.
 */
#ifndef DS_PROBES_H
#define DS_PROBES_H

#include "app.h"

/* On the src pad of nvinfer, FIRST of the probes there: with the pose model, turns the keypoints the parser put into the
 * object's "mask" (relative to the detector's box) into pixels of the frame, using that box. It has to happen before
 * the tracker, which moves the box a little (median 4%, ROADMAP_V2_NHAT_KY.md 4); after it the keypoints no longer
 * depend on the box. Does nothing for the detection-only model. */
GstPadProbeReturn pose_probe(GstPad *pad, GstPadProbeInfo *info, gpointer user_data);

/* On the src pad of nvinfer, before the tracker: applies each camera's conf_threshold / ai_enabled. */
GstPadProbeReturn settings_probe(GstPad *pad, GstPadProbeInfo *info, gpointer user_data);

/* On the src pad of nvtracker, while the batch is still whole: Detection[] per source, EventManager,
 * statistics, events and their snapshots pushed to the publisher thread's queue. */
GstPadProbeReturn detection_probe(GstPad *pad, GstPadProbeInfo *info, gpointer user_data);

#endif /* DS_PROBES_H */
