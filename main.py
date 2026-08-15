"""Entry point for AI-Based-Home-Surveillance."""

from __future__ import annotations

import argparse
from typing import Optional

from src.config import RTSP_URL, CONFIDENCE_THRESHOLD, ABSENCE_TIMEOUT

from src.detection import PersonDetector
from src.tracking import PersonTracker
from src.video import play_video_source
from src.logging_config import setup_logging
import logging
from src.config import LOG_LEVEL


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
        default=None,
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

    source: Optional[str] = args.source or RTSP_URL

    if not source:
        print("AI-Based-Home-Surveillance started successfully.")
        print("Use --source to play an MP4 file, webcam, or RTSP stream.")
        print("You may also set RTSP_URL in .env to use an IP camera by default.")
        print("Use --conf-threshold to adjust person detection confidence.")
        print("Use --track to enable ByteTrack-based person tracking.")
        return 0

    # Determine confidence threshold (CLI overrides config)
    conf = args.conf_threshold if args.conf_threshold is not None else CONFIDENCE_THRESHOLD

    if args.track:
        processor = PersonTracker(confidence_threshold=conf, absence_timeout=ABSENCE_TIMEOUT)
    else:
        processor = PersonDetector(confidence_threshold=conf)

    # Configure logging once at startup
    setup_logging(LOG_LEVEL)
    logger = logging.getLogger(__name__)
    logger.info("Application started")
    logger.info("Using device: %s", processor.device)
    frame_processor = processor.annotate_frame
    return play_video_source(source, frame_processor=frame_processor)


if __name__ == "__main__":
    raise SystemExit(main())