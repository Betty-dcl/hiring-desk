"""The note that goes to the person who applied.

This is the only output of the system a candidate ever sees, and the only one
where a wrong word does damage that no later correction reaches. So the tests
are mostly prohibitions.
"""

from __future__ import annotations

import pytest

from intake.cv import Fact, Facts
from intake.posting import DraftAgenda, DraftCriterion
from reply import note
from screen import Assessment, Screening


def setup(*assessments):
    s = Screening(candidate_id="c", posting_id="p")
    s.assessments = list(assessments)
    facts = Facts(candidate_id="c", name="C", facts=[
        Fact("f1", "experience", "Rebuilt the process", "rebuilt the process after the migration",
             evidence="instance")])
    agenda = DraftAgenda(posting_id="p", title="t", criteria=[
        DraftCriterion(id="k", question="Show me a workflow you automated",
                       source_quote="Extreme ownership.", section="requirement", weight=2.0),
        DraftCriterion(id="silent", question="Have you worked at a startup before?",
                       source_quote="Startup experience", section="nice_to_have", weight=1.0),
    ])
    return s, facts, agenda


STRONG = Assessment("k", "strong", ["f1"], "r", weight=2.0)
SILENT = Assessment("silent", "unknown", [], "", weight=1.0)


# --------------------------------------------------------------------------
# What it must never say
# --------------------------------------------------------------------------

@pytest.mark.parametrize("word", [
    "reject", "rejected", "unfortunately", "regret", "unsuccessful",
    "we have decided", "not a fit", "shortlist", "hire",
])
def test_the_note_contains_no_decision(word):
    text = note(*setup(STRONG, SILENT)).lower()
    assert word not in text


def test_the_note_never_compares_the_candidate_to_anyone_else():
    text = note(*setup(STRONG, SILENT)).lower()
    for word in ("other candidates", "ranked", "rank ", "stronger than", "compared"):
        assert word not in text


def test_a_silent_criterion_is_not_framed_as_a_failing():
    text = note(*setup(STRONG, SILENT))
    assert "not marks" in text
    assert "things the papers left open" in text


# --------------------------------------------------------------------------
# What it must say
# --------------------------------------------------------------------------

def test_the_note_says_a_machine_wrote_it():
    """Article 50 transparency, and a note that reads as personal when it is
    not is worse than no note."""
    assert "written by the screening system, not by a person" in note(*setup(STRONG))


def test_the_note_quotes_the_candidates_own_words_back():
    assert "rebuilt the process after the migration" in note(*setup(STRONG))


def test_the_note_quotes_the_employers_words_for_what_was_asked():
    assert "Extreme ownership." in note(*setup(STRONG))


def test_a_silent_criterion_carries_what_would_have_answered_it():
    assert "Have you worked at a startup before?" in note(*setup(STRONG, SILENT))


def test_the_note_offers_a_route_to_a_person():
    text = note(*setup(STRONG))
    assert "reply and say which line" in text
    assert "goes to a person" in text


# --------------------------------------------------------------------------
# What it must not overstate
# --------------------------------------------------------------------------

def test_a_thin_criterion_says_the_claim_was_there_and_the_occasion_was_not():
    thin = Assessment("k", "weak", ["f1"], "r", weight=2.0, rests_on="assertions only")
    text = note(*setup(thin))
    assert "self-description only" in text
    assert "an occasion" in text


def test_the_interval_is_shown_when_it_has_been_measured():
    s, facts, agenda = setup(STRONG)
    assert "move this measurement by about 5%" in note(s, facts, agenda, spread=0.05)


def test_no_interval_is_claimed_when_none_was_measured():
    assert "move this measurement" not in note(*setup(STRONG))


def test_the_note_says_whether_a_person_has_read_it_yet():
    s, facts, agenda = setup(STRONG)
    assert "has not read this yet" in note(s, facts, agenda, reviewer_pending=True)
    assert "has not read this yet" not in note(s, facts, agenda, reviewer_pending=False)


def test_an_open_condition_is_named_as_open_not_as_failed():
    gate = Assessment("k", "unknown", [], "", weight=2.0, hard=True)
    text = note(*setup(gate))
    assert "leave open" in text
    assert "fail" not in text.lower()
