"""Entry point for AI-Based-Home-Surveillance."""

from __future__ import annotations

import argparse

from src.detection import PersonDetector
from src.video import play_video_source


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="AI-Based-Home-Surveillance")
    parser.add_argument(
        "--source",
        help="Video source path, webcam index, or RTSP URL",
    )
    parser.add_argument(
        "--conf-threshold",
        type=float,
        default=0.5,
        help="Confidence threshold for person detection",
    )
    return parser.parse_args()


def main() -> int:
    """Start the application."""
    args = parse_args()

    if not args.source:
        print("AI-Based-Home-Surveillance started successfully.")
        print("Use --source to play an MP4 file, webcam, or RTSP stream.")
        print("Use --conf-threshold to adjust person detection confidence.")
        return 0

    detector = PersonDetector(confidence_threshold=args.conf_threshold)
    return play_video_source(args.source, frame_processor=detector.annotate_frame)


if __name__ == "__main__":
    raise SystemExit(main())