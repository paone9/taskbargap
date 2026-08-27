"""Pure placement geometry: plain ints in, plain ints out, no Windows.

Deliberately free of ctypes so the arithmetic that decides *where the window
goes* can be unit-tested on any OS. The Win32 half (`_detect`, `_place`) reads
rects from the shell and hands them to these functions.

All coordinates are physical pixels.
"""
from __future__ import annotations

#: Fallback for the app-button edge when a shell hosts no measurable button
#: window: assume the buttons end 30% of the way across the taskbar. Inherited
#: from the app this code came from, where it shipped. Stock Windows 11 (26200)
#: does host those windows, in both left and centred layouts, so this is a
#: rarely-taken and unvalidated path. `Gap.measured` is False whenever it is used.
BUTTON_FRACTION = 0.30

ALIGNMENTS = ("left", "center", "right")

#: Narrowest a Windows 11 taskbar button gets, in logical pixels. Used only to
#: judge whether a claimed strip width could possibly hold the buttons we know
#: are running, so it is deliberately below the ~44px they actually occupy: the
#: test should fire only when the claim is impossible, never when it is merely
#: tight.
MIN_BUTTON_WIDTH = 40

#: What a button typically occupies, logical pixels. Used to estimate where the
#: strip really ends once the reported edge is known to be wrong.
TYPICAL_BUTTON_WIDTH = 44


def is_on_taskbar(
    rect: tuple[int, int, int, int] | None,
    tray_rect: tuple[int, int, int, int],
) -> bool:
    """Is `rect` a live window rect that actually sits on this taskbar?

    A window handle existing is not evidence that its rect means anything. The
    shell keeps taskbar children around that are hidden or mid-teardown, and during
    an Explorer restart `GetWindowRect` happily returns `(0, 0, 0, 0)`. A
    zero rect read as a button edge puts the gap's left bound at the far left of
    the screen, i.e. straight over the Start button and every app button.

    So a candidate must be a non-empty rect that overlaps the taskbar's own.
    Failing this means "couldn't measure", which sends `find_gap()` to its
    heuristic, which is the honest answer. It deliberately does not reject a plausible
    edge that leaves no room: a genuinely full taskbar has no gap, and inventing
    one there would cover real buttons.
    """
    if rect is None:
        return False
    left, top, right, bottom = rect
    if right <= left or bottom <= top:
        return False                                 # degenerate: not a live window
    tray_left, tray_top, tray_right, tray_bottom = tray_rect
    if right <= tray_left or left >= tray_right:
        return False                                 # not horizontally on this bar
    return not (bottom <= tray_top or top >= tray_bottom)


def gap_span(
    tray_left: int,
    tray_right: int,
    notify_left: int | None = None,
    button_right: int | None = None,
    fraction: float = BUTTON_FRACTION,
) -> tuple[int, int, bool] | None:
    """Compute the empty horizontal span of the taskbar.

    `tray_left`/`tray_right` bound the whole taskbar. `notify_left` is the left
    edge of the notification/clock cluster (the gap's right bound); `None` means
    it wasn't found, so the taskbar's own right edge is used. `button_right` is
    the right edge of the app-button strip (the gap's left bound); `None` means
    it couldn't be measured, so `fraction` of the taskbar width is assumed.

    Returns `(left, right, measured)` where `measured` is True only when the
    button edge came from a real window, or `None` when there is no usable span.
    """
    if tray_right <= tray_left:
        return None
    if button_right is None:
        left = tray_left + int((tray_right - tray_left) * fraction)
        measured = False
    else:
        left = button_right
        measured = True
    right = tray_right if notify_left is None else notify_left
    # Clamp into the taskbar: a stale or bogus child rect must not produce a
    # span that reaches off the bar.
    left = min(max(left, tray_left), tray_right)
    right = min(max(right, tray_left), tray_right)
    if right - left <= 0:
        return None
    return left, right, measured


def fit(
    gap_left: int,
    gap_right: int,
    *,
    align: str = "right",
    margin: int = 12,
    min_width: int = 160,
    width: int | None = None,
) -> tuple[int, int] | None:
    """Compute `(x, width)` for a window inside the span `[gap_left, gap_right)`.

    A `margin` of clear space is kept at each end. `width` is the desired width;
    `None` means "fill the gap". The result never widens past the gap, so it can
    never overlap the app buttons or the tray.

    Returns `None` when `min_width` doesn't fit, leaving the caller to decide
    what that means (hide, shrink, do nothing), because that is policy rather
    than placement.
    """
    if align not in ALIGNMENTS:
        raise ValueError(f"align must be one of {ALIGNMENTS!r}, got {align!r}")
    if margin < 0:
        raise ValueError(f"margin must be >= 0, got {margin!r}")
    if min_width < 0:
        raise ValueError(f"min_width must be >= 0, got {min_width!r}")
    if width is not None and width <= 0:
        raise ValueError(f"width must be > 0 or None, got {width!r}")

    span = gap_right - gap_left
    avail = span - 2 * margin
    if avail < min_width or avail <= 0:
        return None
    w = avail if width is None else min(width, avail)
    w = max(w, min_width)      # avail >= min_width here, so w <= avail always
    if align == "left":
        x = gap_left + margin
    elif align == "center":
        x = gap_left + (span - w) // 2
    else:
        x = gap_right - margin - w
    return x, w


def strip_can_hold(strip_left: int, strip_right: int, button_count: int,
                   scale: float, min_button: int = MIN_BUTTON_WIDTH) -> bool:
    """Could a strip this wide really hold `button_count` buttons?

    The app-button container reports its own width, and on Windows 11 that
    width can stop short of the buttons it contains: measured on a 250% display
    with nine windows open, it claimed 878 physical pixels, i.e. 39 logical
    pixels per button, when the narrowest a button gets is about 44.

    `button_count` must be a floor (pinned-but-not-running apps also take
    slots), and `min_button` sits below the real minimum, so False means the
    claim is impossible rather than merely tight.
    """
    if button_count <= 0 or scale <= 0:
        return True                      # nothing to contradict
    return (strip_right - strip_left) >= button_count * min_button * scale


def estimate_button_edge(strip_left: int, button_count: int, scale: float,
                         button_width: int = TYPICAL_BUTTON_WIDTH) -> int:
    """Where the buttons probably end, when the reported edge is not credible.

    Also a floor, for the same reason: it counts only windows that exist, so
    pinned apps push the real edge further right. Treat the result as a better
    guess than the reported edge, never as a measurement.
    """
    return strip_left + round(button_count * button_width * scale)
