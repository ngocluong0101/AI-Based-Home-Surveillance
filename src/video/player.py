"""Video playback utilities for local files, webcams, and RTSP streams."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable
from src.config import RECONNECT_DELAY


WINDOW_NAME = "AI-Based-Home-Surveillance"
FrameProcessor = Callable[[Any], Any]
FrameCallback = Callable[[Any], None]


def _resolve_source(source: str) -> str | int:
    """Convert a CLI source into a capture-friendly value."""
    if source.isdigit():
        return int(source)

    return source


def _validate_local_file(source: str) -> Path | None:
    """Return the local path if the source points to an existing file."""
    path = Path(source).expanduser()
    if path.exists():
        return path

    return None


def _draw_fps(frame: Any, fps: float) -> Any:
    """Overlay FPS information on the current frame."""
    import cv2

    label = f"FPS: {fps:.1f}"
    origin = (10, 30)

    cv2.putText(
        frame,
        label,
        origin,
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (0, 255, 255),
        2,
        cv2.LINE_AA,
    )
    return frame


def play_video_source(
    source: str,
    frame_processor: FrameProcessor | None = None,
    display: bool = True,
    stop_event: threading.Event | None = None,
    on_frame: FrameCallback | None = None,
) -> int:
    """Play a video source frame by frame until the user quits or it ends.

    When `display` is False, no OpenCV window is created (headless mode for
    running on a server/NAS with no screen); quitting then relies on the
    source ending (local file) or Ctrl+C / SIGTERM (webcam/RTSP).

    `stop_event` and `on_frame` let another component drive the loop from a
    worker thread: the loop exits cleanly once `stop_event` is set, and
    `on_frame` receives every processed frame (after the FPS overlay).
    """
    def stopped() -> bool:
        return stop_event is not None and stop_event.is_set()

    def wait(seconds: float) -> None:
        # Interruptible sleep so a stop request doesn't wait out a full delay.
        if stop_event is not None:
            stop_event.wait(seconds)
        else:
            time.sleep(seconds)

    local_path = _validate_local_file(source)
    if local_path is None and not source.isdigit() and not source.startswith(("rtsp://", "rtsps://", "http://", "https://")):
        print(f"Error: video file not found: {source}")
        return 1

    try:
        import cv2
    except ImportError:
        import logging

        logging.getLogger(__name__).error("OpenCV is not installed. Run: pip install -r requirements.txt")
        return 1

    capture_source: str | int = _resolve_source(source)
    if local_path is not None:
        capture_source = str(local_path)
    capture = None

    # Determine whether the source is an RTSP stream (we'll enable reconnects)
    rtsp_protocols = ("rtsp://", "rtsps://")
    is_rtsp = local_path is None and isinstance(capture_source, str) and capture_source.startswith(rtsp_protocols)

    import logging
    logger = logging.getLogger(__name__)

    try:
        last_frame_time = time.perf_counter()

        # RTSP sources: attempt to reconnect on failures with a delay.
        if is_rtsp:
            reconnect_delay = RECONNECT_DELAY
            while not stopped():
                try:
                    capture = cv2.VideoCapture(capture_source)
                except Exception:
                    capture = None

                if capture is None or not capture.isOpened():
                    logger.warning("Could not open RTSP stream: %s", source)
                    logger.info("[CAMERA] Reconnecting...")
                    try:
                        wait(reconnect_delay)
                    except KeyboardInterrupt:
                        break
                    continue

                logger.info("[CAMERA] Connected")

                failure_count = 0
                while not stopped():
                    success, frame = capture.read()
                    if not success or frame is None:
                        failure_count += 1
                        # treat several consecutive failures as a lost connection
                        if failure_count >= 5:
                            logger.warning("[CAMERA] Connection lost")
                            try:
                                capture.release()
                            except Exception:
                                logger.debug("Error releasing capture after loss", exc_info=True)
                            logger.info("[CAMERA] Reconnecting...")
                            break
                        # avoid tight spin, allow Ctrl+C
                        wait(0.2)
                        continue

                    # reset failure counter on successful read
                    failure_count = 0

                    # update fps (no shared status)

                    if frame_processor is not None:
                        frame = frame_processor(frame)

                    # no MJPEG frame publishing (web removed)

                    current_time = time.perf_counter()
                    elapsed = current_time - last_frame_time
                    last_frame_time = current_time
                    if elapsed > 0:
                        fps = 1.0 / elapsed
                        frame = _draw_fps(frame, fps)
                        # no shared status update

                    if on_frame is not None:
                        on_frame(frame)

                    if display:
                        cv2.imshow(WINDOW_NAME, frame)
                        if cv2.waitKey(1) & 0xFF == ord("q"):
                            return 0

        else:
            # Local files and webcams: no RTSP-style reconnect
            capture = cv2.VideoCapture(capture_source)
            if not capture.isOpened():
                logger.error("Could not open video source: %s", source)
                return 1

            while not stopped():
                success, frame = capture.read()
                if not success or frame is None:
                    # If it's a file, assume end of video; if webcam, exit gracefully.
                    logger.info("Video ended or no more frames available.")
                    break

                if frame_processor is not None:
                    frame = frame_processor(frame)

                    # no MJPEG frame publishing (web removed)

                    current_time = time.perf_counter()
                    elapsed = current_time - last_frame_time
                    last_frame_time = current_time
                    if elapsed > 0:
                        fps = 1.0 / elapsed
                        frame = _draw_fps(frame, fps)
                        # no shared status update

                if on_frame is not None:
                    on_frame(frame)

                if display:
                    cv2.imshow(WINDOW_NAME, frame)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        return 0

    except KeyboardInterrupt:
        # allow clean exit on Ctrl+C or SIGTERM (main.py maps SIGTERM to this)
        logger.info("Shutdown requested, releasing camera and exiting")
        return 0
    finally:
        if capture is not None:
            try:
                capture.release()
            except Exception:
                logger.debug("Error releasing capture on shutdown", exc_info=True)
        if display:
            try:
                cv2.destroyAllWindows()
            except Exception:
                logger.debug("Error destroying OpenCV windows", exc_info=True)

    # mark camera offline on normal exit

    return 0