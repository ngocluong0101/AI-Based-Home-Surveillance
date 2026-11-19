/*
 * report.c - source table, statistics, frame rate and heartbeat. See report.h.
 */
#include "report.h"

#include <stdatomic.h>
#include <string.h>

#include "config.h"
#include "delivery.h"
#include "heartbeat_record.h"

/* Tracks shorter than this many frames count as "short" in the end-of-run statistics: a high
 * share of them means the tracker fragments one person into several IDs. */
#define SHORT_TRACK_FRAMES 10

/* The people count of a camera is reported only while its latest frame is at most this old: after that
 * the number describes a scene we no longer see, and "0" would claim an empty room. */
#define PEOPLE_FRESH_US (2 * G_USEC_PER_SEC)

void print_sources(const AppCtx *app)
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

void print_stats(const AppCtx *app)
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
        if (src->pose_count > 0)
            g_print("source_id=%u camera_id=%d poses=%" G_GUINT64_FORMAT " tracks_with_pose=%u skeleton_overflow=%"
                    G_GUINT64_FORMAT "\n", src->source_id, src->camera_id, src->pose_count,
                    g_hash_table_size(src->pose_hist), src->skeleton_overflow);
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

/* Timer on the main loop: print the frame rate of every source over the last interval. */
gboolean report_fps(gpointer data)
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
gboolean heartbeat_tick(gpointer data)
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
