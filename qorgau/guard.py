"""Temporary Windows kiosk guard. No registry edits or process termination."""
import os
import queue
import sys
import threading
import time


def blocked_shortcut(vk, pressed):
    ctrl = bool(pressed & {0x11, 0xA2, 0xA3})
    alt = bool(pressed & {0x12, 0xA4, 0xA5})
    shift = bool(pressed & {0x10, 0xA0, 0xA1})
    if ctrl and shift and vk == 0x51:
        return "EMERGENCY"
    # Windows owns the secure-attention sequence; never interfere with it.
    if ctrl and alt and vk == 0x2E: return None
    if vk in {0x5B, 0x5C} or pressed & {0x5B, 0x5C}: return "Win"
    if vk == 0x2C: return "PrintScreen"
    if ctrl and shift and vk in {0x49, 0x4A, 0x43}: return "DevTools"
    if alt and vk in {0x09, 0x1B, 0x73, 0x20}: return "Alt+Tab / Alt+Esc / Alt+F4"
    if alt and vk in {0x25, 0x27, 0x44}: return "Alt+Left / Alt+Right / Alt+D"
    if ctrl and vk in {0x21, 0x22}: return "Ctrl+PageUp / Ctrl+PageDown"
    if ctrl and vk in range(0x31, 0x3A): return "Ctrl+номер вкладки"
    if (ctrl or shift) and vk == 0x2D: return "Ctrl+Insert / Shift+Insert"
    if shift and vk == 0x2E: return "Shift+Delete"
    if ctrl and vk in {0x43, 0x56, 0x58, 0x50, 0x52, 0x53, 0x55, 0x4C, 0x4E, 0x54, 0x57, 0x09, 0x1B}: return "Ctrl+комбинация"
    if shift and vk == 0x79: return "Shift+F10"
    if vk == 0x5D: return "ContextMenu"
    if vk in {0xA6, 0xA7, 0xA8, 0xAC}: return "Browser navigation"
    if vk == 0x74: return "F5"
    if vk in {0x7A, 0x7B}: return "F11 / F12"
    return None


def _handle_value(handle):
    """Accept Python, ctypes, and pythonnet IntPtr window handles."""
    if hasattr(handle, "ToInt64"):
        return int(handle.ToInt64())
    return int(getattr(handle, "value", handle) or 0)


class ForegroundController:
    """PyAutoGUI window observation/activation, scoped to our exact HWND.

    PyAutoGUI exposes the Windows PyGetWindow adapter. Names alone are not an
    identity: another process can use the same title. We verify both the native
    pywebview handle and owning process before caching its window object.
    No mouse moves, generated keystrokes, or changes to PyAutoGUI FAILSAFE.
    """
    def __init__(self, window, automation, owner_of, current_pid):
        self.automation = automation
        native = getattr(window, "native", None)
        self.hwnd = _handle_value(getattr(native, "Handle", 0))
        if not self.hwnd or owner_of(self.hwnd) != current_pid:
            raise RuntimeError("Не удалось подтвердить системное окно Qorgau")
        # Bind the existing native window directly. PyGetWindow 0.0.9's title
        # enumeration uses a 32-bit callback handle; avoid it on 64-bit Windows.
        self.target = automation.Window(self.hwnd)
        if _handle_value(getattr(self.target, "_hWnd", 0)) != self.hwnd:
            raise RuntimeError("PyAutoGUI не подтвердил окно Qorgau; защита фокуса недоступна")
        self.owner_of, self.current_pid = owner_of, current_pid

    def is_foreground(self):
        if self.owner_of(self.hwnd) != self.current_pid:
            raise RuntimeError("Системное окно экзамена закрыто или заменено")
        current = self.automation.getActiveWindow()
        return current is not None and _handle_value(getattr(current, "_hWnd", 0)) == self.hwnd

    def activate(self):
        # Revalidate before acting, including the unlikely HWND-reuse case.
        if self.owner_of(self.hwnd) != self.current_pid:
            raise RuntimeError("Нельзя вернуть фокус: окно Qorgau больше не существует")
        self.target.activate()


def _foreground_controller(window):
    import ctypes
    from ctypes import wintypes
    try:
        import pyautogui
    except Exception as exc:
        raise RuntimeError("Контроль окон PyAutoGUI не загружен. Повторите 01_install.cmd: " + str(exc)) from exc
    user32 = ctypes.windll.user32
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    # PyGetWindow calls the shared ctypes WinDLL functions, so retain pointer-
    # sized HWNDs for its active-window lookup and activation on 64-bit Windows.
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    # PyGetWindow constructs a rectangle for each Window returned by its API.
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.c_void_p]
    user32.GetWindowRect.restype = wintypes.BOOL
    def owner_of(hwnd):
        pid = wintypes.DWORD()
        return pid.value if user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) else 0
    return ForegroundController(window, pyautogui, owner_of, os.getpid())


class DesktopGuard:
    def __init__(self, emit, emergency, *, activation_event=None):
        self.emit, self.emergency = emit, emergency
        self.window = None
        self.isolated = False
        self.navigation_guard = False
        self.active = False
        self.hook_ok = False
        self.error = ""
        self.handle = None
        self.thread_id = None
        self.ready = threading.Event()
        self.stopped = threading.Event()
        self.events = queue.SimpleQueue()
        self.hook_thread = None
        self.watch_thread = None
        self.foreground = None
        self._fullscreen_entered = False
        # A per-attempt window is armed on a non-input desktop first. Its
        # supervisor acknowledges SwitchDesktop before focus may be enforced.
        # Ordinary callers need no separate activation handshake.
        self.activation_event = activation_event

    def capabilities(self):
        navigation = self.navigation_guard
        if hasattr(navigation, "is_set"):
            navigation = navigation.is_set()
        return {"desktop": self.window is not None, "platform": sys.platform,
                "desktop_isolated": self.isolated,
                "navigation_guard": bool(navigation),
                "window_controller": "pyautogui" if self.foreground is not None else None,
                "supported": sys.platform == "win32" and self.window is not None,
                "active": self.active, "keyboard_hook": self.hook_ok, "error": self.error}

    def enter(self):
        if self.window is None or self.active:
            return
        if any(thread and thread.is_alive() for thread in (self.hook_thread, self.watch_thread)):
            raise RuntimeError("Предыдущая защита окна ещё завершается. Перезапустите Qorgau.")
        self.active = True
        self.error = ""
        try:
            self.window.toggle_fullscreen()
            self._fullscreen_entered = True
            self.window.on_top = True
            if sys.platform == "win32":
                self.hook_ok = False
                self.ready.clear()
                self.stopped.clear()
                self.events = queue.SimpleQueue()
                self.hook_thread = threading.Thread(target=self._hook, daemon=True, name="qorgau-keyboard")
                self.hook_thread.start()
                ready = self.ready.wait(2)
                if not ready or not self.hook_ok:
                    raise RuntimeError("Не удалось включить блокировку клавиш. Экзамен в защищённом режиме не запущен.")
                self.foreground = _foreground_controller(self.window)
                self.watch_thread = threading.Thread(target=self._watch, daemon=True, name="qorgau-window")
                self.watch_thread.start()
        except Exception as exc:
            self.error = str(exc)
            self.leave()
            raise

    def leave(self):
        was_active = self.active
        self.active = False
        self.stopped.set()
        if sys.platform == "win32" and self.thread_id:
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, 0x12, 0, 0)
        for thread in (self.hook_thread, self.watch_thread):
            if thread and thread is not threading.current_thread():
                thread.join(timeout=2)
        self.foreground = None
        if self.window and was_active:
            try:
                self.window.on_top = False
                if self._fullscreen_entered:
                    self.window.toggle_fullscreen()
            except Exception:
                pass
            self._fullscreen_entered = False

    def _hook(self):
        import ctypes
        from ctypes import wintypes
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        LRESULT = ctypes.c_ssize_t
        HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                        ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = wintypes.HANDLE
        user32.CallNextHookEx.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = LRESULT
        user32.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
        user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user32.GetAsyncKeyState.restype = ctypes.c_short
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        user32.GetMessageW.restype = ctypes.c_int
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        # Include modifiers held down before the hook was installed.
        pressed = {vk for vk in (0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5)
                   if user32.GetAsyncKeyState(vk) & 0x8000}
        swallowed = set()
        def callback(code, wp, lp):
            if code < 0 or not self.active:
                return user32.CallNextHookEx(self.handle, code, wp, lp)
            vk = ctypes.cast(lp, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents.vkCode
            if wp in {0x100, 0x104}:
                already_down = vk in pressed
                pressed.add(vk)
                action = blocked_shortcut(vk, pressed)
                if action:
                    if not already_down:
                        self.events.put(action)
                    if action == "EMERGENCY":
                        # Let the supervisor's independently registered hotkey
                        # receive this too, even when the app is hung.
                        return user32.CallNextHookEx(self.handle, code, wp, lp)
                    swallowed.add(vk)
                    return 1
            elif wp in {0x101, 0x105}:
                pressed.discard(vk)
                # Generic VK_CONTROL/VK_SHIFT can be sampled at installation
                # whereas actual hook messages use left/right virtual keys.
                if vk in {0xA0, 0xA1}: pressed.discard(0x10)
                if vk in {0xA2, 0xA3}: pressed.discard(0x11)
                if vk in {0xA4, 0xA5}: pressed.discard(0x12)
                if vk in swallowed:
                    swallowed.discard(vk)
                    return 1
            return user32.CallNextHookEx(self.handle, code, wp, lp)
        proc = HOOKPROC(callback)
        self.thread_id = kernel32.GetCurrentThreadId()
        # Ensure the thread message queue exists before allowing leave().
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, 0)
        try:
            self.handle = user32.SetWindowsHookExW(13, proc, kernel32.GetModuleHandleW(None), 0)
            self.hook_ok = bool(self.handle)
            if not self.handle:
                self.error = "Не удалось установить блокировку клавиш Windows"
            self.ready.set()
            if self.handle:
                while self.active and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            if self.handle:
                user32.UnhookWindowsHookEx(self.handle)
            self.handle = None
            self.thread_id = None
            self.hook_ok = False
            self.ready.set()

    def _watch(self):
        try:
            self._watch_loop()
        except Exception as exc:
            self.error = "Сбой контроля окна: " + str(exc)
            try:
                self.emit("guard_lost", self.error)
            except Exception:
                pass
            try:
                self.emergency()
            finally:
                self.leave()

    def _check_health(self):
        if not self.hook_ok or not self.hook_thread or not self.hook_thread.is_alive():
            raise RuntimeError("Блокировка клавиш перестала работать; экзамен остановлен")
        if self.isolated and not self.capabilities()["navigation_guard"]:
            raise RuntimeError("Защита навигации WebView2 потеряна; экзамен остановлен")

    def _watch_loop(self):
        import ctypes
        user32 = ctypes.windll.user32
        foreground = self.foreground
        if foreground is None:
            raise RuntimeError("Контроль фокуса PyAutoGUI не готов")
        last_focus = 0
        focus_lost_since = None
        if user32.GetSystemMetrics(80) > 1:
            self.emit("monitor", "Второй монитор обнаружен. Прототип не отключает дисплеи.")
        while self.active and not self.stopped.wait(0.15):
            self._check_health()
            while not self.events.empty():
                action = self.events.get()
                if action == "EMERGENCY":
                    self.emergency()
                    return
                self.emit("shortcut", action)
            if not self.active:
                return
            if self.activation_event is not None and not self.activation_event.is_set():
                # Keep keyboard/navigation health and emergency processing
                # alive while waiting; the ordinary desktop must retain focus.
                focus_lost_since = None
                continue
            if not foreground.is_foreground():
                now = time.monotonic()
                if focus_lost_since is None:
                    focus_lost_since = now
                if now - last_focus > 4:
                    self.emit("focus_lost", "Фокус перешёл в другое окно; выполнена попытка вернуть окно экзамена.")
                    last_focus = now
                try:
                    foreground.activate()
                except Exception:
                    # Windows can transiently deny activation. Do not claim
                    # successful protection if focus remains elsewhere.
                    if now - focus_lost_since >= 2:
                        raise RuntimeError("Не удалось вернуть фокус окна за 2 секунды; экзамен остановлен")
                if now - focus_lost_since >= 2 and not foreground.is_foreground():
                    raise RuntimeError("Окно экзамена потеряло фокус более чем на 2 секунды")
            else:
                focus_lost_since = None
