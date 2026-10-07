"""Regression checks for calibrated gaze geometry, not hardware accuracy."""
import math
from types import SimpleNamespace

import pytest

from qorgau.gaze import angle_delta, calibration_sample, estimate_gaze, extract_eye_features, fit_neutral
from qorgau.rules import Policy
from qorgau.vision import Camera


BASELINE = [4.0, 3.0, 0.5, 0.5, 0.2]


def observation(**changes):
    return {"faces": 1, "landmark_faces": 1, "landmarks_valid": True,
            "eye_open": True, "eye_features_valid": True, "brightness": 110,
            "yaw": 4.0, "pitch": 3.0, "iris_x": 0.5, "iris_y": 0.5,
            "head_vertical": 0.2, **changes}


def estimate(**changes):
    return estimate_gaze(observation(**changes), BASELINE, Policy())


def eye_mesh(rotation=0, width=1280, height=720, iris_x=0.5, iris_y=0.5,
             aperture=12, other_x=None):
    pixels = {33: (400, 300), 133: (448, 300), 362: (520, 300), 263: (568, 300),
              159: (424, 300-aperture/2), 145: (424, 300+aperture/2),
              386: (544, 300-aperture/2), 374: (544, 300+aperture/2),
              468: (400+48*iris_x, 300-aperture/2+aperture*iris_y),
              473: (520+48*(iris_x if other_x is None else other_x), 300-aperture/2+aperture*iris_y),
              1: (484, 340)}
    radians = math.radians(rotation)
    cosine, sine = math.cos(radians), math.sin(radians)
    points = [SimpleNamespace(x=0.5, y=0.5) for _ in range(478)]
    for index, (x, y) in pixels.items():
        x, y = x-484, y-300
        points[index] = SimpleNamespace(x=(484+x*cosine-y*sine)/width,
                                        y=(300+x*sine+y*cosine)/height)
    return points


@pytest.mark.parametrize("rotation", [-35, 0, 35])
@pytest.mark.parametrize("size", [(1280, 720), (640, 640), (1920, 1080)])
def test_eye_features_do_not_change_with_head_roll_or_frame_aspect(rotation, size):
    width, height = size
    features = extract_eye_features(eye_mesh(rotation, width, height, 0.7, 0.8), width, height)
    assert features["eye_open"] and features["eye_features_valid"]
    assert features["iris_x"] == pytest.approx(0.7)
    assert features["iris_y"] == pytest.approx(0.8)
    assert features["head_vertical"] == pytest.approx(40/168)


@pytest.mark.parametrize("aperture", [0, 3, 4.8])
def test_closed_eyes_do_not_yield_iris_gaze(aperture):
    features = extract_eye_features(eye_mesh(aperture=aperture), 1280, 720)
    assert features["eye_open"] is False
    assert features["eye_features_valid"] is False


def test_occluded_or_contradictory_second_eye_is_not_averaged_into_accusation():
    features = extract_eye_features(eye_mesh(iris_x=0.8, other_x=0.2), 1280, 720)
    assert features["eye_visible_count"] == 2
    assert features["eye_open"] is False


def test_corrupt_landmarks_are_unavailable():
    points = eye_mesh()
    points[468].x = float("nan")
    assert extract_eye_features(points, 1280, 720)["eye_open"] is False
    assert extract_eye_features([], 1280, 720)["eye_open"] is False


@pytest.mark.parametrize("changes,direction,source", [
    ({}, "center", "neutral"),
    ({"yaw": 40}, "left", "head"),
    ({"yaw": -40}, "right", "head"),
    ({"iris_x": 0.9}, "left", "iris"),
    ({"iris_x": 0.1}, "right", "iris"),
    ({"pitch": 40, "head_vertical": 0.4}, "down", "head"),
    ({"pitch": -40, "head_vertical": 0.0}, "up", "head"),
    ({"iris_y": 0.9}, "down", "iris"),
    ({"iris_y": 0.1}, "up", "iris"),
    ({"yaw": 40, "iris_x": 0.9}, "left", "head+iris"),
])
def test_participant_directions_after_neutral_calibration(changes, direction, source):
    result = estimate(**changes)
    assert result["gaze_valid"]
    assert result["gaze_direction"] == direction
    assert result["gaze_source"] == source
    assert result["looking_side"] == (direction in {"left", "right"})
    assert result["looking_down"] == (direction == "down")


def test_head_up_is_not_mislabeled_down_when_nose_ratio_is_noisy():
    assert estimate(pitch=-40, head_vertical=0.4)["looking_down"] is False


def test_diagonal_preserves_both_observed_axes():
    result = estimate(iris_x=0.9, iris_y=0.9)
    assert result["gaze_direction"] == "down"
    assert result["gaze_horizontal"] == "left"
    assert result["looking_left"] and result["looking_down"]


@pytest.mark.parametrize("changes,reason", [
    ({"faces": 0}, "no_face"),
    ({"faces": 2}, "multiple_faces"),
    ({"brightness": 29}, "low_light"),
    ({"eye_open": False}, "eyes_closed"),
    ({"landmark_faces": 0, "landmarks_valid": False}, "invalid_landmarks"),
    ({"iris_x": float("nan")}, "invalid_landmarks"),
    ({"eye_features_valid": False}, "invalid_landmarks"),
])
def test_unavailable_gaze_cannot_set_direction_flags(changes, reason):
    result = estimate(**{"yaw": 45, "pitch": 45, "head_vertical": 0.4, **changes})
    assert result["gaze_direction"] == "unknown"
    assert result["gaze_reason"] == reason
    assert result["gaze_valid"] is False
    assert not any(result[key] for key in ("looking_side", "looking_left", "looking_right", "looking_down", "looking_up"))


def test_closed_eyes_still_report_head_angles_separately():
    result = estimate(yaw=34, pitch=18, eye_open=False)
    assert result["head_yaw"] == 30
    assert result["head_pitch"] == 15
    assert result["gaze_direction"] == "unknown"


def test_opposing_iris_and_head_cues_are_unknown_not_a_forced_accusation():
    result = estimate(yaw=45, iris_x=0.1)
    assert not result["gaze_valid"]
    assert result["gaze_reason"] == "conflicting_cues"


def test_neutral_calibration_requires_full_sample_and_rejects_eye_motion():
    stable = [BASELINE[:] for _ in range(30)]
    assert fit_neutral(stable[:29]) is None
    assert fit_neutral(stable) == BASELINE
    for i, row in enumerate(stable):
        row[2] = 0.1 if i % 2 else 0.9
    assert fit_neutral(stable) is None


def test_euler_boundary_does_not_break_stable_calibration():
    samples = [[0, (179 if i % 2 else -179), 0.5, 0.5, 0.2] for i in range(30)]
    neutral = fit_neutral(samples)
    assert neutral is not None
    assert abs(neutral[1]) == 180
    assert angle_delta(179, neutral[1]) == -1


def test_calibration_rejects_yolo_face_without_face_mesh():
    assert calibration_sample(observation(landmark_faces=0, landmarks_valid=False)) is None


def test_camera_never_accepts_legacy_neutral_only_calibration(tmp_path):
    camera = Camera(tmp_path, lambda *args: None)
    camera.calibrating = True
    for _ in range(30):
        sample = observation()
        camera._calibrate_and_gaze(sample, None)
    assert not sample["calibrated"]
    assert sample["calibration_version"] == 0
    assert sample["gaze_direction"] == "unknown"


def test_old_baseline_cannot_arm_production_camera(tmp_path):
    camera = Camera(tmp_path, lambda *args: None)
    camera.baseline = BASELINE[:]
    sample = observation()
    camera._calibrate_and_gaze(sample, None)
    assert sample["calibrated"] is False
    assert sample["calibration_version"] == 0
