"""Phone dismissal and instructor controls, without a physical webcam.

The injected camera emits independently numbered analysis results.  Preview
callbacks alone must never accumulate enough evidence to end an attempt.
"""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import time

import pytest
from fastapi.testclient import TestClient

import qorgau.server as server


EXAM = {
    "title": "Контрольная по базам данных",
    "duration_minutes": 7,
    "questions": [
        {"id": "sql", "text": "Команда выборки?", "options": ["SELECT", "DROP", "DELETE", "ALTER"], "correct": 0},
        {"id": "key", "text": "Назначение ключа?", "options": ["Цвет", "Размер", "Идентификация", "Шрифт"], "correct": 2},
    ],
}


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


class CameraSpy:
    def __init__(self, _models, callback):
        self.callback = callback
        self.jpeg = None
        self.stops = 0
        self.policy = None

    def available(self):
        return True

    def status(self):
        return {"camera_ok": True, "analysis_ready": True, "faces": 1,
                "brightness": 100, "calibrated": True, "calibration_version": 2, "phone": False}

    def stop(self):
        self.stops += 1

    def start(self, _index=0):
        pass

    def calibrate(self):
        pass

    def cancel_calibration(self, calibration_id=None):
        pass

    def calibration_target(self, step, viewport=None, calibration_id=None):
        pass

    def use_calibration(self, viewport=None):
        return self.status()


class GuardSpy:
    def __init__(self, _emit, _emergency):
        self.enters = 0
        self.leaves = 0

    def capabilities(self):
        return {"native": False, "platform": "test"}

    def enter(self):
        self.enters += 1

    def leave(self):
        self.leaves += 1


@pytest.fixture
def live_app(tmp_path, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(server, "Camera", CameraSpy)
    monkeypatch.setattr(server, "DesktopGuard", GuardSpy)
    # Replace only this module's clock: asyncio and TestClient keep real time.
    monkeypatch.setattr(server, "time", SimpleNamespace(time=time.time, monotonic=clock))

    @contextmanager
    def open_app(configured=True):
        app = server.create_app(Path(__file__).resolve().parents[1], tmp_path,
                                demo=False, initial_pin="123456")
        rt = app.state.runtime
        rt.closed.set()  # Timers are irrelevant to these deterministic frame tests.
        try:
            with TestClient(app) as client:
                client.headers["X-Qorgau-Token"] = rt.csrf
                if configured:
                    instructor(client)
                    response = client.put("/api/exam-config", json=deepcopy(EXAM))
                    assert response.status_code == 200, response.text
                    client.headers.pop("Authorization")
                yield client, rt, clock
        finally:
            rt.store.close()

    return open_app


def instructor(client):
    response = client.post("/api/login", json={"pin": "123456"})
    assert response.status_code == 200, response.text
    client.headers["Authorization"] = "Bearer " + response.json()["token"]


def start(client, **kwargs):
    payload = {"name": "Участник", "group": "ИСУ", "consent": True, **kwargs}
    if client.get("/api/state").json().get("participant") is None:
        entered = client.post("/api/participant/login", json={
            "name": payload["name"], "group": payload["group"],
        })
        assert entered.status_code == 200, entered.text
    return client.post("/api/sessions", json=payload)


def frame(rt, clock, at, seq, **changes):
    clock.now = at
    signal = {"camera_ok": True, "analysis_ready": True, "faces": 1,
              "brightness": 100, "calibrated": True, "phone": True,
              "phone_confidence": .91, "analyzed_seq": seq, "analyzed_at": at}
    signal.update(changes)
    rt.on_frame(signal, b"test-jpeg")


def sustained_phone(rt, clock):
    for seq, at in enumerate((100.0, 100.6, 101.2, 101.6), 1):
        frame(rt, clock, at, seq)


def test_sustained_phone_ends_attempt_preserves_score_and_requires_instructor(live_app):
    with live_app() as (client, rt, clock):
        response = start(client)
        assert response.status_code == 200, response.text
        sid = response.json()["session"]["id"]
        private_question = rt.store.session(sid, True)["exam"][0]
        answer = {"question_id": private_question["id"], "option": private_question["correct"]}
        assert client.post("/api/answer", json=answer).status_code == 200

        sustained_phone(rt, clock)

        session = rt.store.session(sid)
        assert session["status"] == "disqualified"
        assert session["result"]["termination_reason"] == "phone"
        assert session["result"]["correct"] == 1
        assert session["result"]["total"] == 2
        assert rt.active_id is None
        assert rt.camera.stops >= 1
        assert rt.guard.enters == 1 and rt.guard.leaves >= 1
        enforcement = client.get("/api/state").json()["enforcement"]
        assert enforcement["blocked"] is True
        assert enforcement["session_id"] == sid
        assert enforcement["phone_seconds"] == 0
        assert enforcement["confidence"] == .40
        assert enforcement["min_frames"] == 1

        events = rt.store.events(sid)
        kinds = [e["kind"] for e in events]
        assert "phone" in kinds and kinds.count("auto_disqualified") == 1
        assert all(not e["simulated"] for e in events)
        assert rt.store.verify(sid)["ok"]
        assert client.post("/api/answer", json=answer).status_code == 409
        assert start(client, name="Другое имя").status_code in (400, 409)
        assert client.post("/api/unlock", json={}).status_code == 401

        instructor(client)
        assert client.post("/api/unlock", json={}).status_code == 200
        assert client.get("/api/state").json()["enforcement"]["blocked"] is False
        assert rt.store.session(sid)["status"] == "disqualified"
        assert [e["kind"] for e in rt.store.events(sid)].count("instructor_unlock") == 1
        assert rt.store.verify(sid)["ok"]
        report = client.get(f"/api/sessions/{sid}/export/json")
        assert report.status_code == 200
        assert report.json()["session"]["result"]["correct"] == 1
        client.headers.pop("Authorization")
        assert start(client).status_code == 200


@pytest.mark.parametrize("scenario", ["low_confidence", "repeated_sequence", "stale_analysis",
                                     "analysis_not_ready", "pre_arm", "future", "nan", "above_one"])
def test_invalid_phone_evidence_keeps_attempt_running(live_app, scenario):
    with live_app() as (client, rt, clock):
        response = start(client)
        assert response.status_code == 200, response.text
        sid = response.json()["session"]["id"]
        if scenario == "repeated_sequence":
            frame(rt, clock, 100, 1, phone=False)
            frame(rt, clock, 100.1, 1)
        else:
            changes = {"low_confidence": {"phone_confidence": .399},
                       "stale_analysis": {"analyzed_at": 98},
                       "analysis_not_ready": {"analysis_ready": False},
                       "pre_arm": {"analyzed_at": 99.99},
                       "future": {"analyzed_at": 101},
                       "nan": {"phone_confidence": float("nan")},
                       "above_one": {"phone_confidence": 1.01}}[scenario]
            frame(rt, clock, 100.1, 1, **changes)
        assert rt.active_id == sid
        assert rt.store.session(sid)["status"] == "running"
        assert not client.get("/api/state").json()["enforcement"]["blocked"]
        assert "auto_disqualified" not in [e["kind"] for e in rt.store.events(sid)]


@pytest.mark.parametrize("confidence", [.40, .40001, 1.0])
def test_first_fresh_small_phone_detection_stops_immediately(live_app, confidence):
    with live_app() as (client, rt, clock):
        sid = start(client).json()["session"]["id"]
        frame(rt, clock, 100, 1, phone_confidence=confidence,
              objects=[{"kind": "phone", "confidence": confidence, "box": [0, 0, 1, 2]}])
        assert rt.store.session(sid)["status"] == "disqualified"
        assert rt.store.session(sid)["result"]["termination_reason"] == "phone"
        event = next(e for e in rt.store.events(sid) if e["kind"] == "auto_disqualified")
        assert event["duration"] == 0 and event["observations"]["required_frames"] == 1


def test_station_block_survives_application_restart(live_app):
    with live_app() as (client, rt, clock):
        response = start(client)
        assert response.status_code == 200, response.text
        sid = response.json()["session"]["id"]
        sustained_phone(rt, clock)
        assert rt.store.session(sid)["status"] == "disqualified"

    with live_app(configured=False) as (client, rt, _clock):
        enforcement = client.get("/api/state").json()["enforcement"]
        assert enforcement["blocked"] is True
        assert enforcement["session_id"] == sid
        assert start(client).status_code in (400, 409)
        assert rt.store.session(sid)["status"] == "disqualified"
        assert rt.store.verify(sid)["ok"]
        instructor(client)
        assert client.post("/api/unlock", json={}).status_code == 200

    with live_app(configured=False) as (client, rt, _clock):
        assert not client.get("/api/state").json()["enforcement"]["blocked"]
        assert "instructor_unlock" in [e["kind"] for e in rt.store.events(sid)]
        assert start(client).status_code == 200


def test_expired_exam_completes_before_pending_phone_frame_can_disqualify(live_app):
    with live_app() as (client, rt, clock):
        response = start(client)
        assert response.status_code == 200, response.text
        sid = response.json()["session"]["id"]
        frame(rt, clock, 100.0, 1, phone=False)
        frame(rt, clock, 100.8, 2, phone=False)
        with rt.store.db:
            rt.store.db.execute("UPDATE sessions SET started=? WHERE id=?",
                                (time.time() - 8 * 60, sid))
        # The first actual phone detection would otherwise stop immediately.
        frame(rt, clock, 101.6, 3)
        session = rt.store.session(sid)
        assert session["status"] == "completed"
        assert "termination_reason" not in session["result"]
        assert rt.active_id is None
        assert not client.get("/api/state").json()["enforcement"]["blocked"]
        assert "auto_disqualified" not in [e["kind"] for e in rt.store.events(sid)]


def test_exam_configuration_is_instructor_owned_and_student_cannot_extend_time(live_app):
    with live_app(configured=False) as (client, rt, _clock):
        assert start(client).status_code == 400
        assert not client.get("/api/state").json()["exam_config"]["configured"]
        assert client.get("/api/exam-config").status_code == 401
        assert client.put("/api/exam-config", json=deepcopy(EXAM)).status_code == 401
        instructor(client)
        for invalid in ({**EXAM, "questions": []},
                        {**EXAM, "questions": [{**EXAM["questions"][0], "correct": 4}]},
                        {**EXAM, "questions": [{**EXAM["questions"][0], "options": ["A", "B"]}]}):
            assert client.put("/api/exam-config", json=invalid).status_code in (400, 422)
        assert client.put("/api/exam-config", json=deepcopy(EXAM)).status_code == 200
        stored = client.get("/api/exam-config")
        assert stored.status_code == 200
        assert stored.json()["questions"][0]["correct"] == 0
        client.headers.pop("Authorization")
        metadata = client.get("/api/state").json()["exam_config"]
        assert metadata["configured"] is True
        assert metadata["title"] == EXAM["title"]
        assert "questions" not in metadata
        response = start(client, duration_minutes=180)
        assert response.status_code == 200, response.text
        session = response.json()["session"]
        assert session["config"]["duration_minutes"] == 7
        assert 0 < session["remaining"] <= 7 * 60
        assert len(session["exam"]) == 2
        assert all("correct" not in question for question in session["exam"])


def test_arm_time_excludes_frames_captured_while_protected_window_opens(live_app, monkeypatch):
    with live_app() as (client, rt, clock):
        base_status = rt.camera.status()
        status = {**base_status, "analyzed_seq": 0}
        monkeypatch.setattr(rt.camera, "status", lambda: status)
        def enter():
            rt.guard.enters += 1
            clock.now = 105
            status["analyzed_seq"] = 20
        monkeypatch.setattr(rt.guard, "enter", enter)
        sid = start(client).json()["session"]["id"]
        assert rt.armed_at == 105
        frame(rt, clock, 105.1, 21, analyzed_at=104.99)
        frame(rt, clock, 105.2, 20)
        assert rt.active_id == sid and not rt.store.events(sid)
        frame(rt, clock, 105.3, 22)
        assert rt.store.session(sid)["status"] == "disqualified"


def test_phone_cleanup_restores_guard_before_stopping_camera(live_app, monkeypatch):
    with live_app() as (client, rt, clock):
        start(client)
        calls = []
        monkeypatch.setattr(rt.guard, "leave", lambda: calls.append("guard"))
        monkeypatch.setattr(rt.camera, "stop", lambda: calls.append("camera"))
        frame(rt, clock, 100, 1)
        assert calls == ["guard", "camera"]


def test_phone_while_idle_does_not_create_attempt_or_block(live_app):
    with live_app() as (_client, rt, clock):
        frame(rt, clock, 100, 1)
        assert rt.store.sessions() == [] and not rt.entry_block
