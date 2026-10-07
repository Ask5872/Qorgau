"""Regressions for illumination and a wizard stalled without fresh analysis."""
import numpy as np
import pytest

from qorgau.illumination import measure_illumination
from qorgau.screen_calibration import ScreenCalibration
from qorgau.vision import Camera


def test_well_lit_face_is_not_rejected_for_a_dark_background():
    frame = np.full((480, 640, 3), 3, dtype=np.uint8)
    frame[100:260, 220:360] = 95
    values = measure_illumination(frame, [220, 100, 140, 160])
    assert values["frame_brightness"] < 30
    assert values["brightness"] == values["face_brightness"] == 95
    assert values["illumination_source"] == "face"


def test_a_bright_background_does_not_hide_an_underexposed_face():
    frame = np.full((480, 640, 3), 170, dtype=np.uint8)
    frame[100:260, 220:360] = 12
    values = measure_illumination(frame, [220, 100, 140, 160])
    assert values["frame_brightness"] > 100
    assert values["brightness"] == 12


def test_small_reflection_cannot_make_dark_face_pass_exposure_check():
    frame = np.full((480, 640, 3), 5, dtype=np.uint8)
    frame[100:260, 220:360] = 10
    frame[150:163, 260:280] = 255
    assert measure_illumination(frame, [220, 100, 140, 160])["brightness"] == 10


@pytest.mark.parametrize("box", [None, [2, 2, 3, 3], [1000, 1000, 100, 100],
                                  [float("nan"), 2, 100, 100]])
def test_missing_or_unusable_face_uses_real_frame_exposure(box):
    values = measure_illumination(np.full((240, 320, 3), 50, dtype=np.uint8), box)
    assert values["brightness"] == 50 and values["illumination_source"] == "frame"
    assert values["face_brightness"] is None


def test_polling_expires_calibration_even_when_analyzer_stops_returning_frames(tmp_path, monkeypatch):
    camera = Camera(tmp_path, lambda *args: None)
    camera.calibration = ScreenCalibration(now=100)
    camera.calibrating = True
    viewport = {"width": 1366, "height": 768, "screen_width": 1366,
                "screen_height": 768, "device_pixel_ratio": 1}
    camera.calibration.acknowledge(0, viewport, now=100)
    camera.state.update(calibrating=True, calibration=camera.calibration.status(now=100))
    monkeypatch.setattr("qorgau.vision.time.monotonic", lambda: 100 + ScreenCalibration.TARGET_TIMEOUT + 1)
    status = camera.status()
    assert status["calibration"]["phase"] == "failed"
    assert not status["calibrating"] and not status["calibrated"]
    assert camera.baseline is None and status["error"]


def test_live_status_refreshes_countdown_without_manufacturing_a_frame(tmp_path, monkeypatch):
    camera = Camera(tmp_path, lambda *args: None)
    camera.calibration = ScreenCalibration(now=100)
    camera.calibration.acknowledge(0, {"width": 1366, "height": 768,
        "screen_width": 1366, "screen_height": 768, "device_pixel_ratio": 1}, now=100)
    monkeypatch.setattr("qorgau.vision.time.monotonic", lambda: 103)
    status = camera.status()
    assert status["calibration"]["target_elapsed"] == 3
    assert status["calibration"]["target_remaining"] == ScreenCalibration.TARGET_TIMEOUT - 3
    assert status["calibration"]["accepted_samples"] == 0
    assert not camera.calibration.samples and not camera.calibration.groups


@pytest.mark.parametrize("latency", [1.1, 2.5])
def test_camera_adapter_completes_slow_serial_analysis_without_reset(tmp_path, monkeypatch, latency):
    """Exercise real Camera calibration/state publication, with no physical camera."""
    from qorgau.screen_calibration import TARGETS

    clock = [100.]
    monkeypatch.setattr("qorgau.vision.time.monotonic", lambda: clock[0])
    camera = Camera(tmp_path, lambda *args: None)
    camera._capture_at = clock[0]
    camera._analyzed_at = clock[0] - .1
    camera.state.update(camera_ok=True, analysis_ready=True, running=True)
    calibration_id = camera.calibrate()["id"]
    viewport = {"width": 1366, "height": 768, "screen_width": 1366,
                "screen_height": 768, "device_pixel_ratio": 1}
    sequence = 0
    previous_completion = clock[0]
    cadence = max(2., latency + .1)
    for step, (_, x, y) in enumerate(TARGETS):
        camera.calibration_target(step, viewport, calibration_id)
        acknowledged_at = clock[0]
        for index in range(ScreenCalibration.MIN_SAMPLES):
            captured_at = acknowledged_at + .7 + index * cadence
            assert captured_at > previous_completion
            sequence += 1
            clock[0] = previous_completion = captured_at + latency
            signals = {"faces": 1, "landmark_faces": 1, "landmarks_valid": True,
                       "pose_valid": True, "camera_ok": True, "analysis_ready": True,
                       "brightness": 100, "eye_open": True, "eye_features_valid": True,
                       "yaw": 2., "pitch": -1., "roll": 3., "head_vertical": .2,
                       "iris_x": .5 + .2 * (x - .5) + .01 * (y - .5),
                       "iris_y": .5 + .5 * (y - .5) + .01 * (x - .5),
                       "analyzed_seq": sequence, "analyzed_at": captured_at}
            with camera.lock:
                camera._calibrate_and_gaze(signals, np)
                # Match publication by the analysis worker; capture runs independently.
                camera.state.update(signals)
                camera._capture_at = clock[0]
                camera._analyzed_at = captured_at
            status = camera.status()
            assert status["calibration"]["id"] == calibration_id
            assert status["calibrated"] is (step == len(TARGETS) - 1 and index == ScreenCalibration.MIN_SAMPLES - 1)
            assert status["calibration"]["restart_count"] == 0
        assert status["calibration"]["saved_points"] == step + 1
        clock[0] += .2
    assert status["calibration"]["phase"] == "complete"
    assert status["calibration_version"] == 2 and camera.baseline is not None
    assert clock[0] - 100 < ScreenCalibration.TOTAL_TIMEOUT
