/*
 * dump.c - the CSV writer behind --dump-detections (see dump.h).
 *
 * Columns: stage,source_id,frame_num,pts_ns,object_id,class_id,left,top,width,height,confidence,tracker_confidence,kpts
 *   stage   "fdet" / "ftrk" = a frame went through nvinfer / nvtracker (all columns after pts_ns are empty; it is
 *           how an offline script knows about frames that had no object), "det" = an object from nvinfer,
 *           "trk" = an object from nvtracker.
 *   boxes   pixels of the nvstreammux frame (MUXER_WIDTH x MUXER_HEIGHT); a source of another size is scaled
 *           to it, so the script must scale back.
 *   object_id  the track ID (trk); empty for det.
 *   kpts    pose model only: 17 keypoints as "x y visibility" triples (51 numbers separated by spaces), x and y in pixels of
 *           the nvstreammux frame (pose_probe made them absolute, before the tracker moved any box); empty when the object
 *           has none.
 * Numbers are printed with fixed-point formats on purpose (no locale surprises, easy to parse).
 */
#include "dump.h"

#include <stdio.h>
#include <string.h>

/* 17 keypoints x (x_rel, y_rel, visibility) */
#define DUMP_KPT_VALUES 51

#define COLUMNS "stage,source_id,frame_num,pts_ns,object_id,class_id,left,top,width,height,confidence,tracker_confidence,kpts\n"

static GAsyncQueue *dump_queue;
static GThread *dump_thread;
static FILE *dump_file;
/* A queue entry that is never a pointer to text, so that "stop" cannot be mistaken for a row block. */
static gchar dump_stop_marker;

static gpointer dump_writer(gpointer unused)
{
    (void)unused;
    for (;;) {
        gchar *text = g_async_queue_pop(dump_queue);
        if (text == &dump_stop_marker)
            break;
        fputs(text, dump_file);
        g_free(text);
    }
    return NULL;
}

gboolean dump_start(const gchar *path)
{
    dump_file = fopen(path, "w");
    if (dump_file == NULL) {
        g_printerr("Could not open --dump-detections file for writing\n");
        return FALSE;
    }
    fputs(COLUMNS, dump_file);
    dump_queue = g_async_queue_new();
    dump_thread = g_thread_new("dump-writer", dump_writer, NULL);
    return TRUE;
}

void dump_stop(void)
{
    if (dump_thread == NULL)
        return;
    g_async_queue_push(dump_queue, &dump_stop_marker);
    g_thread_join(dump_thread);
    dump_thread = NULL;
    g_async_queue_unref(dump_queue);
    dump_queue = NULL;
    fclose(dump_file);
    dump_file = NULL;
}

/* One block of text per batch: a frame row ("fdet"/"ftrk") and one row per object, for every frame of the batch. */
static GstPadProbeReturn dump_batch(GstPadProbeInfo *info, const gchar *stage, gboolean tracked)
{
    GstBuffer *buf = GST_PAD_PROBE_INFO_BUFFER(info);
    NvDsBatchMeta *batch_meta = (buf != NULL) ? gst_buffer_get_nvds_batch_meta(buf) : NULL;
    if (batch_meta == NULL || dump_queue == NULL)
        return GST_PAD_PROBE_OK;

    GString *out = g_string_new(NULL);
    for (NvDsMetaList *l_frame = batch_meta->frame_meta_list; l_frame != NULL; l_frame = l_frame->next) {
        const NvDsFrameMeta *fm = (const NvDsFrameMeta *)l_frame->data;
        g_string_append_printf(out, "f%s,%u,%d,%" G_GUINT64_FORMAT ",,,,,,,,,\n", stage, fm->source_id,
                               fm->frame_num, (guint64)fm->buf_pts);
        for (NvDsMetaList *l_obj = fm->obj_meta_list; l_obj != NULL; l_obj = l_obj->next) {
            const NvDsObjectMeta *obj = (const NvDsObjectMeta *)l_obj->data;
            g_string_append_printf(out, "%s,%u,%d,%" G_GUINT64_FORMAT ",", stage, fm->source_id,
                                   fm->frame_num, (guint64)fm->buf_pts);
            if (tracked)
                g_string_append_printf(out, "%" G_GUINT64_FORMAT, (guint64)obj->object_id);
            g_string_append_printf(out, ",%d,%.3f,%.3f,%.3f,%.3f,%.6f,%.6f,", obj->class_id,
                                   obj->rect_params.left, obj->rect_params.top,
                                   obj->rect_params.width, obj->rect_params.height,
                                   (double)obj->confidence, (double)obj->tracker_confidence);
            /* the pose parser (nvdsinfer_yolov8_pose_parser.cpp) sends its 17 keypoints in the "mask" */
            if (obj->mask_params.data != NULL && obj->mask_params.size == DUMP_KPT_VALUES * sizeof(float)) {
                for (guint i = 0; i < DUMP_KPT_VALUES; i++)
                    g_string_append_printf(out, i == 0 ? "%.4f" : " %.4f", (double)obj->mask_params.data[i]);
            }
            g_string_append_c(out, '\n');
        }
    }
    g_async_queue_push(dump_queue, g_string_free(out, FALSE));
    return GST_PAD_PROBE_OK;
}

GstPadProbeReturn dump_det_probe(GstPad *pad, GstPadProbeInfo *info, gpointer user_data)
{
    (void)pad;
    (void)user_data;
    return dump_batch(info, "det", FALSE);
}

GstPadProbeReturn dump_trk_probe(GstPad *pad, GstPadProbeInfo *info, gpointer user_data)
{
    (void)pad;
    (void)user_data;
    return dump_batch(info, "trk", TRUE);
}
