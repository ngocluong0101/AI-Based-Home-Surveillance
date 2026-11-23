#!/bin/bash
# @author: Ngoc Luong <ngocluongg0101@gmail.com>

# Encode a numbered image sequence into an H.264 MP4 with NVENC (the host has no x264). Runs inside the DeepStream container
# (see in_container.sh).
#   encode_h264.sh FORMAT PATTERN OUT.mp4 [FPS] [BITRATE_BPS] [START_INDEX]
#   FORMAT = png | jpg ; PATTERN = printf-style path, e.g. eval/work/frames/%06d.png
# The default bit rate (40 Mbit/s at 1080p30) is high on purpose: compression noise should not decide the result. Frames are not scaled.
set -e
FMT="$1"; PATTERN="$2"; OUT="$3"; FPS="${4:-30}"; BR="${5:-40000000}"; START="${6:-0}"
case "$FMT" in
    png) DEC=pngdec; CAPS="image/png,framerate=$FPS/1" ;;
    jpg) DEC=jpegdec; CAPS="image/jpeg,framerate=$FPS/1" ;;
    *) echo "format must be png or jpg" >&2; exit 2 ;;
esac
gst-launch-1.0 -e multifilesrc location="$PATTERN" index="$START" caps="$CAPS" ! $DEC ! videoconvert ! \
    nvvideoconvert ! 'video/x-raw(memory:NVMM),format=NV12' ! \
    nvv4l2h264enc bitrate="$BR" profile=4 iframeinterval=30 ! h264parse ! qtmux ! filesink location="$OUT"
