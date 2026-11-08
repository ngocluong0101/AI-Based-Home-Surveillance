/*
 * probes.h - the pad probes: they run on GStreamer streaming threads and must return fast (no network, no
 * heavy disk work); they only copy values out of the DeepStream metadata and hand them on.
 */
#ifndef DS_PROBES_H
#define DS_PROBES_H

#include "app.h"

/* On the src pad of nvinfer, before the tracker: applies each camera's conf_threshold / ai_enabled. */
GstPadProbeReturn settings_probe(GstPad *pad, GstPadProbeInfo *info, gpointer user_data);

/* On the src pad of nvtracker, while the batch is still whole: Detection[] per source, EventManager,
 * statistics, events and their snapshots pushed to the publisher thread's queue. */
GstPadProbeReturn detection_probe(GstPad *pad, GstPadProbeInfo *info, gpointer user_data);

#endif /* DS_PROBES_H */
