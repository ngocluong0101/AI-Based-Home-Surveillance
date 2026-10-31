/*
 * ds_engine.c - engine DeepStream: N nguồn video trong 1 pipeline batch.
 *
 * Đây là KHUNG khởi đầu (ROADMAP Phase 1.0/1.1). Đã có: đọc CLI (--camera ID=URI),
 * bảng nguồn SourceCtx[N], xử lý bus (ERROR/WARNING/EOS) và mã thoát. Chưa có: dựng pipeline
 * (build_pipeline), pad probe thật. Các chỗ cần làm đánh dấu TODO(mục roadmap).
 *
 * Quy ước (xem CLAUDE.md / ROADMAP.md):
 *  - Mọi trạng thái theo nguồn nằm trong SourceCtx[N], không dùng biến global đơn lẻ.
 *  - Không giữ con trỏ NvDs*Meta ra ngoài callback probe; probe luôn trả GST_PAD_PROBE_OK nhanh.
 *  - Không log nguyên URI (RTSP có thể chứa mật khẩu); chỉ log scheme.
 *  - Một file duy nhất cho phần DeepStream; chỉ tách khi chủ repo yêu cầu.
 */
#include <glib.h>
#include <gst/gst.h>
#include <stdio.h>
#include <string.h>

#include "gstnvdsmeta.h"

#define EXIT_BAD_ARGS 2
#define EXIT_NOT_IMPLEMENTED 3

/* Trạng thái của một nguồn. source_id = thứ tự khai báo --camera. */
typedef struct {
    guint source_id;
    gint camera_id;
    gchar *uri;
    /* TODO(2.1): EventManager của nguồn này.
     * TODO(3.6): fps, last_frame time, state (RUNNING / RECONNECTING). */
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
      "Nguon video dang ID=URI, lap lai cho moi camera "
      "(vd. --camera 1=file:///a.mp4 --camera 2=rtsp://...)", "ID=URI" },
    { "config", 0, 0, G_OPTION_ARG_FILENAME, &opt_config,
      "File cau hinh nvinfer (TODO 1.2)", "FILE" },
    { NULL, 0, 0, G_OPTION_ARG_NONE, NULL, NULL, NULL },
};

/* Tách "ID=URI". Trả FALSE nếu sai định dạng hoặc ID không phải số nguyên không âm. */
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

/* Dựng SourceCtx[N] từ các --camera. Trả FALSE (và in lý do) nếu có lỗi. */
static gboolean init_sources(AppCtx *app)
{
    app->num_sources = g_strv_length(opt_cameras);
    app->sources = g_new0(SourceCtx, app->num_sources);

    for (guint i = 0; i < app->num_sources; i++) {
        SourceCtx *src = &app->sources[i];
        src->source_id = i;

        if (!parse_camera_arg(opt_cameras[i], &src->camera_id, &src->uri)) {
            g_printerr("--camera '%s' sai dinh dang, can ID=URI voi ID la so nguyen >= 0\n",
                       opt_cameras[i]);
            return FALSE;
        }
        for (guint j = 0; j < i; j++) {
            if (app->sources[j].camera_id == src->camera_id) {
                g_printerr("camera_id %d bi khai bao trung\n", src->camera_id);
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

/* Bus: ERROR -> exit_code=1 và dừng; WARNING -> log; EOS -> dừng (exit 0). */
static gboolean bus_call(GstBus *bus, GstMessage *msg, gpointer data)
{
    (void)bus;
    AppCtx *app = (AppCtx *)data;

    switch (GST_MESSAGE_TYPE(msg)) {
    case GST_MESSAGE_EOS:
        g_print("EOS\n");
        /* TODO(2.7): lỗi/EOS của MỘT nguồn không được dừng cả pipeline khi còn nguồn khác;
         * hiện chỉ xử lý EOS của cả pipeline. */
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
 * Pad probe: đọc NvDsBatchMeta -> NvDsFrameMeta -> NvDsObjectMeta.
 * Gắn trên src pad của nvtracker (TODO 1.5). Hiện chưa được gắn nên đánh dấu unused.
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

    /* TODO(1.4/1.5): duyệt batch_meta->frame_meta_list; với mỗi frame lấy source_id, buf_pts;
     * duyệt obj_meta_list, bỏ UNTRACKED_OBJECT_ID, chỉ lấy class person; copy giá trị sang
     * Detection (struct riêng) rồi đưa vào EventManager[source_id]. */
    return GST_PAD_PROBE_OK;
}

/*
 * Dựng pipeline N nguồn -> nvstreammux -> nvinfer -> fakesink. Trả NULL nếu chưa làm/lỗi.
 *
 * TODO(1.1), làm với N = 1 rồi 2 rồi 4 trên CÙNG một đường code:
 *   1. gst_pipeline_new; tạo nvstreammux, set batch-size = app->num_sources, width/height, live-source.
 *   2. Với mỗi SourceCtx: tạo nguồn (nvurisrcbin hoặc uridecodebin), xin pad "sink_%u" từ
 *      nvstreammux với %u = source_id, link pad nguồn -> pad đó. Bỏ qua track audio.
 *   3. nvstreammux -> nvinfer (config-file-path = opt_config, batch-size = N) -> fakesink.
 *   4. Kiểm tra mọi gst_element_factory_make != NULL; unref/free cân bằng khi lỗi.
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

    GOptionContext *ctx = g_option_context_new("- engine DeepStream (N nguon, 1 pipeline)");
    g_option_context_add_main_entries(ctx, option_entries, NULL);
    g_option_context_add_group(ctx, gst_init_get_option_group());
    if (!g_option_context_parse(ctx, &argc, &argv, &error)) {
        g_printerr("Loi tham so: %s\n", error->message);
        g_error_free(error);
        g_option_context_free(ctx);
        return EXIT_BAD_ARGS;
    }
    g_option_context_free(ctx);

    if (opt_cameras == NULL || opt_cameras[0] == NULL) {
        g_printerr("Can it nhat mot --camera ID=URI\n");
        return EXIT_BAD_ARGS;
    }
    /* TODO(1.1): RTSP co mat khau thi doc tu file config (quyen 600) hoac bien moi truong,
     * KHONG de trong argv (lo qua `ps`). */

    if (!init_sources(&app)) {
        free_sources(&app);
        g_strfreev(opt_cameras);
        return EXIT_BAD_ARGS;
    }
    print_sources(&app);

    app.loop = g_main_loop_new(NULL, FALSE);
    app.pipeline = build_pipeline(&app);
    if (app.pipeline == NULL) {
        g_printerr("Pipeline chua duoc trien khai (TODO 1.1 trong build_pipeline)\n");
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
