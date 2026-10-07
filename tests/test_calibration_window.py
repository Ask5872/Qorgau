"""Voluntary calibration fullscreen must never leave the main window stuck."""
from types import SimpleNamespace

import pytest
import qorgau.hub_window as module


@pytest.fixture
def calibration_window(monkeypatch):
    clock = [100.0]
    calls, timers = [], []
    class Timer:
        def __init__(self, delay, callback):
            self.callback, self.cancelled, self.daemon = callback, False, False
            timers.append(self)
        def start(self): pass
        def cancel(self): self.cancelled = True
    monkeypatch.setattr(module.threading, "Timer", Timer)
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    runtime = SimpleNamespace(active_id=None, cleanup_in_progress=False)
    window = SimpleNamespace(toggle_fullscreen=lambda: calls.append("toggle"),
                             minimize=lambda: calls.append("minimize"))
    bridge = module.HubWindowBridge(runtime)
    bridge._bind(window)
    return bridge, runtime, window, calls, timers, clock


def test_fullscreen_is_opt_in_and_begin_and_end_are_idempotent(calibration_window):
    bridge, _, _, calls, timers, _ = calibration_window
    assert calls == [] and timers == []
    assert bridge.hub_calibration_begin()["ok"]
    assert bridge.hub_calibration_begin()["ok"]
    assert calls == ["toggle"]
    assert timers[0].cancelled and timers[-1].daemon
    assert bridge.hub_calibration_end()["ok"]
    assert bridge.hub_calibration_end()["ok"]
    assert calls == ["toggle", "toggle"] and timers[-1].cancelled


def test_expired_ui_lease_restores_ordinary_frame(calibration_window):
    bridge, _, _, calls, timers, clock = calibration_window
    bridge.hub_calibration_begin()
    clock[0] = 111
    timers[-1].callback()
    assert calls == ["toggle", "toggle"]
    assert not bridge.hub_calibration_ping()["ok"]


def test_fresh_ui_ping_keeps_only_current_lease(calibration_window):
    bridge, _, _, calls, timers, clock = calibration_window
    bridge.hub_calibration_begin()
    clock[0] = 107
    assert bridge.hub_calibration_ping()["ok"]
    clock[0] = 111
    timers[-1].callback()
    assert calls == ["toggle"]
    clock[0] = 122
    timers[-1].callback()
    assert calls == ["toggle", "toggle"]


@pytest.mark.parametrize("active,cleanup", [("test", False), (None, True)])
def test_test_or_cleanup_cannot_enter_calibration(calibration_window, active, cleanup):
    bridge, runtime, _, calls, _, _ = calibration_window
    runtime.active_id, runtime.cleanup_in_progress = active, cleanup
    assert not bridge.hub_calibration_begin()["ok"]
    assert calls == []


def test_minimize_first_restores_normal_frame(calibration_window):
    bridge, _, _, calls, _, _ = calibration_window
    bridge.hub_calibration_begin()
    assert bridge.hub_minimize()["ok"]
    assert calls == ["toggle", "toggle", "minimize"]


def test_native_failure_is_reported_and_restoration_can_retry(calibration_window):
    bridge, _, window, calls, _, _ = calibration_window
    def failed(): raise RuntimeError("native unavailable")
    window.toggle_fullscreen = failed
    assert not bridge.hub_calibration_begin()["ok"]
    window.toggle_fullscreen = lambda: calls.append("toggle")
    assert bridge.hub_calibration_begin()["ok"]
    window.toggle_fullscreen = failed
    assert not bridge.hub_calibration_end()["ok"]
    window.toggle_fullscreen = lambda: calls.append("toggle")
    assert bridge.hub_calibration_end()["ok"]
    assert calls == ["toggle", "toggle"]


def test_dispose_cancels_watchdog_without_touching_closed_window(calibration_window):
    bridge, _, _, calls, timers, _ = calibration_window
    bridge.hub_calibration_begin()
    bridge._dispose()
    timers[-1].callback()
    assert timers[-1].cancelled and calls == ["toggle"]


def test_windows_calibration_pins_actual_native_display(calibration_window, monkeypatch):
    from qorgau import calibration_display
    bridge, runtime, window, calls, _, _ = calibration_window
    expected = {"device_name": "DISPLAY2", "bounds": [-1920, 0, 1920, 1080], "dpi": 120}
    runtime.guard = SimpleNamespace(calibration_display=None)
    def capture(target):
        assert target is window
        calls.append("capture_display")
        return expected
    monkeypatch.setattr(module.sys, "platform", "win32")
    monkeypatch.setattr(calibration_display, "capture_calibration_display", capture)
    assert bridge.hub_calibration_begin()["ok"]
    assert runtime.guard.calibration_display == expected
    assert calls == ["capture_display", "toggle"]
