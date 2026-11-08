/*
 * probes.c - the pad probes (see probes.h) and everything they need to turn a frame into queue items: the
 * Detection[] of a frame, the events it raised, the snapshot JPEG, and the push to the publisher's queue.
 */
#include "probes.h"

#include <stdatomic.h>
#include <string.h>

#include "config.h"
#include "delivery.h"
#include "detection.h"
#include "gstnvdsmeta.h"
#include "nvbufsurface.h"
#include "nvds_obj_encode.h"

/* Most Detection entries gathered from one frame (nvtracker keeps at most 150 targets per
 * stream, see maxTargetsPerStream in the tracker config). */
#define MAX_DETECTIONS_PER_FRAME 256

/* Events waiting for the publisher thread. A dead backend does not fill it: the worker moves
 * events to the spool file at once. It fills only if the worker is stuck in one slow request
 * (HTTP_TIMEOUT_MS at most); then new events are dropped and counted rather than letting memory
 * grow. */
#define EVENT_QUEUE_MAX 1024

/* JPEG bytes that may wait in the queue (a 1080p frame is ~200 KB); beyond it a snapshot is skipped
 * and its events are published without one. */
#define SNAPSHOT_QUEUE_MAX_BYTES (64 * 1024 * 1024)

/* Class id of "person" in the nvinfer output (the YOLOv8 parser reports only this class). */
#define PERSON_CLASS_ID 0

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

/*
 * Pad probe on the src pad of nvinfer, i.e. before the tracker: applies the settings of each camera
 * to what the detector found. A camera with AI off loses every object; one with a confidence
 * threshold loses the objects below it. The tracker, the events, the people count and the picture
 * (the boxes of the OSD) then all see the same, smaller set. Installed only when some camera has
 * such a setting, and does nothing for the cameras that do not.
 */
GstPadProbeReturn settings_probe(GstPad *pad, GstPadProbeInfo *info, gpointer user_data)
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
GstPadProbeReturn detection_probe(GstPad *pad, GstPadProbeInfo *info, gpointer user_data)
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
