"""Synthetic geometry/temporal checks, not a real-world accuracy benchmark."""
import json

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from qorgau.phone_pose import PhonePoseTracker, plane_features


def scene(box=(280, 90, 80, 160), quad=None):
    frame = np.full((480, 640, 3), 160, np.uint8)
    if quad is None:
        x, y, w, h = box
        quad = [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]
    cv2.fillConvexPoly(frame, np.asarray(quad, np.int32), (20, 20, 20))
    return frame, [{"box": list(box), "confidence": 0.9}]


@pytest.mark.parametrize("box", [(280, 90, 80, 160), (250, 90, 160, 80)])
def test_clear_portrait_and_landscape_have_measured_plane(box):
    frame, detections = scene(box)
    result = plane_features(frame, box)
    assert result["plane_visible"] and result["plane_facing_camera"]
    assert result["quality"] > 0.8
    assert len(result["quad"]) == 4
    assert result["border_contrast"] > 50
    json.dumps(result)  # Native floats/bools are safe for API, logs and reports.


@pytest.mark.parametrize("box", [(300, 90, 8, 160), (300, 90, 26, 160), (300, 90, 12, 24)])
def test_edge_on_and_tiny_phone_cannot_produce_aim(box):
    frame, _ = scene(box)
    assert not plane_features(frame, box)["plane_facing_camera"]


def test_blank_crop_and_unrelated_rectangle_outside_detection_are_unknown():
    blank = np.full((480, 640, 3), 160, np.uint8)
    assert plane_features(blank, [280, 90, 80, 160])["reason"] == "insufficient_contrast"
    other, _ = scene((40, 90, 80, 160))
    assert not plane_features(other, [280, 90, 80, 160])["plane_visible"]


def test_strong_perspective_rejects_front_plane_proxy():
    # One end projects to less than half the width of the other.
    frame, _ = scene(quad=[[306, 90], [334, 90], [360, 250], [280, 250]])
    result = plane_features(frame, [280, 90, 80, 160])
    assert not result["plane_facing_camera"]


def test_aim_requires_distinct_frames_hold_and_retains_uncertainty():
    frame, detections = scene()
    tracker = PhonePoseTracker()
    for timestamp in [0, 0.1, 0.2, 0.3]:
        assert not tracker.update(frame, detections, timestamp)["phone_aimed"]
    assert not tracker.update(frame, detections, 0.3)["phone_aimed"]
    result = tracker.update(frame, detections, 0.4)
    assert result["phone_aimed"] and not result["phone_raised"]
    assert "stable_hold" in result["phone_aim_details"]["reasons"]
    assert result["phone_aim_details"]["camera_side_known"] is False
    assert result["phone_aim_details"]["photo_proven"] is False
    json.dumps(result)


def test_raise_tracks_motion_of_same_phone_then_can_settle_into_aim():
    tracker = PhonePoseTracker()
    results = []
    for i, y in enumerate([250, 220, 190, 160, 130, 100, 100, 100, 100, 100]):
        frame, detections = scene((280, y, 80, 160))
        results.append(tracker.update(frame, detections, i / 10))
    assert len({r["phone_track_id"] for r in results}) == 1
    assert not results[0]["phone_raised"]
    assert results[3]["phone_raised"]
    assert results[3]["phone_aim_details"]["rise_frame_fraction"] >= 0.1
    assert not results[3]["phone_aimed"]
    assert results[-1]["phone_aimed"]


def test_motion_and_low_position_prevent_aim_even_with_clear_plane():
    tracker = PhonePoseTracker()
    for i, x in enumerate([200, 240, 200, 240, 200, 240, 200]):
        frame, detections = scene((x, 90, 80, 160))
        assert not tracker.update(frame, detections, i / 10)["phone_aimed"]
    frame, detections = scene((280, 300, 80, 160))
    for i in range(6):
        assert not tracker.update(frame, detections, 1 + i / 10)["phone_aimed"]


def test_gap_missing_detection_and_different_phone_reset_hold():
    tracker = PhonePoseTracker()
    frame, detections = scene()
    for timestamp in [0, 0.1, 0.2, 0.3, 0.4]:
        result = tracker.update(frame, detections, timestamp)
    assert result["phone_aimed"]
    old_id = result["phone_track_id"]
    # A long analysis outage must not turn one stale frame into a held phone.
    result = tracker.update(frame, detections, 2.0)
    assert not result["phone_aimed"] and result["phone_track_id"] != old_id
    for timestamp in [2.1, 2.2, 2.3, 2.4]:
        result = tracker.update(frame, detections, timestamp)
    assert result["phone_aimed"]
    assert not tracker.update(frame, [], 2.5)["phone_aimed"]
    assert not tracker.update(frame, detections, 2.6)["phone_aimed"]
    moved, moved_detections = scene((60, 90, 80, 160))
    result = tracker.update(moved, moved_detections, 2.7)
    assert not result["phone_aimed"]
    assert result["phone_track_id"] != detections[0]["track_id"]


def test_no_yolo_detection_means_no_phone_pose_even_with_rectangular_scene():
    frame, _ = scene()
    tracker = PhonePoseTracker()
    for timestamp in [0, 0.2, 0.4, 0.6]:
        result = tracker.update(frame, [], timestamp)
        assert result["phone_pose_tracks"] == []
        assert not result["phone_aimed"] and not result["phone_raised"]
