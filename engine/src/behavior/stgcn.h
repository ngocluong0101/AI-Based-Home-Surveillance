/*
 * @author: Ngoc Luong <ngocluongg0101@gmail.com>
 */

/*
 * stgcn.h - runs the skeleton classifier of V2-5 (eval/train/) with the TensorRT C++ API. C interface, C++ implementation.
 *
 * Input: windows of (3, T, 17) floats (core/skel_window.h); output: probability that the window ends in a fall. The first call
 * for an ONNX file builds a TensorRT FP16 engine for batch 1..max_batch (~10-20 s) and caches it next to the ONNX
 * (`<onnx>.T<T>.b<max_batch>.fp16.engine`, rebuilt when the ONNX is newer). Not thread-safe: one thread owns a model.
 */
#ifndef STGCN_H
#define STGCN_H

#ifdef __cplusplus
extern "C" {
#endif

#define STGCN_CHANNELS 3
#define STGCN_JOINTS 17

typedef struct StgcnModel StgcnModel;

/* NULL on failure, with a message in err (when err != NULL). window_len = T of the model's input. */
StgcnModel *stgcn_open(const char *onnx_path, int window_len, int max_batch, char *err, int err_len);
int stgcn_window_len(const StgcnModel *m);
int stgcn_max_batch(const StgcnModel *m);

/* windows: n x (3 x T x 17) floats, row-major. prob_fall: n floats out. n in 1..max_batch. Returns 0, or -1 (and a message in err). */
int stgcn_run(StgcnModel *m, const float *windows, int n, float *prob_fall, char *err, int err_len);

/* Wall time of the last stgcn_run() in microseconds (copy in + inference + copy out). */
double stgcn_last_run_us(const StgcnModel *m);

void stgcn_close(StgcnModel *m);

#ifdef __cplusplus
}
#endif
#endif /* STGCN_H */
