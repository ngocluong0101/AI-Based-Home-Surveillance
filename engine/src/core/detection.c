#include "detection.h"

int detection_bbox_normalize(float left, float top, float width, float height,
                             float frame_w, float frame_h, float out[4])
{
    if (frame_w <= 0.0f || frame_h <= 0.0f || width <= 0.0f || height <= 0.0f)
        return 0;

    float x0 = left < 0.0f ? 0.0f : left;
    float y0 = top < 0.0f ? 0.0f : top;
    float x1 = left + width > frame_w ? frame_w : left + width;
    float y1 = top + height > frame_h ? frame_h : top + height;
    if (x1 <= x0 || y1 <= y0)
        return 0;

    out[0] = x0 / frame_w;
    out[1] = y0 / frame_h;
    out[2] = (x1 - x0) / frame_w;
    out[3] = (y1 - y0) / frame_h;
    return 1;
}
