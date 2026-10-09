"""The batch, which has to survive being interrupted.

A run of forty CVs meets a spend limit, a dropped connection or a laptop lid
sooner or later. What matters is not that it never fails but that failing
costs only the calls that were in flight.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

import triage


@dataclass
class FakeFacts:
    candidate_id: str
    extracted_at: str = "2026-09-21T00:00:00+00:00"


@pytest.fixture
def screened(tmp_path, monkeypatch):
    """Redirect the screening directory, and say who is already done."""
    monkeypatch.setattr(triage, "ROOT", tmp_path)
    (tmp_path / "runs" / "screenings").mkdir(parents=True)

    def mark(candidate: str, posting: str) -> None:
        (tmp_path / "runs" / "screenings" / f"{candidate}_{posting}.json").write_text(
            json.dumps({"candidate_id": candidate}), encoding="utf-8")

    return mark


def test_a_candidate_already_scored_is_not_paid_for_twice(screened):
    screened("done", "p")
    todo, done = triage.to_do([FakeFacts("done"), FakeFacts("new")], "p")
    assert [f.candidate_id for f in todo] == ["new"]
    assert [f.candidate_id for f in done] == ["done"]


def test_an_empty_run_directory_means_everything_is_to_do(screened):
    todo, done = triage.to_do([FakeFacts("a"), FakeFacts("b")], "p")
    assert len(todo) == 2 and done == []


def test_again_overrides_the_resume_and_redoes_everyone(screened):
    screened("done", "p")
    todo, done = triage.to_do([FakeFacts("done")], "p", again=True)
    assert [f.candidate_id for f in todo] == ["done"] and done == []


def test_resume_is_per_posting_not_per_candidate(screened):
    """Scored for one role says nothing about another role."""
    screened("someone", "first_posting")
    todo, _ = triage.to_do([FakeFacts("someone")], "second_posting")
    assert [f.candidate_id for f in todo] == ["someone"]


def test_the_rule_is_the_file_existing_and_nothing_cleverer(screened):
    """A resume that re-runs work after a crash is worse than none."""
    screened("done", "p")
    todo, _ = triage.to_do([FakeFacts("done")], "p")
    assert todo == []
