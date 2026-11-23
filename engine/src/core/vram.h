/*
 * @author: Ngoc Luong <ngocluongg0101@gmail.com>
 */

#ifndef CORE_VRAM_H
#define CORE_VRAM_H

#include <stdint.h>

/*
 * GPU memory (MiB) the engine needs on top of what is in use, for `slots` camera places, with or without the video output (one NVENC
 * encoder per camera). Fitted to the V2-7 measurements (pose model + fall network + frame ring, 1080p: ~370 MiB for 1 place, ~850 for
 * 4, ~1460 for 8, ~2220 for 12 with video) plus 15 %. Used to refuse a start that cannot work: when the GPU runs out of memory
 * DeepStream does not fail cleanly, it ends in CUDA errors and a crash.
 */
int64_t vram_needed_mib(unsigned slots, int with_stream);

/* Whether `free_mib` is enough for it (1) or not (0). */
int vram_is_enough(int64_t free_mib, unsigned slots, int with_stream);

#endif
