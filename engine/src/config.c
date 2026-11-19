/*
 * config.c - the command line and the cameras file (--cameras-file): option parsing and checking, and the
 * per-camera settings (conf_threshold, absence_timeout, ai_enabled).
 */
#include "config.h"

#include <glib/gstdio.h>
#include <gst/gst.h>
#include <string.h>
#include <sys/stat.h>

/* Defaults and limits of the options. */
#define HEARTBEAT_INTERVAL_DEFAULT_S 5
#define RTSP_TIMEOUT_DEFAULT_S 10
#define SNAPSHOT_QUALITY_DEFAULT 85
/* Longest snapshot directory accepted, so that dir + "/" + name stays a sane path. */
#define SNAPSHOT_DIR_MAX 700
/* Most cameras one engine is asked to have room for (--max-cameras). */
#define MAX_CAMERAS_LIMIT 32
#define STREAM_BITRATE_DEFAULT_KBPS 4000

gchar **opt_cameras = NULL;
gchar *opt_cameras_file = NULL;
gchar *opt_config = NULL;
gchar *opt_tracker_config = NULL;
gchar *opt_output = NULL;
gint opt_fps_interval = 5;
gchar *opt_snapshot_dir = NULL;
gint opt_snapshot_quality = SNAPSHOT_QUALITY_DEFAULT;
gchar *opt_backend_url = NULL;
gchar *opt_spool_file = NULL;
gchar *opt_heartbeat_url = NULL;
gchar *opt_stream_url = NULL;
gint opt_stream_bitrate = STREAM_BITRATE_DEFAULT_KBPS;
gint opt_heartbeat_interval = HEARTBEAT_INTERVAL_DEFAULT_S;
gint opt_rtsp_timeout = RTSP_TIMEOUT_DEFAULT_S;
gint opt_max_cameras = 0;
gchar *opt_dump_detections = NULL;
gdouble opt_absence_timeout = 10.0;  /* same default as ABSENCE_TIMEOUT in src/config */

static GOptionEntry option_entries[] = {
    { "snapshot-dir", 0, 0, G_OPTION_ARG_FILENAME, &opt_snapshot_dir,
      "Save a JPEG of the whole frame for each PERSON_DETECTED event in DIR "
      "(created if missing); without it no snapshots are taken", "DIR" },
    { "snapshot-quality", 0, 0, G_OPTION_ARG_INT, &opt_snapshot_quality,
      "JPEG quality 1-100 (default 85)", "Q" },
    { "backend-url", 0, 0, G_OPTION_ARG_STRING, &opt_backend_url,
      "POST each event as JSON to this URL (e.g. http://127.0.0.1:8000/internal/events); without "
      "it events are printed on stdout. Needs --spool-file. If the environment variable "
      "DS_ENGINE_TOKEN is set its value is sent as the header X-Internal-Token", "URL" },
    { "spool-file", 0, 0, G_OPTION_ARG_FILENAME, &opt_spool_file,
      "JSON-lines file where events wait while the backend cannot be reached; delivered when it "
      "is back, also after a restart of the engine", "FILE" },
    { "heartbeat-url", 0, 0, G_OPTION_ARG_STRING, &opt_heartbeat_url,
      "POST the state of every camera as JSON to this URL (e.g. http://127.0.0.1:8000/internal/heartbeat) "
      "each --heartbeat-interval seconds. Needs --backend-url; uses the same DS_ENGINE_TOKEN", "URL" },
    { "heartbeat-interval", 0, 0, G_OPTION_ARG_INT, &opt_heartbeat_interval,
      "Seconds between two heartbeats, 1-3600 (default 5)", "SEC" },
    { "stream-url", 0, 0, G_OPTION_ARG_STRING, &opt_stream_url,
      "Publish every camera, with boxes and track IDs drawn on it, over RTMP to a media server such as "
      "MediaMTX at BASE/cam<ID> (e.g. rtmp://127.0.0.1:1935). One H.264 encoder per camera. Cannot be "
      "combined with --output", "BASE" },
    { "stream-bitrate", 0, 0, G_OPTION_ARG_INT, &opt_stream_bitrate,
      "Bit rate of each published stream in kbit/s, 100-50000 (default 4000)", "KBPS" },
    { "max-cameras", 0, 0, G_OPTION_ARG_INT, &opt_max_cameras,
      "Batch size of the muxer and the detector: room for this many cameras, so that cameras can be added "
      "while the engine runs (0 = exactly the cameras given at start)", "N" },
    { "rtsp-timeout", 0, 0, G_OPTION_ARG_INT, &opt_rtsp_timeout,
      "Seconds without a frame after which an RTSP camera is switched off and on again, 0 = only "
      "react to errors (default 10)", "SEC" },
    { "absence-timeout", 0, 0, G_OPTION_ARG_DOUBLE, &opt_absence_timeout,
      "Seconds a person must be out of sight before the same track ID raises a new "
      "PERSON_DETECTED event (default 10)", "SEC" },
    { "dump-detections", 0, 0, G_OPTION_ARG_FILENAME, &opt_dump_detections,
      "Measuring tool: write every detection of nvinfer and every tracked object of nvtracker, frame by frame, "
      "to this CSV file (see dump.c for the columns)", "FILE" },
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
      "One group per camera: [camera.<ID>] with uri=... and optionally conf_threshold= (0-1), "
      "absence_timeout= (seconds) and ai_enabled= (true/false)", "FILE" },
    { "config", 0, 0, G_OPTION_ARG_FILENAME, &opt_config,
      "nvinfer config file (required)", "FILE" },
    { "tracker-config", 0, 0, G_OPTION_ARG_FILENAME, &opt_tracker_config,
      "nvtracker low-level config YAML, e.g. configs/tracker_nvdcf.yml (required)", "FILE" },
    { NULL, 0, 0, G_OPTION_ARG_NONE, NULL, NULL, NULL },
};

/* What a camera group of the cameras file says besides its address. */
typedef struct {
    gdouble conf_threshold;
    gdouble absence_timeout;   /* < 0: not given, --absence-timeout applies */
    gboolean ai_enabled;
} CameraSettings;

#define CONF_THRESHOLD_MAX 1.0
#define ABSENCE_TIMEOUT_MAX 86400.0

/* camera id -> CameraSettings, for the cameras that came from the cameras file */
GHashTable *file_settings = NULL;

GHashTable *new_settings_table(void)
{
    return g_hash_table_new_full(g_direct_hash, g_direct_equal, NULL, g_free);
}

/* The settings a camera runs with: what the file says, else the defaults (no extra filter, the
 * --absence-timeout, AI on). `table` may hold no entry for it. */
void settings_of(const GHashTable *table, gint camera_id, gdouble *conf, gdouble *absence, gboolean *ai)
{
    const CameraSettings *cs = table != NULL ? g_hash_table_lookup((GHashTable *)table, GINT_TO_POINTER(camera_id)) : NULL;
    *conf = cs != NULL ? cs->conf_threshold : 0.0;
    *ai = cs != NULL ? cs->ai_enabled : TRUE;
    *absence = (cs != NULL && cs->absence_timeout >= 0.0) ? cs->absence_timeout : opt_absence_timeout;
}

/*
 * Read the optional conf_threshold=, absence_timeout= and ai_enabled= of one [camera.<ID>] group and
 * store them in `settings` (camera id -> CameraSettings). Messages name the group, never the address.
 */
static gboolean read_camera_settings(GKeyFile *kf, const gchar *group, GHashTable *settings)
{
    gchar *end = NULL;
    gint64 id = g_ascii_strtoll(group + strlen("camera."), &end, 10);
    if (*end != '\0' || id < 0 || id > G_MAXINT)
        return TRUE;   /* a bad id is refused by init_sources() */

    CameraSettings s = { .conf_threshold = 0.0, .absence_timeout = -1.0, .ai_enabled = TRUE };
    GError *err = NULL;

    if (g_key_file_has_key(kf, group, "conf_threshold", NULL)) {
        s.conf_threshold = g_key_file_get_double(kf, group, "conf_threshold", &err);
        if (err != NULL || !(s.conf_threshold >= 0.0 && s.conf_threshold <= CONF_THRESHOLD_MAX)) {
            g_printerr("Cameras file: group '%s': conf_threshold must be a number between 0 and 1\n", group);
            g_clear_error(&err);
            return FALSE;
        }
    }
    if (g_key_file_has_key(kf, group, "absence_timeout", NULL)) {
        s.absence_timeout = g_key_file_get_double(kf, group, "absence_timeout", &err);
        if (err != NULL || !(s.absence_timeout >= 0.0 && s.absence_timeout <= ABSENCE_TIMEOUT_MAX)) {
            g_printerr("Cameras file: group '%s': absence_timeout must be a number of seconds between 0 "
                       "and 86400\n", group);
            g_clear_error(&err);
            return FALSE;
        }
    }
    if (g_key_file_has_key(kf, group, "ai_enabled", NULL)) {
        s.ai_enabled = g_key_file_get_boolean(kf, group, "ai_enabled", &err);
        if (err != NULL) {
            g_printerr("Cameras file: group '%s': ai_enabled must be true or false\n", group);
            g_clear_error(&err);
            return FALSE;
        }
    }

    g_hash_table_replace(settings, GINT_TO_POINTER((gint)id), g_memdup2(&s, sizeof(s)));
    return TRUE;
}

/* Split "ID=URI". Returns FALSE on a bad format or when ID is not a non-negative integer. */
gboolean parse_camera_arg(const gchar *arg, gint *camera_id, gchar **uri)
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
 * Append "ID=URI" specs read from a key file to `specs` and the per-camera settings to `settings`. The file holds credentials, so it must
 * not be readable by group/others. Error messages never include the URI.
 */
gboolean load_cameras_file(const gchar *path, GPtrArray *specs, GHashTable *settings)
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
            if (!read_camera_settings(kf, *g, settings))
                ok = FALSE;
        }
        g_free(uri);
    }
    g_strfreev(groups);
    g_key_file_free(kf);
    return ok;
}

gboolean config_load(int *argc, char ***argv, GPtrArray *specs)
{
    GError *error = NULL;

    GOptionContext *ctx = g_option_context_new("- DeepStream engine (N sources, 1 pipeline)");
    g_option_context_add_main_entries(ctx, option_entries, NULL);
    g_option_context_add_group(ctx, gst_init_get_option_group());
    if (!g_option_context_parse(ctx, argc, argv, &error)) {
        g_printerr("Option error: %s\n", error->message);
        g_error_free(error);
        g_option_context_free(ctx);
        return FALSE;
    }
    g_option_context_free(ctx);

    /* Collect "ID=URI" specs: --camera first (in order), then the cameras file. */
    for (gchar **c = opt_cameras; c != NULL && *c != NULL; c++)
        g_ptr_array_add(specs, g_strdup(*c));
    file_settings = new_settings_table();
    if (opt_cameras_file != NULL && !load_cameras_file(opt_cameras_file, specs, file_settings)) {
        return FALSE;
    }
    if (specs->len == 0) {
        g_printerr("At least one --camera ID=URI (or --cameras-file) is required\n");
        return FALSE;
    }
    if (opt_config == NULL || opt_tracker_config == NULL) {
        g_printerr("--config FILE (nvinfer config) and --tracker-config FILE (nvtracker YAML) "
                   "are required\n");
        return FALSE;
    }

    if (!(opt_absence_timeout >= 0.0 && opt_absence_timeout <= 86400.0)) {
        g_printerr("--absence-timeout must be between 0 and 86400 seconds\n");
        return FALSE;
    }

    if (opt_max_cameras < 0 || opt_max_cameras > MAX_CAMERAS_LIMIT) {
        g_printerr("--max-cameras must be between 0 and %d\n", MAX_CAMERAS_LIMIT);
        return FALSE;
    }
    if (opt_max_cameras > 0 && opt_output != NULL) {
        g_printerr("--max-cameras cannot be combined with --output (the tiled video has a fixed layout)\n");
        return FALSE;
    }

    if ((opt_backend_url == NULL) != (opt_spool_file == NULL)) {
        g_printerr("--backend-url and --spool-file go together: without a spool file events would "
                   "be lost while the backend is down\n");
        return FALSE;
    }
    if (opt_backend_url != NULL && !g_str_has_prefix(opt_backend_url, "http://") &&
        !g_str_has_prefix(opt_backend_url, "https://")) {
        g_printerr("--backend-url must start with http:// or https://\n");
        return FALSE;
    }

    if (opt_heartbeat_url != NULL && opt_backend_url == NULL) {
        g_printerr("--heartbeat-url needs --backend-url (the heartbeat goes to the same backend)\n");
        return FALSE;
    }
    if (opt_heartbeat_url != NULL && !g_str_has_prefix(opt_heartbeat_url, "http://") &&
        !g_str_has_prefix(opt_heartbeat_url, "https://")) {
        g_printerr("--heartbeat-url must start with http:// or https://\n");
        return FALSE;
    }
    if (opt_heartbeat_interval < 1 || opt_heartbeat_interval > 3600) {
        g_printerr("--heartbeat-interval must be between 1 and 3600 seconds\n");
        return FALSE;
    }
    if (opt_stream_url != NULL && opt_output != NULL) {
        g_printerr("--stream-url and --output cannot be used together (one tiles the cameras, the other "
                   "keeps them apart)\n");
        return FALSE;
    }
    if (opt_stream_url != NULL && !g_str_has_prefix(opt_stream_url, "rtmp://") &&
        !g_str_has_prefix(opt_stream_url, "rtmps://")) {
        g_printerr("--stream-url must start with rtmp:// or rtmps://\n");
        return FALSE;
    }
    if (opt_stream_url != NULL && opt_stream_url[strlen(opt_stream_url) - 1] == '/')
        opt_stream_url[strlen(opt_stream_url) - 1] = '\0';   /* BASE/cam<ID>, one slash */
    if (opt_stream_bitrate < 100 || opt_stream_bitrate > 50000) {
        g_printerr("--stream-bitrate must be between 100 and 50000 kbit/s\n");
        return FALSE;
    }
    if (opt_rtsp_timeout < 0 || opt_rtsp_timeout > 3600) {
        g_printerr("--rtsp-timeout must be between 0 and 3600 seconds\n");
        return FALSE;
    }
    if (opt_snapshot_quality < 1 || opt_snapshot_quality > 100) {
        g_printerr("--snapshot-quality must be between 1 and 100\n");
        return FALSE;
    }
    if (opt_snapshot_dir != NULL) {
        if (strlen(opt_snapshot_dir) > SNAPSHOT_DIR_MAX ||
            g_mkdir_with_parents(opt_snapshot_dir, 0755) != 0) {
            g_printerr("Cannot use --snapshot-dir (too long, or it cannot be created)\n");
            return FALSE;
        }
    }
    return TRUE;
}

void config_free(void)
{
    g_clear_pointer(&file_settings, g_hash_table_destroy);
    g_free(opt_cameras_file);
    g_free(opt_config);
    g_free(opt_tracker_config);
    g_free(opt_output);
    g_free(opt_dump_detections);
    g_free(opt_snapshot_dir);
    g_free(opt_backend_url);
    g_free(opt_spool_file);
    g_free(opt_heartbeat_url);
    g_free(opt_stream_url);
}
