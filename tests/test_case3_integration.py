"""Case 3 integration boundaries, using numbered camera results and no hardware.

These tests do not validate real-world recognition accuracy or Win32 desktop
switching. They exercise persistence, event order and the child/guardian API.
"""
from types import SimpleNamespace
import threading

import pytest

import qorgau.server as server
from test_enforcement import frame, live_app, start


POSE = {
    "phone_raised": True,
    "phone_aimed": True,
    "phone_track_id": 7,
    "phone_aim_details": {
        "track_id": 7,
        "raised": True,
        "aimed": True,
        "rise_frame_fraction": .16,
        "hold_seconds": .6,
        "in_screen_area": True,
        "plane": {"quality": .88, "plane_facing_camera": True},
        "reasons": ["tracked_upward_motion", "broad_plane_toward_webcam", "stable_hold"],
        "method": "tracked_plane_geometry",
        "photo_proven": False,
        "camera_side_known": False,
    },
}


class IsolationControlSpy:
    def __init__(self, calls=None):
        self.calls = calls if calls is not None else []

    def heartbeat(self):
        self.calls.append(("heartbeat", None))

    def request_exit(self, reason=""):
        self.calls.append(("request_exit", reason))


def test_observed_pose_is_saved_before_phone_dismissal_and_never_afterwards(live_app):
    with live_app() as (client, rt, clock):
        response = start(client, snapshots=True)
        assert response.status_code == 200, response.text
        sid = response.json()["session"]["id"]
        for seq, at in enumerate((100.0, 100.3, 100.6, 100.9, 101.2, 101.6), 1):
            frame(rt, clock, at, seq, **POSE)

        events = rt.store.events(sid)
        kinds = [event["kind"] for event in events]
        assert rt.store.session(sid)["status"] == "disqualified"
        assert kinds.count("auto_disqualified") == 1
        for kind in ("phone_raised", "phone_aimed"):
            assert kinds.count(kind) == 1
            assert kinds.index(kind) < kinds.index("auto_disqualified")
            observation = next(event for event in events if event["kind"] == kind)
            assert observation["detail"]
            # A pose must not become a claim that a photo was actually taken.
            assert "не " in observation["detail"]
            assert any(word in observation["detail"].lower() for word in ("съём", "сним", "кнопк"))
            assert observation["simulated"] is False
            assert (rt.store.folder / "evidence" / observation["evidence"]).read_bytes() == b"test-jpeg"
        assert rt.store.verify(sid)["ok"]

        for seq, at in enumerate((101.9, 102.3, 115.0, 115.6), 7):
            frame(rt, clock, at, seq, **POSE)
        assert rt.store.events(sid) == events


@pytest.mark.parametrize("outage", ["stale_analysis", "analysis_not_ready"])
def test_outdated_pose_cannot_accumulate_observations_or_dismiss(live_app, outage):
    with live_app() as (client, rt, clock):
        response = start(client)
        assert response.status_code == 200, response.text
        sid = response.json()["session"]["id"]
        for seq, at in enumerate((100.0, 100.3, 100.6, 100.9, 101.2, 101.6, 103.0), 1):
            changes = {**POSE}
            changes.update({"analyzed_at": at - 2} if outage == "stale_analysis"
                           else {"analysis_ready": False})
            frame(rt, clock, at, seq, **changes)
        kinds = {event["kind"] for event in rt.store.events(sid)}
        assert not kinds.intersection({"phone", "phone_raised", "phone_aimed", "auto_disqualified"})
        assert rt.store.session(sid)["status"] == "running"
        assert not rt.entry_block


def test_ui_heartbeat_reaches_guardian_before_exam_start(live_app):
    with live_app() as (client, rt, clock):
        control = IsolationControlSpy()
        rt.isolation_control = control
        assert rt.active_id is None
        clock.now = 150.0
        response = client.post("/api/heartbeat")
        assert response.status_code == 200, response.text
        assert rt.last_seen == 150.0
        assert control.calls == [("heartbeat", None)]


def test_emergency_requests_desktop_restoration_before_camera_cleanup(live_app, monkeypatch):
    with live_app() as (client, rt, _clock):
        response = start(client)
        assert response.status_code == 200, response.text
        sid = response.json()["session"]["id"]
        calls = []
        rt.isolation_control = IsolationControlSpy(calls)

        def stop_camera():
            calls.append(("camera_stop", None))
            # Slow/native cleanup must never hold the student in the desktop.
            assert any(kind == "request_exit" for kind, _ in calls)
            assert rt.active_id is None
            assert rt.store.session(sid)["status"] == "interrupted"

        monkeypatch.setattr(rt.camera, "stop", stop_camera)
        response = client.post("/api/emergency")
        assert response.status_code == 200, response.text
        assert calls[0][0] == "request_exit"
        assert any(kind == "camera_stop" for kind, _ in calls)
        assert [e["kind"] for e in rt.store.events(sid)].count("emergency_exit") == 1
        assert not rt.entry_block


def test_windows_live_exam_requires_isolation_before_start(live_app, monkeypatch):
    with live_app() as (client, rt, _clock):
        # Replace this module's binding, not global sys.platform: TestClient and
        # pathlib must keep running with the actual host's platform semantics.
        monkeypatch.setattr(server, "sys", SimpleNamespace(platform="win32"))
        rt.guard.isolated = False
        rt.guard.navigation_guard = False
        monkeypatch.setattr(rt.guard, "capabilities", lambda: {
            "native": True, "platform": "win32", "desktop_isolated": rt.guard.isolated,
            "navigation_guard": rt.guard.navigation_guard})
        response = start(client)
        assert response.status_code == 400, response.text
        assert "02_start.cmd" in response.json()["detail"]
        assert rt.active_id is None
        assert rt.guard.enters == 0

        rt.guard.isolated = True
        response = start(client)
        assert response.status_code == 400, response.text
        assert "WebView2" in response.json()["detail"]
        assert rt.active_id is None
        assert rt.guard.enters == 0

        rt.guard.navigation_guard = True
        response = start(client)
        assert response.status_code == 200, response.text
        sid = response.json()["session"]["id"]
        assert rt.store.session(sid)["config"]["guard"]["desktop_isolated"] is True
        assert rt.guard.enters == 1


def test_failed_guard_start_interrupts_attempt_and_releases_camera_outside_lock(live_app, monkeypatch):
    with live_app() as (client, rt, _clock):
        original_enter = rt.guard.enter

        def failed_enter():
            rt.guard.enters += 1
            raise RuntimeError("Keyboard hook unavailable")

        def stop_camera():
            rt.camera.stops += 1
            # A model/camera worker may need the runtime lock to finish its
            # callback. Cleanup must allow that worker to complete.
            acquired = threading.Event()

            def worker():
                with rt.lock:
                    acquired.set()

            thread = threading.Thread(target=worker, daemon=True)
            thread.start()
            thread.join(timeout=1)
            assert acquired.is_set(), "Camera cleanup still holds the runtime lock"

        monkeypatch.setattr(rt.guard, "enter", failed_enter)
        monkeypatch.setattr(rt.camera, "stop", stop_camera)
        response = start(client)
        assert response.status_code == 400, response.text
        assert "Keyboard hook unavailable" in response.json()["detail"]
        assert rt.active_id is None
        assert not rt.cleanup_in_progress
        assert not rt.entry_block
        assert rt.camera.stops >= 1
        assert rt.guard.leaves >= 1
        sessions = rt.store.sessions()
        assert len(sessions) == 1
        assert sessions[0]["status"] == "interrupted"
        events = rt.store.events(sessions[0]["id"])
        assert [event["kind"] for event in events] == ["guard_lost"]
        assert "Keyboard hook unavailable" in events[0]["detail"]
        assert rt.store.verify(sessions[0]["id"])["ok"]

        # A transient initialization failure must not lock this workstation.
        monkeypatch.setattr(rt.guard, "enter", original_enter)
        assert start(client).status_code == 200
