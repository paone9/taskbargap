"""The adverse case the rect guards exist for: the shell going away underneath us.

**Destructive, and opt-in.** It kills `explorer.exe`, so the taskbar disappears
for a few seconds and open File Explorer windows close. Nothing else does this to
your desktop, so it is gated behind an explicit environment variable and never
runs in CI or in a normal `pytest` run:

    TASKBARGAP_RESTART_EXPLORER=1 python -m pytest tests/test_explorer_restart.py

Windows restarts the shell by itself (AutoRestartShell); this restores it anyway
if it doesn't come back.
"""
import os
import subprocess
import sys
import time

import pytest

import taskbargap
from taskbargap import _win32

pytestmark = [
    pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows-only"),
    pytest.mark.skipif(
        os.environ.get("TASKBARGAP_RESTART_EXPLORER") != "1",
        reason="destructive: set TASKBARGAP_RESTART_EXPLORER=1 to kill Explorer"),
]

OUTAGE_BUDGET = 10.0      # seconds to watch while the shell is down
RECOVERY_BUDGET = 25.0    # seconds to wait for it to come back and settle


@pytest.fixture
def restored_explorer():
    """Whatever the test does, leave a working taskbar behind."""
    yield
    if not _win32.find_window("Shell_TrayWnd"):
        subprocess.Popen(["explorer.exe"])
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not _win32.find_window("Shell_TrayWnd"):
            time.sleep(0.5)


def test_survives_an_explorer_restart(restored_explorer):
    taskbargap.enable_dpi_awareness()
    tk = pytest.importorskip("tkinter")
    root = tk.Tk()
    root.withdraw()
    top = tk.Toplevel(root)
    top.overrideredirect(True)
    top.geometry("200x40+100+100")
    top.update()
    hwnd = int(top.wm_frame(), 16)

    before_tray = _win32.find_window("Shell_TrayWnd")
    if not before_tray:
        pytest.skip("no taskbar to restart")

    watcher = taskbargap.GapWatcher(hwnd, align="left", width=400, interval=0.2)
    watcher.start()
    observed = []

    def sample():
        """One observation, pumping Tk so the watcher's posted moves land."""
        root.update()
        observed.append(taskbargap.find_gap())      # must never raise

    try:
        subprocess.run(["taskkill", "/f", "/im", "explorer.exe"],
                       capture_output=True, text=True, check=False)

        deadline = time.monotonic() + OUTAGE_BUDGET
        while time.monotonic() < deadline:
            sample()
            time.sleep(0.1)

        if not _win32.find_window("Shell_TrayWnd"):
            subprocess.Popen(["explorer.exe"])

        deadline = time.monotonic() + RECOVERY_BUDGET
        while time.monotonic() < deadline:
            sample()
            if (_win32.find_window("Shell_TrayWnd") not in (0, before_tray)
                    and observed[-1] is not None):
                break
            time.sleep(0.1)

        after_tray = _win32.find_window("Shell_TrayWnd")
        gap = taskbargap.find_gap()

        # 1. The shell really did go away and come back as a different window.
        assert after_tray not in (0, before_tray), "Explorer never restarted"
        assert any(g is None for g in observed), (
            "never saw the taskbar absent - the outage wasn't observed, so this "
            "run proves nothing")

        # 2. Through all of it, detection stayed honest: no exception (sample()
        #    would have propagated it), and never a *confident* wrong answer.
        #    A dead rect trusted as the button edge looks exactly like this.
        tray_left = _win32.window_rect(after_tray)[0]
        bogus = [g for g in observed
                 if g is not None and g.measured and g.left <= tray_left]
        assert not bogus, f"reported a measured gap starting at the Start button: {bogus[:3]!r}"

        # 3. The watcher lived through it and re-fitted to the NEW taskbar.
        assert watcher.running is True
        assert watcher.last_error is None
        assert gap is not None
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            root.update()
            rect = _win32.window_rect(hwnd)
            if rect[0] == gap.left + 12:
                break
            time.sleep(0.1)
        assert rect[0] == gap.left + 12
        assert (rect[1], rect[3]) == (gap.top, gap.bottom)
    finally:
        watcher.stop()
        root.destroy()
