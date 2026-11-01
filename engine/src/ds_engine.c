/*
 * ds_engine.c - DeepStream engine: N video sources in one batched pipeline.
 *
 * ROADMAP Phase 1: CLI parsing (--camera ID=URI, --cameras-file), the SourceCtx[N] table, the
 * pipeline N sources -> nvstreammux -> nvinfer -> nvtracker (NvDCF) -> fakesink, or with --output
 * -> tiler -> OSD -> H.264 -> MP4, bus handling (ERROR/WARNING/EOS), exit codes, and a probe on
 * the nvtracker src pad that copies the tracked persons of every frame into Detection structs and
 * gathers frame/object/track statistics per source_id. Things still to do are marked
 * TODO(roadmap item).
 *
 * Conventions (see CLAUDE.md / ROADMAP.md):
 *  - All per-source state lives in SourceCtx[N]; no single global per-source variables.
 *  - Never keep NvDs*Meta pointers outside a probe callback; a probe returns GST_PAD_PROBE_OK fast.
 *  - Never log a full URI (an RTSP URL may contain a password); log only the scheme.
 *  - The DeepStream part is a single file; split it only when the repo owner asks for it.
 */
#include <glib.h>
#include <glib/gstdio.h>
#include <gst/gst.h>
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>

#include "detection.h"
#include "event_manager.h"
#include "gstnvdsmeta.h"

#define EXIT_BAD_ARGS 2
#define EXIT_BUILD_FAILED 3

/* nvstreammux output size and how long it waits to fill a batch (microseconds). */
#define MUXER_WIDTH 1920
#define MUXER_HEIGHT 1080
#define MUXER_BATCH_TIMEOUT_USEC 40000

/* NvDCF tracker (low-level library of nvtracker) and the frame size it works at; both must be
 * multiples of 32. The algorithm itself is chosen by the --tracker-config YAML file. */
#define TRACKER_LIB "/opt/nvidia/deepstream/deepstream/lib/libnvds_nvmultiobjecttracker.so"
#define TRACKER_WIDTH 960
#define TRACKER_HEIGHT 544

/* Tracks shorter than this many frames count as "short" in the end-of-run statistics: a high
 * share of them means the tracker fragments one person into several IDs. */
#define SHORT_TRACK_FRAMES 10

/* Most Detection entries gathered from one frame (nvtracker keeps at most 150 targets per
 * stream, see maxTargetsPerStream in the tracker config). */
#define MAX_DETECTIONS_PER_FRAME 256

/* Class id of "person" in the nvinfer output (the YOLOv8 parser reports only this class). */
#define PERSON_CLASS_ID 0

/* --output: size of the tiled video that holds all sources. */
#define TILER_WIDTH 1920
#define TILER_HEIGHT 1080

/* State of one source. source_id = declaration order of --camera, then of --cameras-file. */
typedef struct {
    guint source_id;
    gint camera_id;
    gchar *uri;
    GstElement *bin;     /* nvurisrcbin, owned by the pipeline */
    GstPad *mux_sinkpad; /* request pad "sink_<source_id>" of nvstreammux; we hold one ref */
    /* Written by the streaming thread (probe). frame_count is atomic because the FPS timer reads
     * it while the pipeline runs; the other counters are read only after it stopped, so they
     * need no lock. */
    _Atomic guint64 frame_count;
    guint64 fps_prev_frames;  /* frame_count at the previous FPS report (main thread only) */
    guint64 object_count;
    guint64 untracked_count;  /* objects that came out of the tracker without an ID */
    /* track_id -> number of frames it was seen in. Statistics only (ROADMAP 1.3); it is replaced
     * by the EventManager state in Phase 2. */
    GHashTable *track_frames;
    /* Event de-duplication of this source (ROADMAP 2.1). Touched only by the probe, which runs on
     * one streaming thread, and read after the pipeline stopped, so it needs no lock. */
    EventManager events;
    /* Wall-clock time (ns since the Unix epoch) that PTS 0 of this stream corresponds to, fixed by
     * the first frame seen; the EventManager clock is stream_start_ns + pts. */
    gint64 stream_start_ns;
    gboolean have_stream_start;
    guint64 event_count;   /* PERSON_DETECTED events emitted */
    guint64 event_errors;  /* frames dropped from event processing because memory ran out */
    /* TODO(3.6): fps, last-frame time, state (RUNNING / RECONNECTING). */
} SourceCtx;

typedef struct {
    GMainLoop *loop;
    GstElement *pipeline;
    SourceCtx *sources;
    guint num_sources;
    gint exit_code;
    gint64 start_us;  /* monotonic time when the pipeline went to PLAYING, for the summary only */
    gint64 end_us;
} AppCtx;

static gchar **opt_cameras = NULL;
static gchar *opt_cameras_file = NULL;
static gchar *opt_config = NULL;
static gchar *opt_tracker_config = NULL;
static gchar *opt_output = NULL;
static gint opt_fps_interval = 5;
static gdouble opt_absence_timeout = 10.0;  /* same default as ABSENCE_TIMEOUT in src/config */

static GOptionEntry option_entries[] = {
    { "absence-timeout", 0, 0, G_OPTION_ARG_DOUBLE, &opt_absence_timeout,
      "Seconds a person must be out of sight before the same track ID raises a new "
      "PERSON_DETECTED event (default 10)", "SEC" },
    { "output", 0, 0, G_OPTION_ARG_FILENAME, &opt_output,
      "Debug: write an MP4 of all sources tiled in one video, with boxes and track IDs drawn on it "
      "(without it nothing is rendered)", "FILE" },
    { "fps-interval", 0, 0, G_OPTION_ARG_INT, &opt_fps_interval,
      "Print the frame rate of every source each SEC seconds, 0 = off (default 5)", "SEC" },
    { "camera", 'c', 0, G_OPTION_ARG_STRING_ARRAY, &opt_cameras,
      "Video source as ID=URI, repeat for each camera "
      "(e.g. --camera 1=file:///a.mp4 --camera 2=rtsp://...)", "ID=URI" },
    { "cameras-file", 0, 0, G_OPTION_ARG_FILENAME, &opt_cameras_file,
      "File (mode 600) with sources whose URI holds a password, so it stays out of argv. "
      "One group per camera: [camera.<ID>] with uri=...", "FILE" },
    { "config", 0, 0, G_OPTION_ARG_FILENAME, &opt_config,
      "nvinfer config file (required)", "FILE" },
    { "tracker-config", 0, 0, G_OPTION_ARG_FILENAME, &opt_tracker_config,
      "nvtracker low-level config YAML, e.g. configs/tracker_nvdcf.yml (required)", "FILE" },
    { NULL, 0, 0, G_OPTION_ARG_NONE, NULL, NULL, NULL },
};

/* Split "ID=URI". Returns FALSE on a bad format or when ID is not a non-negative integer. */
static gboolean parse_camera_arg(const gchar *arg, gint *camera_id, gchar **uri)
{
    const gchar *eq = strchr(arg, '=');
    if (eq == NULL || eq == arg || eq[1] == '\0')
        return FALSE;

    gchar *id_text = g_strndup(arg, (gsize)(eq - arg));
    gchar *end = NULL;
    gint64 id = g_ascii_strtoll(id_text, &end, 10);
    gboolean ok = (*end == '\0' && id >= 0 && id <= G_MAXINT);
    g_free(id_text);
    if (!ok)
        return FALSE;

    *camera_id = (gint)id;
    *uri = g_strdup(eq + 1);
    return TRUE;
}

/*
 * Append "ID=URI" specs read from a key file to `specs`. The file holds credentials, so it must
 * not be readable by group/others. Error messages never include the URI.
 */
static gboolean load_cameras_file(const gchar *path, GPtrArray *specs)
{
    GStatBuf st;
    if (g_stat(path, &st) != 0) {
        g_printerr("Cannot read cameras file '%s'\n", path);
        return FALSE;
    }
    if ((st.st_mode & (S_IRWXG | S_IRWXO)) != 0) {
        g_printerr("Cameras file '%s' must not be accessible by group/others (chmod 600)\n", path);
        return FALSE;
    }

    GKeyFile *kf = g_key_file_new();
    GError *err = NULL;
    gboolean ok = TRUE;
    if (!g_key_file_load_from_file(kf, path, G_KEY_FILE_NONE, &err)) {
        g_printerr("Cameras file '%s': %s\n", path, err->message);
        g_error_free(err);
        g_key_file_free(kf);
        return FALSE;
    }

    gchar **groups = g_key_file_get_groups(kf, NULL);
    for (gchar **g = groups; *g != NULL; g++) {
        if (!g_str_has_prefix(*g, "camera.")) {
            g_printerr("Cameras file: ignoring unknown group '%s'\n", *g);
            continue;
        }
        gchar *uri = g_key_file_get_string(kf, *g, "uri", NULL);
        if (uri == NULL || uri[0] == '\0') {
            g_printerr("Cameras file: group '%s' has no uri=\n", *g);
            ok = FALSE;
        } else {
            g_ptr_array_add(specs, g_strdup_printf("%s=%s", *g + strlen("camera."), uri));
        }
        g_free(uri);
    }
    g_strfreev(groups);
    g_key_file_free(kf);
    return ok;
}

/* Build SourceCtx[N] from the "ID=URI" specs. Returns FALSE (and prints why) on error. */
static gboolean init_sources(AppCtx *app, const GPtrArray *specs)
{
    app->num_sources = specs->len;
    app->sources = g_new0(SourceCtx, app->num_sources);

    for (guint i = 0; i < app->num_sources; i++) {
        SourceCtx *src = &app->sources[i];
        src->source_id = i;
        src->track_frames = g_hash_table_new(g_direct_hash, g_direct_equal);
        event_manager_init(&src->events, opt_absence_timeout);

        /* Do not echo the spec: it may hold a password. */
        if (!parse_camera_arg(g_ptr_array_index(specs, i), &src->camera_id, &src->uri)) {
            g_printerr("Source #%u has a bad format, expected ID=URI with ID an integer >= 0\n", i);
            return FALSE;
        }
        for (guint j = 0; j < i; j++) {
            if (app->sources[j].camera_id == src->camera_id) {
                g_printerr("camera_id %d is declared more than once\n", src->camera_id);
                return FALSE;
            }
        }
    }
    return TRUE;
}

static void free_sources(AppCtx *app)
{
    for (guint i = 0; i < app->num_sources; i++) {
        g_free(app->sources[i].uri);
        event_manager_destroy(&app->sources[i].events);
        if (app->sources[i].track_frames != NULL)
            g_hash_table_destroy(app->sources[i].track_frames);
        if (app->sources[i].mux_sinkpad != NULL)
            gst_object_unref(app->sources[i].mux_sinkpad);
    }
    g_free(app->sources);
    app->sources = NULL;
    app->num_sources = 0;
}

static void print_sources(const AppCtx *app)
{
    for (guint i = 0; i < app->num_sources; i++) {
        const SourceCtx *src = &app->sources[i];
        gchar *scheme = g_uri_parse_scheme(src->uri);
        g_print("source_id=%u camera_id=%d scheme=%s\n", src->source_id, src->camera_id,
                scheme != NULL ? scheme : "?");
        g_free(scheme);
    }
}

static void print_stats(const AppCtx *app)
{
    guint64 total_frames = 0;
    double seconds = (double)(app->end_us - app->start_us) / G_USEC_PER_SEC;

    for (guint i = 0; i < app->num_sources; i++) {
        const SourceCtx *src = &app->sources[i];

        /* tracks = distinct track IDs; short = seen in fewer than SHORT_TRACK_FRAMES frames. */
        guint tracks = g_hash_table_size(src->track_frames);
        guint short_tracks = 0;
        GHashTableIter it;
        gpointer frames;
        g_hash_table_iter_init(&it, src->track_frames);
        while (g_hash_table_iter_next(&it, NULL, &frames)) {
            if (GPOINTER_TO_SIZE(frames) < SHORT_TRACK_FRAMES)
                short_tracks++;
        }

        g_print("source_id=%u camera_id=%d frames=%" G_GUINT64_FORMAT " objects=%" G_GUINT64_FORMAT
                " untracked=%" G_GUINT64_FORMAT " tracks=%u short_tracks=%u events=%"
                G_GUINT64_FORMAT " fps=%.1f\n",
                src->source_id, src->camera_id, (guint64)src->frame_count, src->object_count,
                src->untracked_count, tracks, short_tracks, src->event_count,
                seconds > 0.0 ? (double)src->frame_count / seconds : 0.0);
        if (src->event_errors > 0)
            g_printerr("source_id=%u: %" G_GUINT64_FORMAT " frames skipped by the event manager "
                       "(out of memory)\n", src->source_id, src->event_errors);
        total_frames += src->frame_count;
    }

    if (seconds > 0.0)
        g_print("elapsed=%.1fs total_frames=%" G_GUINT64_FORMAT " fps=%.1f (all sources together, "
                "includes startup)\n", seconds, total_frames, (double)total_frames / seconds);
}

/* Bus: ERROR -> exit_code=1 and stop; WARNING -> log; EOS -> stop (exit 0). */
static gboolean bus_call(GstBus *bus, GstMessage *msg, gpointer data)
{
    (void)bus;
    AppCtx *app = (AppCtx *)data;

    switch (GST_MESSAGE_TYPE(msg)) {
    case GST_MESSAGE_EOS:
        /* The pipeline posts EOS only after nvstreammux saw EOS on every sink pad, i.e. when
         * ALL sources finished. */
        g_print("EOS\n");
        /* TODO(2.7): an error/EOS of ONE source must not stop the whole pipeline while other
         * sources are still running; for now only the EOS of the whole pipeline is handled. */
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
        g_printerr("ERROR from %s: %s\n", GST_OBJECT_NAME(msg->src), err->message);
        if (debug != NULL)
            g_printerr("  debug: %s\n", debug);
        g_error_free(err);
        g_free(debug);
        app->exit_code = 1;
        g_main_loop_quit(app->loop);
        break;
    }
    default:
        break;
    }
    return TRUE;
}

/*
 * Copy the tracked persons of one frame into out[] (at most `max`). This is the only place that
 * reads NvDsObjectMeta; it keeps no pointer to it. Boxes are normalised to the muxer frame
 * (MUXER_WIDTH x MUXER_HEIGHT): nvstreammux scales every source to that size and rect_params is
 * expressed in it. *total is the number of objects in the frame, *untracked the number of them
 * that have no track ID yet (those are not copied). Returns the number of Detection written.
 */
static size_t extract_person_detections(const NvDsFrameMeta *frame_meta, Detection *out,
                                        size_t max, guint *total, guint *untracked)
{
    size_t n = 0;

    *total = 0;
    *untracked = 0;
    for (NvDsMetaList *l_obj = frame_meta->obj_meta_list; l_obj != NULL; l_obj = l_obj->next) {
        const NvDsObjectMeta *obj = (const NvDsObjectMeta *)l_obj->data;

        (*total)++;
        if (obj->object_id == UNTRACKED_OBJECT_ID) {
            (*untracked)++;
            continue;
        }
        if (obj->class_id != PERSON_CLASS_ID || n >= max)
            continue;

        Detection *det = &out[n];
        if (!detection_bbox_normalize(obj->rect_params.left, obj->rect_params.top,
                                      obj->rect_params.width, obj->rect_params.height,
                                      MUXER_WIDTH, MUXER_HEIGHT, det->bbox))
            continue;
        det->source_id = frame_meta->source_id;
        det->track_id = obj->object_id;
        det->confidence = obj->confidence;
        det->pts = frame_meta->buf_pts;
        n++;
    }
    return n;
}

/*
 * Pad probe on the src pad of nvtracker, before any tiler/demux so the batch is still whole:
 * NvDsBatchMeta -> one NvDsFrameMeta per source -> Detection[] for that source. For now the
 * Detection values only feed statistics per source_id (frames, objects, and for how many frames
 * each track ID was seen; object_id is unique only inside one source, hence one table per
 * SourceCtx). It does no I/O and returns at once.
 */
static GstPadProbeReturn detection_probe(GstPad *pad, GstPadProbeInfo *info, gpointer user_data)
{
    (void)pad;
    AppCtx *app = (AppCtx *)user_data;
    Detection dets[MAX_DETECTIONS_PER_FRAME];

    GstBuffer *buf = GST_PAD_PROBE_INFO_BUFFER(info);
    NvDsBatchMeta *batch_meta = (buf != NULL) ? gst_buffer_get_nvds_batch_meta(buf) : NULL;
    if (batch_meta == NULL)
        return GST_PAD_PROBE_OK;

    for (NvDsMetaList *l_frame = batch_meta->frame_meta_list; l_frame != NULL;
         l_frame = l_frame->next) {
        const NvDsFrameMeta *frame_meta = (const NvDsFrameMeta *)l_frame->data;
        if (frame_meta->source_id >= app->num_sources)
            continue;
        SourceCtx *src = &app->sources[frame_meta->source_id];

        guint total = 0, untracked = 0;
        size_t n = extract_person_detections(frame_meta, dets, G_N_ELEMENTS(dets), &total,
                                             &untracked);
        atomic_fetch_add_explicit(&src->frame_count, 1, memory_order_relaxed);
        src->object_count += total;
        src->untracked_count += untracked;

        for (size_t i = 0; i < n; i++) {
            gpointer key = GSIZE_TO_POINTER((gsize)dets[i].track_id);
            gsize seen = GPOINTER_TO_SIZE(g_hash_table_lookup(src->track_frames, key));
            g_hash_table_insert(src->track_frames, key, GSIZE_TO_POINTER(seen + 1));
        }

        /* Event clock = stream_start + PTS of the frame, never the wall clock at processing time,
         * so a file read faster than real time behaves like the same stream played live. Called
         * for every frame, also with n == 0: that is how absent tracks time out. */
        if (!src->have_stream_start) {
            src->stream_start_ns = g_get_real_time() * 1000 - (gint64)frame_meta->buf_pts;
            src->have_stream_start = TRUE;
        }
        PersonDetectedEvent events[MAX_DETECTIONS_PER_FRAME];
        int emitted = event_manager_process(&src->events, dets, n,
                                            src->stream_start_ns + (gint64)frame_meta->buf_pts,
                                            events, G_N_ELEMENTS(events));
        if (emitted < 0)
            src->event_errors++;
        else
            src->event_count += (guint64)emitted;
        /* TODO(2.2): snapshot + hand events[0..emitted) to the publisher queue. */
    }
    return GST_PAD_PROBE_OK;
}

/* Timer on the main loop: print the frame rate of every source over the last interval. */
static gboolean report_fps(gpointer data)
{
    AppCtx *app = (AppCtx *)data;

    for (guint i = 0; i < app->num_sources; i++) {
        SourceCtx *src = &app->sources[i];
        guint64 now = atomic_load_explicit(&src->frame_count, memory_order_relaxed);
        g_print("fps source_id=%u camera_id=%d %.1f\n", src->source_id, src->camera_id,
                (double)(now - src->fps_prev_frames) / (double)opt_fps_interval);
        src->fps_prev_frames = now;
    }
    return G_SOURCE_CONTINUE;
}

/* Create an element, check it, and add it to the bin (the bin takes ownership). */
static GstElement *make_element(GstElement *pipeline, const gchar *factory, const gchar *name)
{
    GstElement *elem = gst_element_factory_make(factory, name);
    if (elem == NULL) {
        g_printerr("Could not create element '%s' (%s)\n", name, factory);
        return NULL;
    }
    if (!gst_bin_add(GST_BIN(pipeline), elem)) {
        g_printerr("Could not add element '%s' to the pipeline\n", name);
        gst_object_unref(elem);
        return NULL;
    }
    return elem;
}

/* nvurisrcbin creates its video pad after it knows the stream: link it to the muxer pad that
 * was requested for this source. Audio (and any other non-video pad) is ignored. */
static void on_source_pad_added(GstElement *bin, GstPad *new_pad, gpointer user_data)
{
    SourceCtx *src = (SourceCtx *)user_data;

    GstCaps *caps = gst_pad_get_current_caps(new_pad);
    if (caps == NULL)
        caps = gst_pad_query_caps(new_pad, NULL);
    const gchar *media = gst_structure_get_name(gst_caps_get_structure(caps, 0));
    gboolean is_video = (media != NULL && g_str_has_prefix(media, "video"));
    gst_caps_unref(caps);
    if (!is_video)
        return;

    if (gst_pad_link(new_pad, src->mux_sinkpad) != GST_PAD_LINK_OK)
        GST_ELEMENT_ERROR(bin, CORE, PAD, (NULL),
                          ("Could not link source_id=%u to nvstreammux", src->source_id));
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
    GstElement *sink = NULL;
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
    } else {
        sink = make_element(pipeline, "fakesink", "sink");
        if (sink == NULL)
            goto fail;
    }

    gboolean live = FALSE;
    for (guint i = 0; i < app->num_sources; i++) {
        gchar *scheme = g_uri_parse_scheme(app->sources[i].uri);
        if (scheme != NULL && g_ascii_strcasecmp(scheme, "rtsp") == 0)
            live = TRUE;
        g_free(scheme);
    }
    g_object_set(streammux, "batch-size", app->num_sources, "width", MUXER_WIDTH, "height",
                 MUXER_HEIGHT, "batched-push-timeout", MUXER_BATCH_TIMEOUT_USEC, "live-source",
                 live, NULL);

    /* batch-size after config-file-path, so it overrides any batch-size in the config. */
    g_object_set(pgie, "config-file-path", opt_config, "batch-size", app->num_sources, NULL);
    g_object_set(tracker, "ll-lib-file", TRACKER_LIB, "ll-config-file", opt_tracker_config,
                 "tracker-width", TRACKER_WIDTH, "tracker-height", TRACKER_HEIGHT, NULL);
    /* sync=FALSE: files run as fast as the GPU allows. The event logic uses the stream PTS, not
     * the wall clock, so results do not depend on this. */
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

        gchar *bin_name = g_strdup_printf("source-%u", src->source_id);
        src->bin = make_element(pipeline, "nvurisrcbin", bin_name);
        g_free(bin_name);
        if (src->bin == NULL)
            goto fail;
        g_object_set(src->bin, "uri", src->uri, NULL);

        /* Request "sink_<source_id>" so that source_id == the muxer pad index == the
         * frame_meta->source_id seen in the probe. */
        gchar *pad_name = g_strdup_printf("sink_%u", src->source_id);
        src->mux_sinkpad = gst_element_request_pad_simple(streammux, pad_name);
        if (src->mux_sinkpad == NULL)
            g_printerr("Could not request pad '%s' from nvstreammux\n", pad_name);
        g_free(pad_name);
        if (src->mux_sinkpad == NULL)
            goto fail;

        g_signal_connect(src->bin, "pad-added", G_CALLBACK(on_source_pad_added), src);
    }

    if (!gst_element_link_many(streammux, pgie, tracker, NULL)) {
        g_printerr("Could not link nvstreammux -> nvinfer -> nvtracker\n");
        goto fail;
    }
    gboolean tail_linked =
        (opt_output != NULL)
            ? gst_element_link_many(tracker, tiler, conv_osd, osd, conv_enc, enc_caps, encoder,
                                    parser, mux, sink, NULL)
            : gst_element_link_many(tracker, sink, NULL);
    if (!tail_linked) {
        g_printerr("Could not link the end of the pipeline after nvtracker\n");
        goto fail;
    }

    /* Before the tiler (or any demux), while the batch is still whole. */
    GstPad *tracker_src = gst_element_get_static_pad(tracker, "src");
    if (tracker_src == NULL) {
        g_printerr("Could not get the src pad of nvtracker\n");
        goto fail;
    }
    gst_pad_add_probe(tracker_src, GST_PAD_PROBE_TYPE_BUFFER, detection_probe, app, NULL);
    gst_object_unref(tracker_src);

    return pipeline;

fail:
    gst_object_unref(pipeline);
    for (guint i = 0; i < app->num_sources; i++)
        app->sources[i].bin = NULL;
    return NULL;
}

int main(int argc, char *argv[])
{
    AppCtx app = { 0 };
    GError *error = NULL;

    GOptionContext *ctx = g_option_context_new("- DeepStream engine (N sources, 1 pipeline)");
    g_option_context_add_main_entries(ctx, option_entries, NULL);
    g_option_context_add_group(ctx, gst_init_get_option_group());
    if (!g_option_context_parse(ctx, &argc, &argv, &error)) {
        g_printerr("Option error: %s\n", error->message);
        g_error_free(error);
        g_option_context_free(ctx);
        return EXIT_BAD_ARGS;
    }
    g_option_context_free(ctx);

    /* Collect "ID=URI" specs: --camera first (in order), then the cameras file. */
    GPtrArray *specs = g_ptr_array_new_with_free_func(g_free);
    for (gchar **c = opt_cameras; c != NULL && *c != NULL; c++)
        g_ptr_array_add(specs, g_strdup(*c));
    if (opt_cameras_file != NULL && !load_cameras_file(opt_cameras_file, specs)) {
        g_ptr_array_free(specs, TRUE);
        return EXIT_BAD_ARGS;
    }
    if (specs->len == 0) {
        g_printerr("At least one --camera ID=URI (or --cameras-file) is required\n");
        g_ptr_array_free(specs, TRUE);
        return EXIT_BAD_ARGS;
    }
    if (opt_config == NULL || opt_tracker_config == NULL) {
        g_printerr("--config FILE (nvinfer config) and --tracker-config FILE (nvtracker YAML) "
                   "are required\n");
        g_ptr_array_free(specs, TRUE);
        return EXIT_BAD_ARGS;
    }

    if (!(opt_absence_timeout >= 0.0 && opt_absence_timeout <= 86400.0)) {
        g_printerr("--absence-timeout must be between 0 and 86400 seconds\n");
        g_ptr_array_free(specs, TRUE);
        return EXIT_BAD_ARGS;
    }

    gboolean sources_ok = init_sources(&app, specs);
    g_ptr_array_free(specs, TRUE);
    g_strfreev(opt_cameras);
    if (!sources_ok) {
        free_sources(&app);
        return EXIT_BAD_ARGS;
    }
    print_sources(&app);

    app.loop = g_main_loop_new(NULL, FALSE);
    app.pipeline = build_pipeline(&app);
    if (app.pipeline == NULL) {
        app.exit_code = EXIT_BUILD_FAILED;
    } else {
        GstBus *bus = gst_pipeline_get_bus(GST_PIPELINE(app.pipeline));
        guint bus_watch_id = gst_bus_add_watch(bus, bus_call, &app);
        gst_object_unref(bus);

        if (gst_element_set_state(app.pipeline, GST_STATE_PLAYING) == GST_STATE_CHANGE_FAILURE) {
            g_printerr("Could not set the pipeline to PLAYING\n");
            app.exit_code = 1;
        } else {
            guint fps_timer_id = 0;
            if (opt_fps_interval > 0)
                fps_timer_id = g_timeout_add_seconds((guint)opt_fps_interval, report_fps, &app);
            app.start_us = g_get_monotonic_time();
            g_main_loop_run(app.loop);
            app.end_us = g_get_monotonic_time();
            if (fps_timer_id != 0)
                g_source_remove(fps_timer_id);
        }

        gst_element_set_state(app.pipeline, GST_STATE_NULL);
        gst_object_unref(app.pipeline);
        g_source_remove(bus_watch_id);
        print_stats(&app);
    }

    g_main_loop_unref(app.loop);
    free_sources(&app);
    g_free(opt_cameras_file);
    g_free(opt_config);
    g_free(opt_tracker_config);
    g_free(opt_output);
    return app.exit_code;
}
