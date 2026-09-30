import threading

from src.video import play_video_source


def test_on_frame_receives_every_frame(sample_video, frame_count):
    frames = []

    code = play_video_source(str(sample_video), display=False, on_frame=frames.append)

    assert code == 0
    assert len(frames) == frame_count


def test_frame_processor_runs_before_on_frame(sample_video, frame_count):
    processed = []
    received = []

    def processor(frame):
        processed.append(1)
        return frame

    play_video_source(str(sample_video), frame_processor=processor, display=False, on_frame=received.append)

    assert len(processed) == len(received) == frame_count


def test_stop_event_ends_playback_early(sample_video):
    stop = threading.Event()
    frames = []

    def on_frame(frame):
        frames.append(frame)
        if len(frames) == 3:
            stop.set()

    code = play_video_source(str(sample_video), display=False, stop_event=stop, on_frame=on_frame)

    assert code == 0
    assert len(frames) == 3


def test_stop_event_already_set_processes_nothing(sample_video):
    stop = threading.Event()
    stop.set()
    frames = []

    code = play_video_source(str(sample_video), display=False, stop_event=stop, on_frame=frames.append)

    assert code == 0
    assert frames == []


def test_missing_file_returns_error_code(tmp_path):
    assert play_video_source(str(tmp_path / "nope.mp4"), display=False) == 1
