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

/* Scores of the skeleton network taken per frame (one per tracked person per frame is the most it can produce for one camera; older
 * ones that did not fit stay for the next frame). */
#define BEH_MAX_SCORES 64

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

/* The 16 bones that are drawn (the 19 of the COCO skeleton minus the three that join the ears to the shoulders:
 * 16 is what one display meta holds). Pairs of keypoint indices. */
static const guint8 BONES[16][2] = {
    { KP_RIGHT_ANKLE, KP_RIGHT_KNEE }, { KP_RIGHT_KNEE, KP_RIGHT_HIP }, { KP_LEFT_ANKLE, KP_LEFT_KNEE },
    { KP_LEFT_KNEE, KP_LEFT_HIP }, { KP_LEFT_HIP, KP_RIGHT_HIP }, { KP_LEFT_SHOULDER, KP_LEFT_HIP },
    { KP_RIGHT_SHOULDER, KP_RIGHT_HIP }, { KP_LEFT_SHOULDER, KP_RIGHT_SHOULDER },
    { KP_LEFT_SHOULDER, KP_LEFT_ELBOW }, { KP_RIGHT_SHOULDER, KP_RIGHT_ELBOW }, { KP_LEFT_ELBOW, KP_LEFT_WRIST },
    { KP_RIGHT_ELBOW, KP_RIGHT_WRIST }, { KP_NOSE, KP_LEFT_EYE }, { KP_NOSE, KP_RIGHT_EYE },
    { KP_LEFT_EYE, KP_LEFT_EAR }, { KP_RIGHT_EYE, KP_RIGHT_EAR },
};

/* Draw one skeleton on the frame with a display meta (the OSD draws it with the boxes). One colour per track. Costs
 * a display meta from the batch's pool; when the pool is empty the skeleton is skipped and counted. */
static void draw_skeleton(NvDsBatchMeta *batch_meta, NvDsFrameMeta *frame_meta, SourceCtx *src, const Pose *pose,
                          guint64 track_id)
{
    static const gdouble PALETTE[6][3] = { { 0.1, 0.9, 0.3 }, { 1.0, 0.6, 0.1 }, { 0.2, 0.7, 1.0 },
                                           { 1.0, 0.3, 0.6 }, { 0.9, 0.9, 0.2 }, { 0.7, 0.4, 1.0 } };
    NvDsDisplayMeta *dm = nvds_acquire_display_meta_from_pool(batch_meta);
    if (dm == NULL) {
        src->skeleton_overflow++;
        return;
    }
    const gdouble *c = PALETTE[track_id % G_N_ELEMENTS(PALETTE)];
    for (guint b = 0; b < G_N_ELEMENTS(BONES); b++) {
        const Keypoint *a = &pose->kp[BONES[b][0]], *e = &pose->kp[BONES[b][1]];
        if (a->v < POSE_VISIBILITY_MIN || e->v < POSE_VISIBILITY_MIN || dm->num_lines >= MAX_ELEMENTS_IN_DISPLAY_META)
            continue;
        NvOSD_LineParams *line = &dm->line_params[dm->num_lines++];
        line->x1 = (guint)CLAMP(a->x, 0.0f, MUXER_WIDTH - 1.0f);
        line->y1 = (guint)CLAMP(a->y, 0.0f, MUXER_HEIGHT - 1.0f);
        line->x2 = (guint)CLAMP(e->x, 0.0f, MUXER_WIDTH - 1.0f);
        line->y2 = (guint)CLAMP(e->y, 0.0f, MUXER_HEIGHT - 1.0f);
        line->line_width = 3;
        line->line_color = (NvOSD_ColorParams){ c[0], c[1], c[2], 1.0 };
    }
    nvds_add_display_meta_to_frame(frame_meta, dm);
}

/* Pose model: put the skeleton of every tracked person into its track's history (and draw it when there is a picture
 * to draw on). The keypoints are in the object's mask, already in frame pixels (pose_probe). Returns how many were
 * stored. Skeletons of tracks gone for CLEANUP_AFTER_FRAMES are dropped from the table now and then. */
#define POSE_CLEANUP_EVERY_FRAMES 256
#define POSE_CLEANUP_AFTER_FRAMES 300
static guint collect_poses(NvDsBatchMeta *batch_meta, NvDsFrameMeta *frame_meta, SourceCtx *src, gboolean draw)
{
    guint stored = 0;
    for (NvDsMetaList *l_obj = frame_meta->obj_meta_list; l_obj != NULL; l_obj = l_obj->next) {
        const NvDsObjectMeta *obj = (const NvDsObjectMeta *)l_obj->data;
        if (obj->object_id == UNTRACKED_OBJECT_ID || obj->class_id != PERSON_CLASS_ID ||
            obj->mask_params.data == NULL || obj->mask_params.size != POSE_VALUES * sizeof(float))
            continue;
        Pose pose;
        if (pose_from_values(obj->mask_params.data, &pose) != 0)
            continue;
        gpointer key = GSIZE_TO_POINTER((gsize)obj->object_id);
        PoseHistory *hist = g_hash_table_lookup(src->pose_hist, key);
        if (hist == NULL) {
            hist = pose_history_new();
            if (hist == NULL)
                continue;
            g_hash_table_insert(src->pose_hist, key, hist);
        }
        const float size_hint = MAX(obj->rect_params.width, obj->rect_params.height);
        if (pose_history_push(hist, frame_meta->frame_num, &pose, size_hint) == 0) {
            stored++;
            src->pose_count++;
        }
        if (draw)
            draw_skeleton(batch_meta, frame_meta, src, &pose, obj->object_id);
    }
    if (frame_meta->frame_num % POSE_CLEANUP_EVERY_FRAMES == 0) {
        GHashTableIter it;
        gpointer key, value;
        g_hash_table_iter_init(&it, src->pose_hist);
        while (g_hash_table_iter_next(&it, &key, &value)) {
            if (frame_meta->frame_num - pose_history_last_frame(value) > POSE_CLEANUP_AFTER_FRAMES)
                g_hash_table_iter_remove(&it);
        }
    }
    return stored;
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

/* The tracked people of one frame as the behaviour rules want them (boxes and, with the pose model, skeletons in frame pixels).
 * Returns how many were written to out (at most max). */
static guint collect_behavior_people(const NvDsFrameMeta *frame_meta, BehaviorPerson *out, guint max)
{
    guint n = 0;
    for (NvDsMetaList *l_obj = frame_meta->obj_meta_list; l_obj != NULL && n < max; l_obj = l_obj->next) {
        const NvDsObjectMeta *obj = (const NvDsObjectMeta *)l_obj->data;
        if (obj->object_id == UNTRACKED_OBJECT_ID || obj->class_id != PERSON_CLASS_ID)
            continue;
        BehaviorPerson *p = &out[n++];
        p->track_id = obj->object_id;
        p->box[0] = obj->rect_params.left;
        p->box[1] = obj->rect_params.top;
        p->box[2] = obj->rect_params.width;
        p->box[3] = obj->rect_params.height;
        p->has_pose = obj->mask_params.data != NULL && obj->mask_params.size == POSE_VALUES * sizeof(float);
        if (p->has_pose) {
            for (int k = 0; k < BEHAVIOR_KEYPOINTS; k++) {
                p->kp[k].x = obj->mask_params.data[3 * k];
                p->kp[k].y = obj->mask_params.data[3 * k + 1];
                p->kp[k].v = obj->mask_params.data[3 * k + 2];
            }
        }
    }
    return n;
}

/* Probe side: a queue item holding the EventRecord of a behaviour event of source `src`. The box is the person's, or for
 * a fight the box around both people (relative to the frame). */
static QueueItem *new_behavior_item(const AppCtx *app, const SourceCtx *src, const BehaviorEvent *ev,
                                    const BehaviorPerson *people, guint num_people)
{
    QueueItem *item = g_new0(QueueItem, 1);
    EventRecord *rec = &item->rec;
    item->kind = ITEM_EVENT;
    gchar *id = g_uuid_string_random();
    g_strlcpy(rec->event_id, id, sizeof(rec->event_id));
    g_free(id);
    g_strlcpy(rec->pipeline_run_id, app->pipeline_run_id, sizeof(rec->pipeline_run_id));
    rec->camera_id = src->camera_id;
    rec->type = ev->type == BEHAVIOR_FALL ? EVENT_FALL : ev->type == BEHAVIOR_FIGHT ? EVENT_FIGHT : EVENT_RESTRICTED_AREA;
    rec->track_id = ev->track_ids[0];
    rec->num_track_ids = ev->num_tracks;
    rec->track_ids[0] = ev->track_ids[0];
    rec->track_ids[1] = ev->track_ids[1];
    rec->confidence = (float)ev->score;
    rec->timestamp_ns = ev->now_ns;
    rec->occurred_ns = ev->occurred_ns;
    if (ev->type == BEHAVIOR_RESTRICTED_AREA) {
        rec->has_zone = 1;
        rec->zone_id = ev->zone_id;
    }
    double x1 = 1e30, y1 = 1e30, x2 = -1e30, y2 = -1e30;
    for (guint i = 0; i < num_people; i++) {
        gboolean involved = people[i].track_id == ev->track_ids[0] || (ev->num_tracks > 1 && people[i].track_id == ev->track_ids[1]);
        if (!involved)
            continue;
        x1 = MIN(x1, people[i].box[0]);
        y1 = MIN(y1, people[i].box[1]);
        x2 = MAX(x2, people[i].box[0] + people[i].box[2]);
        y2 = MAX(y2, people[i].box[1] + people[i].box[3]);
    }
    if (x2 > x1 && y2 > y1)
        detection_bbox_normalize(x1, y1, x2 - x1, y2 - y1, MUXER_WIDTH, MUXER_HEIGHT, rec->bbox);
    return item;
}

/* ---- what the picture shows of the behaviour rules ---- */
typedef struct {
    gint64 until_ns;        /* stream clock */
    BehaviorType type;
} AlertLabel;

#define ALERT_LABEL_SECONDS 5

/* A display meta that has room for one more line / text; a new one is added to the frame when the current one is full. */
static NvDsDisplayMeta *next_meta(NvDsBatchMeta *bm, NvDsFrameMeta *fm, NvDsDisplayMeta *dm, gboolean for_text,
                                  SourceCtx *src)
{
    if (dm != NULL && (for_text ? dm->num_labels < MAX_ELEMENTS_IN_DISPLAY_META : dm->num_lines < MAX_ELEMENTS_IN_DISPLAY_META))
        return dm;
    NvDsDisplayMeta *fresh = nvds_acquire_display_meta_from_pool(bm);
    if (fresh == NULL) {
        src->skeleton_overflow++;
        return NULL;
    }
    nvds_add_display_meta_to_frame(fm, fresh);
    return fresh;
}

/* The zones (red: restricted, blue: lie_ok, grey: ignore) and, over a person who raised an event, its name. */
static void draw_rules(NvDsBatchMeta *bm, NvDsFrameMeta *fm, SourceCtx *src, const BehaviorPerson *people, guint np, gint64 now)
{
    NvDsDisplayMeta *lines = NULL;
    for (size_t z = 0; z < src->num_draw_zones; z++) {
        const BehaviorZone *zone = &src->draw_zones[z];
        if (zone->num_points < 3)
            continue;
        const gdouble *c = zone->kind == BEHAVIOR_ZONE_RESTRICTED ? (const gdouble[]){ 1.0, 0.2, 0.2 }
                           : zone->kind == BEHAVIOR_ZONE_LIE_OK ? (const gdouble[]){ 0.3, 0.5, 1.0 }
                                                                 : (const gdouble[]){ 0.6, 0.6, 0.6 };
        for (size_t i = 0; i < zone->num_points; i++) {
            const gdouble *a = zone->points[i], *b = zone->points[(i + 1) % zone->num_points];
            lines = next_meta(bm, fm, lines, FALSE, src);
            if (lines == NULL)
                return;
            NvOSD_LineParams *l = &lines->line_params[lines->num_lines++];
            l->x1 = (guint)CLAMP(a[0] * MUXER_WIDTH, 0.0, MUXER_WIDTH - 1.0);
            l->y1 = (guint)CLAMP(a[1] * MUXER_HEIGHT, 0.0, MUXER_HEIGHT - 1.0);
            l->x2 = (guint)CLAMP(b[0] * MUXER_WIDTH, 0.0, MUXER_WIDTH - 1.0);
            l->y2 = (guint)CLAMP(b[1] * MUXER_HEIGHT, 0.0, MUXER_HEIGHT - 1.0);
            l->line_width = 3;
            l->line_color = (NvOSD_ColorParams){ c[0], c[1], c[2], 1.0 };
        }
    }

    NvDsDisplayMeta *texts = NULL;
    for (guint i = 0; i < np; i++) {
        const AlertLabel *al = g_hash_table_lookup(src->alert_labels, GSIZE_TO_POINTER((gsize)people[i].track_id));
        if (al == NULL || now > al->until_ns)
            continue;
        texts = next_meta(bm, fm, texts, TRUE, src);
        if (texts == NULL)
            return;
        NvOSD_TextParams *t = &texts->text_params[texts->num_labels++];
        t->display_text = g_strdup(al->type == BEHAVIOR_FALL ? "FALL" : al->type == BEHAVIOR_FIGHT ? "FIGHT" : "RESTRICTED AREA");
        t->x_offset = (guint)CLAMP(people[i].box[0], 0.0, MUXER_WIDTH - 200.0);
        t->y_offset = (guint)CLAMP(people[i].box[1] - 36.0, 0.0, MUXER_HEIGHT - 40.0);
        t->font_params.font_name = (gchar *)"Serif";
        t->font_params.font_size = 22;
        t->font_params.font_color = (NvOSD_ColorParams){ 1.0, 1.0, 1.0, 1.0 };
        t->set_bg_clr = 1;
        t->text_bg_clr = (NvOSD_ColorParams){ 0.85, 0.0, 0.0, 0.85 };
    }
}

/* The events that started in one frame, as a range of the probe's `pending` array. */
typedef struct {
    NvDsFrameMeta *frame_meta;
    guint first, count;
    gboolean encoding;       /* an encode of this frame was queued with the encoder */
    QueueItem *snapshot;     /* its JPEG, once finish() is done; NULL = no snapshot */
} FrameGroup;

/* A frame of the ring (--frame-ring-dir) waiting for the encoder; its JPEG becomes an ITEM_RING. */
typedef struct {
    NvDsFrameMeta *frame_meta;
    char name[64];
} RingJob;

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

/* Ask the encoder for a JPEG of one frame for the ring. Same rules as submit_snapshot(): the caller finishes the encoder before the buffer leaves. */
static gboolean submit_ring_frame(AppCtx *app, GstBuffer *buf, GstMapInfo *map, gboolean *mapped, NvDsFrameMeta *frame_meta)
{
    if (!*mapped) {
        if (!gst_buffer_map(buf, map, GST_MAP_READ))
            return FALSE;
        *mapped = TRUE;
    }
    NvDsObjEncUsrArgs args = { 0 };
    args.isFrame = TRUE;
    args.saveImg = FALSE;
    args.attachUsrMeta = TRUE;
    args.quality = 70;   /* the VLM shrinks them to 640 px anyway */
    return nvds_obj_enc_process(app->snap_ctx, &args, (NvBufSurface *)map->data, NULL, frame_meta) ? TRUE : FALSE;
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

GstPadProbeReturn pose_probe(GstPad *pad, GstPadProbeInfo *info, gpointer user_data)
{
    (void)pad;
    (void)user_data;
    GstBuffer *buf = GST_PAD_PROBE_INFO_BUFFER(info);
    NvDsBatchMeta *batch_meta = (buf != NULL) ? gst_buffer_get_nvds_batch_meta(buf) : NULL;
    if (batch_meta == NULL)
        return GST_PAD_PROBE_OK;

    for (NvDsMetaList *l_frame = batch_meta->frame_meta_list; l_frame != NULL; l_frame = l_frame->next) {
        const NvDsFrameMeta *frame_meta = (const NvDsFrameMeta *)l_frame->data;
        for (NvDsMetaList *l_obj = frame_meta->obj_meta_list; l_obj != NULL; l_obj = l_obj->next) {
            NvDsObjectMeta *obj = (NvDsObjectMeta *)l_obj->data;
            float *v = obj->mask_params.data;
            if (v == NULL || obj->mask_params.size != POSE_VALUES * sizeof(float))
                continue;   /* the detection-only model has no mask */
            for (int k = 0; k < POSE_KEYPOINTS; k++) {
                v[3 * k] = obj->rect_params.left + v[3 * k] * obj->rect_params.width;
                v[3 * k + 1] = obj->rect_params.top + v[3 * k + 1] * obj->rect_params.height;
            }
        }
    }
    return GST_PAD_PROBE_OK;
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
    GArray *ring_jobs = NULL;

    for (NvDsMetaList *l_frame = batch_meta->frame_meta_list; l_frame != NULL;
         l_frame = l_frame->next) {
        NvDsFrameMeta *frame_meta = (NvDsFrameMeta *)l_frame->data;
        if (frame_meta->source_id >= app->num_sources)
            continue;
        SourceCtx *src = &app->sources[frame_meta->source_id];
        if (!src->active)
            continue;   /* a frame of a camera that was just taken out */

        guint total = 0, untracked = 0;
        gboolean event_frame = FALSE;   /* this frame's events are being encoded as a snapshot */
        size_t n = extract_person_detections(frame_meta, dets, G_N_ELEMENTS(dets), &total,
                                             &untracked);
        atomic_fetch_add_explicit(&src->frame_count, 1, memory_order_relaxed);
        atomic_store_explicit(&src->people_now, (gint)n, memory_order_relaxed);
        atomic_store_explicit(&src->last_frame_us, g_get_monotonic_time(), memory_order_relaxed);
        src->object_count += total;
        src->untracked_count += untracked;
        if (n > 0)
            collect_poses(batch_meta, frame_meta, src, opt_output != NULL || opt_stream_url != NULL);

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

        /* Behaviour rules (falls, fights, restricted areas), on the same clock. SIGHUP changes are taken over here, between
         * two frames. */
        BehaviorEvent bev[8];
        int nbev = 0;
        guint nppl = 0;
        if (atomic_exchange(&src->beh_dirty, FALSE)) {
            g_mutex_lock(&src->beh_lock);
            BehaviorSettings fresh = src->beh_pending;
            g_mutex_unlock(&src->beh_lock);
            behavior_set_config(src->behavior, &fresh.cfg);
            src->fall_net_on = fresh.cfg.fall && fresh.cfg.fall_net != 0.0 && app->fallnet != NULL;
            src->ring_on = opt_frame_ring_dir != NULL && (fresh.cfg.fall || fresh.cfg.fight);
            behavior_set_zones(src->behavior, fresh.zones, fresh.num_zones);
            memcpy(src->draw_zones, fresh.zones, sizeof(src->draw_zones));
            src->num_draw_zones = fresh.num_zones;
        }
        if (src->beh_active && src->behavior != NULL && src->beh_people != NULL) {
            nppl = collect_behavior_people(frame_meta, src->beh_people, BEH_MAX_PEOPLE);
            /* The skeleton network (V2-5): a window of every person we have a skeleton of in this frame goes to the worker (which never
             * blocks us); the scores that came back since the last frame go into the rules. */
            BehaviorScore scores[BEH_MAX_SCORES];
            size_t nscores = 0;
            if (src->fall_net_on && app->fallnet != NULL) {
                const gint64 at = src->stream_start_ns + (gint64)frame_meta->buf_pts;
                const guint T = (guint)fallnet_window_len(app->fallnet);
                for (guint i = 0; i < nppl; i++) {
                    const PoseHistory *hist = g_hash_table_lookup(src->pose_hist, GSIZE_TO_POINTER((gsize)src->beh_people[i].track_id));
                    if (hist != NULL && pose_history_skeleton_window(hist, frame_meta->frame_num, T, POSE_VISIBILITY_MIN, app->fallnet_window) == 0)
                        fallnet_submit(app->fallnet, (int)src->source_id, src->beh_people[i].track_id, at, app->fallnet_window);
                }
                nscores = fallnet_take(app->fallnet, (int)src->source_id, scores, BEH_MAX_SCORES);
            }
            nbev = behavior_process_scored(src->behavior, src->beh_people, nppl, scores, nscores,
                                           src->stream_start_ns + (gint64)frame_meta->buf_pts, bev, G_N_ELEMENTS(bev));
            if (nbev < 0)
                nbev = 0;   /* out of memory: nothing reported for this frame */
            const gint64 now = src->stream_start_ns + (gint64)frame_meta->buf_pts;
            for (int i = 0; i < nbev; i++) {
                src->behavior_events[bev[i].type]++;
                for (int k = 0; k < bev[i].num_tracks; k++) {
                    AlertLabel *al = g_new0(AlertLabel, 1);
                    al->until_ns = now + (gint64)ALERT_LABEL_SECONDS * G_GINT64_CONSTANT(1000000000);
                    al->type = bev[i].type;
                    g_hash_table_replace(src->alert_labels, GSIZE_TO_POINTER((gsize)bev[i].track_ids[k]), al);
                }
            }
            if (opt_output != NULL || opt_stream_url != NULL)
                draw_rules(batch_meta, frame_meta, src, src->beh_people, nppl, now);
        }

        if (emitted > 0 || nbev > 0) {
            if (emitted < 0)
                emitted = 0;
            if (pending == NULL) {
                pending = g_ptr_array_new();
                groups = g_array_new(FALSE, TRUE, sizeof(FrameGroup));
            }
            FrameGroup group = { .frame_meta = frame_meta, .first = pending->len,
                                 .count = (guint)(emitted + nbev) };
            for (int i = 0; i < emitted; i++)
                g_ptr_array_add(pending, new_event_item(app, src, &events[i]));
            for (int i = 0; i < nbev; i++)
                g_ptr_array_add(pending, new_behavior_item(app, src, &bev[i], src->beh_people, nppl));
            if (app->snap_ctx != NULL && opt_snapshot_dir != NULL) {
                submit_snapshot(app, buf, &map, &mapped, &group,
                                (QueueItem **)pending->pdata + group.first);
                encode_queued |= group.encoding;
            }
            g_array_append_val(groups, group);
            event_frame = group.encoding;
        }

        /* The ring of recent frames for the VLM (V2-6): a few per second per camera with a fall or fight rule, never the frame that already
         * carries an event snapshot (a frame has one encoded image attached). */
        if (src->ring_on && app->snap_ctx != NULL && !event_frame) {
            const gint64 at = src->stream_start_ns + (gint64)frame_meta->buf_pts;
            if (at - src->ring_last_ns >= G_GINT64_CONSTANT(1000000000) / opt_frame_ring_fps &&
                (guint64)atomic_load_explicit(&app->snapshot_bytes_queued, memory_order_relaxed) < SNAPSHOT_QUEUE_MAX_BYTES &&
                submit_ring_frame(app, buf, &map, &mapped, frame_meta)) {
                if (ring_jobs == NULL)
                    ring_jobs = g_array_new(FALSE, TRUE, sizeof(RingJob));
                RingJob job = { .frame_meta = frame_meta };
                g_snprintf(job.name, sizeof(job.name), "ring_cam%d_%" G_GINT64_FORMAT ".jpg", src->camera_id, at / 1000000);
                g_array_append_val(ring_jobs, job);
                atomic_fetch_add_explicit(&app->ring_submitted, 1, memory_order_relaxed);
                src->ring_last_ns = at;
            }
        }
    }

    if (encode_queued || (ring_jobs != NULL && ring_jobs->len > 0)) {
        gint64 t0 = g_get_monotonic_time();
        nvds_obj_enc_finish(app->snap_ctx);
        guint64 waited = (guint64)(g_get_monotonic_time() - t0);
        app->snapshot_block_us += waited;
        if (waited > app->snapshot_block_max_us)
            app->snapshot_block_max_us = waited;
    }
    if (ring_jobs != NULL) {
        for (guint i = 0; i < ring_jobs->len; i++) {
            const RingJob *job = &g_array_index(ring_jobs, RingJob, i);
            QueueItem *item = take_jpeg(job->frame_meta, job->name);
            if (item == NULL)
                continue;
            if (g_async_queue_length(app->event_queue) >= (gint)EVENT_QUEUE_MAX) {   /* (signed: it is negative while the publisher waits) the publisher is behind: a ring frame is the first thing to give up */
                queue_item_free(item);
                continue;
            }
            item->kind = ITEM_RING;
            atomic_fetch_add_explicit(&app->snapshot_bytes_queued, item->jpeg_len, memory_order_relaxed);
            atomic_fetch_add_explicit(&app->ring_queued, 1, memory_order_relaxed);
            g_async_queue_push(app->event_queue, item);
        }
        g_array_free(ring_jobs, TRUE);
    }
    if (pending != NULL) {
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
