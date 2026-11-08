/*
 * stream_out.h - the live video of each camera to a media server (--stream-url, ROADMAP 4.1): the encoding
 * branch inside the main pipeline, and one small publisher pipeline per camera (appsrc ! h264parse ! flvmux !
 * rtmp2sink) that is thrown away and built again when the server fails.
 */
#ifndef DS_STREAM_OUT_H
#define DS_STREAM_OUT_H

#include "app.h"

/* The encoding branch of one camera, fed by the "src_<source_id>" pad of nvstreamdemux. */
gboolean build_stream_branch(GstElement *pipeline, GstElement *demux, SourceCtx *src, gboolean live);

/* Build and start / take down the publisher of `src` (main thread). */
gboolean pub_create(SourceCtx *src);
void pub_destroy(SourceCtx *src);

/* Timer (main thread): build the failed publishers again after a pause. */
gboolean stream_retry_tick(gpointer data);

#endif /* DS_STREAM_OUT_H */
