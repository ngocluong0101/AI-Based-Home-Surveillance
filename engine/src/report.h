/*
 * @author: Ngoc Luong <ngocluongg0101@gmail.com>
 */

/*
 * report.h - what the engine says about itself: the source table at start, statistics at the end, the frame rate every few
 * seconds, and the heartbeat. All on the main thread, where the source states live.
 */
#ifndef DS_REPORT_H
#define DS_REPORT_H

#include "app.h"

void print_sources(const AppCtx *app);
void print_stats(const AppCtx *app);

/* Timer: print the frame rate of every source over the last interval. */
gboolean report_fps(gpointer data);

/* Timer (ROADMAP 3.6): the state of every camera, queued for the publisher thread to POST. */
gboolean heartbeat_tick(gpointer data);

#endif /* DS_REPORT_H */
