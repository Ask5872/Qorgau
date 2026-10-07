"""Optional calibration permits entry without inventing monitoring capability."""
from copy import deepcopy

import pytest

from qorgau.rules import AttentionRemovalGate
from qorgau.server import report_data, html_report
from test_enforcement import frame, instructor, live_app, start
from test_case3_acceptance import attention_frame, confirmed_attention
from test_gaze_reliability import signal


def camera_state(rt, mode="none", saved=0, head=False, gaze="unavailable", phase="idle", **changes):
    return {**rt.camera.status(), "calibrated": mode == "full", "calibration_version": 2 if saved else None,
            "calibration_mode": mode, "gaze_monitoring": gaze, "head_calibrated": head,
            "calibration": {"phase": phase, "active": phase == "collecting", "saved_points": saved,
                            "total": 5, "quality": {"rms_error": .12} if saved else {}}, **changes}


@pytest.mark.parametrize("mode,saved,head,gaze,phase", [
    ("none", 0, False, "unavailable", "idle"),
    ("none", 0, False, "unavailable", "failed"),
    ("partial", 1, True, "unavailable", "canceled"),
    ("partial", 3, True, "approximate", "collecting"),
    ("partial", 5, True, "approximate", "failed"),
    ("full", 5, True, "calibrated", "complete"),
])
def test_exam_freezes_actual_calibration_and_saves_disclosed_capabilities(
        live_app, monkeypatch, mode, saved, head, gaze, phase):
    with live_app() as (client, rt, clock):
        state = camera_state(rt, mode, saved, head, gaze, phase)
        monkeypatch.setattr(rt.camera, "status", lambda: deepcopy(state))
        freezes = []
        viewport = {"width": 900, "height": 700, "screen_width": 1920,
                    "screen_height": 1080, "device_pixel_ratio": 1.25}
        def use_calibration(viewport=None):
            freezes.append(viewport)
            state["calibration"]["active"] = False
            if state["calibration"]["phase"] == "collecting":
                state["calibration"]["phase"] = "canceled"
            return deepcopy(state)
        monkeypatch.setattr(rt.camera, "use_calibration", use_calibration)
        response = start(client, calibration_viewport=viewport, gaze_enabled=False)
        assert response.status_code == 200, response.text
        sid = rt.active_id
        assert freezes == [viewport]
        config = rt.store.session(sid)["config"]
        assert config["calibration_mode"] == mode
        assert config["calibration_saved_points"] == saved and config["calibration_total_points"] == 5
        assert config["gaze_monitoring"] == gaze and config["head_calibrated"] is head
        assert config["gaze_auto_remove"] is (mode == "full") and config["head_auto_remove"] is head
        assert config["calibration_quality"] == state["calibration"]["quality"]
        assert config["calibration"]["active"] is False
        for key in ("calibration_mode", "calibration_saved_points", "gaze_monitoring", "head_calibrated"):
            assert response.json()["session"]["config"][key] == config[key]
        assert response.json()["enforcement"]["gaze_auto_remove"] is (mode == "full")
        report = report_data(rt, sid)
        assert f"{saved} из 5" in report["notice"]
        if mode != "full":
            assert "автоматическая остановка по взгляду отключена" in report["notice"]
            assert "автоматическая остановка по взгляду отключена" in html_report(report)
        # Later measurements claiming a full profile cannot upgrade an attempt.
        state.update(calibrated=True, calibration_mode="full", gaze_monitoring="calibrated", head_calibrated=True)
        if mode != "full":
            confirmed_attention(rt, clock, calibration_mode="full", head_calibrated=True)
            assert rt.active_id == sid
            if not head:
                attention_frame(rt, clock, 100.5, 4, head_calibrated=True, head_outside=True, yaw_delta=20.)
                assert rt.active_id == sid
        else:
            confirmed_attention(rt, clock)
            assert rt.store.session(sid)["result"]["termination_reason"] == "gaze"


@pytest.mark.parametrize("condition", [{"faces": 0}, {"faces": 2}, {"brightness": 5},
                                       {"landmarks_valid": False}, {"eye_open": False}])
def test_camera_quality_conditions_do_not_reintroduce_calibration_entry_block(live_app, monkeypatch, condition):
    with live_app() as (client, rt, _):
        state = camera_state(rt, **condition)
        monkeypatch.setattr(rt.camera, "status", lambda: state)
        response = start(client)
        assert response.status_code == 200, response.text
        assert rt.guard.enters == 1


@pytest.mark.parametrize("condition", [{"camera_ok": False}, {"analysis_ready": False}])
def test_actual_camera_and_analysis_are_still_required(live_app, monkeypatch, condition):
    with live_app() as (client, rt, _):
        state = camera_state(rt, **condition)
        monkeypatch.setattr(rt.camera, "status", lambda: state)
        response = start(client)
        assert response.status_code == 400
        assert not rt.active_id and not rt.guard.enters


def test_phone_still_stops_first_fresh_frame_without_calibration(live_app, monkeypatch):
    with live_app() as (client, rt, clock):
        state = camera_state(rt)
        monkeypatch.setattr(rt.camera, "status", lambda: state)
        sid = start(client).json()["session"]["id"]
        frame(rt, clock, 100, 1, calibrated=False, calibration_mode="none", head_calibrated=False)
        assert rt.store.session(sid)["result"]["termination_reason"] == "phone"


def test_partial_center_allows_independent_head_monitoring(live_app, monkeypatch):
    with live_app() as (client, rt, clock):
        state = camera_state(rt, "partial", 1, True)
        monkeypatch.setattr(rt.camera, "status", lambda: state)
        sid = start(client).json()["session"]["id"]
        attention_frame(rt, clock, calibrated=False, calibration_mode="partial", head_calibrated=True,
                        head_outside=True, yaw_delta=5.1, eye_open=False, gaze_valid=False)
        assert rt.store.session(sid)["result"]["termination_reason"] == "head"


@pytest.mark.parametrize("mode", ["none", "partial", "full"])
def test_stale_native_display_does_not_block_nonfull_start(live_app, monkeypatch, mode):
    with live_app() as (client, rt, _):
        state = camera_state(rt, mode, saved=5 if mode == "full" else 0,
                             head=mode == "full", gaze="calibrated" if mode == "full" else "unavailable")
        monkeypatch.setattr(rt.camera, "status", lambda: state)
        display = {"name": "previous-monitor"}
        rt.guard.calibration_display = display
        captured = []
        monkeypatch.setattr(rt.guard, "enter", lambda: captured.append(rt.guard.calibration_display))
        assert start(client).status_code == 200
        assert captured == [display if mode == "full" else None]


@pytest.mark.parametrize("mode,calibrated,head", [("partial", False, False), ("partial", True, False),
                                               ("none", False, False)])
def test_attention_gate_never_uses_approximate_or_missing_profile(mode, calibrated, head):
    gate = AttentionRemovalGate()
    for seq, at in enumerate((100., 100.125, 100.25, 100.5), 1):
        assert gate.evaluate(signal(seq, at, calibrated=calibrated, calibration_mode=mode,
                                    head_calibrated=head, head_outside=True, yaw_delta=20.), at) is None
    assert not gate.frames


def test_start_body_cannot_forge_monitoring_capability(live_app):
    with live_app() as (client, rt, _):
        assert start(client, calibration_mode="full", head_calibrated=True).status_code == 422
        assert rt.active_id is None
