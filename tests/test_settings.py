"""The answers a posting never contains.

The failure this guards against is a default that reads like a decision: a
score computed with weights nobody chose, presented exactly like a score
computed with weights somebody argued about.
"""

from __future__ import annotations

import pytest

import settings as settings_mod
from intake.posting import DraftAgenda, DraftCriterion
from settings import DEFAULT_SCALE, Settings


def agenda():
    return DraftAgenda(posting_id="p", title="t", criteria=[
        DraftCriterion(id="a", question="q", source_quote="s", section="requirement",
                       weight=2.0),
        DraftCriterion(id="b", question="q", source_quote="s", section="nice_to_have",
                       weight=1.0),
    ])


# --------------------------------------------------------------------------
# Unanswered is a state, not a value
# --------------------------------------------------------------------------

def test_an_empty_record_says_nobody_answered():
    s = Settings(posting_id="p")
    assert not s.answered
    assert "nobody has set" in s.render()


def test_weights_nobody_set_are_named_rather_than_hidden():
    s = Settings(posting_id="p", weights={"a": 3.0}, answered_by="x")
    assert s.unanswered_criteria(agenda()) == ["b"]


def test_an_unanswered_scale_falls_back_to_the_default_rather_than_to_zero():
    assert Settings(posting_id="p").credit() == DEFAULT_SCALE


def test_answers_without_a_name_do_not_count_as_answered():
    """A settings file is a claim about a company, so it says who made it."""
    assert not Settings(posting_id="p", weights={"a": 3.0}).answered


# --------------------------------------------------------------------------
# Applying them
# --------------------------------------------------------------------------

def test_applying_weights_leaves_the_extracted_agenda_alone():
    """The posting's record must stay what the posting said."""
    original = agenda()
    Settings(posting_id="p", weights={"a": 3.0}, answered_by="x").apply(original)
    assert original.criteria[0].weight == 2.0


def test_a_set_weight_is_no_longer_marked_as_needing_confirmation():
    out = Settings(posting_id="p", weights={"a": 3.0}, answered_by="x").apply(agenda())
    assert out.criteria[0].weight == 3.0 and out.criteria[0].needs_confirmation is False


def test_a_criterion_left_out_keeps_the_extractors_weight():
    out = Settings(posting_id="p", weights={"a": 3.0}, answered_by="x").apply(agenda())
    assert out.criteria[1].weight == 1.0
    assert out.criteria[1].needs_confirmation is True


def test_a_company_declared_condition_says_it_came_from_a_person():
    """A gate the posting did not declare must not look like one that it did."""
    out = Settings(posting_id="p", gates=["b"], answered_by="iris").apply(agenda())
    assert out.criteria[1].hard is True
    assert "iris" in out.criteria[1].hard_quote
    assert "not by the posting" in out.criteria[1].hard_quote


# --------------------------------------------------------------------------
# Round trip
# --------------------------------------------------------------------------

def test_the_file_survives_a_save_and_a_load(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_mod, "ROOT", tmp_path)
    s = Settings(posting_id="p", weights={"a": 3.0}, scale={"strong": 1.0, "moderate": 0.8,
                                                           "weak": 0.1},
                 gates=["a"], answered_by="iris", notes="on-site is not negotiable")
    settings_mod.save(s)
    back = settings_mod.load("p")
    assert back.weights == {"a": 3.0} and back.gates == ["a"]
    assert back.credit()["moderate"] == 0.8
    assert back.answered_by == "iris" and back.answered_at
    assert back.notes == "on-site is not negotiable"


def test_a_missing_file_is_an_unanswered_record_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_mod, "ROOT", tmp_path)
    assert settings_mod.load("never_configured").answered is False
