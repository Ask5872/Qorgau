"""Supervisor fail-path tests. The Win32 APIs require separate Windows testing."""
from pathlib import Path
import json
import threading

import pytest

from qorgau.desktop_session import ChildControl, supervise
from qorgau.guard import blocked_shortcut, DesktopGuard


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.on_wait = lambda: None

    def __call__(self):
        return self.now

    def wait(self, seconds):
        self.now += seconds
        self.on_wait()


class FakeProcess:
    def __init__(self, calls):
        self.calls = calls
        self.code = None

    def poll(self):
        return self.code

    def terminate(self):
        self.calls.append("terminate_owned_child")
        self.code = 1

    def close(self):
        self.calls.append("close_process")


class FakeAPI:
    def __init__(self, folder, fail=None):
        self.calls, self.folder, self.fail = [], folder, fail
        self.child = FakeProcess(self.calls)
        self.emergency = False
        self.on_spawn = lambda: None

    def open_original(self):
        self.calls.append("open_original")
        return "original"

    def create(self, name):
        self.calls.append("create_isolated")
        if self.fail == "create":
            raise OSError("access denied")
        return "isolated"

    def start_emergency(self, desktop):
        self.calls.append("start_independent_emergency")
        if self.fail == "hotkey":
            raise OSError("hotkey unavailable")

    def emergency_requested(self):
        return self.emergency

    def spawn(self, command, cwd, desktop_name, log_path):
        self.calls.append("spawn")
        assert command[-2:] == ["--isolation-control", str(self.folder)]
        assert desktop_name.startswith("Qorgau_")
        self.on_spawn()
        return self.child

    def switch(self, handle):
        self.calls.append("switch_" + handle)
        if self.fail == "switch" and handle == "isolated":
            raise OSError("cannot switch")

    def stop_emergency(self):
        self.calls.append("stop_emergency")

    def close_desktop(self, handle):
        if handle:
            self.calls.append("close_" + handle)


def run_fake(tmp_path, api, clock):
    return supervise(["python", "run.py", "--desktop"], tmp_path, tmp_path, api,
                     startup_timeout=1, heartbeat_timeout=0.6, shutdown_grace=0.3,
                     clock=clock, wait=clock.wait, output=lambda _: None)


def test_child_control_ready_and_pulses(tmp_path):
    child = ChildControl(tmp_path)
    child.heartbeat()
    state = json.loads((tmp_path / "status.json").read_text())
    assert state == {"ready": False, "sequence": 1}
    child.ready()
    child.heartbeat()
    assert json.loads((tmp_path / "status.json").read_text()) == {"ready": True, "sequence": 3}
    assert not child.stop_requested()
    (tmp_path / "shutdown.json").write_text("{}")
    assert child.stop_requested()
    child.request_exit("Аварийный выход")
    assert json.loads((tmp_path / "exit.json").read_text())["reason"] == "Аварийный выход"


def test_unready_window_never_replaces_desktop(tmp_path):
    api, clock = FakeAPI(tmp_path), FakeClock()
    assert run_fake(tmp_path, api, clock) == 1
    assert "switch_isolated" not in api.calls
    assert api.calls.index("switch_original") < api.calls.index("terminate_owned_child")


def test_stalled_ui_restores_desktop_before_terminating_own_child(tmp_path):
    api, clock = FakeAPI(tmp_path), FakeClock()
    api.on_spawn = ChildControl(tmp_path).ready
    assert run_fake(tmp_path, api, clock) == 1
    assert api.calls.index("start_independent_emergency") < api.calls.index("switch_isolated")
    assert api.calls.index("switch_original") < api.calls.index("terminate_owned_child")
    assert (tmp_path / "shutdown.json").exists()


def test_independent_emergency_survives_missing_ui_heartbeat(tmp_path):
    api, clock = FakeAPI(tmp_path), FakeClock()
    api.on_spawn = ChildControl(tmp_path).ready
    clock.on_wait = lambda: setattr(api, "emergency", True)
    assert run_fake(tmp_path, api, clock) == 0
    assert "switch_isolated" in api.calls and "switch_original" in api.calls
    assert api.calls.index("switch_original") < api.calls.index("terminate_owned_child")


def test_clean_child_exit_restores_without_termination(tmp_path):
    api, clock = FakeAPI(tmp_path), FakeClock()
    api.on_spawn = ChildControl(tmp_path).ready
    clock.on_wait = lambda: setattr(api.child, "code", 0)
    assert run_fake(tmp_path, api, clock) == 0
    assert "switch_original" in api.calls
    assert "terminate_owned_child" not in api.calls


def test_child_request_restores_before_orderly_shutdown(tmp_path):
    api, clock, control = FakeAPI(tmp_path), FakeClock(), ChildControl(tmp_path)
    api.on_spawn = control.ready

    def progress():
        if control.stop_requested():
            api.child.code = 0
        else:
            control.request_exit()
    clock.on_wait = progress
    assert run_fake(tmp_path, api, clock) == 0
    assert "switch_original" in api.calls
    assert "terminate_owned_child" not in api.calls


@pytest.mark.parametrize("failure", ["create", "hotkey", "switch"])
def test_setup_errors_restore_original_and_release_handles(tmp_path, failure):
    api, clock = FakeAPI(tmp_path, fail=failure), FakeClock()
    api.on_spawn = ChildControl(tmp_path).ready
    assert run_fake(tmp_path, api, clock) == 1
    assert "switch_original" in api.calls
    assert api.calls[-1] == "close_original"
    if failure == "hotkey":
        assert "spawn" not in api.calls


def test_live_heartbeats_prevent_timeout(tmp_path):
    api, clock, control = FakeAPI(tmp_path), FakeClock(), ChildControl(tmp_path)
    api.on_spawn = control.ready

    def progress():
        control.heartbeat()
        if clock.now > 3:
            api.child.code = 0
    clock.on_wait = progress
    assert run_fake(tmp_path, api, clock) == 0
    assert clock.now > 3
    assert "terminate_owned_child" not in api.calls


@pytest.mark.parametrize("vk, pressed", [
    (0x21, {0x11}), (0x22, {0x11}), (0x25, {0x12}), (0x27, {0x12}),
    (0x79, {0x10}), (0xA6, set()), (0xA7, set()), (0x74, set()),
    (ord("R"), {0x11}), (ord("T"), {0x11}), (ord("W"), {0x11}),
])
def test_navigation_shortcuts_are_blocked(vk, pressed):
    assert blocked_shortcut(vk, pressed)


def test_safety_exit_and_regular_typing_stay_available():
    assert blocked_shortcut(ord("Q"), {0x11, 0x10}) == "EMERGENCY"
    assert blocked_shortcut(0x2E, {0x11, 0x12}) is None
    assert blocked_shortcut(ord("A"), set()) is None
    guard = DesktopGuard(lambda *_: None, lambda: None)
    assert not guard.capabilities()["desktop_isolated"]
    guard.isolated = True
    assert guard.capabilities()["desktop_isolated"]
    guard.navigation_guard = threading.Event()
    assert not guard.capabilities()["navigation_guard"]
    guard.navigation_guard.set()
    assert guard.capabilities()["navigation_guard"]


def test_isolated_keyboard_failure_prevents_exam_and_releases_window(monkeypatch):
    import qorgau.guard as module
    monkeypatch.setattr(module.sys, "platform", "win32")

    class Window:
        def __init__(self):
            self.toggles = 0
            self.on_top = False
        def toggle_fullscreen(self):
            self.toggles += 1

    guard = DesktopGuard(lambda *_: None, lambda: None)
    guard.window, guard.isolated = Window(), True
    monkeypatch.setattr(guard, "_hook", lambda: guard.ready.set())
    with pytest.raises(RuntimeError, match="блокировку клавиш"):
        guard.enter()
    assert not guard.active and not guard.window.on_top
    assert guard.window.toggles == 2
    assert guard.watch_thread is None


def test_control_attaches_attempt_identity_and_native_health(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"control_id": "attempt-1"}))
    child = ChildControl(tmp_path)
    health = {"active": True, "keyboard_hook": True}
    child.set_protection(lambda: dict(health))
    child.ready()
    state = json.loads((tmp_path / "status.json").read_text())
    assert state["control_id"] == "attempt-1" and state["guard"] == health
    health["keyboard_hook"] = False
    child.heartbeat()
    assert not json.loads((tmp_path / "status.json").read_text())["guard"]["keyboard_hook"]
    child.emit("shortcut", "Alt+Tab")
    child.emit("focus_lost", "focus")
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert [event["id"] for event in events] == [1, 2]
    assert all(event["control_id"] == "attempt-1" for event in events)
    assert not child.activated()
    (tmp_path / "activated.json").write_text(json.dumps({"activated": True, "control_id": "old"}))
    assert not child.activated()
    (tmp_path / "activated.json").write_text(json.dumps({"activated": True, "control_id": "attempt-1"}))
    assert child.activated()


def test_supervisor_acknowledges_switch_and_restoration(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"control_id": "attempt-2"}))
    api, clock, control = FakeAPI(tmp_path), FakeClock(), ChildControl(tmp_path)
    control.set_protection({"active": True, "keyboard_hook": True, "navigation_guard": True,
                            "window_controller": "pyautogui"})
    api.on_spawn = control.ready
    observed = []

    def progress():
        if control.activated():
            observed.append("activated")
            assert "switch_isolated" in api.calls
        if (tmp_path / "restored.json").exists():
            assert "switch_original" in api.calls
            observed.append("restored")
        if control.stop_requested():
            api.child.code = 0
        else:
            control.request_exit()
    clock.on_wait = progress
    assert run_fake(tmp_path, api, clock) == 0
    assert "activated" in observed and "restored" in observed
    assert json.loads((tmp_path / "restored.json").read_text())["control_id"] == "attempt-2"


def test_private_attempt_refuses_switch_without_native_protection(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"control_id": "attempt-3"}))
    api, clock, control = FakeAPI(tmp_path), FakeClock(), ChildControl(tmp_path)
    control.set_protection({"active": True, "keyboard_hook": False, "navigation_guard": True,
                            "window_controller": "pyautogui"})
    api.on_spawn = control.ready
    assert run_fake(tmp_path, api, clock) == 1
    assert "switch_isolated" not in api.calls
    assert "switch_original" in api.calls
