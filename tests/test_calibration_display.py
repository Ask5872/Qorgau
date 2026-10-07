"""Display/DPI continuity between ordinary calibration and protected attempts."""
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from qorgau.calibration_display import (
    capture_calibration_display, restore_calibration_display, verify_calibration_display,
)
from test_attempt_guard import rig
from test_exam_window import make_lifecycle


@pytest.fixture
def native_display(monkeypatch):
    trace = []

    class Size:
        def __init__(self, width, height):
            self.Width, self.Height = width, height

    class Rectangle(Size):
        def __init__(self, x, y, width, height):
            super().__init__(width, height)
            self.X, self.Y = x, y

    primary = SimpleNamespace(DeviceName="DISPLAY1", Bounds=Rectangle(0, 0, 1920, 1080),
                              WorkingArea=Rectangle(0, 0, 1920, 1040), dpi=96)
    secondary = SimpleNamespace(DeviceName="DISPLAY2", Bounds=Rectangle(-1366, 0, 1366, 768),
                                WorkingArea=Rectangle(-1366, 0, 1366, 728), dpi=144)
    displays = [primary, secondary]

    class Form:
        InvokeRequired = True
        MinimumSize = Size(640, 420)
        WindowState = "Normal"
        Bounds = Rectangle(200, 100, 1100, 700)
        on_ui = False

        def Invoke(self, action):
            trace.append("invoke")
            self.on_ui = True
            try:
                action()
            finally:
                self.on_ui = False

        @property
        def DeviceDpi(self):
            return from_control(self).dpi

    form = Form()

    def from_control(target):
        assert target is form and (form.on_ui or not form.InvokeRequired)
        center = form.Bounds.X + form.Bounds.Width // 2
        return next((screen for screen in displays
                     if screen.Bounds.X <= center < screen.Bounds.X + screen.Bounds.Width), primary)

    system, drawing, windows, forms = (ModuleType(name) for name in
        ("System", "System.Drawing", "System.Windows", "System.Windows.Forms"))
    system.Action = lambda function: function
    drawing.Rectangle, drawing.Size = Rectangle, Size
    forms.Screen = SimpleNamespace(FromControl=from_control, AllScreens=displays)
    forms.FormWindowState = SimpleNamespace(Normal="Normal")
    for module in (system, drawing, windows, forms):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    window = SimpleNamespace(native=form)
    return window, displays, trace


def test_capture_uses_actual_native_monitor_and_dpi(native_display):
    window, _, trace = native_display
    window.native.Bounds.X = -1250
    assert capture_calibration_display(window) == {
        "device_name": "DISPLAY2", "bounds": [-1366, 0, 1366, 768], "dpi": 144}
    assert trace == ["invoke"]


def test_child_moves_to_calibrated_secondary_display_without_double_dpi_scale(native_display):
    window, displays, _ = native_display
    expected = {"device_name": "DISPLAY2", "bounds": [-1366, 0, 1366, 768], "dpi": 144}
    restore_calibration_display(window, expected)
    area, bounds = displays[1].WorkingArea, window.native.Bounds
    assert area.X <= bounds.X < 0
    assert bounds.X + bounds.Width <= area.X + area.Width
    assert bounds.Width == 1100  # Native width must not be divided by 1.5.
    assert bounds.Y + bounds.Height <= area.Y + area.Height
    assert capture_calibration_display(window) == expected


@pytest.mark.parametrize("change", ["missing", "resolution", "position", "dpi"])
def test_removed_or_changed_display_requires_recalibration(native_display, change):
    window, displays, _ = native_display
    expected = {"device_name": "DISPLAY2", "bounds": [-1366, 0, 1366, 768], "dpi": 144}
    if change == "missing":
        displays.pop()
    elif change == "resolution":
        displays[1].Bounds.Width = 1280
    elif change == "position":
        displays[1].Bounds.X = -1400
    else:
        displays[1].dpi = 120
    with pytest.raises(ValueError, match="Повторите калибровку"):
        restore_calibration_display(window, expected)


def test_fullscreen_monitor_mismatch_is_rejected(native_display):
    window, _, _ = native_display
    expected = {"device_name": "DISPLAY2", "bounds": [-1366, 0, 1366, 768], "dpi": 144}
    with pytest.raises(ValueError, match="Повторите калибровку"):
        verify_calibration_display(window, expected)


@pytest.mark.parametrize("expected", [None, {}, {"device_name": "D", "bounds": [0, 0, 1, 1], "dpi": True}])
def test_malformed_display_snapshot_does_not_move_window(native_display, expected):
    window, _, trace = native_display
    with pytest.raises(ValueError):
        restore_calibration_display(window, expected)
    assert trace == []


def test_ui_thread_exception_propagates_as_python_error(native_display, monkeypatch):
    window, _, _ = native_display
    forms = sys.modules["System.Windows.Forms"]
    monkeypatch.setattr(forms.Screen, "FromControl", lambda form: (_ for _ in ()).throw(ValueError("unavailable")))
    with pytest.raises(ValueError, match="unavailable"):
        capture_calibration_display(window)


def test_display_snapshot_is_forwarded_without_starting_an_extra_server(rig):
    guard, _, _, _, _, control, *_ = rig
    expected = {"device_name": "DISPLAY2", "bounds": [-1366, 0, 1366, 768], "dpi": 144}
    guard.calibration_display = expected
    guard.enter()
    assert json.loads((control["folder"] / "config.json").read_text())["calibration_display"] == expected


def test_child_places_display_before_protection_and_verifies_before_ready(monkeypatch):
    import qorgau.calibration_display as module

    owner, control, trace = make_lifecycle()
    owner.calibration_display = {"display": "test"}
    monkeypatch.setattr(module, "restore_calibration_display", lambda window, expected: trace.append("pin-display"))
    monkeypatch.setattr(module, "verify_calibration_display", lambda window, expected: trace.append("verify-display"))
    owner.run()
    assert control.ready_called
    assert trace.index("pin-display") < trace.index("enter-guard") < trace.index("verify-display") < trace.index("ready")


@pytest.mark.parametrize("fault", ["restore_calibration_display", "verify_calibration_display"])
def test_invalid_display_never_arms_exam_and_restores_desktop(monkeypatch, fault):
    import qorgau.calibration_display as module

    owner, control, trace = make_lifecycle()
    owner.calibration_display = {"display": "test"}
    monkeypatch.setattr(module, "restore_calibration_display", lambda *args: None)
    monkeypatch.setattr(module, "verify_calibration_display", lambda *args: None)
    monkeypatch.setattr(module, fault, lambda *args: (_ for _ in ()).throw(ValueError("Повторите калибровку")))
    owner.run()
    assert not control.ready_called and "switch-desktop" not in trace
    assert trace.index("restore-desktop") < trace.index("destroy-window")
