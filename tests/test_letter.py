"""The cover letter, which is the document most written for the reader.

Every test here is about one risk: that a letter becomes a way to score
without having done anything. The quote discipline is inherited from the CV
path; what is new is the role taxonomy and the cap, and both are tested from
the direction of the abuse rather than the happy path.
"""

from __future__ import annotations

import pytest

from intake.cv import Fact, Facts
from intake.letter import Letter, merge, verify
from intake.posting import DraftCriterion
from screen import LETTER_CAP, Assessment, CREDIT


TEXT = """\
I spent the spring rebuilding our close process after the ERP migration,
which took four months and is still in use.
The gap in 2023 was a year caring for a parent, not a career break I chose.
Your open-door page says you read every application, which is why I am
writing one.
I am rigorous, fast-moving and take extreme ownership.
"""


def letter(text: str = TEXT, cid: str = "c") -> Letter:
    return Letter(id=cid, name="C", text=text)


def item(fid, quote, role, evidence="assertion", kind="other", claim="x"):
    return {"id": fid, "kind": kind, "claim": claim, "source_quote": quote,
            "role": role, "evidence": evidence}


# --------------------------------------------------------------------------
# What survives extraction
# --------------------------------------------------------------------------

def test_a_quote_not_in_the_letter_is_discarded():
    f = verify(letter(), [item("a", "I single-handedly saved the company.", "instance",
                               "instance")])
    assert f.facts == []
    assert f.rejected[0].reason == "source_quote does not appear in the letter"


def test_letter_facts_are_labelled_as_such_and_cannot_be_mistaken_for_cv_facts():
    f = verify(letter(), [item("gap", "The gap in 2023 was a year caring for a parent",
                               "explanation")])
    assert f.facts[0].source == "letter"
    assert f.facts[0].from_letter
    assert f.facts[0].id.startswith("letter_")
    assert "[from the letter, explanation]" in f.facts[0].render()


def test_an_unlabelled_sentence_becomes_the_least_generous_role():
    """A missing label most often hides the flattering kind."""
    f = verify(letter(), [item("x", "I am rigorous, fast-moving", "")])
    assert f.facts[0].role == "restatement"


def test_an_instance_that_is_not_an_instance_is_demoted():
    """The model may label a role and an evidence that contradict. Code wins."""
    f = verify(letter(), [item("x", "I am rigorous, fast-moving", "instance",
                               evidence="assertion")])
    assert f.facts[0].role == "restatement"


def test_a_real_occasion_in_the_letter_survives_as_an_instance():
    f = verify(letter(), [item("close", "rebuilding our close process after the ERP migration",
                               "instance", evidence="instance", kind="project")])
    assert f.facts[0].role == "instance" and f.facts[0].is_instance


# --------------------------------------------------------------------------
# Merging
# --------------------------------------------------------------------------

def test_merging_keeps_the_letter_facts_distinguishable():
    cv = Facts(candidate_id="c", name="C", facts=[Fact("job", "experience", "x", "q")])
    lt = verify(letter(), [item("gap", "The gap in 2023 was a year caring for a parent",
                                "explanation")])
    both = merge(cv, lt)
    assert [f.source for f in both.facts] == ["cv", "letter"]


def test_a_letter_repeating_the_cv_is_kept_rather_than_deduplicated():
    """That the letter repeats the CV is itself a fact about the letter."""
    cv = Facts(candidate_id="c", name="C",
               facts=[Fact("a", "other", "rigorous", "q", evidence="assertion")])
    lt = verify(letter(), [item("b", "I am rigorous, fast-moving", "restatement")])
    assert len(merge(cv, lt).facts) == 2


def test_two_different_people_cannot_be_merged():
    cv = Facts(candidate_id="one", name="A")
    lt = Facts(candidate_id="two", name="B")
    with pytest.raises(ValueError, match="two different people"):
        merge(cv, lt)


# --------------------------------------------------------------------------
# The cap, which is the whole point
# --------------------------------------------------------------------------

def test_the_letter_alone_cannot_establish_a_criterion(screened):
    """Even a genuine occasion, told only in the letter, stops at moderate."""
    a = screened(strength="strong", facts=[
        Fact("letter_x", "project", "x", "q", evidence="instance", source="letter",
             role="instance"),
    ])
    assert a.strength == LETTER_CAP
    assert a.capped_from == "strong"
    assert a.capped_why == "the letter only"


def test_the_cap_lifts_when_the_cv_backs_the_same_criterion(screened):
    a = screened(strength="strong", facts=[
        Fact("cv_x", "experience", "x", "q", evidence="instance"),
        Fact("letter_x", "project", "x", "q", evidence="instance", source="letter",
             role="instance"),
    ])
    assert a.strength == "strong" and not a.capped_from


def test_a_flattering_letter_is_capped_by_the_assertion_rule_first(screened):
    """Two caps exist; the report has to say which one fired."""
    a = screened(strength="strong", facts=[
        Fact("letter_x", "other", "x", "q", evidence="assertion", source="letter",
             role="restatement"),
    ])
    assert CREDIT[a.strength] <= CREDIT["weak"]
    assert a.capped_why in ("assertions only", "the letter only")


def test_an_unknown_is_not_promoted_by_the_cap_machinery(screened):
    a = screened(strength="unknown", facts=[
        Fact("letter_x", "other", "x", "q", evidence="instance", source="letter",
             role="explanation"),
    ])
    assert a.strength == "unknown" and not a.capped_from


@pytest.fixture
def screened():
    """Run one assessment through `screen.screen` with a stubbed model.

    The cap lives in Python after the model answers, so a stub is enough --
    and a stub is what makes it a test of the rule rather than of the model.
    """
    from intake.posting import DraftAgenda
    from screen import screen

    def go(*, strength: str, facts: list[Fact]) -> Assessment:
        agenda = DraftAgenda(posting_id="p", title="t", criteria=[
            DraftCriterion(id="k", question="q?", source_quote="s",
                           section="requirement", weight=2.0),
        ])
        bundle = Facts(candidate_id="c", name="C", facts=facts)

        class Stub:
            def structured(self, **kw):
                return {"assessments": [{
                    "criterion_id": "k", "strength": strength,
                    "fact_ids": [f.id for f in facts], "reasoning": "r",
                }]}

        return screen(Stub(), agenda, bundle, model="stub", anonymous=False).assessments[0]

    return go


def test_resting_on_the_letter_is_said_even_when_the_cap_did_not_have_to_fire(screened):
    """Two criteria on the same letter fact must not be labelled differently
    just because one arrived above the cap and the other at it."""
    one_fact = [Fact("letter_x", "project", "x", "q", evidence="instance",
                     source="letter", role="instance")]
    capped = screened(strength="strong", facts=one_fact)
    at_cap = screened(strength=LETTER_CAP, facts=one_fact)
    assert capped.rests_on == at_cap.rests_on == "the letter only"
    assert capped.capped_from == "strong" and not at_cap.capped_from
