/*
 * @author: Ngoc Luong <ngocluongg0101@gmail.com>
 */

/* Host test of yolov8_pose_decode.h: g++ only, no DeepStream. make -C engine/parser test */
#include <cmath>
#include <cstdio>
#include <vector>

#include "../yolov8_pose_decode.h"

static int failures = 0;
#define CHECK(cond)                                                                      \
    do {                                                                                 \
        if (!(cond)) {                                                                   \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);                  \
            failures++;                                                                  \
        }                                                                                \
    } while (0)
static bool near(float a, float b, float eps = 1e-4f) { return std::fabs(a - b) <= eps; }

/* a tensor of `anchors` anchors, all zero */
static std::vector<float> blank(unsigned anchors) { return std::vector<float>(pose::kRows * anchors, 0.0f); }
static void put(std::vector<float> &t, unsigned anchors, unsigned i, float cx, float cy, float w, float h, float s)
{
    t[0 * anchors + i] = cx; t[1 * anchors + i] = cy; t[2 * anchors + i] = w; t[3 * anchors + i] = h;
    t[4 * anchors + i] = s;
}
static void kpt(std::vector<float> &t, unsigned anchors, unsigned i, unsigned k, float x, float y, float v)
{
    t[(5 + 3 * k) * anchors + i] = x; t[(6 + 3 * k) * anchors + i] = y; t[(7 + 3 * k) * anchors + i] = v;
}

static void test_threshold_nms_order_and_relative_keypoints()
{
    const unsigned A = 8;
    std::vector<float> t = blank(A);
    put(t, A, 0, 320, 192, 100, 200, 0.90f);      /* box (270,92)-(370,292) */
    kpt(t, A, 0, 0, 320, 92, 0.95f);              /* nose at the top centre of the box */
    kpt(t, A, 0, 16, 370, 292, 0.5f);             /* right ankle at the bottom right corner */
    put(t, A, 1, 322, 192, 100, 200, 0.80f);      /* same person, worse score: removed by NMS */
    put(t, A, 2, 100, 100, 40, 40, 0.30f);        /* a second person, far away */
    put(t, A, 3, 500, 300, 40, 40, 0.10f);        /* under the threshold */
    std::vector<pose::Person> out;
    CHECK(pose::decode(t.data(), A, 640, 384, 0.25f, 0.45f, 300, out));
    CHECK(out.size() == 2);
    CHECK(near(out[0].score, 0.90f) && near(out[1].score, 0.30f));   /* best first */
    CHECK(near(out[0].left, 270) && near(out[0].top, 92) && near(out[0].width, 100) && near(out[0].height, 200));
    CHECK(near(out[0].kpt[0], 0.5f) && near(out[0].kpt[1], 0.0f) && near(out[0].kpt[2], 0.95f));        /* nose */
    CHECK(near(out[0].kpt[3 * 16 + 0], 1.0f) && near(out[0].kpt[3 * 16 + 1], 1.0f));                    /* ankle */
}

static void test_box_is_clipped_and_keypoints_follow_the_clipped_box()
{
    const unsigned A = 2;
    std::vector<float> t = blank(A);
    put(t, A, 0, 10, 10, 100, 100, 0.9f);          /* sticks out to the left and top: (-40,-40)-(60,60) */
    kpt(t, A, 0, 0, 30, 30, 1.0f);
    std::vector<pose::Person> out;
    CHECK(pose::decode(t.data(), A, 640, 384, 0.25f, 0.45f, 300, out));
    CHECK(out.size() == 1);
    CHECK(near(out[0].left, 0) && near(out[0].top, 0) && near(out[0].width, 60) && near(out[0].height, 60));
    CHECK(near(out[0].kpt[0], 0.5f) && near(out[0].kpt[1], 0.5f));   /* (30-0)/60 */
}

static void test_limits_and_bad_arguments()
{
    const unsigned A = 4;
    std::vector<float> t = blank(A);
    for (unsigned i = 0; i < A; i++)
        put(t, A, i, 50 + 150 * i, 100, 20, 20, 0.9f - 0.1f * i);   /* four separate people */
    std::vector<pose::Person> out;
    CHECK(pose::decode(t.data(), A, 640, 384, 0.25f, 0.45f, 2, out));
    CHECK(out.size() == 2 && near(out[0].score, 0.9f));            /* max_people keeps the best */
    CHECK(!pose::decode(nullptr, A, 640, 384, 0.25f, 0.45f, 2, out) && out.empty());
    CHECK(!pose::decode(t.data(), 0, 640, 384, 0.25f, 0.45f, 2, out));
    put(t, A, 0, 700, 100, 20, 20, 0.9f);                           /* entirely outside the input: dropped */
    CHECK(pose::decode(t.data(), A, 640, 384, 0.25f, 0.45f, 300, out));
    CHECK(out.size() == 3);
}

int main()
{
    test_threshold_nms_order_and_relative_keypoints();
    test_box_is_clipped_and_keypoints_follow_the_clipped_box();
    test_limits_and_bad_arguments();
    std::printf("%s (%d failure%s)\n", failures ? "FAILED" : "OK", failures, failures == 1 ? "" : "s");
    return failures ? 1 : 0;
}
