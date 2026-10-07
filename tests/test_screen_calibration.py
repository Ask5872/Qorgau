"""Five-point protocol and quality regressions; not physical webcam validation."""
import math

import pytest

from qorgau.gaze import estimate_screen_gaze
from qorgau.screen_calibration import FIT_INDICES, ScreenCalibration, TARGETS
from qorgau.vision import Camera


VIEWPORT = {"width": 1920, "height": 1080, "device_pixel_ratio": 1,
            "screen_width": 1920, "screen_height": 1080}


def observation(x=.5, y=.5, seq=1, at=1., polarity=1, **changes):
    return {"faces": 1, "landmark_faces": 1, "landmarks_valid": True, "pose_valid": True,
            "camera_ok": True, "analysis_ready": True,
            "brightness": 100, "eye_open": True, "eye_features_valid": True,
            "yaw": 2., "pitch": -1., "roll": 3., "head_vertical": .2,
            "iris_x": .5 + polarity * .2 * (x - .5) + .01 * (y - .5),
            "iris_y": .5 + .5 * (y - .5) + .01 * (x - .5),
            "analyzed_seq": seq, "analyzed_at": at, **changes}


def feed_point(calibration, transform=None, period=.11, latency=0., polarity=1):
    now = calibration.last_at + latency + .2 if calibration.last_seq >= 0 else calibration.started_at
    step, sequence = calibration.step, calibration.last_seq
    _, x, y = TARGETS[step]
    calibration.acknowledge(step, VIEWPORT, now=now)
    for index in range(calibration.MIN_SAMPLES):
        captured_at = now + .5 + index * period
        frame = observation(x, y, seq=sequence + index + 1, at=captured_at, polarity=polarity)
        if transform:
            frame = transform(step, index, frame)
        calibration.collect(frame, now=captured_at + latency)
    return captured_at + latency


def complete(polarity=1, transform=None, period=.11, latency=0.):
    calibration = ScreenCalibration(now=0.)
    for step in range(len(TARGETS)):
        feed_point(calibration, transform, period, latency, polarity)
        if calibration.step != step + 1 or not calibration.active:
            break
    return calibration


def scaled_eye_observation(step, index, sample, ranges=(.02, .04), noise=0.):
    _, x, y = TARGETS[step]
    jitter = noise * (1 if index % 2 else -1)
    return {**sample, "iris_x": .5 + ranges[0] * (x - .5) / .84 + jitter,
            "iris_y": .5 + ranges[1] * (y - .5) / .84 - jitter}


@pytest.mark.parametrize("polarity", [-1, 1])
def test_five_points_use_four_corners_to_fit_and_independent_center_to_validate(polarity):
    calibration = complete(polarity)
    assert calibration.phase == "complete"
    status = calibration.status()
    assert status["mode"] == calibration.profile["mode"] == "simple_5"
    assert status["total"] == status["saved_points"] == 5 and status["progress"] == 100
    assert FIT_INDICES == (1, 2, 3, 4)
    assert status["fit_points"] == calibration.quality["fit_points"] == 4
    assert status["validation_points"] == calibration.quality["validation_points"] == 1
    assert calibration.profile["version"] == 2 and calibration.quality["samples_per_point"] == 6
    assert len(calibration.quality["point_errors"]) == 5
    assert "point_retry" not in status and "retry_kind" not in status
    for x, y in ((0, 0), (.01, .99), (.5, .5), (1, 1)):
        result = estimate_screen_gaze(observation(x, y, polarity=polarity), calibration.profile)
        assert result["gaze_valid"]
        assert result["gaze_screen_x"] == pytest.approx(x, abs=.0001)
        assert result["gaze_screen_y"] == pytest.approx(y, abs=.0001)


def test_wrong_center_cannot_improve_training_fit_or_trigger_a_quality_retry():
    calibration = complete(transform=lambda step, index, sample:
                           {**sample, "iris_x": sample["iris_x"] + .1} if step == 0 else sample)
    assert calibration.phase == "failed" and calibration.issue == "validation_error"
    assert calibration.quality["rms_error"] < .000001
    assert calibration.quality["max_fit_error"] < .000001
    assert calibration.quality["validation_error"] > .1
    assert calibration.quality["point_errors"][0] == calibration.quality["validation_error"]
    assert calibration.step == 5 and not calibration.active and calibration.profile is None


def test_bad_corner_fit_fails_directly_without_revisiting_points():
    calibration = complete(transform=lambda step, index, sample:
                           {**sample, "iris_x": sample["iris_x"] + .2} if step == 2 else sample)
    assert calibration.phase == "failed" and calibration.issue == "fit_error"
    assert calibration.step == 5 and calibration.profile is None
    assert calibration.status()["saved_points"] == 5
    assert calibration.quality["rms_error"] > .075 or calibration.quality["max_fit_error"] > .14


@pytest.mark.parametrize("x,y", [(-.001, .5), (1.001, .5), (.5, -.001), (.5, 1.001)])
def test_five_point_profile_retains_uncertainty_for_runtime_boundary_decisions(x, y):
    result = estimate_screen_gaze(observation(x, y), complete().profile)
    assert result["gaze_valid"] and result["screen_outside"]
    assert result["gaze_boundary_uncertain"] and not result["gaze_outside_confirmable"]
    assert result["gaze_reason"] == "boundary_uncertain"


@pytest.mark.parametrize("changes", [{"eye_open": False}, {"eye_features_valid": False}])
def test_head_movement_is_independent_of_blink_and_bad_iris(changes):
    result = estimate_screen_gaze(observation(yaw=7.1, **changes), complete().profile)
    assert result["head_pose_valid"] and result["head_outside"]
    assert not result["gaze_valid"]


@pytest.mark.parametrize("axis,neutral", [("yaw", 2), ("pitch", -1), ("roll", 3)])
def test_runtime_head_limit_remains_five_degrees(axis, neutral):
    profile = complete().profile
    assert not estimate_screen_gaze(observation(**{axis: neutral + 4.99}), profile)["head_outside"]
    assert estimate_screen_gaze(observation(**{axis: neutral + 5.01}), profile)["head_outside"]


@pytest.mark.parametrize("changes", [{"pose_valid": False}, {"faces": 2}, {"landmarks_valid": False},
                                    {"brightness": 10}, {"roll": math.nan}])
def test_invalid_pose_never_generates_head_violation(changes):
    result = estimate_screen_gaze(observation(yaw=40, **changes), complete().profile)
    assert not result["head_pose_valid"] and not result["head_outside"]


def test_no_frames_are_accepted_before_visible_target_acknowledgement():
    calibration = ScreenCalibration(now=0)
    for sequence in range(20):
        calibration.collect(observation(seq=sequence, at=sequence / 10), now=sequence / 10)
    assert not calibration.samples and not calibration.groups


def test_target_ack_is_idempotent_but_stale_step_is_rejected():
    calibration = ScreenCalibration(now=0)
    calibration.acknowledge(0, VIEWPORT, now=1)
    calibration.acknowledge(0, VIEWPORT, now=2)
    assert calibration.ack_at == 1
    with pytest.raises(ValueError):
        calibration.acknowledge(1, VIEWPORT, now=2)


@pytest.mark.parametrize("frame,now", [(observation(at=.9), 2.), (observation(at=1.4), 2.),
    (observation(at=1.5), 4.5), (observation(at=3.), 2.), (observation(at=math.nan), 2.)])
def test_capture_time_freshness_and_settling_checks_are_preserved(frame, now):
    calibration = ScreenCalibration(now=0)
    calibration.acknowledge(0, VIEWPORT, now=1)
    calibration.collect(frame, now=now)
    assert not calibration.samples


@pytest.mark.parametrize("sequence", [True, "1", 1.2, None])
def test_noninteger_frame_sequences_are_rejected(sequence):
    calibration = ScreenCalibration(now=0)
    calibration.acknowledge(0, VIEWPORT, now=0)
    calibration.collect(observation(seq=sequence, at=.5), now=.5)
    assert not calibration.samples


def test_repolling_one_frame_cannot_complete_a_point():
    calibration = ScreenCalibration(now=0)
    calibration.acknowledge(0, VIEWPORT, now=0)
    for _ in range(20):
        calibration.collect(observation(seq=1, at=.5), now=.5)
    assert len(calibration.samples) == 1 and calibration.step == 0


@pytest.mark.parametrize("viewport", [None, {"width": 1920, "height": 1080},
    {**VIEWPORT, "width": 1100}, {**VIEWPORT, "height": 700}, {**VIEWPORT, "device_pixel_ratio": 0}])
def test_full_screen_geometry_is_required(viewport):
    calibration = ScreenCalibration(now=0)
    with pytest.raises(ValueError):
        calibration.acknowledge(0, viewport, now=0)
    assert calibration.ack_at is None


def test_monitor_geometry_change_invalidates_calibration():
    calibration = ScreenCalibration(now=0)
    calibration.acknowledge(0, VIEWPORT, now=0)
    with pytest.raises(ValueError):
        calibration.acknowledge(0, {**VIEWPORT, "width": 1280, "screen_width": 1280}, now=.1)
    assert calibration.phase == "failed" and calibration.profile is None


@pytest.mark.parametrize("changes,issue", [({"faces": 0}, "no_face"),
    ({"camera_ok": False}, "camera_unavailable"), ({"analysis_ready": False}, "camera_unavailable"),
    ({"pose_valid": False}, "pose_unavailable"), ({"brightness": 10}, "low_light"),
    ({"eye_open": False}, "eyes_closed"), ({"eye_features_valid": False}, "eyes_unavailable")])
def test_one_bad_frame_under_two_fps_pauses_without_erasing_good_frames(changes, issue):
    calibration = ScreenCalibration(now=0)
    calibration.acknowledge(0, VIEWPORT, now=0)
    for sequence in range(1, 4):
        captured_at = .5 + (sequence - 1) * .56
        calibration.collect(observation(seq=sequence, at=captured_at), now=captured_at)
    calibration.collect(observation(seq=4, at=2.18, **changes), now=2.18)
    assert len(calibration.samples) == 3 and calibration.issue == issue
    calibration.collect(observation(seq=5, at=2.74), now=2.74)
    assert len(calibration.samples) == 4 and calibration.restart_count == 0


@pytest.mark.parametrize("changes,issue", [({"faces": 0}, "no_face"),
    ({"camera_ok": False}, "camera_unavailable"), ({"pose_valid": False}, "pose_unavailable"),
    ({"eye_open": False}, "eyes_closed")])
def test_long_loss_restarts_only_unfinished_point_with_visible_reason(changes, issue):
    calibration = ScreenCalibration(now=0)
    feed_point(calibration)
    saved = calibration.groups[0][:]
    acknowledged_at = calibration.last_at + .2
    calibration.acknowledge(1, VIEWPORT, now=acknowledged_at)
    sequence = calibration.last_seq
    for index in range(3):
        at = acknowledged_at + .5 + index * .1
        calibration.collect(observation(seq=sequence + index + 1, at=at), now=at)
    sequence = calibration.last_seq
    for index, offset in enumerate((.8, 1.4, 3.0, 3.5)):
        at = acknowledged_at + offset
        calibration.collect(observation(seq=sequence + index + 1, at=at, **changes), now=at)
    status = calibration.status(now=at)
    assert not calibration.samples and calibration.groups[0] == saved
    assert status["restart_count"] == status["current_point_restarts"] == 1
    assert status["last_restart_reason"] == issue
    assert status["saved_points"] == 1 and status["progress"] == 20


def test_second_face_immediately_discards_only_current_samples():
    calibration = ScreenCalibration(now=0)
    calibration.acknowledge(0, VIEWPORT, now=0)
    calibration.collect(observation(seq=1, at=.5), now=.5)
    calibration.collect(observation(seq=2, at=.6, faces=2), now=.6)
    assert not calibration.samples
    assert calibration.status(now=.6)["last_restart_reason"] == "multiple_faces"
    assert calibration.restart_count == 1


def test_unobserved_gap_longer_than_three_seconds_does_not_bridge_samples():
    calibration = ScreenCalibration(now=0)
    calibration.acknowledge(0, VIEWPORT, now=0)
    calibration.collect(observation(seq=1, at=.5), now=.5)
    calibration.collect(observation(seq=2, at=3.6), now=3.6)
    assert len(calibration.samples) == 1 and calibration.last_restart_reason == "sample_gap"


def test_head_acquisition_stays_within_four_degrees_of_initial_center():
    calibration = ScreenCalibration(now=0)
    feed_point(calibration)
    original = calibration.initial_head_reference
    now = calibration.last_at + .2
    calibration.acknowledge(1, VIEWPORT, now=now)
    calibration.collect(observation(seq=calibration.last_seq + 1, at=now + .5, yaw=6.1, eye_open=False), now=now + .5)
    assert calibration.issue == "head_moved" and not calibration.samples
    assert calibration.initial_head_reference == original


def test_one_outlier_can_be_discarded_without_accepting_an_unstable_average():
    calibration = ScreenCalibration(now=0)
    calibration.acknowledge(0, VIEWPORT, now=0)
    for index in range(7):
        at = .5 + index * .11
        calibration.collect(observation(seq=index + 1, at=at, iris_x=.7 if index == 3 else .5), now=at)
        if index == 5:
            assert calibration.step == 0 and calibration.inlier_count == 5
    assert calibration.step == 1 and calibration.groups[0][2] == .5


def test_alternating_clusters_never_form_a_fake_stable_point_and_storage_is_bounded():
    calibration = ScreenCalibration(now=0)
    calibration.acknowledge(0, VIEWPORT, now=0)
    for index in range(100):
        at = .5 + index * .1
        calibration.collect(observation(seq=index + 1, at=at, iris_x=.54 if index % 2 else .46), now=at)
    assert calibration.step == 0 and calibration.issue == "unstable"
    assert calibration.profile is None and not calibration.groups
    assert len(calibration.samples) <= calibration.MAX_SAMPLES


def test_six_fast_frames_are_not_finished_before_minimum_observation_span():
    calibration = ScreenCalibration(now=0)
    calibration.acknowledge(0, VIEWPORT, now=0)
    for index in range(6):
        at = .5 + index * .01
        calibration.collect(observation(seq=index + 1, at=at), now=at)
    assert calibration.step == 0 and calibration.inlier_count == 6
    assert calibration.status(now=at)["point_progress"] < 100


@pytest.mark.parametrize("period,latency", [(2., .05), (2., 1.1), (2.6, 2.5)])
def test_all_five_points_complete_with_slow_serial_analysis(period, latency):
    assert period >= latency
    calibration = complete(period=period, latency=latency)
    assert calibration.phase == "complete" and calibration.profile
    assert calibration.last_at + latency < calibration.TOTAL_TIMEOUT
    assert calibration.restart_count == 0
    assert calibration.status()["last_frame_age_ms"] == pytest.approx(latency * 1000)
    assert 12 <= calibration.sample_window_seconds <= 30


@pytest.mark.parametrize("changes", [{"faces": 0}, {"pose_valid": False}, {"eye_open": False}, {"camera_ok": False}])
def test_five_points_complete_with_intermittent_tracker_loss_at_slow_fps(changes):
    calibration = ScreenCalibration(now=0)
    now, sequence, progress = 0., 0, []
    for step, (_, x, y) in enumerate(TARGETS):
        calibration.acknowledge(step, VIEWPORT, now=now)
        for index in range(20):
            at = now + .5 + index * .56
            sequence += 1
            calibration.collect(observation(x, y, seq=sequence, at=at, **(changes if index % 4 == 3 else {})), now=at)
            progress.append(calibration.status(now=at)["progress"])
            if calibration.step != step:
                break
        assert calibration.step == step + 1
        now = at + .2
    assert calibration.phase == "complete" and progress == sorted(progress)
    assert calibration.restart_count == 0


@pytest.mark.parametrize("latency,accepted", [(2.999, True), (3., False), (3.001, False)])
def test_camera_freshness_boundary_is_shared_and_exclusive(latency, accepted):
    assert ScreenCalibration.MAX_FRAME_AGE_SECONDS == Camera.ANALYSIS_MAX_AGE == 3.
    calibration = ScreenCalibration(now=0)
    calibration.acknowledge(0, VIEWPORT, now=0)
    calibration.collect(observation(at=1.), now=1. + latency)
    assert bool(calibration.samples) is accepted
    if not accepted:
        assert calibration.issue == "slow_analysis"


def test_target_painting_delay_is_excluded_from_capture_cadence():
    calibration = ScreenCalibration(now=0)
    feed_point(calibration, period=.1)
    before = tuple(calibration.valid_frame_intervals)
    now = calibration.last_at + .4
    calibration.acknowledge(1, VIEWPORT, now=now)
    calibration.collect(observation(seq=calibration.last_seq + 1, at=now + .5), now=now + .5)
    assert tuple(calibration.valid_frame_intervals) == before


def test_deadlines_apply_without_frames_and_never_arm_a_partial_profile():
    calibration = ScreenCalibration(now=0)
    calibration.acknowledge(0, VIEWPORT, now=0)
    assert calibration.check_timeout(now=calibration.TARGET_TIMEOUT + .1)
    assert calibration.phase == "failed" and calibration.profile is None
    unacknowledged = ScreenCalibration(now=0)
    assert unacknowledged.check_timeout(now=unacknowledged.TOTAL_TIMEOUT + .1)


def test_revisions_order_point_updates_pauses_and_cancellation():
    calibration = ScreenCalibration(now=0)
    revisions = [calibration.status()["revision"]]
    calibration.acknowledge(0, VIEWPORT, now=0)
    revisions.append(calibration.status()["revision"])
    for sequence, at, changes in [(1, .5, {}), (2, 1., {"faces": 0}), (3, 3.1, {"faces": 0})]:
        calibration.collect(observation(seq=sequence, at=at, **changes), now=at)
        revisions.append(calibration.status()["revision"])
    calibration.cancel()
    revisions.append(calibration.status()["revision"])
    assert all(a < b for a, b in zip(revisions, revisions[1:]))


def test_camera_publishes_only_complete_profile_and_cancel_clears_it(tmp_path):
    camera = Camera(tmp_path, lambda *args: None)
    camera.calibration = complete()
    signals = observation()
    camera._calibrate_and_gaze(signals, None)
    assert signals["calibrated"] and signals["calibration_version"] == 2
    camera.cancel_calibration()
    camera._calibrate_and_gaze(signals, None)
    assert not signals["calibrated"] and camera.baseline is None


def test_old_target_or_cancel_cannot_mutate_a_new_calibration(tmp_path, monkeypatch):
    monkeypatch.setattr("qorgau.vision.time.monotonic", lambda: 100.)
    camera = Camera(tmp_path, lambda *args: None)
    camera._capture_at = camera._analyzed_at = 100.
    camera.state.update(camera_ok=True, analysis_ready=True)
    first, second = camera.calibrate(), camera.calibrate()
    assert first["id"] != second["id"]
    assert camera.cancel_calibration(calibration_id=first["id"])["id"] == second["id"]
    with pytest.raises(ValueError):
        camera.calibration_target(0, VIEWPORT, calibration_id=first["id"])
    assert camera.calibration.ack_at is None


@pytest.mark.parametrize("noise", [0., .0001])
def test_small_stable_eye_motion_passes_geometry_fit_center_and_noise_checks(noise):
    calibration = complete(transform=lambda step, index, sample:
                           scaled_eye_observation(step, index, sample, noise=noise))
    assert calibration.phase == "complete" and calibration.profile
    assert calibration.quality["rms_error"] < .000001 and calibration.quality["validation_error"] < .000001
    assert calibration.quality["eye_range_x"] == pytest.approx(.02)
    assert calibration.quality["screen_jitter"] < .01


def test_perfect_medians_cannot_hide_large_single_frame_noise():
    calibration = complete(transform=lambda step, index, sample:
        scaled_eye_observation(step, index, sample, ranges=(.00002, .00004), noise=.0001))
    assert calibration.quality["rms_error"] < .000001 and calibration.quality["validation_error"] < .000001
    assert calibration.quality["screen_jitter"] > .1
    assert calibration.phase == "failed" and calibration.issue == "noise_error" and calibration.profile is None


def test_scaling_motion_and_noise_preserves_quality_and_uses_single_frame_variance():
    jitters = []
    for factor in (1., .1, .001):
        calibration = complete(transform=lambda step, index, sample:
            scaled_eye_observation(step, index, sample, ranges=(.02 * factor, .04 * factor), noise=.0001 * factor))
        assert calibration.phase == "complete"
        expected_sd = math.sqrt(6 / 5) * .0001 * factor
        assert calibration.quality["eye_noise_x"] == pytest.approx(expected_sd, rel=1e-8)
        jitters.append(calibration.quality["screen_jitter"])
    expected = 2 * math.sqrt(6 / 5) * .0001 * (.84 / .02)
    assert jitters == pytest.approx([expected] * 3, rel=1e-8)


def test_correlated_iris_noise_is_projected_with_full_covariance():
    import numpy as np
    covariance = np.asarray([[[1e-8, 1e-8], [1e-8, 1e-8]]])
    weights = np.asarray([[0., 0.], [100., 20.], [-100., 20.]])
    assert ScreenCalibration._projected_jitter(covariance, weights, np.ones(2), np) == pytest.approx(.008)
    calibration = complete(transform=lambda step, index, sample:
                           scaled_eye_observation(step, index, sample, noise=.0001))
    assert calibration.group_covariances[0][0][1] == pytest.approx(-6 / 5 * .0001 ** 2)


@pytest.mark.parametrize("invalid", [None, [[math.nan, 0.], [0., 0.]], [[-1e-8, 0.], [0., 0.]],
    [[1e-8, 1e-8], [0., 1e-8]], [[1e-8, 2e-8], [2e-8, 1e-8]], [[0., 0.]]])
def test_every_target_including_center_needs_valid_noise_statistics(invalid):
    calibration = complete()
    if invalid is None:
        del calibration.group_covariances[0]
    else:
        calibration.group_covariances[0] = invalid
    calibration._finish()
    assert calibration.phase == "failed" and calibration.issue == "noise_error" and calibration.profile is None


@pytest.mark.parametrize("transform,issue", [
    (lambda step, index, sample: {**sample, "iris_x": .5, "iris_y": .5}, "insufficient_eye_range"),
    (lambda step, index, sample: scaled_eye_observation(step, index, sample, ranges=(1e-15, 2e-15)), "insufficient_eye_range"),
    (lambda step, index, sample: {**sample, "iris_y": sample["iris_x"] * 2 - .5}, "degenerate_fit"),
])
def test_constant_or_inseparable_signals_fail_without_fake_head_only_success(transform, issue):
    calibration = complete(transform=transform)
    assert calibration.phase == "failed" and calibration.issue == issue and calibration.profile is None
    assert "eye_range_x" in calibration.quality and "eye_noise_x" in calibration.quality


def test_quality_diagnostics_only_expose_finite_aggregate_metrics():
    calibration = complete()
    allowed = {"fit_rms_limit", "fit_max_limit", "validation_limit", "head_limit_deg", "fit_points",
               "validation_points", "samples_per_point", "rms_error", "max_fit_error", "point_errors",
               "validation_error", "uncertainty", "eye_range_x", "eye_range_y", "eye_noise_x",
               "eye_noise_y", "screen_jitter", "screen_jitter_limit"}
    assert set(calibration.quality) <= allowed
    assert set(calibration.group_covariances) == set(range(5))
    for key, value in calibration.quality.items():
        if key == "point_errors":
            assert len(value) == 5 and all(math.isfinite(item) and item >= 0 for item in value)
        else:
            assert isinstance(value, (int, float)) and math.isfinite(value)
    assert "group_covariances" not in calibration.status() and "group_covariances" not in calibration.profile
