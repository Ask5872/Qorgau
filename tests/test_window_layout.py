"""Work-area geometry and UI-thread-only normal window setup."""
import sys
from types import ModuleType, SimpleNamespace

import pytest

from qorgau.window_layout import fit_native_window, fit_window_bounds, normal_window_options


def test_initial_window_has_normal_chrome_and_conservative_dimensions():
    options = normal_window_options()
    assert options["width"] <= 1100 and options["height"] <= 700
    assert options["min_size"] == (640, 420)
    assert options["resizable"] is True
    assert all(options[name] is False for name in
               ("fullscreen", "on_top", "frameless", "easy_drag"))


@pytest.mark.parametrize("area", [
    (0, 0, 1366, 728),       # 1366x768 with a taskbar.
    (0, 0, 1093, 574),       # Effective work area at 125% scale.
    (0, 0, 911, 472),        # Effective work area at 150% scale.
    (0, 40, 1920, 1040),     # Taskbar at the top.
    (80, 0, 1840, 1080),     # Taskbar at the left.
    (-1920, 0, 1920, 1040),  # A monitor left of the primary monitor.
    (-1280, -800, 1280, 760),
    (0, 0, 500, 300),
    (2, -4, 1, 1),
])
def test_entire_outer_frame_and_minimum_fit_work_area(area):
    layout = fit_window_bounds(area, (2000, 1400), (1000, 720))
    left, top, width, height = area
    assert left <= layout.x
    assert top <= layout.y
    assert layout.x + layout.width <= left + width
    assert layout.y + layout.height <= top + height
    assert 1 <= layout.minimum_width <= layout.width
    assert 1 <= layout.minimum_height <= layout.height


def test_normal_desktop_preserves_preferred_size_instead_of_changing_resolution():
    layout = fit_window_bounds((0, 0, 1920, 1040))
    assert (layout.width, layout.height) == (1100, 700)
    assert (layout.x, layout.y) == (410, 170)


@pytest.mark.parametrize("area", [(0, 0, 0, 600), (0, 0, 800, 0), (0, 0, -1, 2)])
def test_invalid_work_area_is_rejected(area):
    with pytest.raises(ValueError):
        fit_window_bounds(area)


@pytest.fixture
def native(monkeypatch):
    trace = []

    class Size:
        def __init__(self, width, height):
            self.Width, self.Height = width, height

    class Rectangle(Size):
        def __init__(self, x, y, width, height):
            super().__init__(width, height)
            self.X, self.Y = x, y

    class Form:
        def __init__(self):
            self.on_ui = False
            self.InvokeRequired = True
            self.TopMost = True
            self.FormBorderStyle = "None"
            self.ControlBox = self.MinimizeBox = self.MaximizeBox = self.ShowInTaskbar = False
            self.WindowState = "Maximized"
            self.Bounds = Rectangle(-1400, -100, 1800, 950)
            self.MinimumSize = Size(1000, 720)

        def Invoke(self, action):
            trace.append("invoke")
            self.on_ui = True
            try:
                action()
            finally:
                self.on_ui = False

    form = Form()
    area = Rectangle(-1366, 0, 1366, 728)

    def from_control(target):
        assert target is form
        assert form.on_ui or not form.InvokeRequired
        trace.append("screen-from-control")
        return SimpleNamespace(WorkingArea=area)

    system, drawing, windows, forms = (ModuleType(name) for name in
        ("System", "System.Drawing", "System.Windows", "System.Windows.Forms"))
    system.Action = lambda function: function
    drawing.Rectangle, drawing.Size = Rectangle, Size
    forms.Screen = SimpleNamespace(FromControl=from_control)
    forms.FormBorderStyle = SimpleNamespace(Sizable="Sizable")
    forms.FormWindowState = SimpleNamespace(Normal="Normal")
    for module in (system, drawing, windows, forms):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    return SimpleNamespace(native=form), trace, area


def test_hub_restores_native_controls_and_fits_whole_outer_frame(native):
    window, trace, area = native
    assert fit_native_window(window)
    form = window.native
    assert trace == ["invoke", "screen-from-control"]
    assert form.TopMost is False and form.FormBorderStyle == "Sizable"
    assert form.ControlBox and form.MinimizeBox and form.MaximizeBox and form.ShowInTaskbar
    assert form.WindowState == "Normal"
    assert form.Bounds.X >= area.X and form.Bounds.Y >= area.Y
    assert form.Bounds.X + form.Bounds.Width <= area.X + area.Width
    assert form.Bounds.Y + form.Bounds.Height <= area.Y + area.Height
    assert form.MinimumSize.Height <= form.Bounds.Height


def test_fitting_does_not_divide_native_coordinates_by_dpi_again(native):
    window, _, area = native
    window.native.DeviceDpi = 144
    assert fit_native_window(window)
    assert window.native.Bounds.Width == area.Width - 32
    assert window.native.Bounds.Height == area.Height - 32


def test_second_call_preserves_user_minimize_resize_and_position(native):
    window, trace, _ = native
    assert fit_native_window(window)
    window.native.WindowState = "Minimized"
    window.native.Bounds.Width = 730
    window.native.Bounds.X = -1200
    trace.clear()
    assert fit_native_window(window)
    assert trace == []
    assert window.native.WindowState == "Minimized"
    assert window.native.Bounds.Width == 730 and window.native.Bounds.X == -1200


def test_exam_child_fit_does_not_modify_protection_or_chrome(native):
    window, _, _ = native
    assert fit_native_window(window, free=False)
    assert window.native.TopMost is True
    assert window.native.FormBorderStyle == "None"
    assert window.native.WindowState == "Maximized"
    assert window.native.ControlBox is False


def test_already_on_ui_thread_does_not_invoke_again(native):
    window, trace, _ = native
    window.native.InvokeRequired = False
    assert fit_native_window(window)
    assert trace == ["screen-from-control"]


def test_missing_native_window_reports_warning_without_crash(native, capsys):
    assert fit_native_window(SimpleNamespace(native=None)) is False
    assert "не удалось подобрать размер окна" in capsys.readouterr().err


def test_native_error_remains_retryable(native, capsys):
    window, _, area = native
    area.Width = 0
    assert fit_native_window(window) is False
    assert not getattr(window, "_qorgau_native_layout_fitted", False)
    area.Width = 1366
    assert fit_native_window(window) is True
    assert "не удалось подобрать размер окна" in capsys.readouterr().err
