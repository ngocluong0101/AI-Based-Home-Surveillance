#include "backoff.h"

uint32_t backoff_next_ms(uint32_t current_ms)
{
    if (current_ms < BACKOFF_BASE_MS)
        return BACKOFF_BASE_MS;
    if (current_ms >= BACKOFF_MAX_MS / 2)
        return BACKOFF_MAX_MS;
    return current_ms * 2;
}
