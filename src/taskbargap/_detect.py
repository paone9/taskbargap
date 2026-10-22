"""Find the empty span of the taskbar: `Shell_TrayWnd` -> tray edge, button edge."""
from __future__ import annotations

from dataclasses import dataclass

import threading
import time

from . import _uia, _win32
from ._geometry import (
    BUTTON_FRACTION,
    choose_span,
    estimate_button_edge,
    free_spans,
    gap_span,
    is_on_taskbar,
    strip_can_hold,
)

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

    `measured` is True when the span was read from the buttons the taskbar
    actually draws: on Windows 11, through the taskbar's own UI Automation tree
    (every Start, Search, Task View, app and Widgets button, with its screen
    rectangle); on Windows 10, from the app-button strip's window when that
    reading survived corroboration against the number of windows that must own
    a button. It is False when the edge is a guess.

    False does not mean "unsafe" and True does not mean "safe": it means the
    library is telling you which of the two it is. A guessed edge may still sit
    on top of buttons. Treat False as "draw here at your own risk, or don't draw".
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


def _button_strip(tray: int, tray_rect: tuple[int, int, int, int]):
    """Rect of the app-button strip, or None if no window will admit to one.

    Every candidate is sanity-checked against the taskbar's own rect rather than
    trusted on sight: the shell keeps stale and hidden button windows around,
    and during an Explorer restart a live handle can still report a zero rect.
    Candidates that don't check out are skipped, so a good window later in the
    list still wins.
    """
    for cls in BUTTON_CLASSES:
        hwnd = _win32.find_child(tray, cls) or _win32.find_descendant(tray, cls)
        if not hwnd:
            continue
        rect = _win32.window_rect(hwnd)
        if is_on_taskbar(rect, tray_rect):
            return rect
    return None


def _button_edge(tray: int, tray_rect: tuple[int, int, int, int], scale: float):
    """Where the app buttons end, and whether that is a measurement or a guess.

    The strip's own window rect is the obvious source, and on Windows 11 it can
    be wrong: it reports a width too narrow to hold the buttons it contains, so
    trusting it puts the gap's left edge on top of real, clickable icons. That
    is worse than not knowing, because it is wrong *confidently*.

    So the reported edge is corroborated against the number of windows that must
    own a button. When the arithmetic is impossible, the edge is downgraded to a
    guess: the caller gets `measured=False` and the further right of the two
    estimates, which is the side that risks a smaller gap rather than a covered
    button. It is still a guess. It is not a safe edge, only an honest one.
    """
    strip = _button_strip(tray, tray_rect)
    if strip is None:
        return None, False
    count = _win32.taskbar_button_count()
    if strip_can_hold(strip[0], strip[2], count, scale):
        return strip[2], True
    return max(strip[2], estimate_button_edge(strip[0], count, scale)), False


#: How long a UI Automation reading of the taskbar is reused while nothing the
#: cheap Win32 reads can see has changed. A full reading costs ~20-30ms (one
#: cross-process round trip, cached rectangles for every button), which is too
#: much for callers polling at a few Hz, and the buttons cannot move without
#: one of: a window appearing or vanishing (the button count changes), the tray
#: cluster or the bar itself moving, or a drag-to-reorder, which this interval
#: catches soon enough.
UIA_REFRESH_S = 3.0

_uia_lock = threading.Lock()
_uia_cache: tuple[tuple, float, list[tuple[int, int, int, int]] | None] | None = None


def _taskbar_items(tray: int, signature: tuple) -> list[tuple[int, int, int, int]] | None:
    """`_uia.taskbar_items`, memoised on `signature` for `UIA_REFRESH_S`.

    `signature` is everything the legacy reads know about the bar; any change
    in it forces a fresh reading. None (no answer) is cached too, so a shell
    with no XAML taskbar is not re-asked on every tick.
    """
    global _uia_cache
    now = time.monotonic()
    with _uia_lock:
        cached = _uia_cache
        if cached is not None and cached[0] == signature and now - cached[1] < UIA_REFRESH_S:
            return cached[2]
    items = _uia.taskbar_items(tray)
    with _uia_lock:
        _uia_cache = (signature, now, items)
    return items


def taskbar_items() -> list[tuple[int, int, int, int]] | None:
    """Screen rects, physical px, of the buttons the Windows 11 taskbar draws:
    Start, Search, Task View, every app button, Widgets. Uncached.

    None when there is no answer: not Windows 11's XAML taskbar (Windows 10,
    Server, a replacement shell), UI Automation unavailable, or Explorer not
    responding. `find_gap()` uses this when it can and falls back to the legacy
    button windows when it can't; it is exposed so a caller can show *why* the
    gap is where it is.
    """
    tray = _win32.find_window(TRAY_CLASS)
    if not tray:
        return None
    return _uia.taskbar_items(tray)


def find_gap(fraction: float = BUTTON_FRACTION) -> Gap | None:
    """Detect the empty taskbar gap. Returns a `Gap`, or `None` if there isn't one.

    `None` covers every "no gap right now" state: no taskbar (Explorer
    restarting), no measurable span, an auto-hidden bar mid-slide. Callers
    can treat it as normal rather than exceptional. It does not swallow bugs:
    on a non-Windows platform this raises `NotWindowsError`.

    On Windows 11 the span comes from the buttons the taskbar actually draws,
    read through UI Automation (`measured=True`); where there is more than one
    empty span, see `_geometry.choose_span` for which one is returned. Elsewhere,
    or when that reading fails, the legacy button windows are used, and
    `fraction` overrides the button-edge heuristic taken when even those can't
    be measured.
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

    scale = _win32.window_scale(tray)
    edge, credible = _button_edge(tray, rect, scale)
    items = _taskbar_items(tray, (rect, notify_rect, edge, credible))
    if items:
        # The taskbar's own account of what it draws. Nothing can be covered
        # that isn't in it, so a span between its items is a measurement.
        right_bound = tray_r if notify_left is None else notify_left
        span = choose_span(free_spans(tray_l, right_bound, items))
        if span is None:
            return None      # every pixel is a button: there is no gap
        left, right = span
        measured = credible = True
    else:
        span = gap_span(tray_l, tray_r, notify_left, edge, fraction)
        if span is None:
            return None
        left, right, measured = span
    return Gap(
        left=left, right=right, top=tray_t, bottom=tray_b,
        scale=scale,
        monitor=_win32.monitor_from_window(tray),
        measured=measured and credible,
    )
