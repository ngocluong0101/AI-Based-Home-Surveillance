/*
 * backoff.h - how long the publisher waits before it tries a dead backend again (ROADMAP 2.6).
 *
 * Pure C. Exponential: 1 s after the first failure, doubling after every further one, never more
 * than 60 s, so a backend that comes back is found again within a minute.
 */
#ifndef BACKOFF_H
#define BACKOFF_H

#include <stdint.h>

#define BACKOFF_BASE_MS 1000u
#define BACKOFF_MAX_MS 60000u

/* The delay after one more failure; `current_ms` is the delay used after the previous failure
 * (0 = this is the first failure). */
uint32_t backoff_next_ms(uint32_t current_ms);

#endif /* BACKOFF_H */
