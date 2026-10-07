"""Restrictions are armed per attempt; the ordinary application stays free.

These integration checks use a Windows-capability spy, not Win32 APIs. They
exercise the public HTTP flow and release/error paths without seizing a desktop.
"""
from types import SimpleNamespace

import pytest

import qorgau.server as server
import qorgau.store as store_module
from test_enforcement import EXAM, instructor, live_app, start, sustained_phone


class AttemptGuardSpy:
    """The main window advertises availability before opening an exam window."""

    def __init__(self):
        self.enters = 0
        self.leaves = 0
        self.active = False
        self.available = True
        self.broken = None
        self.before_enter = None

    def capabilities(self):
        result = {
            "desktop": True, "platform": "win32", "supported": True,
            "isolation_available": self.available,
            "desktop_isolated": self.active, "navigation_guard": self.active,
            "active": self.active, "keyboard_hook": self.active,
            "window_controller": "pyautogui" if self.active else None,
            "error": "",
        }
        if self.active and self.broken:
            result[self.broken] = False if self.broken != "window_controller" else None
        return result

    def enter(self):
        self.enters += 1
        if self.before_enter:
            self.before_enter()
        self.active = True

    def leave(self):
        self.leaves += 1
        self.active = False


def use_attempt_guard(rt, monkeypatch):
    # Do not modify global sys.platform: pathlib/TestClient use the host OS.
    monkeypatch.setattr(server, "sys", SimpleNamespace(platform="win32"))
    rt.guard = AttemptGuardSpy()
    return rt.guard


def test_entry_teacher_editor_and_camera_preparation_do_not_arm_restrictions(live_app, monkeypatch):
    with live_app() as (client, rt, _clock):
        guard = use_attempt_guard(rt, monkeypatch)
        state = client.get("/api/state").json()
        assert state["guard"]["isolation_available"]
        assert not state["guard"]["desktop_isolated"]
        assert client.post("/api/participant/login", json={"name": "Асет", "group": "ИСУ"}).status_code == 200
        assert client.post("/api/camera", json={"on": True, "index": 0}).status_code == 200
        assert client.post("/api/calibrate").status_code == 200
        assert client.post("/api/heartbeat").status_code == 200
        assert client.post("/api/participant/reset").status_code == 200
        instructor(client)
        assert client.get("/api/exam-config").status_code == 200
        assert client.put("/api/exam-config", json=EXAM).status_code == 200
        assert client.get("/api/sessions").status_code == 200
        assert client.post("/api/logout").status_code == 200
        assert guard.enters == guard.leaves == 0
        assert not guard.active
        assert rt.store.sessions() == []


def test_exam_start_activates_isolation_and_saves_activated_capabilities(live_app, monkeypatch):
    with live_app() as (client, rt, _clock):
        guard = use_attempt_guard(rt, monkeypatch)
        assert not guard.capabilities()["desktop_isolated"]
        response = start(client)
        assert response.status_code == 200, response.text
        sid = response.json()["session"]["id"]
        saved = rt.store.session(sid)["config"]["guard"]
        for key in ("active", "keyboard_hook", "desktop_isolated", "navigation_guard"):
            assert saved[key] is True
        assert saved["window_controller"] == "pyautogui"
        assert guard.enters == 1
        assert guard.active


@pytest.mark.parametrize("ending", ["finish", "emergency", "phone", "timeout", "heartbeat"])
def test_every_terminal_path_releases_restrictions_and_preserves_attempt(live_app, monkeypatch, ending):
    with live_app() as (client, rt, clock):
        guard = use_attempt_guard(rt, monkeypatch)
        response = start(client)
        assert response.status_code == 200, response.text
        sid = response.json()["session"]["id"]
        question = rt.store.session(sid, True)["exam"][0]
        assert client.post("/api/answer", json={"question_id": question["id"], "option": 0}).status_code == 200
        if ending == "finish":
            assert client.post("/api/finish").status_code == 200
        elif ending == "emergency":
            assert client.post("/api/emergency").status_code == 200
        elif ending == "phone":
            sustained_phone(rt, clock)
        else:
            if ending == "timeout":
                session = rt.store.session(sid)
                now = session["started"] + session["config"]["duration_minutes"] * 60 + 1
                monkeypatch.setattr(server, "time", SimpleNamespace(time=lambda: now, monotonic=clock))
            else:
                clock.now = rt.last_seen + 21
            # One watchdog pass, with no sleeping or background races.
            waits = iter([False, True])
            monkeypatch.setattr(rt.closed, "wait", lambda _seconds: next(waits))
            rt.tick()
        assert rt.active_id is None
        assert not guard.active
        assert guard.leaves == 1
        assert not rt.cleanup_in_progress
        session = rt.store.session(sid)
        assert session["status"] == ({"emergency": "interrupted", "heartbeat": "interrupted", "phone": "disqualified"}.get(ending, "completed"))
        assert session["answers"][question["id"]] == 0
        assert rt.store.verify(sid)["ok"]
        assert not client.get("/api/state").json()["guard"]["active"]


def test_second_participant_gets_a_new_restricted_attempt_after_free_handoff(live_app, monkeypatch):
    with live_app() as (client, rt, _clock):
        guard = use_attempt_guard(rt, monkeypatch)
        first = start(client)
        assert first.status_code == 200, first.text
        assert client.post("/api/finish").status_code == 200
        assert client.post("/api/participant/reset").status_code == 200
        assert client.post("/api/participant/login", json={"name": "Другой участник", "group": "ИС"}).status_code == 200
        assert not guard.active and guard.enters == 1
        second = start(client)
        assert second.status_code == 200, second.text
        assert second.json()["session"]["id"] != first.json()["session"]["id"]
        assert second.json()["session"]["name"] == "Другой участник"
        assert guard.active and guard.enters == 2
        assert client.post("/api/finish").status_code == 200
        assert not guard.active and guard.leaves == 2


def test_diagnostic_window_cannot_start_a_live_exam_without_isolation_available(live_app, monkeypatch):
    with live_app() as (client, rt, _clock):
        guard = use_attempt_guard(rt, monkeypatch)
        guard.available = False
        response = start(client)
        assert response.status_code == 400, response.text
        assert rt.active_id is None and guard.enters == 0
        assert not guard.active
        assert rt.store.sessions() == []


def test_camera_not_ready_leaves_main_window_unrestricted(live_app, monkeypatch):
    with live_app() as (client, rt, _clock):
        guard = use_attempt_guard(rt, monkeypatch)
        monkeypatch.setattr(rt.camera, "status", lambda: {"camera_ok": False})
        response = start(client)
        assert response.status_code == 400, response.text
        assert "камер" in response.json()["detail"].lower()
        assert guard.enters == 0 and not guard.active
        assert rt.active_id is None


@pytest.mark.parametrize("missing", ["active", "keyboard_hook", "desktop_isolated", "navigation_guard", "window_controller"])
def test_incomplete_activation_fails_closed_and_releases_main_window(live_app, monkeypatch, missing):
    with live_app() as (client, rt, _clock):
        guard = use_attempt_guard(rt, monkeypatch)
        guard.broken = missing
        response = start(client)
        assert response.status_code == 400, response.text
        assert rt.active_id is None and not rt.cleanup_in_progress
        assert not guard.active and guard.enters == 1 and guard.leaves == 1
        assert not rt.entry_block
        saved = rt.store.sessions()
        assert len(saved) == 1 and saved[0]["status"] == "interrupted"
        assert [event["kind"] for event in rt.store.events(saved[0]["id"])] == ["guard_lost"]


def test_slow_isolation_activation_does_not_consume_exam_time(live_app, monkeypatch):
    with live_app() as (client, rt, clock):
        guard = use_attempt_guard(rt, monkeypatch)
        wall = [1000.0]
        monkeypatch.setattr(server, "time", SimpleNamespace(time=lambda: wall[0], monotonic=clock))
        monkeypatch.setattr(store_module, "time", SimpleNamespace(time=lambda: wall[0]))

        def slow_activation():
            wall[0] += 35
            clock.now += 35

        guard.before_enter = slow_activation
        response = start(client)
        assert response.status_code == 200, response.text
        session = response.json()["session"]
        assert session["started"] == wall[0]
        assert session["remaining"] == EXAM["duration_minutes"] * 60
        assert rt.last_seen == clock.now
