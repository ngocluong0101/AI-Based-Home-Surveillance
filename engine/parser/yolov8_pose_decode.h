/*
 * yolov8_pose_decode.h - turn the output tensor of the YOLOv8-pose ONNX (engine/tools/export_yolov8_pose_onnx.py)
 * into people with 17 keypoints each. Plain C++11, no DeepStream types, so it can be tested on the host
 * (make -C engine/parser test).
 *
 * The tensor of one frame is [56, anchors], channel by channel:
 *   row 0..3   cx, cy, w, h of the box, in pixels of the network input
 *   row 4      person score (a probability; the pose model has the one class "person")
 *   row 5..55  17 keypoints, each x, y (network input pixels), visibility (a probability)
 *
 * Keypoints are returned RELATIVE TO THE BOX that is returned: x_rel = (x - left) / width, same for y. nvinfer
 * moves the box from the network input to the frame (scale and the padding of the letterbox); a point written
 * relative to that box follows it without this code having to know how the frame was resized. Points outside
 * the box give values below 0 or above 1, which is fine.
 */
#ifndef DS_YOLOV8_POSE_DECODE_H
#define DS_YOLOV8_POSE_DECODE_H

#include <algorithm>
#include <cstddef>
#include <vector>

namespace pose {

constexpr unsigned kRows = 56;       /* 4 box + 1 score + 17 * 3 */
constexpr unsigned kKeypoints = 17;
constexpr unsigned kKeypointValues = kKeypoints * 3;   /* x_rel, y_rel, visibility per keypoint */

struct Person {
    float left, top, width, height;   /* box in network input pixels, clipped to the input */
    float score;
    float kpt[kKeypointValues];        /* x_rel, y_rel, visibility for each of the 17 keypoints */
};

inline float iou(const Person &a, const Person &b)
{
    const float x1 = std::max(a.left, b.left), y1 = std::max(a.top, b.top);
    const float x2 = std::min(a.left + a.width, b.left + b.width);
    const float y2 = std::min(a.top + a.height, b.top + b.height);
    const float inter = std::max(0.0f, x2 - x1) * std::max(0.0f, y2 - y1);
    const float uni = a.width * a.height + b.width * b.height - inter;
    return uni > 0.0f ? inter / uni : 0.0f;
}

/* data: [kRows, anchors] floats. Keeps people with score >= threshold, removes boxes that overlap a better
 * one by more than nms_iou, and returns at most max_people, best first. Returns false on a bad argument. */
inline bool decode(const float *data, unsigned anchors, float net_w, float net_h, float threshold, float nms_iou,
                   unsigned max_people, std::vector<Person> &out)
{
    out.clear();
    if (data == nullptr || anchors == 0 || net_w <= 0.0f || net_h <= 0.0f)
        return false;

    const float *score = data + 4 * anchors;
    std::vector<unsigned> order;
    for (unsigned i = 0; i < anchors; i++)
        if (score[i] >= threshold)
            order.push_back(i);
    std::sort(order.begin(), order.end(), [&](unsigned a, unsigned b) { return score[a] > score[b]; });

    for (unsigned idx : order) {
        if (out.size() >= max_people)
            break;
        const float cx = data[0 * anchors + idx], cy = data[1 * anchors + idx];
        const float w = data[2 * anchors + idx], h = data[3 * anchors + idx];
        Person p;
        p.left = std::max(0.0f, cx - w * 0.5f);
        p.top = std::max(0.0f, cy - h * 0.5f);
        const float right = std::min(net_w, cx + w * 0.5f);
        const float bottom = std::min(net_h, cy + h * 0.5f);
        if (right <= p.left || bottom <= p.top)
            continue;
        p.width = right - p.left;
        p.height = bottom - p.top;
        p.score = score[idx];

        bool suppressed = false;
        for (const Person &kept : out) {
            if (iou(kept, p) > nms_iou) {
                suppressed = true;
                break;
            }
        }
        if (suppressed)
            continue;

        for (unsigned k = 0; k < kKeypoints; k++) {
            const float x = data[(5 + 3 * k) * anchors + idx];
            const float y = data[(6 + 3 * k) * anchors + idx];
            p.kpt[3 * k + 0] = (x - p.left) / p.width;
            p.kpt[3 * k + 1] = (y - p.top) / p.height;
            p.kpt[3 * k + 2] = data[(7 + 3 * k) * anchors + idx];
        }
        out.push_back(p);
    }
    return true;
}

}  // namespace pose

#endif /* DS_YOLOV8_POSE_DECODE_H */
