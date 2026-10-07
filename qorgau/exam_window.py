"""One isolated GUI per attempt, connected to the existing local HTTP server.

There is deliberately no server or camera in this process. The ordinary app
owns both. Its supervisor restores the user's desktop before asking this child
to stop; live UI pulses, rather than a Python timer, establish responsiveness.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import threading
import time
from urllib.parse import urlsplit, urlunsplit

from .desktop_session import ChildControl
from .guard import DesktopGuard


def _exam_url(directory):
    config = json.loads((Path(directory) / "config.json").read_text(encoding="utf-8"))
    url = config.get("url")
    if not isinstance(url, str) or any(ord(c) < 32 for c in url) or "\\" in url:
        raise ValueError("Не указан локальный адрес окна экзамена")
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
            or parsed.port is None):
        raise ValueError("Окно экзамена требует адрес локального сервера Qorgau")
    return urlunsplit((parsed.scheme, parsed.netloc, "/", "exam=1", "exam"))


class _Bridge:
    """The only Python method available to JavaScript in the exam window."""

    def __init__(self, lifecycle):
        self._lifecycle = lifecycle

    def exam_heartbeat(self):
        return self._lifecycle.ui_heartbeat()


class ExamWindowLifecycle:
    """Testable startup/teardown ordering without Win32 or an HTTP dependency."""

    def __init__(self, control, *, startup_timeout=25.0, clock=time.monotonic,
                 wait=time.sleep, calibration_display=None):
        self.control = control
        self.calibration_display = calibration_display
        self.startup_timeout, self.clock, self.wait = startup_timeout, clock, wait
        self.first_ui = threading.Event()
        self.activated = threading.Event()
        self.closed = threading.Event()
        self.exit_requested = threading.Event()
        self._exit_lock = threading.Lock()
        self._start_lock = threading.Lock()
        self.worker = None
        self.window = self.navigation = None
        self.guard = DesktopGuard(self.emit, self.emergency, activation_event=self.activated)
        self.guard.isolated = True
        self.control.set_protection(self.guard.capabilities)
        self.destroying = False

    def emit(self, kind, detail):
        self.control.emit(kind, detail)
        if kind == "guard_lost":
            self.request_exit(str(detail))

    def request_exit(self, reason):
        with self._exit_lock:
            if self.exit_requested.is_set():
                return
            # Publish before allowing any teardown; the supervisor owns the
            # original desktop and can restore it even if the UI is stalled.
            self.control.request_exit(reason)
            self.exit_requested.set()

    def emergency(self):
        self.request_exit("Аварийный выход из экзамена Ctrl+Shift+Q")

    def ui_heartbeat(self):
        if self.closed.is_set() or self.exit_requested.is_set():
            return False
        self.control.heartbeat()
        self.first_ui.set()
        return True

    def closing(self):
        if self.destroying:
            return True
        self.request_exit("Окно экзамена закрыто пользователем")
        # Wait until the independent supervisor has restored the desktop.
        return False

    def shown(self):
        with self._start_lock:
            if self.worker is not None:
                return
            self.worker = threading.Thread(target=self.run, name="qorgau-exam-window", daemon=True)
            self.worker.start()

    def _await_ui(self):
        deadline = self.clock() + self.startup_timeout
        while not self.closed.is_set():
            if self.control.stop_requested() or self.exit_requested.is_set():
                return False
            if self.navigation.ready.is_set() and self.first_ui.is_set():
                return True
            if self.navigation.error and not self.navigation.ready.is_set():
                raise RuntimeError(self.navigation.error)
            if self.clock() >= deadline:
                raise RuntimeError("Интерфейс экзамена не запустился вовремя")
            self.wait(0.05)
        return False

    def _release_window(self):
        try:
            self.guard.leave()
        finally:
            try:
                if self.navigation is not None:
                    self.navigation.refresh()
            finally:
                if self.window is not None and not self.closed.is_set():
                    self.destroying = True
                    self.window.destroy()

    def run(self):
        try:
            if self._await_ui():
                if self.calibration_display is not None:
                    from .calibration_display import restore_calibration_display
                    restore_calibration_display(self.window, self.calibration_display)
                self.guard.enter()
                if self.calibration_display is not None:
                    from .calibration_display import verify_calibration_display
                    verify_calibration_display(self.window, self.calibration_display)
                if not self.navigation.refresh():
                    raise RuntimeError(self.navigation.error or "Защита навигации не включилась")
                self.guard._check_health()
                if self.exit_requested.is_set() or self.control.stop_requested():
                    raise RuntimeError("Запуск экзамена отменён")
                # No parent HTTP request is allowed here: start holds the
                # runtime lock until this native readiness handshake completes.
                self.control.ready()
        except Exception as exc:
            self.request_exit("Не удалось открыть защищённый экзамен: " + str(exc))
        # Heartbeats continue only through the bridge after successful UI
        # refreshes. Polling control files must never manufacture a UI pulse.
        while not self.closed.is_set():
            if self.control.stop_requested():
                self._release_window()
                return
            if self.control.activated():
                self.activated.set()
            self.wait(0.05)

    def cleanup(self):
        try:
            self.request_exit("Окно экзамена завершено")
        finally:
            self.closed.set()
            if self.worker and self.worker is not threading.current_thread():
                self.worker.join(timeout=2)
            try:
                self.guard.leave()
            finally:
                try:
                    if self.navigation is not None:
                        self.navigation.cleanup()
                finally:
                    self.control.close()


def run_exam_window(directory):
    """Called by run.py --exam-window; creates no additional HTTP listener."""
    control = ChildControl(directory)
    lifecycle = ExamWindowLifecycle(control)
    try:
        if sys.platform != "win32":
            raise OSError("Защищённое окно экзамена доступно только в Windows")
        import webview
        from .navigation import install_navigation_guard
        from .window_layout import normal_window_options, fit_native_window

        url = _exam_url(directory)
        config = json.loads((Path(directory) / "config.json").read_text(encoding="utf-8"))
        lifecycle.calibration_display = config.get("calibration_display")
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
        webview.settings["ALLOW_DOWNLOADS"] = True
        window = webview.create_window(
            "Qorgau — экзамен", url, background_color="#ffffff", text_select=True,
            js_api=_Bridge(lifecycle), **normal_window_options())
        lifecycle.window = lifecycle.guard.window = window
        lifecycle.navigation = install_navigation_guard(
            window, url, lambda: lifecycle.guard.active, lifecycle.emit)
        lifecycle.guard.navigation_guard = lifecycle.navigation.ready
        def shown():
            fit_native_window(window, free=False)
            lifecycle.shown()
        window.events.shown += shown
        window.events.closing += lifecycle.closing
        webview.start(debug=False, private_mode=True, gui="edgechromium")
        return 0
    except Exception as exc:
        lifecycle.request_exit("Защищённое окно не запустилось: " + str(exc))
        print("Не удалось открыть окно экзамена: " + str(exc), flush=True)
        return 1
    finally:
        lifecycle.cleanup()
