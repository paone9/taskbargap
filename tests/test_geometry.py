"""Pure placement arithmetic: runs on any OS, no Windows, no windows."""
import pytest

from taskbargap._geometry import (
    ALIGNMENTS,
    estimate_button_edge,
    fit,
    gap_span,
    is_on_taskbar,
    strip_can_hold,
)

# The real taskbar this was developed against: 3840x2160 at 175%, classic shell.
# Shell_TrayWnd (0,2076)-(3840,2160), ReBarWindow32 right edge 788,
# TrayNotifyWnd left edge 3404.
REAL = {"tray_left": 0, "tray_right": 3840, "notify_left": 3404, "button_right": 788}


class TestIsOnTaskbar:
    TRAY = (0, 2076, 3840, 2160)

    def test_the_real_button_strip_qualifies(self):
        assert is_on_taskbar((95, 2076, 788, 2160), self.TRAY) is True

    def test_missing_rect(self):
        assert is_on_taskbar(None, self.TRAY) is False

    @pytest.mark.parametrize("rect", [
        (0, 0, 0, 0),                  # Explorer restarting: live handle, dead rect
        (788, 2076, 788, 2160),        # zero width
        (95, 2076, 788, 2076),         # zero height
        (788, 2076, 95, 2160),         # inverted
    ])
    def test_degenerate_rects_are_rejected(self, rect):
        # This is the one that matters: a zero rect read as a button edge would
        # put the gap's left bound at x=0, i.e. over the Start button.
        assert is_on_taskbar(rect, self.TRAY) is False

    @pytest.mark.parametrize("rect", [
        (3935, 2076, 4628, 2160),      # the *other* monitor's taskbar
        (95, 100, 788, 400),           # somewhere else on screen entirely
        (95, 1000, 788, 2000),         # above the bar, not touching
    ])
    def test_rects_off_this_taskbar_are_rejected(self, rect):
        assert is_on_taskbar(rect, self.TRAY) is False

    def test_a_full_width_strip_is_accepted(self):
        # A genuinely full taskbar is plausible, not bogus: it must be accepted
        # here so gap_span reports "no gap" instead of inventing one over the
        # buttons via the heuristic.
        assert is_on_taskbar((95, 2076, 3404, 2160), self.TRAY) is True
        assert gap_span(0, 3840, 3404, 3404) is None


class TestGapSpan:
    def test_measured_edges(self):
        assert gap_span(**REAL) == (788, 3404, True)

    def test_no_button_window_falls_back_to_heuristic(self):
        left, right, measured = gap_span(0, 3840, 3404, None)
        assert (left, right) == (1152, 3404)      # 30% of 3840
        assert measured is False

    def test_no_tray_notify_uses_taskbar_right_edge(self):
        assert gap_span(0, 3840, None, 788) == (788, 3840, True)

    def test_custom_fraction(self):
        left, _, measured = gap_span(0, 1000, 900, None, fraction=0.5)
        assert (left, measured) == (500, False)

    def test_second_monitor_offsets_are_not_special(self):
        # Secondary taskbar at x=3840..7680: coordinates are virtual-desktop wide.
        assert gap_span(3840, 7680, 7300, 4628) == (4628, 7300, True)

    @pytest.mark.parametrize("tray_left,tray_right", [(0, 0), (100, 100), (500, 100)])
    def test_degenerate_taskbar_is_no_gap(self, tray_left, tray_right):
        assert gap_span(tray_left, tray_right, None, None) is None

    def test_buttons_reaching_the_tray_is_no_gap(self):
        assert gap_span(0, 3840, 900, 900) is None      # exactly touching
        assert gap_span(0, 3840, 900, 1200) is None     # overlapping

    def test_edges_outside_the_taskbar_are_clamped(self):
        # A stale child rect must not produce a span that leaves the bar.
        assert gap_span(0, 3840, 9999, -500) == (0, 3840, True)
        assert gap_span(1000, 3840, 3404, 200) == (1000, 3404, True)


class TestFit:
    def test_fills_the_gap_when_no_width_given(self):
        assert fit(788, 3404, align="right", margin=12) == (800, 2592)

    def test_right_alignment_hugs_the_tray(self):
        x, w = fit(788, 3404, align="right", margin=12, width=400)
        assert (x, w) == (2992, 400)
        assert x + w == 3404 - 12

    def test_left_alignment_hugs_the_buttons(self):
        assert fit(788, 3404, align="left", margin=12, width=400) == (800, 400)

    def test_center_alignment(self):
        x, w = fit(1000, 2000, align="center", margin=12, width=400)
        assert (x, w) == (1300, 400)
        assert x - 1000 == 2000 - (x + w)          # symmetric

    def test_never_widens_past_the_gap(self):
        x, w = fit(788, 3404, align="right", margin=12, width=99999)
        assert w == 3404 - 788 - 24
        assert x >= 788 + 12 and x + w <= 3404 - 12

    def test_widths_below_the_minimum_are_raised_to_it(self):
        assert fit(1000, 2000, width=10, min_width=160)[1] == 160

    def test_returns_none_when_the_minimum_does_not_fit(self):
        assert fit(1000, 1100, min_width=160) is None      # 76 available
        assert fit(1000, 1200, min_width=160) is not None  # 176 available

    def test_margin_is_counted_at_both_ends(self):
        assert fit(1000, 1176, min_width=160, margin=12) is None   # 152 available
        assert fit(1000, 1176, min_width=160, margin=8) is not None

    def test_zero_margin_fills_edge_to_edge(self):
        assert fit(1000, 2000, align="left", margin=0) == (1000, 1000)

    def test_empty_or_inverted_gap(self):
        assert fit(1000, 1000, min_width=0) is None
        assert fit(2000, 1000, min_width=0) is None

    @pytest.mark.parametrize("align", ALIGNMENTS)
    def test_every_alignment_stays_inside_the_gap(self, align):
        for width in (None, 100, 500, 2000, 99999):
            box = fit(788, 3404, align=align, margin=12, min_width=160, width=width)
            assert box is not None
            x, w = box
            assert 788 + 12 <= x
            assert x + w <= 3404 - 12

    def test_rejects_bad_arguments(self):
        with pytest.raises(ValueError):
            fit(0, 1000, align="middle")
        with pytest.raises(ValueError):
            fit(0, 1000, margin=-1)
        with pytest.raises(ValueError):
            fit(0, 1000, min_width=-1)
        with pytest.raises(ValueError):
            fit(0, 1000, width=0)


class TestStripCredibility:
    """The reported button strip can be too narrow for the buttons it holds.

    Measured on Windows 11 at 250% with nine windows open: the strip claimed
    878 physical pixels, which is 39 logical pixels per button, when the
    narrowest a button gets is about 44. Trusting it put the gap's left edge on
    top of four real icons.
    """

    def test_the_real_failure_is_rejected(self):
        assert strip_can_hold(140, 1018, 9, 2.5) is False

    def test_a_strip_with_room_is_accepted(self):
        assert strip_can_hold(140, 3000, 9, 2.5) is True

    def test_exactly_at_the_floor_is_accepted(self):
        # 9 buttons x 40 logical x 2.5 = 900 exactly: possible, so not rejected.
        assert strip_can_hold(0, 900, 9, 2.5) is True
        assert strip_can_hold(0, 899, 9, 2.5) is False

    @pytest.mark.parametrize("count,scale", [(0, 2.5), (-1, 2.5), (9, 0)])
    def test_no_evidence_means_no_accusation(self, count, scale):
        # With nothing to contradict the strip, it stands. The check exists to
        # catch impossibility, not to second-guess on principle.
        assert strip_can_hold(140, 200, count, scale) is True

    def test_a_hidpi_strip_is_judged_in_its_own_units(self):
        # 9 buttons need 360 logical px; the same pixel width passes at 1x and
        # fails at 2x, which is the whole point of scaling the threshold.
        assert strip_can_hold(0, 400, 9, 1.0) is True
        assert strip_can_hold(0, 400, 9, 2.0) is False


class TestEstimateButtonEdge:
    def test_matches_the_observed_layout(self):
        # The machine this was found on: strip starts at 140, nine windows,
        # 250%. The estimate lands where the icons visibly end.
        assert estimate_button_edge(140, 9, 2.5) == 1130

    def test_scales_with_dpi(self):
        assert estimate_button_edge(0, 10, 1.0) == 440
        assert estimate_button_edge(0, 10, 2.0) == 880

    def test_no_buttons_leaves_the_edge_alone(self):
        assert estimate_button_edge(140, 0, 2.5) == 140
