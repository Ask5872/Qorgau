"""Strict-policy acceptance using numbered camera frames, no hardware claims."""
from copy import deepcopy

import pytest

from test_core import client as demo_client
from test_enforcement import EXAM, frame, instructor, live_app, start


def begin(client, **changes):
    response = start(client, **changes)
    assert response.status_code == 200, response.text
    return response.json()["session"]["id"]


def attention_frame(rt, clock, at=100, sequence=1, **changes):
    signals = dict(phone=False, landmarks_valid=True, calibration_version=2,
                   gaze_valid=True, eye_open=True, gaze_screen_x=1.11,
                   gaze_screen_y=.5, gaze_uncertainty=.08, screen_outside=True,
                   head_pose_valid=True, head_outside=False,
                   yaw_delta=0., pitch_delta=0., roll_delta=0.)
    signals.update(changes)
    frame(rt, clock, at, sequence, **signals)


def confirmed_attention(rt, clock, **changes):
    for sequence, at in enumerate((100., 100.125, 100.25), 1):
        attention_frame(rt, clock, at=at, sequence=sequence, **changes)


def test_student_cannot_disable_or_delay_strict_policy(live_app):
    with live_app() as (client, rt, clock):
        sid = begin(client, gaze_enabled=False, gaze_seconds=15)
        config = rt.store.session(sid)["config"]
        assert config["gaze_enabled"] is True and config["gaze_seconds"] == .25
        assert config["strict"] and config["gaze_auto_remove"] and config["head_auto_remove"]
        assert config["min_frames"] == 1 and config["head_limit_deg"] == 5
        assert rt.rules.policy.strict and rt.camera.policy.gaze_seconds == .25
        attention_frame(rt, clock)
        assert rt.active_id == sid
        attention_frame(rt, clock, at=100.125, sequence=2)
        attention_frame(rt, clock, at=100.25, sequence=3)
        assert rt.store.session(sid)["result"]["termination_reason"] == "gaze"


def test_demo_keeps_explicit_controls_without_changing_live_policy(demo_client):
    client, rt = demo_client
    sid = begin(client, gaze_enabled=False, gaze_seconds=12)
    assert rt.store.session(sid)["config"]["gaze_enabled"] is False
    assert rt.rules.policy.gaze_seconds == 12 and not rt.rules.policy.strict


@pytest.mark.parametrize("invalid", [
    {"calibrated": False}, {"calibration_version": 1},
    {"calibration_version": None}, {"landmarks_valid": False},
])
def test_live_start_allows_missing_calibration_or_unavailable_eye_landmarks(live_app, monkeypatch, invalid):
    with live_app() as (client, rt, _clock):
        status = {**rt.camera.status(), **invalid}
        monkeypatch.setattr(rt.camera, "status", lambda: status)
        response = start(client, gaze_enabled=False)
        assert response.status_code == 200, response.text
        assert rt.active_id and rt.guard.enters == 1
        config = rt.store.session(rt.active_id)["config"]
        if invalid.get("landmarks_valid") is not False:
            assert config["calibration_mode"] == "none"
            assert not config["gaze_auto_remove"] and not config["head_auto_remove"]


def test_legacy_teacher_threshold_cannot_weaken_strict_policy(live_app):
    with live_app() as (client, rt, _clock):
        configuration = {**deepcopy(EXAM), "gaze_seconds": 6.5}
        assert client.put("/api/exam-config", json=configuration).status_code == 401
        instructor(client)
        response = client.put("/api/exam-config", json=configuration)
        assert response.status_code == 200
        assert response.json()["gaze_seconds"] == .25
        # Simulate importing a pre-1.9 stored exam without rewriting questions.
        rt.store.set_setting("exam_config", configuration)
    with live_app(configured=False) as (client, rt, clock):
        assert client.get("/api/state").json()["exam_config"]["gaze_seconds"] == .25
        instructor(client)
        assert client.get("/api/exam-config").json()["gaze_seconds"] == .25
        client.headers.pop("Authorization")
        sid = begin(client)
        assert rt.store.session(sid)["config"]["gaze_seconds"] == .25
        confirmed_attention(rt, clock)
        assert rt.store.session(sid)["status"] == "disqualified"


@pytest.mark.parametrize("threshold", [-.01, 15.01, "disabled"])
def test_invalid_legacy_threshold_is_rejected(live_app, threshold):
    with live_app() as (client, rt, _clock):
        before = rt.store.get_setting("exam_config")
        instructor(client)
        response = client.put("/api/exam-config", json={**deepcopy(EXAM), "gaze_seconds": threshold})
        assert response.status_code == 422
        assert rt.store.get_setting("exam_config") == before


@pytest.mark.parametrize("reason,changes", [
    ("gaze", {}),
    ("head", {"head_outside": True, "yaw_delta": 5.01}),
    ("head", {"head_outside": True, "pitch_delta": -5.01}),
    ("head", {"head_outside": True, "roll_delta": 5.01, "gaze_valid": False, "eye_open": False}),
])
def test_resolvable_gaze_confirmation_or_first_head_frame_preserves_evidence(live_app, reason, changes):
    with live_app() as (client, rt, clock):
        sid = begin(client, snapshots=True)
        question = rt.store.session(sid, True)["exam"][0]
        assert client.post("/api/answer", json={"question_id": question["id"], "option": question["correct"]}).status_code == 200
        if reason == "gaze":
            confirmed_attention(rt, clock, **changes)
        else:
            attention_frame(rt, clock, **changes)
        session = rt.store.session(sid)
        assert session["status"] == "disqualified"
        assert session["result"]["termination_reason"] == reason
        assert session["result"]["correct"] == 1
        events = rt.store.events(sid)
        assert [event["kind"] for event in events] == [reason + "_outside", "auto_disqualified"]
        for event in events:
            assert event["duration"] == (.25 if reason == "gaze" else 0)
            assert event["observations"]["reason"] == reason
            assert event["observations"]["required_frames"] == (3 if reason == "gaze" else 1)
            assert event["observations"]["delay_seconds"] == (.25 if reason == "gaze" else 0)
            assert (rt.store.folder / "evidence" / event["evidence"]).read_bytes() == b"test-jpeg"
        assert rt.store.verify(sid)["ok"]
        assert rt.entry_block["reason"] == reason
        assert client.post("/api/unlock", json={}).status_code == 401
        instructor(client)
        exported = client.get(f"/api/sessions/{sid}/export/json")
        assert exported.json()["session"]["result"]["termination_reason"] == reason
        assert client.post("/api/unlock", json={}).status_code == 200
        assert not rt.entry_block


@pytest.mark.parametrize("changes", [
    {"gaze_valid": False}, {"eye_open": False}, {"landmarks_valid": False},
    {"faces": 0}, {"faces": 2}, {"calibrated": False}, {"calibration_version": 1},
    {"gaze_screen_x": 1.0}, {"gaze_screen_x": .5}, {"gaze_uncertainty": -.1},
    {"gaze_uncertainty": .5}, {"gaze_screen_x": float("nan")},
    {"screen_outside": False}, {"gaze_screen_x": None},
    {"screen_outside": False, "head_outside": True, "yaw_delta": 5},
    {"screen_outside": False, "head_outside": True, "yaw_delta": 8, "head_pose_valid": False},
    {"screen_outside": False, "head_outside": True, "roll_delta": float("nan")},
])
def test_invalid_blink_inside_or_uncertain_attention_never_removes(live_app, changes):
    with live_app() as (client, rt, clock):
        sid = begin(client)
        attention_frame(rt, clock, **changes)
        assert rt.active_id == sid and not rt.entry_block
        assert not rt.store.events(sid)


@pytest.mark.parametrize("scenario", ["prestart", "stale", "duplicate", "out_of_order", "idle"])
def test_old_or_idle_attention_frames_never_remove(live_app, scenario):
    with live_app() as (client, rt, clock):
        if scenario == "idle":
            attention_frame(rt, clock)
            assert not rt.entry_block and rt.store.sessions() == []
            return
        sid = begin(client)
        if scenario in {"duplicate", "out_of_order"}:
            attention_frame(rt, clock, sequence=5, screen_outside=False)
            attention_frame(rt, clock, at=100.1, sequence=5 if scenario == "duplicate" else 4)
        else:
            attention_frame(rt, clock, analyzed_at=99.99 if scenario == "prestart" else 98)
        assert rt.active_id == sid and not rt.entry_block
        assert not rt.store.events(sid)


@pytest.mark.parametrize("reason", ["gaze", "head"])
def test_attention_block_survives_restart(live_app, reason):
    with live_app() as (client, rt, clock):
        sid = begin(client)
        if reason == "gaze":
            confirmed_attention(rt, clock)
        else:
            attention_frame(rt, clock, head_outside=True, yaw_delta=6)
    with live_app(configured=False) as (client, rt, _clock):
        assert rt.entry_block["session_id"] == sid and rt.entry_block["reason"] == reason
        assert start(client).status_code == 400
        instructor(client)
        assert client.post("/api/unlock", json={}).status_code == 200
        assert rt.store.session(sid)["result"]["termination_reason"] == reason


@pytest.mark.parametrize("signal,event_kind,seconds", [
    ({"faces": 0}, "no_face", 3), ({"faces": 2}, "multiple_faces", 1.5),
])
def test_face_count_stays_review_only(live_app, signal, event_kind, seconds):
    with live_app() as (client, rt, clock):
        sid = begin(client)
        for seq in range(int(seconds * 2) + 1):
            frame(rt, clock, 100 + seq * .5, seq + 1, phone=False, gaze_valid=False, **signal)
        event, = rt.store.events(sid)
        assert event["kind"] == event_kind and event["duration"] == seconds
        assert rt.active_id == sid


def test_calibration_mutations_require_participant_and_idle_attempt(live_app):
    with live_app() as (client, rt, _clock):
        assert client.post("/api/calibrate").status_code == 409
        assert client.post("/api/participant/login", json={"name": "A", "group": "G"}).status_code == 200
        assert client.post("/api/calibrate").status_code == 200
        assert client.post("/api/calibration/target", json={"step": 0, "calibration_id": "test-calibration"}).status_code == 200
        assert client.post("/api/calibration/target", json={"step": -1, "calibration_id": "test-calibration"}).status_code == 422
        assert client.post("/api/calibration/cancel", json={"calibration_id": "test-calibration"}).status_code == 200
        begin(client)
        assert client.post("/api/calibrate").status_code == 409
        assert client.post("/api/calibration/target", json={"step": 0, "calibration_id": "test-calibration"}).status_code == 409
        assert client.post("/api/calibration/cancel", json={"calibration_id": "test-calibration"}).status_code == 409


@pytest.mark.parametrize("reason,label", [("phone", "Обнаружен телефон"),
                                        ("gaze", "Взгляд за пределами экрана"),
                                        ("head", "Отклонение головы от калиброванного положения")])
def test_html_report_translates_termination_reason(live_app, reason, label):
    with live_app() as (client, rt, clock):
        sid = begin(client)
        if reason == "phone":
            frame(rt, clock, 100, 1)
        elif reason == "gaze":
            confirmed_attention(rt, clock)
        else:
            attention_frame(rt, clock, head_outside=True, yaw_delta=6)
        instructor(client)
        response = client.get(f"/api/sessions/{sid}/export/html")
        assert response.status_code == 200
        assert "Причина остановки: <b>" + label + "</b>" in response.text


@pytest.mark.parametrize("point", [(1.000001, .5), (-.000001, .5), (.5, 1.000001), (.5, -.000001)])
def test_repeated_estimates_just_outside_screen_remain_inconclusive(live_app, point):
    with live_app() as (client, rt, clock):
        sid = begin(client)
        confirmed_attention(rt, clock, gaze_screen_x=point[0], gaze_screen_y=point[1], gaze_uncertainty=.10)
        assert rt.active_id == sid and not rt.entry_block
        assert not rt.store.events(sid)


def test_fresh_dark_frames_keep_technical_low_light_without_removal(live_app):
    with live_app() as (client, rt, clock):
        sid = begin(client)
        for seq in range(10):
            attention_frame(rt, clock, at=100 + seq * .5, sequence=seq + 1, brightness=10)
        assert rt.active_id == sid and not rt.entry_block
        assert [event["kind"] for event in rt.store.events(sid)] == ["low_light"]



def test_late_calibration_messages_cannot_modify_new_wizard(live_app, monkeypatch):
    from pathlib import Path
    from qorgau.vision import Camera
    with live_app() as (client, rt, _clock):
        camera = Camera(Path(__file__).resolve().parents[1] / "models", rt.on_frame)
        camera.state.update(camera_ok=True, analysis_ready=True)
        monkeypatch.setattr(camera, "status", lambda: dict(camera.state))
        rt.camera = camera
        assert client.post("/api/participant/login", json={"name": "A", "group": "G"}).status_code == 200
        first = client.post("/api/calibrate")
        assert first.status_code == 200
        old_id = first.json()["camera"]["calibration"]["id"]
        second = client.post("/api/calibrate")
        assert second.status_code == 200
        current = second.json()["camera"]["calibration"]
        new_id = current["id"]
        assert old_id != new_id and current["active"]
        for path, payload in [("target", {"step": 0}), ("cancel", {})]:
            assert client.post("/api/calibration/" + path, json=payload).status_code == 422
            assert client.get("/api/state").json()["camera"]["calibration"] == current
        late_target = client.post("/api/calibration/target", json={"step": 0, "calibration_id": old_id})
        assert late_target.status_code == 400
        assert client.get("/api/state").json()["camera"]["calibration"] == current
        late_cancel = client.post("/api/calibration/cancel", json={"calibration_id": old_id})
        assert late_cancel.status_code == 200
        assert late_cancel.json()["camera"]["calibration"] == current
        canceled = client.post("/api/calibration/cancel", json={"calibration_id": new_id})
        assert canceled.status_code == 200
        assert not canceled.json()["camera"]["calibration"]["active"]


@pytest.mark.parametrize("interruption", ["duplicate", "stale", "reversed"])
def test_runtime_rejected_frames_interrupt_pending_gaze_confirmation(live_app, interruption):
    with live_app() as (client, rt, clock):
        sid = begin(client)
        attention_frame(rt, clock, sequence=10, at=100.)
        attention_frame(rt, clock, sequence=11, at=100.125)
        if interruption == "stale":
            attention_frame(rt, clock, sequence=12, at=100.25, analyzed_at=98.)
        else:
            attention_frame(rt, clock, sequence=11 if interruption == "duplicate" else 9, at=100.25)
        attention_frame(rt, clock, sequence=13, at=100.375)
        assert rt.active_id == sid
        attention_frame(rt, clock, sequence=14, at=100.5)
        assert rt.active_id == sid
        attention_frame(rt, clock, sequence=15, at=100.625)
        assert rt.store.session(sid)["result"]["termination_reason"] == "gaze"


def test_phone_still_ends_test_while_gaze_is_only_pending(live_app):
    with live_app() as (client, rt, clock):
        sid = begin(client)
        attention_frame(rt, clock)
        assert rt.active_id == sid
        attention_frame(rt, clock, sequence=2, at=100.125, phone=True, phone_confidence=.40)
        assert rt.store.session(sid)["result"]["termination_reason"] == "phone"
