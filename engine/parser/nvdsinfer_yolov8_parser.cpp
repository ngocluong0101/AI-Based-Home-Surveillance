/*
 * nvdsinfer_yolov8_parser.cpp - bbox parser for the YOLOv8 ONNX exported by
 * engine/tools/export_yolov8_onnx.py, loaded by nvinfer (custom-lib-path +
 * parse-bbox-func-name=NvDsInferParseYoloV8).
 *
 * nvinfer calls it once per frame of the batch. The output tensor `output0` of one frame is
 * [4 + num_classes, anchors] stored channel by channel (for yolov8n at 640: [84, 8400]):
 *   row 0..3  : cx, cy, w, h of the box, in pixels of the network input
 *   row 4..   : one score per class (already a probability, no objectness)
 * Only the `person` class is reported. Like Ultralytics, an anchor counts as a person only when
 * person is its best class. There is no NMS here: nvinfer clusters the boxes (cluster-mode=2).
 */
#include <algorithm>
#include <cstring>
#include <vector>

#include "nvdsinfer_custom_impl.h"

namespace {
constexpr unsigned int kPersonClassId = 0;
constexpr unsigned int kBoxRows = 4;
constexpr float kDefaultThreshold = 0.25f;
}  // namespace

extern "C" bool NvDsInferParseYoloV8(std::vector<NvDsInferLayerInfo> const &outputLayersInfo,
                                     NvDsInferNetworkInfo const &networkInfo,
                                     NvDsInferParseDetectionParams const &detectionParams,
                                     std::vector<NvDsInferObjectDetectionInfo> &objectList)
{
    if (outputLayersInfo.empty())
        return false;

    const NvDsInferLayerInfo &layer = outputLayersInfo[0];
    if (layer.buffer == nullptr || layer.inferDims.numDims < 2)
        return false;

    const unsigned int rows = layer.inferDims.d[0];     /* 4 + num_classes */
    const unsigned int anchors = layer.inferDims.d[1];
    if (rows <= kBoxRows + kPersonClassId)
        return false;
    const unsigned int num_classes = rows - kBoxRows;

    float threshold = kDefaultThreshold;
    if (!detectionParams.perClassPreclusterThreshold.empty())
        threshold = detectionParams.perClassPreclusterThreshold[0];

    const float *data = static_cast<const float *>(layer.buffer);
    const float *person_scores = data + (kBoxRows + kPersonClassId) * anchors;
    const float net_w = static_cast<float>(networkInfo.width);
    const float net_h = static_cast<float>(networkInfo.height);

    for (unsigned int i = 0; i < anchors; i++) {
        const float score = person_scores[i];
        if (score < threshold)
            continue;

        /* person must be the best class of this anchor */
        bool best = true;
        for (unsigned int c = 0; c < num_classes && best; c++) {
            if (c != kPersonClassId && data[(kBoxRows + c) * anchors + i] > score)
                best = false;
        }
        if (!best)
            continue;

        const float cx = data[0 * anchors + i];
        const float cy = data[1 * anchors + i];
        const float w = data[2 * anchors + i];
        const float h = data[3 * anchors + i];

        const float left = std::max(0.0f, cx - w * 0.5f);
        const float top = std::max(0.0f, cy - h * 0.5f);
        const float right = std::min(net_w, cx + w * 0.5f);
        const float bottom = std::min(net_h, cy + h * 0.5f);
        if (right <= left || bottom <= top)
            continue;

        NvDsInferObjectDetectionInfo obj;
        std::memset(&obj, 0, sizeof(obj));
        obj.classId = kPersonClassId;
        obj.left = left;
        obj.top = top;
        obj.width = right - left;
        obj.height = bottom - top;
        obj.detectionConfidence = score;
        objectList.push_back(obj);
    }
    return true;
}

CHECK_CUSTOM_PARSE_FUNC_PROTOTYPE(NvDsInferParseYoloV8);
