/*
 * event_spool.h - the file the publisher parks events in while the backend cannot be reached
 * (ROADMAP 2.6).
 *
 * Pure C (stdio + POSIX). One JSON event per line (JSON-lines), appended in arrival order and read
 * back in the same order. Nothing is lost on a crash or restart: the file is flushed to disk after
 * every append and opening an existing spool resumes with its content. The backend ignores an event
 * it already has (same event_id), so an event that was delivered but not yet removed from the spool
 * when the engine died is simply sent again.
 *
 * Not thread safe: the publisher thread is its only user.
 *
 * The read position is kept in memory; the consumed part of the file is released only when the
 * whole spool has been delivered (the file is then truncated). The file size is therefore capped
 * as a whole, which is enough because events are rare and a reachable backend empties it quickly.
 */
#ifndef EVENT_SPOOL_H
#define EVENT_SPOOL_H

#include <stddef.h>
#include <stdint.h>

typedef struct EventSpool EventSpool;

/* Open (create if missing) the spool at `path`. Appends are refused once the file would exceed
 * `max_bytes`. Returns NULL if the file cannot be opened. */
EventSpool *event_spool_open(const char *path, uint64_t max_bytes);

void event_spool_close(EventSpool *s);   /* NULL is allowed */

/* Append one event (a line of JSON without newline; must not contain '\n'). Returns 0, -1 on an
 * I/O error, or -2 when the spool is full. */
int event_spool_append(EventSpool *s, const char *line);

/*
 * Copy the oldest undelivered line (newline stripped, NUL terminated) into `line`, without
 * removing it. Returns 1 if there is one, 0 if the spool is empty, -1 on an I/O error. Blank lines
 * and lines longer than cap - 1 bytes cannot be sent and are skipped; they count in
 * event_spool_skipped().
 */
int event_spool_peek(EventSpool *s, char *line, size_t cap);

/* The line returned by the last event_spool_peek() was delivered: forget it. */
void event_spool_pop(EventSpool *s);

int event_spool_empty(const EventSpool *s);        /* 1 when nothing is waiting to be delivered */
uint64_t event_spool_skipped(const EventSpool *s);

#endif /* EVENT_SPOOL_H */
