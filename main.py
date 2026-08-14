"""Entry point for AI-Based-Home-Surveillance."""

from __future__ import annotations

import argparse

from src.detection import PersonDetector
from src.tracking import PersonTracker
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
    parser.add_argument(
        "--track",
        action="store_true",
        help="Enable ByteTrack-based person tracking",
    )
    return parser.parse_args()


def main() -> int:
    """Start the application."""
    args = parse_args()

    if not args.source:
        print("AI-Based-Home-Surveillance started successfully.")
        print("Use --source to play an MP4 file, webcam, or RTSP stream.")
        print("Use --conf-threshold to adjust person detection confidence.")
        print("Use --track to enable ByteTrack-based person tracking.")
        return 0

    if args.track:
        processor = PersonTracker(confidence_threshold=args.conf_threshold)
    else:
        processor = PersonDetector(confidence_threshold=args.conf_threshold)

    print(f"Using device: {processor.device}")
    frame_processor = processor.annotate_frame
    return play_video_source(args.source, frame_processor=frame_processor)


if __name__ == "__main__":
    raise SystemExit(main())