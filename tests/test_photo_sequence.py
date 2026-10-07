"""Observable phone sequences only; these are not a photography accuracy test."""
import json

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from qorgau.phone_pose import PhonePoseTracker


FACE = [[240, 60, 160, 220]]
UPWARD_PATH = [250, 220, 190, 160, 130, 100, 100, 100, 100, 100]


def scene(y=100, x=280, side_hidden=False):
    box = [x, y, 80, 160]
    frame = np.full((480, 640, 3), 160, np.uint8)
    left, right = (x + 34, x + 46) if side_hidden else (x, x + 80)
    cv2.rectangle(frame, (left, y), (right, y + 160), (20, 20, 20), -1)
    return frame, [{"box": box, "confidence": 0.9}]


def sequence(tracker, face_boxes=FACE, side_hidden=False, start=0.0, x=280):
    results = []
    for index, y in enumerate(UPWARD_PATH):
        frame, detections = scene(y, x, side_hidden)
        results.append(tracker.update(frame, detections, now=start + index / 10,
                                      face_boxes=face_boxes))
    return results


def test_raise_then_hold_near_face_emits_possible_photo_before_phone_removal_delay():
    results = sequence(PhonePoseTracker())
    assert results[0]["phone_aim_details"]["stage"] == "detected"
    assert any(result["phone_raised"] for result in results[:-1])
    assert not any(result["phone_photo_attempt"] for result in results[:8])
    result = results[-1]  # 0.9 s: before the 1.5 s automatic phone removal gate.
    assert result["phone_photo_attempt"]
    details = result["photo_attempt_details"]
    assert details["stage"] == "possible_photo"
    assert details["near_face"] and details["photo_hold_seconds"] >= 0.35
    assert "observed_raise_then_face_level_hold" in details["reasons"]
    assert details["camera_side_known"] is False
    assert details["photo_proven"] is False
    assert all(pose["photo_proven"] is False for pose in result["phone_pose_tracks"])
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("face_boxes", [None, [], [[10, 10, 70, 80]], [[240, 330, 160, 130]]])
def test_raise_and_plane_without_nearby_visible_face_is_not_photo_sequence(face_boxes):
    results = sequence(PhonePoseTracker(), face_boxes=face_boxes)
    assert results[-1]["phone_aimed"]
    assert not any(result["phone_photo_attempt"] for result in results)
    assert not results[-1]["photo_attempt_details"]["near_face"]


def test_already_raised_stationary_phone_has_no_observed_raise_sequence():
    tracker = PhonePoseTracker()
    frame, detections = scene()
    for index in range(15):
        result = tracker.update(frame, detections, now=index / 10, face_boxes=FACE)
        assert not result["phone_photo_attempt"]
    assert result["phone_aimed"]
    assert not result["phone_raised"]
    assert "no_recent_observed_raise" in result["photo_attempt_details"]["reasons"]


def test_edge_on_or_hidden_phone_plane_does_not_become_photography_cue():
    results = sequence(PhonePoseTracker(), side_hidden=True)
    assert any(result["phone_raised"] for result in results)
    assert not any(result["phone_photo_attempt"] for result in results)
    assert results[-1]["photo_attempt_details"]["camera_side_known"] is False


@pytest.mark.parametrize("interruption", ["missed_detection", "analysis_gap", "different_phone"])
def test_interrupted_or_different_phone_cannot_reuse_observed_raise(interruption):
    tracker = PhonePoseTracker()
    assert sequence(tracker)[-1]["phone_photo_attempt"]
    frame, detections = scene()
    now = 1.0
    if interruption == "missed_detection":
        tracker.update(frame, [], now=now, face_boxes=FACE)
        now += 0.1
    elif interruption == "analysis_gap":
        now = 2.0
    else:
        frame, detections = scene(x=440)
    for index in range(6):
        result = tracker.update(frame, detections, now=now + index / 10, face_boxes=FACE)
        assert not result["phone_photo_attempt"]
    assert result["photo_attempt_details"]["observed_raise_age_seconds"] is None


def test_missing_face_interrupts_face_context_hold():
    tracker = PhonePoseTracker()
    sequence(tracker)
    frame, detections = scene()
    assert not tracker.update(frame, detections, now=1.0, face_boxes=[])["phone_photo_attempt"]
    for now in [1.1, 1.2, 1.3, 1.4]:
        assert not tracker.update(frame, detections, now=now, face_boxes=FACE)["phone_photo_attempt"]
    assert tracker.update(frame, detections, now=1.5, face_boxes=FACE)["phone_photo_attempt"]


def test_old_raise_is_not_refreshed_by_stationary_frames_or_late_face():
    tracker = PhonePoseTracker()
    sequence(tracker, face_boxes=[])
    frame, detections = scene()
    for index in range(10, 35):
        result = tracker.update(frame, detections, now=index / 10, face_boxes=[] if index < 27 else FACE)
        assert not result["phone_photo_attempt"]
    assert result["photo_attempt_details"]["observed_raise_age_seconds"] > 2.0
    assert not result["phone_raised"]


def test_leaving_screen_zone_invalidates_raise_sequence():
    tracker = PhonePoseTracker()
    sequence(tracker)
    frame, detections = scene(y=300)
    tracker.update(frame, detections, now=1.0, face_boxes=FACE)
    frame, detections = scene()
    # This jump is too far for the same-phone match and cannot inherit its rise.
    result = tracker.update(frame, detections, now=1.1, face_boxes=FACE)
    assert not result["phone_photo_attempt"]


def test_largest_face_is_used_not_an_unrelated_small_face():
    faces = [[240, 60, 60, 80], [0, 280, 200, 195]]
    result = sequence(PhonePoseTracker(), face_boxes=faces)[-1]
    assert result["photo_attempt_details"]["primary_face_box"] == [0.0, 280.0, 200.0, 195.0]
    assert not result["phone_photo_attempt"]


def test_nonfinite_or_invalid_boxes_and_frame_times_never_escape_to_json():
    tracker = PhonePoseTracker()
    frame, detections = scene()
    invalid_boxes = [[float("nan"), 10, 80, 160], [0, 0, float("inf"), 10], [1, 2], [0, 0, -1, 10]]
    for index, invalid in enumerate(invalid_boxes):
        result = tracker.update(frame, [{"box": invalid, "confidence": 0.9}],
                                now=index / 10, face_boxes=invalid_boxes)
        assert not result["phone_pose_tracks"]
        json.dumps(result, allow_nan=False)
    result = tracker.update(frame, detections, now=float("nan"), face_boxes=FACE)
    assert not result["phone_photo_attempt"]
    json.dumps(result, allow_nan=False)
