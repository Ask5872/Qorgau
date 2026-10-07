"""Window-scoped native navigation policy for the pinned pywebview 6.1 backend.

JavaScript prevention alone cannot stop WebView2 opening an external browser.
The wrappers replace the backend's native handlers before a WebView is created,
so a rejected new-window event never reaches pywebview's webbrowser.open call.
No installed package is modified on disk; cleanup restores the class methods.
"""
from __future__ import annotations

import queue
import sys
import threading
from urllib.parse import urlsplit


def _origin(url):
    if not isinstance(url, str) or any(ord(c) < 32 for c in url) or "\\" in url:
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or parsed.username is not None or parsed.password is not None:
            return None
        if not parsed.hostname:
            return None
        return parsed.scheme, parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == "https" else 80)
    except (ValueError, TypeError):
        return None


class NavigationPolicy:
    def __init__(self, local_url):
        self.origin = _origin(local_url)
        if not self.origin or self.origin[1] not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Защита навигации требует локальный адрес Qorgau")

    def navigation_allowed(self, uri, active):
        # The exam is an SPA; it has no legitimate top-level navigations while
        # running. Outside the attempt the normal backend owns navigation,
        # including links opened by the teacher and completed report exports.
        return not active

    def download_allowed(self, uri, active):
        return not active


class NativeNavigationGuard:
    def __init__(self, window, local_url, is_active, on_violation, edge_class):
        self.window, self.policy = window, NavigationPolicy(local_url)
        self.is_active, self.on_violation = is_active, on_violation
        self.edge_class = edge_class
        self.installed = False
        self.ready = threading.Event()
        self.error = ""
        self._originals = {}
        self._wrappers = {}
        self._events = queue.Queue(maxsize=32)
        self._stop = threading.Event()
        self._worker = None
        self._process_handlers = []
        self._sender = None
        self._core = None
        self._initialized = False

    def _report(self, detail, kind="navigation"):
        # Native .NET UI callbacks must return immediately. Acquiring the
        # runtime/store lock on the UI thread could deadlock a window operation.
        try:
            self._events.put_nowait((kind, detail))
        except queue.Full:
            pass

    def _dispatch(self):
        while not self._stop.is_set():
            try:
                kind, detail = self._events.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                self.on_violation(kind, detail)
            except Exception:
                # Event persistence failure cannot relax browser restrictions.
                pass

    def _active(self):
        try:
            return bool(self.is_active())
        except Exception:
            return True  # Fail closed if state is temporarily unavailable.

    def refresh(self):
        """Apply the current attempt state; return False if protection failed.

        Call after the local desktop guard changes its active flag. WebView2
        settings are apartment-thread-bound, so an HTTP/guard thread must
        synchronously marshal this update to the WinForms control's UI thread.
        The ready event describes guard capability, not whether an exam runs.
        """
        if not self.installed or not self._initialized or self._stop.is_set():
            self.ready.clear()
            self.error = self.error or "WebView2 ещё не готов к переключению защиты"
            return False

        def apply_settings():
            if not self._initialized or self._stop.is_set():
                raise RuntimeError("WebView2 недоступен")
            enabled = not self._active()
            settings = self._core.Settings
            for name in ("AreBrowserAcceleratorKeysEnabled", "AreDefaultContextMenusEnabled",
                         "AreDevToolsEnabled", "IsSwipeNavigationEnabled", "IsZoomControlEnabled"):
                setattr(settings, name, enabled)

        try:
            if getattr(self._sender, "InvokeRequired", False):
                from System import Action
                self._sender.Invoke(Action(apply_settings))
            else:
                apply_settings()
            self.error = ""
            self.ready.set()
            return True
        except Exception as exc:
            self.ready.clear()
            self.error = "Не удалось переключить защиту навигации: " + str(exc)
            self._report(self.error, "guard_lost")
            return False

    def install(self):
        if self.installed:
            return self
        names = ("on_navigation_start", "on_new_window_request", "on_download_starting", "on_webview_ready")
        self._originals = {name: getattr(self.edge_class, name) for name in names}
        owner = self

        def targeted(backend):
            return getattr(backend, "pywebview_window", None) is owner.window

        def navigation(backend, sender, args):
            if targeted(backend) and not owner.policy.navigation_allowed(str(args.Uri), owner._active()):
                args.Cancel = True
                owner._report("Заблокирован переход на другую страницу или перезагрузка окна экзамена.")
                return
            return owner._originals["on_navigation_start"](backend, sender, args)

        def new_window(backend, sender, args):
            if targeted(backend) and owner._active():
                args.Handled = True
                owner._report("Заблокирована попытка открыть новую вкладку, окно или внешний браузер.")
                return
            return owner._originals["on_new_window_request"](backend, sender, args)

        def download(backend, sender, args):
            if targeted(backend):
                uri = str(getattr(getattr(args, "DownloadOperation", None), "Uri", ""))
                if not owner.policy.download_allowed(uri, owner._active()):
                    args.Cancel = True
                    args.Handled = True
                    owner._report("Загрузка файла заблокирована во время экзамена.")
                    return
            return owner._originals["on_download_starting"](backend, sender, args)

        def ready(backend, sender, args):
            if not targeted(backend):
                return owner._originals["on_webview_ready"](backend, sender, args)
            owner.ready.clear()
            owner._initialized = False
            owner._sender = owner._core = None
            try:
                result = owner._originals["on_webview_ready"](backend, sender, args)
                if not args.IsSuccess:
                    raise RuntimeError("WebView2 не запустился; защита вкладок недоступна")
                core = sender.CoreWebView2
                if not any(existing is core for existing, _ in owner._process_handlers):
                    def failed(_sender, _args):
                        if owner._core is not core:
                            return
                        owner._initialized = False
                        owner.ready.clear()
                        owner.error = "Процесс WebView2 завершился со сбоем; защита навигации потеряна"
                        owner._report(owner.error, "guard_lost")
                    core.ProcessFailed += failed
                    owner._process_handlers.append((core, failed))
                owner._sender, owner._core = sender, core
                owner._initialized = True
                owner.refresh()
                return result
            except Exception as exc:
                # An event callback exception can otherwise be swallowed by
                # pythonnet, leaving a misleading ready flag after reinit.
                owner.error = "Защита навигации не готова: " + str(exc)
                owner._report(owner.error, "guard_lost")

        self._wrappers = dict(zip(names, (navigation, new_window, download, ready)))
        for name, wrapper in self._wrappers.items():
            setattr(self.edge_class, name, wrapper)
        self._worker = threading.Thread(target=self._dispatch, name="qorgau-navigation-events", daemon=True)
        self._worker.start()
        self.installed = True
        return self

    def cleanup(self):
        # Call after webview.start returns, when native event delegates are no
        # longer in use. Other windows/backends always retain original behavior.
        self._stop.set()
        self.ready.clear()
        self._initialized = False
        self._sender = self._core = None
        for core, handler in self._process_handlers:
            try:
                core.ProcessFailed -= handler
            except Exception:
                pass  # WebView2 may already be disposed after window.close.
        self._process_handlers.clear()
        if self._worker and self._worker is not threading.current_thread():
            self._worker.join(timeout=1)
        for name, wrapper in self._wrappers.items():
            if getattr(self.edge_class, name) is wrapper:
                setattr(self.edge_class, name, self._originals[name])
        self.installed = False
        self.ready.clear()

    __call__ = cleanup


def install_navigation_guard(window, local_url, is_active, on_violation):
    """Install BEFORE webview.start(gui='edgechromium'); cleanup AFTER it exits.

Returns a NativeNavigationGuard. `ready.is_set()` becomes true only after the
actual WebView2 initialized with native callbacks and the current-state settings.
Call `refresh()` after changing the active flag to enable/restore restrictions.
This deliberately rejects unsupported backends rather than claiming protection.
"""
    if sys.platform != "win32":
        raise OSError("Нативная защита вкладок требует Windows и WebView2")
    from webview.platforms.edgechromium import EdgeChrome
    return NativeNavigationGuard(window, local_url, is_active, on_violation, EdgeChrome).install()
