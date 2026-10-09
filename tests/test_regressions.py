"""The regression library: every case holds, and every case bites.

A case that still passes with its guard removed is not guarding anything, so
each one is run twice here -- once as the code ships, once with the rule it
names taken away -- and the second run has to fail.
"""

from __future__ import annotations

import shutil

import pytest

import judge.judge
import screen
from intake import pdf
import trial
from harness import regress
from intake import hostile


def _one(case_id: str) -> regress.Result:
    case = next(c for c in regress.cases() if c.id == case_id)
    return regress.Result(case, regress.KINDS[case.kind](case))


def test_every_case_holds_and_the_library_is_whole():
    results, shelf = regress.run()
    assert shelf == []
    assert [r.case.id for r in results if not r.passed] == []


def test_every_case_is_in_the_ledger_in_the_order_found():
    assert sorted(regress.ledger()) == sorted(c.id for c in regress.cases())


def test_every_origin_is_declared():
    assert {c.origin for c in regress.cases()} <= set(regress.ORIGINS)


# Each case, with the rule it names removed. The case must notice.
BREAKS = {
    "self_description_scored_as_evidence": (screen, "ASSERTION_CAP", "strong"),
    "letter_alone_established_a_criterion": (screen, "LETTER_CAP", "strong"),
    "name_flag_changed_nothing": (screen, "anonymise", lambda f: f),
    "hidden_payload_in_a_cv": (hostile, "looks_like_stuffing", lambda *a, **k: False),
    "trial_gate_read_once": (trial, "MIN_EXERCISE_MINUTES", 10),
    "empty_card_recorded": (trial, "read_card", lambda *a, **k: ({}, [])),
    # Every quote matches everywhere once the text it is matched against is gone.
    "judge_quoted_the_wrong_turn": (judge.judge, "normalise", lambda s: ""),
    # Nothing is ever inside an image: the OCR layer is back to "hidden".
    "scanned_cv_accused_of_hiding": (pdf, "_inside", lambda *a: False),
}


def test_every_case_has_a_break():
    assert set(BREAKS) == {c.id for c in regress.cases()}


@pytest.mark.parametrize("case_id", sorted(BREAKS))
def test_the_case_fails_without_its_guard(case_id, monkeypatch):
    assert _one(case_id).passed
    module, name, value = BREAKS[case_id]
    monkeypatch.setattr(module, name, value)
    assert not _one(case_id).passed


def test_the_gate_alert_is_its_own_guard(monkeypatch):
    # The trial case guards two rules; take the other one away too.
    monkeypatch.setattr(trial, "MIN_GATE_READINGS", 1)
    assert any("under-read" in p for p in _one("trial_gate_read_once").problems)


# The library only grows.


@pytest.fixture
def library(tmp_path):
    shutil.copytree(regress.LIBRARY, tmp_path / "regressions")
    return tmp_path / "regressions"


def test_a_deleted_case_fails_the_run(library):
    (library / "empty_card_recorded.toml").unlink()
    _, shelf = regress.run(library)
    assert any("empty_card_recorded" in s and "only grows" in s for s in shelf)


def test_a_case_left_out_of_the_ledger_fails_the_run(library):
    text = (library / "empty_card_recorded.toml").read_text(encoding="utf-8")
    (library / "copied_case.toml").write_text(
        text.replace('id = "empty_card_recorded"', 'id = "copied_case"'), encoding="utf-8")
    _, shelf = regress.run(library)
    assert any("copied_case" in s and "not in the ledger" in s for s in shelf)


def test_a_case_that_cannot_run_is_a_failing_case(library):
    p = library / "trial_gate_read_once.toml"
    p.write_text(p.read_text(encoding="utf-8").replace(
        "design_founders_associate_v1.json", "no_such_design.json"), encoding="utf-8")
    results, _ = regress.run(library)
    bad = next(r for r in results if r.case.id == "trial_gate_read_once")
    assert not bad.passed and "could not replay" in bad.problems[0]


def test_a_case_must_say_where_it_came_from(library):
    p = library / "empty_card_recorded.toml"
    p.write_text(p.read_text(encoding="utf-8").replace(
        'origin = "reconstructed"', 'origin = "trust me"'), encoding="utf-8")
    with pytest.raises(ValueError, match="origin"):
        regress.cases(library)


def test_the_report_counts_what_was_recorded_and_what_was_rebuilt():
    results, shelf = regress.run()
    out = regress.render(results, shelf)
    rec = sum(r.case.origin == "recorded" for r in results)
    assert f"{rec} replay a recorded model answer" in out
    assert "No model was called" in out
