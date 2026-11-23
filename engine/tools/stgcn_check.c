/*
 * @author: Ngoc Luong <ngocluongg0101@gmail.com>
 */

/*
 * stgcn_check.c - check and time the TensorRT model against reference probabilities (V2-5.4). Not part of the engine.
 *
 * stgcn_check MODEL.onnx T WINDOWS.f32 PROBS.f32 [BATCH]
 * WINDOWS.f32: N x (3 x T x 17) float32; PROBS.f32: the PyTorch probabilities of the same windows. Prints the largest
 * |TensorRT FP16 - reference| and the time per batch.
 */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>

#include "../src/behavior/stgcn.h"

static float *slurp(const char *path, long *n)
{
    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); return NULL; }
    fseek(f, 0, SEEK_END);
    long bytes = ftell(f);
    fseek(f, 0, SEEK_SET);
    float *buf = malloc((size_t)bytes);
    if (!buf || fread(buf, 1, (size_t)bytes, f) != (size_t)bytes) { fclose(f); free(buf); return NULL; }
    fclose(f);
    *n = bytes / (long)sizeof(float);
    return buf;
}

int main(int argc, char **argv)
{
    if (argc < 5) { fprintf(stderr, "usage: %s MODEL.onnx T WINDOWS.f32 PROBS.f32 [BATCH]\n", argv[0]); return 2; }
    int T = atoi(argv[2]), batch = argc > 5 ? atoi(argv[5]) : 8;
    long nw = 0, np = 0;
    float *w = slurp(argv[3], &nw), *ref = slurp(argv[4], &np);
    if (!w || !ref) return 1;
    int per = STGCN_CHANNELS * T * STGCN_JOINTS, n = (int)(nw / per);
    if (n != np) { fprintf(stderr, "%d windows but %ld probabilities\n", n, np); return 1; }
    char err[256];
    StgcnModel *m = stgcn_open(argv[1], T, batch, err, sizeof err);
    if (!m) { fprintf(stderr, "%s\n", err); return 1; }
    float *got = malloc(sizeof(float) * (size_t)n);
    double worst = 0, total_us = 0;
    int batches = 0;
    for (int i = 0; i < n; i += batch) {
        int k = n - i < batch ? n - i : batch;
        if (stgcn_run(m, w + (size_t)i * per, k, got + i, err, sizeof err) != 0) { fprintf(stderr, "%s\n", err); return 1; }
        total_us += stgcn_last_run_us(m);
        batches++;
    }
    for (int i = 0; i < n; i++) worst = fmax(worst, fabs(got[i] - ref[i]));
    printf("%d windows, batch %d: max |TensorRT FP16 - reference| = %.2e (probability), %.0f us per batch (incl. copies)\n", n, batch, worst, total_us / batches);
    /* timing of every batch size, 200 runs each */
    for (int b = 1; b <= batch; b *= 2) {
        int k = b <= n ? b : n;
        double t = 0;
        for (int r = 0; r < 200; r++) { stgcn_run(m, w, k, got, err, sizeof err); t += stgcn_last_run_us(m); }
        printf("  batch %2d: %.0f us\n", k, t / 200);
    }
    stgcn_close(m);
    return worst < 5e-3 ? 0 : 3;
}
