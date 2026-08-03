"""End-to-end checks against the real shell. Windows only; skipped elsewhere.

These are the tests that can actually catch a broken ctypes signature or a
detection change in a Windows update, so they assert against the live taskbar
and a real throwaway window rather than mocks.
"""
import dataclasses
import itertools
import os
import subprocess
import sys
import time

import pytest

import taskbargap
from taskbargap import _win32
from taskbargap import watcher as watcher_module
from taskbargap._geometry import is_on_taskbar

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("win"), reason="Windows-only: needs the real shell")

#: Set TASKBARGAP_REQUIRE_SHELL=1 on a machine that definitely has a desktop and
#: a taskbar - a release gate, or CI once you know what the runner provides. It
#: turns "no shell, nothing to test" into a failure, so a green run can no longer
#: mean the tests that matter quietly skipped themselves.
REQUIRE_SHELL = os.environ.get("TASKBARGAP_REQUIRE_SHELL") == "1"


def _no_shell(reason):
    if REQUIRE_SHELL:
        pytest.fail(f"TASKBARGAP_REQUIRE_SHELL=1, but {reason}")
    pytest.skip(reason)


@pytest.fixture(scope="module", autouse=True)
def dpi_aware():
    # Per-monitor-v2 must be set before any window is created, and it is
    # process-wide and one-shot, hence module scope.
    taskbargap.enable_dpi_awareness()


@pytest.fixture
def gap():
    g = taskbargap.find_gap()
    if g is None:
        _no_shell("no taskbar gap on this machine right now")
    return g


@pytest.fixture(scope="module")
def tk_root():
    """One hidden Tk interpreter for the module.

    Module-scoped on purpose: repeatedly creating and destroying `Tk()` in a
    single process makes Tcl lose track of its library path partway through a
    run, which shows up as spurious failures that have nothing to do with this
    library.
    """
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError as exc:                     # no interactive desktop
        _no_shell(f"no desktop session for tkinter: {exc}")
    root.withdraw()
    try:
        yield root
    finally:
        root.destroy()


@pytest.fixture
def fits(gap):
    """Placement parameters sized to whatever gap *this* machine happens to have.

    Hardcoding a 200px window silently skipped every placement test on any
    machine with a crowded or small taskbar, including GitHub's Windows
    runners, which do have a desktop and a taskbar but only a narrow gap. That
    turned a free second machine into no coverage at all. These derive from the
    real gap instead, so the tests run anywhere there is room for a window at
    all, and only give up when there genuinely isn't.
    """
    margin = 12
    avail = gap.width - 2 * margin
    if avail < 40:
        _no_shell(f"gap is {max(avail, 0)}px wide - no room for any window at all")
    width = min(200, avail)
    return {"margin": margin, "width": width, "min_width": min(80, width)}


@pytest.fixture
def window(tk_root):
    """A real, frameless top-level window to place, one per test."""
    import tkinter

    top = tkinter.Toplevel(tk_root)
    top.overrideredirect(True)                     # frameless, like a real bar
    top.geometry("200x40+100+100")
    top.update()
    try:
        yield int(top.wm_frame(), 16)              # the top-level HWND
    finally:
        top.destroy()


def test_enable_dpi_awareness_leaves_the_process_aware():
    # Called twice on purpose: awareness can only be set once per process, and
    # the second call must still report the truth (aware), not a failure.
    assert taskbargap.enable_dpi_awareness() is True
    assert taskbargap.enable_dpi_awareness() is True
    assert _win32.process_is_dpi_aware() is True


def test_find_gap_is_plausible(gap):
    assert isinstance(gap, taskbargap.Gap)
    assert gap.width > 0
    assert gap.height > 0
    assert gap.bottom > gap.top
    assert gap.scale >= 1.0
    assert gap.monitor != 0
    assert isinstance(gap.measured, bool)


def test_gap_sits_inside_its_own_monitor(gap):
    mon = _win32.monitor_rect(gap.monitor)
    assert mon is not None
    left, top, right, bottom = mon
    assert left <= gap.left < gap.right <= right
    assert top <= gap.top < gap.bottom <= bottom


def test_gap_matches_the_taskbar_row(gap):
    tray = _win32.window_rect(_win32.find_window("Shell_TrayWnd"))
    assert tray is not None
    assert (gap.top, gap.bottom) == (tray[1], tray[3])
    assert tray[0] <= gap.left and gap.right <= tray[2]


def test_find_descendant_walks_a_real_shell_tree():
    """Exercise the button-edge fallback path, which the primary bar never uses.

    On a classic taskbar `ReBarWindow32` is a direct child and always wins, so
    the descendant walk that native-Win11-ish layouts depend on would otherwise
    ship untested. A secondary-monitor taskbar has the tree we need for real:
    `WorkerW > MSTaskListWClass`, with no rebar at all.
    """
    secondary = _win32.find_window("Shell_SecondaryTrayWnd")
    if not secondary:
        pytest.skip("no secondary-monitor taskbar on this machine")
    assert _win32.find_child(secondary, "MSTaskListWClass") == 0     # not direct
    found = _win32.find_descendant(secondary, "MSTaskListWClass")
    assert found, "descendant walk failed to find the button list"
    assert is_on_taskbar(_win32.window_rect(found), _win32.window_rect(secondary))
    assert _win32.find_descendant(secondary, "NoSuchWindowClass") == 0


_CHILD = (
    "import ctypes, sys, taskbargap; from taskbargap import _win32;"
    " m = sys.argv[1];"
    " m == 'system' and ctypes.WinDLL('user32').SetProcessDPIAware();"
    " m == 'permonitor' and taskbargap.enable_dpi_awareness();"
    " g = taskbargap.find_gap();"
    " print(_win32.process_dpi_awareness(), g and g.scale, g and g.top,"
    "       _win32.system_scale())"
)


def _child_view(mode):
    """Ask a clean interpreter, in a given DPI-awareness mode, what it sees.

    Awareness is process-wide and one-shot, so each mode needs its own process.
    """
    out = subprocess.run([sys.executable, "-c", _CHILD, mode], capture_output=True,
                         text=True, check=True).stdout.split()
    if out[1] == "None":
        _no_shell(f"no taskbar gap in the {mode} child process")
    return {"awareness": int(out[0]), "scale": float(out[1]),
            "top": float(out[2]), "system_scale": float(out[3])}


@pytest.mark.parametrize("mode,awareness", [
    ("unaware", _win32.DPI_AWARENESS_UNAWARE),
    ("system", _win32.DPI_AWARENESS_SYSTEM_AWARE),
    ("permonitor", _win32.DPI_AWARENESS_PER_MONITOR_AWARE),
])
def test_scale_describes_the_space_each_awareness_mode_addresses(mode, awareness):
    """`scale` has to describe the coordinate space the *caller* is in.

    `GetDpiForWindow` answers for the taskbar's window, which belongs to
    per-monitor-aware Explorer, so it reports the monitor's real factor to
    everyone, including processes living in a virtualised space where that
    number sends windows to the wrong place.
    """
    view = _child_view(mode)
    assert view["awareness"] == awareness
    if mode == "unaware":
        assert view["scale"] == 1.0        # virtualised: it already sees "logical" px
    else:
        assert view["scale"] >= 1.0


def _divergent_taskbar():
    """A taskbar on a monitor whose DPI differs from the system's, if one exists.

    Needs a mixed-scale desktop (e.g. a 175% laptop panel beside a 100% external),
    which is the only rig where the interesting branch of the scale rule can fire.
    """
    u = _win32.user32()
    for cls in ("Shell_SecondaryTrayWnd", "Shell_TrayWnd"):
        hwnd = _win32.find_window(cls)
        if not hwnd:
            continue
        if abs(u.GetDpiForWindow(hwnd) / 96.0 - _win32.system_scale()) > 0.01:
            return cls
    return None


def test_scale_ignores_the_monitor_dpi_when_the_process_cannot_use_it():
    """The branch that misplaces windows if it's wrong.

    `GetDpiForWindow` answers for the window's monitor. A system-DPI-aware
    process does not live in that monitor's coordinate space. Windows hands it
    coordinates virtualised to the system DPI, so trusting the monitor's
    factor scales every position by the ratio between the two.

    Measured on a 175% primary beside a 100% secondary: the system-aware view of
    the secondary taskbar is (6720, 3696, 13440, 3780), i.e. the real 3840px edge
    reported as 6720. `GetDpiForWindow` says 96 (1.0x). Dividing by 1.0 leaves
    6720; dividing by the system's 1.75 recovers 3840. The rule must pick 1.75.
    """
    cls = _divergent_taskbar()
    if cls is None:
        pytest.skip("no monitor at a different scale from the system DPI")
    code = ("import ctypes, sys, taskbargap; from taskbargap import _win32;"
            " ctypes.WinDLL('user32').SetProcessDPIAware();"
            " h = _win32.find_window(sys.argv[1]);"
            " print(_win32.process_dpi_awareness(), _win32.window_scale(h),"
            "       _win32.system_scale(),"
            "       _win32.user32().GetDpiForWindow(h) / 96.0)")
    out = subprocess.run([sys.executable, "-c", code, cls], capture_output=True,
                         text=True, check=True).stdout.split()
    awareness, rule, system, naive = int(out[0]), float(out[1]), float(out[2]), float(out[3])

    assert awareness == _win32.DPI_AWARENESS_SYSTEM_AWARE
    assert naive != pytest.approx(system), (
        "this machine stopped being mixed-scale mid-test; nothing was proven")
    assert rule == pytest.approx(system), (
        f"scale followed the monitor ({naive:.2f}) instead of the space the process "
        f"addresses ({system:.2f})")


def test_the_awareness_modes_describe_the_same_taskbar():
    """The strong version: virtualised and physical views must reconcile.

    An unaware process sees the taskbar shrunk by the system DPI factor. Scaling
    its coordinates by the factor a per-monitor-aware process reports has to land
    back on the physical taskbar. If either `scale` were wrong, it wouldn't.
    """
    unaware, physical = _child_view("unaware"), _child_view("permonitor")
    if abs(physical["scale"] - physical["system_scale"]) > 0.01:
        pytest.skip("taskbar is not on a monitor at the system DPI")
    assert unaware["scale"] == 1.0
    assert unaware["top"] * physical["system_scale"] == pytest.approx(
        physical["top"], abs=2)


def test_a_dead_button_rect_falls_back_instead_of_covering_the_buttons(gap, monkeypatch):
    """Regression: a live handle can report a dead rect during an Explorer restart.

    Trusting `(0, 0, 0, 0)` as the app-button edge puts the gap's left bound at
    x=0 and reports `measured=True`, a confident answer that lays the window
    over the Start button and every app button. The right answer is to treat it
    as unmeasurable and say so.
    """
    tray = _win32.find_window("Shell_TrayWnd")
    tray_rect = _win32.window_rect(tray)
    real_rect = _win32.window_rect
    monkeypatch.setattr(_win32, "window_rect",
                        lambda hwnd: real_rect(hwnd) if hwnd == tray else (0, 0, 0, 0))

    degraded = taskbargap.find_gap()
    assert degraded is not None
    assert degraded.measured is False              # honest about not knowing
    assert degraded.left > tray_rect[0]            # not pinned to the far left
    assert degraded.left == int((tray_rect[2] - tray_rect[0]) * 0.30)
    assert degraded.right == tray_rect[2]          # dead tray rect ignored too


def test_find_gap_is_stable_across_calls(gap):
    again = taskbargap.find_gap()
    assert again is not None
    assert abs(again.left - gap.left) <= 8
    assert abs(again.right - gap.right) <= 8


def test_place_lands_inside_the_gap(gap, window, fits):
    assert taskbargap.place(window, align="right", **fits) is True
    rect = _win32.window_rect(window)
    assert rect is not None
    left, top, right, bottom = rect
    assert gap.left <= left and right <= gap.right      # never over the buttons/tray
    assert right == gap.right - fits["margin"]          # right-aligned to the margin
    assert (top, bottom) == (gap.top, gap.bottom)       # fills the taskbar row


def test_place_makes_it_a_topmost_tool_window(window, fits):
    assert taskbargap.place(window, **fits) is True
    u = _win32.user32()
    ex = u.GetWindowLongW(window, _win32.GWL_EXSTYLE) & 0xFFFFFFFF
    assert ex & _win32.WS_EX_TOOLWINDOW      # no taskbar button, no Alt-Tab
    assert ex & _win32.WS_EX_TOPMOST
    assert not ex & _win32.WS_EX_APPWINDOW
    assert _win32.is_window_visible(window)


def test_place_refuses_a_minimum_that_cannot_fit(gap, window):
    before = _win32.window_rect(window)
    assert taskbargap.place(window, min_width=gap.width + 1000) is False
    assert _win32.window_rect(window) == before        # and touched nothing


def test_place_on_a_dead_window_is_false():
    assert taskbargap.place(0) is False


def test_watcher_places_on_start(gap, window, fits):
    watcher = taskbargap.GapWatcher(window, align="left", interval=0.1, **fits)
    with watcher:
        assert watcher.gap is not None
        rect = _win32.window_rect(window)             # already moved, synchronously
    assert watcher.running is False
    assert watcher.last_error is None
    assert rect is not None
    assert rect[0] == gap.left + fits["margin"]       # left-aligned by the watcher
    assert (rect[1], rect[3]) == (gap.top, gap.bottom)


def test_watcher_refits_when_the_gap_moves(gap, tk_root, window, fits, monkeypatch):
    """Drive a gap change past the watcher and prove it re-places and reports.

    The real taskbar won't move on cue, so the change is injected; everything
    below it, the tick, the re-place and the callback, is the real thing. The
    injected gap slides sideways rather than shrinking, so it stays placeable on
    a machine whose real gap is only just wide enough.
    """
    moved = dataclasses.replace(gap, left=gap.left + 100, right=gap.right + 100)
    seen = []
    watcher = taskbargap.GapWatcher(
        window, align="left", interval=0.1, on_change=seen.append, **fits)
    with watcher:                     # start() fits it to the real gap first
        assert watcher.gap == gap
        monkeypatch.setattr(watcher_module, "find_gap", lambda: moved)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            tk_root.update()          # the loop posts its moves; pump for them
            if seen and _win32.window_rect(window)[0] == moved.left + fits["margin"]:
                break
            time.sleep(0.05)
    assert watcher.last_error is None
    assert seen == [moved]
    assert _win32.window_rect(window)[0] == moved.left + fits["margin"]


def test_watcher_loop_does_not_block_on_an_unpumped_window(gap, window, fits, monkeypatch):
    """Regression: the loop must never wait on the window's own thread.

    `window` belongs to the main thread, which sits in `sleep()` here and never
    pumps messages. A synchronous SetWindowPos or style change from the watcher
    thread would block until it did, which is to say forever, and the watcher
    would look
    dead while reporting no error. Feeding it a gap that moves every tick keeps
    it in the placement path the whole time.
    """
    shifts = itertools.count(20, 20)
    monkeypatch.setattr(
        watcher_module, "find_gap",
        lambda: dataclasses.replace(gap, left=gap.left + (n := next(shifts)),
                                    right=gap.right + n))

    seen = []
    watcher = taskbargap.GapWatcher(
        window, interval=0.05, on_change=seen.append, **fits)
    with watcher:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and len(seen) < 3:
            time.sleep(0.05)          # deliberately never pumping messages
    assert len(seen) >= 3, "watcher stalled on the unpumped window's message loop"
    assert watcher.last_error is None


def test_watcher_rejects_double_start(window):
    watcher = taskbargap.GapWatcher(window, interval=0.1)
    with watcher, pytest.raises(RuntimeError):
        watcher.start()
    watcher.stop()      # idempotent


def test_watcher_stop_reports_that_it_stopped(window):
    watcher = taskbargap.GapWatcher(window, interval=0.1)
    watcher.start()
    assert watcher.stop() is True
    assert watcher.running is False
    assert watcher.stop() is True        # already stopped


def test_restarting_gives_the_new_run_its_own_stop_event(window):
    """A restart must not be able to revive an older thread.

    Each run waits on its own Event, so an earlier thread that outlived its
    `stop()` still sees a set flag and exits, instead of being un-stopped by the
    next `start()` and looping forever alongside the new one.
    """
    watcher = taskbargap.GapWatcher(window, interval=0.1)
    watcher.start()
    first = watcher._stop
    watcher.stop()
    watcher.start()
    try:
        assert watcher._stop is not first
        assert first.is_set()            # the old run stays stopped, forever
        assert not watcher._stop.is_set()
    finally:
        watcher.stop()
