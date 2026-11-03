/*
 * Test runner for the pure-C code (engine/src/core/), which does not depend on DeepStream.
 *
 * To add a test: write a `void test_xxx(void)` that uses CHECK(...) (test_util.h) in
 * tests/<name>.c, declare it here and add it to the TESTS table. The exit code is non-zero
 * if any test fails.
 */
#include <stdio.h>

#include "test_util.h"

int g_failures = 0;

typedef void (*test_fn)(void);

typedef struct {
    const char *name;
    test_fn fn;
} TestCase;

/* tests/test_detection.c */
void test_bbox_normalize_inside_frame(void);
void test_bbox_normalize_clips_to_frame(void);
void test_bbox_normalize_rejects_unusable_boxes(void);

/* tests/test_event_manager.c */
void test_event_manager_shared_vectors(void);
void test_event_manager_output_capacity(void);
void test_event_manager_destroy_is_safe(void);
void test_event_manager_instances_are_independent(void);

/* tests/test_event_record.c */
void test_event_record_golden_json(void);
void test_event_record_json_parses_back(void);
void test_event_record_timestamp_is_utc(void);
void test_event_record_numbers_are_clamped_and_rounded(void);
void test_event_record_small_buffer_fails_cleanly(void);
void test_event_snapshot_name(void);

/* tests/test_event_spool.c */
void test_event_spool_keeps_order_and_empties_the_file(void);
void test_event_spool_survives_a_restart(void);
void test_event_spool_repairs_a_torn_last_line(void);
void test_event_spool_refuses_when_full(void);
void test_event_spool_skips_unsendable_lines(void);
void test_event_spool_open_fails_on_bad_path(void);
void test_backoff_doubles_and_is_capped(void);

/* tests/test_heartbeat_record.c */
void test_heartbeat_golden_json(void);
void test_heartbeat_json_parses_back(void);
void test_heartbeat_ended_state_is_written_as_is(void);
void test_heartbeat_people_is_a_number_or_absent(void);
void test_heartbeat_numbers_are_always_valid_json(void);
void test_heartbeat_escapes_the_run_id(void);
void test_heartbeat_capacity_formula_suffices(void);
void test_heartbeat_small_buffer_fails_cleanly(void);

static const TestCase TESTS[] = {
    { "bbox_normalize_inside_frame", test_bbox_normalize_inside_frame },
    { "bbox_normalize_clips_to_frame", test_bbox_normalize_clips_to_frame },
    { "bbox_normalize_rejects_unusable_boxes", test_bbox_normalize_rejects_unusable_boxes },
    { "event_manager_shared_vectors", test_event_manager_shared_vectors },
    { "event_manager_output_capacity", test_event_manager_output_capacity },
    { "event_manager_destroy_is_safe", test_event_manager_destroy_is_safe },
    { "event_manager_instances_are_independent", test_event_manager_instances_are_independent },
    { "event_record_golden_json", test_event_record_golden_json },
    { "event_record_json_parses_back", test_event_record_json_parses_back },
    { "event_record_timestamp_is_utc", test_event_record_timestamp_is_utc },
    { "event_record_numbers_are_clamped_and_rounded", test_event_record_numbers_are_clamped_and_rounded },
    { "event_record_small_buffer_fails_cleanly", test_event_record_small_buffer_fails_cleanly },
    { "event_snapshot_name", test_event_snapshot_name },
    { "event_spool_keeps_order_and_empties_the_file", test_event_spool_keeps_order_and_empties_the_file },
    { "event_spool_survives_a_restart", test_event_spool_survives_a_restart },
    { "event_spool_repairs_a_torn_last_line", test_event_spool_repairs_a_torn_last_line },
    { "event_spool_refuses_when_full", test_event_spool_refuses_when_full },
    { "event_spool_skips_unsendable_lines", test_event_spool_skips_unsendable_lines },
    { "event_spool_open_fails_on_bad_path", test_event_spool_open_fails_on_bad_path },
    { "backoff_doubles_and_is_capped", test_backoff_doubles_and_is_capped },
    { "heartbeat_golden_json", test_heartbeat_golden_json },
    { "heartbeat_json_parses_back", test_heartbeat_json_parses_back },
    { "heartbeat_ended_state_is_written_as_is", test_heartbeat_ended_state_is_written_as_is },
    { "heartbeat_people_is_a_number_or_absent", test_heartbeat_people_is_a_number_or_absent },
    { "heartbeat_numbers_are_always_valid_json", test_heartbeat_numbers_are_always_valid_json },
    { "heartbeat_escapes_the_run_id", test_heartbeat_escapes_the_run_id },
    { "heartbeat_capacity_formula_suffices", test_heartbeat_capacity_formula_suffices },
    { "heartbeat_small_buffer_fails_cleanly", test_heartbeat_small_buffer_fails_cleanly },
    { NULL, NULL }, /* table terminator */
};

int main(void)
{
    int run = 0;

    for (const TestCase *t = TESTS; t->name != NULL; t++) {
        printf("RUN  %s\n", t->name);
        t->fn();
        run++;
    }

    printf("%d tests, %d failed\n", run, g_failures);
    return g_failures == 0 ? 0 : 1;
}
