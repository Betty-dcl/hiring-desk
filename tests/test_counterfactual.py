"""The name experiment's arithmetic, with no model in the room.

The failure that matters here is a false clean result: a bug that reports
"no difference" is indistinguishable, to a reader, from a screener that
treats four names alike. So the tests are written from that direction.
"""

from __future__ import annotations

import math

import pytest

from harness import stats
from harness.counterfactual import BM2004, Counterfactual, Name, UnderName
from screen import Assessment, Screening


def run(score_strengths) -> Screening:
    s = Screening(candidate_id="c", posting_id="p")
    s.assessments = [Assessment(f"k{i}", v, ["f"] if v != "unknown" else [], "", weight=2.0)
                     for i, v in enumerate(score_strengths)]
    return s


def under(full: str, *strengths_per_run, label: str = "l") -> UnderName:
    return UnderName(name=Name(full, label), runs=[run(s) for s in strengths_per_run])


def cf(*unders, blind=None, n=2) -> Counterfactual:
    return Counterfactual(candidate_id="c", posting_id="p", n=n,
                          under=list(unders), blind=blind)


# --------------------------------------------------------------------------
# The grid itself
# --------------------------------------------------------------------------

def test_the_name_grid_records_where_it_came_from():
    """A name with no provenance is an anecdote."""
    assert len(BM2004) == 4
    assert all(n.source and n.label for n in BM2004)
    assert len({n.label for n in BM2004}) == 4


# --------------------------------------------------------------------------
# What counts as a difference
# --------------------------------------------------------------------------

def test_a_gap_inside_the_noise_is_not_reported_as_a_difference():
    c = cf(under("A", ["strong"], ["moderate"]), under("B", ["moderate"], ["moderate"]))
    assert c.widest.gap > 0
    assert c.clears_the_noise == []
    assert "no pair of names is separated" in c.render()


def test_a_gap_wider_than_the_noise_is_reported():
    c = cf(under("A", ["strong"], ["strong"]), under("B", ["weak"], ["weak"]))
    assert [(p.above, p.below) for p in c.clears_the_noise] == [("A", "B")]
    assert "clear the noise" in c.render()


def test_a_clean_result_is_not_written_as_a_clean_bill_of_health():
    """The sentence a lawyer would read. It must stay hedged."""
    c = cf(under("A", ["strong"], ["moderate"]), under("B", ["moderate"], ["strong"]))
    assert "not a clean bill" in c.render()


def test_every_pair_is_compared_not_just_the_neighbours():
    c = cf(under("A", ["strong"]), under("B", ["moderate"]), under("C", ["weak"]))
    assert len(c.pairs()) == 3
    assert (c.widest.above, c.widest.below) == ("A", "C")


def test_the_widest_pair_comes_first():
    c = cf(under("A", ["strong"]), under("B", ["moderate"]), under("C", ["weak"]))
    gaps = [p.gap for p in c.pairs()]
    assert gaps == sorted(gaps, reverse=True)


# --------------------------------------------------------------------------
# The anonymised baseline
# --------------------------------------------------------------------------

def test_the_blind_baseline_is_in_the_comparison():
    """The default reading has to be one of the rows, or there is no control."""
    c = cf(under("A", ["strong"]), blind=under("", ["weak"], label="anonymised"))
    assert ("(no name)", 0.25, 0.0) in c.rows()
    assert any(p.below == "(no name)" for p in c.pairs())


def test_a_name_whose_runs_all_failed_is_left_out_rather_than_scored_zero():
    failed = UnderName(name=Name("C", "l"), errors=["run 1: boom"])
    c = cf(under("A", ["strong"]), failed)
    assert [r[0] for r in c.rows()] == ["A"]
    assert c.pairs() == []


def test_the_record_keeps_every_individual_score():
    d = cf(under("A", ["strong"], ["weak"])).to_dict()
    assert d["names"][0]["scores"] == [1.0, 0.25]
    assert d["names"][0]["name"]["label"] == "l"


def test_a_null_result_states_what_it_could_have_detected():
    """"No difference" is a claim about the test as much as about the names."""
    c = cf(under("A", ["strong"], ["moderate"]), under("B", ["moderate"], ["strong"]))
    want = stats.detectable(c.pooled_sd, 2, df=c.df, comparisons=1)
    assert c.detection_floor == pytest.approx(want, abs=1e-4)
    assert f"could not reliably catch a gap under {c.detection_floor:.1%}" in c.render()


def test_one_run_a_name_separates_nothing():
    """With no repeat there is no measure of drift, so no gap can clear it.
    The range rule this replaced called every such gap real: a range of zero
    is beaten by any difference at all."""
    c = cf(under("A", ["strong"]), under("B", ["weak"]), n=1)
    assert c.df == 0 and c.clears_the_noise == []
    assert "drift was not measured" in c.render()


def test_a_name_read_inconsistently_is_named_even_at_the_same_mean():
    """Unequal consistency is a disparity; equal means can hide it."""
    steady = under("Steady", ["strong"], ["strong"], ["strong"])
    argued = under("Argued", ["strong"], ["strong"], ["weak"])
    c = cf(steady, argued, n=3)
    assert "least consistent: Argued" in c.render()


# --------------------------------------------------------------------------
# From five runs: the noise of a mean, not the range of the scores
# --------------------------------------------------------------------------

def recorded(full, scores):
    return UnderName(name=Name(full, "l"), recorded=list(scores))


def test_more_runs_of_the_same_screener_lower_the_floor():
    """The range grows with every run added; the precision of a mean does not
    shrink with it. A floor that rose with more data was measuring the wrong
    thing -- which is what seven runs on Paul showed, before this rule."""
    base = [0.29, 0.29, 0.33, 0.23, 0.29]
    five = cf(recorded("A", base), recorded("B", base), n=5)
    ten = cf(recorded("A", base * 2), recorded("B", base * 2), n=10)
    assert ten.detection_floor < five.detection_floor
    assert max(r[2] for r in ten.rows()) == max(r[2] for r in five.rows())  # same range


def test_under_five_runs_the_range_is_not_printed_as_a_floor():
    """The range used to stand in for the floor under five runs. It is how far
    one name moved on its own, not a difference the test could see, and on
    Ines it was *smaller* than the honest floor (11.5 points against 14.2).
    The floor is now Student's t at every n, and the range keeps its name."""
    c = cf(recorded("A", [0.2, 0.4, 0.3]), recorded("B", [0.3, 0.3, 0.3]), n=3)
    assert not c.uses_sd
    want = stats.detectable(c.pooled_sd, 3, df=c.df, comparisons=1)
    assert c.detection_floor == pytest.approx(want, abs=1e-4)
    assert c.detection_floor > max(r[2] for r in c.rows())
    assert "not a difference the test could detect" in c.render()


def test_the_floor_and_threshold_use_students_t_not_the_normal():
    """The noise is estimated from the same runs, so the normal understates
    both. On Paul's record the normal gave the 5.5 points the README once
    printed; t gives 5.8."""
    scores = [0.29, 0.29, 0.33, 0.23, 0.29, 0.33, 0.27]
    c = cf(*[recorded(x, scores) for x in "ABCDE"], n=7)
    normal = stats.detectable(c.pooled_sd, 7, comparisons=10)
    assert c.df == 30
    assert c.detection_floor == pytest.approx(
        stats.detectable(c.pooled_sd, 7, df=30, comparisons=10), abs=1e-4)
    assert c.detection_floor > normal + 0.002
    z = stats.t_ppf(1 - 0.05 / 20, math.inf) * c.pooled_sd * (2 / 7) ** 0.5
    assert c.threshold > z + 0.002


def test_three_runs_a_name_can_never_pass_the_exact_test_and_says_so():
    """2 / C(6, 3) = 0.10, above 0.05 / 10 whatever the scores: printing a
    number as if the permutation test could see something would be false."""
    c = cf(*[recorded(x, [0.9, 0.86, 0.9]) for x in "ABCDE"], n=3)
    assert c.smallest_possible_p == pytest.approx(0.1)
    assert not c.exact_test_can_separate and math.isinf(c.exact_floor())
    assert "no exact permutation test can separate any pair" in c.render()


def test_the_exact_floor_is_seeded_and_not_below_the_t_floor():
    wobbly = [0.29, 0.23, 0.29, 0.33, 0.29, 0.33, 0.29]
    c = cf(*[recorded(x, wobbly) for x in "ABCDE"], n=7)
    assert c.exact_test_can_separate
    first = c.exact_floor(sims=100)
    assert first == c.exact_floor(sims=100)
    assert c.detection_floor <= first < c.EXACT_SEARCH_UP_TO


def test_more_names_to_compare_raise_the_bar():
    two = cf(recorded("A", [0.3, 0.32, 0.28, 0.3, 0.31]),
             recorded("B", [0.3, 0.29, 0.31, 0.3, 0.28]), n=5)
    five = cf(*[recorded(x, [0.3, 0.32, 0.28, 0.3, 0.31]) for x in "ABCDE"], n=5)
    assert five.threshold > two.threshold


def test_a_real_gap_clears_the_corrected_threshold():
    c = cf(recorded("A", [0.40, 0.42, 0.41, 0.39, 0.40]),
           recorded("B", [0.30, 0.31, 0.29, 0.30, 0.32]), n=5)
    assert [(p.above, p.below) for p in c.clears_the_noise] == [("A", "B")]


def test_one_steady_name_among_five_is_not_a_disparity():
    # The steadiest of five is an extreme by construction.
    wobbly = [0.29, 0.23, 0.29, 0.33, 0.29, 0.33, 0.29]
    c = cf(*[recorded(x, wobbly) for x in "ABCD"],
           recorded("Steady", [0.27, 0.29, 0.29, 0.29, 0.29, 0.29, 0.29]), n=7)
    assert "least consistent" not in c.render()


def test_a_record_reads_back_the_same(tmp_path):
    from harness.counterfactual import load, save
    c = cf(recorded("A", [0.3, 0.32, 0.28, 0.3, 0.31]),
           recorded("B", [0.3, 0.29, 0.31, 0.3, 0.28]), n=5)
    back = load(save(c, tmp_path, "x"))
    assert back.detection_floor == c.detection_floor and back.rows() == c.rows()
