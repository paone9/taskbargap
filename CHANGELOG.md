# Changelog

All notable changes to `taskbargap`. Versions follow [semantic
versioning](https://semver.org), with the pre-1.0 caveat that the API can still
change between minor versions.

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
