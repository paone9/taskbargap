# Changelog

All notable changes to `taskbargap`. Versions follow [semantic
versioning](https://semver.org), with the pre-1.0 caveat that the API can still
change between minor versions.

## 0.2.0 (2026-10-23)

### Added

- **Windows 11's taskbar is now measured from what it draws.** `find_gap()`
  reads the taskbar's own UI Automation tree, one element per Start, Search,
  Task View, app and Widgets button with its screen rectangle, and returns a
  span between those elements with `measured=True`. This replaces the 0.1.1
  "honest guess" on Windows 11 with a measurement, and it accounts for the
  Widgets button, which the legacy windows never did: in a left-aligned layout
  it sits a few pixels left of the clock, exactly where a right-aligned widget
  was being placed. Where there is more than one empty span the rightmost is
  chosen unless it is under 40% of the widest, which keeps the choice stable as
  apps open and close in both taskbar alignments.

  The read is plain `ctypes` against `CUIAutomation` (no new dependency), asks
  for rectangles only, is cached for 3s unless the cheap Win32 reads see a
  change, and costs ~20-30ms when taken. Any failure falls back to the 0.1.1
  behaviour: the legacy windows, corroborated, `measured=False` when they can't
  be believed. `SECURITY.md` lists the new COM calls.
- `taskbar_items()` exposes those rectangles, so an application can show why
  the gap is where it is.

### Changed

- `Gap.measured` is now True on Windows 11 whenever the UI Automation reading
  succeeded, and keeps its 0.1.1 meaning on the legacy path.

## 0.1.1 (2026-08-27)

### Fixed

- **`measured=True` could be confidently wrong.** On Windows 11 the app-button
  container reports a width too narrow to hold the buttons it contains once
  enough apps are running, so `find_gap()` returned a left edge sitting on top
  of real, clickable icons and labelled it a measurement. Reported with UI
  Automation evidence: 14 real buttons extending to x=1568, against a container
  claiming x=1128.

  The reported edge is now corroborated against the number of windows that must
  own a taskbar button. When the arithmetic is impossible, the gap comes back
  with `measured=False` and the more conservative of the two estimates.

  Read the new note in the README before relying on this: a `measured=False`
  edge is an honest guess, not a safe one, and it can still overlap buttons.
  There is no dependable way to measure the real buttons, which live in an
  undocumented XAML subtree with no structural link to the legacy control.

## 0.1.0 (2026-08-03)

First release. The taskbar detection and placement code out of a shipping
Windows widget, packaged on its own. Alpha: the API can still change, and the
validation table in `DESIGN.md` is specific about what has and hasn't been run.

### Added

- `find_gap()` locates the empty span of the taskbar, or returns `None` when there
  isn't a usable one. It reports `measured` so callers can tell whether the
  app-button edge was read from a real window or guessed.
- `place()` puts an existing window (by HWND) in the gap as a top-most tool
  window, without stealing focus. It never asks for a rectangle wider than the gap.
- `GapWatcher`, a background watcher that re-fits the window as the taskbar
  changes and re-asserts top-most, with an `on_change` callback.
- `enable_dpi_awareness()`, a per-monitor-v2 opt-in with a system-DPI fallback.
- The `Gap` dataclass, `NotWindowsError`, and a `py.typed` marker.

### Known limitations

- Validated on stock Windows 11 build 26200 in both left and centred taskbar
  alignments, across three mixed-DPI monitor layouts, and on GitHub's
  `windows-latest` runners for Python 3.10 and 3.13. Windows 10 and auto-hide
  taskbars are untested.
- The 30% fallback heuristic, used only where a shell hosts no measurable button
  window, has never run on a real machine and can name a left edge that still has
  app buttons on it. Check `Gap.measured`.
- Primary taskbar only, and horizontal taskbars only.

See `DESIGN.md` for the full validation table.
