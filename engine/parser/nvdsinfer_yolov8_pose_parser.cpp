/*
 * @author: Ngoc Luong <ngocluongg0101@gmail.com>
 */

/*
 * nvdsinfer_yolov8_pose_parser.cpp - parser for the YOLOv8-pose ONNX (engine/tools/export_yolov8_pose_onnx.py), loaded by nvinfer with
 * network-type=3, output-instance-mask=1, cluster-mode=4, parse-bbox-instance-mask-func-name=NvDsInferParseYoloV8Pose,
 * custom-lib-path=libnvds_yolov8_parser.so
 *
 * The "instance mask" interface is the one place where nvinfer lets a parser attach a per-object float array
 * (NvDsObjectMeta.mask_params), so the 17 keypoints travel there as (x_rel, y_rel, visibility) relative to the box. The decoding
 * is in yolov8_pose_decode.h. NMS is done here (nvinfer accepts only cluster-mode 4 for instance masks). nvinfer frees the array
 * with delete[], so it is allocated with new[]. NMS IoU is 0.45; DS_POSE_NMS_IOU (0.05-1.0) changes it for an experiment.
 */
#include <cstdlib>
#include <cstring>
#include <vector>

#include "nvdsinfer_custom_impl.h"
#include "yolov8_pose_decode.h"

namespace {
constexpr float kDefaultThreshold = 0.25f;
constexpr float kDefaultNmsIou = 0.45f;
constexpr unsigned kMaxPeople = 300;

float nms_iou()
{
    static const float value = []() {
        const char *env = std::getenv("DS_POSE_NMS_IOU");
        if (env != nullptr) {
            char *end = nullptr;
            const float v = std::strtof(env, &end);
            if (end != env && *end == '\0' && v >= 0.05f && v <= 1.0f)
                return v;
        }
        return kDefaultNmsIou;
    }();
    return value;
}
}  // namespace

extern "C" bool NvDsInferParseYoloV8Pose(std::vector<NvDsInferLayerInfo> const &outputLayersInfo,
                                         NvDsInferNetworkInfo const &networkInfo,
                                         NvDsInferParseDetectionParams const &detectionParams,
                                         std::vector<NvDsInferInstanceMaskInfo> &objectList)
{
    if (outputLayersInfo.empty())
        return false;
    const NvDsInferLayerInfo &layer = outputLayersInfo[0];
    if (layer.buffer == nullptr || layer.inferDims.numDims < 2 || layer.inferDims.d[0] != (int)pose::kRows)
        return false;
    const unsigned anchors = layer.inferDims.d[1];

    float threshold = kDefaultThreshold;
    if (!detectionParams.perClassPreclusterThreshold.empty())
        threshold = detectionParams.perClassPreclusterThreshold[0];

    std::vector<pose::Person> people;
    if (!pose::decode(static_cast<const float *>(layer.buffer), anchors, static_cast<float>(networkInfo.width),
                      static_cast<float>(networkInfo.height), threshold, nms_iou(), kMaxPeople, people))
        return false;

    for (const pose::Person &p : people) {
        NvDsInferInstanceMaskInfo obj;
        std::memset(&obj, 0, sizeof(obj));
        obj.classId = 0;
        obj.left = p.left;
        obj.top = p.top;
        obj.width = p.width;
        obj.height = p.height;
        obj.detectionConfidence = p.score;
        obj.mask = new float[pose::kKeypointValues];
        std::memcpy(obj.mask, p.kpt, sizeof(p.kpt));
        obj.mask_width = 3;                       /* x_rel, y_rel, visibility */
        obj.mask_height = pose::kKeypoints;
        obj.mask_size = sizeof(p.kpt);            /* bytes */
        objectList.push_back(obj);
    }
    return true;
}

CHECK_CUSTOM_INSTANCE_MASK_PARSE_FUNC_PROTOTYPE(NvDsInferParseYoloV8Pose);
