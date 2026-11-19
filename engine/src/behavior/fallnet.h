/*
 * fallnet.h - the skeleton classifier as the engine uses it (ROADMAP_V2 V2-5): a worker thread that scores windows in batches so that the
 * pad probe never waits for the GPU.
 *
 *   probe thread:  fallnet_submit()  one window per tracked person per frame        -> never blocks (a full queue drops its OLDEST job)
 *   worker thread: takes up to max_batch jobs, stgcn_run(), keeps the scores
 *   probe thread:  fallnet_take()    the scores that arrived for one source        -> BehaviorScore for behavior_process_scored()
 *
 * Fail-open: when a batch fails the jobs are counted as `errors` and dropped; the behaviour logic then has no scores, and with fall_net = 2
 * the rules still report falls (with fall_net = 1 the engine refuses to start the model at all if it cannot load, see ds_engine.c).
 */
#ifndef FALLNET_H
#define FALLNET_H

#include <stddef.h>
#include <stdint.h>

#include "behavior.h"

typedef struct FallNet FallNet;

typedef struct {
    uint64_t submitted, dropped, scored, batches, errors;
    double last_batch_us, max_batch_us, total_batch_us;
    uint64_t queue_peak;
} FallNetStats;

/* Loads (and, the first time, builds) the TensorRT engine for onnx_path, starts the worker. NULL with a message in err on failure. */
FallNet *fallnet_new(const char *onnx_path, int window_len, int max_batch, int queue_max, char *err, int err_len);
void fallnet_free(FallNet *f);   /* stops the worker; NULL is fine */

int fallnet_window_len(const FallNet *f);

/* `window` is 3 x window_len x 17 floats (pose_history_skeleton_window); copied. */
void fallnet_submit(FallNet *f, int source_id, uint64_t track_id, int64_t end_ns, const float *window);

/* Moves the scores that arrived for source_id (oldest first) into out, at most cap. Returns how many. */
size_t fallnet_take(FallNet *f, int source_id, BehaviorScore *out, size_t cap);

/* Forget the results and queued jobs of a source (a camera was removed). */
void fallnet_forget_source(FallNet *f, int source_id);

void fallnet_stats(FallNet *f, FallNetStats *out);

#endif /* FALLNET_H */
