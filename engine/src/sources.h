/*
 * @author: Ngoc Luong <ngocluongg0101@gmail.com>
 */

/*
 * sources.h - the camera slots and their life in the running pipeline: fill/empty a slot, add or remove a camera's nvurisrcbin,
 * RTSP loss detection, and the SIGHUP reload. Main thread only unless a comment says otherwise.
 */
#ifndef DS_SOURCES_H
#define DS_SOURCES_H

#include "app.h"

/* Build SourceCtx[slots] and fill the first slots from the "ID=URI" specs. Prints why and returns FALSE on error. */
gboolean init_sources(AppCtx *app, const GPtrArray *specs);
void free_sources(AppCtx *app);

/* The source whose bin contains the element that posted `msg`, or NULL. `debug` is the message's debug text, or NULL. */
SourceCtx *source_of_message(AppCtx *app, GstMessage *msg, const gchar *debug);

/* An RTSP source lost its camera: note it (state RECONNECTING); nvurisrcbin does the reconnecting itself. */
void rtsp_reconnect(SourceCtx *src, const char *why);

/* Timer: finds RTSP sources that went quiet, and notes when a lost source delivers frames again. */
gboolean watchdog_tick(gpointer data);

/* SIGHUP: read the cameras file again and bring the running engine in line with it. */
gboolean on_reload(gpointer data);

/* Pipeline building (build_pipeline): the source bin of a slot, and the encoding branch of a slot (once). */
gboolean attach_source(AppCtx *app, GstElement *pipeline, SourceCtx *src);
gboolean attach_branch(AppCtx *app, GstElement *pipeline, SourceCtx *src, gboolean *built);

#endif /* DS_SOURCES_H */
