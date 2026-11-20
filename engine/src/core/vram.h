#ifndef CORE_VRAM_H
#define CORE_VRAM_H

#include <stdint.h>

/* How much GPU memory the engine needs on top of what is already in use, in MiB, for `slots` camera places (the batch size of
 * nvinfer / nvstreammux) with or without the video output (one NVENC encoder per camera). Fitted to the measurements of V2-7
 * (pose model + fall network + frame ring, 1080p cameras: ~370 MiB for 1 place, ~850 for 4, ~1460 for 8, ~2220 for 12 with the video)
 * plus a margin of 15 %. A starting point to refuse a start that cannot work, not a guarantee: another GPU user may still grow.
 * When the GPU runs out of memory DeepStream does not fail cleanly: allocation errors turn into CUDA errors and finally a crash. */
int64_t vram_needed_mib(unsigned slots, int with_stream);

/* Whether `free_mib` is enough for it (1) or not (0). */
int vram_is_enough(int64_t free_mib, unsigned slots, int with_stream);

#endif
