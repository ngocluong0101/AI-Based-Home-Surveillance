/*
 * event_manager.h - PERSON_DETECTED de-duplication, C port of src/events/person_detected.py.
 *
 * A tracker reports the same person on every frame; the manager turns that into ONE event per
 * appearance: an event is emitted when a track ID is seen for the first time, or again after the
 * track was absent for longer than absence_timeout. The Python PersonDetectedEventManager is the
 * specification, and both are run against the same vectors (tests/vectors/event_manager_*.json).
 *
 * Pure C, no DeepStream or GLib, no clock of its own: the caller passes `now` (in ns), which in the
 * engine is stream_start[source_id] + the frame's PTS. One instance per source: track IDs are only
 * unique inside one source. Not thread-safe; an instance is used from one thread (the pad probe).
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
 * Set up an empty manager. absence_timeout_s is in seconds, like the Python constructor argument
 * (10.0 by default in the config); it is rounded to whole nanoseconds. Never fails: the track
 * table is allocated on first use.
 */
void event_manager_init(EventManager *em, double absence_timeout_s);

/* Free the track table. The manager is empty and reusable by event_manager_init() afterwards;
 * calling it again, or on a zeroed struct, is harmless. */
void event_manager_destroy(EventManager *em);

/*
 * Process the detections of one frame (all from the same source, in the order they should be
 * reported) at time now_ns, and write the events they trigger to out, in the same order. Each
 * detection triggers at most one event, so out needs room for num_dets events; if it has less
 * (out_cap), the remaining events are dropped but the state is still updated. Returns the number
 * of events written, or -1 if memory for the tracks ran out; the call then changed nothing and
 * out is not valid.
 *
 * A track absent longer than the timeout is forgotten at the end of the call. A clock that goes
 * backwards never counts as an absence, as in the Python version.
 */
int event_manager_process(EventManager *em, const Detection *dets, size_t num_dets,
                          int64_t now_ns, PersonDetectedEvent *out, size_t out_cap);

/* Number of tracks currently remembered. */
size_t event_manager_track_count(const EventManager *em);

#endif /* EVENT_MANAGER_H */
