"""Ordinary-window controls must never borrow the isolated exam's capabilities."""
from types import SimpleNamespace

import pytest

from qorgau.hub_window import HubWindowBridge


@pytest.fixture
def hub():
    calls = []
    runtime = SimpleNamespace(active_id=None, cleanup_in_progress=False)
    window = SimpleNamespace(minimize=lambda: calls.append("minimize"),
                             destroy=lambda: calls.append("destroy"))
    bridge = HubWindowBridge(runtime)
    bridge._bind(window)
    return bridge, runtime, calls


@pytest.mark.parametrize("method,expected", [("hub_minimize", "minimize"),
                                             ("hub_close", "destroy")])
def test_idle_actions_call_native_window_method(hub, method, expected):
    bridge, _, calls = hub
    assert getattr(bridge, method)() == {"ok": True}
    assert calls == [expected]


@pytest.mark.parametrize("method", ["hub_minimize", "hub_close"])
@pytest.mark.parametrize("active,cleanup", [("exam", False), (None, True), ("exam", True)])
def test_active_attempt_or_cleanup_refuses_native_action(hub, method, active, cleanup):
    bridge, runtime, calls = hub
    runtime.active_id, runtime.cleanup_in_progress = active, cleanup
    result = getattr(bridge, method)()
    assert result["ok"] is False
    assert "завершите" in result["message"]
    assert calls == []


@pytest.mark.parametrize("method", ["hub_minimize", "hub_close"])
def test_action_before_native_window_is_bound_returns_visible_error(method):
    bridge = HubWindowBridge(SimpleNamespace(active_id=None, cleanup_in_progress=False))
    result = getattr(bridge, method)()
    assert result["ok"] is False and result["message"]


def test_bridge_exposes_only_bounded_hub_actions(hub):
    bridge, _, _ = hub
    assert {name for name in dir(bridge) if not name.startswith("_")} == {
        "hub_minimize", "hub_close", "hub_calibration_begin",
        "hub_calibration_ping", "hub_calibration_end"}


def test_controls_become_available_again_after_attempt_cleanup(hub):
    bridge, runtime, calls = hub
    runtime.active_id = "exam"
    assert bridge.hub_minimize()["ok"] is False
    runtime.active_id, runtime.cleanup_in_progress = None, True
    assert bridge.hub_close()["ok"] is False
    runtime.cleanup_in_progress = False
    assert bridge.hub_minimize()["ok"] is True
    assert bridge.hub_close()["ok"] is True
    assert calls == ["minimize", "destroy"]
