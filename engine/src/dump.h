/*
 * @author: Ngoc Luong <ngocluongg0101@gmail.com>
 */

/*
 * dump.h - --dump-detections FILE: write what the detector and tracker found, frame by frame, to CSV for offline measurement
 * (AP, MOTA/IDF1...). A measuring tool, not part of the normal run.
 *
 * Probes only format a frame's rows into a string and queue it; a writer thread does the file I/O. The queue is unbounded: meant
 * for offline runs of a few minutes, not for a camera that runs for days.
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
