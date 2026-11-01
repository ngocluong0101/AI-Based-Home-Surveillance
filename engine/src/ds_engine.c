/*
 * ds_engine.c - DeepStream engine: N video sources in one batched pipeline.
 *
 * This is the STARTING SKELETON (ROADMAP Phase 1.0/1.1). Already here: CLI parsing
 * (--camera ID=URI), the SourceCtx[N] table, bus handling (ERROR/WARNING/EOS) and exit codes.
 * Not here yet: building the pipeline (build_pipeline) and a real pad probe. Things still to do
 * are marked TODO(roadmap item).
 *
 * Conventions (see CLAUDE.md / ROADMAP.md):
 *  - All per-source state lives in SourceCtx[N]; no single global per-source variables.
 *  - Never keep NvDs*Meta pointers outside a probe callback; a probe returns GST_PAD_PROBE_OK fast.
 *  - Never log a full URI (an RTSP URL may contain a password); log only the scheme.
 *  - The DeepStream part is a single file; split it only when the repo owner asks for it.
 */
#include <glib.h>
#include <gst/gst.h>
#include <stdio.h>
#include <string.h>

#include "gstnvdsmeta.h"

#define EXIT_BAD_ARGS 2
#define EXIT_NOT_IMPLEMENTED 3

/* State of one source. source_id = declaration order of --camera. */
typedef struct {
    guint source_id;
    gint camera_id;
    gchar *uri;
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
static gchar *opt_config = NULL;

static GOptionEntry option_entries[] = {
    { "camera", 'c', 0, G_OPTION_ARG_STRING_ARRAY, &opt_cameras,
      "Video source as ID=URI, repeat for each camera "
      "(e.g. --camera 1=file:///a.mp4 --camera 2=rtsp://...)", "ID=URI" },
    { "config", 0, 0, G_OPTION_ARG_FILENAME, &opt_config,
      "nvinfer config file (TODO 1.2)", "FILE" },
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

/* Build SourceCtx[N] from the --camera options. Returns FALSE (and prints why) on error. */
static gboolean init_sources(AppCtx *app)
{
    app->num_sources = g_strv_length(opt_cameras);
    app->sources = g_new0(SourceCtx, app->num_sources);

    for (guint i = 0; i < app->num_sources; i++) {
        SourceCtx *src = &app->sources[i];
        src->source_id = i;

        if (!parse_camera_arg(opt_cameras[i], &src->camera_id, &src->uri)) {
            g_printerr("--camera '%s' has a bad format, expected ID=URI with ID an integer >= 0\n",
                       opt_cameras[i]);
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
    for (guint i = 0; i < app->num_sources; i++)
        g_free(app->sources[i].uri);
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

/* Bus: ERROR -> exit_code=1 and stop; WARNING -> log; EOS -> stop (exit 0). */
static gboolean bus_call(GstBus *bus, GstMessage *msg, gpointer data)
{
    (void)bus;
    AppCtx *app = (AppCtx *)data;

    switch (GST_MESSAGE_TYPE(msg)) {
    case GST_MESSAGE_EOS:
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
 * Pad probe: reads NvDsBatchMeta -> NvDsFrameMeta -> NvDsObjectMeta.
 * To be attached on the src pad of nvtracker (TODO 1.5). Not attached yet, hence marked unused.
 */
static G_GNUC_UNUSED GstPadProbeReturn detection_probe(GstPad *pad, GstPadProbeInfo *info,
                                                       gpointer user_data)
{
    (void)pad;
    (void)user_data;

    GstBuffer *buf = GST_PAD_PROBE_INFO_BUFFER(info);
    NvDsBatchMeta *batch_meta = (buf != NULL) ? gst_buffer_get_nvds_batch_meta(buf) : NULL;
    if (batch_meta == NULL)
        return GST_PAD_PROBE_OK;

    /* TODO(1.4/1.5): walk batch_meta->frame_meta_list; for each frame read source_id and buf_pts;
     * walk obj_meta_list, skip UNTRACKED_OBJECT_ID, keep only the person class; copy the values
     * into a Detection (own struct) and feed it to EventManager[source_id]. */
    return GST_PAD_PROBE_OK;
}

/*
 * Build the pipeline N sources -> nvstreammux -> nvinfer -> fakesink. Returns NULL when not
 * implemented yet or on error.
 *
 * TODO(1.1), do it with N = 1, then 2, then 4 on the SAME code path:
 *   1. gst_pipeline_new; create nvstreammux, set batch-size = app->num_sources, width/height,
 *      live-source.
 *   2. For each SourceCtx: create the source (nvurisrcbin or uridecodebin), request a "sink_%u"
 *      pad from nvstreammux with %u = source_id, link the source pad to it. Ignore audio tracks.
 *   3. nvstreammux -> nvinfer (config-file-path = opt_config, batch-size = N) -> fakesink.
 *   4. Check that every gst_element_factory_make != NULL; keep unref/free balanced on errors.
 */
static GstElement *build_pipeline(AppCtx *app)
{
    (void)app;
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

    if (opt_cameras == NULL || opt_cameras[0] == NULL) {
        g_printerr("At least one --camera ID=URI is required\n");
        return EXIT_BAD_ARGS;
    }
    /* TODO(1.1): read an RTSP password from a config file (mode 600) or an environment
     * variable, NOT from argv (it leaks through `ps`). */

    if (!init_sources(&app)) {
        free_sources(&app);
        g_strfreev(opt_cameras);
        return EXIT_BAD_ARGS;
    }
    print_sources(&app);

    app.loop = g_main_loop_new(NULL, FALSE);
    app.pipeline = build_pipeline(&app);
    if (app.pipeline == NULL) {
        g_printerr("Pipeline not implemented yet (TODO 1.1 in build_pipeline)\n");
        app.exit_code = EXIT_NOT_IMPLEMENTED;
    } else {
        GstBus *bus = gst_pipeline_get_bus(GST_PIPELINE(app.pipeline));
        guint bus_watch_id = gst_bus_add_watch(bus, bus_call, &app);
        gst_object_unref(bus);

        gst_element_set_state(app.pipeline, GST_STATE_PLAYING);
        g_main_loop_run(app.loop);

        gst_element_set_state(app.pipeline, GST_STATE_NULL);
        gst_object_unref(app.pipeline);
        g_source_remove(bus_watch_id);
    }

    g_main_loop_unref(app.loop);
    free_sources(&app);
    g_strfreev(opt_cameras);
    g_free(opt_config);
    return app.exit_code;
}
