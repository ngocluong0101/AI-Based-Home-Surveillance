/*
 * @author: Ngoc Luong <ngocluongg0101@gmail.com>
 */

/*
 * fallnet.h - the skeleton classifier as the engine uses it (V2-5): a worker thread scores windows in batches so the pad probe
 * never waits for the GPU. fallnet_submit() (probe) queues one window per tracked person and never blocks (a full queue drops its
 * OLDEST job); the worker runs stgcn_run() on up to max_batch jobs; fallnet_take() (probe) returns the scores for one source.
 *
 * Fail-open: a failed batch is counted in `errors` and dropped. With fall_net = 2 the rules still report falls; with fall_net = 1
 * the engine refuses to start if the model cannot load (see ds_engine.c).
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
