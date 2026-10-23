"""taskbargap - place a top-most window in the empty Windows taskbar gap.

Windows 10/11 only. See README.md for usage and DESIGN.md for how it works.
Every coordinate a caller sees is in physical pixels; `Gap.scale` is the taskbar
monitor's DPI factor, for toolkits that want logical ones.

    import taskbargap

    taskbargap.enable_dpi_awareness()      # once, before creating windows
    gap = taskbargap.find_gap()            # -> Gap | None
    taskbargap.place(my_hwnd, align="right")
"""
from ._detect import Gap, find_gap, taskbar_items
from ._place import place
from ._win32 import NotWindowsError, enable_dpi_awareness
from .watcher import GapWatcher

__version__ = "0.2.1"
__all__ = [
    "Gap",
    "GapWatcher",
    "NotWindowsError",
    "enable_dpi_awareness",
    "find_gap",
    "place",
    "taskbar_items",
]
