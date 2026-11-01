/*
 * ds_engine.c - DeepStream engine: N video sources in one batched pipeline.
 *
 * ROADMAP Phase 1.1: CLI parsing (--camera ID=URI, --cameras-file), the SourceCtx[N] table,
 * the pipeline N sources -> nvstreammux -> nvinfer -> fakesink, bus handling (ERROR/WARNING/EOS),
 * exit codes, and a probe that counts frames/objects per source_id. Things still to do are
 * marked TODO(roadmap item).
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
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>

#include "gstnvdsmeta.h"

#define EXIT_BAD_ARGS 2
#define EXIT_BUILD_FAILED 3

/* nvstreammux output size and how long it waits to fill a batch (microseconds). */
#define MUXER_WIDTH 1920
#define MUXER_HEIGHT 1080
#define MUXER_BATCH_TIMEOUT_USEC 40000

/* State of one source. source_id = declaration order of --camera, then of --cameras-file. */
typedef struct {
    guint source_id;
    gint camera_id;
    gchar *uri;
    GstElement *bin;     /* nvurisrcbin, owned by the pipeline */
    GstPad *mux_sinkpad; /* request pad "sink_<source_id>" of nvstreammux; we hold one ref */
    /* Written by the streaming thread (probe), read by the main thread after the pipeline
     * stopped, so no lock is needed. */
    guint64 frame_count;
    guint64 object_count;
    /* TODO(2.1): this source's EventManager.
     * TODO(3.6): fps, last-frame time, state (RUNNING / RECONNECTING). */
} SourceCtx;

typedef struct {
    GMainLoop *loop;
    GstElement *pipeline;
    SourceCtx *sources;
    guint num_sources;
    gint exit_code;
} AppCtx;

static gchar **opt_cameras = NULL;
static gchar *opt_cameras_file = NULL;
static gchar *opt_config = NULL;

static GOptionEntry option_entries[] = {
    { "camera", 'c', 0, G_OPTION_ARG_STRING_ARRAY, &opt_cameras,
      "Video source as ID=URI, repeat for each camera "
      "(e.g. --camera 1=file:///a.mp4 --camera 2=rtsp://...)", "ID=URI" },
    { "cameras-file", 0, 0, G_OPTION_ARG_FILENAME, &opt_cameras_file,
      "File (mode 600) with sources whose URI holds a password, so it stays out of argv. "
      "One group per camera: [camera.<ID>] with uri=...", "FILE" },
    { "config", 0, 0, G_OPTION_ARG_FILENAME, &opt_config,
      "nvinfer config file (required)", "FILE" },
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
    for (guint i = 0; i < app->num_sources; i++) {
        const SourceCtx *src = &app->sources[i];
        g_print("source_id=%u camera_id=%d frames=%" G_GUINT64_FORMAT " objects=%" G_GUINT64_FORMAT "\n",
                src->source_id, src->camera_id, src->frame_count, src->object_count);
    }
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
 * Pad probe: reads NvDsBatchMeta -> NvDsFrameMeta -> NvDsObjectMeta. For now it only counts
 * frames and objects per source_id, which shows that each source keeps its own source_id inside
 * one batch. Attached on the src pad of nvinfer; it moves to nvtracker (src pad) in 1.3/1.5.
 */
static GstPadProbeReturn detection_probe(GstPad *pad, GstPadProbeInfo *info, gpointer user_data)
{
    (void)pad;
    AppCtx *app = (AppCtx *)user_data;

    GstBuffer *buf = GST_PAD_PROBE_INFO_BUFFER(info);
    NvDsBatchMeta *batch_meta = (buf != NULL) ? gst_buffer_get_nvds_batch_meta(buf) : NULL;
    if (batch_meta == NULL)
        return GST_PAD_PROBE_OK;

    for (NvDsMetaList *l_frame = batch_meta->frame_meta_list; l_frame != NULL;
         l_frame = l_frame->next) {
        NvDsFrameMeta *frame_meta = (NvDsFrameMeta *)l_frame->data;
        if (frame_meta->source_id >= app->num_sources)
            continue;
        SourceCtx *src = &app->sources[frame_meta->source_id];

        src->frame_count++;
        for (NvDsMetaList *l_obj = frame_meta->obj_meta_list; l_obj != NULL; l_obj = l_obj->next)
            src->object_count++;
        /* TODO(1.4/1.5): read buf_pts and, per object, object_id (skip UNTRACKED_OBJECT_ID),
         * class_id (keep only person), confidence and rect_params; copy the values into a
         * Detection (own struct) and feed it to EventManager[source_id]. */
    }
    return GST_PAD_PROBE_OK;
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
 * Build the pipeline N sources -> nvstreammux -> nvinfer -> fakesink, the same code path for any
 * N >= 1. batch-size of nvstreammux and nvinfer = N. Returns NULL (after printing why) on error.
 */
static GstElement *build_pipeline(AppCtx *app)
{
    GstElement *pipeline = gst_pipeline_new("ds-engine");
    if (pipeline == NULL) {
        g_printerr("Could not create the pipeline\n");
        return NULL;
    }

    GstElement *streammux = make_element(pipeline, "nvstreammux", "stream-muxer");
    GstElement *pgie = make_element(pipeline, "nvinfer", "primary-infer");
    GstElement *sink = make_element(pipeline, "fakesink", "sink");
    if (streammux == NULL || pgie == NULL || sink == NULL)
        goto fail;

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
    /* sync=FALSE: files run as fast as the GPU allows. The event logic uses the stream PTS, not
     * the wall clock, so results do not depend on this. */
    g_object_set(sink, "sync", FALSE, NULL);

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

    if (!gst_element_link_many(streammux, pgie, sink, NULL)) {
        g_printerr("Could not link nvstreammux -> nvinfer -> fakesink\n");
        goto fail;
    }

    GstPad *pgie_src = gst_element_get_static_pad(pgie, "src");
    if (pgie_src == NULL) {
        g_printerr("Could not get the src pad of nvinfer\n");
        goto fail;
    }
    gst_pad_add_probe(pgie_src, GST_PAD_PROBE_TYPE_BUFFER, detection_probe, app, NULL);
    gst_object_unref(pgie_src);

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
    if (opt_config == NULL) {
        g_printerr("--config FILE (nvinfer config) is required\n");
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
            g_main_loop_run(app.loop);
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
    return app.exit_code;
}
