/*
 * @author: Ngoc Luong <ngocluongg0101@gmail.com>
 */

#include "vram.h"

int64_t vram_needed_mib(unsigned slots, int with_stream)
{
    if (slots == 0)
        slots = 1;
    /* per place: the batch buffers, the tracker, the decoder surfaces; the video adds the encoder's buffers */
    const int64_t fixed = with_stream ? 200 : 250;
    const int64_t per_slot = with_stream ? 170 : 90;
    const int64_t raw = fixed + per_slot * (int64_t)slots;
    return raw + raw * 15 / 100;
}

int vram_is_enough(int64_t free_mib, unsigned slots, int with_stream)
{
    return free_mib >= vram_needed_mib(slots, with_stream);
}
