# taskbargap: design notes

The taskbar detection and placement code out of a shipping Windows widget, lifted
into a standalone library so that any "put something in the taskbar gap" tool can
reuse it. The hard parts had already been proven in a real application before this
package existed, which is why the detection and placement paths are more careful
than a first draft would be.

**Status:** v0.1.1 on PyPI, alpha. The API can still change inside 0.x.

## The problem being solved

Placing a window in the taskbar gap correctly means solving four things that are
individually annoying and collectively a real chunk of Win32 work:

1. **Find the gap.** Locate `Shell_TrayWnd`, the tray (`TrayNotifyWnd`) on the
   right, and the app-button strip on the left, then return the empty span between
   them, per monitor, in physical pixels.
2. **Handle DPI.** The taskbar and windows live in physical pixels, but window
   toolkits, pywebview and WinForms included, often scale *position* by the DPI
   factor. Get this wrong and the window lands off-screen. It needs per-monitor-v2
   awareness and a clear physical versus logical contract.
3. **Place a top-most tool window.** Move the window into the gap, make it
   `WS_EX_TOOLWINDOW | WS_EX_TOPMOST` so it has no taskbar button and no Alt-Tab
   entry, and show it without stealing focus.
4. **Keep it there.** A background watcher that re-fits when the gap changes, as
   apps open and close, as Explorer restarts, as the resolution or DPI changes,
   and that re-asserts top-most.

## What was taken, and what was left behind

What moved into this library is the mechanical part: locating the taskbar and its
children, computing the empty span, applying the extended styles, moving the
window, and the watcher that keeps it fitted as things change.

What deliberately stayed in the application is every decision about *whether* to
be visible. Hiding for a fullscreen app, yielding when the taskbar gets too
crowded, and anything about what the widget actually draws are all policy. A
placement library that made those calls for you would be wrong more often than
right, so `find_gap()` returns the facts and the caller decides.

Two parts were rebuilt rather than copied across. The ctypes layer now uses a
private `user32` handle, for the reason described below, and the geometry was
split into a module with no Windows dependency at all, so the arithmetic that
decides where a window goes can be tested on any machine.

## Package layout (`src/` layout)

```
src/taskbargap/
  __init__.py     public API: find_gap, place, GapWatcher, enable_dpi_awareness, Gap
  _geometry.py    pure arithmetic (no ctypes): gap span, aligned fit
  _win32.py       ctypes bindings, RECT/MONITORINFO, small helpers
  _detect.py      find_gap(): Shell_TrayWnd -> tray edge, button edge, the span
  _place.py       place(): fit + ex-styles + top-most move/show
  watcher.py      GapWatcher: background thread, change detection
```

`_geometry.py` is the one addition to the original plan. Keeping the arithmetic in
a module that imports nothing is what makes "unit-testable on any OS" true rather
than aspirational, and it keeps the ctypes modules down to Win32 plumbing.

## API contract

- **Units.** Every coordinate a caller sees is in physical pixels, in the process's
  own coordinate space. `Gap.scale` is the divisor for toolkits that want logical
  pixels.
- **`find_gap()`** returns `None`, and never raises on Windows, when there is no
  `Shell_TrayWnd`, no usable span, or a vertically docked taskbar. Callers can
  treat "no gap" as a normal state. Off Windows it raises `NotWindowsError`, since
  the library should not pretend a Linux desktop is just a machine with no gap.
- **`Gap.measured`** is `True` only when the app-button edge was read from a real
  window *and* survived corroboration against the number of windows that must own
  a button. `False` means the edge is a guess, whether from the 30% heuristic or
  from a reported rect that failed corroboration. `False` is not a warning that
  the edge is unsafe and `True` is not a promise that it is safe: it says which
  of the two the library is handing you. A guessed edge can still overlap
  buttons, because on Windows 11 there is nothing dependable to measure.
- **`place()`** never widens past the gap. If `min_width` doesn't fit it returns
  `False` and does nothing, leaving the caller to decide whether to hide, because
  that is policy.
- **`GapWatcher`** places once synchronously in `start()`, then owns a daemon
  thread. `on_change(gap)` fires when the gap moves, so callers can react without
  polling.

## Decisions taken during the extraction

**A private `user32` handle rather than `ctypes.windll.user32`.** Annotating the
process-wide handle is fine in an application and wrong in a library. `windll` is
a shared cache, so setting `argtypes` on it changes behaviour for the host app
too, whether that is pywebview, WinForms or any other package. `_win32` loads its
own `WinDLL` and annotates that instead.

**No `ctypes.wintypes` and no `windll` at import time.** `import taskbargap` works
on any OS, and the Windows requirement only bites at the first call. That is what
lets the geometry tests run on Linux CI, and it keeps the import free of side
effects.

**The watcher posts its window changes, while `place()` sends them.** A
`SetWindowPos` or `ShowWindow` issued from a thread that doesn't own the window
blocks until the owning thread pumps its message loop. Measured on a deliberately
unpumped window, a synchronous call from the watcher thread hung for as long as
the test allowed, over ten seconds, with no error reported anywhere. That is a
background watcher which looks alive and does nothing. The loop therefore uses
`SWP_ASYNCWINDOWPOS` and `ShowWindowAsync`. There is no async form of
`SetWindowLongW`, since it sends `WM_STYLECHANGED` and waits, so
`GapWatcher.start()` does the first style-applying placement synchronously on the
caller's thread. After that the loop only ever moves an already-styled window and
cannot block. The regression test is
`test_watcher_loop_does_not_block_on_an_unpumped_window`.

**Each watcher run owns its stop Event.** With one shared Event, a `stop()` whose
join timed out, followed by a `start()`, would clear the flag under the older
thread and revive it, leaving two loops fighting over one window. Now the flag a
thread waits on is its own and stays set forever, so a thread that outlives its
timeout still exits. `stop()` returns whether it actually finished, and keeps the
thread reference if it didn't, so `running` never lies.

**`Gap.scale` describes the caller's coordinate space, not the monitor's specs.**
`GetDpiForWindow` answers for the window, and the taskbar belongs to
per-monitor-aware Explorer, so it reports 168 DPI to anyone who asks, including a
process living in a virtualised space where that number is wrong. The rule is that
only a per-monitor-aware process may use the window's DPI. Everyone else gets
`GetDpiForSystem`, which is documented to answer in the caller's own terms.
Measured on a 175% display, all three modes:

| awareness | `GetDpiForSystem` | `GetDpiForWindow(tray)` | taskbar rect |
|---|---|---|---|
| unaware (0) | 96 | 168 | `(0, 1186, 2194, 1234)` |
| system (1) | 168 | 168 | `(0, 2076, 3840, 2160)` |
| per-monitor (2) | 168 | 168 | `(0, 2076, 3840, 2160)` |

The unaware row is the trap in one line: the window DPI says 1.75 while the
desktop it can address is 2194px wide. An earlier version reported `1.0` only for
the unaware case, which left the same bug in place for a system-aware process on a
monitor whose DPI differs from the system's, the case a docked laptop with an
external screen hits every day.

That second case has since been measured too, in both directions. See the
validation table. It is worth knowing where it can bite. `Shell_TrayWnd` lives on
the primary monitor, and the system DPI tracks the primary monitor's scale.
Changing it mid-session updates `GetDpiForSystem` for newly started processes,
which was measured, with no sign-out needed. So the primary bar is always on the
system-DPI monitor and `find_gap()` cannot reach the divergent branch today. It
becomes reachable the moment secondary taskbars land, because those bars sit on
monitors that can be scaled independently. That makes a mixed-scale desktop the
rig which has to validate v0.3, rather than an optional extra.

**Every shell rect is sanity-checked before it is believed.** A window handle
existing says nothing about whether its rect means anything. This machine has a
hidden `MSTaskListWClass`, and during an Explorer restart a live handle returns
`(0, 0, 0, 0)`. Trusting that as the button edge yields `Gap(left=0,
measured=True)`, a confident answer that lays the widget over the Start button. It
was reproduced by mutating the guard out, and the regression test is
`test_a_dead_button_rect_falls_back_instead_of_covering_the_buttons`.
`_geometry.is_on_taskbar()` requires a non-degenerate rect that overlaps the
taskbar, and a candidate failing it is skipped so a good window later in the list
still wins. It deliberately does not reject a plausible edge that leaves no room.
On Win10 the rebar band grows with the buttons, so a full taskbar legitimately has
no gap, and falling back to the heuristic there would cover real buttons. That
distinction is the whole fix.

**The button strip's own rect cannot be taken at face value.** Reported from
outside with UI Automation evidence, and the most important thing found in this
library so far: on Windows 11 the app-button container reports a width too
narrow to hold the buttons it actually contains once enough apps are running.
Not stale, not mid-update, just short. Trusting it produced `measured=True` with
a left edge on top of four real icons.

Confirmed here without any COM by counting windows that must own a button: nine
of them, in a strip claiming 878 physical pixels at 250%, which is 39 logical
pixels per button when the floor is about 44. The container rect was also seen
moving between two probes minutes apart, so it tracks changes and still lands
short.

There is no good measurement to switch to. `IAccessible` reports zero children
for that control, UIA reports zero descendants for it as well, and the real
buttons live under a `DesktopWindowXamlSource` element that is a **sibling** of
the legacy control rather than a descendant, with no structural relationship to
it. Walking that would mean coupling to an undocumented shell surface with no
stability contract, and taking a COM dependency, to replace one unreliable
source with another.

So the fix is not a better measurement, it is an honest one: corroborate the
reported edge against the button count, and when it is impossible, say so with
`measured=False` and move the edge to the more conservative of the two
estimates. `measured=False` is not a promise of safety, and the README says so
in those words. The credibility floor (40 logical px) sits below the estimate's
per-button width (44), which is what guarantees a downgrade can never move the
edge left, over the buttons.

The known cost: Windows 10 compresses buttons when the bar fills up, and they
can legitimately drop below the 40px floor there. On such a machine this would
report `measured=False` for an edge that was actually correct, which is
pessimistic rather than wrong, and the right direction to err. Windows 10
remains untested, so that is reasoning rather than measurement.

**`place()` reports refusal.** `SetWindowPos` can fail, on a window owned by a
higher-integrity process or one destroyed mid-call, and returning `True` anyway
would be a lie in a bool that callers branch on. Its result is the return value,
and a failure is logged. `ShowWindow` is not consulted, because its return value
is the window's previous visibility rather than success.

**`place()` takes an `hwnd: int` only.** No toolkit adapters in the core. Getting
an HWND out of tkinter, pywebview or WinForms is one line in the caller, and one
dependency the library doesn't take.

## Testing strategy

Win32 and GUI code is hard to unit-test, so it is split:

- **Pure logic** (`tests/test_geometry.py`, `tests/test_api.py`): the gap span and
  the aligned fit as plain ints, plus the public surface. Runs on any OS.
- **Live shell** (`tests/test_windows.py`): asserts against the real taskbar and a
  real throwaway tkinter window. That the gap sits inside its monitor and matches
  the taskbar row, that a placed window lands inside the gap with the right
  ex-styles, that the watcher re-fits and reports, and the non-blocking regression
  above. Every one of these skips itself when the machine has no taskbar or no
  desktop session, so read the skip report before treating a green CI run as proof.
- **Strict mode**: `TASKBARGAP_REQUIRE_SHELL=1` turns those environment skips into
  failures. Without it, "66 passed" on a machine with no taskbar and "66 passed" on
  one with a taskbar look identical. CI gates on it, and it is the release gate to
  run by hand. It earned itself immediately by revealing that the runners do have a
  taskbar, and that the placement tests were skipping there only because they asked
  for a 200px window in a gap narrower than that. They now size themselves to the
  gap they find, which turned a free second machine from zero placement coverage
  into full coverage.
- **Destructive, opt-in** (`tests/test_explorer_restart.py`): kills `explorer.exe`
  under a running watcher, gated behind `TASKBARGAP_RESTART_EXPLORER=1` so it can
  never run by accident or in CI. It restores the shell either way.

What has actually been validated, and where:

| | Result |
|---|---|
| Windows 11 (26200), 3840×2160, dual monitor | the suite passes, with the destructive test opt-in. Button edge measured, placement lands in the gap, screenshot-confirmed |
| All three DPI-awareness modes, same machine | each reports a `scale` matching the space it addresses. The unaware and per-monitor views reconcile to the same taskbar once scaled |
| Dead or off-bar taskbar child rects | forced by injection: detection degrades to `measured=False` instead of claiming `left=0` |
| Watcher against a never-pumping UI thread | keeps ticking. Verified to fail without the async fix |
| Explorer killed under a running watcher | forced for real (`tests/test_explorer_restart.py`): `find_gap()` never raised, never reported a confident gap at the Start button, and returned `None` for the outage. The taskbar came back as a different window (65874 to 132334) and the watcher re-fitted to it (window 800 to 877 px) with no error |
| Stock Windows 11 build 26200, both taskbar alignments | button edge measured correctly either way: left-aligned gap starts at 865, centred at 2121, matching the visible cluster in a screenshot. No shell replacement is installed, checked for ExplorerPatcher, StartAllBack and Start11, and for third-party DLLs in `explorer.exe` |
| The 30% heuristic fallback | **never exercised on a real machine**, only in unit tests and by injecting dead rects. Stock Win11 and Win10 both host the legacy button windows, so reaching this path takes a shell that doesn't |
| Windows 10 | **not validated**, though it is the layout the measured path was written against |
| Mixed-scale monitors, both directions, three layouts: 175%+100%, 125%+200%, 200%+150% | **validated**, and it exercised the case the rule exists for. Primary detection and placement are unaffected in every layout, across three real taskbar row heights (60px at 125%, 84px at 175%, 96px at 200%), which is the row height the library sizes windows to. The divergence shows up on the secondary bar, whose monitor differs from the system DPI. At 175%+100% a system-aware process is shown `(6720, 3696, 13440, 3780)`, the real 3840px edge stretched into 175% space, where `GetDpiForWindow` answers 96 and the naive 1.0 divisor leaves 6720 instead of 3840. At 125%+200% it runs the other way: the same bar appears as `(3840, 1290, 6240, 1350)`, squeezed by 0.625, with `GetDpiForWindow` answering 192 against a system 1.25. The rule returns the system factor both times. Regression test: `test_scale_ignores_the_monitor_dpi_when_the_process_cannot_use_it`, which self-skips on same-scale machines |
| Auto-hide taskbar | **not validated**. The gap follows the bar off-screen by design, but that is untested |
| GitHub `windows-latest` runners, Python 3.10 and 3.13 | a second, independent Windows machine. The full shell suite passes in strict mode (20 passed, 1 skipped for having no secondary taskbar). Detection, placement, ex-styles, the watcher and all three DPI modes are exercised on hardware that isn't the author's |
| Linux | `ubuntu-latest` in CI (44 passed, 22 Windows-only skipped), reproduced locally under WSL on Python 3.12. It imports, never binds user32, raises `NotWindowsError` rather than pretending, and the geometry gives byte-identical answers to Windows |
| macOS | **not tested**, and not supported. The package raises `NotWindowsError` there |

Two failure modes were found by mutation-testing the guards, that is by removing
the fix and confirming the test fails: the dead-rect detection guard, and the
watcher's non-blocking loop. The rest of the suite has not been mutation-tested.

CI (`.github/workflows/ci.yml`) runs `ruff`, `bandit` and the portable tests on
`ubuntu-latest`, and the full suite on `windows-latest` for 3.10 and 3.13, with
pinned action SHAs. CodeQL, Scorecard and the signed-release workflow sit
alongside it.

## Non-goals

- No UI, rendering, or metrics.
- No visibility policy, so no fullscreen-hide and no yield-when-crowded. Return
  the facts and let the app decide.
- No non-Windows support. If a Linux "panel gap" ever makes sense it belongs in a
  separate library with its own name, not a cross-platform abstraction here.

## What isn't built

- Secondary-monitor taskbars (`Shell_SecondaryTrayWnd`). The window tree there is
  known to differ, using `WorkerW > MSTaskListWClass` with no `ReBarWindow32`, and
  the descendant walk that would read it is already tested. Adding it is additive:
  today's `find_gap()` keeps its meaning either way.
- A UI Automation path for shells that host no legacy button windows. Stock
  Windows 11 turned out to host them in both alignments, so the 30% heuristic is a
  rare fallback rather than the main event. Worth revisiting if a Windows build
  actually removes them.

The API has not yet been used by anything other than its own tests. Until a real
application drives it, treat the shape of `place()` and `GapWatcher` as
provisional, which is what the 0.x version number is for.
