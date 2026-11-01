#include "event_manager.h"

#include <stdlib.h>
#include <string.h>

#define INITIAL_TRACK_CAPACITY 32

/* Timeouts beyond this (about 146 years) are clamped so the conversion to ns cannot overflow. */
#define MAX_TIMEOUT_NS (INT64_MAX / 2)

void event_manager_init(EventManager *em, double absence_timeout_s)
{
    double ns = absence_timeout_s * 1e9;

    em->absence_timeout_ns = ns >= (double)MAX_TIMEOUT_NS    ? MAX_TIMEOUT_NS
                             : ns <= -(double)MAX_TIMEOUT_NS ? -MAX_TIMEOUT_NS
                             : (int64_t)(ns + (ns >= 0 ? 0.5 : -0.5));
    em->tracks = NULL;
    em->num_tracks = 0;
    em->cap_tracks = 0;
    em->calls = 0;
}

void event_manager_destroy(EventManager *em)
{
    free(em->tracks);
    em->tracks = NULL;
    em->num_tracks = 0;
    em->cap_tracks = 0;
}

size_t event_manager_track_count(const EventManager *em)
{
    return em->num_tracks;
}

/* Make room for `needed` tracks in total. Returns 0 on success, -1 if memory ran out. */
static int reserve_tracks(EventManager *em, size_t needed)
{
    if (needed <= em->cap_tracks)
        return 0;

    size_t cap = em->cap_tracks > 0 ? em->cap_tracks : INITIAL_TRACK_CAPACITY;
    while (cap < needed) {
        if (cap > SIZE_MAX / 2 / sizeof(EventManagerTrack))
            return -1;
        cap *= 2;
    }
    EventManagerTrack *tracks = realloc(em->tracks, cap * sizeof(*tracks));
    if (tracks == NULL)
        return -1;
    em->tracks = tracks;
    em->cap_tracks = cap;
    return 0;
}

static EventManagerTrack *find_track(EventManager *em, uint64_t track_id)
{
    for (size_t i = 0; i < em->num_tracks; i++) {
        if (em->tracks[i].track_id == track_id)
            return &em->tracks[i];
    }
    return NULL;
}

/* Same test as Python _is_absent_too_long: strictly longer than the timeout. */
static int absent_too_long(const EventManager *em, int64_t last_seen_ns, int64_t now_ns)
{
    return now_ns - last_seen_ns > em->absence_timeout_ns;
}

int event_manager_process(EventManager *em, const Detection *dets, size_t num_dets,
                          int64_t now_ns, PersonDetectedEvent *out, size_t out_cap)
{
    /* Worst case every detection is a new track: reserve once so nothing below can fail halfway. */
    if (num_dets > SIZE_MAX - em->num_tracks || reserve_tracks(em, em->num_tracks + num_dets) != 0)
        return -1;

    em->calls++;
    size_t emitted = 0;

    for (size_t i = 0; i < num_dets; i++) {
        const Detection *d = &dets[i];
        EventManagerTrack *track = find_track(em, d->track_id);

        if (track == NULL || absent_too_long(em, track->last_seen_ns, now_ns)) {
            if (emitted < out_cap) {
                out[emitted].source_id = d->source_id;
                out[emitted].track_id = d->track_id;
                out[emitted].confidence = d->confidence;
                memcpy(out[emitted].bbox, d->bbox, sizeof(d->bbox));
                out[emitted].timestamp_ns = now_ns;
                emitted++;
            }
        }

        if (track == NULL) {
            track = &em->tracks[em->num_tracks++];
            track->track_id = d->track_id;
        }
        track->last_seen_ns = now_ns;
        track->seen_in_call = em->calls;
    }

    /* Forget tracks that were not in this frame and have been gone for longer than the timeout. */
    for (size_t i = 0; i < em->num_tracks;) {
        const EventManagerTrack *t = &em->tracks[i];
        if (t->seen_in_call != em->calls && absent_too_long(em, t->last_seen_ns, now_ns))
            em->tracks[i] = em->tracks[--em->num_tracks];  /* order does not matter */
        else
            i++;
    }

    return (int)emitted;
}
