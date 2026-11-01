/*
 * rtsp_test_server.c - a tiny RTSP "camera" for testing the engine's reconnect (ROADMAP 2.7).
 *
 * Serves the H.264 video of an MP4 file, in real time, at rtsp://127.0.0.1:PORT/MOUNT. Stop it
 * (Ctrl-C / kill) and start it again to make the camera go away and come back. Not part of the
 * product: an offline test tool, built with `make rtsp-test-server` (needs gst-rtsp-server).
 *
 *   build/rtsp_test_server FILE.mp4 [PORT [MOUNT]]      (defaults 8554 and /cam)
 */
#include <gst/gst.h>
#include <gst/rtsp-server/rtsp-server.h>
#include <stdio.h>
#include <stdlib.h>

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
    gst_rtsp_media_factory_set_shared(factory, TRUE);
    g_free(launch);

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
