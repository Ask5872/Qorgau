from types import SimpleNamespace

import pytest

from qorgau.navigation import NavigationPolicy, NativeNavigationGuard


class FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def __isub__(self, handler):
        self.handlers.remove(handler)
        return self

    def fire(self):
        for handler in self.handlers[:]:
            handler(None, None)


@pytest.mark.parametrize("uri", [
    "https://example.com", "http://127.0.0.1:8766/", "http://localhost:8765/",
    "http://127.0.0.1.evil.example:8765/", "http://127.0.0.1:8765@evil.example/",
    "http://evil.example@127.0.0.1:8765/", "http://127.0.0.1:8765\\@evil.example/",
    "file:///C:/Users/student/notes.txt", "javascript:alert(1)", "data:text/html,test",
    "ms-settings:display", "microsoft-edge:https://example.com", "about:blank",
    "http://127.0.0.1:notaport/", "http://127.0.0.1:8765\n/", "",
])
def test_navigation_restrictions_apply_only_during_exam(uri):
    policy = NavigationPolicy("http://127.0.0.1:8765")
    assert policy.navigation_allowed(uri, False)
    assert not policy.navigation_allowed(uri, True)


def test_local_navigation_initially_allowed_and_blocked_during_exam():
    policy = NavigationPolicy("http://127.0.0.1:8765")
    for uri in ["http://127.0.0.1:8765/", "http://127.0.0.1:8765/#reports"]:
        assert policy.navigation_allowed(uri, False)
        assert not policy.navigation_allowed(uri, True)


def test_downloads_before_and_after_exam_follow_normal_backend_policy():
    policy = NavigationPolicy("http://127.0.0.1:8765")
    for uri in ["blob:http://127.0.0.1:8765/uuid", "http://127.0.0.1:8765/api/export"]:
        assert policy.download_allowed(uri, False)
        assert not policy.download_allowed(uri, True)
        assert policy.navigation_allowed(uri, False)
        assert not policy.navigation_allowed(uri, True)
    for uri in ["blob:https://evil.example/id", "blob:null/id", "data:text/html,secret", "file:///C:/secret"]:
        assert policy.download_allowed(uri, False)
        assert not policy.download_allowed(uri, True)


@pytest.fixture
def native_guard():
    class FakeEdge:
        def __init__(self, window):
            self.pywebview_window = window
            self.calls = []

        def on_navigation_start(self, sender, args):
            self.calls.append("navigate")

        def on_new_window_request(self, sender, args):
            self.calls.append("external_browser")

        def on_download_starting(self, sender, args):
            self.calls.append("save_dialog")

        def on_webview_ready(self, sender, args):
            self.calls.append("ready")

    window = object()
    active = [False]
    events = []
    original = FakeEdge.on_new_window_request
    guard = NativeNavigationGuard(window, "http://127.0.0.1:8765", lambda: active[0],
                                  lambda *event: events.append(event), FakeEdge).install()
    yield guard, FakeEdge(window), active, FakeEdge
    guard.cleanup()
    assert FakeEdge.on_new_window_request is original


def test_native_newwindow_blocked_only_during_exam(native_guard):
    guard, edge, active, _ = native_guard
    active[0] = True
    for uri in ["http://127.0.0.1:8765/", "https://example.com/"]:
        args = SimpleNamespace(Uri=uri, Handled=False)
        edge.on_new_window_request(None, args)
        assert args.Handled
    assert not edge.calls
    active[0] = False
    args = SimpleNamespace(Uri="https://example.com/", Handled=False)
    edge.on_new_window_request(None, args)
    assert not args.Handled
    assert edge.calls == ["external_browser"]


def test_native_navigation_and_download_event_flags(native_guard):
    guard, edge, active, _ = native_guard
    nav = SimpleNamespace(Uri="https://example.com", Cancel=False)
    edge.on_navigation_start(None, nav)
    assert not nav.Cancel
    assert edge.calls == ["navigate"]
    local = SimpleNamespace(Uri="http://127.0.0.1:8765/", Cancel=False)
    edge.on_navigation_start(None, local)
    assert edge.calls == ["navigate", "navigate"] and not local.Cancel
    active[0] = True
    edge.on_navigation_start(None, local)
    assert local.Cancel and edge.calls == ["navigate", "navigate"]
    download = SimpleNamespace(DownloadOperation=SimpleNamespace(Uri="blob:http://127.0.0.1:8765/id"),
                               Cancel=False, Handled=False)
    edge.on_download_starting(None, download)
    assert download.Cancel and download.Handled
    assert "save_dialog" not in edge.calls
    active[0] = False
    download.Cancel = download.Handled = False
    edge.on_download_starting(None, download)
    assert not download.Cancel and edge.calls[-1] == "save_dialog"


def test_only_target_window_is_restricted(native_guard):
    guard, edge, active, cls = native_guard
    active[0] = True
    unrelated = cls(object())
    args = SimpleNamespace(Uri="https://example.com/", Handled=False)
    unrelated.on_new_window_request(None, args)
    assert unrelated.calls == ["external_browser"] and not args.Handled


def test_native_settings_transition_from_preparation_to_exam_and_back(native_guard):
    guard, edge, active, _ = native_guard
    settings = SimpleNamespace(AreBrowserAcceleratorKeysEnabled=False, AreDefaultContextMenusEnabled=False,
                               AreDevToolsEnabled=False, IsSwipeNavigationEnabled=False,
                               IsZoomControlEnabled=False)
    sender = SimpleNamespace(CoreWebView2=SimpleNamespace(Settings=settings, ProcessFailed=FakeEvent()))
    assert not guard.ready.is_set()
    edge.on_webview_ready(sender, SimpleNamespace(IsSuccess=False))
    assert not guard.ready.is_set() and guard.error
    edge.on_webview_ready(sender, SimpleNamespace(IsSuccess=True))
    assert guard.ready.is_set()
    assert all(value is True for value in vars(settings).values())
    active[0] = True
    assert guard.refresh()
    assert all(value is False for value in vars(settings).values())
    active[0] = False
    assert guard.refresh()
    assert guard.ready.is_set()
    assert all(value is True for value in vars(settings).values())


def test_native_initialization_during_active_exam_stays_restricted(native_guard):
    guard, edge, active, _ = native_guard
    active[0] = True
    settings = SimpleNamespace()
    core = SimpleNamespace(Settings=settings, ProcessFailed=FakeEvent())
    edge.on_webview_ready(SimpleNamespace(CoreWebView2=core), SimpleNamespace(IsSuccess=True))
    assert guard.ready.is_set()
    assert len(vars(settings)) == 5
    assert all(value is False for value in vars(settings).values())


def test_normal_backend_handlers_do_not_emit_exam_violations(native_guard):
    guard, edge, active, _ = native_guard
    calls = []
    guard._report = lambda *args: calls.append(args)
    edge.on_navigation_start(None, SimpleNamespace(Uri="https://example.com", Cancel=False))
    edge.on_new_window_request(None, SimpleNamespace(Uri="https://example.com", Handled=False))
    args = SimpleNamespace(DownloadOperation=SimpleNamespace(Uri="https://example.com/report.pdf"),
                           Cancel=False, Handled=False)
    edge.on_download_starting(None, args)
    assert edge.calls == ["navigate", "external_browser", "save_dialog"]
    assert not calls


def test_refresh_is_marshalled_to_native_ui_thread(native_guard, monkeypatch):
    import sys
    guard, edge, active, _ = native_guard
    settings = SimpleNamespace()
    invoked = []
    sender = SimpleNamespace(CoreWebView2=SimpleNamespace(Settings=settings, ProcessFailed=FakeEvent()),
                             InvokeRequired=False)
    edge.on_webview_ready(sender, SimpleNamespace(IsSuccess=True))
    monkeypatch.setitem(sys.modules, "System", SimpleNamespace(Action=lambda callback: callback))
    sender.InvokeRequired = True
    sender.Invoke = lambda callback: (invoked.append(True), callback())
    active[0] = True
    assert guard.refresh()
    assert invoked == [True]
    assert all(value is False for value in vars(settings).values())


def test_refresh_setting_failure_revokes_readiness(native_guard):
    guard, edge, active, _ = native_guard

    class Settings:
        fail = False

        def __setattr__(self, name, value):
            if self.fail and name == "AreDevToolsEnabled":
                raise RuntimeError("WebView2 setting failed")
            object.__setattr__(self, name, value)

    settings = Settings()
    core = SimpleNamespace(Settings=settings, ProcessFailed=FakeEvent())
    edge.on_webview_ready(SimpleNamespace(CoreWebView2=core), SimpleNamespace(IsSuccess=True))
    assert guard.ready.is_set()
    settings.fail = True
    active[0] = True
    assert not guard.refresh()
    assert not guard.ready.is_set()
    assert "WebView2 setting failed" in guard.error
    settings.fail = False
    active[0] = False
    assert guard.refresh()
    assert guard.ready.is_set()
    assert settings.AreBrowserAcceleratorKeysEnabled


def test_refresh_before_initialization_is_not_ready(native_guard):
    guard, _, _, _ = native_guard
    assert not guard.refresh()
    assert not guard.ready.is_set()
    assert guard.error


def test_native_process_crash_revokes_readiness(native_guard):
    guard, edge, active, _ = native_guard
    event = FakeEvent()
    core = SimpleNamespace(Settings=SimpleNamespace(), ProcessFailed=event)
    edge.on_webview_ready(SimpleNamespace(CoreWebView2=core), SimpleNamespace(IsSuccess=True))
    assert guard.ready.is_set()
    event.fire()
    assert not guard.ready.is_set()
    assert "со сбоем" in guard.error
    assert not guard.refresh()
    assert not guard.ready.is_set()
    guard.cleanup()
    assert not event.handlers


def test_missing_native_crash_event_fails_closed(native_guard):
    guard, edge, active, _ = native_guard
    core = SimpleNamespace(Settings=SimpleNamespace())
    edge.on_webview_ready(SimpleNamespace(CoreWebView2=core), SimpleNamespace(IsSuccess=True))
    assert not guard.ready.is_set()
    assert guard.error


def test_native_failed_reinitialization_clears_previous_readiness(native_guard):
    guard, edge, active, _ = native_guard
    core = SimpleNamespace(Settings=SimpleNamespace(), ProcessFailed=FakeEvent())
    sender = SimpleNamespace(CoreWebView2=core)
    edge.on_webview_ready(sender, SimpleNamespace(IsSuccess=True))
    assert guard.ready.is_set()
    edge.on_webview_ready(sender, SimpleNamespace(IsSuccess=False))
    assert not guard.ready.is_set()
    assert not guard.refresh()
