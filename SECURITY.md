# Security

`taskbargap` is a small local library. It measures the Windows taskbar and moves a
window you give it. This page spells out exactly what it touches so you, or your
security team, can review it quickly. It is under 800 lines of plain Python across
six modules with no dependencies at all, just the standard library (`ctypes`,
`dataclasses`, `logging`, `sys`, `threading`, `typing`). No binaries, no
obfuscation, nothing to unpack.

## What it reads

Everything goes through `user32.dll` via `ctypes`, on a handle the library loads
for itself rather than mutating the process-wide `ctypes.windll`. The complete
list of read calls:

- Finding the taskbar: `FindWindowW` and `FindWindowExW`, which locate
  `Shell_TrayWnd` and its children (the tray cluster and the app-button strip) by
  window class.
- Measuring: `GetWindowRect`, `MonitorFromWindow`, `GetMonitorInfoW`.
- Checking a window is real: `IsWindow`, `IsWindowVisible`, `GetWindowLongW`.
- DPI: `GetDpiForWindow`, `GetDpiForSystem`, `GetThreadDpiAwarenessContext`,
  `GetAwarenessFromDpiAwarenessContext`.

It reads window geometry. It does not read window contents, capture the screen,
read other processes' memory, hook input, or enumerate what you have open beyond
the taskbar's own child windows.

## What it changes

Only the window handle you pass in, and only with these calls:

- `SetWindowLongW` applies `WS_EX_TOOLWINDOW | WS_EX_TOPMOST` and clears
  `WS_EX_APPWINDOW`, so your window has no taskbar button and no Alt-Tab entry.
- `SetWindowPos` moves it, sizes it, and pins it to the top-most band.
- `ShowWindow` and `ShowWindowAsync` show it without taking focus (`SW_SHOWNA`).

The library never goes looking for windows to move. It acts on the `hwnd` you hand
it and nothing else. If you hand it a window belonging to another process it will
attempt the move, and Windows may refuse on integrity grounds, in which case
`place()` returns `False` and logs the failure rather than pretending it worked.

There is one process-wide change, and only if you ask for it.
`enable_dpi_awareness()` calls `SetProcessDpiAwarenessContext`, falling back to
`SetProcessDPIAware`. That sets your process's DPI awareness, which is one-shot
and affects every window in it. It is a separate, explicit function so that it can
never happen as a side effect of measuring or placing anything.

## What it writes

Nothing. No files, no registry keys, no configuration, no logs, no auto-start
entries, no temporary files. There is no data directory because there is no data.

It emits records to a standard `logging` logger named `taskbargap` when Windows
refuses an operation. It never installs a handler or opens a file. Where those
records go, if anywhere, is entirely your application's logging configuration.

## What it never does

- No network access of any kind. There is no socket, HTTP, or URL code anywhere in
  the package, and no dependency that could add one.
- No subprocesses. The library launches nothing. The test suite does spawn
  `python -c` to check DPI-awareness modes, which are process-wide and cannot be
  tested any other way, but tests are not shipped in your runtime path.
- No administrator rights, elevation, drivers, or kernel access.
- No code injection, DLL injection, or hooks into other processes.
- No telemetry, analytics, or update checks.

## A note on the test suite

One test is destructive. It restarts `explorer.exe` to prove detection survives
the shell going away. It cannot run by accident: it is skipped unless you set
`TASKBARGAP_RESTART_EXPLORER=1` explicitly, it never runs in CI, and it restores
the shell afterwards either way.

## How to check it yourself

- Read it. Six modules, no dependencies. `_win32.py` is the only file that talks to
  Windows, and everything in `_geometry.py` is pure arithmetic.
- CI runs [`ruff`](https://github.com/astral-sh/ruff) and
  [`bandit`](https://github.com/PyCQA/bandit) on every push, plus a
  [CodeQL](https://codeql.github.com/) workflow analysing the Python and the
  workflow files. Results are on the Actions and Security tabs.
- GitHub Actions are pinned to full commit SHAs. Dependabot bumps them and the
  pinned dev tools weekly.
- Releases are published with [PyPI Trusted
  Publishing](https://docs.pypi.org/trusted-publishers/), which uses OIDC so no API
  token is stored anywhere, and signed with [Sigstore](https://www.sigstore.dev/),
  with the `.sigstore` bundles attached to each GitHub Release.
- To confirm there are no network calls, run
  `grep -ri "socket\|urllib\|http\|requests" src/`. It returns nothing. Or put it
  behind a network monitor and watch.

## Reporting a problem

Open a private Security Advisory on the repo, under Security then "Report a
vulnerability", or a normal issue for anything non-sensitive.
