"""Per-attempt desktop lifecycle without invoking Windows or launching children."""
import json
from pathlib import Path

import pytest

from qorgau.attempt_guard import AttemptDesktopGuard
from qorgau.desktop_session import ChildControl, _write_json


HEALTHY = {"active": True, "keyboard_hook": True, "navigation_guard": True,
           "window_controller": "pyautogui", "desktop_isolated": True}


class Clock:
    def __init__(self):
        self.now = 0
        self.progress = lambda: None
    def __call__(self):
        return self.now
    def wait(self, seconds):
        self.now += seconds
        self.progress()


class Process:
    def __init__(self):
        self.code = None
    def poll(self):
        return self.code


class Window:
    def __init__(self, calls):
        self.calls = calls
    def hide(self):
        self.calls.append("hide_hub")
    def show(self):
        self.calls.append("show_hub")


class API:
    def __init__(self, calls):
        self.calls = calls
        self.fail = False
    def open_original(self):
        self.calls.append("open_original")
        return "original"
    def switch(self, desktop):
        self.calls.append("fallback_restore")
        assert desktop == "original"
        if self.fail:
            raise OSError("restore denied")
    def close_desktop(self, desktop):
        self.calls.append("close_original")


@pytest.fixture
def rig(tmp_path, monkeypatch):
    import qorgau.attempt_guard as module
    monkeypatch.setattr(module.sys, "platform", "win32")
    calls, events, emergency, clock = [], [], [], Clock()
    api, process = API(calls), Process()
    settings = {"ready": True, "activate": True, "guard": dict(HEALTHY), "restore": True}
    control = {}

    def spawn(command, **kwargs):
        calls.append("spawn_supervisor")
        assert "--exam-supervisor" in command and kwargs["creationflags"]
        folder = Path(command[command.index("--isolation-control") + 1])
        child = ChildControl(folder)
        child.set_protection(lambda: settings["guard"])
        control.update(folder=folder, child=child)
        if settings["ready"]:
            child.ready()
        if settings["activate"]:
            config = json.loads((folder / "config.json").read_text())
            _write_json(folder / "activated.json", {"activated": True, "control_id": config["control_id"]})
        return process

    guard = AttemptDesktopGuard(lambda *args: events.append(args), lambda: emergency.append(True),
        tmp_path, "http://127.0.0.1:8765", "csrf-secret", process_factory=spawn,
        api_factory=lambda: api, startup_timeout=0.3, restore_timeout=0.2,
        clock=clock, wait=clock.wait)
    guard.window = Window(calls)
    # Tests invoke the monitoring step explicitly; no wall-clock threads race
    # against the deterministic clock used by startup/cleanup assertions.
    monkeypatch.setattr(guard, "_monitor", lambda **kwargs: None)

    def progress():
        folder = control.get("folder")
        if folder and (folder / "exit.json").exists() and settings["restore"]:
            config = json.loads((folder / "config.json").read_text())
            calls.append("supervisor_restore")
            _write_json(folder / "restored.json", {"restored": True, "control_id": config["control_id"]})
            process.code = 0
    clock.progress = progress
    yield guard, calls, events, emergency, settings, control, process, api, clock
    process.code = 0
    api.fail = False
    guard.leave()


def test_idle_hub_never_launches_or_restricts(rig):
    guard, calls, *_ = rig
    state = guard.capabilities()
    assert state["isolation_available"] and state["desktop"]
    assert not any(state[key] for key in ("active", "desktop_isolated", "keyboard_hook", "navigation_guard"))
    assert state["window_controller"] is None
    assert calls == []
    guard.leave()
    assert calls == []


def test_enter_waits_native_guard_and_switch_then_hides_hub(rig):
    guard, calls, _, _, _, control, *_ = rig
    guard.enter()
    assert guard.capabilities()["active"] and guard.capabilities()["desktop_isolated"]
    assert calls == ["open_original", "spawn_supervisor", "hide_hub"]
    assert control["child"].activated()
    guard.leave()
    assert calls.index("supervisor_restore") < calls.index("show_hub")
    assert not guard.capabilities()["active"]
    assert guard.capabilities()["isolation_available"]
    assert not control["folder"].exists()


@pytest.mark.parametrize("setting", ["ready", "activate"])
def test_missing_readiness_or_activation_never_starts_test(rig, setting):
    guard, calls, _, _, settings, *_ = rig
    settings[setting] = False
    with pytest.raises(RuntimeError, match="вовремя"):
        guard.enter()
    assert "hide_hub" not in calls
    assert not guard.active
    assert "supervisor_restore" in calls


@pytest.mark.parametrize("key", ["keyboard_hook", "navigation_guard", "active", "window_controller"])
def test_partial_native_protection_cannot_start(rig, key):
    guard, calls, _, _, settings, *_ = rig
    settings["guard"][key] = False
    with pytest.raises(RuntimeError, match="не готова"):
        guard.enter()
    assert "hide_hub" not in calls and not guard.active


def test_duplicate_enter_does_not_spawn_another_desktop(rig):
    guard, calls, *_ = rig
    guard.enter()
    with pytest.raises(RuntimeError, match="ещё завершается"):
        guard.enter()
    assert calls.count("spawn_supervisor") == 1


def test_nonblocking_exit_written_without_wait_or_teardown(rig):
    guard, calls, _, _, _, control, _, _, clock = rig
    guard.enter()
    before = clock.now
    guard.request_exit("Аварийное завершение")
    assert clock.now == before
    state = json.loads((control["folder"] / "exit.json").read_text())
    assert state["reason"] == "Аварийное завершение"
    assert "show_hub" not in calls


def test_supervisor_crash_uses_original_handle_before_showing_hub(rig):
    guard, calls, _, _, settings, _, process, *_ = rig
    guard.enter()
    settings["restore"] = False
    process.code = 1
    guard.leave()
    assert calls.index("fallback_restore") < calls.index("show_hub")
    assert not guard.capabilities()["desktop_isolated"]


def test_unexpected_child_loss_notifies_runtime(rig):
    guard, _, _, emergencies, _, _, process, *_ = rig
    guard.enter()
    process.code = 1
    AttemptDesktopGuard._monitor(guard)
    assert emergencies == [True]
    assert not guard.capabilities()["active"]


def test_native_events_delivered_once_and_wrong_attempt_ignored(rig):
    guard, _, events, _, _, control, *_ = rig
    guard.enter()
    control["child"].emit("shortcut", "Alt+Tab")
    with (control["folder"] / "events.jsonl").open("a") as stream:
        stream.write(json.dumps({"control_id": "stale", "id": 100, "kind": "shortcut", "detail": "stale"}) + "\n")
    guard._events()
    guard._events()
    assert events == [("shortcut", "Alt+Tab")]
    control["child"].emit("focus_lost", "focus")
    guard._events()
    assert events[-1] == ("focus_lost", "focus")


def test_late_cleanup_blocks_new_attempt_but_hub_is_restored(rig):
    guard, calls, _, _, settings, control, process, *_ = rig
    guard.enter()
    settings["restore"] = False
    guard.leave()
    assert "show_hub" in calls and guard._stopping
    with pytest.raises(RuntimeError, match="ещё завершается"):
        guard.enter()
    process.code = 0
    guard.leave()
    assert not guard._stopping and not control["folder"].exists()


def test_failed_restoration_is_reported_without_discarding_recovery_handle(rig):
    guard, calls, _, _, settings, _, process, api, _ = rig
    guard.enter()
    settings["restore"] = False
    process.code = 1
    api.fail = True
    with pytest.raises(RuntimeError, match="Не удалось вернуть"):
        guard.leave()
    assert "show_hub" not in calls
    assert guard._original == "original"


def test_inflight_previous_monitor_prevents_new_attempt(rig):
    guard, calls, *_ = rig
    class StillFinishing:
        def is_alive(self):
            return True
        def join(self, timeout):
            assert timeout == 2
    guard._monitor_thread = StillFinishing()
    with pytest.raises(RuntimeError, match="ещё завершается"):
        guard.enter()
    assert calls == []
    guard._monitor_thread = None
