"""The screener's rules, checked without a model.

The interesting ones are the assertion cap and the UNKNOWN discipline. Both
exist because of something that happened rather than something anticipated:
a synthetic candidate whose CV recites the posting's own requirements scored
76% and ranked second of five. Every quote she gave verified, because she had
genuinely written those words. These tests hold that door shut.
"""

from __future__ import annotations

import pytest

from intake.cv import Fact, Facts
from intake.posting import DraftAgenda, DraftCriterion
from screen import (
    ASSERTION_CAP,
    CREDIT,
    Assessment,
    Ranking,
    Screening,
    anonymise,
    screen,
)


def crit(cid="ships", weight=2.0, hard=False):
    return DraftCriterion(id=cid, question="Q?", source_quote="q", section="requirement",
                          weight=weight, hard=hard)


def agenda(*criteria):
    return DraftAgenda(posting_id="p", title="T", criteria=list(criteria or [crit()]))


def facts(*items):
    return Facts(candidate_id="c", name="A Person", facts=list(items))


def fact(fid="f1", evidence="instance", kind="project", claim="Built a thing"):
    return Fact(id=fid, kind=kind, claim=claim, source_quote="q", evidence=evidence)


class FakeClient:
    """Returns a fixed answer. The screener's job is what it does with one."""

    def __init__(self, assessments):
        self._a = assessments
        self.usage = None

    def structured(self, **_):
        return {"assessments": self._a}


def one(assessments, ag=None, fs=None, **kw) -> Screening:
    return screen(FakeClient(assessments), ag or agenda(), fs or facts(fact()),
                  model="test", **kw)


# --------------------------------------------------------------------------
# The assertion cap
# --------------------------------------------------------------------------

def test_an_instance_backed_strong_stands():
    s = one([{"criterion_id": "ships", "strength": "strong", "fact_ids": ["f1"], "reasoning": "r"}])
    a = s.assessments[0]
    assert a.strength == "strong" and not a.capped_from and a.rests_on == "instances"


def test_an_assertion_backed_strong_is_capped():
    """The Mara Velichko case, in one test."""
    s = one([{"criterion_id": "ships", "strength": "strong", "fact_ids": ["f1"], "reasoning": "r"}],
            fs=facts(fact(evidence="assertion")))
    a = s.assessments[0]
    assert a.strength == ASSERTION_CAP
    assert a.capped_from == "strong"
    assert a.rests_on == "assertions only"
    assert s.capped == ["ships"]


def test_one_instance_among_assertions_is_enough():
    s = one([{"criterion_id": "ships", "strength": "strong",
              "fact_ids": ["f1", "f2"], "reasoning": "r"}],
            fs=facts(fact("f1", "assertion"), fact("f2", "instance")))
    assert s.assessments[0].strength == "strong"


def test_the_cap_never_raises_a_score():
    """A weak assessment on assertions is already at the cap, not below it."""
    s = one([{"criterion_id": "ships", "strength": "weak", "fact_ids": ["f1"], "reasoning": "r"}],
            fs=facts(fact(evidence="assertion")))
    assert s.assessments[0].strength == "weak" and not s.assessments[0].capped_from


@pytest.mark.parametrize("kind", ["language", "education", "credential"])
def test_declared_kinds_are_not_capped(kind):
    """"English (C1)" is how that fact is stated. There is no instance form."""
    s = one([{"criterion_id": "ships", "strength": "strong", "fact_ids": ["f1"], "reasoning": "r"}],
            fs=facts(fact(evidence="assertion", kind=kind)))
    assert s.assessments[0].strength == "strong"


@pytest.mark.parametrize("kind", ["experience", "project", "skill", "other"])
def test_other_kinds_are_capped(kind):
    s = one([{"criterion_id": "ships", "strength": "strong", "fact_ids": ["f1"], "reasoning": "r"}],
            fs=facts(fact(evidence="assertion", kind=kind)))
    assert s.assessments[0].strength == ASSERTION_CAP


def test_an_unclassified_fact_defaults_to_assertion():
    """The conservative default, so a silent extractor cannot inflate a score."""
    assert Fact(id="f", kind="project", claim="c", source_quote="q").evidence == "assertion"


# --------------------------------------------------------------------------
# Evidence that does not exist
# --------------------------------------------------------------------------

def test_a_cited_fact_that_does_not_exist_voids_the_score():
    s = one([{"criterion_id": "ships", "strength": "strong", "fact_ids": ["nope"], "reasoning": "r"}])
    assert s.assessments[0].strength == "unknown"
    assert "do not exist" in s.dropped[0]["reason"]


def test_a_score_with_no_evidence_at_all_becomes_unknown():
    s = one([{"criterion_id": "ships", "strength": "strong", "fact_ids": [], "reasoning": "r"}])
    assert s.assessments[0].strength == "unknown"


def test_a_criterion_not_on_the_agenda_is_dropped():
    s = one([{"criterion_id": "invented", "strength": "strong", "fact_ids": ["f1"], "reasoning": "r"}])
    assert s.dropped[0]["reason"] == "not on the agenda"


def test_a_criterion_the_screener_skipped_becomes_unknown_not_absent():
    s = one([], ag=agenda(crit("a"), crit("b")))
    assert [a.strength for a in s.assessments] == ["unknown", "unknown"]


# --------------------------------------------------------------------------
# UNKNOWN
# --------------------------------------------------------------------------

def test_unknown_earns_nothing_and_is_not_a_low_score():
    s = one([{"criterion_id": "ships", "strength": "unknown", "fact_ids": [], "reasoning": "r"}])
    assert s.assessments[0].credit == 0.0
    assert s.open_questions == ["ships"]
    assert s.coverage == 0.0


def test_coverage_separates_a_silent_document_from_a_weak_one():
    """Same score, different stories. Coverage is what tells them apart."""
    silent = one([{"criterion_id": "a", "strength": "unknown", "fact_ids": [], "reasoning": ""},
                  {"criterion_id": "b", "strength": "unknown", "fact_ids": [], "reasoning": ""}],
                 ag=agenda(crit("a"), crit("b")))
    thin = one([{"criterion_id": "a", "strength": "weak", "fact_ids": ["f1"], "reasoning": ""},
                {"criterion_id": "b", "strength": "unknown", "fact_ids": [], "reasoning": ""}],
               ag=agenda(crit("a"), crit("b")))
    assert silent.coverage == 0.0 and thin.coverage == 0.5


def test_an_open_gate_is_reported_and_is_not_a_rejection():
    s = one([{"criterion_id": "ships", "strength": "unknown", "fact_ids": [], "reasoning": "r"}],
            ag=agenda(crit(hard=True)))
    assert s.open_gates == ["ships"]
    assert s.to_dict()["decides"] is False


# --------------------------------------------------------------------------
# What the screener is shown
# --------------------------------------------------------------------------

def test_the_screener_is_never_handed_the_document():
    """`Facts` has no field that could carry CV text. Enforced by the type."""
    assert not hasattr(Facts(candidate_id="c", name="n"), "text")


def test_the_name_and_contact_details_are_stripped():
    f = facts(fact("contact_phone", kind="other", claim="Lists phone number"),
              fact("contact_email", kind="other", claim="Lists email address"),
              fact("thing", kind="project", claim="Built a thing"))
    a = anonymise(f)
    assert a.name == ""
    assert [x.id for x in a.facts] == ["thing"]


def test_location_survives_because_a_posting_can_ask_for_it():
    f = facts(fact("contact_location", kind="other", claim="Lists location as Madrid, Spain"))
    assert [x.id for x in anonymise(f).facts] == ["contact_location"]


# --------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------

def make(cid, score_pairs):
    s = Screening(candidate_id=cid, posting_id="p")
    s.assessments = [Assessment(f"c{i}", st, ["f"], "", weight=1.0) for i, st in enumerate(score_pairs)]
    return s


def test_ranking_orders_by_score_then_coverage():
    a = make("a", ["strong", "unknown"])       # 0.5 score, 0.5 coverage
    b = make("b", ["strong", "weak"])          # 0.625 score, 1.0 coverage
    r = Ranking(posting_id="p", screenings=[a, b])
    assert [s.candidate_id for s in r.ordered] == ["b", "a"]


def test_coverage_breaks_a_tie():
    a = make("a", ["moderate", "unknown", "unknown"])
    b = make("b", ["weak", "weak", "unknown"])
    assert a.score == pytest.approx(0.2) and b.score == pytest.approx(0.1667, abs=1e-3)
    tie = make("tie", ["moderate", "unknown", "unknown"])
    tie.assessments[1] = Assessment("c1", "weak", ["f"], "", weight=0.0)
    r = Ranking(posting_id="p", screenings=[a, tie])
    assert r.ordered[0].coverage >= r.ordered[1].coverage


def test_a_ranking_states_that_it_decides_nothing():
    d = Ranking(posting_id="p", screenings=[make("a", ["strong"])]).to_dict()
    assert d["decides"] is False
    assert d["for_human_review"] is True
    assert d["no_reject_state"] is True


def test_credit_is_arithmetic_not_opinion():
    assert CREDIT["strong"] > CREDIT["moderate"] > CREDIT["weak"] > 0
    assert "unknown" not in CREDIT


# --------------------------------------------------------------------------
# What to ask next
# --------------------------------------------------------------------------

def _screening(**strengths):
    from screen import Assessment, Screening
    s = Screening(candidate_id="c", posting_id="p")
    s.assessments = [Assessment(k, v, ["f"] if v != "unknown" else [], "", weight=2.0)
                     for k, v in strengths.items()]
    return s


def test_leverage_is_ordered_by_what_would_move_most():
    from screen import leverage
    s = _screening(small="moderate", big="unknown")
    s.assessments[0].weight = 1.0
    levers = leverage(s)
    assert levers[0].criterion_id == "big"


def test_a_criterion_already_at_full_strength_is_not_a_lever():
    from screen import leverage
    assert [l.criterion_id for l in leverage(_screening(done="strong", open="unknown"))] \
        == ["open"]


def test_silence_and_thinness_are_different_questions():
    """One needs a question; the other needs an occasion. Saying so is the point."""
    from screen import leverage
    kinds = {l.criterion_id: l.kind for l in leverage(_screening(a="unknown", b="weak"))}
    assert kinds == {"a": "unanswered", "b": "thin"}


def test_an_unanswered_gate_comes_first_whatever_its_weight():
    from screen import leverage
    s = _screening(gate="unknown", heavy="unknown")
    s.assessments[0].weight, s.assessments[0].hard = 1.0, True
    s.assessments[1].weight = 5.0
    assert leverage(s)[0].criterion_id == "gate"


def test_an_answered_gate_does_not_jump_the_queue():
    from screen import leverage
    s = _screening(gate="moderate", heavy="unknown")
    s.assessments[0].weight, s.assessments[0].hard = 1.0, True
    s.assessments[1].weight = 5.0
    assert leverage(s)[0].criterion_id == "heavy"


def test_the_gain_is_the_arithmetic_and_nothing_else():
    from screen import leverage
    s = _screening(a="unknown", b="strong")
    assert leverage(s)[0].gain == pytest.approx(0.5)


def test_a_fully_answered_screening_says_so_rather_than_inventing_a_question():
    from screen import next_questions
    assert "nothing left to ask" in next_questions(_screening(a="strong"))


def test_the_question_printed_is_the_one_extracted_from_the_posting():
    from intake.posting import DraftAgenda, DraftCriterion
    from screen import next_questions
    agenda = DraftAgenda(posting_id="p", title="t", criteria=[
        DraftCriterion(id="a", question="Show me a workflow you automated",
                       source_quote="q", section="requirement", weight=2.0)])
    assert "Show me a workflow you automated" in next_questions(_screening(a="unknown"), agenda)


# --------------------------------------------------------------------------
# Walking a number back to both documents
# --------------------------------------------------------------------------

def _traceable():
    from intake.cv import Fact, Facts
    from intake.posting import DraftAgenda, DraftCriterion, Rejected
    from screen import Assessment, Screening
    s = Screening(candidate_id="c", posting_id="p")
    s.assessments = [Assessment("k", "strong", ["f1"], "because of the thing",
                                weight=2.0, hard=True)]
    facts = Facts(candidate_id="c", name="C", facts=[
        Fact("f1", "experience", "Rebuilt the process", "rebuilt the process",
             evidence="instance")],
        rejected=[Rejected("f9", "source_quote does not appear in the CV", "q")])
    agenda = DraftAgenda(posting_id="p", title="t", criteria=[
        DraftCriterion(id="k", question="q?", source_quote="Extreme ownership.",
                       section="requirement", hard=True,
                       hard_quote="This is a hard requirement.", weight=2.0)])
    return s, facts, agenda


def test_the_trace_shows_the_employers_sentence_and_the_candidates():
    from screen import trace
    s, facts, agenda = _traceable()
    out = trace(s, facts, agenda)
    assert "Extreme ownership." in out, "the posting's own words"
    assert "rebuilt the process" in out, "the candidate's own words"
    assert "instance" in out, "and how the second was read"


def test_the_trace_says_when_a_requirement_was_declared_hard_by_them():
    from screen import trace
    s, facts, agenda = _traceable()
    assert "This is a hard requirement." in trace(s, facts, agenda)


def test_the_trace_reports_what_was_discarded_too():
    """Provenance that shows only surviving evidence is an argument, not a record."""
    from screen import trace
    s, facts, agenda = _traceable()
    out = trace(s, facts, agenda)
    assert "discarded before" in out and "f9" in out


def test_a_cited_fact_that_is_missing_is_flagged_not_skipped():
    from screen import trace
    s, facts, agenda = _traceable()
    facts.facts = []
    assert "cited, but not in the fact set" in trace(s, facts, agenda)


def test_an_unknown_criterion_says_the_document_is_silent():
    from screen import Assessment, trace
    s, facts, agenda = _traceable()
    s.assessments = [Assessment("k", "unknown", [], "", weight=2.0)]
    assert "nothing in the document speaks to this" in trace(s, facts, agenda)


def test_the_trace_can_be_narrowed_to_one_criterion():
    from screen import Assessment, trace
    s, facts, agenda = _traceable()
    s.assessments.append(Assessment("other", "weak", [], "", weight=1.0))
    out = trace(s, facts, agenda, only="k")
    assert "other" not in out


def test_the_trace_never_claims_a_verified_quote_is_a_true_claim():
    """The sentence that keeps the whole thing honest."""
    from screen import trace
    s, facts, agenda = _traceable()
    assert "never that the words are true" in trace(s, facts, agenda)
