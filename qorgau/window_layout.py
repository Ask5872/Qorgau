"""Normal window chrome and a one-time fit to the current monitor work area.

The native adapter uses WinForms Bounds and Screen.WorkingArea together. Both
are already in the same coordinate system; dividing them by a DPI scale again
would make the window too small or put it outside a secondary monitor. This
module never changes the display resolution or watches/overrides user resizing.
"""
from __future__ import annotations

from dataclasses import dataclass
import sys


def normal_window_options():
    """Conservative initial geometry before the native monitor is available."""
    return {"width": 1100, "height": 700, "min_size": (640, 420),
            "fullscreen": False, "on_top": False, "frameless": False,
            "easy_drag": False, "resizable": True}


@dataclass(frozen=True)
class WindowLayout:
    x: int
    y: int
    width: int
    height: int
    minimum_width: int
    minimum_height: int


def fit_window_bounds(work_area, preferred_size=(1100, 700),
                      minimum_size=(640, 420), margin=16):
    """Fit an OUTER frame, including its title bar, inside (x, y, w, h).

    Coordinates may be negative for monitors above/left of the primary one.
    Tiny work areas also reduce the minimum size, so Windows cannot force the
    frame back outside the usable monitor. Centering is only done at startup.
    """
    x, y, width, height = (int(value) for value in work_area)
    if width < 1 or height < 1:
        raise ValueError("Рабочая область экрана должна иметь положительный размер")
    preferred_width, preferred_height = (int(value) for value in preferred_size)
    minimum_width, minimum_height = (int(value) for value in minimum_size)
    # Keep a small visible gap around the frame without consuming a tiny screen.
    gap = min(max(0, int(margin)), width // 20, height // 20)
    available_width, available_height = width - 2 * gap, height - 2 * gap
    minimum_width = min(available_width, max(1, minimum_width))
    minimum_height = min(available_height, max(1, minimum_height))
    frame_width = min(available_width, max(minimum_width, preferred_width))
    frame_height = min(available_height, max(minimum_height, preferred_height))
    return WindowLayout(x + (width - frame_width) // 2,
                        y + (height - frame_height) // 2,
                        frame_width, frame_height, minimum_width, minimum_height)


def fit_native_window(window, free=True):
    """Fit once on ``shown``; return False with a warning if unavailable.

    ``free=True`` restores ordinary hub chrome on the WinForms UI thread.
    ``free=False`` only fits the exam child BEFORE its guard enters fullscreen.
    A repeated call does nothing, preserving later resizing/minimizing by users.
    """
    if getattr(window, "_qorgau_native_layout_fitted", False):
        return True
    try:
        from System import Action
        from System.Drawing import Rectangle, Size
        from System.Windows.Forms import FormBorderStyle, FormWindowState, Screen

        form = window.native
        if form is None:
            raise RuntimeError("Системное окно ещё не создано")

        def apply():
            if getattr(window, "_qorgau_native_layout_fitted", False):
                return
            if free:
                form.TopMost = False
                form.FormBorderStyle = FormBorderStyle.Sizable
                form.ControlBox = True
                form.MinimizeBox = True
                form.MaximizeBox = True
                form.ShowInTaskbar = True
                form.WindowState = FormWindowState.Normal
            area = Screen.FromControl(form).WorkingArea
            bounds = form.Bounds
            minimum = form.MinimumSize
            layout = fit_window_bounds(
                (area.X, area.Y, area.Width, area.Height),
                (bounds.Width, bounds.Height),
                (minimum.Width or 640, minimum.Height or 420))
            # Lower an oversized minimum before assigning the outer frame.
            form.MinimumSize = Size(layout.minimum_width, layout.minimum_height)
            form.Bounds = Rectangle(layout.x, layout.y, layout.width, layout.height)
            window._qorgau_native_layout_fitted = True

        if form.InvokeRequired:
            form.Invoke(Action(apply))
        else:
            apply()
        return True
    except Exception as exc:
        print("Qorgau: не удалось подобрать размер окна: " + str(exc),
              file=sys.stderr, flush=True)
        return False
