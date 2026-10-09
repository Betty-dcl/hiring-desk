"""The reviewer's lever, and the marks it has to leave.

What is being defended here is mostly the audit trail. A human overriding a
machine is fine and expected; a human overriding a machine invisibly is the
thing that makes an ordering unarguable six months later, when somebody asks
why this candidate was above that one.
"""

from __future__ import annotations

import pytest

from human import (
    DEFAULT_HUMAN_WEIGHT,
    Override,
    Review,
    ReviewError,
    Reviewed,
    ReviewedRanking,
    apply,
)
from screen import Assessment, Screening


def screening(cid="candidate_a", **strengths) -> Screening:
    s = Screening(candidate_id=cid, posting_id="founders_associate")
    s.assessments = [
        Assessment(k, v, ["f1"] if v != "unknown" else [], "", weight=2.0)
        for k, v in (strengths or {"ships": "strong", "madrid": "unknown"}).items()
    ]
    return s


def override(cid="madrid", strength="strong", why="I spoke to her; she lives here.",
             reviewer="iris_vogel") -> Override:
    return Override(criterion_id=cid, strength=strength, why=why, reviewer=reviewer)


# --------------------------------------------------------------------------
# An override has to be accountable
# --------------------------------------------------------------------------

def test_an_override_without_a_reason_is_refused():
    with pytest.raises(ReviewError, match="no reason"):
        Override(criterion_id="madrid", strength="strong", why="", reviewer="iris_vogel")


def test_a_whitespace_reason_is_still_no_reason():
    with pytest.raises(ReviewError):
        Override(criterion_id="madrid", strength="strong", why="   ", reviewer="iris_vogel")


def test_an_anonymous_override_is_refused():
    with pytest.raises(ReviewError, match="who made it"):
        Override(criterion_id="madrid", strength="strong", why="because", reviewer="")


def test_an_anonymous_review_is_refused():
    with pytest.raises(ReviewError, match="reviewer"):
        Review(candidate_id="candidate_a", posting_id="p", reviewer="")


def test_an_invalid_strength_is_refused():
    with pytest.raises(ReviewError, match="not a strength"):
        Override(criterion_id="madrid", strength="excellent", why="w", reviewer="r")


def test_an_override_of_a_criterion_not_on_the_agenda_is_refused():
    r = Review(candidate_id="candidate_a", posting_id="p", reviewer="iris_vogel",
               overrides=[override(cid="invented")])
    with pytest.raises(ReviewError, match="not on this posting"):
        apply(screening(), r)


def test_a_review_for_the_wrong_candidate_is_refused():
    r = Review(candidate_id="someone_else", posting_id="p", reviewer="iris_vogel")
    with pytest.raises(ReviewError, match="review is for"):
        apply(screening(), r)


# --------------------------------------------------------------------------
# The machine's number survives
# --------------------------------------------------------------------------

def test_the_machine_score_is_preserved_beside_the_adjusted_one():
    s = screening()
    r = Review(candidate_id="candidate_a", posting_id="p", reviewer="iris_vogel", overrides=[override()])
    out = apply(s, r)
    assert out.machine_score == 0.5      # strong + unknown, over two equal weights
    assert out.adjusted_score == 1.0     # unknown lifted to strong
    assert out.machine_score == s.score  # and the screening itself is untouched


def test_every_change_is_recorded_with_its_reason_and_author():
    r = Review(candidate_id="candidate_a", posting_id="p", reviewer="iris_vogel", overrides=[override()])
    out = apply(screening(), r)
    assert len(out.changes) == 1
    c = out.changes[0]
    assert (c.criterion_id, c.was, c.now) == ("madrid", "unknown", "strong")
    assert c.reviewer == "iris_vogel" and c.why


def test_an_override_that_changes_nothing_is_not_recorded_as_a_change():
    r = Review(candidate_id="candidate_a", posting_id="p", reviewer="iris_vogel",
               overrides=[override(cid="ships", strength="strong")])
    assert apply(screening(), r).changes == []


def test_a_reviewer_may_lower_a_score_too():
    """The lever goes both ways, or it is not a lever."""
    s = screening(ships="strong", madrid="strong")
    r = Review(candidate_id="candidate_a", posting_id="p", reviewer="noam_castel",
               overrides=[override(cid="ships", strength="weak",
                                   why="The example he gave was someone else's work.")])
    out = apply(s, r)
    assert out.adjusted_score < out.machine_score
    assert out.changes[0].now == "weak"


def test_overriding_to_unknown_reopens_the_question():
    s = screening(ships="strong", madrid="strong")
    r = Review(candidate_id="candidate_a", posting_id="p", reviewer="ruth_adeyemi",
               overrides=[override(cid="ships", strength="unknown",
                                   why="On a call this did not hold up; treat it as unanswered.")])
    out = apply(s, r)
    assert out.adjusted_coverage < 1.0


# --------------------------------------------------------------------------
# The impression, and its weight
# --------------------------------------------------------------------------

def test_the_impression_is_mixed_at_the_declared_weight():
    s = screening(ships="strong", madrid="strong")  # machine 1.0
    r = Review(candidate_id="candidate_a", posting_id="p", reviewer="iris_vogel", impression=0.0)
    out = apply(s, r, human_weight=0.3)
    assert out.combined == pytest.approx(0.7)


def test_the_weight_is_reported_so_the_mix_can_be_undone():
    s = screening()
    r = Review(candidate_id="candidate_a", posting_id="p", reviewer="iris_vogel", impression=0.9)
    d = apply(s, r).to_dict()
    assert d["human_weight"] == DEFAULT_HUMAN_WEIGHT
    assert d["adjusted_score"] is not None and d["human_impression"] == 0.9


@pytest.mark.parametrize("bad", [-0.1, 1.1])
def test_an_impression_outside_the_range_is_refused(bad):
    with pytest.raises(ReviewError, match="outside"):
        Review(candidate_id="candidate_a", posting_id="p", reviewer="iris_vogel", impression=bad)


def test_an_out_of_range_weight_is_refused():
    with pytest.raises(ReviewError, match="human_weight"):
        apply(screening(), None, human_weight=1.5)


# --------------------------------------------------------------------------
# Not being reviewed is not a verdict
# --------------------------------------------------------------------------

def test_no_review_leaves_the_evidence_score_alone():
    out = apply(screening())
    assert out.combined == out.machine_score
    assert not out.reviewed


def test_an_unreviewed_candidate_is_not_scored_zero_for_it():
    """The failure this guards: the reviewer's queue becoming a ranking."""
    seen = apply(screening(ships="strong", madrid="strong"),
                 Review(candidate_id="candidate_a", posting_id="p", reviewer="iris_vogel", impression=1.0))
    unseen = apply(screening(cid="other", ships="strong", madrid="strong"))
    assert unseen.combined == pytest.approx(seen.combined)


def test_an_empty_review_counts_as_unreviewed():
    r = Review(candidate_id="candidate_a", posting_id="p", reviewer="iris_vogel")
    out = apply(screening(), r)
    assert r.is_empty and not out.reviewed


def test_the_ranking_names_who_nobody_has_read():
    rows = [
        apply(screening(cid="a", ships="strong", madrid="strong"),
              Review(candidate_id="a", posting_id="p", reviewer="iris_vogel", impression=0.9)),
        apply(screening(cid="b", ships="weak", madrid="unknown")),
    ]
    r = ReviewedRanking(posting_id="p", rows=rows)
    assert r.unreviewed == ["b"]
    assert "not yet reviewed by anyone" in r.render()


def test_the_ranking_still_decides_nothing():
    d = ReviewedRanking(posting_id="p", rows=[apply(screening())]).to_dict()
    assert d["decides"] is False and d["no_reject_state"] is True


def test_the_ranking_is_ordered_on_the_combined_figure():
    high = apply(screening(cid="high", ships="weak", madrid="weak"),
                 Review(candidate_id="high", posting_id="p", reviewer="iris_vogel", impression=1.0))
    low = apply(screening(cid="low", ships="moderate", madrid="unknown"))
    order = [r.candidate_id for r in ReviewedRanking(posting_id="p", rows=[low, high]).ordered]
    assert order == ["high", "low"]


def test_the_same_candidate_cannot_appear_twice_in_a_ranking():
    """A stale file once put one person in the list at two different scores."""
    rows = [apply(screening(cid="candidate_a")), apply(screening(cid="candidate_a"))]
    with pytest.raises(ReviewError, match="more than once"):
        ReviewedRanking(posting_id="p", rows=rows)
