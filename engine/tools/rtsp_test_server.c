/*
 * @author: Ngoc Luong <ngocluongg0101@gmail.com>
 */

/*
 * rtsp_test_server.c - a tiny RTSP "camera" for testing the engine's reconnect (ROADMAP 2.7): serves the H.264 video of an MP4 in
 * real time at rtsp://127.0.0.1:PORT/MOUNT. Stop and restart it to make the camera go away and come back. An offline test tool,
 * built with `make rtsp-test-server` (needs gst-rtsp-server).
 *
 * build/rtsp_test_server FILE.mp4 [PORT [MOUNT]]      (defaults 8554 and /cam)
 */
#include <gst/gst.h>
#include <gst/rtsp-server/rtsp-server.h>
#include <stdio.h>
#include <stdlib.h>

/* V2-4 (M4): print "pkt <pts_ns> <wall_ns>" (CLOCK_REALTIME) for the first RTP buffers a client's stream produces, so that
 * eval/e2e/latency.py can tell when a given frame of the clip was "on the air". The first buffers may be produced while the media
 * prerolls, before the client's PLAY, so the caller fits wall - pts over the later, paced ones instead of trusting the first. */
static GstPadProbeReturn pkt_probe(GstPad *pad, GstPadProbeInfo *info, gpointer data)
{
    (void)pad;
    gint *n = data;
    if (*n >= 120)
        return GST_PAD_PROBE_REMOVE;
    GstBuffer *buf = NULL;
    if (info->type & GST_PAD_PROBE_TYPE_BUFFER_LIST) {
        GstBufferList *l = GST_PAD_PROBE_INFO_BUFFER_LIST(info);
        if (gst_buffer_list_length(l) > 0)
            buf = gst_buffer_list_get(l, 0);
    } else {
        buf = GST_PAD_PROBE_INFO_BUFFER(info);
    }
    if (buf && GST_BUFFER_PTS_IS_VALID(buf)) {
        g_print("pkt %" G_GUINT64_FORMAT " %" G_GINT64_FORMAT "\n", (guint64)GST_BUFFER_PTS(buf), g_get_real_time() * 1000);
        fflush(stdout);
        (*n)++;
    }
    return GST_PAD_PROBE_OK;
}

static void media_configure(GstRTSPMediaFactory *factory, GstRTSPMedia *media, gpointer data)
{
    (void)factory; (void)data;
    GstElement *bin = gst_rtsp_media_get_element(media);
    GstElement *pay = gst_bin_get_by_name_recurse_up(GST_BIN(bin), "pay0");
    if (pay) {
        GstPad *pad = gst_element_get_static_pad(pay, "src");
        gint *n = g_new0(gint, 1);
        gst_pad_add_probe(pad, GST_PAD_PROBE_TYPE_BUFFER | GST_PAD_PROBE_TYPE_BUFFER_LIST, pkt_probe, n, g_free);
        gst_object_unref(pad);
        gst_object_unref(pay);
    }
    gst_object_unref(bin);
}

int main(int argc, char **argv)
{
    gst_init(&argc, &argv);
    if (argc < 2) {
        g_printerr("usage: %s FILE.mp4 [PORT [MOUNT]]\n", argv[0]);
        return 2;
    }
    const char *port = argc > 2 ? argv[2] : "8554";
    const char *mount = argc > 3 ? argv[3] : "/cam";

    GMainLoop *loop = g_main_loop_new(NULL, FALSE);
    GstRTSPServer *server = gst_rtsp_server_new();
    gst_rtsp_server_set_service(server, port);

    /* h264parse re-sends SPS/PPS before every key frame so that a client joining late can decode. */
    gchar *launch = g_strdup_printf("( filesrc location=\"%s\" ! qtdemux ! h264parse ! "
                                    "rtph264pay name=pay0 pt=96 config-interval=-1 )", argv[1]);
    GstRTSPMediaFactory *factory = gst_rtsp_media_factory_new();
    gst_rtsp_media_factory_set_launch(factory, launch);
    /* Shared: every client gets the same running stream, so a reconnecting camera rejoins "live".
     * RTSP_TEST_UNSHARED=1 (the benchmark): every client gets its own copy starting at 0, so that
     * N clients that connect at different times all have the whole file in front of them. */
    gst_rtsp_media_factory_set_shared(factory, g_getenv("RTSP_TEST_UNSHARED") == NULL);
    g_free(launch);
    g_signal_connect(factory, "media-configure", G_CALLBACK(media_configure), NULL);

    GstRTSPMountPoints *mounts = gst_rtsp_server_get_mount_points(server);
    gst_rtsp_mount_points_add_factory(mounts, mount, factory);
    g_object_unref(mounts);

    if (gst_rtsp_server_attach(server, NULL) == 0) {
        g_printerr("Could not listen on port %s\n", port);
        return 1;
    }
    g_print("serving %s on rtsp://127.0.0.1:%s%s\n", argv[1], port, mount);
    g_main_loop_run(loop);
    return 0;
}
