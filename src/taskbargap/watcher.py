"""Keep a window fitted to the gap and top-most as the taskbar changes."""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from . import _win32
from ._detect import Gap, find_gap
from ._place import place

log = logging.getLogger("taskbargap")

#: Ignore sub-pixel-ish jitter in the detected edges; re-place only on a real move.
MOVE_TOLERANCE = 8
#: DPI factors are floats; compare with a tolerance, not ==.
SCALE_TOLERANCE = 0.01


class GapWatcher:
    """Background watcher that re-fits `hwnd` when the taskbar gap changes.

    Apps open and close, Explorer restarts, the screen changes resolution, the
    window gets dragged to a monitor with different scaling. All of these move
    the gap out from under a window placed once at startup. The watcher polls
    (there is no reliable event for "the button strip resized"), re-asserts
    top-most every tick, and re-places only when the gap has actually moved.

    `on_change(gap)` fires from the watcher thread whenever the gap moves. The
    initial placement in `start()` doesn't count, and a change is reported even
    when the new gap is too narrow to place into, because "the gap shrank" is
    exactly when an app wants to apply its own policy (yield when crowded, hide
    over fullscreen). A raising callback is logged, not fatal.

    Every window change the loop makes is posted, not sent (SWP_ASYNCWINDOWPOS /
    ShowWindowAsync), because the window belongs to the app's UI thread: a
    synchronous SetWindowPos from here would block until that thread pumped its
    message loop, and a busy one would silently freeze the watcher. The flip
    side is that a re-fit lands when the app next pumps, which is normal for a GUI app,
    but nothing moves while the UI thread is blocked.

    Not started automatically. Owns one daemon thread between `start()` and
    `stop()`; usable as a context manager.
    """

    def __init__(
        self,
        hwnd: int,
        *,
        align: str = "right",
        margin: int = 12,
        min_width: int = 160,
        width: int | None = None,
        height: int | None = None,
        interval: float = 1.0,
        on_change: Callable[[Gap], None] | None = None,
    ) -> None:
        if interval <= 0:
            raise ValueError(f"interval must be > 0, got {interval!r}")
        self.hwnd = hwnd
        self.align = align
        self.margin = margin
        self.min_width = min_width
        self.width = width
        self.height = height
        self.interval = interval
        self.on_change = on_change
        self._thread: threading.Thread | None = None
        # One Event per run, owned by the thread that run started. A shared Event
        # could be cleared by a restart while an older thread was still inside a
        # tick, which would revive it and leave two loops fighting over the window.
        self._stop: threading.Event | None = None
        self._gap: Gap | None = None
        self._last_error: BaseException | None = None

    # --- state ---------------------------------------------------------------

    @property
    def gap(self) -> Gap | None:
        """The gap as of the last time it changed; None until the first tick."""
        return self._gap

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def last_error(self) -> BaseException | None:
        """The last exception the loop swallowed, so a caller can surface it.

        The loop keeps going after an error (a transient Win32 failure during an
        Explorer restart must not silently kill the watcher for the rest of the
        session), but it does not hide it: every error is logged and kept here.
        """
        return self._last_error

    # --- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Place the window now, then start watching.

        The first placement runs synchronously, on *your* thread, on purpose:
        it is the call that applies the tool-window/top-most styles, and a style
        change is the one Win32 operation here with no asynchronous form, and it
        would block the watcher thread until the app pumped messages. Doing it
        here means the loop only ever moves an already-styled window, which it
        can do without waiting on anyone.

        Call it from the thread that owns the window (normally the UI thread).
        """
        if self.running:
            raise RuntimeError("GapWatcher is already running")
        gap = find_gap()
        if gap is not None:
            self._gap = gap
            self._place(gap, nonblocking=False)
        stop = threading.Event()
        self._stop = stop
        self._thread = threading.Thread(
            target=self._loop, args=(stop,), name="taskbargap-watcher", daemon=True)
        self._thread.start()

    def stop(self, timeout: float | None = 5.0) -> bool:
        """Ask the thread to finish and wait for it. Safe to call when stopped.

        Returns whether it actually finished. A thread that outlives `timeout`
        is kept rather than forgotten, so `running` keeps telling the truth, and it
        will still exit on its own, because the Event it waits on is its own and
        stays set no matter how many times the watcher is restarted.
        """
        if self._stop is not None:
            self._stop.set()
        thread = self._thread
        if thread is None or thread is threading.current_thread():
            self._thread = None
            return True
        thread.join(timeout)
        if thread.is_alive():
            log.warning("taskbargap: watcher thread did not stop within %ss; "
                        "it will exit after its current tick", timeout)
            return False
        self._thread = None
        return True

    # Annotated by name rather than `typing.Self`, which needs 3.11 or a
    # typing_extensions dependency. This package has no dependencies and the
    # floor is 3.10, so the postponed annotation stays a plain string.
    def __enter__(self) -> GapWatcher:  # noqa: PYI034
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # --- loop ----------------------------------------------------------------

    def _changed(self, gap: Gap) -> bool:
        old = self._gap
        return (
            old is None
            or abs(gap.left - old.left) > MOVE_TOLERANCE
            or abs(gap.right - old.right) > MOVE_TOLERANCE
            or gap.top != old.top
            or gap.bottom != old.bottom
            or abs(gap.scale - old.scale) > SCALE_TOLERANCE
        )

    def _place(self, gap: Gap, nonblocking: bool) -> bool:
        return place(self.hwnd, align=self.align, margin=self.margin,
                     min_width=self.min_width, width=self.width,
                     height=self.height, gap=gap, nonblocking=nonblocking)

    def _loop(self, stop: threading.Event) -> None:
        while not stop.wait(self.interval):
            try:
                if not _win32.is_window(self.hwnd):
                    log.debug("taskbargap: window %s is gone, watcher exiting", self.hwnd)
                    return
                _win32.assert_topmost(self.hwnd, nonblocking=True)
                gap = find_gap()
                if gap is None or not self._changed(gap):
                    continue
                self._gap = gap
                self._place(gap, nonblocking=True)
                if self.on_change is not None:
                    try:
                        self.on_change(gap)
                    except Exception as exc:            # caller's callback
                        self._last_error = exc
                        log.exception("taskbargap: on_change callback raised")
            except Exception as exc:                    # keep watching
                self._last_error = exc
                log.exception("taskbargap: watcher tick failed")
