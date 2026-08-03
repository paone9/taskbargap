"""Find the empty span of the taskbar: `Shell_TrayWnd` -> tray edge, button edge."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from . import _win32
from ._geometry import BUTTON_FRACTION, gap_span, is_on_taskbar

#: The taskbar itself. (Secondary monitors get `Shell_SecondaryTrayWnd`; v0.1
#: reads the primary taskbar only. See DESIGN.md.)
TRAY_CLASS = "Shell_TrayWnd"
#: The notification/clock cluster on the right: the gap's right bound.
NOTIFY_CLASS = "TrayNotifyWnd"
#: The app-button strip: the gap's left bound. `ReBarWindow32` hosts it on both
#: Win10 and stock Win11; `MSTaskListWClass` is the button list itself, checked
#: as a fallback because some shells (and secondary taskbars, which use
#: `WorkerW`) host it elsewhere.
BUTTON_CLASSES = ("ReBarWindow32", "MSTaskListWClass")


@dataclass(frozen=True)
class Gap:
    """The empty span of the taskbar, in physical pixels.

    `scale` is the DPI factor of the taskbar's monitor, exposed because toolkits
    that scale window *position* by DPI (pywebview, WinForms) need the logical
    value: `x_logical = round(gap.left / gap.scale)`.

    `measured` is True when the app-button edge was read from a real window, and
    False when it came from the fallback heuristic. Check it before trusting the
    left edge not to sit on top of the buttons.
    """
    left: int
    right: int
    top: int
    bottom: int
    scale: float          # DPI factor of the taskbar's monitor
    monitor: int          # HMONITOR
    measured: bool        # True if the button edge came from a real window

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


def _button_edge(tray: int, tray_rect: Tuple[int, int, int, int]) -> Optional[int]:
    """Right edge of the app-button strip, or None if nothing measurable exists.

    Every candidate is sanity-checked against the taskbar's own rect rather than
    trusted on sight: the shell keeps stale and hidden button windows around
    (this machine has an invisible `MSTaskListWClass`), and during an Explorer
    restart a live handle can still report a zero rect. Candidates that don't
    check out are skipped, so a good window later in the list still wins.

    None means "no shell window would tell us", which sends the caller to the
    heuristic. Stock Windows 11 (26200) does host these windows and moves them
    with the buttons in both left and centred layouts, so on it the answer is
    measured either way.
    """
    for cls in BUTTON_CLASSES:
        hwnd = _win32.find_child(tray, cls) or _win32.find_descendant(tray, cls)
        if not hwnd:
            continue
        rect = _win32.window_rect(hwnd)
        if is_on_taskbar(rect, tray_rect):
            return rect[2]
    return None


def find_gap(fraction: float = BUTTON_FRACTION) -> Optional[Gap]:
    """Detect the empty taskbar gap. Returns a `Gap`, or `None` if there isn't one.

    `None` covers every "no gap right now" state: no taskbar (Explorer
    restarting), no measurable span, an auto-hidden bar mid-slide. Callers
    can treat it as normal rather than exceptional. It does not swallow bugs:
    on a non-Windows platform this raises `NotWindowsError`.

    `fraction` overrides the fallback button-edge heuristic used when the button
    strip can't be measured (native Windows 11).
    """
    tray = _win32.find_window(TRAY_CLASS)
    if not tray:
        return None
    rect = _win32.window_rect(tray)
    if not rect:
        return None
    tray_l, tray_t, tray_r, tray_b = rect
    if tray_r <= tray_l or tray_b <= tray_t:
        return None      # Explorer restarting: a live handle with a dead rect
    if (tray_b - tray_t) > (tray_r - tray_l):
        return None      # docked left/right (Win10): there is no horizontal gap

    notify_rect = _win32.window_rect(_win32.find_child(tray, NOTIFY_CLASS))
    # The tray cluster's *left* edge is the gap's right bound - and it gets the
    # same sanity check, so a dead tray rect can't push the gap over the clock.
    notify_left = notify_rect[0] if is_on_taskbar(notify_rect, rect) else None

    span = gap_span(tray_l, tray_r, notify_left, _button_edge(tray, rect), fraction)
    if span is None:
        return None
    left, right, measured = span
    return Gap(
        left=left, right=right, top=tray_t, bottom=tray_b,
        scale=_win32.window_scale(tray),
        monitor=_win32.monitor_from_window(tray),
        measured=measured,
    )
