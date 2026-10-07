"""Start Windows isolation only for an individual exam attempt.

The normal hub owns the backend and camera. Each attempt gets an independent
supervisor process and a GUI-only child. No desktop or keyboard changes happen
when this controller is constructed or queried from a teacher/preparation page.
"""
from __future__ import annotations

import json
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

from .desktop_session import WindowsDesktopAPI, _write_json


class AttemptDesktopGuard:
    def __init__(self, emit, emergency, root, url, csrf, *, process_factory=None,
                 api_factory=None, startup_timeout=50.0, restore_timeout=8.0,
                 clock=time.monotonic, wait=time.sleep):
        self.emit, self.emergency = emit, emergency
        self.root, self.url, self.csrf = Path(root), url, csrf
        self.window = None
        self.calibration_display = None
        self.active = False
        self.error = ""
        self._process_factory = process_factory or subprocess.Popen
        self._api_factory = api_factory or WindowsDesktopAPI
        self._startup_timeout, self._restore_timeout = startup_timeout, restore_timeout
        self._clock, self._wait = clock, wait
        self._directory = None
        self._temporary = None
        self._control_id = None
        self._process = None
        self._api = None
        self._original = None
        self._starting = False
        self._stopping = False
        self._host_hidden = False
        self._monitor_stop = threading.Event()
        self._monitor_thread = None
        self._event_id = 0
        self._event_offset = 0
        self._lock = threading.RLock()
        self._leave_lock = threading.Lock()

    def _read(self, name):
        directory, identity = self._directory, self._control_id
        if directory is None:
            return {}
        try:
            state = json.loads((directory / name).read_text(encoding="utf-8"))
            return state if state.get("control_id") == identity else {}
        except (OSError, ValueError, AttributeError):
            return {}

    @staticmethod
    def _healthy(guard):
        return all(guard.get(key) is True for key in ("active", "keyboard_hook", "navigation_guard")) and guard.get("window_controller") == "pyautogui"

    def capabilities(self):
        supported = sys.platform == "win32" and self.window is not None
        state = self._read("status.json") if self.active else {}
        guard = state.get("guard", {})
        alive = self._process is not None and self._process.poll() is None
        active = bool(self.active and alive and self._healthy(guard))
        return {"desktop": self.window is not None, "platform": sys.platform,
                "supported": supported, "isolation_available": supported,
                "desktop_isolated": bool(active and self._read("activated.json").get("activated")),
                "active": active, "keyboard_hook": active and bool(guard.get("keyboard_hook")),
                "navigation_guard": active and bool(guard.get("navigation_guard")),
                "window_controller": guard.get("window_controller") if active else None,
                "starting": self._starting, "error": self.error or guard.get("error", "")}

    def enter(self):
        if self._process is not None and self._process.poll() is not None and not self.active and not self._starting:
            self.leave()  # Reap a previous supervisor that finished slowly.
        with self._lock:
            if (self.active or self._starting or self._stopping or self._process is not None or
                    self._monitor_thread is not None and self._monitor_thread.is_alive()):
                raise RuntimeError("Предыдущая защита ещё завершается. Повторите запуск через несколько секунд.")
            if not self.capabilities()["supported"]:
                raise RuntimeError("Для защищённого теста нужно окно приложения в Windows.")
            self._starting = True
            self.error = ""
            self._monitor_stop = threading.Event()
            self._event_id = 0
            self._event_offset = 0
        try:
            # Retain a fallback restoration handle in the hub as well. The
            # independent supervisor remains the primary recovery mechanism.
            self._api = self._api_factory()
            self._original = self._api.open_original()
            self._temporary = tempfile.TemporaryDirectory(prefix="qorgau-attempt-", ignore_cleanup_errors=True)
            self._directory = Path(self._temporary.name)
            self._control_id = secrets.token_urlsafe(32)
            config = {"url": self.url, "csrf": self.csrf, "control_id": self._control_id}
            if self.calibration_display is not None:
                config["calibration_display"] = self.calibration_display
            _write_json(self._directory / "config.json", config)
            command = [sys.executable, str(self.root / "run.py"), "--exam-supervisor",
                       "--isolation-control", str(self._directory), "--port", str(urlsplit(self.url).port or 8765)]
            with (self._directory / "supervisor.log").open("ab", buffering=0) as output:
                self._process = self._process_factory(command, cwd=str(self.root),
                    stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                    creationflags=0x08000000 if sys.platform == "win32" else 0)
            deadline = self._clock() + self._startup_timeout
            while self._clock() < deadline:
                if self._stopping:
                    raise RuntimeError("Запуск экзамена отменён.")
                exit_request = self._read("exit.json")
                if exit_request:
                    raise RuntimeError(str(exit_request.get("reason") or "Запуск экзамена отменён.")[:500])
                if self._process.poll() is not None:
                    raise RuntimeError("Защищённое окно не запустилось. Рабочий стол восстановлен.")
                state = self._read("status.json")
                if self._read("restored.json").get("restored"):
                    raise RuntimeError("Защищённое окно завершилось до начала теста.")
                if state.get("ready") is True and self._read("activated.json").get("activated") is True:
                    if not self._healthy(state.get("guard", {})):
                        raise RuntimeError(state.get("guard", {}).get("error") or "Защита окна не готова.")
                    self.active = True
                    self._starting = False
                    if self.window is not None:
                        try:
                            self.window.hide()
                            self._host_hidden = True
                        except Exception:
                            pass  # The hub is already on the other desktop.
                    self._monitor_thread = threading.Thread(target=self._monitor,
                        kwargs={"identity": self._control_id, "stop": self._monitor_stop},
                        name="qorgau-attempt-monitor", daemon=True)
                    self._monitor_thread.start()
                    return
                self._wait(0.05)
            raise RuntimeError("Защищённое окно не запустилось вовремя. Повторите начало теста.")
        except Exception as exc:
            self.error = str(exc)
            self.leave()
            raise
        finally:
            self._starting = False

    def request_exit(self, reason="Экзамен завершён"):
        # This is deliberately nonblocking and does not need Runtime.lock.
        directory, identity = self._directory, self._control_id
        if directory is not None:
            try:
                _write_json(directory / "exit.json", {"reason": str(reason)[:500], "control_id": identity})
            except OSError:
                pass  # leave() retains the original-desktop recovery handle.

    def _restore(self):
        if self._read("restored.json").get("restored") is True:
            return True
        if self._api is None or self._original is None:
            return self._process is None
        try:
            self._api.switch(self._original)
            if self._directory:
                _write_json(self._directory / "restored.json", {
                    "restored": True, "control_id": self._control_id, "fallback": True})
            return True
        except Exception as exc:
            self.error = "Не удалось вернуть рабочий стол: " + str(exc)
            return False

    def leave(self):
        if not self._read("exit.json"):
            self.request_exit()
        with self._leave_lock:
            self._stopping = True
            self.active = False
            self._monitor_stop.set()
            try:
                deadline = self._clock() + self._restore_timeout
                while self._process is not None and self._process.poll() is None:
                    if self._read("restored.json").get("restored") is True:
                        break
                    if self._clock() >= deadline:
                        break
                    self._wait(0.05)
                if not self._restore():
                    raise RuntimeError(self.error)
                if self._host_hidden and self.window is not None:
                    try:
                        self.window.show()
                    except Exception:
                        pass
                    self._host_hidden = False
                monitor = self._monitor_thread
                if monitor and monitor is not threading.current_thread():
                    monitor.join(timeout=2)
                # Never kill the supervisor before it restores the desktop.
                # It owns child shutdown and gets time to finish that cleanup.
                deadline = self._clock() + 7
                while self._process is not None and self._process.poll() is None and self._clock() < deadline:
                    self._wait(0.05)
                if self._process is not None and self._process.poll() is None:
                    self.error = "Рабочий стол восстановлен. Предыдущее окно ещё завершается."
                    return
                self._process = None
                if self._api is not None and self._original is not None:
                    self._api.close_desktop(self._original)
                self._api, self._original = None, None
                self._directory, self._control_id = None, None
                temporary, self._temporary = self._temporary, None
                if temporary:
                    temporary.cleanup()
            finally:
                self._stopping = self._process is not None

    def _events(self, identity=None):
        identity = identity or self._control_id
        directory = self._directory
        if directory is None or identity != self._control_id:
            return
        try:
            with (directory / "events.jsonl").open("rb") as stream:
                stream.seek(self._event_offset)
                lines = stream.read(65536).splitlines(keepends=True)
        except OSError:
            return
        for line in lines:
            if identity != self._control_id:
                return
            if not line.endswith(b"\n"):
                break  # A concurrent append may not have finished this line.
            self._event_offset += len(line)
            try:
                event = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            event_id = event.get("id")
            if event.get("control_id") != identity or not isinstance(event_id, int) or event_id <= self._event_id:
                continue
            self._event_id = event_id
            if event.get("kind") in {"shortcut", "focus_lost", "navigation", "guard_lost", "monitor", "emergency_exit"}:
                self.emit(event["kind"], str(event.get("detail", ""))[:2000])

    def _monitor(self, identity=None, stop=None):
        identity, stop = identity or self._control_id, stop or self._monitor_stop
        while not stop.wait(0.15):
            try:
                if identity != self._control_id or self._stopping or not self.active:
                    return
                self._events(identity)
                if identity != self._control_id or stop.is_set():
                    return
                state = self._read("status.json")
                if (self._process is None or self._process.poll() is not None or
                        self._read("restored.json").get("restored") or
                        not self._healthy(state.get("guard", {}))):
                    self.error = "Защищённое окно завершилось или потеряло защиту."
                    self.request_exit(self.error)
                    self.emergency()
                    return
            except Exception as exc:
                if identity != self._control_id or stop.is_set():
                    return
                self.error = "Сбой связи с защищённым окном: " + str(exc)
                self.request_exit(self.error)
                try:
                    self.emergency()
                finally:
                    return
