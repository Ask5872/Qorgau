"""Temporary Win32 desktop with a supervisor outside the exam process.

Existing applications stay on the original desktop. Nothing changes the registry,
user permissions, logon settings, or other applications. Ctrl+Alt+Delete remains
owned by Windows. This is desktop isolation, not an administrator-proof sandbox.

The supervisor switches only after the GUI is ready and an independent emergency
hotkey is registered. It restores the original desktop BEFORE shutting down its
own child on missing GUI heartbeats, Ctrl+Shift+Q, or errors.
"""
from __future__ import annotations

import codecs
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import uuid


def _write_json(path: Path, value: dict):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


class ChildControl:
    """Child side. Call heartbeat from the live UI, never from a blind timer."""

    def __init__(self, directory):
        self.directory = Path(directory)
        if not self.directory.is_dir():
            raise ValueError("Папка наблюдателя защищённого окна не найдена")
        self._lock = threading.Lock()
        self._ready = False
        self._sequence = 0
        self._event_sequence = 0
        self._protection = None
        try:
            self._control_id = json.loads((self.directory / "config.json").read_text(encoding="utf-8")).get("control_id")
        except (OSError, ValueError):
            self._control_id = None

    def set_protection(self, provider):
        """Publish native guard health; this does not fabricate a UI heartbeat."""
        self._protection = provider

    def activated(self):
        try:
            state = json.loads((self.directory / "activated.json").read_text(encoding="utf-8"))
            return state.get("activated") is True and (not self._control_id or state.get("control_id") == self._control_id)
        except (OSError, ValueError):
            return False

    def emit(self, kind, detail=""):
        # Native callbacks feed this through DesktopGuard's event queue, never
        # call the backend while its start transaction holds Runtime.lock.
        with self._lock:
            self._event_sequence += 1
            event = {"id": self._event_sequence, "kind": str(kind)[:80], "detail": str(detail)[:2000]}
            if self._control_id:
                event["control_id"] = self._control_id
            with (self.directory / "events.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")

    def ready(self):
        with self._lock:
            self._ready = True
            self._pulse()

    def heartbeat(self):
        with self._lock:
            self._pulse()

    def _pulse(self):
        self._sequence += 1
        state = {"ready": self._ready, "sequence": self._sequence}
        if self._control_id:
            state["control_id"] = self._control_id
        if self._protection is not None:
            try:
                state["guard"] = dict(self._protection() if callable(self._protection) else self._protection)
            except Exception as exc:
                state["guard"] = {"active": False, "error": str(exc)}
        _write_json(self.directory / "status.json", state)

    def request_exit(self, reason="Выход из защищённого окна"):
        state = {"reason": str(reason)[:500]}
        if self._control_id:
            state["control_id"] = self._control_id
        _write_json(self.directory / "exit.json", state)

    def stop_requested(self):
        return (self.directory / "shutdown.json").exists()

    def close(self):
        self.request_exit("Окно закрыто")


class _NativeProcess:
    def __init__(self, api, handle, pid):
        self.api, self.handle, self.pid = api, handle, pid

    def poll(self):
        code = self.api.wintypes.DWORD()
        if not self.api.kernel32.GetExitCodeProcess(self.handle, self.api.ctypes.byref(code)):
            raise self.api.error("GetExitCodeProcess")
        return None if code.value == 259 else code.value

    def terminate(self):
        if self.poll() is None and not self.api.kernel32.TerminateProcess(self.handle, 1):
            raise self.api.error("TerminateProcess (Qorgau child)")

    def close(self):
        if self.handle:
            self.api.kernel32.CloseHandle(self.handle)
            self.handle = None


class WindowsDesktopAPI:
    """Typed, lazy Win32 bindings: importing this module is safe on other OSes."""

    def __init__(self):
        if sys.platform != "win32":
            raise OSError("Изолированный рабочий стол доступен только в Windows")
        import ctypes
        from ctypes import wintypes
        self.ctypes, self.wintypes = ctypes, wintypes
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._emergency = threading.Event()
        self._hotkey_stop = threading.Event()
        self._hotkey_ready = threading.Event()
        self._hotkey_error = None
        self._hotkey_thread = None
        self._signatures()

    def _signatures(self):
        c, w = self.ctypes, self.wintypes
        specifications = {
            "OpenInputDesktop": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            "CreateDesktopW": ([w.LPCWSTR, w.LPCWSTR, c.c_void_p, w.DWORD, w.DWORD, c.c_void_p], w.HANDLE),
            "CloseDesktop": ([w.HANDLE], w.BOOL),
            "SwitchDesktop": ([w.HANDLE], w.BOOL),
            "SetThreadDesktop": ([w.HANDLE], w.BOOL),
            "GetThreadDesktop": ([w.DWORD], w.HANDLE),
            "RegisterHotKey": ([w.HWND, c.c_int, w.UINT, w.UINT], w.BOOL),
            "UnregisterHotKey": ([w.HWND, c.c_int], w.BOOL),
            "PeekMessageW": ([c.POINTER(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT], w.BOOL),
        }
        for name, (args, result) in specifications.items():
            fn = getattr(self.user32, name)
            fn.argtypes, fn.restype = args, result
        self.kernel32.GetCurrentThreadId.argtypes = []
        self.kernel32.GetCurrentThreadId.restype = w.DWORD
        self.kernel32.GetExitCodeProcess.argtypes = [w.HANDLE, c.POINTER(w.DWORD)]
        self.kernel32.GetExitCodeProcess.restype = w.BOOL
        self.kernel32.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
        self.kernel32.TerminateProcess.restype = w.BOOL
        self.kernel32.CloseHandle.argtypes = [w.HANDLE]
        self.kernel32.CloseHandle.restype = w.BOOL

    def error(self, action):
        return OSError(self.ctypes.get_last_error(), action + ": " + self.ctypes.FormatError(self.ctypes.get_last_error()))

    def open_original(self):
        handle = self.user32.OpenInputDesktop(0, False, 0x0100 | 0x0001 | 0x0080)
        if not handle:
            raise self.error("OpenInputDesktop")
        return handle

    def create(self, name):
        # CREATEWINDOW, READOBJECTS, WRITEOBJECTS, SWITCHDESKTOP and HOOKCONTROL.
        handle = self.user32.CreateDesktopW(name, None, None, 0, 0x018B, None)
        if not handle:
            raise self.error("CreateDesktopW")
        return handle

    def switch(self, handle):
        if not self.user32.SwitchDesktop(handle):
            raise self.error("SwitchDesktop")

    def close_desktop(self, handle):
        if handle:
            self.user32.CloseDesktop(handle)

    def start_emergency(self, desktop):
        def hotkey_worker():
            original = self.user32.GetThreadDesktop(self.kernel32.GetCurrentThreadId())
            registered = False
            try:
                if not self.user32.SetThreadDesktop(desktop):
                    raise self.error("SetThreadDesktop (аварийный выход)")
                # MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, Q. Registered in the
                # supervisor, independently of the GUI, camera, and HTTP server.
                if not self.user32.RegisterHotKey(None, 0x5147, 0x4006, 0x51):
                    raise self.error("RegisterHotKey Ctrl+Shift+Q")
                registered = True
                self._hotkey_ready.set()
                message = self.wintypes.MSG()
                while not self._hotkey_stop.wait(0.03):
                    while self.user32.PeekMessageW(self.ctypes.byref(message), None, 0, 0, 1):
                        if message.message == 0x0312 and message.wParam == 0x5147:
                            self._emergency.set()
            except Exception as exc:
                self._hotkey_error = exc
                self._emergency.set()
            finally:
                if registered:
                    self.user32.UnregisterHotKey(None, 0x5147)
                if original:
                    self.user32.SetThreadDesktop(original)
                self._hotkey_ready.set()
        self._hotkey_thread = threading.Thread(target=hotkey_worker, name="qorgau-independent-exit", daemon=True)
        self._hotkey_thread.start()
        if not self._hotkey_ready.wait(3):
            raise RuntimeError("Независимый аварийный выход не запустился; изоляция отменена")
        if self._hotkey_error:
            raise self._hotkey_error

    def emergency_requested(self):
        return self._emergency.is_set()

    def stop_emergency(self):
        self._hotkey_stop.set()
        if self._hotkey_thread:
            self._hotkey_thread.join(timeout=2)

    def spawn(self, command, cwd, desktop_name, log_path):
        import msvcrt
        c, w = self.ctypes, self.wintypes

        class STARTUPINFOW(c.Structure):
            _fields_ = [("cb", w.DWORD), ("lpReserved", w.LPWSTR), ("lpDesktop", w.LPWSTR),
                        ("lpTitle", w.LPWSTR), ("dwX", w.DWORD), ("dwY", w.DWORD),
                        ("dwXSize", w.DWORD), ("dwYSize", w.DWORD), ("dwXCountChars", w.DWORD),
                        ("dwYCountChars", w.DWORD), ("dwFillAttribute", w.DWORD), ("dwFlags", w.DWORD),
                        ("wShowWindow", w.WORD), ("cbReserved2", w.WORD), ("lpReserved2", c.c_void_p),
                        ("hStdInput", w.HANDLE), ("hStdOutput", w.HANDLE), ("hStdError", w.HANDLE)]

        class PROCESS_INFORMATION(c.Structure):
            _fields_ = [("hProcess", w.HANDLE), ("hThread", w.HANDLE),
                        ("dwProcessId", w.DWORD), ("dwThreadId", w.DWORD)]

        create_process = self.kernel32.CreateProcessW
        create_process.argtypes = [w.LPCWSTR, w.LPWSTR, c.c_void_p, c.c_void_p, w.BOOL, w.DWORD,
                                  c.c_void_p, w.LPCWSTR, c.POINTER(STARTUPINFOW), c.POINTER(PROCESS_INFORMATION)]
        create_process.restype = w.BOOL
        startup, process = STARTUPINFOW(), PROCESS_INFORMATION()
        startup.cb = c.sizeof(startup)
        startup.lpDesktop = desktop_name
        startup.dwFlags = 0x100  # STARTF_USESTDHANDLES; child has no console on exam desktop.
        environment = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        environment_block = c.create_unicode_buffer("\0".join(f"{k}={v}" for k, v in sorted(environment.items(), key=lambda p: p[0].upper())) + "\0")
        line = c.create_unicode_buffer(subprocess.list2cmdline([str(x) for x in command]))
        with open(log_path, "ab", buffering=0) as log, open(os.devnull, "rb") as null:
            out_handle, in_handle = msvcrt.get_osfhandle(log.fileno()), msvcrt.get_osfhandle(null.fileno())
            os.set_handle_inheritable(out_handle, True)
            os.set_handle_inheritable(in_handle, True)
            startup.hStdOutput = startup.hStdError = out_handle
            startup.hStdInput = in_handle
            if not create_process(str(command[0]), line, None, None, True, 0x08000400,
                                  environment_block, str(cwd), c.byref(startup), c.byref(process)):
                raise self.error("CreateProcessW (Qorgau)")
        self.kernel32.CloseHandle(process.hThread)
        return _NativeProcess(self, process.hProcess, process.dwProcessId)


class _ChildLog:
    def __init__(self, path, output):
        self.path, self.output, self.position = path, output, 0
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")

    def drain(self):
        try:
            with self.path.open("rb") as handle:
                handle.seek(self.position)
                chunk = handle.read(65536)
                self.position = handle.tell()
        except OSError:
            return
        if chunk:
            text = self.decoder.decode(chunk)
            if text:
                self.output(text)


def supervise(command, cwd, directory, api, *, startup_timeout=45.0, heartbeat_timeout=15.0,
              shutdown_grace=4.0, clock=time.monotonic, wait=time.sleep,
              output=lambda text: print(text, end="", flush=True)):
    """Testable lifecycle. API adapter is the only layer that changes desktops."""
    directory = Path(directory)
    try:
        control_id = json.loads((directory / "config.json").read_text(encoding="utf-8")).get("control_id")
    except (OSError, ValueError):
        control_id = None
    original = desktop = child = None
    activated = False
    reason = "Окно закрыто"
    returncode = 1
    log = _ChildLog(directory / "child.log", output)
    try:
        original = api.open_original()
        name = "Qorgau_" + uuid.uuid4().hex
        desktop = api.create(name)
        api.start_emergency(desktop)
        child = api.spawn([*command, "--isolation-control", str(directory)], Path(cwd), name, directory / "child.log")
        started = last_pulse = clock()
        previous_sequence = None
        while True:
            log.drain()
            code = child.poll()
            if code is not None:
                returncode = code
                break
            if api.emergency_requested():
                reason, returncode = "Аварийный выход Ctrl+Shift+Q", 0
                break
            if (directory / "exit.json").exists():
                reason, returncode = "Запрошен выход из защищённого окна", 0
                break
            try:
                state = json.loads((directory / "status.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                state = {}
            if control_id and state.get("control_id") != control_id:
                state = {}
            sequence = state.get("sequence")
            if isinstance(sequence, int) and sequence != previous_sequence:
                previous_sequence, last_pulse = sequence, clock()
            if not activated and state.get("ready") is True:
                if control_id:
                    guard = state.get("guard", {})
                    if (not all(guard.get(key) is True for key in ("active", "keyboard_hook", "navigation_guard")) or
                            guard.get("window_controller") != "pyautogui"):
                        reason = "Защита окна не готова; переключение рабочего стола отменено"
                        break
                api.switch(desktop)
                activated = True
                _write_json(directory / "activated.json", {"activated": True, "control_id": control_id})
            if not activated and clock() - started > startup_timeout:
                reason = "Защищённое окно не запустилось вовремя; рабочий стол не изменён"
                break
            if activated and clock() - last_pulse > heartbeat_timeout:
                reason = "Окно Qorgau перестало отвечать; выполнен аварийный выход"
                break
            wait(0.15)
    except KeyboardInterrupt:
        reason, returncode = "Запуск остановлен пользователем", 0
    except Exception as exc:
        reason = "Не удалось запустить защищённое окно: " + str(exc)
    finally:
        # Safety ordering is deliberate: restore the user's desktop even if the
        # child's camera/HTTP/GUI code can no longer clean up itself.
        if original:
            try:
                api.switch(original)
                _write_json(directory / "restored.json", {"restored": True, "control_id": control_id, "reason": reason})
            except Exception as exc:
                output("\nНе удалось вернуть рабочий стол: " + str(exc) +
                       ". Используйте Ctrl+Alt+Delete → выход из учётной записи.\n")
                returncode = 1
        if child:
            try:
                _write_json(directory / "shutdown.json", {"reason": reason})
                deadline = clock() + shutdown_grace
                while child.poll() is None and clock() < deadline:
                    log.drain()
                    wait(0.1)
                if child.poll() is None:
                    child.terminate()  # The owned Qorgau child only; never third-party apps.
                    # TerminateProcess is asynchronous. Let Windows release the
                    # child's desktop/log handles before closing our handles.
                    deadline = clock() + 2
                    while child.poll() is None and clock() < deadline:
                        wait(0.05)
                log.drain()
            except Exception as exc:
                output("\nЗавершение дочернего Qorgau: " + str(exc) + "\n")
            finally:
                child.close()
        api.stop_emergency()
        api.close_desktop(desktop)
        api.close_desktop(original)
        output("\n" + reason + "\n")
    return returncode


def run_isolated(command: list[str], cwd: Path) -> int:
    if sys.platform != "win32":
        raise OSError("Защищённый рабочий стол доступен только в Windows")
    api = WindowsDesktopAPI()
    # Random, per-run control directory; no permanent restriction files.
    with tempfile.TemporaryDirectory(prefix="qorgau-desktop-", ignore_cleanup_errors=True) as directory:
        return supervise(command, cwd, directory, api)


def run_supervisor(root: Path, directory: Path) -> int:
    """An independent process supervises only one temporary exam window."""
    root, directory = Path(root).resolve(), Path(directory).resolve()
    config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
    if not config.get("control_id") or not config.get("url"):
        raise ValueError("Не найдены параметры запуска защищённого окна")
    return supervise([sys.executable, str(root / "run.py"), "--exam-window"], root, directory, WindowsDesktopAPI())
