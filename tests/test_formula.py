"""Choosing the scale from a spec written beforehand.

The failure to guard against is a search that always finds something: a
selector which reports a recommendation whatever the evidence is worth is a
random number with a justification attached.
"""

from __future__ import annotations

import pytest

from harness.formula import (Result, Scale, band_allows, check, must_outrank,
                             score, search)
from screen import Assessment, Screening


def screening(cid: str, **strengths) -> Screening:
    s = Screening(candidate_id=cid, posting_id="p")
    s.assessments = [Assessment(k, v, ["f"] if v != "unknown" else [], "", weight=2.0)
                     for k, v in strengths.items()]
    return s


# --------------------------------------------------------------------------
# Reading a band no more tightly than it was written
# --------------------------------------------------------------------------

@pytest.mark.parametrize("rank,ok", [(1, True), (2, False), (5, False)])
def test_first_means_first(rank, ok):
    assert band_allows("first", rank, 5) is ok


@pytest.mark.parametrize("rank,ok", [(1, False), (2, False), (3, True), (5, True)])
def test_bottom_half_is_the_lower_half(rank, ok):
    assert band_allows("bottom half", rank, 5) is ok


@pytest.mark.parametrize("rank,ok", [(1, False), (2, True), (4, True), (5, False)])
def test_middle_is_neither_first_nor_last(rank, ok):
    """Read literally. Tightening it here would invent a constraint."""
    assert band_allows("middle", rank, 5) is ok


def test_an_unwritten_band_is_an_error_not_a_pass():
    with pytest.raises(ValueError, match="unknown rank band"):
        band_allows("top third", 1, 5)


# --------------------------------------------------------------------------
# The check
# --------------------------------------------------------------------------

def test_a_satisfied_spec_reports_no_failures():
    order, failures = check({"a": 0.9, "b": 0.2}, {"a": "first"}, [("a", "b")])
    assert order == ["a", "b"] and failures == []


def test_a_violated_band_is_named_with_the_rank_it_got():
    _, failures = check({"a": 0.1, "b": 0.9}, {"a": "first"}, [])
    assert failures == ["a: first, got rank 2 of 2"]


def test_a_must_outrank_is_enforced_separately_from_the_bands():
    _, failures = check({"a": 0.1, "b": 0.9}, {}, [("a", "b")])
    assert failures == ["a must outrank b"]


def test_must_outrank_is_read_out_of_the_spec_file():
    spec = {"ines": {"must": ["she must outrank mara_velichko", "ai_native strong"]}}
    assert must_outrank(spec) == [("ines", "mara_velichko")]


# --------------------------------------------------------------------------
# The search, and its three honest outcomes
# --------------------------------------------------------------------------

def test_an_impossible_spec_yields_no_recommendation():
    """A weighted sum cannot put fewer strongs above more strongs."""
    cohort = [screening("more", a="strong", b="strong"),
              screening("fewer", a="strong", b="unknown")]
    r = Result(trials=search(cohort, {"fewer": "first"}, [], steps=6))
    assert r.passing == []
    assert r.best() is None and r.centre() is None
    assert "no scale satisfies" in r.verdict
    assert "upstream of the formula" in r.render()


def test_a_spec_every_scale_meets_is_reported_as_constraining_nothing():
    cohort = [screening("hi", a="strong"), screening("lo", a="weak")]
    r = Result(trials=search(cohort, {"hi": "first"}, [], steps=4))
    assert len(r.passing) == len(r.trials)
    assert "constrain nothing" in r.verdict


def test_a_constraining_spec_yields_bounds_on_the_constants():
    """Thin-and-broad below narrow-and-proven only holds for a low `weak`."""
    cohort = [screening("broad", a="weak", b="weak", c="weak", d="weak"),
              screening("proven", a="strong", b="unknown", c="unknown", d="unknown")]
    r = Result(trials=search(cohort, {"proven": "first"}, [], steps=10))
    assert 0 < len(r.passing) < len(r.trials)
    #: Four weaks must stay under one strong, so `weak` is bounded below a
    #: quarter. The ratio to `moderate` is unconstrained here, because this
    #: cohort contains no `moderate` at all -- which is exactly why the
    #: bounds are reported per constant rather than as a single number.
    assert r.bounds()["weak"][1] < 0.25


# --------------------------------------------------------------------------
# Two kinds of robustness, which are not the same
# --------------------------------------------------------------------------

def test_the_centre_of_the_region_is_not_the_widest_margin():
    """One is robust to a rounded constant, the other to a re-ranked cohort."""
    cohort = [screening("broad", a="weak", b="weak", c="weak", d="weak"),
              screening("proven", a="strong", b="unknown", c="unknown", d="unknown")]
    r = Result(trials=search(cohort, {"proven": "first"}, [], steps=10))
    assert r.centre() is not None and r.best() is not None
    assert r.best().margin() >= r.centre().margin()


def test_a_margin_thinner_than_the_drift_is_called_out():
    cohort = [screening("a", x="strong", y="moderate"), screening("b", x="strong", y="weak")]
    r = Result(trials=search(cohort, {"a": "first"}, [], steps=6), noise={"a": 0.9, "b": 0.9})
    assert "less than the instrument can resolve" in r.render()


def test_the_record_says_which_scale_it_recommends_and_why_it_is_sound():
    cohort = [screening("hi", a="strong"), screening("lo", a="weak")]
    d = Result(trials=search(cohort, {"hi": "first"}, [], steps=4)).to_dict()
    assert d["recommended"]["strong"] == 1.0
    assert "order" in d["recommended"] and "margin" in d["recommended"]


def test_scoring_is_unchanged_when_the_scale_is_the_shipped_one():
    from screen import CREDIT
    s = screening("a", x="strong", y="moderate", z="weak", w="unknown")
    mine = score(s, Scale(moderate=CREDIT["moderate"], weak=CREDIT["weak"]))
    assert mine == pytest.approx(s.score, abs=1e-6)
