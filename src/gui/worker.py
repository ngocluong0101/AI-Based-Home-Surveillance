"""Background worker that runs the video pipeline for the desktop GUI."""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any, Callable

from src.video import play_video_source

logger = logging.getLogger(__name__)

ProcessorFactory = Callable[[float, bool], Any]

STATE_IDLE = "idle"
STATE_LOADING = "loading"
STATE_RUNNING = "running"
STATE_STOPPED = "stopped"


def default_processor_factory(confidence: float, track: bool) -> Any:
    """Build the same processor `main.py` uses for the CLI."""
    from src.config import ABSENCE_TIMEOUT
    from src.detection import PersonDetector
    from src.tracking import PersonTracker

    if track:
        return PersonTracker(confidence_threshold=confidence, absence_timeout=ABSENCE_TIMEOUT)
    return PersonDetector(confidence_threshold=confidence)


class PipelineWorker:
    """Run `play_video_source` on a daemon thread and expose the latest frame.

    The Tk main thread polls `frames` (a size-1 queue holding only the newest
    frame, so a slow UI never builds a backlog) and `state`/`error`.
    """

    def __init__(
        self,
        source: str,
        confidence: float,
        track: bool,
        processor_factory: ProcessorFactory = default_processor_factory,
    ) -> None:
        self.source = source
        self.track = track
        self.frames: queue.Queue[Any] = queue.Queue(maxsize=1)
        self.state = STATE_IDLE
        self.error: str | None = None
        self.exit_code: int | None = None

        self._confidence = confidence
        self._processor_factory = processor_factory
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("PipelineWorker can only be started once")
        self.state = STATE_LOADING
        self._thread = threading.Thread(target=self._run, name="pipeline-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float | None = None) -> None:
        """Ask the pipeline to stop; optionally wait up to `timeout` seconds."""
        self._stop_event.set()
        if timeout is not None and self._thread is not None:
            self._thread.join(timeout)

    def set_confidence(self, value: float) -> None:
        """Change the detection threshold; applied from the next frame on."""
        self._confidence = value

    def _publish(self, frame: Any) -> None:
        try:
            self.frames.get_nowait()
        except queue.Empty:
            pass
        try:
            self.frames.put_nowait(frame)
        except queue.Full:
            pass

    def _run(self) -> None:
        try:
            processor = self._processor_factory(self._confidence, self.track)
        except Exception as exc:
            logger.error("Failed to load detection model: %s", exc, exc_info=True)
            self.error = f"Không tải được model: {exc}"
            self.state = STATE_STOPPED
            return

        logger.info("GUI pipeline started (device=%s, track=%s)", getattr(processor, "device", "?"), self.track)
        detector = getattr(processor, "detector", processor)

        def process(frame: Any) -> Any:
            detector.confidence_threshold = self._confidence
            return processor.annotate_frame(frame)

        self.state = STATE_RUNNING
        try:
            self.exit_code = play_video_source(
                self.source,
                frame_processor=process,
                display=False,
                stop_event=self._stop_event,
                on_frame=self._publish,
            )
            if self.exit_code != 0 and not self._stop_event.is_set():
                self.error = f"Không mở được nguồn video: {self.source}"
        except Exception as exc:
            logger.error("Pipeline crashed: %s", exc, exc_info=True)
            self.error = f"Pipeline gặp lỗi: {exc}"
        finally:
            self.state = STATE_STOPPED
            logger.info("GUI pipeline stopped")
