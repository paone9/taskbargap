"""Move an existing window into the gap as a top-most tool window."""
from __future__ import annotations

from . import _win32
from ._detect import Gap, find_gap
from ._geometry import fit

#: A gap window wants no taskbar button and no Alt-Tab entry, and it must sit in
#: the top-most band. WS_EX_APPWINDOW is cleared because it would force the
#: taskbar button back on.
_EX_ADD = _win32.WS_EX_TOOLWINDOW | _win32.WS_EX_TOPMOST
_EX_REMOVE = _win32.WS_EX_APPWINDOW


def place(
    hwnd: int,
    *,
    align: str = "right",
    margin: int = 12,
    min_width: int = 160,
    width: int | None = None,
    height: int | None = None,
    gap: Gap | None = None,
    nonblocking: bool = False,
) -> bool:
    """Size and position `hwnd` inside the taskbar gap as a top-most tool window.

    All sizes are physical pixels. `align` is "left" | "center" | "right".
    `width` defaults to filling the gap; `height` to the taskbar's own height.
    Pass `gap` to place against an already-detected gap instead of re-detecting.

    Returns False, and touches nothing, when there is no gap or `min_width`
    doesn't fit inside it; and False if Windows refuses the move (a window owned
    by a higher-integrity process, or destroyed since the call began), which is
    logged. The rectangle it asks for is never wider than the gap, so it cannot
    ask to cover the app buttons or the tray. Whether "didn't fit" should mean
    hiding is the caller's call, since that is app policy rather than placement.

    The window is shown with SW_SHOWNA, so placing it never steals focus.

    Call it from the thread that owns `hwnd` (the usual case) and the move has
    happened by the time it returns. From any other thread pass
    `nonblocking=True`: the move is posted to the owning thread instead, so a
    busy or stalled UI thread can't hang the caller. It then takes effect when
    that thread next pumps messages, and `True` means "asked", not "moved".

    One step has no asynchronous form: applying the tool-window/top-most
    extended styles sends WM_STYLECHANGED to the owning thread and waits. It
    only ever runs when the styles aren't already right, that is the first time a
    given window is placed, so call `place()` (or `GapWatcher.start()`) once
    from the owning thread and every later call is fully non-blocking.
    """
    if not _win32.is_window(hwnd):
        return False
    if gap is None:
        gap = find_gap()
    if gap is None:
        return False
    box = fit(gap.left, gap.right, align=align, margin=margin,
              min_width=min_width, width=width)
    if box is None:
        return False
    x, w = box
    h = gap.height if height is None else height

    # Windows only drops an existing taskbar button / Alt-Tab entry when the
    # window is hidden across the style change, so hide first if we changed it.
    # A changed style also needs SWP_FRAMECHANGED to take effect on the frame,
    # which the move below folds in.
    styled = _win32.set_ex_styles(hwnd, add=_EX_ADD, remove=_EX_REMOVE)
    if styled:
        _win32.hide(hwnd, nonblocking)
    return _win32.move_topmost(hwnd, x, gap.top, w, h, nonblocking=nonblocking,
                               frame_changed=styled)
