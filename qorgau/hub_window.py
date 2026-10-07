"""Ordinary-window actions. Exam protection belongs to another process."""
import threading
import time
import sys


class HubWindowBridge:
    """Window controls and a voluntary, temporary calibration fullscreen.

    Calibration never grabs focus, blocks keys or marks the window topmost.
    A renewable lease restores the ordinary frame if the page stops responding.
    """

    def __init__(self, runtime):
        self._runtime = runtime
        self._window = None
        self._calibration_lock = threading.RLock()
        self._calibration_timer = None
        self._calibration_until = 0.0
        self._calibration_fullscreen = False

    def _bind(self, window):
        self._window = window

    def _available(self):
        if self._window is None:
            return {"ok": False, "message": "Окно ещё открывается"}
        if self._runtime.active_id or self._runtime.cleanup_in_progress:
            return {"ok": False, "message": "Сначала завершите текущий тест"}
        return None

    def hub_minimize(self):
        refused = self._available()
        if refused:
            return refused
        self.hub_calibration_end()
        self._window.minimize()
        return {"ok": True}

    def hub_close(self):
        refused = self._available()
        if refused:
            return refused
        # The native closing handler rechecks state on the UI thread.
        self._window.destroy()
        return {"ok": True}

    def _schedule_expiry(self):
        if self._calibration_timer is not None:
            self._calibration_timer.cancel()
        self._calibration_timer = threading.Timer(10.0, self._expire_calibration)
        self._calibration_timer.daemon = True
        self._calibration_timer.start()

    def _expire_calibration(self):
        with self._calibration_lock:
            if not self._calibration_fullscreen:
                return
            if time.monotonic() < self._calibration_until:
                self._schedule_expiry()
                return
            self.hub_calibration_end()

    def hub_calibration_begin(self):
        refused = self._available()
        if refused:
            return refused
        with self._calibration_lock:
            if not self._calibration_fullscreen:
                try:
                    if sys.platform == "win32":
                        from .calibration_display import capture_calibration_display
                        # Retain physical monitor identity, not just CSS size:
                        # equal-resolution monitors are not interchangeable.
                        display = capture_calibration_display(self._window)
                        self._runtime.guard.calibration_display = display
                    self._window.toggle_fullscreen()
                except Exception as exc:
                    return {"ok": False, "message": "Не удалось развернуть калибровку на весь экран: " + str(exc)[:250]}
                self._calibration_fullscreen = True
            self._calibration_until = time.monotonic() + 10.0
            self._schedule_expiry()
        return {"ok": True}

    def hub_calibration_ping(self):
        with self._calibration_lock:
            if not self._calibration_fullscreen:
                return {"ok": False, "message": "Полноэкранная калибровка завершена"}
            refused = self._available()
            if refused:
                self.hub_calibration_end()
                return refused
            self._calibration_until = time.monotonic() + 10.0
        return {"ok": True}

    def hub_calibration_end(self):
        # This releases only the ordinary hub, never the isolated exam window.
        with self._calibration_lock:
            if self._calibration_timer is not None:
                self._calibration_timer.cancel()
                self._calibration_timer = None
            if self._calibration_fullscreen:
                try:
                    self._window.toggle_fullscreen()
                except Exception:
                    # Keep ownership so a subsequent end can retry restoration.
                    self._schedule_expiry()
                    return {"ok": False, "message": "Не удалось вернуть обычное окно. Повторите выход из калибровки"}
                self._calibration_fullscreen = False
            self._calibration_until = 0.0
        return {"ok": True}

    def _dispose(self):
        with self._calibration_lock:
            if self._calibration_timer is not None:
                self._calibration_timer.cancel()
                self._calibration_timer = None
            self._calibration_fullscreen = False
