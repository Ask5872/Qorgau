"""Measured uncertainty and fresh-observation checks, not webcam guarantees."""
import math

import pytest

from qorgau.gaze import estimate_screen_gaze
from qorgau.rules import AttentionRemovalGate, PhoneRemovalGate


PROFILE = {"version": 2, "center": [.5, .5], "scale": [1., 1.],
           "weights": [[.5, .5], [1., 0.], [0., 1.]], "uncertainty": .08,
           "neutral": [0., 0., .5, .5, .2], "neutral_roll": 0., "head_limit_deg": 5.}


def signal(seq=1, at=100., **changes):
    return {"analyzed_seq": seq, "analyzed_at": at, "camera_ok": True,
            "analysis_ready": True, "brightness": 100., "faces": 1,
            "landmark_faces": 1, "landmarks_valid": True, "pose_valid": True,
            "calibrated": True, "calibration_version": 2,
            "eye_open": True, "eye_features_valid": True, "gaze_valid": True,
            "gaze_screen_x": 1.2, "gaze_screen_y": .5, "gaze_uncertainty": .08,
            "screen_outside": True, "head_pose_valid": True, "head_outside": False,
            "yaw_delta": 0., "pitch_delta": 0., "roll_delta": 0.,
            "yaw": 0., "pitch": 0., "roll": 0., "iris_x": .5, "iris_y": .5,
            "head_vertical": .2, **changes}


def evaluate(gate, seq, at, **changes):
    return gate.evaluate(signal(seq, at, **changes), now=at)


@pytest.mark.parametrize("x,y", [(-.001, .5), (1.001, .5), (.5, -.001), (.5, 1.001),
                                   (-.08, .5), (1.08, .5), (.5, -.08), (.5, 1.08)])
def test_edge_estimate_is_available_but_cannot_confirm_violation(x, y):
    result = estimate_screen_gaze(signal(iris_x=x, iris_y=y), PROFILE)
    assert result["gaze_valid"] and result["screen_outside"]
    assert result["gaze_boundary_uncertain"]
    assert result["gaze_reason"] == "boundary_uncertain"
    assert not result["gaze_outside_confirmable"]
    assert result["gaze_screen_x"] == pytest.approx(x)
    assert result["gaze_screen_y"] == pytest.approx(y)
    gate = AttentionRemovalGate()
    for seq, at in enumerate((100., 100.125, 100.25, 100.5), 1):
        assert evaluate(gate, seq, at, gaze_screen_x=x, gaze_screen_y=y) is None
    assert gate.frames == 0


@pytest.mark.parametrize("x,y,direction", [(-.09, .5, "left"), (1.09, .5, "right"),
                                            (.5, -.09, "up"), (.5, 1.09, "down")])
def test_resolvable_outside_position_preserves_diagnostics(x, y, direction):
    result = estimate_screen_gaze(signal(iris_x=x, iris_y=y), PROFILE)
    assert result["gaze_outside_confirmable"] and result["screen_outside"]
    assert not result["gaze_boundary_uncertain"] and not result["gaze_reason"]
    assert result["gaze_direction"] == direction


def test_three_fresh_observations_over_quarter_second_are_required():
    gate = AttentionRemovalGate()
    assert evaluate(gate, 1, 100.) is None
    assert evaluate(gate, 2, 100.125) is None
    assert evaluate(gate, 3, 100.25) == "gaze"
    assert gate.frames == 3 and gate.last_at - gate.started == .25


def test_three_frames_without_required_duration_and_two_slow_frames_do_not_fire():
    gate = AttentionRemovalGate()
    for seq, at in enumerate((100., 100.05, 100.1), 1):
        assert evaluate(gate, seq, at) is None
    assert evaluate(gate, 4, 100.25) == "gaze"
    gate.reset()
    assert evaluate(gate, 1, 100.) is None
    assert evaluate(gate, 2, 100.5) is None


def test_capture_sequence_gaps_are_legal_when_analysis_consumes_latest_frame():
    gate = AttentionRemovalGate()
    assert evaluate(gate, 1, 100.) is None
    assert evaluate(gate, 5, 100.125) is None
    assert evaluate(gate, 9, 100.25) == "gaze"


@pytest.mark.parametrize("changes", [
    {"gaze_screen_x": .5, "screen_outside": False},
    {"gaze_screen_x": 1.001}, {"eye_open": False}, {"eye_features_valid": False},
    {"gaze_valid": False}, {"calibrated": False}, {"landmarks_valid": False},
    {"camera_ok": False}, {"analysis_ready": False}, {"brightness": 10},
    {"faces": 0}, {"faces": 2}, {"gaze_screen_x": math.nan},
    {"gaze_uncertainty": math.nan}, {"gaze_uncertainty": -.01}, {"gaze_uncertainty": .11},
])
def test_inside_uncertain_blink_or_invalid_measurement_breaks_episode(changes):
    gate = AttentionRemovalGate()
    assert evaluate(gate, 1, 100.) is None
    assert evaluate(gate, 2, 100.125) is None
    assert evaluate(gate, 3, 100.25, **changes) is None
    assert gate.frames == 0
    assert evaluate(gate, 4, 100.375) is None
    assert evaluate(gate, 5, 100.5) is None
    assert evaluate(gate, 6, 100.625) == "gaze"


@pytest.mark.parametrize("seq,at,now", [(2, 100.125, 100.25), (1, 100.2, 100.25),
                                         (3, 98., 100.25), (3, 100.3, 100.25),
                                         (3, 100.125, 100.25), (3, 100.1, 100.25)])
def test_stale_duplicate_reversed_future_or_reused_capture_time_resets(seq, at, now):
    gate = AttentionRemovalGate()
    assert evaluate(gate, 1, 100.) is None
    assert evaluate(gate, 2, 100.125) is None
    assert gate.evaluate(signal(seq, at), now) is None
    assert gate.frames == 0
    assert evaluate(gate, 4, 100.375) is None
    assert evaluate(gate, 5, 100.5) is None
    assert evaluate(gate, 6, 100.625) == "gaze"


def test_gap_of_missing_camera_evidence_starts_new_episode():
    gate = AttentionRemovalGate()
    assert evaluate(gate, 1, 100.) is None
    assert evaluate(gate, 2, 100.125) is None
    assert evaluate(gate, 3, 103.25) is None
    assert gate.frames == 1
    assert evaluate(gate, 4, 103.375) is None
    assert evaluate(gate, 5, 103.5) == "gaze"


def test_inference_completion_times_cannot_manufacture_sustained_capture_duration():
    gate = AttentionRemovalGate()
    for seq in range(1, 4):
        assert gate.evaluate(signal(seq, 100. + seq * .01), 100. + seq * .15) is None
    assert gate.frames == 3
    assert gate.last_at - gate.started == pytest.approx(.02)


def test_alternating_sides_never_accumulate_one_continuous_episode():
    gate = AttentionRemovalGate()
    for seq in range(1, 8):
        x = -.2 if seq % 2 else 1.2
        assert evaluate(gate, seq, 100. + seq * .125, gaze_screen_x=x) is None
        assert gate.frames == 1


def test_diagonal_transition_needs_one_common_outside_edge_for_whole_episode():
    gate = AttentionRemovalGate()
    assert evaluate(gate, 1, 100., gaze_screen_x=-.2) is None
    assert evaluate(gate, 2, 100.125, gaze_screen_x=-.2, gaze_screen_y=-.2) is None
    assert evaluate(gate, 3, 100.25, gaze_screen_x=.5, gaze_screen_y=-.2) is None
    assert gate.frames == 1
    assert evaluate(gate, 4, 100.375, gaze_screen_x=.5, gaze_screen_y=-.2) is None
    assert evaluate(gate, 5, 100.5, gaze_screen_x=.5, gaze_screen_y=-.2) == "gaze"


def test_outer_runtime_interruption_preserves_replay_and_arm_protection():
    gate = AttentionRemovalGate()
    gate.reset(armed_at=100., baseline_seq=10)
    assert evaluate(gate, 11, 100.) is None
    assert evaluate(gate, 12, 100.125) is None
    gate.interrupt_gaze()
    assert gate.frames == 0 and gate.last_seq == 12 and gate.armed_at == 100.
    assert evaluate(gate, 12, 100.25) is None
    assert evaluate(gate, 13, 100.375) is None
    assert evaluate(gate, 14, 100.5) is None
    assert evaluate(gate, 15, 100.625) == "gaze"


def test_attempt_reset_clears_previous_pending_episode():
    gate = AttentionRemovalGate()
    assert evaluate(gate, 1, 100.) is None
    assert evaluate(gate, 2, 100.125) is None
    gate.reset(armed_at=101., baseline_seq=10)
    assert evaluate(gate, 11, 101.) is None
    assert evaluate(gate, 12, 101.125) is None
    assert evaluate(gate, 13, 101.25) == "gaze"


@pytest.mark.parametrize("axis", ["yaw_delta", "pitch_delta", "roll_delta"])
def test_head_still_fires_first_frame_independent_of_blink(axis):
    gate = AttentionRemovalGate()
    assert evaluate(gate, 1, 100., head_outside=True, eye_open=False,
                    gaze_valid=False, **{axis: 5.01}) == "head"
    assert gate.frames == 0


def test_phone_is_unchanged_first_frame_even_when_gaze_inconclusive():
    gate = PhoneRemovalGate()
    assert gate.evaluate(signal(phone=True, phone_confidence=.40,
                                gaze_screen_x=1.001), 100.)
    assert gate.frames == 1


def test_low_cadence_fresh_observations_can_confirm_with_honest_longer_latency():
    gate = AttentionRemovalGate()
    assert evaluate(gate, 1, 100.) is None
    assert evaluate(gate, 30, 102.) is None
    assert evaluate(gate, 60, 104.) == "gaze"
    assert gate.frames == 3 and gate.last_at - gate.started == 4.
