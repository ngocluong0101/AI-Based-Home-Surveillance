/*
 * @author: Ngoc Luong <ngocluongg0101@gmail.com>
 */

/*
 * backoff.h - how long the publisher waits before retrying a dead backend (ROADMAP 2.6). Pure C. 1 s after the first failure,
 * doubling, never more than 60 s.
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
