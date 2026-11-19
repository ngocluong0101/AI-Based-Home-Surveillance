/*
 * app.h - the types every part of the engine shares: SourceCtx (one camera slot), AppCtx (the whole
 * application), and the few constants more than one file needs.
 *
 * Which thread touches what (each field below says it again where it matters):
 *  - main thread (GLib main loop): sources.c, stream_out.c (publishers), report.c, ds_engine.c
 *  - GStreamer streaming threads:  probes.c, stream_out.c (stream_new_sample), mux_eos_probe in sources.c
 *  - publisher thread:             delivery.c
 */
#ifndef DS_APP_H
#define DS_APP_H

#include <curl/curl.h>
#include <glib.h>
#include <gst/gst.h>
#include <stdatomic.h>

#include "event_manager.h"
#include "event_record.h"
#include "behavior.h"
#include "config.h"
#include "event_spool.h"
#include "pose.h"
#include "gstnvdsmeta.h"
#include "nvbufsurface.h"
#include "nvds_obj_encode.h"   /* needs the two DeepStream headers above first */

/* People handed to the behaviour rules from one frame (the first ones; a hall with more is not what the rules are for). */
#define BEH_MAX_PEOPLE 64

#define EXIT_BAD_ARGS 2
#define EXIT_BUILD_FAILED 3

/* nvstreammux output size and how long it waits to fill a batch (microseconds). Shorter than a frame
 * interval at 30 fps (33 ms): with a longer wait, a camera that is down would limit the others to
 * one frame per wait (25 fps at 40 ms) while the muxer waits for its frames. */
#define MUXER_WIDTH 1920
#define MUXER_HEIGHT 1080
#define MUXER_BATCH_TIMEOUT_USEC 20000

/* Frames the stream branch may hold while the stream cannot keep up (live pipelines drop the oldest
 * instead of stalling detection). Every one of them is a whole batch buffer of nvstreammux, taken from
 * its small fixed pool (STREAM_POOL_EXTRA + one per camera, at most 16): a branch that holds more
 * than its share while it is stuck, e.g. just after it reconnected, starves the pool and stops
 * the detection of every camera. */
#define STREAM_QUEUE_BUFFERS 1
#define STREAM_POOL_EXTRA 4
#define MUXER_POOL_MAX 16

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
    /* Pose model only (ROADMAP_V2 V2-2): track_id -> PoseHistory* (the last POSE_HISTORY_FRAMES skeletons of the track),
     * and how many skeletons were stored. Probe thread only; read after the pipeline stopped. */
    GHashTable *pose_hist;
    guint64 pose_count;
    guint64 skeleton_overflow;   /* skeletons not drawn because the frame's display-meta pool was empty */
    /* Behaviour rules (ROADMAP_V2 V2-3: falls, fights, restricted areas). `behavior` is made with the slot and used by
     * the probe (under slot_lock). SIGHUP (main thread) leaves new settings in beh_pending under beh_lock and sets
     * beh_dirty; the probe takes them over at the start of its next frame, so the rules never change in the middle of one. */
    Behavior *behavior;
    BehaviorPerson *beh_people;     /* scratch for the probe, BEH_MAX_PEOPLE entries */
    GMutex beh_lock;                /* guards beh_pending */
    BehaviorSettings beh_pending;   /* the settings last asked for (the starting ones too) */
    _Atomic gboolean beh_dirty;
    _Atomic gboolean beh_active;    /* a rule runs or a zone exists: the probe has something to do */
    guint64 behavior_events[3];     /* FALL, FIGHT, RESTRICTED_AREA reported (probe only) */
    /* What the picture shows of the rules (--output / --stream-url): the zones, and for a few seconds a label on the people
     * of an event. Probe only. */
    BehaviorZone draw_zones[BEHAVIOR_MAX_ZONES];
    size_t num_draw_zones;
    GHashTable *alert_labels;       /* track_id -> AlertLabel* (probes.c) */
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

/* Create an element, check it, and add it to the bin (the bin takes ownership). Shared by the code that
 * builds the pipeline (ds_engine.c), a camera's source (sources.c) and its stream branch (stream_out.c). */
static inline GstElement *make_element(GstElement *pipeline, const gchar *factory, const gchar *name)
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

#endif /* DS_APP_H */
