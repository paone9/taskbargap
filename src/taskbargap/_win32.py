"""ctypes bindings for the small slice of user32 this library needs.

Two deliberate choices:

* **A private `user32` handle.** We load our own `ctypes.WinDLL("user32")`
  rather than annotating the process-wide `ctypes.windll.user32`, because a
  library must not mutate global ctypes state that the host app (pywebview,
  WinForms, another package) also uses.
* **Importable anywhere.** No `ctypes.wintypes`, no `windll` at import time, so
  `import taskbargap` works on any OS and the pure geometry can be tested there.
  The Windows requirement only bites when you actually call something.
"""
from __future__ import annotations

import ctypes
import logging
import sys
from typing import Optional, Tuple

log = logging.getLogger("taskbargap")

# --- constants ---------------------------------------------------------------

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_NOACTIVATE = 0x08000000

HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
#: Required for a SetWindowLong style change to take effect on the window frame.
SWP_FRAMECHANGED = 0x0020
#: Post the change to the owning thread instead of waiting for it to handle it.
#: Without this, a SetWindowPos/ShowWindow issued from a thread that does not own
#: the window blocks until the owner pumps its message loop - so a background
#: watcher would hang for as long as the UI thread is busy.
SWP_ASYNCWINDOWPOS = 0x4000
SWP_TOPMOST_ONLY = SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE

SW_HIDE, SW_SHOW, SW_SHOWNA = 0, 5, 8

MONITOR_DEFAULTTONEAREST = 2
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
#: DPI_AWARENESS enum. Per-monitor-v2 also reports as PER_MONITOR_AWARE here.
DPI_AWARENESS_UNAWARE = 0
DPI_AWARENESS_SYSTEM_AWARE = 1
DPI_AWARENESS_PER_MONITOR_AWARE = 2


# --- structures (hand-rolled: ctypes.wintypes is Windows-only) ----------------

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT),
                ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]


_HWND = ctypes.c_void_p
_INT = ctypes.c_int
_UINT = ctypes.c_uint
_DWORD = ctypes.c_ulong
_BOOL = ctypes.c_int
_LPCWSTR = ctypes.c_wchar_p

_user32 = None


class NotWindowsError(OSError):
    """Raised when a Win32 call is attempted on a non-Windows platform."""


def user32():
    """Return the annotated, process-private user32 handle (cached)."""
    global _user32
    if _user32 is None:
        if not sys.platform.startswith("win"):
            raise NotWindowsError(
                "taskbargap is Windows-only: it is built on Win32 shell windows "
                "(running on %r)" % (sys.platform,))
        u = ctypes.WinDLL("user32", use_last_error=True)
        _annotate(u)
        _user32 = u
    return _user32


def _annotate(u) -> None:
    """Set argtypes/restypes. Handles are pointer-sized: without this they are
    truncated to 32 bits on 64-bit Python and every call silently fails."""
    u.FindWindowW.argtypes = [_LPCWSTR, _LPCWSTR]
    u.FindWindowW.restype = _HWND
    u.FindWindowExW.argtypes = [_HWND, _HWND, _LPCWSTR, _LPCWSTR]
    u.FindWindowExW.restype = _HWND
    u.GetWindowRect.argtypes = [_HWND, ctypes.POINTER(RECT)]
    u.GetWindowRect.restype = _BOOL
    u.IsWindow.argtypes = [_HWND]
    u.IsWindow.restype = _BOOL
    u.IsWindowVisible.argtypes = [_HWND]
    u.IsWindowVisible.restype = _BOOL
    u.SetWindowPos.argtypes = [_HWND, _HWND, _INT, _INT, _INT, _INT, _UINT]
    u.SetWindowPos.restype = _BOOL
    u.ShowWindow.argtypes = [_HWND, _INT]
    u.ShowWindow.restype = _BOOL
    u.ShowWindowAsync.argtypes = [_HWND, _INT]
    u.ShowWindowAsync.restype = _BOOL
    u.GetWindowLongW.argtypes = [_HWND, _INT]
    u.GetWindowLongW.restype = ctypes.c_int32
    u.SetWindowLongW.argtypes = [_HWND, _INT, ctypes.c_int32]
    u.SetWindowLongW.restype = ctypes.c_int32
    u.MonitorFromWindow.argtypes = [_HWND, _DWORD]
    u.MonitorFromWindow.restype = _HWND
    u.GetMonitorInfoW.argtypes = [_HWND, ctypes.POINTER(MONITORINFO)]
    u.GetMonitorInfoW.restype = _BOOL
    try:                                   # Win10 1607+; absent on older builds
        u.GetDpiForWindow.argtypes = [_HWND]
        u.GetDpiForWindow.restype = _UINT
        u.GetDpiForSystem.restype = _UINT
        u.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        u.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        u.GetAwarenessFromDpiAwarenessContext.restype = _INT
    except AttributeError:
        pass


# --- helpers -----------------------------------------------------------------

def _handle(value) -> int:
    """Normalise a returned HWND/HMONITOR (int or None) to a plain int."""
    return int(value or 0)


def _as_i32(value: int) -> int:
    """Window longs are signed 32-bit; style bit-sets are naturally unsigned."""
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value > 0x7FFFFFFF else value


def find_window(class_name: str) -> int:
    """Top-level window by class name; 0 if absent."""
    return _handle(user32().FindWindowW(class_name, None))


def find_child(parent: int, class_name: str) -> int:
    """Direct child by class name; 0 if absent."""
    return _handle(user32().FindWindowExW(parent, None, class_name, None))


def find_descendant(parent: int, class_name: str, max_depth: int = 3) -> int:
    """Breadth-first search of `parent`'s descendants for a class name; 0 if absent.

    Depth-limited: the taskbar tree is shallow, and an unbounded walk of shell
    windows is both slow and a good way to hang on a busy Explorer.
    """
    u = user32()
    level = [parent]
    for _ in range(max_depth):
        nxt = []
        for hwnd in level:
            found = _handle(u.FindWindowExW(hwnd, None, class_name, None))
            if found:
                return found
            child = None
            while True:
                child = u.FindWindowExW(hwnd, child, None, None)
                if not child:
                    break
                nxt.append(_handle(child))
        if not nxt:
            break
        level = nxt
    return 0


def window_rect(hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    """`(left, top, right, bottom)` in physical px, or None if the call fails."""
    if not hwnd:
        return None
    r = RECT()
    if not user32().GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return r.left, r.top, r.right, r.bottom


def is_window(hwnd: int) -> bool:
    return bool(hwnd) and bool(user32().IsWindow(hwnd))


def is_window_visible(hwnd: int) -> bool:
    return bool(hwnd) and bool(user32().IsWindowVisible(hwnd))


def monitor_from_window(hwnd: int) -> int:
    return _handle(user32().MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST))


def monitor_rect(hmonitor: int) -> Optional[Tuple[int, int, int, int]]:
    if not hmonitor:
        return None
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32().GetMonitorInfoW(hmonitor, ctypes.byref(mi)):
        return None
    r = mi.rcMonitor
    return r.left, r.top, r.right, r.bottom


def system_scale(fallback: float = 1.0) -> float:
    """DPI factor of the primary monitor (or of the process, if unaware)."""
    try:
        dpi = user32().GetDpiForSystem()
    except (AttributeError, OSError):
        return fallback
    return dpi / 96.0 if dpi else fallback


def process_dpi_awareness() -> int:
    """This process's `DPI_AWARENESS_*` value, or PER_MONITOR when unknowable."""
    u = user32()
    try:
        ctx = u.GetThreadDpiAwarenessContext()
        return u.GetAwarenessFromDpiAwarenessContext(ctx)
    except (AttributeError, OSError):
        # Pre-1607: no way to ask, and no per-monitor DPI to get wrong either.
        return DPI_AWARENESS_PER_MONITOR_AWARE


def process_is_dpi_aware() -> bool:
    """True unless this process is DPI-*un*aware, i.e. gets virtualised coordinates."""
    return process_dpi_awareness() != DPI_AWARENESS_UNAWARE


def window_scale(hwnd: int, fallback: Optional[float] = None) -> float:
    """DPI factor of the monitor `hwnd` is on, *as this process sees it*.

    The subtlety: `GetDpiForWindow` answers for the **window**, and the taskbar
    belongs to per-monitor-aware Explorer, so it reports the taskbar monitor's
    real DPI to anyone who asks, including a process living in a virtualised
    coordinate space where that number is wrong. Measured on a 175% display:
    an unaware process is told the taskbar is 168 DPI while it sees a 2194px
    desktop, and dividing by 1.75 would put the window a third of the way across
    the screen from where it belongs.

    Only a per-monitor-aware process gets real per-monitor coordinates, so only
    it may use the window's DPI. Everyone else gets `GetDpiForSystem`, which is
    documented to answer in the caller's own terms: 96 for an unaware process,
    the system DPI for a system-aware one, which is exactly the space each of
    them addresses windows in.
    """
    if process_dpi_awareness() == DPI_AWARENESS_PER_MONITOR_AWARE:
        try:
            dpi = user32().GetDpiForWindow(hwnd)
            if dpi:
                return dpi / 96.0
        except (AttributeError, OSError):
            pass
    return system_scale() if fallback is None else system_scale(fallback)


def set_ex_styles(hwnd: int, add: int = 0, remove: int = 0) -> bool:
    """OR in / mask out extended styles. True if they were actually changed.

    False means either "already correct" or "Windows refused". The caller only
    needs it to decide whether a frame recalculation is due, and a refusal is
    logged rather than raised so one un-styleable window can't take down a
    watcher loop.
    """
    u = user32()
    cur = u.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
    new = (cur | add) & ~remove & 0xFFFFFFFF
    if new == cur:
        return False
    ctypes.set_last_error(0)
    previous = u.SetWindowLongW(hwnd, GWL_EXSTYLE, _as_i32(new))
    err = ctypes.get_last_error()
    if previous == 0 and err:          # 0 is a legal previous value; 0 + error is not
        log.warning("taskbargap: could not set extended styles on window %s "
                    "(WinError %d) - it may keep its taskbar button", hwnd, err)
        return False
    return True


def move_topmost(hwnd: int, x: int, y: int, w: int, h: int,
                 show: int = SW_SHOWNA, nonblocking: bool = False,
                 frame_changed: bool = False) -> bool:
    """Move and size a window, pin it to the topmost band, and show it.

    Shown with SW_SHOWNA by default, so it never steals focus. Pass
    `nonblocking=True` from a thread that doesn't own the window, and
    `frame_changed=True` after a style change, because Win32 requires SWP_FRAMECHANGED
    for one to take effect on the window frame, and folding it into this call
    saves a second round trip to the owning thread.

    Returns whether Windows accepted the request. `ShowWindow`'s return value is
    the window's *previous* visibility, not a success flag, so it isn't consulted.
    """
    u = user32()
    flags = SWP_NOACTIVATE
    if nonblocking:
        flags |= SWP_ASYNCWINDOWPOS
    if frame_changed:
        flags |= SWP_FRAMECHANGED
    ctypes.set_last_error(0)
    ok = bool(u.SetWindowPos(hwnd, HWND_TOPMOST, x, y, w, h, flags))
    if not ok:
        log.warning("taskbargap: could not place window %s (WinError %d)",
                    hwnd, ctypes.get_last_error())
    show_window(hwnd, show, nonblocking)
    return ok


def assert_topmost(hwnd: int, nonblocking: bool = False) -> None:
    """Re-pin to the topmost band without moving, resizing, or activating."""
    flags = SWP_TOPMOST_ONLY | (SWP_ASYNCWINDOWPOS if nonblocking else 0)
    user32().SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags)


def show_window(hwnd: int, show: int, nonblocking: bool = False) -> None:
    u = user32()
    (u.ShowWindowAsync if nonblocking else u.ShowWindow)(hwnd, show)


def hide(hwnd: int, nonblocking: bool = False) -> None:
    show_window(hwnd, SW_HIDE, nonblocking)


def enable_dpi_awareness() -> bool:
    """Opt into per-monitor-v2 DPI awareness. Call once, before creating windows.

    Without it, Windows lies to the process about coordinates on any monitor
    whose scaling differs from the primary, and a window aimed at the taskbar
    gap lands somewhere else. Falls back to system-DPI awareness on Windows
    older than 1703.

    Returns whether the process ends up DPI-aware, rather than whether this particular
    call was the one that did it. Awareness can only be set once per process, so
    a second call, or a process already aware via its manifest (pywebview, a
    frozen exe), still answers True rather than reporting a failure that isn't.
    """
    u = user32()
    try:
        if u.SetProcessDpiAwarenessContext(
                ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)):
            return True
    except (AttributeError, OSError):
        pass
    try:
        if u.SetProcessDPIAware():
            return True
    except (AttributeError, OSError):
        pass
    return process_is_dpi_aware()
