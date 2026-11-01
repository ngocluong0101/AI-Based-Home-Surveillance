/*
 * event_record.h - event schema v1 (ROADMAP 2.3): the record the engine hands to the backend, and its
 * JSON text.
 *
 * Pure C. The JSON is written by hand instead of with json-glib so that it can be tested on the host
 * and does not depend on the locale: numbers are printed by integer arithmetic (no "0,91"), the time
 * is converted from ns since the Unix epoch without gmtime()/timezones.
 *
 * {"schema_version":1,"event_id":"<uuid>","camera_id":1,"pipeline_run_id":"<uuid>",
 *  "type":"PERSON_DETECTED","track_id":12,"confidence":0.9100,"timestamp":"2026-01-01T12:00:00.123Z",
 *  "bbox":[0.1000,0.2000,0.3000,0.4000],"snapshot":null}
 *
 * event_id is unique per event and is what lets the backend ignore a repeated delivery;
 * pipeline_run_id changes every time the engine (re)builds its pipeline, because track IDs restart
 * then, so (pipeline_run_id, camera_id, track_id) identifies a person. snapshot is the file name of
 * the JPEG (no directory), or null while there is none.
 */
#ifndef EVENT_RECORD_H
#define EVENT_RECORD_H

#include <stddef.h>
#include <stdint.h>

#define EVENT_RECORD_SCHEMA_VERSION 1
#define EVENT_UUID_LEN 36                 /* "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx", without the NUL */
#define EVENT_SNAPSHOT_NAME_MAX 192       /* including the NUL */

typedef struct {
    char event_id[EVENT_UUID_LEN + 1];
    char pipeline_run_id[EVENT_UUID_LEN + 1];
    int32_t camera_id;
    uint64_t track_id;
    float confidence;                     /* 0..1 */
    int64_t timestamp_ns;                 /* UTC, ns since the Unix epoch */
    float bbox[4];                        /* x, y, w, h relative to the frame, each 0..1 */
    char snapshot[EVENT_SNAPSHOT_NAME_MAX];  /* "" = no snapshot */
} EventRecord;

/*
 * Write the JSON object for r to out as one line without a trailing newline, NUL terminated.
 * Returns its length (without the NUL), or -1 if it does not fit in cap bytes; EVENT_JSON_MAX
 * always suffices. Numbers outside 0..1 (and NaN) are clamped for confidence and bbox.
 */
#define EVENT_JSON_MAX 1536   /* fixed part ~300 bytes + a snapshot name of 191 chars at 6 bytes each */
int event_record_to_json(const EventRecord *r, char *out, size_t cap);

/*
 * File name (no directory) of the snapshot of this event:
 *   PERSON_DETECTED_cam<camera_id>_id<track_id>_<YYYYMMDD>T<HHMMSS>_<first 8 characters of event_id>.jpg
 * The time is UTC. It matches PERSON_DETECTED_*.jpg, which SnapshotCleaner deletes by age. Returns
 * the length, or -1 (and an empty out) if it does not fit in cap bytes; EVENT_SNAPSHOT_NAME_MAX
 * always suffices.
 */
int event_snapshot_name(const EventRecord *r, char *out, size_t cap);

#endif /* EVENT_RECORD_H */
