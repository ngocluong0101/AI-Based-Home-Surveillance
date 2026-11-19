/* fallnet.c - see fallnet.h. */
#include "fallnet.h"

#include <glib.h>
#include <string.h>

#include "stgcn.h"

#define RESULT_MAX 512     /* scores kept for the probe to take; more than that and the oldest go */

typedef struct {
    int source_id;
    uint64_t track_id;
    int64_t end_ns;
    float *window;         /* 3 x T x 17, owned */
} Job;

typedef struct {
    int source_id;
    BehaviorScore score;
} Result;

struct FallNet {
    StgcnModel *model;
    int T, max_batch, queue_max;
    size_t window_floats;

    GMutex lock;
    GCond wake;
    GQueue jobs;           /* Job* */
    GArray *results;       /* Result, oldest first */
    gboolean stop;
    GThread *thread;
    FallNetStats stats;
};

static void job_free(Job *j)
{
    g_free(j->window);
    g_free(j);
}

static gpointer worker_main(gpointer data)
{
    FallNet *f = data;
    Job **batch = g_new0(Job *, f->max_batch);
    float *windows = g_new(float, f->window_floats * (size_t)f->max_batch);
    float *probs = g_new(float, f->max_batch);
    char err[256];
    for (;;) {
        g_mutex_lock(&f->lock);
        while (!f->stop && g_queue_is_empty(&f->jobs))
            g_cond_wait(&f->wake, &f->lock);
        if (f->stop) {
            g_mutex_unlock(&f->lock);
            break;
        }
        int n = 0;
        while (n < f->max_batch && !g_queue_is_empty(&f->jobs))
            batch[n++] = g_queue_pop_head(&f->jobs);
        g_mutex_unlock(&f->lock);

        for (int i = 0; i < n; i++)
            memcpy(windows + f->window_floats * (size_t)i, batch[i]->window, f->window_floats * sizeof(float));
        const int rc = stgcn_run(f->model, windows, n, probs, err, sizeof err);
        const double us = stgcn_last_run_us(f->model);

        g_mutex_lock(&f->lock);
        f->stats.batches++;
        if (rc == 0) {
            f->stats.scored += (uint64_t)n;
            f->stats.last_batch_us = us;
            f->stats.total_batch_us += us;
            if (us > f->stats.max_batch_us)
                f->stats.max_batch_us = us;
            for (int i = 0; i < n; i++) {
                Result r = { batch[i]->source_id, { batch[i]->track_id, probs[i], batch[i]->end_ns } };
                g_array_append_val(f->results, r);
            }
            if (f->results->len > RESULT_MAX)
                g_array_remove_range(f->results, 0, f->results->len - RESULT_MAX);
        } else {
            f->stats.errors += (uint64_t)n;
        }
        g_mutex_unlock(&f->lock);
        if (rc != 0)
            g_printerr("fallnet: %s\n", err);
        for (int i = 0; i < n; i++)
            job_free(batch[i]);
    }
    g_free(batch);
    g_free(windows);
    g_free(probs);
    return NULL;
}

FallNet *fallnet_new(const char *onnx_path, int window_len, int max_batch, int queue_max, char *err, int err_len)
{
    StgcnModel *m = stgcn_open(onnx_path, window_len, max_batch, err, err_len);
    if (m == NULL)
        return NULL;
    FallNet *f = g_new0(FallNet, 1);
    f->model = m;
    f->T = window_len;
    f->max_batch = max_batch;
    f->queue_max = queue_max;
    f->window_floats = (size_t)STGCN_CHANNELS * (size_t)window_len * STGCN_JOINTS;
    g_mutex_init(&f->lock);
    g_cond_init(&f->wake);
    g_queue_init(&f->jobs);
    f->results = g_array_new(FALSE, FALSE, sizeof(Result));
    f->thread = g_thread_new("fallnet", worker_main, f);
    return f;
}

void fallnet_free(FallNet *f)
{
    if (f == NULL)
        return;
    g_mutex_lock(&f->lock);
    f->stop = TRUE;
    g_cond_signal(&f->wake);
    g_mutex_unlock(&f->lock);
    g_thread_join(f->thread);
    while (!g_queue_is_empty(&f->jobs))
        job_free(g_queue_pop_head(&f->jobs));
    stgcn_close(f->model);
    g_array_free(f->results, TRUE);
    g_mutex_clear(&f->lock);
    g_cond_clear(&f->wake);
    g_free(f);
}

int fallnet_window_len(const FallNet *f)
{
    return f->T;
}

void fallnet_submit(FallNet *f, int source_id, uint64_t track_id, int64_t end_ns, const float *window)
{
    Job *j = g_new(Job, 1);
    j->source_id = source_id;
    j->track_id = track_id;
    j->end_ns = end_ns;
    j->window = g_memdup2(window, f->window_floats * sizeof(float));
    g_mutex_lock(&f->lock);
    f->stats.submitted++;
    if ((int)g_queue_get_length(&f->jobs) >= f->queue_max) {
        job_free(g_queue_pop_head(&f->jobs));   /* the oldest: a score for a moment that has passed is worth the least */
        f->stats.dropped++;
    }
    g_queue_push_tail(&f->jobs, j);
    if (g_queue_get_length(&f->jobs) > f->stats.queue_peak)
        f->stats.queue_peak = g_queue_get_length(&f->jobs);
    g_cond_signal(&f->wake);
    g_mutex_unlock(&f->lock);
}

size_t fallnet_take(FallNet *f, int source_id, BehaviorScore *out, size_t cap)
{
    size_t n = 0;
    g_mutex_lock(&f->lock);
    for (guint i = 0; i < f->results->len && n < cap;) {
        const Result *r = &g_array_index(f->results, Result, i);
        if (r->source_id == source_id) {
            out[n++] = r->score;
            g_array_remove_index(f->results, i);
        } else {
            i++;
        }
    }
    g_mutex_unlock(&f->lock);
    return n;
}

void fallnet_forget_source(FallNet *f, int source_id)
{
    g_mutex_lock(&f->lock);
    for (guint i = 0; i < f->results->len;) {
        if (g_array_index(f->results, Result, i).source_id == source_id)
            g_array_remove_index(f->results, i);
        else
            i++;
    }
    for (GList *l = f->jobs.head; l != NULL;) {
        GList *next = l->next;
        Job *j = l->data;
        if (j->source_id == source_id) {
            g_queue_delete_link(&f->jobs, l);
            job_free(j);
        }
        l = next;
    }
    g_mutex_unlock(&f->lock);
}

void fallnet_stats(FallNet *f, FallNetStats *out)
{
    g_mutex_lock(&f->lock);
    *out = f->stats;
    g_mutex_unlock(&f->lock);
}
