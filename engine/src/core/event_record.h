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
 * Behaviour events (ROADMAP_V2 V2-3) are schema 2: the same fields with "type":"FALL" | "FIGHT" | "RESTRICTED_AREA"
 * (confidence = the score of the rule) and three more, after "track_id" / "timestamp":
 *  "track_ids":[12],"occurred_at":"2026-01-01T11:59:58.100Z","zone_id":null
 * so a consumer that knows schema 1 still reads them (it ignores what it does not know). occurred_at is when the
 * behaviour began, timestamp when it was reported (a fall is reported two seconds after the impact).
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

#define EVENT_RECORD_SCHEMA_VERSION 1       /* PERSON_DETECTED: byte for byte what it always was */
#define EVENT_RECORD_SCHEMA_VERSION_BEHAVIOUR 2   /* FALL / FIGHT / RESTRICTED_AREA: v1 plus track_ids, occurred_at, zone_id */
#define EVENT_UUID_LEN 36                 /* "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx", without the NUL */
#define EVENT_SNAPSHOT_NAME_MAX 192       /* including the NUL */

/* What happened. PERSON_DETECTED is 0, so a zeroed record is the v1 event. */
typedef enum { EVENT_PERSON_DETECTED = 0, EVENT_FALL, EVENT_FIGHT, EVENT_RESTRICTED_AREA } EventType;

const char *event_type_name(EventType t);

typedef struct {
    EventType type;
    /* Behaviour events only (type != PERSON_DETECTED). The person's track_id above is the first of track_ids. */
    uint64_t track_ids[2];                /* a fight has two */
    int num_track_ids;                    /* 1 or 2; 0 = only track_id */
    int has_zone;                         /* a restricted-area event names its zone */
    int32_t zone_id;
    int64_t occurred_ns;                  /* when the behaviour began (a fall: the impact), UTC ns; timestamp_ns is when it was reported */
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
 *   <TYPE>_cam<camera_id>_id<track_id>_<YYYYMMDD>T<HHMMSS>_<first 8 characters of event_id>.jpg
 * where TYPE is PERSON_DETECTED, FALL, FIGHT or RESTRICTED_AREA. The time is UTC. SnapshotCleaner deletes these by age. Returns
 * the length, or -1 (and an empty out) if it does not fit in cap bytes; EVENT_SNAPSHOT_NAME_MAX
 * always suffices.
 */
int event_snapshot_name(const EventRecord *r, char *out, size_t cap);

#endif /* EVENT_RECORD_H */
