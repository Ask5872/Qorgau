"""Policy and failure-path tests; actual hook/desktop acceptance needs Windows."""
from types import SimpleNamespace
import threading

import pytest

import qorgau.guard as module
from qorgau.guard import DesktopGuard, ForegroundController, blocked_shortcut


class FakeAutomation:
    def __init__(self):
        self.foreground = None
        self.activations = []

    def Window(self, hwnd):
        return SimpleNamespace(_hWnd=hwnd, activate=lambda: self.activations.append(hwnd))

    def getActiveWindow(self):
        return self.foreground


def test_pyautogui_monitors_and_restores_only_verified_native_window():
    automation = FakeAutomation()
    hwnd = 0x100000012  # Pointer-sized native handles must not be truncated.
    window = SimpleNamespace(native=SimpleNamespace(Handle=SimpleNamespace(ToInt64=lambda: hwnd)))
    owners = {hwnd: 99}
    controller = ForegroundController(window, automation, lambda h: owners.get(h, 0), 99)
    assert not controller.is_foreground()
    automation.foreground = automation.Window(42)
    assert not controller.is_foreground()
    controller.activate()
    assert automation.activations == [hwnd]
    automation.foreground = automation.Window(hwnd)
    assert controller.is_foreground()
    owners[hwnd] = 100  # Deleted/reused HWND must never activate another process.
    with pytest.raises(RuntimeError, match="закрыто или заменено"):
        controller.is_foreground()
    with pytest.raises(RuntimeError, match="больше не существует"):
        controller.activate()
    assert automation.activations == [hwnd]


@pytest.mark.parametrize("handle, owner", [(0, 99), (5, 101)])
def test_missing_or_foreign_window_prevents_controller_start(handle, owner):
    window = SimpleNamespace(native=SimpleNamespace(Handle=handle))
    with pytest.raises(RuntimeError, match="подтвердить"):
        ForegroundController(window, FakeAutomation(), lambda h: owner, 99)


@pytest.mark.parametrize("vk, pressed", [
    (0x09, {0x12}), (ord("C"), {0x11}), (ord("V"), {0xA3}),
    (0x5B, set()), (0x5C, set()), (0x2C, set()), (0x09, {0x5B}),
    (0x2D, {0x10}), (0x2D, {0x11}), (0x2E, {0x10}), (0x5D, set()),
    (ord("1"), {0x11}), (ord("9"), {0x11}),
])
def test_case3_and_alternative_copy_paste_tab_shortcuts_are_blocked(vk, pressed):
    assert blocked_shortcut(vk, pressed) not in {None, "EMERGENCY"}


def test_escape_sequences_survive_even_with_preheld_windows_key():
    assert blocked_shortcut(ord("Q"), {0x11, 0x10, 0x5B}) == "EMERGENCY"
    assert blocked_shortcut(0x2E, {0x11, 0x12, 0x5B}) is None


def healthy_guard():
    guard = DesktopGuard(lambda *_: None, lambda: None)
    guard.active = guard.hook_ok = guard.isolated = True
    guard.hook_thread = SimpleNamespace(is_alive=lambda: True)
    guard.navigation_guard = threading.Event()
    guard.navigation_guard.set()
    return guard


@pytest.mark.parametrize("fault", ["hook_flag", "hook_thread", "webview"])
def test_loss_of_native_protection_is_detected_after_start(fault):
    guard = healthy_guard()
    guard._check_health()
    if fault == "hook_flag":
        guard.hook_ok = False
    elif fault == "hook_thread":
        guard.hook_thread = SimpleNamespace(is_alive=lambda: False)
    else:
        guard.navigation_guard.clear()
    with pytest.raises(RuntimeError, match="экзамен остановлен"):
        guard._check_health()


def test_watchdog_failure_reports_and_invokes_emergency(monkeypatch):
    events = []
    guard = DesktopGuard(lambda *event: events.append(event), lambda: events.append(("emergency",)))
    monkeypatch.setattr(guard, "_watch_loop", lambda: (_ for _ in ()).throw(RuntimeError("hook lost")))
    monkeypatch.setattr(guard, "leave", lambda: events.append(("leave",)))
    guard._watch()
    assert [event[0] for event in events] == ["guard_lost", "emergency", "leave"]


def test_failed_window_mode_change_does_not_leave_guard_active():
    class Window:
        on_top = False
        def toggle_fullscreen(self):
            raise RuntimeError("window disposed")
    guard = DesktopGuard(lambda *_: None, lambda: None)
    guard.window = Window()
    with pytest.raises(RuntimeError, match="window disposed"):
        guard.enter()
    assert not guard.active
    assert not guard.window.on_top


def test_previous_guard_thread_prevents_unsafe_reentry():
    guard = DesktopGuard(lambda *_: None, lambda: None)
    guard.window = object()
    guard.hook_thread = SimpleNamespace(is_alive=lambda: True)
    with pytest.raises(RuntimeError, match="ещё завершается"):
        guard.enter()
    assert not guard.active


def test_pyautogui_initialization_failure_rolls_back_protection(monkeypatch):
    monkeypatch.setattr(module.sys, "platform", "win32")
    class Window:
        toggles = 0
        on_top = False
        def toggle_fullscreen(self):
            self.toggles += 1
    guard = DesktopGuard(lambda *_: None, lambda: None)
    guard.window, guard.isolated = Window(), True
    def hook():
        guard.hook_ok = True
        guard.ready.set()
        guard.stopped.wait(3)
        guard.hook_ok = False
    monkeypatch.setattr(guard, "_hook", hook)
    monkeypatch.setattr(module, "_foreground_controller", lambda _: (_ for _ in ()).throw(RuntimeError("PyAutoGUI unavailable")))
    with pytest.raises(RuntimeError, match="PyAutoGUI unavailable"):
        guard.enter()
    assert not guard.active and not guard.window.on_top
    assert guard.window.toggles == 2
    assert not guard.hook_thread.is_alive()
    assert guard.capabilities()["window_controller"] is None


@pytest.mark.parametrize("recovers", [False, True])
def test_focus_loss_must_recover_or_interrupt_within_two_seconds(monkeypatch, recovers):
    import ctypes
    now = [10.0]
    guard = healthy_guard()
    events = []
    guard.emit = lambda *event: events.append(event)
    class Stop:
        count = 0
        def wait(self, seconds):
            now[0] += seconds
            self.count += 1
            return self.count >= 25
    class Controller:
        active = False
        def is_foreground(self):
            return self.active
        def activate(self):
            if recovers:
                self.active = True
            else:
                raise OSError("activation denied")
    guard.stopped, guard.foreground = Stop(), Controller()
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=SimpleNamespace(GetSystemMetrics=lambda _: 1)), raising=False)
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    if recovers:
        guard._watch_loop()
        assert guard.foreground.active
    else:
        with pytest.raises(RuntimeError, match="2 секунды"):
            guard._watch_loop()
        assert now[0] <= 12.3
    assert events[0][0] == "focus_lost"
