"""Per-attempt GUI handshake; native desktop switching is tested separately."""
import json
import threading
from types import SimpleNamespace

import pytest

from qorgau.exam_window import ExamWindowLifecycle, _Bridge, _exam_url
from qorgau.guard import DesktopGuard


class FakeControl:
    def __init__(self, trace):
        self.trace = trace
        self.stop = self.switched = False
        self.pulses = 0
        self.ready_called = False

    def set_protection(self, provider):
        self.provider = provider

    def emit(self, kind, detail):
        self.trace.append((kind, detail))

    def heartbeat(self):
        self.pulses += 1
        self.trace.append("ui-pulse")

    def request_exit(self, reason):
        self.trace.append(("exit", reason))
        # The real supervisor writes shutdown only AFTER restoring the desktop.
        self.trace.append("restore-desktop")
        self.stop = True

    def ready(self):
        self.ready_called = True
        self.trace.append("ready")

    def activated(self):
        return self.switched

    def stop_requested(self):
        return self.stop

    def close(self):
        self.trace.append("close-control")


def make_lifecycle(*, first_ui=True, nav_ready=True, fault=None):
    trace, tick = [], [0.0]
    control = FakeControl(trace)
    holder = {}

    def wait(seconds):
        tick[0] += seconds
        if control.ready_called:
            if control.switched:
                # Leave one iteration to observe acknowledgement before stop.
                assert holder["owner"].activated.is_set()
                trace.append("restore-desktop")
                control.stop = True
            else:
                trace.append("switch-desktop")
                control.switched = True

    owner = ExamWindowLifecycle(control, clock=lambda: tick[0], wait=wait, startup_timeout=0.2)
    holder["owner"] = owner

    class Guard:
        active = False
        def enter(self):
            trace.append("enter-guard")
            if fault == "enter":
                raise RuntimeError("native hook failed")
            self.active = True

        def leave(self):
            trace.append("leave-guard")
            self.active = False

        def _check_health(self):
            trace.append("check-health")
            if fault == "health":
                raise RuntimeError("native hook lost")

    class Navigation:
        def __init__(self):
            self.ready = threading.Event()
            if nav_ready:
                self.ready.set()
            self.error = ""

        def refresh(self):
            trace.append("restrict-navigation" if owner.guard.active else "release-navigation")
            if fault == "navigation" and owner.guard.active:
                self.error = "native navigation failed"
                return False
            return True

        def cleanup(self):
            trace.append("cleanup-navigation")

    owner.guard = Guard()
    owner.navigation = Navigation()
    owner.window = SimpleNamespace(destroy=lambda: trace.append("destroy-window"))
    if first_ui:
        _Bridge(owner).exam_heartbeat()
    return owner, control, trace


def test_ready_requires_live_ui_then_full_native_protection_before_switch():
    owner, control, trace = make_lifecycle()
    owner.run()
    assert trace == ["ui-pulse", "enter-guard", "restrict-navigation", "check-health", "ready",
                     "switch-desktop", "restore-desktop", "leave-guard", "release-navigation", "destroy-window"]
    assert control.pulses == 1  # No worker or activation polling heartbeat.
    assert owner.activated.is_set()
    assert not owner.guard.active


@pytest.mark.parametrize("first_ui,nav_ready", [(False, True), (True, False), (False, False)])
def test_missing_ui_or_native_readiness_never_arms_window(first_ui, nav_ready):
    owner, control, trace = make_lifecycle(first_ui=first_ui, nav_ready=nav_ready)
    owner.run()
    assert not control.ready_called
    assert "enter-guard" not in trace
    assert "switch-desktop" not in trace
    assert control.pulses == int(first_ui)
    assert any(item[0] == "exit" and "вовремя" in item[1] for item in trace if isinstance(item, tuple))
    assert trace.index("restore-desktop") < trace.index("destroy-window")


@pytest.mark.parametrize("fault", ["enter", "health", "navigation"])
def test_failed_native_start_requests_exit_without_claiming_ready(fault):
    owner, control, trace = make_lifecycle(fault=fault)
    owner.run()
    assert not control.ready_called
    assert "switch-desktop" not in trace
    exit_index = next(i for i, item in enumerate(trace) if isinstance(item, tuple) and item[0] == "exit")
    assert exit_index < trace.index("leave-guard") < trace.index("destroy-window")


def test_native_initialization_error_requests_restore_before_teardown():
    owner, control, trace = make_lifecycle(nav_ready=False)
    owner.navigation.error = "WebView2 unavailable"
    owner.run()
    assert not control.ready_called
    assert "enter-guard" not in trace
    assert any(item[0] == "exit" and "WebView2 unavailable" in item[1]
               for item in trace if isinstance(item, tuple))
    assert trace.index("restore-desktop") < trace.index("destroy-window")


def test_closing_and_emergency_request_exit_before_window_can_close():
    owner, control, trace = make_lifecycle()
    assert owner.closing() is False
    owner.emergency()
    assert len([item for item in trace if isinstance(item, tuple) and item[0] == "exit"]) == 1
    assert "destroy-window" not in trace
    assert not _Bridge(owner).exam_heartbeat()
    owner.destroying = True
    assert owner.closing() is True


def test_no_guard_side_effects_outside_an_attempt():
    trace = []
    window = SimpleNamespace(on_top=False, toggle_fullscreen=lambda: trace.append("fullscreen"))
    guard = DesktopGuard(lambda *args: trace.append(args), lambda: trace.append("emergency"))
    guard.window = window
    guard.leave()
    assert trace == []
    assert window.on_top is False
    assert guard.active is False and guard.hook_ok is False
    assert guard.hook_thread is None and guard.watch_thread is None


def test_pending_desktop_activation_checks_health_but_never_takes_focus(monkeypatch):
    import ctypes
    trace = []
    activate = threading.Event()
    guard = DesktopGuard(lambda *args: trace.append(args), lambda: trace.append("emergency"),
                         activation_event=activate)
    guard.active = True
    guard.foreground = SimpleNamespace(is_foreground=lambda: trace.append("query-focus"),
                                       activate=lambda: trace.append("take-focus"))
    guard._check_health = lambda: trace.append("health")
    count = [0]
    def stop_wait(_seconds):
        count[0] += 1
        return count[0] >= 4
    guard.stopped = SimpleNamespace(wait=stop_wait)
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=SimpleNamespace(GetSystemMetrics=lambda _: 1)), raising=False)
    guard._watch_loop()
    assert trace == ["health", "health", "health"]


def test_pending_activation_still_handles_emergency(monkeypatch):
    import ctypes
    trace = []
    guard = DesktopGuard(lambda *args: trace.append(args), lambda: trace.append("emergency"),
                         activation_event=threading.Event())
    guard.active = True
    guard.foreground = SimpleNamespace(is_foreground=lambda: trace.append("query-focus"))
    guard._check_health = lambda: trace.append("health")
    guard.events.put("EMERGENCY")
    guard.stopped = SimpleNamespace(wait=lambda _: False)
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=SimpleNamespace(GetSystemMetrics=lambda _: 1)), raising=False)
    guard._watch_loop()
    assert trace == ["health", "emergency"]


@pytest.mark.parametrize("url", ["http://127.0.0.1:8765", "http://localhost:8765/", "http://[::1]:8765"])
def test_child_joins_local_server_url_without_starting_a_second_server(tmp_path, url):
    (tmp_path / "config.json").write_text(json.dumps({"url": url, "csrf": "secret"}), encoding="utf-8")
    assert _exam_url(tmp_path) == url.rstrip("/") + "/?exam=1#exam"


@pytest.mark.parametrize("url", [None, "https://example.com", "http://127.0.0.1:8765/foreign",
                                    "http://user@localhost:8765", "http://127.0.0.1", "http://127.0.0.1:8765/?x=1"])
def test_child_rejects_nonlocal_or_malformed_configuration(tmp_path, url):
    (tmp_path / "config.json").write_text(json.dumps({"url": url}), encoding="utf-8")
    with pytest.raises(ValueError):
        _exam_url(tmp_path)


def test_gui_entry_connects_to_existing_server_and_binds_only_exam_heartbeat(tmp_path, monkeypatch):
    import sys
    import qorgau.exam_window as module
    import qorgau.navigation as navigation
    from qorgau.desktop_session import ChildControl

    (tmp_path / "config.json").write_text(json.dumps({"url": "http://127.0.0.1:8765", "control_id": "attempt"}), encoding="utf-8")
    trace, capture = [], {}

    class Event:
        def __init__(self):
            self.handlers = []
        def __iadd__(self, handler):
            self.handlers.append(handler)
            return self

    window = SimpleNamespace(events=SimpleNamespace(shown=Event(), closing=Event()), on_top=False)
    def create_window(title, url, **kwargs):
        capture.update(title=title, url=url, **kwargs)
        return window
    def install(window_arg, url, is_active, emit):
        assert window_arg is window
        assert url == "http://127.0.0.1:8765/?exam=1#exam"
        assert is_active() is False
        capture["active"] = is_active
        return SimpleNamespace(ready=threading.Event(), cleanup=lambda: trace.append("cleanup-navigation"))
    def start(**kwargs):
        assert kwargs == {"debug": False, "private_mode": True, "gui": "edgechromium"}
        assert capture["js_api"].exam_heartbeat()
        status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
        assert not status["ready"] and not status["guard"]["active"]
        assert status["control_id"] == "attempt"
        assert len(window.events.shown.handlers) == len(window.events.closing.handlers) == 1

    monkeypatch.setattr(module.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(settings={}, create_window=create_window, start=start))
    monkeypatch.setattr(navigation, "install_navigation_guard", install)
    assert module.run_exam_window(tmp_path) == 0
    assert capture["url"] == "http://127.0.0.1:8765/?exam=1#exam"
    assert not capture.get("fullscreen") and not capture.get("on_top")
    assert [name for name in dir(capture["js_api"]) if not name.startswith("_")] == ["exam_heartbeat"]
    assert trace == ["cleanup-navigation"]
    assert not capture["active"]()
    assert (tmp_path / "exit.json").exists()
