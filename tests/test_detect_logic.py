"""The corroboration decision, tested without a taskbar.

`_button_edge` is where a wrong-but-confident answer gets caught, so it is worth
testing on every OS rather than only where a shell exists. The Win32 calls it
makes are replaced, and the logic under test is the part that decides whether to
believe the strip.
"""
import pytest

from taskbargap import _detect

TRAY = (0, 2040, 3840, 2160)


@pytest.fixture
def shell(monkeypatch):
    """Pretend the shell reports a given strip rect and window count."""
    def _configure(strip, count):
        monkeypatch.setattr(_detect, "_button_strip", lambda tray, rect: strip)
        monkeypatch.setattr(_detect._win32, "taskbar_button_count", lambda: count)
    return _configure


def test_a_credible_strip_is_believed(shell):
    shell((140, 2040, 3000, 2140), 9)
    edge, credible = _detect._button_edge(1, TRAY, 2.5)
    assert (edge, credible) == (3000, True)


def test_the_observed_failure_is_downgraded(shell):
    """Nine windows cannot fit in 878px at 250%, so the edge is not a measurement."""
    shell((140, 2040, 1018, 2140), 9)
    edge, credible = _detect._button_edge(1, TRAY, 2.5)
    assert credible is False
    assert edge == 1130, "should move right to clear the buttons, not stay at 1018"


@pytest.mark.parametrize("scale", [1.0, 1.5, 2.5])
@pytest.mark.parametrize("count", [1, 5, 9, 20, 60])
def test_a_downgrade_never_moves_the_edge_left(shell, count, scale):
    """A downgrade only ever shrinks the gap, never widens it over the buttons.

    This holds by construction rather than by luck: the credibility floor is
    40 logical px per button and the estimate uses 44, so any strip narrow
    enough to be disbelieved is also narrower than its own replacement. If those
    two constants are ever changed so the floor exceeds the estimate width, this
    fails, which is the point of testing it across a range.
    """
    short = 140 + int(count * 39 * scale)        # 39 < the 40px floor: never credible
    shell((140, 2040, short, 2140), count)
    edge, credible = _detect._button_edge(1, TRAY, scale)
    assert credible is False
    assert edge >= short


def test_no_strip_at_all_falls_back_to_the_heuristic(shell):
    shell(None, 9)
    assert _detect._button_edge(1, TRAY, 2.5) == (None, False)


TRAY_RECT = (0, 2076, 3840, 2160)
NOTIFY_RECT = (3397, 2076, 3840, 2160)
WIDGETS = (3117, 2076, 3383, 2160)
BUTTONS = [(0, 2076, 96, 2160)] + [(x, 2076, x + 77, 2160) for x in range(96, 1328, 77)]


@pytest.fixture
def win11(monkeypatch):
    """A stand-in Windows 11 shell: legacy windows that understate the buttons
    (the 0.1.1 failure), plus whatever UI Automation is told to report."""
    monkeypatch.setattr(_detect, "_uia_cache", None)
    monkeypatch.setattr(_detect._win32, "find_window", lambda cls: 0x1234)
    monkeypatch.setattr(_detect._win32, "find_child", lambda parent, cls: 0x5678)
    monkeypatch.setattr(_detect._win32, "window_rect",
                        lambda hwnd: TRAY_RECT if hwnd == 0x1234 else NOTIFY_RECT)
    monkeypatch.setattr(_detect._win32, "window_scale", lambda hwnd: 1.75)
    monkeypatch.setattr(_detect._win32, "monitor_from_window", lambda hwnd: 1)
    monkeypatch.setattr(_detect, "_button_strip", lambda tray, rect: (96, 2076, 710, 2160))
    monkeypatch.setattr(_detect._win32, "taskbar_button_count", lambda: 16)

    def _configure(items):
        monkeypatch.setattr(_detect._uia, "taskbar_items", lambda tray: items)
    return _configure


def test_uia_items_make_the_gap_a_measurement(win11):
    """The 0.1.1 failure, resolved: the legacy strip says the buttons end at
    710 and sixteen windows say that is impossible, so without UIA the gap is
    a guess. With the drawn buttons known, it is the span between the last app
    button and Widgets, and nothing in it is covered."""
    win11(BUTTONS + [WIDGETS])
    gap = _detect.find_gap()
    assert (gap.left, gap.right, gap.measured) == (1328, 3117, True)


def test_the_sliver_right_of_widgets_is_not_the_gap(win11):
    win11(BUTTONS + [WIDGETS])
    gap = _detect.find_gap()
    assert gap.right <= WIDGETS[0]


def test_a_centred_layout_yields_the_right_hand_span(win11):
    centred = [(0, 2076, 96, 2160)] + [(x, 2076, x + 77, 2160) for x in range(1500, 2200, 77)]
    win11(centred)
    gap = _detect.find_gap()
    assert gap.measured is True
    assert gap.left == centred[-1][2]
    assert gap.right == NOTIFY_RECT[0]


def test_no_uia_answer_falls_back_to_the_legacy_path(win11):
    win11(None)
    gap = _detect.find_gap()
    assert gap.measured is False                   # the strip was not credible
    assert gap.right == NOTIFY_RECT[0]


def test_a_bar_that_is_all_buttons_has_no_gap(win11):
    win11([(0, 2076, 3397, 2160)])
    assert _detect.find_gap() is None


def test_uia_is_reused_while_nothing_cheap_changed(win11, monkeypatch):
    calls = []
    win11(BUTTONS + [WIDGETS])
    real = _detect._uia.taskbar_items
    monkeypatch.setattr(_detect._uia, "taskbar_items", lambda tray: calls.append(1) or real(tray))
    _detect.find_gap()
    _detect.find_gap()
    assert len(calls) == 1
    # ...and re-read as soon as the legacy reads see something move.
    monkeypatch.setattr(_detect._win32, "taskbar_button_count", lambda: 17)
    _detect.find_gap()
    assert len(calls) == 2


def test_uia_asks_nothing_of_a_null_handle():
    from taskbargap import _uia
    assert _uia.taskbar_items(0) is None
