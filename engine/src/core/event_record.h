/*
 * @author: Ngoc Luong <ngocluongg0101@gmail.com>
 */

/*
 * event_record.h - event schema v1 (ROADMAP 2.3): the record the engine hands to the backend, and its JSON text.
 *
 * Pure C. The JSON is written by hand (testable on the host, no locale: numbers use integer arithmetic, time is converted from
 * ns since the epoch without gmtime()).
 *
 * {"schema_version":1,"event_id":"<uuid>","camera_id":1,"pipeline_run_id":"<uuid>",
 * "type":"PERSON_DETECTED","track_id":12,"confidence":0.9100,"timestamp":"2026-01-01T12:00:00.123Z",
 * "bbox":[0.1000,0.2000,0.3000,0.4000],"snapshot":null}
 *
 * Behaviour events (V2-3, schema 2) have "type":"FALL" | "FIGHT" | "RESTRICTED_AREA" and three more fields:
 * "track_ids":[12],"occurred_at":"2026-01-01T11:59:58.100Z","zone_id":null
 * so a schema-1 consumer still reads them. occurred_at is when the behaviour began, timestamp when it was reported.
 *
 * event_id lets the backend ignore a repeated delivery. pipeline_run_id changes whenever the pipeline is rebuilt (track IDs
 * restart then), so (pipeline_run_id, camera_id, track_id) identifies a person. snapshot is a file name (no directory) or null.
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
 * Write the JSON object for r to out as one line, NUL terminated. Returns its length, or -1 if it does not fit in cap bytes
 * (EVENT_JSON_MAX always suffices). Confidence and bbox values outside 0..1 (and NaN) are clamped.
 */
#define EVENT_JSON_MAX 1536   /* fixed part ~300 bytes + a snapshot name of 191 chars at 6 bytes each */
int event_record_to_json(const EventRecord *r, char *out, size_t cap);

/*
 * File name of the snapshot of this event: <TYPE>_cam<camera_id>_id<track_id>_<YYYYMMDD>T<HHMMSS>_<first 8 of event_id>.jpg
 * (TYPE = PERSON_DETECTED, FALL, FIGHT or RESTRICTED_AREA; UTC; SnapshotCleaner deletes these by age). Returns the length, or -1
 * (and an empty out) if it does not fit in cap bytes; EVENT_SNAPSHOT_NAME_MAX always suffices.
 */
int event_snapshot_name(const EventRecord *r, char *out, size_t cap);

#endif /* EVENT_RECORD_H */
