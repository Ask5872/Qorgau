"""Launcher routing and idle-hub behavior without Win32, sockets or processes."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

import qorgau.attempt_guard as attempts
import qorgau.desktop_session as desktops
import qorgau.exam_window as exam_window
import qorgau.navigation as navigation
import qorgau.window_layout as layout


def forbidden(*args, **kwargs):
    raise AssertionError("An idle hub must not enter Windows exam protection")


class Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


@pytest.fixture
def launcher(monkeypatch):
    filename = Path(__file__).resolve().parents[1] / "run.py"
    spec = importlib.util.spec_from_file_location("qorgau_launch_scope", filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Keep the host interpreter/pytest platform intact. Only production modules
    # under test see Windows, and no real Windows API is initialized.
    windows = SimpleNamespace(platform="win32", executable=sys.executable)
    monkeypatch.setattr(module, "sys", windows)
    monkeypatch.setattr(attempts, "sys", windows)
    monkeypatch.setattr(attempts, "WindowsDesktopAPI", forbidden)
    monkeypatch.setattr(desktops, "WindowsDesktopAPI", forbidden)
    monkeypatch.setattr(attempts.AttemptDesktopGuard, "enter", forbidden)
    monkeypatch.setattr(module, "socket", SimpleNamespace(socket=forbidden))
    monkeypatch.setattr(module, "webbrowser", SimpleNamespace(open=forbidden))
    return module


@pytest.mark.parametrize("mode", ["--desktop", "--isolate"])
def test_hub_and_legacy_launcher_remain_unrestricted_before_exam(launcher, monkeypatch, mode):
    calls, servers = [], []
    window = SimpleNamespace(events=SimpleNamespace(closing=Event(), shown=Event()),
                             toggle_fullscreen=forbidden, hide=forbidden, on_top=False)
    runtime = SimpleNamespace(auth=SimpleNamespace(initial_pin=None), csrf="local-csrf",
                              guard=SimpleNamespace(window=None, enter=forbidden),
                              emit=lambda *args: None, emergency=forbidden,
                              active_id=None, cleanup_in_progress=False)

    def close_runtime():
        calls.append("runtime_close")
        runtime.guard.leave()

    runtime.close = close_runtime
    app = SimpleNamespace(state=SimpleNamespace(runtime=runtime))

    def create_app(root, data, demo, reset_pin, initial_pin):
        calls.append("create_app")
        assert root == Path(launcher.__file__).parent
        assert not demo
        return app

    class Socket:
        def __enter__(self):
            return self

        def bind(self, address):
            assert address == ("127.0.0.1", 8765)
            calls.append("port_check")

        def __exit__(self, *args):
            return False

    class Server:
        def __init__(self, config):
            self.started = self.should_exit = False
            servers.append(self)

        def run(self):
            calls.append("http_start")
            self.started = True

    class Thread:
        def __init__(self, *, target, daemon):
            self.target = target
            assert daemon

        def start(self):
            self.target()

    def create_window(title, url, **kwargs):
        calls.append("create_window")
        assert "кабинеты" in title and "1.9" in title
        assert url == "http://127.0.0.1:8765"
        assert kwargs["fullscreen"] is False and kwargs["on_top"] is False
        assert kwargs["frameless"] is False and kwargs["resizable"] is True
        assert kwargs["easy_drag"] is False
        assert kwargs["min_size"][0] <= 640 and kwargs["min_size"][1] <= 420
        assert kwargs["width"] <= 1100 and kwargs["height"] <= 700
        assert kwargs["text_select"] is True
        assert callable(kwargs["js_api"].hub_minimize)
        assert callable(kwargs["js_api"].hub_close)
        return window

    def fit_native_window(target, free=True):
        assert target is window and free is True
        calls.append("fit_window")

    def start_webview(**kwargs):
        calls.append("webview_start")
        assert kwargs["gui"] == "edgechromium"
        assert isinstance(runtime.guard, attempts.AttemptDesktopGuard)
        assert runtime.guard.window is window
        caps = runtime.guard.capabilities()
        assert caps["isolation_available"] and caps["supported"]
        assert not any(caps[key] for key in ("active", "desktop_isolated", "keyboard_hook", "navigation_guard"))
        assert runtime.guard._api is None and runtime.guard._process is None
        shown, = window.events.shown.handlers
        shown()
        closing, = window.events.closing.handlers
        assert closing() is None
        runtime.active_id = "an-attempt-in-another-window"
        assert closing() is False
        runtime.active_id = None
        runtime.cleanup_in_progress = True
        assert closing() is False
        runtime.cleanup_in_progress = False

    webview = SimpleNamespace(settings={}, create_window=create_window, start=start_webview)
    monkeypatch.setattr(launcher, "socket", SimpleNamespace(socket=Socket))
    monkeypatch.setattr(launcher, "threading", SimpleNamespace(Thread=Thread))
    monkeypatch.setattr(launcher, "time", SimpleNamespace(sleep=forbidden))
    monkeypatch.setattr(navigation, "install_navigation_guard", forbidden)
    monkeypatch.setattr(layout, "fit_native_window", fit_native_window)
    monkeypatch.setitem(sys.modules, "qorgau.server", SimpleNamespace(create_app=create_app))
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(Config=lambda *a, **kw: (a, kw), Server=Server))
    monkeypatch.setitem(sys.modules, "webview", webview)
    monkeypatch.setattr(sys, "argv", ["run.py", mode])
    assert launcher.main() is None
    assert calls == ["port_check", "create_app", "http_start", "create_window", "webview_start",
                     "fit_window", "runtime_close"]
    assert len(servers) == 1 and servers[0].should_exit
    assert webview.settings == {"OPEN_EXTERNAL_LINKS_IN_BROWSER": True, "ALLOW_DOWNLOADS": True}


@pytest.mark.parametrize("mode,entry", [
    ("--exam-window", "window"), ("--exam-supervisor", "supervisor")])
def test_internal_process_routes_without_creating_http_server(launcher, monkeypatch, tmp_path, mode, entry):
    calls = []
    monkeypatch.setitem(sys.modules, "qorgau.server", SimpleNamespace(create_app=forbidden))
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(Config=forbidden, Server=forbidden))
    monkeypatch.setattr(exam_window, "run_exam_window", lambda directory: calls.append(("window", directory)) or 21)
    monkeypatch.setattr(desktops, "run_supervisor", lambda root, directory: calls.append(("supervisor", root, directory)) or 22)
    monkeypatch.setattr(sys, "argv", ["run.py", mode, "--isolation-control", str(tmp_path)])
    assert launcher.main() == (21 if entry == "window" else 22)
    expected = (entry, str(tmp_path)) if entry == "window" else (entry, Path(launcher.__file__).parent, str(tmp_path))
    assert calls == [expected]


@pytest.mark.parametrize("args", [
    ["--exam-window"], ["--exam-supervisor"],
    ["--exam-window", "--exam-supervisor", "--isolation-control", "unused"],
    ["--desktop", "--isolation-control", "unused"],
])
def test_invalid_internal_launcher_flags_fail_before_any_side_effect(launcher, monkeypatch, args):
    monkeypatch.setitem(sys.modules, "qorgau.server", SimpleNamespace(create_app=forbidden))
    monkeypatch.setattr(sys, "argv", ["run.py", *args])
    with pytest.raises(SystemExit) as exc:
        launcher.main()
    assert exc.value.code == 2
