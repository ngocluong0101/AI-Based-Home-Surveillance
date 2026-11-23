/*
 * @author: Ngoc Luong <ngocluongg0101@gmail.com>
 */

/*
 * delivery.h - the publisher thread: takes what the probes and heartbeat timer queue and does the slow work they must not do
 * (write snapshots, POST events and heartbeats with libcurl, keep events in the spool while the backend is down).
 */
#ifndef DS_DELIVERY_H
#define DS_DELIVERY_H

#include "app.h"

/* What travels through the queue to the publisher thread. */
typedef enum { ITEM_EVENT, ITEM_SNAPSHOT, ITEM_HEARTBEAT, ITEM_RING, ITEM_STOP } ItemKind;

typedef struct {
    ItemKind kind;
    EventRecord rec;       /* ITEM_EVENT */
    gchar *snapshot_name;  /* ITEM_SNAPSHOT: file name inside --snapshot-dir; ITEM_RING: inside --frame-ring-dir */
    guint8 *jpeg;          /* ITEM_SNAPSHOT: the encoded image */
    gsize jpeg_len;
    gchar *heartbeat_json; /* ITEM_HEARTBEAT: the body to POST */
} QueueItem;

void queue_item_free(QueueItem *item);

/* Start the publisher thread (and open the spool / libcurl handles when --backend-url is given). */
gboolean publisher_start(AppCtx *app);

/* Let the worker finish what is queued (within a grace period), then stop it. Safe to call twice. */
void publisher_stop(AppCtx *app);

#endif /* DS_DELIVERY_H */
