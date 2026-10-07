"""Saved calibration checkpoints are useful, but never stand in for validation."""
import copy

import pytest

from qorgau.gaze import estimate_screen_gaze
from qorgau.screen_calibration import ScreenCalibration
from qorgau.vision import Camera
from test_screen_calibration import VIEWPORT, complete, feed_point, observation


def partial(count):
    calibration = ScreenCalibration(now=0.)
    for _ in range(count):
        feed_point(calibration)
    return calibration


@pytest.mark.parametrize("count,mode,gaze", [(0, "none", "unavailable"), (1, "partial", "unavailable"),
    (2, "partial", "unavailable"), (3, "partial", "approximate"), (4, "partial", "approximate"),
    (5, "full", "calibrated")])
def test_use_saved_freezes_only_completed_checkpoints(count, mode, gaze):
    calibration = partial(count)
    status = calibration.use_saved()
    assert not status["active"] and status["saved_points"] == count
    assert status["calibration_mode"] == mode and status["gaze_monitoring"] == gaze
    assert status["calibrated"] is (count == 5)
    assert status["head_calibrated"] is (count > 0)
    assert (calibration.profile is not None) is (count == 5)


def test_unfinished_point_cannot_supply_a_neutral_pose():
    calibration = ScreenCalibration(now=0.)
    calibration.acknowledge(0, VIEWPORT, now=0.)
    calibration.collect(observation(seq=1, at=.5), now=.5)
    assert calibration.samples
    status = calibration.use_saved()
    assert not calibration.samples and status["saved_points"] == 0
    assert status["calibration_mode"] == "none" and not status["head_calibrated"]


@pytest.mark.parametrize("action", ["cancel", "fail", "timeout"])
def test_stable_points_survive_cancel_failure_and_expiry(action):
    calibration = partial(3)
    groups = copy.deepcopy(calibration.groups)
    if action == "cancel":
        calibration.cancel()
    elif action == "fail":
        calibration.fail("Нет новых кадров.")
    else:
        assert calibration.check_timeout(now=1000.)
    assert calibration.groups == groups and calibration.profile is None
    assert calibration.status()["saved_points"] == 3
    assert calibration.status()["gaze_monitoring"] == "approximate"
    calibration.collect(observation(seq=999, at=1001.), now=1001.)
    assert calibration.groups == groups


def test_failed_complete_validation_preserves_neutral_without_hiding_failure():
    calibration = complete(transform=lambda step, index, sample:
        {**sample, "iris_x": sample["iris_x"] + .1} if step == 0 else sample)
    status = calibration.use_saved()
    assert status["phase"] == "failed" and status["saved_points"] == 5
    assert status["calibration_mode"] == "partial" and not status["calibrated"]
    assert status["head_calibrated"] and status["gaze_monitoring"] == "unavailable"
    assert calibration.profile is None


@pytest.mark.parametrize("x,y", [(-1., .5), (2., .5), (.5, -1.), (.5, 2.)])
def test_approximate_gaze_is_reported_without_enforcement_evidence(x, y):
    profile = partial(3).partial_profile
    estimate = estimate_screen_gaze(observation(x, y), profile)
    assert estimate["gaze_valid"] and estimate["screen_outside"]
    assert estimate["gaze_source"] == "approximate_screen"
    assert estimate["gaze_reason"] == "partial_calibration"
    assert not estimate["gaze_outside_confirmable"]


def test_saved_center_supplies_head_pose_independently_of_eyes():
    profile = partial(1).partial_profile
    estimate = estimate_screen_gaze(observation(yaw=8., eye_open=False), profile)
    assert estimate["head_pose_valid"] and estimate["head_outside"]
    assert not estimate["gaze_valid"] and not estimate["gaze_outside_confirmable"]
    assert estimate["gaze_screen_x"] is None


@pytest.mark.parametrize("covariance", [None, [[float("nan"), 0], [0, 0]], [[-1., 0], [0, 0]],
                                         [[.1, 0], [0, .1]]])
def test_invalid_or_noisy_partial_fit_keeps_only_saved_head_pose(covariance):
    calibration = partial(3)
    if covariance is None:
        del calibration.group_covariances[1]
    else:
        calibration.group_covariances[1] = covariance
    status = calibration.use_saved()
    assert status["head_calibrated"] and status["gaze_monitoring"] == "unavailable"
    assert not status["calibrated"]


def test_constant_eye_measurements_cannot_make_an_approximate_mapping():
    calibration = ScreenCalibration(now=0.)
    for _ in range(3):
        feed_point(calibration, lambda step, index, sample: {**sample, "iris_x": .5, "iris_y": .5})
    assert calibration.use_saved()["gaze_monitoring"] == "unavailable"


def test_matching_cancel_keeps_points_but_participant_reset_clears_everything(tmp_path):
    camera = Camera(tmp_path, lambda *args: None)
    camera.calibration = partial(3)
    saved_id = camera.calibration.id
    status = camera.cancel_calibration(calibration_id=saved_id)
    assert status["saved_points"] == 3 and status["gaze_monitoring"] == "approximate"
    assert camera.status()["head_calibrated"] and camera.baseline is not None
    camera.cancel_calibration()
    state = camera.status()
    assert camera.calibration is None and camera.baseline is None
    assert state["calibration_mode"] == "none" and not state["head_calibrated"]
    assert state["gaze_screen_x"] is None and state["head_yaw"] is None


def test_stale_cancel_does_not_freeze_or_restore_another_attempt(tmp_path):
    camera = Camera(tmp_path, lambda *args: None)
    previous = partial(3)
    camera.calibration = partial(1)
    current = camera.calibration
    camera.cancel_calibration(calibration_id=previous.id)
    assert camera.calibration is current and current.active and len(current.groups) == 1


@pytest.mark.parametrize("count", [0, 1, 3, 5])
def test_camera_start_contract_publishes_capabilities_and_freezes_collection(tmp_path, count):
    camera = Camera(tmp_path, lambda *args: None)
    camera.calibration = partial(count)
    status = camera.use_calibration()
    assert not camera.calibration.active
    assert status["calibration"]["saved_points"] == count
    signals = observation(seq=999, at=2000.)
    camera._calibrate_and_gaze(signals, None)
    for field in ("calibrated", "calibration_mode", "gaze_monitoring", "head_calibrated"):
        assert signals[field] == status[field]
    assert signals["calibrated"] is (count == 5)
    assert len(camera.calibration.groups) == count


def test_screen_geometry_change_disables_saved_profiles_but_retains_count(tmp_path):
    camera = Camera(tmp_path, lambda *args: None)
    camera.calibration = complete()
    status = camera.use_calibration(viewport={**VIEWPORT, "screen_width": 1366})
    assert status["calibration"]["saved_points"] == 5
    assert status["calibration_mode"] == "none" and not status["head_calibrated"]
    assert camera.baseline is None and not status["calibrated"]


def test_normal_window_dimensions_do_not_invalidate_same_screen(tmp_path):
    camera = Camera(tmp_path, lambda *args: None)
    camera.calibration = complete()
    status = camera.use_calibration(viewport={**VIEWPORT, "width": 1100, "height": 680})
    assert status["calibrated"] and status["calibration_mode"] == "full"


def test_recalibration_and_camera_stop_clear_partial_capabilities(tmp_path, monkeypatch):
    monkeypatch.setattr("qorgau.vision.time.monotonic", lambda: 100.)
    camera = Camera(tmp_path, lambda *args: None)
    camera.calibration = partial(3)
    camera._capture_at = camera._analyzed_at = 100.
    camera.state.update(running=True, camera_ok=True, analysis_ready=True)
    camera.use_calibration()
    camera.calibrate()
    assert camera.baseline is None and camera.state["calibration_mode"] == "none"
    assert camera.calibration.groups == []
    camera.calibration = partial(3)
    camera.use_calibration()
    camera.stop()
    assert camera.calibration is None and camera.baseline is None
    assert camera.state["calibration_mode"] == "none" and not camera.state["head_calibrated"]


def test_public_partial_status_does_not_export_raw_eye_data():
    status = partial(3).use_saved()
    assert not {"groups", "group_covariances", "neutral", "partial_profile", "weights"}.intersection(status)
