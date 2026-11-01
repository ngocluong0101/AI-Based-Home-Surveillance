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

static const TestCase TESTS[] = {
    { "bbox_normalize_inside_frame", test_bbox_normalize_inside_frame },
    { "bbox_normalize_clips_to_frame", test_bbox_normalize_clips_to_frame },
    { "bbox_normalize_rejects_unusable_boxes", test_bbox_normalize_rejects_unusable_boxes },
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
