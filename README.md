# taskbargap

[![Platform: Windows 10/11](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0078d6)](#honest-limitations)
[![PyPI](https://img.shields.io/pypi/v/taskbargap?logo=pypi&logoColor=white)](https://pypi.org/project/taskbargap/)
[![CI](https://github.com/paone9/taskbargap/actions/workflows/ci.yml/badge.svg)](https://github.com/paone9/taskbargap/actions/workflows/ci.yml)
[![CodeQL](https://github.com/paone9/taskbargap/actions/workflows/codeql.yml/badge.svg)](https://github.com/paone9/taskbargap/actions/workflows/codeql.yml)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/paone9/taskbargap/badge)](https://scorecard.dev/viewer/?uri=github.com/paone9/taskbargap)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Find the empty stretch of the Windows taskbar, the gap between your app buttons
and the system tray, and put a top-most window in it. Correctly, across multiple
monitors and per-monitor DPI scaling.

Windows leaves that strip unused, so a small always-on widget there costs you no
screen space. Doing it properly turns out to be fiddly. You have to detect the gap
across two generations of taskbar internals, handle per-monitor DPI and the
difference between physical and logical pixels, keep the window fitted as apps
open and close, and end up with a top-most tool window that owns its input without
grabbing a taskbar button of its own. This library does that, and nothing else.

> **Windows 10/11 only.** It is built on Win32 APIs, and there is no macOS or Linux
> build. The package imports anywhere, but calling into it off Windows raises
> `NotWindowsError`.

## Install

```
pip install taskbargap
```

The only dependency is the standard library (`ctypes`). It writes nothing at all:
no files, no registry keys, no config, no logs. It makes no network calls. See
[SECURITY.md](SECURITY.md) for the complete list of Win32 calls it makes.

This is an alpha, and it says so on the tin. Before you rely on it, read [what
has and hasn't been validated](DESIGN.md), which is specific about the
configurations it has never run on.

## Quickstart

```python
import taskbargap

taskbargap.enable_dpi_awareness()          # call once, before creating windows

gap = taskbargap.find_gap()                # -> Gap | None
if gap:
    print(gap.left, gap.right, gap.width, gap.scale)

# Place a window (by HWND) into the gap, right-aligned near the tray:
taskbargap.place(my_hwnd, align="right", margin=12)
```

To keep it fitted as the taskbar changes, when apps open and close, when Explorer
restarts, when the resolution changes:

```python
watcher = taskbargap.GapWatcher(my_hwnd, align="right")
watcher.start()          # places it now, then re-fits on change and re-asserts top-most
# ...
watcher.stop()
```

`start()` places the window immediately, on your thread, and returns. The polling
happens on a daemon thread afterwards. Pass `on_change=fn` if you want to be told
when the gap moves, including when it gets too narrow to fit, which is usually
when an app wants to get out of the way.

## API

| Object | Purpose |
|--------|---------|
| `find_gap() -> Gap \| None` | Detect the empty taskbar gap on the primary monitor. `None` if there isn't a usable one. |
| `place(hwnd, *, align="right", margin=12, min_width=160, width=None, height=None, gap=None, nonblocking=False) -> bool` | Size and position an existing window inside the gap as a top-most tool window. `width` defaults to filling the gap, `height` to the taskbar's own height. False means it did nothing: no gap, `min_width` didn't fit, or Windows refused the move. |
| `GapWatcher(hwnd, *, align, margin, min_width, width, height, interval=1.0, on_change=None)` | Background watcher that keeps the window fitted and top-most as the taskbar changes. `.start()` / `.stop() -> bool`, or use it as a context manager. |
| `enable_dpi_awareness() -> bool` | Opt into per-monitor-v2 DPI awareness. Falls back gracefully on old Windows. |
| `Gap` | Frozen dataclass: `left, right, top, bottom` (physical px), `scale` (DPI factor), `monitor` (HMONITOR), `measured`, plus `width` / `height`. |
| `NotWindowsError` | Raised by any Win32 call when you're not on Windows. |

### Units, and the DPI contract

Every coordinate you get back is a **physical pixel** in the coordinate space your
process actually sees. `Gap.scale` is the divisor for toolkits that scale window
position by DPI, such as pywebview and WinForms:

```python
x_logical = round(gap.left / gap.scale)
```

Call `enable_dpi_awareness()` before you create any window. Without it Windows
virtualises coordinates (a 3840px screen at 175% looks 2194px wide) and a window
aimed at the gap lands somewhere else.

Whatever you do, `scale` describes the space your process addresses windows in,
rather than the monitor's spec sheet. That means `1.0` for a DPI-unaware process,
the system DPI for a system-aware one, and the taskbar monitor's own factor only
for a per-monitor-aware one. The distinction is not academic: `GetDpiForWindow`
will happily report the taskbar's real 175% to an unaware caller that sees a
2194px desktop, and dividing by that number puts the window a third of the screen
away from where it belongs. The library stays self-consistent in every mode. It is
only in per-monitor mode that you get real screen pixels and an unscaled window.

## What it deliberately does not do

- No metrics, rendering, or UI. You bring the window, it does the placement.
- No decision about *whether* to be visible. Fullscreen-hide and yielding to a
  crowded taskbar are app policy, and `find_gap()` gives you the facts to decide.
- No cross-platform panels. Windows taskbar only.

## Honest limitations

- **When the button strip can't be measured.** The app-button edge is read from the
  taskbar's own `ReBarWindow32` and `MSTaskListWClass` windows. On stock Windows 11
  (build 26200) those exist and track the buttons in both left-aligned and centred
  layouts, measured here both ways, so the common cases are the measured ones.
  Where a shell doesn't host them, `find_gap()` falls back to assuming the buttons
  end 30% across the bar, and sets `measured=False`. Be clear-eyed about that
  fallback. It is a guess inherited from the app this code was extracted from, it
  has never run on a real machine, and it can name a left edge that still has
  buttons on it. Check `Gap.measured` if covering a button would matter to you.
- **Rects are sanity-checked, so detection degrades rather than lies.** A taskbar
  child reporting a dead or off-bar rectangle, which happens while Explorer is
  restarting, is ignored rather than believed, and you get the heuristic with
  `measured=False`. A plausible edge that leaves no room is respected: a genuinely
  full taskbar returns `None`, because inventing a gap there would cover buttons.
- **Primary taskbar only.** Secondary monitors get their own
  `Shell_SecondaryTrayWnd` bars, and v0.1 reads the primary `Shell_TrayWnd`. The
  gap you get is on whichever monitor that taskbar is on, at that monitor's DPI.
- **Horizontal taskbars only.** A taskbar docked left or right, which Windows 10
  allows, has no horizontal gap worth speaking of, so `find_gap()` returns `None`
  rather than guess.
- **The watcher needs your app to pump messages.** It posts its moves rather than
  sending them, so a busy UI thread can never block it. The flip side is that a
  re-fit only lands the next time your app processes messages. Normal GUI apps do
  that constantly, but a wedged one won't move.
- **Auto-hide taskbars** are not special-cased. You get the gap of the bar wherever
  it currently is, mid-slide included.

## License

MIT.
