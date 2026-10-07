"""Keep a calibrated screen and its isolated exam on the same native display.

WinForms reports Bounds and DeviceDpi in native units. No second conversion by
the browser scale is applied. All window access runs on the WinForms UI thread.
"""
from __future__ import annotations

from .window_layout import fit_window_bounds


RECALIBRATE = "Экран или масштаб изменился. Повторите калибровку на экране для теста."


def _on_ui(window, callback):
    from System import Action

    form = getattr(window, "native", None)
    if form is None:
        raise ValueError("Не удалось определить экран калибровки. Повторите калибровку.")
    result = []

    def run():
        # Python exceptions cannot safely cross a managed void Action callback.
        try:
            result.append((True, callback(form)))
        except Exception as exc:
            result.append((False, exc))

    if form.InvokeRequired:
        form.Invoke(Action(run))
    else:
        run()
    if not result:
        raise ValueError(RECALIBRATE)
    ok, value = result[0]
    if not ok:
        raise value
    return value


def _bounds(screen):
    area = screen.Bounds
    return [int(area.X), int(area.Y), int(area.Width), int(area.Height)]


def _snapshot(form, screen):
    dpi = int(form.DeviceDpi)
    bounds = _bounds(screen)
    name = str(screen.DeviceName)
    if not name or dpi <= 0 or min(bounds[2:]) <= 0:
        raise ValueError(RECALIBRATE)
    return {"device_name": name, "bounds": bounds, "dpi": dpi}


def capture_calibration_display(window):
    """Capture the hub's actual display after voluntary calibration fullscreen."""
    from System.Windows.Forms import Screen

    return _on_ui(window, lambda form: _snapshot(form, Screen.FromControl(form)))


def _validate(expected):
    if not isinstance(expected, dict):
        raise ValueError(RECALIBRATE)
    name, bounds, dpi = (expected.get(key) for key in ("device_name", "bounds", "dpi"))
    if (not isinstance(name, str) or not name or not isinstance(bounds, (list, tuple))
            or len(bounds) != 4 or any(type(value) is not int for value in bounds)
            or min(bounds[2:]) <= 0 or type(dpi) is not int or dpi <= 0):
        raise ValueError(RECALIBRATE)


def verify_calibration_display(window, expected):
    """Fail before readiness if fullscreen changed the calibrated display/scale."""
    _validate(expected)
    if capture_calibration_display(window) != expected:
        raise ValueError(RECALIBRATE)


def restore_calibration_display(window, expected):
    """Place the child on its calibrated monitor before native protection enters."""
    from System.Drawing import Rectangle, Size
    from System.Windows.Forms import FormWindowState, Screen

    _validate(expected)

    def apply(form):
        matches = [screen for screen in Screen.AllScreens if str(screen.DeviceName) == expected["device_name"]]
        if len(matches) != 1 or _bounds(matches[0]) != expected["bounds"]:
            raise ValueError(RECALIBRATE)
        area, bounds, minimum = matches[0].WorkingArea, form.Bounds, form.MinimumSize
        layout = fit_window_bounds((area.X, area.Y, area.Width, area.Height),
                                   (bounds.Width, bounds.Height),
                                   (minimum.Width or 640, minimum.Height or 420))
        form.WindowState = FormWindowState.Normal
        form.MinimumSize = Size(layout.minimum_width, layout.minimum_height)
        form.Bounds = Rectangle(layout.x, layout.y, layout.width, layout.height)

    _on_ui(window, apply)
    # Moving between monitors can change DeviceDpi. Read after the UI move,
    # without retaining the primary monitor's initial value.
    verify_calibration_display(window, expected)
