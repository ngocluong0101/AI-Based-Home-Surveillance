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
#include <curl/curl.h>
#include <glib-unix.h>
#include <glib.h>
#include <glib/gstdio.h>
#include <gst/app/gstappsink.h>
#include <gst/app/gstappsrc.h>
#include <gst/gst.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>

#include "backoff.h"
#include "detection.h"
#include "event_manager.h"
#include "event_record.h"
#include "event_spool.h"
#include "heartbeat_record.h"
#include "gstnvdsmeta.h"
#include "nvbufsurface.h"
#include "nvds_obj_encode.h"

#define EXIT_BAD_ARGS 2
#define EXIT_BUILD_FAILED 3

/* nvstreammux output size and how long it waits to fill a batch (microseconds). Shorter than a frame
 * interval at 30 fps (33 ms): with a longer wait, a camera that is down would limit the others to
 * one frame per wait (25 fps at 40 ms) while the muxer waits for its frames. */
#define MUXER_WIDTH 1920
#define MUXER_HEIGHT 1080
#define MUXER_BATCH_TIMEOUT_USEC 20000

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

/* Events waiting for the publisher thread. A dead backend does not fill it: the worker moves
 * events to the spool file at once. It fills only if the worker is stuck in one slow request
 * (HTTP_TIMEOUT_MS at most); then new events are dropped and counted rather than letting memory
 * grow. */
#define EVENT_QUEUE_MAX 1024

/* Delivery to the backend (ROADMAP 2.6). Every request is bounded by these timeouts. */
#define HTTP_CONNECT_TIMEOUT_MS 2000
#define HTTP_TIMEOUT_MS 5000
/* Environment variable that holds the token for the backend's internal endpoints. */
#define TOKEN_ENV "DS_ENGINE_TOKEN"
/* Largest spool file; further events are lost (and counted) once it is full. */
#define SPOOL_MAX_BYTES (256ull * 1024 * 1024)
/* Events moved from the spool to the backend between two looks at the queue, so a long spool
 * does not keep the live events waiting. */
#define SPOOL_DRAIN_BATCH 50
/* How long the worker may keep trying to deliver when the engine is shutting down; what is left
 * stays in the spool for the next run. */
#define SHUTDOWN_GRACE_US (10 * G_USEC_PER_SEC)
/* The worker wakes up this often even when no event arrives, to empty the spool once the backend
 * is back. */
#define WORKER_IDLE_US G_USEC_PER_SEC

/* Heartbeat (ROADMAP 3.6): how the backend learns that the engine is alive and what every camera is
 * doing. Short timeouts: a heartbeat is worth nothing late, and the worker also has events to send. */
#define HEARTBEAT_INTERVAL_DEFAULT_S 5
#define HEARTBEAT_CONNECT_TIMEOUT_MS 1000
#define HEARTBEAT_TIMEOUT_MS 2000

/* How often the main loop looks for RTSP sources that stopped delivering frames. */
#define WATCHDOG_INTERVAL_S 1
#define RTSP_TIMEOUT_DEFAULT_S 10

/* GPU used for the JPEG encoder of the snapshots (nvinfer and the muxer use the default, 0). */
#define GPU_ID 0
#define SNAPSHOT_QUALITY_DEFAULT 85
/* Longest snapshot directory accepted, so that dir + "/" + name stays a sane path. */
#define SNAPSHOT_DIR_MAX 700
/* JPEG bytes that may wait in the queue (a 1080p frame is ~200 KB); beyond it a snapshot is skipped
 * and its events are published without one. */
#define SNAPSHOT_QUEUE_MAX_BYTES (64 * 1024 * 1024)

/* Class id of "person" in the nvinfer output (the YOLOv8 parser reports only this class). */
#define PERSON_CLASS_ID 0

/* Most cameras one engine is asked to have room for (--max-cameras). */
#define MAX_CAMERAS_LIMIT 32

/* Live stream of each camera to MediaMTX (ROADMAP 4.1): the encoder settings of the stream branch. A
 * key frame every second at 30 fps lets a browser that joins late start within a second. */
#define STREAM_BITRATE_DEFAULT_KBPS 4000
#define STREAM_KEYFRAME_INTERVAL 30
/* Frames the stream branch may hold while the stream cannot keep up (live pipelines drop the oldest
 * instead of stalling detection). Every one of them is a whole batch buffer of nvstreammux, taken from
 * its small fixed pool (STREAM_POOL_EXTRA + one per camera, at most 16): a branch that holds more
 * than its share while it is stuck, e.g. just after it reconnected, starves the pool and stops
 * the detection of every camera. */
#define STREAM_QUEUE_BUFFERS 1
#define STREAM_POOL_EXTRA 4
#define MUXER_POOL_MAX 16
/* Seconds before a failed stream publisher is built again. */
#define STREAM_RETRY_S 5
/* Encoded video the publisher may hold while the server is slow; beyond it the oldest is dropped. */
#define STREAM_PUBLISH_QUEUE_BYTES (4 * 1024 * 1024)

/* --output: size of the tiled video that holds all sources. */
#define TILER_WIDTH 1920
#define TILER_HEIGHT 1080

/* The people count of a camera is reported only while its latest frame is at most this old: after that
 * the number describes a scene we no longer see, and "0" would claim an empty room. */
#define PEOPLE_FRESH_US (2 * G_USEC_PER_SEC)

/* Where a source is (ROADMAP 2.7). An RTSP source can go from RUNNING to RECONNECTING and back; a
 * file source stays RUNNING until its EOS reaches the muxer, then it is ENDED for good. */
typedef enum { SOURCE_RUNNING, SOURCE_RECONNECTING, SOURCE_ENDED } SourceState;

struct AppCtx;

/* State of one source. source_id = declaration order of --camera, then of --cameras-file. */
typedef struct {
    struct AppCtx *app;
    guint source_id;
    gint camera_id;
    gchar *uri;
    gboolean is_rtsp;
    /* Per-camera settings from the cameras file. Set before the pipeline runs; SIGHUP (on_reload, main
     * thread) may change them later while the probes (streaming threads) read them, hence atomic. */
    _Atomic gdouble conf_threshold;   /* detections below it are dropped before tracking; 0 = keep what the detector gives */
    _Atomic gdouble absence_timeout;  /* seconds out of sight after which a track raises a new event */
    _Atomic gboolean ai_enabled;      /* FALSE: the picture goes on, but nobody is detected, tracked or reported */
    gdouble em_timeout;               /* probe only: the timeout the EventManager was last given */
    gboolean from_file;               /* the camera came from --cameras-file, so SIGHUP may change its settings */
    /* A slot holds one camera. There are as many slots as the muxer has batch places (num_sources); a slot
     * with uri == NULL is free. source_id is the slot number, which is also the muxer pad index and the
     * frame_meta->source_id the probes see. active = the camera is in the running pipeline: the probes, the
     * heartbeat and the timers look at active slots only. */
    _Atomic gboolean active;
    gint64 freed_us;                  /* monotonic time the slot was emptied; the longest-free slot is reused first */
    /* Reconnect (RTSP only). The reconnecting itself is done by nvurisrcbin (rtsp-reconnect-interval);
     * this is what the engine knows and reports. Everything but last_frame_us and reanchor belongs
     * to the main thread. */
    SourceState state;
    gint64 lost_us;              /* monotonic time the source was declared lost */
    guint64 reconnects;          /* times the source was lost */
    _Atomic gint64 last_frame_us;  /* monotonic time of the last frame (probe); start time before the first */
    _Atomic gboolean reanchor;     /* set when the source is lost: the probe takes a new stream_start_ns (PTS may restart) */
    _Atomic gboolean eos_seen;     /* file sources: their EOS passed the muxer sink pad (mux_eos_probe); never set for RTSP */
    GstElement *bin;     /* nvurisrcbin, owned by the pipeline */
    GstPad *mux_sinkpad; /* request pad "sink_<source_id>" of nvstreammux; we hold one ref */
    GstPad *demux_pad;   /* request pad "src_<source_id>" of nvstreamdemux (--stream-url), asked for when the pipeline is built:
                          * the demuxer hands out pads only then. It stays until the end; we hold one ref */
    /* Written by the streaming thread (probe). frame_count is atomic because the FPS timer reads
     * it while the pipeline runs; the other counters are read only after it stopped, so they
     * need no lock. */
    _Atomic guint64 frame_count;
    _Atomic gint people_now;   /* tracked persons in the latest frame (probe); meaningless while frame_count is 0 */
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
    /* Stream (--stream-url). Two parts that never touch each other's state: the encoding branch inside the
     * main pipeline (nvstreamdemux ! queue ! ... ! H.264 ! appsink; it never fails, so the encoder is never
     * restarted) and, per camera, a small publisher pipeline of its own (appsrc ! h264parse ! flvmux !
     * rtmp2sink) that is thrown away and built again when the server fails. The appsink callback (a
     * streaming thread) hands the encoded frames to the publisher under pub_lock; the rest is main thread. */
    GstElement *stream_bin;   /* the encoding branch, owned by the main pipeline; NULL without --stream-url */
    GMutex pub_lock;
    GstElement *pub_appsrc;   /* NULL while no publisher takes frames; guarded by pub_lock */
    gboolean pub_need_key;    /* guarded by pub_lock: a new publisher starts at the next key frame */
    GstClockTime pub_pts_base;   /* guarded by pub_lock: PTS of the first frame it was given */
    GstElement *pub_pipeline;
    guint pub_bus_watch;
    gboolean pub_failed;      /* the publisher reported an error; it is built again after a pause */
    gint64 pub_failed_us;
    guint64 pub_failures;
    guint64 hb_prev_frames;   /* frame_count at the previous heartbeat (main thread only) */
} SourceCtx;

typedef struct AppCtx {
    GMainLoop *loop;
    GstElement *pipeline;
    SourceCtx *sources;
    guint num_sources;
    gint exit_code;
    gboolean eos_requested;  /* --output: SIGINT/SIGTERM sent EOS so that qtmux can finalize the MP4 */
    guint eos_timer_id;      /* gives up waiting for that EOS */
    gint64 start_us;  /* monotonic time when the pipeline went to PLAYING, for the summary only */
    gint64 end_us;
    /* Event flow (ROADMAP 2.2): the probe pushes EventRecord (heap, owned by whoever pops it) and a
     * worker thread consumes them, so nothing slow ever runs in the probe. */
    gchar pipeline_run_id[EVENT_UUID_LEN + 1];  /* new on every start: track IDs restart with it */
    GAsyncQueue *event_queue;
    GThread *publisher;
    _Atomic guint64 events_published;  /* written by the worker: delivered (or printed) */
    _Atomic guint64 events_spooled;    /* written to the spool file, to be delivered later */
    _Atomic guint64 events_rejected;   /* the backend refused them for good (HTTP 400/413/422) */
    _Atomic guint64 events_lost;       /* could not even be spooled (file full or unwritable) */
    /* Delivery to the backend; backend_url == NULL means events are printed on stdout instead.
     * Used by the worker thread only, after publisher_start(). */
    CURL *curl;
    struct curl_slist *curl_headers;
    EventSpool *spool;
    uint32_t backoff_ms;       /* delay used after the last failure, 0 = backend considered up */
    gint64 retry_at_us;        /* monotonic time before which the backend is not tried */
    _Atomic gint64 shutdown_deadline_us;  /* 0 until publisher_stop(); then when to give up */
    _Atomic guint64 events_dropped;    /* written by the probe: queue full */
    /* Snapshots (ROADMAP 2.5); snap_ctx is NULL when --snapshot-dir is not given. The probe asks
     * the encoder for JPEG bytes and queues them; the publisher thread writes the files. */
    NvDsObjEncCtxHandle snap_ctx;
    _Atomic guint64 snapshots_written;     /* files the worker wrote */
    _Atomic guint64 snapshots_failed;      /* encode failed, over the byte budget, or write failed */
    _Atomic guint64 events_with_snapshot;  /* published with a file name */
    _Atomic guint64 snapshot_bytes_queued; /* JPEG bytes waiting in the queue */
    guint64 snapshot_block_us;             /* probe only: time in nvds_obj_enc_finish(), total */
    guint64 snapshot_block_max_us;
    /* Heartbeat (ROADMAP 3.6): the main loop builds it (that is where the source states live) and
     * the worker POSTs it, on a handle of its own so that it never disturbs the event connection. */
    gint64 hb_prev_us;                    /* main thread: monotonic time of the previous heartbeat */
    _Atomic gboolean heartbeat_pending;   /* one is queued or being sent; the next tick is skipped */
    CURL *hb_curl;                        /* worker only */
    gboolean hb_failing;                  /* worker only: the last one was refused (reported once) */
    _Atomic guint64 heartbeats_sent;
    _Atomic guint64 heartbeats_failed;
    /* The pieces a camera is added to or taken out of while the pipeline runs (main thread only). */
    GstElement *streammux;
    GstElement *demux;        /* NULL without --stream-url */
    gboolean live;
    /* Held by detection_probe while it works on a batch, and by the main thread while it fills or empties a
     * slot: a camera is never freed under the feet of the probe. */
    GMutex slot_lock;
} AppCtx;

static gchar **opt_cameras = NULL;
static gchar *opt_cameras_file = NULL;
static gchar *opt_config = NULL;
static gchar *opt_tracker_config = NULL;
static gchar *opt_output = NULL;
static gint opt_fps_interval = 5;
static gchar *opt_snapshot_dir = NULL;
static gint opt_snapshot_quality = SNAPSHOT_QUALITY_DEFAULT;
static gchar *opt_backend_url = NULL;
static gchar *opt_spool_file = NULL;
static gchar *opt_heartbeat_url = NULL;
static gchar *opt_stream_url = NULL;
static gint opt_stream_bitrate = STREAM_BITRATE_DEFAULT_KBPS;
static gint opt_heartbeat_interval = HEARTBEAT_INTERVAL_DEFAULT_S;
static gint opt_rtsp_timeout = RTSP_TIMEOUT_DEFAULT_S;
static gint opt_max_cameras = 0;
static gdouble opt_absence_timeout = 10.0;  /* same default as ABSENCE_TIMEOUT in src/config */

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
static GHashTable *file_settings = NULL;

static GHashTable *new_settings_table(void)
{
    return g_hash_table_new_full(g_direct_hash, g_direct_equal, NULL, g_free);
}

/* The settings a camera runs with: what the file says, else the defaults (no extra filter, the
 * --absence-timeout, AI on). `table` may hold no entry for it. */
static void settings_of(const GHashTable *table, gint camera_id, gdouble *conf, gdouble *absence, gboolean *ai)
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
 * Append "ID=URI" specs read from a key file to `specs` and the per-camera settings to `settings`. The file holds credentials, so it must
 * not be readable by group/others. Error messages never include the URI.
 */
static gboolean load_cameras_file(const gchar *path, GPtrArray *specs, GHashTable *settings)
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

/*
 * Put a camera into a free slot: its settings, its EventManager and fresh counters. `spec` is "ID=URI";
 * `settings` is the camera-id -> CameraSettings table of the cameras file (NULL for a --camera camera).
 * The slot is not active yet. Prints why (never the address) and returns FALSE if the spec is bad or
 * the camera id is already in use. Call with app->slot_lock held once the pipeline runs.
 */
static gboolean slot_fill(AppCtx *app, SourceCtx *src, const gchar *spec, gboolean from_file,
                          const GHashTable *settings)
{
    gint camera_id = 0;
    gchar *uri = NULL;
    if (!parse_camera_arg(spec, &camera_id, &uri)) {
        g_printerr("Source #%u has a bad format, expected ID=URI with ID an integer >= 0\n", src->source_id);
        return FALSE;
    }
    for (guint j = 0; j < app->num_sources; j++) {
        if (app->sources[j].uri != NULL && app->sources[j].camera_id == camera_id) {
            g_printerr("camera_id %d is declared more than once\n", camera_id);
            g_free(uri);
            return FALSE;
        }
    }

    src->camera_id = camera_id;
    src->uri = uri;
    src->from_file = from_file;
    gdouble conf, absence;
    gboolean ai;
    settings_of(settings, camera_id, &conf, &absence, &ai);
    src->conf_threshold = conf;
    src->ai_enabled = ai;
    src->absence_timeout = absence;
    src->em_timeout = absence;
    event_manager_init(&src->events, absence);
    src->track_frames = g_hash_table_new(g_direct_hash, g_direct_equal);

    gchar *scheme = g_uri_parse_scheme(src->uri);
    src->is_rtsp = (scheme != NULL && g_ascii_strcasecmp(scheme, "rtsp") == 0);
    g_free(scheme);

    src->state = SOURCE_RUNNING;
    src->lost_us = 0;
    src->reconnects = 0;
    atomic_store_explicit(&src->last_frame_us, g_get_monotonic_time(), memory_order_relaxed);
    atomic_store_explicit(&src->reanchor, FALSE, memory_order_relaxed);
    atomic_store_explicit(&src->eos_seen, FALSE, memory_order_relaxed);
    atomic_store_explicit(&src->frame_count, 0, memory_order_relaxed);
    atomic_store_explicit(&src->people_now, 0, memory_order_relaxed);
    src->fps_prev_frames = 0;
    src->hb_prev_frames = 0;
    src->object_count = 0;
    src->untracked_count = 0;
    src->stream_start_ns = 0;
    src->have_stream_start = FALSE;
    src->event_count = 0;
    src->event_errors = 0;
    src->pub_failed = FALSE;
    src->pub_failed_us = 0;
    src->pub_failures = 0;
    return TRUE;
}

/* Empty a slot (the counterpart of slot_fill). The stream branch and the mutex stay: they belong to the slot. */
static void slot_clear(SourceCtx *src)
{
    g_free(src->uri);
    src->uri = NULL;
    event_manager_destroy(&src->events);
    if (src->track_frames != NULL) {
        g_hash_table_destroy(src->track_frames);
        src->track_frames = NULL;
    }
}

/*
 * Build SourceCtx[slots] and fill the first slots from the "ID=URI" specs (--camera first, then the cameras
 * file). There are max(cameras, --max-cameras) slots. Returns FALSE (and prints why) on error.
 */
static gboolean init_sources(AppCtx *app, const GPtrArray *specs)
{
    app->num_sources = MAX(specs->len, (guint)MAX(opt_max_cameras, 0));
    app->sources = g_new0(SourceCtx, app->num_sources);
    const guint n_cli = opt_cameras != NULL ? g_strv_length(opt_cameras) : 0;   /* --camera comes first in specs */

    for (guint i = 0; i < app->num_sources; i++) {
        SourceCtx *src = &app->sources[i];
        src->app = app;
        src->source_id = i;
        g_mutex_init(&src->pub_lock);
        src->pub_pts_base = GST_CLOCK_TIME_NONE;
    }
    for (guint i = 0; i < specs->len; i++) {
        /* A camera from the cameras file may carry its own settings; every other one gets the defaults. */
        gboolean from_file = (i >= n_cli);
        if (!slot_fill(app, &app->sources[i], g_ptr_array_index(specs, i), from_file,
                       from_file ? file_settings : NULL))
            return FALSE;
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
        if (app->sources[i].demux_pad != NULL)
            gst_object_unref(app->sources[i].demux_pad);
        g_mutex_clear(&app->sources[i].pub_lock);
    }
    g_free(app->sources);
    app->sources = NULL;
    app->num_sources = 0;
}

static void print_sources(const AppCtx *app)
{
    for (guint i = 0; i < app->num_sources; i++) {
        const SourceCtx *src = &app->sources[i];
        if (src->uri == NULL)
            continue;
        gchar *scheme = g_uri_parse_scheme(src->uri);
        g_print("source_id=%u camera_id=%d scheme=%s conf_threshold=%.2f absence_timeout=%.1f ai=%s\n",
                src->source_id, src->camera_id, scheme != NULL ? scheme : "?", src->conf_threshold,
                src->absence_timeout, src->ai_enabled ? "on" : "off");
        g_free(scheme);
    }
}

static const char *source_state_name(SourceState st)
{
    switch (st) {
    case SOURCE_RUNNING: return "RUNNING";
    case SOURCE_RECONNECTING: return "RECONNECTING";
    case SOURCE_ENDED: return "ENDED";
    }
    return "RUNNING";
}

/* What to report for a source: ENDED once its EOS was seen, else the state the main thread keeps. */
static SourceState source_state(const SourceCtx *src)
{
    return atomic_load_explicit(&src->eos_seen, memory_order_acquire) ? SOURCE_ENDED : src->state;
}

static void print_stats(const AppCtx *app)
{
    guint64 total_frames = 0;
    double seconds = (double)(app->end_us - app->start_us) / G_USEC_PER_SEC;

    for (guint i = 0; i < app->num_sources; i++) {
        const SourceCtx *src = &app->sources[i];
        if (src->track_frames == NULL)
            continue;   /* a free slot */

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
        if (src->is_rtsp)
            g_print("source_id=%u camera_id=%d state=%s reconnects=%" G_GUINT64_FORMAT "\n",
                    src->source_id, src->camera_id, source_state_name(src->state), src->reconnects);
        if (src->stream_bin != NULL)
            g_print("source_id=%u camera_id=%d stream failures=%" G_GUINT64_FORMAT "\n", src->source_id,
                    src->camera_id, src->pub_failures);
        if (src->event_errors > 0)
            g_printerr("source_id=%u: %" G_GUINT64_FORMAT " frames skipped by the event manager "
                       "(out of memory)\n", src->source_id, src->event_errors);
        total_frames += src->frame_count;
    }

    g_print("events published=%" G_GUINT64_FORMAT " dropped=%" G_GUINT64_FORMAT
            " pipeline_run_id=%s\n", (guint64)app->events_published, (guint64)app->events_dropped,
            app->pipeline_run_id);
    if (opt_backend_url != NULL)
        g_print("backend spooled=%" G_GUINT64_FORMAT " rejected=%" G_GUINT64_FORMAT " lost=%"
                G_GUINT64_FORMAT "\n", (guint64)app->events_spooled, (guint64)app->events_rejected,
                (guint64)app->events_lost);
    if (opt_heartbeat_url != NULL)
        g_print("heartbeat sent=%" G_GUINT64_FORMAT " failed=%" G_GUINT64_FORMAT "\n",
                (guint64)app->heartbeats_sent, (guint64)app->heartbeats_failed);
    if (opt_snapshot_dir != NULL)
        g_print("snapshots written=%" G_GUINT64_FORMAT " failed=%" G_GUINT64_FORMAT
                " events_with_snapshot=%" G_GUINT64_FORMAT " probe_blocked_ms total=%.1f max=%.1f\n",
                (guint64)app->snapshots_written, (guint64)app->snapshots_failed,
                (guint64)app->events_with_snapshot, (double)app->snapshot_block_us / 1000.0,
                (double)app->snapshot_block_max_us / 1000.0);
    if (seconds > 0.0)
        g_print("elapsed=%.1fs total_frames=%" G_GUINT64_FORMAT " fps=%.1f (all sources together, "
                "includes startup)\n", seconds, total_frames, (double)total_frames / seconds);
}

/* The source whose bin contains the element that posted `msg`, or NULL (nvinfer, the muxer...).
 * `debug` is the debug text of the message, or NULL. A message that is handled late may come from an
 * element that the bin already took apart (it has no parent then), and only the element path in the
 * debug text, ".../GstDsNvUriSrcBin:source-<id>/...", still says where it came from. */
static SourceCtx *source_of_message(AppCtx *app, GstMessage *msg, const gchar *debug)
{
    GstObject *obj = GST_MESSAGE_SRC(msg) != NULL ? gst_object_ref(GST_MESSAGE_SRC(msg)) : NULL;
    while (obj != NULL) {
        for (guint i = 0; i < app->num_sources; i++) {
            if (app->sources[i].active && GST_OBJECT(app->sources[i].bin) == obj) {
                gst_object_unref(obj);
                return &app->sources[i];
            }
        }
        GstObject *parent = gst_object_get_parent(obj);
        gst_object_unref(obj);
        obj = parent;
    }
    for (guint i = 0; debug != NULL && i < app->num_sources; i++) {
        if (!app->sources[i].active)
            continue;
        gchar *needle = g_strdup_printf("/GstDsNvUriSrcBin:source-%u/", app->sources[i].source_id);
        gboolean found = strstr(debug, needle) != NULL;
        g_free(needle);
        if (found)
            return &app->sources[i];
    }
    return NULL;
}

/*
 * appsink callback of the encoding branch (a streaming thread): hand the encoded frame to the publisher of
 * this camera. A publisher starts at a key frame, and its time stamps start at 0, whatever the stream time
 * is when it is built. The copy shares the memory of the original; nothing is encoded twice.
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
static void pub_destroy(SourceCtx *src)
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
static gboolean pub_create(SourceCtx *src)
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
static gboolean stream_retry_tick(gpointer data)
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
 * An RTSP source lost its camera: an error on the bus, no frame for --rtsp-timeout seconds, or the
 * stream ended. The engine only notes it (state RECONNECTING, a log line, a new time anchor for the
 * events): nvurisrcbin, told to with rtsp-reconnect-interval, restarts its RTSP client by itself
 * until the camera answers. (Switching the whole bin off and on does not work: it then adds its
 * "vsrc_0" pad a second time and fails.) Only this source is affected; the others keep running
 * because nvstreammux, with live-source set, batches whatever frames it has after a timeout.
 * Called from the main thread.
 */
static void rtsp_reconnect(SourceCtx *src, const char *why)
{
    if (src->state != SOURCE_RUNNING)
        return;
    g_printerr("source_id=%u camera_id=%d: %s, reconnecting\n", src->source_id, src->camera_id, why);
    src->state = SOURCE_RECONNECTING;
    src->lost_us = g_get_monotonic_time();
    src->reconnects++;
    atomic_store_explicit(&src->reanchor, TRUE, memory_order_relaxed);
}

/* Timer (main thread): finds RTSP sources that went quiet without any error, and notes when a lost
 * source delivers frames again. */
static gboolean watchdog_tick(gpointer data)
{
    AppCtx *app = (AppCtx *)data;
    gint64 now = g_get_monotonic_time();

    for (guint i = 0; i < app->num_sources; i++) {
        SourceCtx *src = &app->sources[i];
        if (!src->active || !src->is_rtsp)
            continue;
        gint64 last = atomic_load_explicit(&src->last_frame_us, memory_order_relaxed);
        if (src->state == SOURCE_RECONNECTING) {
            if (last > src->lost_us) {
                src->state = SOURCE_RUNNING;
                g_printerr("source_id=%u camera_id=%d: reconnected\n", src->source_id,
                           src->camera_id);
            }
        } else if (opt_rtsp_timeout > 0 && now - last > (gint64)opt_rtsp_timeout * G_USEC_PER_SEC) {
            rtsp_reconnect(src, "no frames");
        }
    }
    return G_SOURCE_CONTINUE;
}

/* Pad probe on the nvstreammux sink pad of a file source: notes that the file has ended. (An RTSP
 * nvurisrcbin swallows the EOS of its stream, so this is not installed for those.) Runs on the
 * streaming thread, once per source, and only sets a flag that the main thread reads. */
static GstPadProbeReturn mux_eos_probe(GstPad *pad, GstPadProbeInfo *info, gpointer data)
{
    (void)pad;
    SourceCtx *src = (SourceCtx *)data;
    GstEvent *event = GST_PAD_PROBE_INFO_EVENT(info);

    if (event != NULL && GST_EVENT_TYPE(event) == GST_EVENT_EOS &&
        !atomic_exchange_explicit(&src->eos_seen, TRUE, memory_order_acq_rel))
        g_print("source_id=%u camera_id=%d: ended (end of file)\n", src->source_id, src->camera_id);
    return GST_PAD_PROBE_OK;
}

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

static gboolean add_source(AppCtx *app, SourceCtx *src);
static void detach_source(AppCtx *app, SourceCtx *src);

/*
 * SIGHUP: read the cameras file again and bring the running engine in line with it, without stopping the
 * pipeline:
 *  - a camera that is in the file but not running is added (to a free slot: the engine has as many as
 *    --max-cameras, or as many cameras as it started with);
 *  - a running camera that left the file, or whose address changed, is taken out (the second case is a
 *    removal plus an addition);
 *  - the settings (conf_threshold, absence_timeout, ai_enabled) of every camera that stays are updated.
 * It is all or nothing for the list: if the file does not parse, names a camera twice, takes a camera id
 * a --camera camera has, needs more slots than are free or the engine writes --output, nothing at all is
 * changed and the engine says why (the engine manager restarts it for those). Cameras given with --camera
 * are not in the file and are left alone. Messages never contain an address.
 */
static gboolean on_reload(gpointer data)
{
    AppCtx *app = (AppCtx *)data;

    if (opt_cameras_file == NULL) {
        g_print("reload: ignored, the engine was not started with --cameras-file\n");
        return G_SOURCE_CONTINUE;
    }

    GPtrArray *specs = g_ptr_array_new_with_free_func(g_free);
    GHashTable *settings = new_settings_table();
    gboolean ok = load_cameras_file(opt_cameras_file, specs, settings);
    gint *ids = g_new0(gint, specs->len + 1);
    gchar **uris = g_new0(gchar *, specs->len + 1);
    gboolean *keep = g_new0(gboolean, app->num_sources);   /* per slot: the camera stays as it is */
    gboolean *add = g_new0(gboolean, specs->len + 1);      /* per file entry: to be added */
    const char *refusal = NULL;

    for (guint i = 0; ok && i < specs->len; i++) {
        if (!parse_camera_arg(g_ptr_array_index(specs, i), &ids[i], &uris[i])) {
            ok = FALSE;
            break;
        }
        for (guint k = 0; k < i; k++)
            if (ids[k] == ids[i])
                refusal = "a camera is named twice in the cameras file";
    }

    guint adds = 0, removals = 0, free_slots = 0;
    for (guint i = 0; ok && refusal == NULL && i < specs->len; i++) {
        const SourceCtx *same_id = NULL;
        for (guint j = 0; j < app->num_sources; j++)
            if (app->sources[j].active && app->sources[j].camera_id == ids[i])
                same_id = &app->sources[j];
        if (same_id != NULL && !same_id->from_file) {
            refusal = "the file names a camera that was given with --camera";
        } else if (same_id != NULL && strcmp(same_id->uri, uris[i]) == 0) {
            keep[same_id->source_id] = TRUE;
        } else {
            add[i] = TRUE;
            adds++;
        }
    }
    for (guint j = 0; ok && refusal == NULL && j < app->num_sources; j++) {
        if (app->sources[j].uri == NULL)
            free_slots++;
        else if (app->sources[j].active && app->sources[j].from_file && !keep[j])
            removals++;
    }
    if (ok && refusal == NULL && (adds > 0 || removals > 0)) {
        if (opt_output != NULL)
            refusal = "cameras cannot be added or removed while --output is written";
        else if (adds > free_slots + removals)
            refusal = "there is no free place for the new cameras (see --max-cameras)";
    }

    if (!ok) {
        g_print("reload: the cameras file could not be used, nothing changed\n");
    } else if (refusal != NULL) {
        g_print("reload: nothing applied, %s (the engine has to be restarted for that)\n", refusal);
    } else {
        guint removed = 0, added = 0, changed = 0;

        for (guint j = 0; j < app->num_sources; j++) {
            SourceCtx *src = &app->sources[j];
            if (src->active && src->from_file && !keep[j]) {
                detach_source(app, src);
                removed++;
            }
        }
        for (guint i = 0; i < specs->len; i++) {
            if (!add[i])
                continue;
            SourceCtx *slot = NULL;   /* the free slot that has been free the longest: a frame of the previous camera may still be on its way */
            for (guint j = 0; j < app->num_sources; j++)
                if (app->sources[j].uri == NULL && (slot == NULL || app->sources[j].freed_us < slot->freed_us))
                    slot = &app->sources[j];
            if (slot == NULL)
                break;   /* cannot happen: counted above */
            g_mutex_lock(&app->slot_lock);
            gboolean filled = slot_fill(app, slot, g_ptr_array_index(specs, i), TRUE, settings);
            g_mutex_unlock(&app->slot_lock);
            if (!filled)
                continue;
            if (add_source(app, slot)) {
                added++;
                g_print("reload: camera_id=%d added in slot %u\n", slot->camera_id, slot->source_id);
            } else {
                g_printerr("reload: camera_id=%d could not be added\n", slot->camera_id);
                detach_source(app, slot);
            }
        }
        for (guint j = 0; j < app->num_sources; j++) {
            SourceCtx *src = &app->sources[j];
            if (!keep[j] || !src->active)
                continue;
            gdouble conf, absence;
            gboolean ai;
            settings_of(settings, src->camera_id, &conf, &absence, &ai);
            if (conf == src->conf_threshold && absence == src->absence_timeout && ai == src->ai_enabled)
                continue;
            src->conf_threshold = conf;
            src->absence_timeout = absence;
            src->ai_enabled = ai;
            changed++;
            g_print("reload: camera_id=%d conf_threshold=%.2f absence_timeout=%.1f ai=%s\n",
                    src->camera_id, conf, absence, ai ? "on" : "off");
        }
        g_print("reload: done, %u added, %u removed, %u with new settings\n", added, removed, changed);
    }

    for (guint i = 0; i < specs->len; i++)
        g_free(uris[i]);
    g_free(uris);
    g_free(ids);
    g_free(keep);
    g_free(add);
    g_hash_table_destroy(settings);
    g_ptr_array_free(specs, TRUE);
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

/* What travels through the queue to the publisher thread. */
typedef enum { ITEM_EVENT, ITEM_SNAPSHOT, ITEM_HEARTBEAT, ITEM_STOP } ItemKind;

typedef struct {
    ItemKind kind;
    EventRecord rec;       /* ITEM_EVENT */
    gchar *snapshot_name;  /* ITEM_SNAPSHOT: file name inside --snapshot-dir */
    guint8 *jpeg;          /* ITEM_SNAPSHOT: the encoded image */
    gsize jpeg_len;
    gchar *heartbeat_json; /* ITEM_HEARTBEAT: the body to POST */
} QueueItem;

/* Pushed after the last item to stop the worker (g_async_queue_push() refuses NULL). */
static QueueItem publisher_stop_item = { .kind = ITEM_STOP };

static void queue_item_free(QueueItem *item)
{
    g_free(item->snapshot_name);
    g_free(item->jpeg);
    g_free(item->heartbeat_json);
    g_free(item);
}

/* Probe side: a queue item holding the EventRecord of an event of source `src`. */
static QueueItem *new_event_item(const AppCtx *app, const SourceCtx *src, const PersonDetectedEvent *ev)
{
    QueueItem *item = g_new0(QueueItem, 1);
    EventRecord *rec = &item->rec;
    item->kind = ITEM_EVENT;
    gchar *id = g_uuid_string_random();
    g_strlcpy(rec->event_id, id, sizeof(rec->event_id));
    g_free(id);
    g_strlcpy(rec->pipeline_run_id, app->pipeline_run_id, sizeof(rec->pipeline_run_id));
    rec->camera_id = src->camera_id;
    rec->track_id = ev->track_id;
    rec->confidence = ev->confidence;
    rec->timestamp_ns = ev->timestamp_ns;
    memcpy(rec->bbox, ev->bbox, sizeof(rec->bbox));
    return item;
}

/* The events that started in one frame, as a range of the probe's `pending` array. */
typedef struct {
    NvDsFrameMeta *frame_meta;
    guint first, count;
    gboolean encoding;       /* an encode of this frame was queued with the encoder */
    QueueItem *snapshot;     /* its JPEG, once finish() is done; NULL = no snapshot */
} FrameGroup;

/*
 * Ask the object encoder for a JPEG of the whole frame of group->frame_meta; the events of the
 * group share it and carry the name derived from the first one. nvds_obj_enc_process() only
 * enqueues, and the encoder reads the NVMM surface of this buffer, so the caller must call
 * nvds_obj_enc_finish() before the buffer leaves the probe. `map` holds the mapping of `buf`
 * (made on first use, unmapped by the caller): its data is the NvBufSurface of the whole batch.
 *
 * The encoder is told not to save a file (saveImg = FALSE) but to attach the JPEG to the frame as
 * user meta: the library's own file writer does not check fopen() and crashes the process when the
 * directory is not writable, and writing files does not belong in the probe anyway.
 */
static void submit_snapshot(AppCtx *app, GstBuffer *buf, GstMapInfo *map, gboolean *mapped,
                            FrameGroup *group, QueueItem **items)
{
    if (!*mapped) {
        if (!gst_buffer_map(buf, map, GST_MAP_READ))
            goto failed;
        *mapped = TRUE;
    }

    char name[EVENT_SNAPSHOT_NAME_MAX];
    if (event_snapshot_name(&items[0]->rec, name, sizeof(name)) < 0)
        goto failed;

    NvDsObjEncUsrArgs args = { 0 };
    args.isFrame = TRUE;
    args.saveImg = FALSE;
    args.attachUsrMeta = TRUE;
    args.quality = opt_snapshot_quality;
    if (!nvds_obj_enc_process(app->snap_ctx, &args, (NvBufSurface *)map->data, NULL,
                              group->frame_meta))
        goto failed;

    for (guint i = 0; i < group->count; i++)
        g_strlcpy(items[i]->rec.snapshot, name, sizeof(items[i]->rec.snapshot));
    group->encoding = TRUE;
    return;

failed:
    atomic_fetch_add_explicit(&app->snapshots_failed, 1, memory_order_relaxed);
}

/* After nvds_obj_enc_finish(): copy the JPEG the encoder attached to the frame into a snapshot
 * item, or return NULL. (Whether a frame-level encode attaches its meta to the frame rather than
 * to an object was not documented; it was checked by running it.) */
static QueueItem *take_jpeg(const NvDsFrameMeta *frame_meta, const gchar *name)
{
    for (NvDsMetaList *l = frame_meta->frame_user_meta_list; l != NULL; l = l->next) {
        const NvDsUserMeta *um = (const NvDsUserMeta *)l->data;
        if (um->base_meta.meta_type != NVDS_CROP_IMAGE_META)
            continue;
        const NvDsObjEncOutParams *enc = (const NvDsObjEncOutParams *)um->user_meta_data;
        if (enc == NULL || enc->outBuffer == NULL || enc->outLen == 0)
            return NULL;
        QueueItem *item = g_new0(QueueItem, 1);
        item->kind = ITEM_SNAPSHOT;
        item->snapshot_name = g_strdup(name);
        item->jpeg = g_malloc(enc->outLen);
        memcpy(item->jpeg, enc->outBuffer, enc->outLen);
        item->jpeg_len = enc->outLen;
        return item;
    }
    return NULL;
}

/* Hand one frame's events (and their snapshot, which goes first) to the publisher. Never blocks:
 * when the worker is too far behind the events are dropped and counted, together, so that no event
 * is left pointing at a file that was never queued. */
static void enqueue_group(AppCtx *app, QueueItem **items, const FrameGroup *group)
{
    guint needed = group->count + (group->snapshot != NULL ? 1 : 0);

    if ((guint)g_async_queue_length(app->event_queue) + needed > EVENT_QUEUE_MAX) {
        atomic_fetch_add_explicit(&app->events_dropped, group->count, memory_order_relaxed);
        for (guint i = 0; i < group->count; i++)
            queue_item_free(items[i]);
        if (group->snapshot != NULL)
            queue_item_free(group->snapshot);
        return;
    }
    if (group->snapshot != NULL) {
        atomic_fetch_add_explicit(&app->snapshot_bytes_queued, group->snapshot->jpeg_len,
                                  memory_order_relaxed);
        g_async_queue_push(app->event_queue, group->snapshot);
    }
    for (guint i = 0; i < group->count; i++)
        g_async_queue_push(app->event_queue, items[i]);
}

/* Write a snapshot file: temp file + rename inside g_file_set_contents(), so a reader (or the
 * cleaner) never sees half an image. Returns FALSE and logs when it cannot. */
static gboolean write_snapshot(const QueueItem *item)
{
    gchar *path = g_build_filename(opt_snapshot_dir, item->snapshot_name, NULL);
    GError *error = NULL;
    gboolean ok = g_file_set_contents(path, (const gchar *)item->jpeg, (gssize)item->jpeg_len, &error);
    if (!ok) {
        g_printerr("Could not write a snapshot: %s\n", error->message);
        g_error_free(error);
    }
    g_free(path);
    return ok;
}

typedef enum { SEND_OK, SEND_RETRY, SEND_REJECTED } SendResult;

/* libcurl would write the response body to stdout by default; we do not need it. */
static size_t discard_body(char *data, size_t size, size_t nmemb, void *user)
{
    (void)data; (void)user;
    return size * nmemb;
}

/* The backend is tried only when the last failure is far enough in the past, and not once the
 * grace period of a shutdown is over. */
static gboolean backend_ready(const AppCtx *app)
{
    gint64 now = g_get_monotonic_time();
    gint64 deadline = atomic_load_explicit(&app->shutdown_deadline_us, memory_order_relaxed);
    return now >= app->retry_at_us && (deadline == 0 || now < deadline);
}

static void backend_failed(AppCtx *app)
{
    app->backoff_ms = backoff_next_ms(app->backoff_ms);
    app->retry_at_us = g_get_monotonic_time() + (gint64)app->backoff_ms * 1000;
}

static void backend_ok(AppCtx *app)
{
    if (app->backoff_ms != 0)
        g_printerr("Backend reachable again\n");
    app->backoff_ms = 0;
    app->retry_at_us = 0;
}

/* POST one event. Errors are logged without the URL (it may carry a secret). Only the answers that
 * say "this event is bad" (400/413/422) are final; anything else, including 404/401/403 from a
 * misconfigured backend, is retried so that no event is lost to a setup mistake. 409 means the
 * backend already has this event_id, which is what we wanted. */
static SendResult http_post(AppCtx *app, const char *json)
{
    curl_easy_setopt(app->curl, CURLOPT_POSTFIELDS, json);
    curl_easy_setopt(app->curl, CURLOPT_POSTFIELDSIZE, (long)strlen(json));
    CURLcode rc = curl_easy_perform(app->curl);
    if (rc != CURLE_OK) {
        if (app->backoff_ms == 0)
            g_printerr("Backend unreachable: %s; events go to the spool file\n",
                       curl_easy_strerror(rc));
        return SEND_RETRY;
    }

    long status = 0;
    curl_easy_getinfo(app->curl, CURLINFO_RESPONSE_CODE, &status);
    if ((status >= 200 && status < 300) || status == 409)
        return SEND_OK;
    if (status == 400 || status == 413 || status == 422) {
        if (atomic_load_explicit(&app->events_rejected, memory_order_relaxed) == 0)
            g_printerr("Backend rejected an event (HTTP %ld); further rejections are only counted\n",
                       status);
        return SEND_REJECTED;
    }
    if (app->backoff_ms == 0)
        g_printerr("Backend answered HTTP %ld; events go to the spool file\n", status);
    return SEND_RETRY;
}

/* Send what the spool holds, oldest first, at most `max` events (0 = until it is empty or the
 * backend fails). */
static void drain_spool(AppCtx *app, guint max)
{
    char json[EVENT_JSON_MAX];
    for (guint n = 0; (max == 0 || n < max) && backend_ready(app); n++) {
        int got = event_spool_peek(app->spool, json, sizeof(json));
        if (got < 0) {
            g_printerr("Could not read the spool file\n");
            backend_failed(app);
            return;
        }
        if (got == 0)
            return;
        SendResult r = http_post(app, json);
        if (r == SEND_RETRY) {
            backend_failed(app);
            return;
        }
        backend_ok(app);
        event_spool_pop(app->spool);
        atomic_fetch_add_explicit(r == SEND_OK ? &app->events_published : &app->events_rejected,
                                  1, memory_order_relaxed);
    }
}

static void spool_event(AppCtx *app, const char *json)
{
    int rc = event_spool_append(app->spool, json);
    if (rc == 0) {
        atomic_fetch_add_explicit(&app->events_spooled, 1, memory_order_relaxed);
        return;
    }
    g_printerr(rc == -2 ? "Spool file is full, an event is lost\n"
                        : "Could not write to the spool file, an event is lost\n");
    atomic_fetch_add_explicit(&app->events_lost, 1, memory_order_relaxed);
}

/* One serialised event: to the backend if it is up and nothing older is waiting, otherwise behind
 * what is already in the spool, so that events keep their order. */
static void deliver_event(AppCtx *app, const char *json)
{
    if (app->curl == NULL) {
        g_print("event %s\n", json);
        atomic_fetch_add_explicit(&app->events_published, 1, memory_order_relaxed);
        return;
    }

    drain_spool(app, SPOOL_DRAIN_BATCH);
    if (event_spool_empty(app->spool) && backend_ready(app)) {
        SendResult r = http_post(app, json);
        if (r != SEND_RETRY) {
            backend_ok(app);
            atomic_fetch_add_explicit(r == SEND_OK ? &app->events_published : &app->events_rejected,
                                      1, memory_order_relaxed);
            return;
        }
        backend_failed(app);
    }
    spool_event(app, json);
}

/* POST one heartbeat. Unlike an event it is never spooled or retried (a late one is worthless; the
 * next comes in a few seconds) and it does not touch the backoff of the event path: the backend can
 * answer heartbeats while its database refuses events. Only the first failure after a success is
 * logged. */
static void send_heartbeat(AppCtx *app, const char *json)
{
    gint64 deadline = atomic_load_explicit(&app->shutdown_deadline_us, memory_order_relaxed);
    if (app->hb_curl == NULL || deadline != 0)   /* shutting down: the engine is going away anyway */
        return;

    curl_easy_setopt(app->hb_curl, CURLOPT_POSTFIELDS, json);
    curl_easy_setopt(app->hb_curl, CURLOPT_POSTFIELDSIZE, (long)strlen(json));
    CURLcode rc = curl_easy_perform(app->hb_curl);
    long status = 0;
    if (rc == CURLE_OK)
        curl_easy_getinfo(app->hb_curl, CURLINFO_RESPONSE_CODE, &status);

    if (rc == CURLE_OK && status >= 200 && status < 300) {
        atomic_fetch_add_explicit(&app->heartbeats_sent, 1, memory_order_relaxed);
        if (app->hb_failing)
            g_printerr("Heartbeat accepted again\n");
        app->hb_failing = FALSE;
        return;
    }
    atomic_fetch_add_explicit(&app->heartbeats_failed, 1, memory_order_relaxed);
    if (!app->hb_failing) {
        if (rc != CURLE_OK)
            g_printerr("Heartbeat not delivered: %s\n", curl_easy_strerror(rc));
        else
            g_printerr("Heartbeat refused (HTTP %ld)\n", status);
    }
    app->hb_failing = TRUE;
}

/* Worker thread: takes items off the queue until it gets publisher_stop_item. A snapshot item
 * always comes before the events that name it, so the file is on disk before the event goes out.
 * The probe never waits for any of this: a slow or dead backend only slows this thread down. */
static gpointer publisher_thread(gpointer data)
{
    AppCtx *app = (AppCtx *)data;
    char json[EVENT_JSON_MAX];
    gchar *failed_snapshot = NULL;   /* name of the last snapshot that could not be written */

    for (;;) {
        QueueItem *item = g_async_queue_timeout_pop(app->event_queue, WORKER_IDLE_US);
        if (item == NULL) {   /* nothing arrived: a good moment to empty the spool */
            if (app->curl != NULL)
                drain_spool(app, SPOOL_DRAIN_BATCH);
            continue;
        }
        if (item->kind == ITEM_STOP)
            break;

        if (item->kind == ITEM_HEARTBEAT) {
            send_heartbeat(app, item->heartbeat_json);
            atomic_store_explicit(&app->heartbeat_pending, FALSE, memory_order_release);
        } else if (item->kind == ITEM_SNAPSHOT) {
            atomic_fetch_sub_explicit(&app->snapshot_bytes_queued, item->jpeg_len,
                                      memory_order_relaxed);
            if (write_snapshot(item)) {
                atomic_fetch_add_explicit(&app->snapshots_written, 1, memory_order_relaxed);
            } else {
                atomic_fetch_add_explicit(&app->snapshots_failed, 1, memory_order_relaxed);
                g_free(failed_snapshot);
                failed_snapshot = g_strdup(item->snapshot_name);
            }
        } else {
            EventRecord *rec = &item->rec;
            if (failed_snapshot != NULL && strcmp(rec->snapshot, failed_snapshot) == 0)
                rec->snapshot[0] = '\0';   /* never name a file that is not there */
            if (rec->snapshot[0] != '\0')
                atomic_fetch_add_explicit(&app->events_with_snapshot, 1, memory_order_relaxed);
            if (event_record_to_json(rec, json, sizeof(json)) > 0)
                deliver_event(app, json);
            else
                g_printerr("Could not serialise an event\n");
        }
        queue_item_free(item);
    }

    /* Shutting down: one last try to empty the spool, within the grace period. */
    if (app->curl != NULL)
        drain_spool(app, 0);
    g_free(failed_snapshot);
    return NULL;
}

/* A POST of JSON to `url` with the shared headers; the same settings for events and heartbeats. */
static void configure_handle(const AppCtx *app, CURL *handle, const char *url, long connect_ms,
                             long total_ms)
{
    curl_easy_setopt(handle, CURLOPT_URL, url);
    curl_easy_setopt(handle, CURLOPT_POST, 1L);
    curl_easy_setopt(handle, CURLOPT_HTTPHEADER, app->curl_headers);
    curl_easy_setopt(handle, CURLOPT_CONNECTTIMEOUT_MS, connect_ms);
    curl_easy_setopt(handle, CURLOPT_TIMEOUT_MS, total_ms);
    curl_easy_setopt(handle, CURLOPT_NOSIGNAL, 1L);          /* we call it from a thread */
    curl_easy_setopt(handle, CURLOPT_FOLLOWLOCATION, 0L);
    curl_easy_setopt(handle, CURLOPT_WRITEFUNCTION, discard_body);
}

static void delivery_free(AppCtx *app);

/* Everything the worker needs for the HTTP side: libcurl handle (kept alive between events, so the
 * connection is reused) and the spool. Returns FALSE with a message if it cannot be set up. */
static gboolean delivery_init(AppCtx *app)
{
    curl_global_init(CURL_GLOBAL_DEFAULT);   /* not thread safe: before the worker exists */
    app->spool = event_spool_open(opt_spool_file, SPOOL_MAX_BYTES);
    if (app->spool == NULL) {
        g_printerr("Cannot open the spool file (--spool-file)\n");
        return FALSE;
    }
    app->curl = curl_easy_init();
    if (app->curl == NULL) {
        g_printerr("Could not initialise libcurl\n");
        event_spool_close(app->spool);
        app->spool = NULL;
        return FALSE;
    }
    app->curl_headers = curl_slist_append(NULL, "Content-Type: application/json");
    /* The shared secret of the backend (its internal endpoints) comes from the environment, not argv, where
     * `ps` would show it. It is never printed. */
    const char *token = g_getenv(TOKEN_ENV);
    if (token != NULL && token[0] != '\0') {
        gchar *header = g_strdup_printf("X-Internal-Token: %s", token);
        app->curl_headers = curl_slist_append(app->curl_headers, header);   /* curl copies it */
        g_free(header);
    }
    configure_handle(app, app->curl, opt_backend_url, HTTP_CONNECT_TIMEOUT_MS, HTTP_TIMEOUT_MS);
    if (opt_heartbeat_url != NULL) {
        app->hb_curl = curl_easy_init();
        if (app->hb_curl == NULL) {
            g_printerr("Could not initialise libcurl\n");
            delivery_free(app);
            return FALSE;
        }
        configure_handle(app, app->hb_curl, opt_heartbeat_url, HEARTBEAT_CONNECT_TIMEOUT_MS,
                         HEARTBEAT_TIMEOUT_MS);
    }
    return TRUE;
}

static void delivery_free(AppCtx *app)
{
    if (app->curl != NULL)
        curl_easy_cleanup(app->curl);
    app->curl = NULL;
    if (app->hb_curl != NULL)
        curl_easy_cleanup(app->hb_curl);
    app->hb_curl = NULL;
    curl_slist_free_all(app->curl_headers);
    app->curl_headers = NULL;
    event_spool_close(app->spool);
    app->spool = NULL;
}

static gboolean publisher_start(AppCtx *app)
{
    gchar *run_id = g_uuid_string_random();
    g_strlcpy(app->pipeline_run_id, run_id, sizeof(app->pipeline_run_id));
    g_free(run_id);

    if (opt_backend_url != NULL && !delivery_init(app))
        return FALSE;

    app->event_queue = g_async_queue_new();
    app->publisher = g_thread_try_new("publisher", publisher_thread, app, NULL);
    if (app->publisher == NULL) {
        g_async_queue_unref(app->event_queue);
        app->event_queue = NULL;
        delivery_free(app);
        return FALSE;
    }
    return TRUE;
}

/* Let the worker finish what is queued (for at most SHUTDOWN_GRACE_US when the backend is slow or
 * down; the rest goes to the spool), then stop it. Call after the pipeline has stopped, so the
 * probe pushes nothing more. Safe to call twice. */
static void publisher_stop(AppCtx *app)
{
    if (app->publisher == NULL)
        return;
    atomic_store_explicit(&app->shutdown_deadline_us,
                          g_get_monotonic_time() + SHUTDOWN_GRACE_US, memory_order_relaxed);
    g_async_queue_push(app->event_queue, &publisher_stop_item);
    g_thread_join(app->publisher);
    app->publisher = NULL;
    delivery_free(app);
}

/*
 * Pad probe on the src pad of nvinfer, i.e. before the tracker: applies the settings of each camera
 * to what the detector found. A camera with AI off loses every object; one with a confidence
 * threshold loses the objects below it. The tracker, the events, the people count and the picture
 * (the boxes of the OSD) then all see the same, smaller set. Installed only when some camera has
 * such a setting, and does nothing for the cameras that do not.
 */
static GstPadProbeReturn settings_probe(GstPad *pad, GstPadProbeInfo *info, gpointer user_data)
{
    (void)pad;
    const AppCtx *app = (const AppCtx *)user_data;

    GstBuffer *buf = GST_PAD_PROBE_INFO_BUFFER(info);
    NvDsBatchMeta *batch_meta = (buf != NULL) ? gst_buffer_get_nvds_batch_meta(buf) : NULL;
    if (batch_meta == NULL)
        return GST_PAD_PROBE_OK;

    for (NvDsMetaList *l_frame = batch_meta->frame_meta_list; l_frame != NULL; l_frame = l_frame->next) {
        NvDsFrameMeta *frame_meta = (NvDsFrameMeta *)l_frame->data;
        if (frame_meta->source_id >= app->num_sources)
            continue;
        const SourceCtx *src = &app->sources[frame_meta->source_id];
        const gboolean ai = src->ai_enabled;
        const gdouble min_conf = src->conf_threshold;
        if (ai && min_conf <= 0.0)
            continue;

        NvDsMetaList *l_obj = frame_meta->obj_meta_list;
        while (l_obj != NULL) {
            NvDsMetaList *next = l_obj->next;   /* removing the object frees this list node */
            NvDsObjectMeta *obj = (NvDsObjectMeta *)l_obj->data;
            if (!ai || obj->confidence < min_conf)
                nvds_remove_obj_meta_from_frame(frame_meta, obj);
            l_obj = next;
        }
    }
    return GST_PAD_PROBE_OK;
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

    /* A camera taken out by the main thread is not freed while this runs. */
    g_mutex_lock(&app->slot_lock);

    /* Events of this batch wait here, grouped by frame, until the snapshots are encoded. Created on
     * the first event only: most batches have none. */
    GPtrArray *pending = NULL;
    GArray *groups = NULL;
    GstMapInfo map = GST_MAP_INFO_INIT;
    gboolean mapped = FALSE;
    gboolean encode_queued = FALSE;

    for (NvDsMetaList *l_frame = batch_meta->frame_meta_list; l_frame != NULL;
         l_frame = l_frame->next) {
        NvDsFrameMeta *frame_meta = (NvDsFrameMeta *)l_frame->data;
        if (frame_meta->source_id >= app->num_sources)
            continue;
        SourceCtx *src = &app->sources[frame_meta->source_id];
        if (!src->active)
            continue;   /* a frame of a camera that was just taken out */

        guint total = 0, untracked = 0;
        size_t n = extract_person_detections(frame_meta, dets, G_N_ELEMENTS(dets), &total,
                                             &untracked);
        atomic_fetch_add_explicit(&src->frame_count, 1, memory_order_relaxed);
        atomic_store_explicit(&src->people_now, (gint)n, memory_order_relaxed);
        atomic_store_explicit(&src->last_frame_us, g_get_monotonic_time(), memory_order_relaxed);
        src->object_count += total;
        src->untracked_count += untracked;

        for (size_t i = 0; i < n; i++) {
            gpointer key = GSIZE_TO_POINTER((gsize)dets[i].track_id);
            gsize seen = GPOINTER_TO_SIZE(g_hash_table_lookup(src->track_frames, key));
            g_hash_table_insert(src->track_frames, key, GSIZE_TO_POINTER(seen + 1));
        }

        /* Event clock = stream_start + PTS of the frame, never the wall clock at processing time,
         * so a file read faster than real time behaves like the same stream played live. Called
         * for every frame, also with n == 0: that is how absent tracks time out. After a reconnect
         * the PTS may start again from 0, so the anchor is taken anew from the wall clock. */
        if (!src->have_stream_start || atomic_exchange(&src->reanchor, FALSE)) {
            src->stream_start_ns = g_get_real_time() * 1000 - (gint64)frame_meta->buf_pts;
            src->have_stream_start = TRUE;
        }
        /* A SIGHUP may have changed the timeout; only this thread touches the EventManager, so it is
         * handed over here. */
        const gdouble timeout = src->absence_timeout;
        if (timeout != src->em_timeout) {
            event_manager_set_absence_timeout(&src->events, timeout);
            src->em_timeout = timeout;
        }
        PersonDetectedEvent events[MAX_DETECTIONS_PER_FRAME];
        int emitted = event_manager_process(&src->events, dets, n,
                                            src->stream_start_ns + (gint64)frame_meta->buf_pts,
                                            events, G_N_ELEMENTS(events));
        if (emitted < 0)
            src->event_errors++;
        else
            src->event_count += (guint64)emitted;
        if (emitted > 0) {
            if (pending == NULL) {
                pending = g_ptr_array_new();
                groups = g_array_new(FALSE, TRUE, sizeof(FrameGroup));
            }
            FrameGroup group = { .frame_meta = frame_meta, .first = pending->len,
                                 .count = (guint)emitted };
            for (int i = 0; i < emitted; i++)
                g_ptr_array_add(pending, new_event_item(app, src, &events[i]));
            if (app->snap_ctx != NULL) {
                submit_snapshot(app, buf, &map, &mapped, &group,
                                (QueueItem **)pending->pdata + group.first);
                encode_queued |= group.encoding;
            }
            g_array_append_val(groups, group);
        }
    }

    if (pending != NULL) {
        if (encode_queued) {
            gint64 t0 = g_get_monotonic_time();
            nvds_obj_enc_finish(app->snap_ctx);
            guint64 waited = (guint64)(g_get_monotonic_time() - t0);
            app->snapshot_block_us += waited;
            if (waited > app->snapshot_block_max_us)
                app->snapshot_block_max_us = waited;
        }
        for (guint g = 0; g < groups->len; g++) {
            FrameGroup *group = &g_array_index(groups, FrameGroup, g);
            QueueItem **items = (QueueItem **)pending->pdata + group->first;
            if (group->encoding) {
                if ((guint64)atomic_load_explicit(&app->snapshot_bytes_queued, memory_order_relaxed) <
                    SNAPSHOT_QUEUE_MAX_BYTES)
                    group->snapshot = take_jpeg(group->frame_meta, items[0]->rec.snapshot);
                if (group->snapshot == NULL) {   /* encode gave nothing, or the queue is full of JPEGs */
                    atomic_fetch_add_explicit(&app->snapshots_failed, 1, memory_order_relaxed);
                    for (guint i = 0; i < group->count; i++)
                        items[i]->rec.snapshot[0] = '\0';
                }
            }
            enqueue_group(app, items, group);
        }
        g_ptr_array_free(pending, TRUE);
        g_array_free(groups, TRUE);
    }
    if (mapped)
        gst_buffer_unmap(buf, &map);
    g_mutex_unlock(&app->slot_lock);
    return GST_PAD_PROBE_OK;
}

/* Timer on the main loop: print the frame rate of every source over the last interval. */
static gboolean report_fps(gpointer data)
{
    AppCtx *app = (AppCtx *)data;

    for (guint i = 0; i < app->num_sources; i++) {
        SourceCtx *src = &app->sources[i];
        if (!src->active)
            continue;
        guint64 now = atomic_load_explicit(&src->frame_count, memory_order_relaxed);
        g_print("fps source_id=%u camera_id=%d %.1f %s\n", src->source_id, src->camera_id,
                (double)(now - src->fps_prev_frames) / (double)opt_fps_interval,
                source_state_name(source_state(src)));
        src->fps_prev_frames = now;
    }
    return G_SOURCE_CONTINUE;
}

/* Timer on the main loop (ROADMAP 3.6): the state of every camera, handed to the worker to POST.
 * Everything is read here, on the main thread, because the source states live here. A tick is
 * skipped while the previous heartbeat is still waiting, so a slow backend never gets a pile of
 * stale ones (the frame rate of the next one then covers the longer span). */
static gboolean heartbeat_tick(gpointer data)
{
    AppCtx *app = (AppCtx *)data;

    if (atomic_load_explicit(&app->heartbeat_pending, memory_order_acquire))
        return G_SOURCE_CONTINUE;

    gint64 now = g_get_monotonic_time();
    double seconds = (double)(now - app->hb_prev_us) / G_USEC_PER_SEC;
    HeartbeatCamera *cameras = g_new0(HeartbeatCamera, app->num_sources);
    guint n = 0;   /* the cameras that are in the pipeline now */

    for (guint i = 0; i < app->num_sources; i++) {
        SourceCtx *src = &app->sources[i];
        if (!src->active)
            continue;
        HeartbeatCamera *c = &cameras[n++];
        guint64 frames = atomic_load_explicit(&src->frame_count, memory_order_relaxed);
        gint64 last = atomic_load_explicit(&src->last_frame_us, memory_order_relaxed);
        c->camera_id = src->camera_id;
        g_strlcpy(c->state, source_state_name(source_state(src)), sizeof(c->state));
        c->fps = seconds > 0.0 ? (double)(frames - src->hb_prev_frames) / seconds : 0.0;
        c->last_frame_age = (double)(now - last) / G_USEC_PER_SEC;
        gint people = atomic_load_explicit(&src->people_now, memory_order_relaxed);
        /* With AI off nobody is looked for, so "0 people" would be a claim the engine cannot make. */
        c->people = (src->ai_enabled && frames > 0 && now - last <= PEOPLE_FRESH_US) ? people : -1;
        src->hb_prev_frames = frames;
    }
    app->hb_prev_us = now;

    size_t cap = heartbeat_json_capacity(n);
    QueueItem *item = g_new0(QueueItem, 1);
    item->kind = ITEM_HEARTBEAT;
    item->heartbeat_json = g_malloc(cap);
    if (heartbeat_to_json(app->pipeline_run_id, cameras, n, item->heartbeat_json, cap) < 0) {
        queue_item_free(item);   /* cannot happen: cap is sized for it */
    } else {
        atomic_store_explicit(&app->heartbeat_pending, TRUE, memory_order_release);
        g_async_queue_push(app->event_queue, item);
    }
    g_free(cameras);
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
 * was requested for this source. Audio (and any other non-video pad) is ignored. Called again
 * if the bin makes a new pad: the old link is dropped first. */
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

    GstPad *old_peer = gst_pad_get_peer(src->mux_sinkpad);
    if (old_peer != NULL) {
        gst_pad_unlink(old_peer, src->mux_sinkpad);
        gst_object_unref(old_peer);
    }
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
/*
 * The encoding branch of one camera (ROADMAP 4.1), fed by the "src_<source_id>" pad of nvstreamdemux:
 *   queue ! nvvideoconvert ! nvdsosd ! nvvideoconvert ! I420 ! nvv4l2h264enc ! h264parse ! appsink
 * nvdsosd draws the boxes and track IDs from the metadata the demux keeps with each frame. The appsink
 * only passes the frames on (stream_new_sample), so nothing the server does can stop this branch or
 * the pipeline: the publisher (RTMP) is a pipeline of its own, see pub_create(). The queue drops its
 * oldest frames for live sources and the appsink does not wait for the clock; with files the queue
 * blocks and the appsink waits for the clock, which paces a file to real time for the viewer.
 */
static gboolean build_stream_branch(GstElement *pipeline, GstElement *demux, SourceCtx *src, gboolean live)
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

/*
 * Put the source bin of a camera into the pipeline: nvurisrcbin, and the muxer pad "sink_<source_id>" it is
 * linked to when it has found its video (on_source_pad_added). The bin starts with the pipeline, or with
 * add_source() when the pipeline already runs. Returns FALSE (after printing why) on error; detach_source()
 * undoes whatever was done.
 */
static gboolean attach_source(AppCtx *app, GstElement *pipeline, SourceCtx *src)
{
    gchar *bin_name = g_strdup_printf("source-%u", src->source_id);
    src->bin = make_element(pipeline, "nvurisrcbin", bin_name);
    g_free(bin_name);
    if (src->bin == NULL)
        return FALSE;
    g_object_set(src->bin, "uri", src->uri, NULL);
    if (src->is_rtsp)
        g_object_set(src->bin, "rtsp-reconnect-interval", (guint)opt_rtsp_timeout,
                     "rtsp-reconnect-attempts", -1, NULL);

    /* Request "sink_<source_id>" so that source_id == the muxer pad index == the
     * frame_meta->source_id seen in the probe. */
    gchar *pad_name = g_strdup_printf("sink_%u", src->source_id);
    src->mux_sinkpad = gst_element_request_pad_simple(app->streammux, pad_name);
    if (src->mux_sinkpad == NULL)
        g_printerr("Could not request pad '%s' from nvstreammux\n", pad_name);
    g_free(pad_name);
    if (src->mux_sinkpad == NULL)
        return FALSE;
    if (!src->is_rtsp)
        gst_pad_add_probe(src->mux_sinkpad, GST_PAD_PROBE_TYPE_EVENT_DOWNSTREAM, mux_eos_probe, src, NULL);

    g_signal_connect(src->bin, "pad-added", G_CALLBACK(on_source_pad_added), src);
    atomic_store(&src->active, TRUE);
    return TRUE;
}

/*
 * The encoding branch of a slot, once. It stays in the pipeline when the camera goes: nvstreamdemux cannot
 * give the same pad out twice, so the next camera of the slot uses the branch that is there (its encoder idles
 * meanwhile). Returns TRUE and sets *built when it had to be made.
 */
static gboolean attach_branch(AppCtx *app, GstElement *pipeline, SourceCtx *src, gboolean *built)
{
    *built = FALSE;
    if (app->demux == NULL || src->stream_bin != NULL)
        return TRUE;
    if (!build_stream_branch(pipeline, app->demux, src, app->live))
        return FALSE;
    *built = TRUE;
    return TRUE;
}

/*
 * Add a camera to the running pipeline (main thread). The slot has been filled with slot_fill(). The stream
 * branch is brought up first, so that the first frames have somewhere to go, then the source.
 */
static gboolean add_source(AppCtx *app, SourceCtx *src)
{
    gboolean built = FALSE;
    if (!attach_branch(app, app->pipeline, src, &built))
        return FALSE;
    if (built && !gst_element_sync_state_with_parent(src->stream_bin))
        return FALSE;
    if (!attach_source(app, app->pipeline, src))
        return FALSE;
    if (!gst_element_sync_state_with_parent(src->bin)) {
        g_printerr("Could not start the source of camera %d\n", src->camera_id);
        return FALSE;
    }
    atomic_store_explicit(&src->last_frame_us, g_get_monotonic_time(), memory_order_relaxed);
    if (opt_stream_url != NULL && !pub_create(src)) {   /* tried again by the timer */
        src->pub_failed = TRUE;
        src->pub_failed_us = g_get_monotonic_time();
    }
    return TRUE;
}

/*
 * Take a camera out of the running pipeline and free its slot (main thread). Safe on a camera that is only
 * partly added. The muxer pad is released the way NVIDIA's runtime add/delete sample does: the source is
 * stopped, the pad gets a flush-stop, then it is released. The stream branch stays (see attach_branch).
 */
static void detach_source(AppCtx *app, SourceCtx *src)
{
    atomic_store(&src->active, FALSE);   /* the probes and timers leave it alone from now on */
    if (opt_stream_url != NULL) {
        pub_destroy(src);
        src->pub_failed = FALSE;
    }
    if (src->bin != NULL)
        gst_element_set_state(src->bin, GST_STATE_NULL);
    if (src->mux_sinkpad != NULL) {
        GstPad *peer = gst_pad_get_peer(src->mux_sinkpad);
        if (peer != NULL) {
            gst_pad_unlink(peer, src->mux_sinkpad);
            gst_object_unref(peer);
        }
        gst_pad_send_event(src->mux_sinkpad, gst_event_new_flush_stop(FALSE));
        gst_element_release_request_pad(app->streammux, src->mux_sinkpad);
        gst_object_unref(src->mux_sinkpad);
        src->mux_sinkpad = NULL;
    }
    if (src->bin != NULL) {
        gst_bin_remove(GST_BIN(app->pipeline), src->bin);
        src->bin = NULL;
    }
    g_print("source_id=%u camera_id=%d: removed (frames=%" G_GUINT64_FORMAT " events=%" G_GUINT64_FORMAT ")\n",
            src->source_id, src->camera_id, (guint64)src->frame_count, src->event_count);
    g_mutex_lock(&app->slot_lock);   /* not while the probe is working on a batch */
    slot_clear(src);
    g_mutex_unlock(&app->slot_lock);
    src->freed_us = g_get_monotonic_time();
}

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
    gst_pad_add_probe(tracker_src, GST_PAD_PROBE_TYPE_BUFFER, detection_probe, app, NULL);
    gst_object_unref(tracker_src);

    /* Always attached, because a SIGHUP can turn a filter on later; with the default settings it only
     * looks at each frame's two settings and moves on. */
    GstPad *pgie_src = gst_element_get_static_pad(pgie, "src");
    if (pgie_src == NULL) {
        g_printerr("Could not get the src pad of nvinfer\n");
        goto fail;
    }
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

int main(int argc, char *argv[])
{
    AppCtx app = { 0 };
    GError *error = NULL;
    g_mutex_init(&app.slot_lock);

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
    file_settings = new_settings_table();
    if (opt_cameras_file != NULL && !load_cameras_file(opt_cameras_file, specs, file_settings)) {
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

    if (opt_max_cameras < 0 || opt_max_cameras > MAX_CAMERAS_LIMIT) {
        g_printerr("--max-cameras must be between 0 and %d\n", MAX_CAMERAS_LIMIT);
        g_ptr_array_free(specs, TRUE);
        return EXIT_BAD_ARGS;
    }
    if (opt_max_cameras > 0 && opt_output != NULL) {
        g_printerr("--max-cameras cannot be combined with --output (the tiled video has a fixed layout)\n");
        g_ptr_array_free(specs, TRUE);
        return EXIT_BAD_ARGS;
    }

    if ((opt_backend_url == NULL) != (opt_spool_file == NULL)) {
        g_printerr("--backend-url and --spool-file go together: without a spool file events would "
                   "be lost while the backend is down\n");
        g_ptr_array_free(specs, TRUE);
        return EXIT_BAD_ARGS;
    }
    if (opt_backend_url != NULL && !g_str_has_prefix(opt_backend_url, "http://") &&
        !g_str_has_prefix(opt_backend_url, "https://")) {
        g_printerr("--backend-url must start with http:// or https://\n");
        g_ptr_array_free(specs, TRUE);
        return EXIT_BAD_ARGS;
    }

    if (opt_heartbeat_url != NULL && opt_backend_url == NULL) {
        g_printerr("--heartbeat-url needs --backend-url (the heartbeat goes to the same backend)\n");
        g_ptr_array_free(specs, TRUE);
        return EXIT_BAD_ARGS;
    }
    if (opt_heartbeat_url != NULL && !g_str_has_prefix(opt_heartbeat_url, "http://") &&
        !g_str_has_prefix(opt_heartbeat_url, "https://")) {
        g_printerr("--heartbeat-url must start with http:// or https://\n");
        g_ptr_array_free(specs, TRUE);
        return EXIT_BAD_ARGS;
    }
    if (opt_heartbeat_interval < 1 || opt_heartbeat_interval > 3600) {
        g_printerr("--heartbeat-interval must be between 1 and 3600 seconds\n");
        g_ptr_array_free(specs, TRUE);
        return EXIT_BAD_ARGS;
    }
    if (opt_stream_url != NULL && opt_output != NULL) {
        g_printerr("--stream-url and --output cannot be used together (one tiles the cameras, the other "
                   "keeps them apart)\n");
        g_ptr_array_free(specs, TRUE);
        return EXIT_BAD_ARGS;
    }
    if (opt_stream_url != NULL && !g_str_has_prefix(opt_stream_url, "rtmp://") &&
        !g_str_has_prefix(opt_stream_url, "rtmps://")) {
        g_printerr("--stream-url must start with rtmp:// or rtmps://\n");
        g_ptr_array_free(specs, TRUE);
        return EXIT_BAD_ARGS;
    }
    if (opt_stream_url != NULL && opt_stream_url[strlen(opt_stream_url) - 1] == '/')
        opt_stream_url[strlen(opt_stream_url) - 1] = '\0';   /* BASE/cam<ID>, one slash */
    if (opt_stream_bitrate < 100 || opt_stream_bitrate > 50000) {
        g_printerr("--stream-bitrate must be between 100 and 50000 kbit/s\n");
        g_ptr_array_free(specs, TRUE);
        return EXIT_BAD_ARGS;
    }
    if (opt_rtsp_timeout < 0 || opt_rtsp_timeout > 3600) {
        g_printerr("--rtsp-timeout must be between 0 and 3600 seconds\n");
        g_ptr_array_free(specs, TRUE);
        return EXIT_BAD_ARGS;
    }
    if (opt_snapshot_quality < 1 || opt_snapshot_quality > 100) {
        g_printerr("--snapshot-quality must be between 1 and 100\n");
        g_ptr_array_free(specs, TRUE);
        return EXIT_BAD_ARGS;
    }
    if (opt_snapshot_dir != NULL) {
        if (strlen(opt_snapshot_dir) > SNAPSHOT_DIR_MAX ||
            g_mkdir_with_parents(opt_snapshot_dir, 0755) != 0) {
            g_printerr("Cannot use --snapshot-dir (too long, or it cannot be created)\n");
            g_ptr_array_free(specs, TRUE);
            return EXIT_BAD_ARGS;
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

    /* Installed this early so that a SIGHUP during the start (which can include minutes of TensorRT
     * building) waits for the main loop instead of killing the process; on_reload() runs once it runs. */
    guint sighup_id = g_unix_signal_add(SIGHUP, on_reload, &app);

    if (!publisher_start(&app)) {
        g_printerr("Could not start the publisher thread\n");
        free_sources(&app);
        return EXIT_BUILD_FAILED;
    }

    if (opt_snapshot_dir != NULL) {
        app.snap_ctx = nvds_obj_enc_create_context(GPU_ID);
        if (app.snap_ctx == NULL) {
            g_printerr("Could not create the JPEG encoder context\n");
            publisher_stop(&app);
            free_sources(&app);
            return EXIT_BUILD_FAILED;
        }
    }

    app.loop = g_main_loop_new(NULL, FALSE);
    app.pipeline = build_pipeline(&app);
    if (app.pipeline == NULL) {
        app.exit_code = EXIT_BUILD_FAILED;
        publisher_stop(&app);
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
        print_stats(&app);
    }

    if (app.snap_ctx != NULL)
        nvds_obj_enc_destroy_context(app.snap_ctx);
    if (app.event_queue != NULL)
        g_async_queue_unref(app.event_queue);
    g_main_loop_unref(app.loop);
    free_sources(&app);
    g_mutex_clear(&app.slot_lock);
    g_clear_pointer(&file_settings, g_hash_table_destroy);
    g_free(opt_cameras_file);
    g_free(opt_config);
    g_free(opt_tracker_config);
    g_free(opt_output);
    g_free(opt_snapshot_dir);
    g_free(opt_backend_url);
    g_free(opt_spool_file);
    g_free(opt_heartbeat_url);
    g_free(opt_stream_url);
    curl_global_cleanup();
    return app.exit_code;
}
