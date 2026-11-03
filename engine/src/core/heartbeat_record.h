/*
 * heartbeat_record.h - the heartbeat the engine POSTs to the backend every few seconds (ROADMAP 3.6):
 * one per process, with the state of every camera in it.
 *
 * {"schema_version":1,"run_id":"<uuid>","cameras":[
 *   {"camera_id":1,"state":"RUNNING","fps":28.4,"last_frame_age":0.030,"people":2},
 *   {"camera_id":2,"state":"RECONNECTING","fps":0.0,"last_frame_age":12.500}]}
 *
 * "people" is how many tracked persons the latest frame of that camera held (the same people the events
 * are made from). It is left out when the engine does not know (no frame yet, or none for a while: then
 * 0 would claim an empty room), and an older engine never sends it.
 *
 * Pure C like event_record.h: written by hand, numbers printed by integer arithmetic so the locale
 * cannot turn "28.4" into "28,4". run_id is the pipeline_run_id of the events, so the backend can tell
 * the heartbeats of a new run from those of the one before.
 */
#ifndef HEARTBEAT_RECORD_H
#define HEARTBEAT_RECORD_H

#include <stddef.h>
#include <stdint.h>

#define HEARTBEAT_SCHEMA_VERSION 1
#define HEARTBEAT_STATE_MAX 16   /* including the NUL: "RECONNECTING" fits */

typedef struct {
    int32_t camera_id;
    char state[HEARTBEAT_STATE_MAX];   /* "RUNNING", "RECONNECTING" or "ENDED" (a file that has been played to the end) */
    double fps;                        /* frames per second over the last interval, >= 0 */
    double last_frame_age;             /* seconds since the last frame of this camera, >= 0 */
    int32_t people;                    /* persons in the latest frame; < 0 = unknown, left out of the JSON */
} HeartbeatCamera;

/* Largest "people" printed (the backend refuses more); bigger values are clamped to it. */
#define HEARTBEAT_PEOPLE_MAX 100000

/* A buffer of this many bytes always holds the JSON for `cameras` cameras. */
#define HEARTBEAT_JSON_FIXED 128
#define HEARTBEAT_JSON_PER_CAMERA 160
static inline size_t heartbeat_json_capacity(size_t cameras)
{
    return HEARTBEAT_JSON_FIXED + cameras * HEARTBEAT_JSON_PER_CAMERA;
}

/*
 * Write the JSON object to out as one line without a trailing newline, NUL terminated. Returns its
 * length (without the NUL), or -1 if it does not fit in cap bytes; heartbeat_json_capacity(n) always
 * suffices. fps is printed with 1 decimal and last_frame_age with 3; NaN and negative values become
 * 0, and values above 1e9 are clamped, so the output is always a valid JSON number.
 */
int heartbeat_to_json(const char *run_id, const HeartbeatCamera *cameras, size_t count, char *out,
                      size_t cap);

#endif /* HEARTBEAT_RECORD_H */
