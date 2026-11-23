/*
 * @author: Ngoc Luong <ngocluongg0101@gmail.com>
 */

/*
 * event_manager.h - PERSON_DETECTED de-duplication, C port of src/events/person_detected.py (the specification; both run
 * tests/vectors/event_manager_*.json).
 *
 * A tracker reports a person on every frame; this turns it into ONE event per appearance: when a track ID is first seen, or
 * again after it was absent for longer than absence_timeout. Pure C, no clock: the caller passes `now` in ns (stream start + the
 * frame's PTS). One instance per source (track IDs are unique only inside one source), used from one thread.
 */
#ifndef EVENT_MANAGER_H
#define EVENT_MANAGER_H

#include <stddef.h>
#include <stdint.h>

#include "detection.h"

/* What the manager emits: one person appeared. */
typedef struct {
    uint32_t source_id;    /* copied from the Detection */
    uint64_t track_id;
    float confidence;      /* confidence of the detection that started the appearance */
    float bbox[4];         /* x, y, w, h relative to the frame, of that same detection */
    int64_t timestamp_ns;  /* the `now` of the call that emitted it */
} PersonDetectedEvent;

typedef struct {
    uint64_t track_id;
    int64_t last_seen_ns;
    uint64_t seen_in_call;  /* value of EventManager.calls when the track was last updated */
} EventManagerTrack;

typedef struct {
    int64_t absence_timeout_ns;
    EventManagerTrack *tracks;  /* unordered; owned, grown on demand */
    size_t num_tracks;
    size_t cap_tracks;
    uint64_t calls;             /* number of event_manager_process() calls so far */
} EventManager;

/*
 * Set up an empty manager. absence_timeout_s is in seconds, rounded to whole nanoseconds. Never fails: the track table is
 * allocated on first use.
 */
void event_manager_init(EventManager *em, double absence_timeout_s);

/*
 * Change the timeout of a manager in use (camera settings edited while the engine runs); remembered tracks are judged against
 * the new value from the next call. Must not run at the same time as event_manager_process() on the same manager.
 */
void event_manager_set_absence_timeout(EventManager *em, double absence_timeout_s);

/* Free the track table. The manager is empty and reusable by event_manager_init() afterwards;
 * calling it again, or on a zeroed struct, is harmless. */
void event_manager_destroy(EventManager *em);

/*
 * Process the detections of one frame (same source, in report order) at now_ns and write the events they trigger to out, in the
 * same order. Each detection triggers at most one event, so out needs num_dets places; with fewer (out_cap) the rest are dropped
 * but the state is still updated. Returns the number written, or -1 if memory ran out (nothing changed). A track absent longer
 * than the timeout is forgotten at the end of the call; a clock that goes backwards never counts as an absence.
 */
int event_manager_process(EventManager *em, const Detection *dets, size_t num_dets,
                          int64_t now_ns, PersonDetectedEvent *out, size_t out_cap);

/* Number of tracks currently remembered. */
size_t event_manager_track_count(const EventManager *em);

#endif /* EVENT_MANAGER_H */
