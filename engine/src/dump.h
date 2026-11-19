/*
 * dump.h - --dump-detections FILE: write what the detector and the tracker found, frame by frame, to a CSV
 * file, so that an offline script can measure it (AP against ground truth, MOTA/IDF1 of the tracker,
 * agreement with another detector). It is a measuring tool, not part of the normal run.
 *
 * The probes only format the rows of one frame into a string and push it to a queue; a writer thread does
 * the file I/O, so the probes stay fast. The queue is not bounded: this is for offline runs of a few minutes,
 * where the writer keeps up easily; do not leave it on for a camera that runs for days.
 */
#ifndef DS_DUMP_H
#define DS_DUMP_H

#include "app.h"

/* Open FILE (written from scratch) and start the writer thread. FALSE = could not open it (reported). */
gboolean dump_start(const gchar *path);

/* Let the writer finish what is queued, then close the file. Safe to call when dump_start() was not called. */
void dump_stop(void);

/* On the src pad of nvinfer, before settings_probe (so it sees every detection the detector made): rows
 * "det", one per object, with the detector's confidence. */
GstPadProbeReturn dump_det_probe(GstPad *pad, GstPadProbeInfo *info, gpointer user_data);

/* On the src pad of nvtracker: rows "trk", one per tracked object, with its track ID. */
GstPadProbeReturn dump_trk_probe(GstPad *pad, GstPadProbeInfo *info, gpointer user_data);

#endif /* DS_DUMP_H */
