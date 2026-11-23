/*
 * @author: Ngoc Luong <ngocluongg0101@gmail.com>
 */

/*
 * delivery.c - the publisher thread. See delivery.h.
 */
#include "delivery.h"

#include <glib/gstdio.h>
#include <stdatomic.h>
#include <string.h>

#include "backoff.h"
#include "config.h"
#include "heartbeat_record.h"

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
#define HEARTBEAT_CONNECT_TIMEOUT_MS 1000
#define HEARTBEAT_TIMEOUT_MS 2000

/* Pushed after the last item to stop the worker (g_async_queue_push() refuses NULL). */
static QueueItem publisher_stop_item = { .kind = ITEM_STOP };

void queue_item_free(QueueItem *item)
{
    g_free(item->snapshot_name);
    g_free(item->jpeg);
    g_free(item->heartbeat_json);
    g_free(item);
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

/* The ring of recent frames (--frame-ring-dir, V2-6): written here, in the publisher thread, and the files older than --frame-ring-seconds are removed
 * as new ones arrive. The names (ring_cam<ID>_<epoch ms>.jpg) are remembered in order of writing, so nothing scans the directory. Files left by an earlier
 * run are removed once, at the first write. Only this thread touches the list. */
typedef struct {
    gchar *path;
    gint64 written_us;
} RingFile;
static GQueue ring_files = G_QUEUE_INIT;
static gboolean ring_swept;
static guint64 ring_write_failures;

static void ring_sweep_old_run(void)
{
    GDir *dir = g_dir_open(opt_frame_ring_dir, 0, NULL);
    if (dir == NULL)
        return;
    const gchar *name;
    while ((name = g_dir_read_name(dir)) != NULL) {
        if (g_str_has_prefix(name, "ring_cam")) {
            gchar *path = g_build_filename(opt_frame_ring_dir, name, NULL);
            g_unlink(path);
            g_free(path);
        }
    }
    g_dir_close(dir);
}

static void ring_write(AppCtx *app, const QueueItem *item)
{
    if (!ring_swept) {
        ring_sweep_old_run();
        ring_swept = TRUE;
    }
    gchar *path = g_build_filename(opt_frame_ring_dir, item->snapshot_name, NULL);
    gboolean okk = g_file_set_contents(path, (const gchar *)item->jpeg, (gssize)item->jpeg_len, NULL);
    if (okk) {
        RingFile *rf = g_new(RingFile, 1);
        rf->path = path;
        rf->written_us = g_get_monotonic_time();
        g_queue_push_tail(&ring_files, rf);
        atomic_fetch_add_explicit(&app->ring_written, 1, memory_order_relaxed);
    } else {
        if (ring_write_failures++ == 0)
            g_printerr("Could not write to --frame-ring-dir; the VLM check will have fewer pictures\n");
        g_free(path);
    }
    const gint64 oldest_kept = g_get_monotonic_time() - (gint64)opt_frame_ring_seconds * G_GINT64_CONSTANT(1000000);
    RingFile *head;
    while ((head = g_queue_peek_head(&ring_files)) != NULL && head->written_us < oldest_kept) {
        g_unlink(head->path);
        g_free(head->path);
        g_free(g_queue_pop_head(&ring_files));
    }
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

/*
 * POST one event. Errors are logged without the URL (it may carry a secret). Only 400/413/422 ("this event is bad") are final; anything
 * else, even 404/401/403 from a misconfigured backend, is retried so no event is lost to a setup mistake. 409 = already stored.
 */
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

/*
 * POST one heartbeat. Never spooled or retried (a late one is worthless) and it does not touch the event path's backoff (the backend
 * can answer heartbeats while its database refuses events). Only the first failure after a success is logged.
 */
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
        } else if (item->kind == ITEM_RING) {
            atomic_fetch_sub_explicit(&app->snapshot_bytes_queued, item->jpeg_len, memory_order_relaxed);
            ring_write(app, item);
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

gboolean publisher_start(AppCtx *app)
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
void publisher_stop(AppCtx *app)
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
