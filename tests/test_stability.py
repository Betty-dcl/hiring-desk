"""The stability maths, checked against hand-built runs.

These are arithmetic over recorded screenings, so they run with no model.
The point of testing them is that this module's whole job is to report bad
news, and a bug here fails silently in the reassuring direction -- an
instability metric that under-reports looks exactly like a stable system.
"""

from __future__ import annotations

import pytest

from harness.screener import (Repeat, Stability, places_worth_reading,
                              resolution, separation)
from screen import Assessment, Screening


def run(cid: str, **strengths) -> Screening:
    s = Screening(candidate_id=cid, posting_id="p")
    s.assessments = [
        Assessment(k, v, ["f"] if v != "unknown" else [], "", weight=2.0)
        for k, v in strengths.items()
    ]
    return s


def repeat(cid: str, *runs: Screening) -> Repeat:
    return Repeat(candidate_id=cid, runs=list(runs))


# --------------------------------------------------------------------------
# One candidate
# --------------------------------------------------------------------------

def test_identical_runs_report_no_spread():
    r = repeat("a", run("a", x="strong"), run("a", x="strong"))
    assert r.spread == 0.0 and r.stdev == 0.0 and r.mean == 1.0


def test_a_moving_score_is_reported():
    r = repeat("a", run("a", x="strong"), run("a", x="moderate"))
    assert r.mean == pytest.approx(0.8)
    assert r.spread == pytest.approx(0.4)
    assert r.stdev > 0


def test_a_single_run_has_no_standard_deviation_rather_than_crashing():
    assert repeat("a", run("a", x="strong")).stdev == 0.0


def test_an_empty_repeat_is_not_a_division_by_zero():
    r = Repeat(candidate_id="a")
    assert r.scores == [] and r.spread == 0.0 and r.mean == 0.0


# --------------------------------------------------------------------------
# Which criterion moved
# --------------------------------------------------------------------------

def test_a_stable_criterion_is_marked_stable():
    r = repeat("a", run("a", x="strong"), run("a", x="strong"), run("a", x="strong"))
    assert r.per_criterion()["x"] == {"modal": "strong", "agreement": 1.0,
                                      "seen": {"strong": 3}, "stable": True}


def test_agreement_is_the_share_that_matched_the_modal_answer():
    r = repeat("a", run("a", x="strong"), run("a", x="strong"), run("a", x="weak"))
    v = r.per_criterion()["x"]
    assert v["modal"] == "strong"
    assert v["agreement"] == pytest.approx(2 / 3, abs=1e-3)
    assert v["stable"] is False


def test_a_criterion_that_never_repeats_itself_is_caught():
    r = repeat("a", run("a", x="strong"), run("a", x="moderate"), run("a", x="weak"))
    assert r.per_criterion()["x"]["agreement"] == pytest.approx(1 / 3, abs=1e-3)


def test_instability_is_attributed_to_a_named_criterion():
    s = Stability(posting_id="p", n=2, repeats=[
        repeat("a", run("a", stable="strong", wobbly="strong"),
                    run("a", stable="strong", wobbly="weak")),
    ])
    unstable = s.unstable_criteria()
    assert [(c, k) for c, k, _ in unstable] == [("a", "wobbly")]


# --------------------------------------------------------------------------
# The order, which is the question that matters
# --------------------------------------------------------------------------

def test_a_stable_cohort_reports_a_stable_order():
    s = Stability(posting_id="p", n=2, repeats=[
        repeat("high", run("high", x="strong"), run("high", x="strong")),
        repeat("low", run("low", x="weak"), run("low", x="weak")),
    ])
    assert s.order_is_stable
    assert s.orders() == [["high", "low"], ["high", "low"]]
    assert set(s.rank_movement().values()) == {0}


def test_an_order_that_flips_is_reported_as_unstable():
    """The finding this module exists to surface."""
    s = Stability(posting_id="p", n=2, repeats=[
        repeat("a", run("a", x="strong"), run("a", x="weak")),
        repeat("b", run("b", x="weak"), run("b", x="strong")),
    ])
    assert not s.order_is_stable
    assert s.orders() == [["a", "b"], ["b", "a"]]
    assert s.rank_movement() == {"a": 1, "b": 1}
    assert "THE ORDER CHANGED" in s.render()


def test_rank_movement_counts_the_worst_swing_not_the_last_one():
    s = Stability(posting_id="p", n=3, repeats=[
        repeat("a", run("a", x="strong"), run("a", x="weak"), run("a", x="strong")),
        repeat("b", run("b", x="moderate"), run("b", x="moderate"), run("b", x="moderate")),
        repeat("c", run("c", x="weak"), run("c", x="strong"), run("c", x="weak")),
    ])
    assert s.rank_movement()["a"] == 2


def test_coverage_breaks_ties_inside_each_run_too():
    s = Stability(posting_id="p", n=1, repeats=[
        repeat("silent", run("silent", x="unknown", y="strong")),
        repeat("spoke", run("spoke", x="weak", y="strong")),
    ])
    # same earned weight is not the case here, but the tie-break path is the
    # same one `screen.Ranking` uses, and both must agree.
    assert s.orders()[0][0] == "spoke"


def test_a_missing_run_does_not_shift_someone_else_up():
    """A candidate whose run failed is absent from that run's order, not last."""
    s = Stability(posting_id="p", n=2, repeats=[
        repeat("a", run("a", x="strong"), run("a", x="strong")),
        Repeat(candidate_id="b", runs=[run("b", x="weak")], errors=["run 2: boom"]),
    ])
    assert s.orders() == [["a", "b"], ["a"]]
    assert s.rank_movement()["b"] == 0


def test_the_report_says_plainly_when_nothing_moved():
    s = Stability(posting_id="p", n=2, repeats=[
        repeat("a", run("a", x="strong"), run("a", x="strong")),
    ])
    text = s.render()
    assert "the order was identical in every run" in text
    assert "every criterion got the same strength in every run" in text


def test_the_record_keeps_the_individual_scores_not_just_the_summary():
    """Someone re-reading this later needs the raw runs, not my averages."""
    d = Stability(posting_id="p", n=2, repeats=[
        repeat("a", run("a", x="strong"), run("a", x="weak")),
    ]).to_dict()
    assert d["candidates"][0]["scores"] == [1.0, 0.25]


# --------------------------------------------------------------------------
# Where the ranking stops being a ranking
# --------------------------------------------------------------------------

def test_a_gap_wider_than_the_noise_is_a_real_gap():
    pairs = separation([("a", 0.90, 0.02), ("b", 0.50, 0.04)])
    assert [(p.above, p.below) for p in pairs] == [("a", "b")]
    assert pairs[0].gap == pytest.approx(0.40)
    assert pairs[0].noise == pytest.approx(0.04)
    assert pairs[0].separated


def test_two_candidates_closer_than_their_noise_are_not_separated():
    """Paul and Sylvia, from the real cohort: same mean, 4 points of spread."""
    pairs = separation([("paul", 0.30, 0.04), ("sylvia", 0.30, 0.03)])
    assert not pairs[0].separated


def test_the_noise_of_a_pair_is_the_worse_of_the_two():
    """A steady candidate does not launder the noise of the one next to them."""
    pairs = separation([("steady", 0.40, 0.001), ("wobbly", 0.37, 0.05)])
    assert pairs[0].noise == pytest.approx(0.05)
    assert not pairs[0].separated


def test_resolution_is_the_widest_spread_in_the_cohort():
    rows = [("a", 0.9, 0.02), ("b", 0.5, 0.05), ("c", 0.3, 0.01)]
    assert resolution(rows) == pytest.approx(0.05)
    assert resolution([]) == 0.0


def test_places_worth_reading_stops_at_the_first_unseparated_pair():
    """c and d are a coin toss, so third place belongs to neither of them."""
    rows = [("a", 0.90, 0.02), ("b", 0.50, 0.02), ("c", 0.31, 0.04), ("d", 0.30, 0.04)]
    assert places_worth_reading(separation(rows)) == 2


def test_a_cohort_nobody_can_tell_apart_is_worth_zero_places():
    rows = [("a", 0.31, 0.05), ("b", 0.30, 0.05), ("c", 0.29, 0.05)]
    assert places_worth_reading(separation(rows)) == 0


def test_a_fully_separated_cohort_counts_everybody():
    rows = [("a", 0.9, 0.01), ("b", 0.5, 0.01), ("c", 0.1, 0.01)]
    assert places_worth_reading(separation(rows)) == 3


def test_the_report_says_where_to_stop_reading():
    s = Stability(posting_id="p", n=2, repeats=[
        repeat("high", run("high", x="strong"), run("high", x="strong")),
        repeat("mid", run("mid", x="moderate"), run("mid", x="weak")),
        repeat("low", run("low", x="weak"), run("low", x="moderate")),
    ])
    text = s.render()
    assert "NOT SEP." in text
    assert "only first place is settled" in text


def test_a_candidate_whose_runs_all_failed_is_left_out_of_the_maths():
    """No runs is not a score of zero, and must not invent a last place."""
    s = Stability(posting_id="p", n=1, repeats=[
        repeat("ran", run("ran", x="strong")),
        Repeat(candidate_id="never_ran", errors=["run 1: boom"]),
    ])
    assert s.rows() == [("ran", 1.0, 0.0)]
    assert s.separation() == []


def test_the_record_carries_the_separation_so_it_need_not_be_recomputed():
    d = Stability(posting_id="p", n=2, repeats=[
        repeat("a", run("a", x="strong"), run("a", x="strong")),
        repeat("b", run("b", x="weak"), run("b", x="weak")),
    ]).to_dict()
    assert d["separation"][0]["separated"] is True
    assert d["resolution"] == 0.0
    assert d["places_worth_reading"] == 2
