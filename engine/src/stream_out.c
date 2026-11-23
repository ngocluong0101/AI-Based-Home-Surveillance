/*
 * @author: Ngoc Luong <ngocluongg0101@gmail.com>
 */

/*
 * stream_out.c - the live video of each camera to a media server. See stream_out.h.
 */
#include "stream_out.h"

#include <gst/app/gstappsink.h>
#include <gst/app/gstappsrc.h>

#include "config.h"

/* The encoder settings of the stream branch (the bit rate is --stream-bitrate). A key frame every second at
 * 30 fps lets a browser that joins late start within a second. */
#define STREAM_KEYFRAME_INTERVAL 30
/* Seconds before a failed stream publisher is built again. */
#define STREAM_RETRY_S 5
/* Encoded video the publisher may hold while the server is slow; beyond it the oldest is dropped. */
#define STREAM_PUBLISH_QUEUE_BYTES (4 * 1024 * 1024)

/*
 * appsink callback of the encoding branch (a streaming thread): hand the encoded frame to this camera's publisher. A publisher
 * starts at a key frame and its time stamps start at 0. The copy shares the memory of the original.
 */
static GstFlowReturn stream_new_sample(GstAppSink *sink, gpointer data)
{
    SourceCtx *src = (SourceCtx *)data;
    GstSample *sample = gst_app_sink_pull_sample(sink);
    if (sample == NULL)
        return GST_FLOW_OK;   /* the appsink is shutting down */
    GstBuffer *buf = gst_sample_get_buffer(sample);

    g_mutex_lock(&src->pub_lock);
    if (src->pub_appsrc != NULL) {
        if (src->pub_need_key && !GST_BUFFER_FLAG_IS_SET(buf, GST_BUFFER_FLAG_DELTA_UNIT))
            src->pub_need_key = FALSE;
        if (!src->pub_need_key) {
            GstBuffer *out = gst_buffer_copy(buf);
            if (src->pub_pts_base == GST_CLOCK_TIME_NONE && GST_BUFFER_PTS_IS_VALID(buf))
                src->pub_pts_base = GST_BUFFER_PTS(buf);
            GST_BUFFER_PTS(out) = (GST_BUFFER_PTS_IS_VALID(buf) && GST_BUFFER_PTS(buf) >= src->pub_pts_base)
                                      ? GST_BUFFER_PTS(buf) - src->pub_pts_base : GST_CLOCK_TIME_NONE;
            GST_BUFFER_DTS(out) = (GST_BUFFER_DTS_IS_VALID(buf) && GST_BUFFER_DTS(buf) >= src->pub_pts_base)
                                      ? GST_BUFFER_DTS(buf) - src->pub_pts_base : GST_CLOCK_TIME_NONE;
            gst_app_src_push_buffer(GST_APP_SRC(src->pub_appsrc), out);   /* takes the buffer */
        }
    }
    g_mutex_unlock(&src->pub_lock);
    gst_sample_unref(sample);
    return GST_FLOW_OK;
}

/* Stop giving frames to the publisher of `src`. */
static void pub_stop_feeding(SourceCtx *src)
{
    g_mutex_lock(&src->pub_lock);
    src->pub_appsrc = NULL;
    g_mutex_unlock(&src->pub_lock);
}

/* Take the publisher of `src` down. rtmp2sink goes to NULL without trouble, which rtspclientsink does not
 * after a failed connection (it deadlocks). Main thread. */
void pub_destroy(SourceCtx *src)
{
    pub_stop_feeding(src);
    if (src->pub_bus_watch != 0)
        g_source_remove(src->pub_bus_watch);
    src->pub_bus_watch = 0;
    if (src->pub_pipeline != NULL) {
        gst_element_set_state(src->pub_pipeline, GST_STATE_NULL);
        gst_object_unref(src->pub_pipeline);
    }
    src->pub_pipeline = NULL;
}

/* Bus watch of one publisher pipeline (main thread). An error means the server is gone or refuses us:
 * stop feeding it and build a new publisher later. Nothing else is touched: detection and events go on. */
static gboolean pub_bus_call(GstBus *bus, GstMessage *msg, gpointer data)
{
    (void)bus;
    SourceCtx *src = (SourceCtx *)data;

    if (GST_MESSAGE_TYPE(msg) != GST_MESSAGE_ERROR)
        return TRUE;
    GError *err = NULL;
    gst_message_parse_error(msg, &err, NULL);   /* not the debug text: it can hold the server address */
    if (!src->pub_failed) {
        g_printerr("source_id=%u camera_id=%d: stream failed (%s), trying again in %d s\n", src->source_id,
                   src->camera_id, err->message, STREAM_RETRY_S);
        src->pub_failed = TRUE;
        src->pub_failed_us = g_get_monotonic_time();
        src->pub_failures++;
    }
    g_error_free(err);
    pub_stop_feeding(src);
    src->pub_bus_watch = 0;
    return G_SOURCE_REMOVE;
}

/* Build and start the publisher of `src`: appsrc ! h264parse ! flvmux ! rtmp2sink to BASE/cam<ID>. */
gboolean pub_create(SourceCtx *src)
{
    GstElement *pipeline = gst_pipeline_new(NULL);
    GstElement *appsrc = gst_element_factory_make("appsrc", "publisher-source");
    GstElement *parser = gst_element_factory_make("h264parse", "publisher-parser");
    GstElement *flv = gst_element_factory_make("flvmux", "publisher-muxer");
    GstElement *sink = gst_element_factory_make("rtmp2sink", "publisher-sink");
    if (pipeline == NULL || appsrc == NULL || parser == NULL || flv == NULL || sink == NULL) {
        g_printerr("Could not create the stream publisher of camera %d\n", src->camera_id);
        for (GstElement **e = (GstElement *[]){ pipeline, appsrc, parser, flv, sink, NULL }; *e != NULL; e++)
            gst_object_unref(*e);
        return FALSE;
    }
    gst_bin_add_many(GST_BIN(pipeline), appsrc, parser, flv, sink, NULL);

    GstCaps *caps = gst_caps_from_string("video/x-h264, stream-format=(string)byte-stream, "
                                         "alignment=(string)au");
    g_object_set(appsrc, "caps", caps, "format", GST_FORMAT_TIME, "is-live", TRUE, "block", FALSE,
                 "max-bytes", (guint64)STREAM_PUBLISH_QUEUE_BYTES, NULL);
    gst_caps_unref(caps);
    if (g_object_class_find_property(G_OBJECT_GET_CLASS(appsrc), "leaky-type") != NULL)
        gst_util_set_object_arg(G_OBJECT(appsrc), "leaky-type", "downstream");
    g_object_set(parser, "config-interval", -1, NULL);   /* SPS/PPS before every key frame */
    g_object_set(flv, "streamable", TRUE, NULL);
    gchar *location = g_strdup_printf("%s/cam%d", opt_stream_url, src->camera_id);
    g_object_set(sink, "location", location, "sync", FALSE, NULL);   /* the frames are already paced */
    g_free(location);

    if (!gst_element_link_many(appsrc, parser, flv, sink, NULL)) {
        g_printerr("Could not link the stream publisher of camera %d\n", src->camera_id);
        gst_object_unref(pipeline);
        return FALSE;
    }

    GstBus *bus = gst_pipeline_get_bus(GST_PIPELINE(pipeline));
    src->pub_bus_watch = gst_bus_add_watch(bus, pub_bus_call, src);
    gst_object_unref(bus);
    src->pub_pipeline = pipeline;

    g_mutex_lock(&src->pub_lock);
    src->pub_need_key = TRUE;
    src->pub_pts_base = GST_CLOCK_TIME_NONE;
    src->pub_appsrc = appsrc;   /* the pipeline owns it */
    g_mutex_unlock(&src->pub_lock);

    if (gst_element_set_state(pipeline, GST_STATE_PLAYING) == GST_STATE_CHANGE_FAILURE) {
        pub_destroy(src);
        return FALSE;
    }
    return TRUE;
}

/* Timer (main thread): build the failed publishers again after a pause. */
gboolean stream_retry_tick(gpointer data)
{
    AppCtx *app = (AppCtx *)data;
    gint64 now = g_get_monotonic_time();

    for (guint i = 0; i < app->num_sources; i++) {
        SourceCtx *src = &app->sources[i];
        if (!src->active || src->stream_bin == NULL || !src->pub_failed ||
            now - src->pub_failed_us < (gint64)STREAM_RETRY_S * G_USEC_PER_SEC)
            continue;
        pub_destroy(src);
        if (pub_create(src))
            src->pub_failed = FALSE;
        else
            src->pub_failed_us = now;
    }
    return G_SOURCE_CONTINUE;
}

/*
 * The encoding branch of one camera (ROADMAP 4.1), fed by the "src_<source_id>" pad of nvstreamdemux:
 * queue ! nvvideoconvert ! nvdsosd ! nvvideoconvert ! I420 ! nvv4l2h264enc ! h264parse ! appsink
 * nvdsosd draws boxes and track IDs from the frame metadata. The appsink only passes frames on, so nothing the server does can
 * stop this branch (the RTMP publisher is a pipeline of its own, see pub_create()). Live sources: the queue drops the oldest
 * frames and the appsink does not wait for the clock. Files: the queue blocks and the appsink waits, pacing the file to real time.
 */
gboolean build_stream_branch(GstElement *pipeline, GstElement *demux, SourceCtx *src, gboolean live)
{
    gchar *bin_name = g_strdup_printf("stream-%u", src->source_id);
    GstElement *bin = gst_bin_new(bin_name);
    g_free(bin_name);
    if (bin == NULL)
        return FALSE;

    GstElement *queue = make_element(bin, "queue", "queue");
    GstElement *conv_osd = make_element(bin, "nvvideoconvert", "convert-for-osd");
    GstElement *osd = make_element(bin, "nvdsosd", "osd");
    GstElement *conv_enc = make_element(bin, "nvvideoconvert", "convert-for-encoder");
    GstElement *caps = make_element(bin, "capsfilter", "encoder-caps");
    GstElement *encoder = make_element(bin, "nvv4l2h264enc", "encoder");
    GstElement *parser = make_element(bin, "h264parse", "parser");
    GstElement *out_caps = make_element(bin, "capsfilter", "output-caps");
    GstElement *sink = make_element(bin, "appsink", "frames");
    if (queue == NULL || conv_osd == NULL || osd == NULL || conv_enc == NULL || caps == NULL ||
        encoder == NULL || parser == NULL || out_caps == NULL || sink == NULL) {
        gst_object_unref(bin);   /* the elements added so far go with it */
        return FALSE;
    }

    g_object_set(queue, "max-size-buffers", STREAM_QUEUE_BUFFERS, "max-size-bytes", 0,
                 "max-size-time", (guint64)0, "leaky", live ? 2 : 0, NULL);
    GstCaps *i420 = gst_caps_from_string("video/x-raw(memory:NVMM), format=I420");
    g_object_set(caps, "caps", i420, NULL);
    gst_caps_unref(i420);
    g_object_set(encoder, "bitrate", (guint)opt_stream_bitrate * 1000, "iframeinterval",
                 STREAM_KEYFRAME_INTERVAL, "idrinterval", STREAM_KEYFRAME_INTERVAL, NULL);
    g_object_set(parser, "config-interval", -1, NULL);
    GstCaps *annexb = gst_caps_from_string("video/x-h264, stream-format=(string)byte-stream, "
                                           "alignment=(string)au");
    g_object_set(out_caps, "caps", annexb, NULL);
    gst_caps_unref(annexb);
    g_object_set(sink, "sync", !live, "max-buffers", 4, "drop", FALSE, "emit-signals", FALSE, NULL);
    GstAppSinkCallbacks callbacks = { .new_sample = stream_new_sample };
    gst_app_sink_set_callbacks(GST_APP_SINK(sink), &callbacks, src, NULL);

    GstPad *queue_sink = gst_element_get_static_pad(queue, "sink");
    GstPad *ghost = gst_ghost_pad_new("sink", queue_sink);
    gst_object_unref(queue_sink);
    gboolean ok = gst_element_link_many(queue, conv_osd, osd, conv_enc, caps, encoder, parser, out_caps,
                                        sink, NULL) &&
                  gst_element_add_pad(bin, ghost) && gst_bin_add(GST_BIN(pipeline), bin);
    if (!ok) {
        g_printerr("Could not build the stream branch of camera %d\n", src->camera_id);
        gst_object_unref(bin);
        return FALSE;
    }

    (void)demux;
    GstPadLinkReturn linked = (src->demux_pad != NULL) ? gst_pad_link(src->demux_pad, ghost) : GST_PAD_LINK_REFUSED;
    if (linked != GST_PAD_LINK_OK) {
        g_printerr("Could not link nvstreamdemux to the stream branch of camera %d (%s)\n", src->camera_id,
                   src->demux_pad == NULL ? "no pad" : gst_pad_link_get_name(linked));
        gst_bin_remove(GST_BIN(pipeline), bin);   /* so that a later try does not meet a bin of the same name */
        return FALSE;
    }
    src->stream_bin = bin;
    return TRUE;
}
