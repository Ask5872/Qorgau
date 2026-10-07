"""Threading checks with a synthetic camera and deliberately slow inference.

These verify scheduling and evidence correspondence, not physical webcam FPS
or detection accuracy. Real models are checked separately in test_vision_paths.
"""
import threading
import time

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from qorgau import vision


class SyntheticCapture:
    def __init__(self):
        self.sequence = 0
        self.released = False

    def read(self):
        self.sequence += 1
        # Solid grey survives JPEG encoding exactly, allowing evidence matching.
        value = 30 + self.sequence % 150
        return True, np.full((360, 640, 3), value, dtype=np.uint8)

    def release(self):
        self.released = True


class SlowModels:
    closed = []

    def __init__(self, model_dir):
        self.delay = 0.20

    def analyze(self, frame):
        time.sleep(self.delay)
        return {"faces": 1, "eye_open": True, "brightness": int(frame[0, 0, 0]),
                "yaw": 0, "pitch": 0, "iris_x": 0.5, "iris_y": 0.5,
                "head_vertical": 0.0, "phone": False, "phone_confidence": 0,
                "phone_raised": False, "face_boxes": [], "phone_boxes": []}

    def close(self):
        self.closed.append(self)


def make_camera(monkeypatch, tmp_path, callback):
    for name in ("face_landmarker.task", "yolov8n.onnx", "yolov8n-face.onnx"):
        (tmp_path / name).write_bytes(b"synthetic-test-only")
    capture = SyntheticCapture()
    monkeypatch.setattr(vision, "VisionModels", SlowModels)
    monkeypatch.setattr(vision.Camera, "_open_capture", staticmethod(lambda cv, index: capture))
    return vision.Camera(tmp_path, callback), capture


def test_preview_keeps_up_while_inference_skips_old_frames(monkeypatch, tmp_path):
    callbacks = []
    done = threading.Event()

    def on_frame(signals, jpeg):
        callbacks.append((dict(signals), jpeg))
        if len(callbacks) >= 5:
            done.set()

    camera, capture = make_camera(monkeypatch, tmp_path, on_frame)
    camera.start()
    preview_sequences = []
    previous = 0
    deadline = time.monotonic() + 5
    try:
        while not done.is_set() and time.monotonic() < deadline:
            seq, jpg = camera.wait_for_jpeg(previous, 0.5)
            if jpg:
                preview_sequences.append(seq)
                previous = seq
        state = camera.status()
        assert done.is_set(), state
        assert len(preview_sequences) >= 3 * len(callbacks)
        assert state["fps"] > state["analysis_fps"] * 2
        assert state["width"] == 640 and state["height"] == 360
        analyzed = [signals["analyzed_seq"] for signals, _ in callbacks]
        assert analyzed == sorted(set(analyzed))
        assert any(b - a > 1 for a, b in zip(analyzed, analyzed[1:]))
        for signals, jpg in callbacks:
            evidence = cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)
            assert abs(int(evidence[0, 0, 0]) - signals["brightness"]) <= 1
            assert signals["analysis_ready"] and signals["analysis_ms"] >= 180
    finally:
        camera.stop()
    assert capture.released
    assert not camera.thread.is_alive() and not camera.analysis_thread.is_alive()
    assert not camera.status()["running"] and camera.jpeg is None


def test_callback_can_stop_and_camera_can_restart(monkeypatch, tmp_path):
    stopped = threading.Event()
    results = []

    def on_frame(signals, jpeg):
        results.append(signals)
        camera.stop()
        stopped.set()

    camera, capture = make_camera(monkeypatch, tmp_path, on_frame)
    camera.start()
    assert stopped.wait(3), camera.status()
    camera.analysis_thread.join(1)
    assert capture.released
    assert not camera.analysis_thread.is_alive()
    assert camera.jpeg is None and camera.raw_jpeg is None
    assert not camera.status()["analysis_ready"]
    stopped.clear()
    restarted_at = time.monotonic()
    camera.start()
    assert stopped.wait(3), camera.status()
    camera.analysis_thread.join(1)
    assert len(results) == 2
    assert results[1]["analyzed_at"] >= restarted_at
    camera.stop()


def test_status_invalidates_stale_capture_and_analysis(tmp_path):
    camera = vision.Camera(tmp_path, lambda *_: None)
    camera.state.update(camera_ok=True, analysis_ready=True, running=True)
    camera._capture_at = time.monotonic()
    camera._analyzed_at = time.monotonic() - 4
    assert camera.status()["camera_ok"]
    assert not camera.status()["analysis_ready"]
    camera._analyzed_at = time.monotonic()
    camera._capture_at = time.monotonic() - 3
    assert not camera.status()["camera_ok"]
    assert not camera.status()["analysis_ready"]


def test_restart_waits_for_native_model_cleanup(monkeypatch, tmp_path):
    stopped, release_close = threading.Event(), threading.Event()

    class ClosingModels(SlowModels):
        def close(self):
            release_close.wait(3)

    def on_frame(*_):
        camera.stop()
        stopped.set()

    camera, _ = make_camera(monkeypatch, tmp_path, on_frame)
    monkeypatch.setattr(vision, "VisionModels", ClosingModels)
    camera.start()
    try:
        assert stopped.wait(3)
        with pytest.raises(ValueError, match="ещё завершается"):
            camera.start()
        # The restart attempt must not clear the previous run's stop flag.
        assert camera.stop_flag.is_set()
    finally:
        release_close.set()
        camera.analysis_thread.join(2)
        camera.stop()
    assert not camera.analysis_thread.is_alive()
