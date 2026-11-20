/*
 * ds_engine.c - DeepStream engine: N video sources in one batched pipeline.
 *
 * Pipeline: N sources -> nvstreammux -> nvinfer (YOLOv8n) -> nvtracker (NvDCF) -> fakesink, or with --output
 * -> tiler -> OSD -> H.264 -> MP4, or with --stream-url -> nvstreamdemux -> one encoding branch per camera.
 * This file builds that pipeline, handles the bus and the signals, and runs main(). The rest is in modules:
 *
 *   app.h         SourceCtx / AppCtx, the types everything shares
 *   config.c      command line, cameras file, per-camera settings
 *   sources.c     camera slots, add / remove while running (SIGHUP), RTSP loss detection
 *   probes.c      the pad probes: detections, events, snapshots, per-camera filters
 *   dump.c        --dump-detections: detections and tracks to a CSV file, for measuring
 *   delivery.c    the publisher thread: snapshot files, HTTP to the backend, the spool file
 *   stream_out.c  live video of each camera to a media server (RTMP)
 *   report.c      statistics, frame rate, heartbeat
 *   core/         pure C without DeepStream (EventManager, event JSON, spool...), tested on the host
 *
 * Conventions (see CLAUDE.md / ROADMAP.md):
 *  - All per-source state lives in SourceCtx[N]; no single global per-source variables.
 *  - Never keep NvDs*Meta pointers outside a probe callback; a probe returns GST_PAD_PROBE_OK fast.
 *  - Never log a full URI (an RTSP URL may contain a password); log only the scheme.
 */
#include <execinfo.h>
#include <glib-unix.h>
#include <signal.h>
#include <unistd.h>
#include <stdatomic.h>
#include <stdio.h>

#include "app.h"
#include "config.h"
#include "delivery.h"
#include "dump.h"
#include "nvds_obj_encode.h"
#include "probes.h"
#include "report.h"
#include "sources.h"
#include "stream_out.h"

/* NvDCF tracker (low-level library of nvtracker) and the frame size it works at; both must be
 * multiples of 32. The algorithm itself is chosen by the --tracker-config YAML file. */
#define TRACKER_LIB "/opt/nvidia/deepstream/deepstream/lib/libnvds_nvmultiobjecttracker.so"
#define TRACKER_WIDTH 960
#define TRACKER_HEIGHT 544

/* How often the main loop looks for RTSP sources that stopped delivering frames. */
#define WATCHDOG_INTERVAL_S 1

/* GPU used for the JPEG encoder of the snapshots (nvinfer and the muxer use the default, 0). */
#define GPU_ID 0

/* --output: size of the tiled video that holds all sources. */
#define TILER_WIDTH 1920
#define TILER_HEIGHT 1080

/* How long to wait for the EOS sent on SIGINT/SIGTERM to reach the MP4 muxer before stopping anyway. */
#define EOS_DRAIN_TIMEOUT_S 5

static gboolean eos_drain_timeout(gpointer data)
{
    AppCtx *app = (AppCtx *)data;
    g_printerr("WARNING: EOS did not reach the output file in %d s, stopping anyway (the MP4 may be "
               "unreadable)\n", EOS_DRAIN_TIMEOUT_S);
    app->eos_timer_id = 0;
    g_main_loop_quit(app->loop);
    return G_SOURCE_REMOVE;
}

/* A crash prints where it happened (the stack of the crashing thread) before the process dies, so that a segfault of a long
 * run leaves something to look at without a debugger or a core dump (containers cannot count on either). Only async-signal-safe
 * calls after the first backtrace(), which loads libgcc and is therefore called once at start-up. Symbol names need -rdynamic
 * (Makefile); for the rest use addr2line on the offsets. The signal is then raised again with its default action. */
static void on_crash(int sig)
{
    static const char head[] = "\nds_engine: crashed (signal ";
    void *frames[48];
    char num[4] = {(char)('0' + sig / 10), (char)('0' + sig % 10), ')', '\n'};
    if (write(STDERR_FILENO, head, sizeof head - 1) < 0 || write(STDERR_FILENO, num, sizeof num) < 0)
        _exit(128 + sig);
    int n = backtrace(frames, 48);
    backtrace_symbols_fd(frames, n, STDERR_FILENO);
    signal(sig, SIG_DFL);
    raise(sig);
}

static void install_crash_handler(void)
{
    void *warm[1];
    backtrace(warm, 1);
    struct sigaction sa = {0};
    sa.sa_handler = on_crash;
    sigemptyset(&sa.sa_mask);
    sa.sa_flags = SA_RESETHAND;
    sigaction(SIGSEGV, &sa, NULL);
    sigaction(SIGABRT, &sa, NULL);
    sigaction(SIGBUS, &sa, NULL);
    sigaction(SIGFPE, &sa, NULL);
}

/* Stop on SIGINT/SIGTERM like on EOS: leave the loop, then the pipeline is stopped in order and the
 * publisher flushes. (RTSP sources never end by themselves, so this is how the engine is stopped.)
 * With --output the loop is left only after the pipeline posted EOS (bus_call), because an MP4 that
 * never saw EOS has no moov atom and cannot be played. A second signal stops at once. */
static gboolean on_signal(gpointer data)
{
    AppCtx *app = (AppCtx *)data;

    g_print("Stopping\n");
    if (opt_output != NULL && !app->eos_requested) {
        app->eos_requested = TRUE;
        /* EOS goes into the nvstreammux sink pads, not into the pipeline: an RTSP nvurisrcbin
         * swallows the EOS of its stream (that is how it reconnects), so it would never get here. */
        guint sent = 0;
        for (guint i = 0; i < app->num_sources; i++) {
            if (app->sources[i].mux_sinkpad != NULL &&
                gst_pad_send_event(app->sources[i].mux_sinkpad, gst_event_new_eos()))
                sent++;
        }
        if (sent > 0) {
            app->eos_timer_id = g_timeout_add_seconds(EOS_DRAIN_TIMEOUT_S, eos_drain_timeout, app);
            return G_SOURCE_CONTINUE;
        }
    }
    g_main_loop_quit(app->loop);
    return G_SOURCE_CONTINUE;
}

/* Bus: EOS -> stop (exit 0). ERROR of an RTSP source -> that source reconnects, the rest goes on; of
 * anything else (a file that cannot be read, inference, tracker, sink) -> exit_code=1 and stop.
 * (A file source that fails in a pipeline with live sources leaves nvstreammux starving the others,
 * so it is not tried to keep going; files are for tests, a camera is what disappears.) WARNING -> log. */
static gboolean bus_call(GstBus *bus, GstMessage *msg, gpointer data)
{
    (void)bus;
    AppCtx *app = (AppCtx *)data;

    switch (GST_MESSAGE_TYPE(msg)) {
    case GST_MESSAGE_EOS:
        /* The pipeline posts EOS only after nvstreammux saw EOS on every sink pad, i.e. when
         * ALL sources finished. */
        g_print("EOS\n");
        g_main_loop_quit(app->loop);
        break;
    case GST_MESSAGE_WARNING: {
        GError *err = NULL;
        gchar *debug = NULL;
        gst_message_parse_warning(msg, &err, &debug);
        g_printerr("WARNING from %s: %s\n", GST_OBJECT_NAME(msg->src), err->message);
        g_error_free(err);
        g_free(debug);
        break;
    }
    case GST_MESSAGE_ERROR: {
        GError *err = NULL;
        gchar *debug = NULL;
        gst_message_parse_error(msg, &err, &debug);
        SourceCtx *src = source_of_message(app, msg, debug);
        if (src != NULL && src->is_rtsp) {
            /* Not the debug text: for a camera it can hold the URL, with its password. */
            g_printerr("ERROR from source_id=%u camera_id=%d (%s): %s\n", src->source_id,
                       src->camera_id, GST_OBJECT_NAME(msg->src), err->message);
            rtsp_reconnect(src, "error");
        } else {
            g_printerr("ERROR from %s: %s\n", GST_OBJECT_NAME(msg->src), err->message);
            if (debug != NULL && src == NULL)
                g_printerr("  debug: %s\n", debug);
            app->exit_code = 1;
            g_main_loop_quit(app->loop);
        }
        g_error_free(err);
        g_free(debug);
        break;
    }
    default:
        break;
    }
    return TRUE;
}

/*
 * Build the pipeline N sources -> nvstreammux -> nvinfer -> nvtracker -> fakesink, the same path
 * for any N >= 1. With --output the fakesink is replaced by the debug branch
 * nvmultistreamtiler -> nvvideoconvert -> nvdsosd -> nvvideoconvert -> H.264 -> MP4 file; the
 * probe stays on nvtracker, before the tiler. batch-size of nvstreammux and nvinfer = N.
 * Returns NULL (after printing why) on error.
 */
static GstElement *build_pipeline(AppCtx *app)
{
    GstElement *pipeline = gst_pipeline_new("ds-engine");
    if (pipeline == NULL) {
        g_printerr("Could not create the pipeline\n");
        return NULL;
    }

    GstElement *tiler = NULL, *conv_osd = NULL, *osd = NULL, *conv_enc = NULL, *enc_caps = NULL;
    GstElement *encoder = NULL, *parser = NULL, *mux = NULL;
    GstElement *streammux = make_element(pipeline, "nvstreammux", "stream-muxer");
    GstElement *pgie = make_element(pipeline, "nvinfer", "primary-infer");
    GstElement *tracker = make_element(pipeline, "nvtracker", "tracker");
    GstElement *sink = NULL, *demux = NULL;
    if (streammux == NULL || pgie == NULL || tracker == NULL)
        goto fail;

    if (opt_output != NULL) {
        tiler = make_element(pipeline, "nvmultistreamtiler", "tiler");
        conv_osd = make_element(pipeline, "nvvideoconvert", "convert-for-osd");
        osd = make_element(pipeline, "nvdsosd", "osd");
        conv_enc = make_element(pipeline, "nvvideoconvert", "convert-for-encoder");
        enc_caps = make_element(pipeline, "capsfilter", "encoder-caps");
        encoder = make_element(pipeline, "nvv4l2h264enc", "encoder");
        parser = make_element(pipeline, "h264parse", "h264-parser");
        mux = make_element(pipeline, "qtmux", "mp4-muxer");
        sink = make_element(pipeline, "filesink", "sink");
        if (tiler == NULL || conv_osd == NULL || osd == NULL || conv_enc == NULL ||
            enc_caps == NULL || encoder == NULL || parser == NULL || mux == NULL || sink == NULL)
            goto fail;
    } else if (opt_stream_url != NULL) {
        demux = make_element(pipeline, "nvstreamdemux", "stream-demux");
        if (demux == NULL)
            goto fail;
    } else {
        sink = make_element(pipeline, "fakesink", "sink");
        if (sink == NULL)
            goto fail;
    }

    gboolean live = FALSE;
    guint present = 0;
    for (guint i = 0; i < app->num_sources; i++) {
        if (app->sources[i].uri == NULL)
            continue;
        present++;
        gchar *scheme = g_uri_parse_scheme(app->sources[i].uri);
        if (scheme != NULL && g_ascii_strcasecmp(scheme, "rtsp") == 0)
            live = TRUE;
        g_free(scheme);
    }
    if (app->num_sources > present)
        live = TRUE;   /* room for more: whatever is added later is most likely a camera, and the muxer must not wait for all of them */
    app->streammux = streammux;
    app->demux = demux;
    app->live = live;
    const guint batch = app->num_sources;
    g_object_set(streammux, "batch-size", batch, "width", MUXER_WIDTH, "height",
                 MUXER_HEIGHT, "batched-push-timeout", MUXER_BATCH_TIMEOUT_USEC, "live-source",
                 live, NULL);
    if (opt_stream_url != NULL)
        g_object_set(streammux, "buffer-pool-size",
                     MIN((guint)MUXER_POOL_MAX, (guint)STREAM_POOL_EXTRA + batch), NULL);

    /* batch-size after config-file-path, so it overrides any batch-size in the config. */
    g_object_set(pgie, "config-file-path", opt_config, "batch-size", batch, NULL);
    g_object_set(tracker, "ll-lib-file", TRACKER_LIB, "ll-config-file", opt_tracker_config,
                 "tracker-width", TRACKER_WIDTH, "tracker-height", TRACKER_HEIGHT, NULL);
    /* sync=FALSE: files run as fast as the GPU allows. The event logic uses the stream PTS, not
     * the wall clock, so results do not depend on this. */
    if (sink != NULL)
        g_object_set(sink, "sync", FALSE, NULL);

    if (opt_output != NULL) {
        /* smallest rows x columns grid that holds all sources */
        guint rows = 1;
        while (rows * rows < app->num_sources)
            rows++;
        guint columns = (app->num_sources + rows - 1) / rows;
        g_object_set(tiler, "rows", rows, "columns", columns, "width", TILER_WIDTH, "height",
                     TILER_HEIGHT, NULL);

        GstCaps *caps = gst_caps_from_string("video/x-raw(memory:NVMM), format=I420");
        g_object_set(enc_caps, "caps", caps, NULL);
        gst_caps_unref(caps);
        g_object_set(sink, "location", opt_output, NULL);
    }

    for (guint i = 0; i < app->num_sources; i++) {
        SourceCtx *src = &app->sources[i];
        if (src->uri != NULL && !attach_source(app, pipeline, src))
            goto fail;
    }

    if (!gst_element_link_many(streammux, pgie, tracker, NULL)) {
        g_printerr("Could not link nvstreammux -> nvinfer -> nvtracker\n");
        goto fail;
    }
    gboolean tail_linked =
        (opt_output != NULL)
            ? gst_element_link_many(tracker, tiler, conv_osd, osd, conv_enc, enc_caps, encoder,
                                    parser, mux, sink, NULL)
            : (demux != NULL) ? gst_element_link(tracker, demux)
                              : gst_element_link_many(tracker, sink, NULL);
    if (!tail_linked) {
        g_printerr("Could not link the end of the pipeline after nvtracker\n");
        goto fail;
    }

    /* A pad for every slot now, linked to its branch when the slot gets a camera (see src->demux_pad). */
    for (guint i = 0; demux != NULL && i < app->num_sources; i++) {
        gchar *demux_pad_name = g_strdup_printf("src_%u", i);
        app->sources[i].demux_pad = gst_element_request_pad_simple(demux, demux_pad_name);
        if (app->sources[i].demux_pad == NULL)
            g_printerr("Could not request pad '%s' from nvstreamdemux\n", demux_pad_name);
        g_free(demux_pad_name);
        if (app->sources[i].demux_pad == NULL)
            goto fail;
    }
    for (guint i = 0; demux != NULL && i < app->num_sources; i++) {
        gboolean built;
        if (app->sources[i].uri != NULL && !attach_branch(app, pipeline, &app->sources[i], &built))
            goto fail;
    }

    /* Before the tiler (or any demux), while the batch is still whole. */
    GstPad *tracker_src = gst_element_get_static_pad(tracker, "src");
    if (tracker_src == NULL) {
        g_printerr("Could not get the src pad of nvtracker\n");
        goto fail;
    }
    if (opt_dump_detections != NULL)
        gst_pad_add_probe(tracker_src, GST_PAD_PROBE_TYPE_BUFFER, dump_trk_probe, app, NULL);
    gst_pad_add_probe(tracker_src, GST_PAD_PROBE_TYPE_BUFFER, detection_probe, app, NULL);
    gst_object_unref(tracker_src);

    /* Always attached, because a SIGHUP can turn a filter on later; with the default settings it only
     * looks at each frame's two settings and moves on. */
    GstPad *pgie_src = gst_element_get_static_pad(pgie, "src");
    if (pgie_src == NULL) {
        g_printerr("Could not get the src pad of nvinfer\n");
        goto fail;
    }
    /* pose_probe first (keypoints to frame pixels), then the dump, so that both see what the detector made before
     * settings_probe removes anything. */
    gst_pad_add_probe(pgie_src, GST_PAD_PROBE_TYPE_BUFFER, pose_probe, app, NULL);
    if (opt_dump_detections != NULL)
        gst_pad_add_probe(pgie_src, GST_PAD_PROBE_TYPE_BUFFER, dump_det_probe, app, NULL);
    gst_pad_add_probe(pgie_src, GST_PAD_PROBE_TYPE_BUFFER, settings_probe, app, NULL);
    gst_object_unref(pgie_src);

    return pipeline;

fail:
    gst_object_unref(pipeline);
    for (guint i = 0; i < app->num_sources; i++) {
        app->sources[i].bin = NULL;
        app->sources[i].stream_bin = NULL;
        atomic_store(&app->sources[i].active, FALSE);
    }
    return NULL;
}

/* Windows waiting for the worker; a full queue drops its oldest (fallnet.c). 8 cameras x 5 people x 30 fps is 1200 windows/s. */
#define FALLNET_QUEUE_MAX 256

int main(int argc, char *argv[])
{
    AppCtx app = { 0 };
    g_mutex_init(&app.slot_lock);

    /* Collect "ID=URI" specs: --camera first (in order), then the cameras file. */
    GPtrArray *specs = g_ptr_array_new_with_free_func(g_free);
    if (!config_load(&argc, &argv, specs)) {
        g_ptr_array_free(specs, TRUE);
        return EXIT_BAD_ARGS;
    }

    /* The skeleton network (V2-5), loaded before the cameras are set up because each camera decides from it whether it uses fall_net. A model that
     * cannot be loaded is not an error: the cameras use the rules (fall_model_ready stays FALSE and config.c forces fall_net = 0). */
    if (opt_fall_model != NULL) {
        char err[256];
        app.fallnet = fallnet_new(opt_fall_model, opt_fall_model_window, opt_fall_model_batch, FALLNET_QUEUE_MAX, err, sizeof err);
        if (app.fallnet != NULL) {
            app.fallnet_window = g_new(float, (gsize)3 * (gsize)opt_fall_model_window * 17);
            fall_model_ready = TRUE;
            g_print("fall model: %s, window %d frames, batch up to %d\n", opt_fall_model, opt_fall_model_window, opt_fall_model_batch);
        } else {
            g_printerr("WARNING: the fall model is not used (%s); cameras fall back to the rules\n", err);
        }
    }

    gboolean sources_ok = init_sources(&app, specs);
    g_ptr_array_free(specs, TRUE);
    g_strfreev(opt_cameras);
    if (!sources_ok) {
        free_sources(&app);
        return EXIT_BAD_ARGS;
    }
    print_sources(&app);

    install_crash_handler();

    /* Installed this early so that a SIGHUP during the start (which can include minutes of TensorRT
     * building) waits for the main loop instead of killing the process; on_reload() runs once it runs. */
    guint sighup_id = g_unix_signal_add(SIGHUP, on_reload, &app);

    if (!publisher_start(&app)) {
        g_printerr("Could not start the publisher thread\n");
        free_sources(&app);
        return EXIT_BUILD_FAILED;
    }

    if (opt_snapshot_dir != NULL || opt_frame_ring_dir != NULL) {
        app.snap_ctx = nvds_obj_enc_create_context(GPU_ID);
        if (app.snap_ctx == NULL) {
            g_printerr("Could not create the JPEG encoder context\n");
            publisher_stop(&app);
            free_sources(&app);
            return EXIT_BUILD_FAILED;
        }
    }

    if (opt_dump_detections != NULL && !dump_start(opt_dump_detections)) {
        publisher_stop(&app);
        free_sources(&app);
        return EXIT_BUILD_FAILED;
    }

    app.loop = g_main_loop_new(NULL, FALSE);
    app.pipeline = build_pipeline(&app);
    if (app.pipeline == NULL) {
        app.exit_code = EXIT_BUILD_FAILED;
        publisher_stop(&app);
        dump_stop();
    } else {
        GstBus *bus = gst_pipeline_get_bus(GST_PIPELINE(app.pipeline));
        guint bus_watch_id = gst_bus_add_watch(bus, bus_call, &app);
        gst_object_unref(bus);

        if (gst_element_set_state(app.pipeline, GST_STATE_PLAYING) == GST_STATE_CHANGE_FAILURE) {
            g_printerr("Could not set the pipeline to PLAYING\n");
            app.exit_code = 1;
        } else {
            guint fps_timer_id = 0, watchdog_id = 0, heartbeat_id = 0, stream_id = 0;
            if (opt_fps_interval > 0)
                fps_timer_id = g_timeout_add_seconds((guint)opt_fps_interval, report_fps, &app);
            app.start_us = g_get_monotonic_time();
            if (opt_stream_url != NULL) {
                for (guint i = 0; i < app.num_sources; i++) {
                    if (!app.sources[i].active)
                        continue;
                    if (!pub_create(&app.sources[i])) {   /* tried again by the timer */
                        app.sources[i].pub_failed = TRUE;
                        app.sources[i].pub_failed_us = g_get_monotonic_time();
                    }
                }
                stream_id = g_timeout_add_seconds(1, stream_retry_tick, &app);
            }
            if (opt_heartbeat_url != NULL) {
                app.hb_prev_us = app.start_us;
                heartbeat_id = g_timeout_add_seconds((guint)opt_heartbeat_interval, heartbeat_tick, &app);
            }
            for (guint i = 0; i < app.num_sources; i++)   /* the clock of the "no frames" check */
                atomic_store_explicit(&app.sources[i].last_frame_us, app.start_us, memory_order_relaxed);
            /* Always: a camera that is added later may be an RTSP one. */
            watchdog_id = g_timeout_add_seconds(WATCHDOG_INTERVAL_S, watchdog_tick, &app);
            guint sigint_id = g_unix_signal_add(SIGINT, on_signal, &app);
            guint sigterm_id = g_unix_signal_add(SIGTERM, on_signal, &app);
            g_main_loop_run(app.loop);
            app.end_us = g_get_monotonic_time();
            g_source_remove(sigint_id);
            g_source_remove(sigterm_id);
            g_source_remove(sighup_id);
            if (app.eos_timer_id != 0)
                g_source_remove(app.eos_timer_id);
            if (fps_timer_id != 0)
                g_source_remove(fps_timer_id);
            if (watchdog_id != 0)
                g_source_remove(watchdog_id);
            if (heartbeat_id != 0)
                g_source_remove(heartbeat_id);
            if (stream_id != 0)
                g_source_remove(stream_id);

        }

        for (guint i = 0; i < app.num_sources; i++)
            pub_destroy(&app.sources[i]);
        gst_element_set_state(app.pipeline, GST_STATE_NULL);
        gst_object_unref(app.pipeline);
        g_source_remove(bus_watch_id);
        publisher_stop(&app);   /* the pipeline is down, so no more events come */
        dump_stop();
        print_stats(&app);
    }

    if (app.snap_ctx != NULL)
        nvds_obj_enc_destroy_context(app.snap_ctx);
    if (app.event_queue != NULL)
        g_async_queue_unref(app.event_queue);
    g_main_loop_unref(app.loop);
    free_sources(&app);
    g_mutex_clear(&app.slot_lock);
    config_free();
    curl_global_cleanup();
    return app.exit_code;
}
