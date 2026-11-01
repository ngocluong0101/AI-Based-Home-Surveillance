#include "detection.h"
#include "test_util.h"

void test_bbox_normalize_inside_frame(void)
{
    float b[4] = { -1, -1, -1, -1 };

    CHECK(detection_bbox_normalize(192.0f, 108.0f, 384.0f, 216.0f, 1920.0f, 1080.0f, b) == 1);
    CHECK_NEAR(b[0], 0.1f, 1e-6f);
    CHECK_NEAR(b[1], 0.1f, 1e-6f);
    CHECK_NEAR(b[2], 0.2f, 1e-6f);
    CHECK_NEAR(b[3], 0.2f, 1e-6f);
}

void test_bbox_normalize_clips_to_frame(void)
{
    float b[4] = { 0 };

    /* sticks out on the left/top */
    CHECK(detection_bbox_normalize(-100.0f, -50.0f, 300.0f, 150.0f, 1000.0f, 500.0f, b) == 1);
    CHECK_NEAR(b[0], 0.0f, 1e-6f);
    CHECK_NEAR(b[1], 0.0f, 1e-6f);
    CHECK_NEAR(b[2], 0.2f, 1e-6f);   /* 200 px of 1000 */
    CHECK_NEAR(b[3], 0.2f, 1e-6f);   /* 100 px of 500 */

    /* sticks out on the right/bottom */
    CHECK(detection_bbox_normalize(900.0f, 400.0f, 300.0f, 300.0f, 1000.0f, 500.0f, b) == 1);
    CHECK_NEAR(b[0], 0.9f, 1e-6f);
    CHECK_NEAR(b[1], 0.8f, 1e-6f);
    CHECK_NEAR(b[2], 0.1f, 1e-6f);
    CHECK_NEAR(b[3], 0.2f, 1e-6f);
}

void test_bbox_normalize_rejects_unusable_boxes(void)
{
    float b[4] = { 7, 7, 7, 7 };

    CHECK(detection_bbox_normalize(10.0f, 10.0f, 0.0f, 50.0f, 100.0f, 100.0f, b) == 0);      /* no width */
    CHECK(detection_bbox_normalize(10.0f, 10.0f, 50.0f, -5.0f, 100.0f, 100.0f, b) == 0);     /* negative height */
    CHECK(detection_bbox_normalize(200.0f, 10.0f, 50.0f, 50.0f, 100.0f, 100.0f, b) == 0);    /* right of the frame */
    CHECK(detection_bbox_normalize(-80.0f, 10.0f, 50.0f, 50.0f, 100.0f, 100.0f, b) == 0);    /* left of the frame */
    CHECK(detection_bbox_normalize(10.0f, 10.0f, 50.0f, 50.0f, 0.0f, 100.0f, b) == 0);       /* bad frame size */
    /* out is untouched on failure */
    CHECK_NEAR(b[0], 7.0f, 0.0f);
    CHECK_NEAR(b[3], 7.0f, 0.0f);
}
