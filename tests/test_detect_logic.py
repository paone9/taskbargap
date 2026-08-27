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
