"""Entry point for AI-Based-Home-Surveillance."""

from __future__ import annotations

import argparse

from src.video import play_video_source


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="AI-Based-Home-Surveillance")
    parser.add_argument(
        "--source",
        help="Video source path, webcam index, or RTSP URL",
    )
    return parser.parse_args()


def main() -> int:
    """Start the application."""
    args = parse_args()

    if not args.source:
        print("AI-Based-Home-Surveillance started successfully.")
        print("Use --source to play an MP4 file, webcam, or RTSP stream.")
        return 0

    return play_video_source(args.source)


if __name__ == "__main__":
    raise SystemExit(main())