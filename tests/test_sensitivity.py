"""How much of a ranking is the evidence, and how much is the constant.

The risk this guards against is the quiet one: a number that looks like a
measurement of a person and is partly a measurement of a choice nobody
remembers making.
"""

from __future__ import annotations

import pytest

from harness.sensitivity import SCALES, Sensitivity, score_under, sensitivity
from screen import Assessment, Screening


def screening(cid: str, **strengths) -> Screening:
    s = Screening(candidate_id=cid, posting_id="p")
    s.assessments = [Assessment(k, v, ["f"] if v != "unknown" else [], "", weight=2.0)
                     for k, v in strengths.items()]
    return s


def test_a_cohort_of_only_strong_and_unknown_is_immune_to_the_scale():
    """The constants price partial answers, so a cohort with none is unmoved."""
    s = screening("a", x="strong", y="unknown")
    assert len({score_under(s, c) for c in SCALES.values()}) == 1


def test_the_swing_is_the_widest_gap_between_scales():
    out = sensitivity("p", [screening("a", x="moderate", y="weak")])
    lo = min(out.scores["a"].values())
    hi = max(out.scores["a"].values())
    assert out.swing("a") == pytest.approx(hi - lo)


def test_a_ranking_that_survives_every_scale_is_reported_as_stable():
    out = sensitivity("p", [screening("hi", x="strong"), screening("lo", x="weak")])
    assert out.order_is_stable
    assert "every scale produces the same order" in out.render()
    assert set(out.movement().values()) == {0}


def test_an_order_that_depends_on_the_constants_is_the_headline():
    """Thin-but-broad against narrow-but-proven: the scale decides, not the evidence."""
    broad = screening("broad", a="weak", b="weak", c="weak", d="weak")
    narrow = screening("narrow", a="strong", b="unknown", c="unknown", d="unknown")
    out = sensitivity("p", [broad, narrow])
    assert not out.order_is_stable
    assert "THE ORDER DEPENDS ON THE SCALE" in out.render()
    assert out.movement()["broad"] == 1


def test_a_candidate_the_scale_moves_more_than_the_model_is_named():
    out = sensitivity("p", [screening("a", x="moderate", y="weak")], noise={"a": 0.01})
    assert out.louder_than_noise() == ["a"]
    assert "further than the model's own drift" in out.render()


def test_without_a_stability_record_no_comparison_is_claimed():
    """A swing with nothing to compare it against must not read as a verdict."""
    out = sensitivity("p", [screening("a", x="moderate")])
    assert out.louder_than_noise() == []
    assert "no candidate is moved further" in out.render()


def test_the_record_keeps_every_scale_it_used():
    """Someone re-reading this must be able to see which constants produced it."""
    d = sensitivity("p", [screening("a", x="moderate")]).to_dict()
    assert d["scales"]["strict"]["moderate"] == 0.5
    assert set(d["scores"]["a"]) == set(SCALES)


def test_an_empty_screening_scores_zero_rather_than_dividing_by_zero():
    assert score_under(Screening(candidate_id="a", posting_id="p"), SCALES["current"]) == 0.0
