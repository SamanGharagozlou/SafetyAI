import time

import numpy as np

from fakes import FakeCapture, RecordingLogger, ScriptedOpener
from human_perception.capture import (build_gstreamer_rtsp_pipeline, ConnectionState,
                                      FrameGrabber, normalize_frame)
from human_perception.config import BackoffConfig, SourceConfig


def _wait(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def _rtsp_cfg(url='rtsp://user:secret@10.0.0.9:8554/test'):
    return SourceConfig(source_type='rtsp', rtsp_url=url,
                        backoff=BackoffConfig(0.01, 0.04, 2.0)).validate()


def test_reconnects_with_backoff_after_failures():
    frames = [np.full((4, 4, 3), i, np.uint8) for i in range(3)]
    opener = ScriptedOpener([
        FakeCapture(opened=False),
        RuntimeError('connection refused'),
        FakeCapture(script=frames[:2] + [None]),   # stream drops after 2 frames
        FakeCapture(opened=False),
        FakeCapture(script=[frames[2]]),
    ])
    got = []
    g = FrameGrabber(_rtsp_cfg(), on_frame=lambda f, t: got.append(int(f[0, 0, 0])),
                     open_fn=opener)
    g.start()
    try:
        assert _wait(lambda: len(got) == 3)
    finally:
        g.stop()
    assert got == [0, 1, 2]
    assert opener.calls >= 5
    assert g.reconnect_count >= 2
    assert g.state == ConnectionState.STOPPED


def test_disconnected_state_and_bounded_delay():
    opener = ScriptedOpener([])  # never opens
    g = FrameGrabber(_rtsp_cfg(), open_fn=opener)
    g.start()
    try:
        assert _wait(lambda: g.backoff.attempts >= 5)
        assert g.state in (ConnectionState.RECONNECT_WAIT, ConnectionState.CONNECTING)
        assert g.next_retry_in_s <= 0.04 + 1e-9
        assert g.last_error
    finally:
        g.stop()


def test_logs_never_contain_credentials():
    log = RecordingLogger()
    opener = ScriptedOpener([RuntimeError('cannot open rtsp://user:secret@10.0.0.9:8554/test')])
    g = FrameGrabber(_rtsp_cfg(), open_fn=opener, logger=log)
    g.start()
    try:
        assert _wait(lambda: opener.calls >= 2)
    finally:
        g.stop()
    text = ' '.join(m for _, m in log.messages) + g.last_error
    assert 'secret' not in text and 'user:' not in text


def test_latest_frame_slot_keeps_only_newest():
    frames = [np.full((4, 4, 3), i, np.uint8) for i in range(50)]
    g = FrameGrabber(_rtsp_cfg(), open_fn=ScriptedOpener([FakeCapture(script=frames)]))
    g.start()
    try:
        assert _wait(lambda: g.get_latest() is not None and g.get_latest()[0] == 50)
    finally:
        g.stop()
    seq, frame, _ = g.get_latest()
    assert seq == 50 and int(frame[0, 0, 0]) == 49


def test_file_source_without_loop_stops_at_eof():
    cfg = SourceConfig(source_type='file', file_path='x.mp4', loop_file=False, fps=500.0,
                       backoff=BackoffConfig(0.01, 0.02)).validate()
    frames = [np.zeros((4, 4, 3), np.uint8)] * 3
    got = []
    g = FrameGrabber(cfg, on_frame=lambda f, t: got.append(1),
                     open_fn=ScriptedOpener([FakeCapture(script=frames)]))
    g.start()
    assert _wait(lambda: g.state == ConnectionState.END_OF_FILE)
    g.stop()
    assert len(got) == 3


def test_invalid_frames_skipped_and_callback_errors_contained():
    script = [np.zeros((4, 4, 3), np.float32), np.zeros((4, 4, 3), np.uint8)]
    calls = []

    def bad_callback(frame, t):
        calls.append(1)
        raise RuntimeError('publisher exploded')

    g = FrameGrabber(_rtsp_cfg(), on_frame=bad_callback,
                     open_fn=ScriptedOpener([FakeCapture(script=script)]))
    g.start()
    try:
        assert _wait(lambda: calls)
    finally:
        g.stop()
    assert g.invalid_frame_count == 1


def test_normalize_frame():
    assert normalize_frame(np.zeros((3, 3), np.uint8)).shape == (3, 3, 3)
    assert normalize_frame(np.zeros((3, 3, 4), np.uint8)).shape == (3, 3, 3)
    assert normalize_frame(np.zeros((3, 3, 3), np.float32)) is None
    assert normalize_frame(None) is None


def test_gstreamer_pipeline_is_low_latency():
    p = build_gstreamer_rtsp_pipeline('rtsp://h/x', 100, 'tcp')
    assert 'drop=true' in p and 'max-buffers=1' in p and 'protocols=tcp' in p


def test_wait_for_frame_returns_only_newer_frames():
    frames = [np.full((4, 4, 3), i, np.uint8) for i in range(5)]
    g = FrameGrabber(_rtsp_cfg(), open_fn=ScriptedOpener([FakeCapture(script=frames)]))
    assert g.wait_for_frame(0, timeout=0.01) is None  # nothing captured yet
    g.start()
    try:
        first = g.wait_for_frame(0, timeout=2.0)
        assert first is not None and first[0] >= 1
        assert _wait(lambda: g.get_latest()[0] == 5)
        assert g.wait_for_frame(5, timeout=0.05) is None  # no newer frame -> timeout
    finally:
        g.stop()
