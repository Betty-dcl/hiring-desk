"""Whether the facts themselves hold still.

This module measures the upstream of every other measurement, so a bug here
is not a wrong number, it is a wrong attribution: drift blamed on the
screener that actually came from extraction.
"""

from __future__ import annotations

import pytest

from harness.extraction import ExtractionStability, Repeat
from intake.cv import Fact, Facts
from intake.posting import Rejected


def run(*facts: Fact, rejected: int = 0) -> Facts:
    return Facts(candidate_id="c", name="C", facts=list(facts),
                 rejected=[Rejected(f"r{i}", "quote not found", "q") for i in range(rejected)])


def fact(quote: str, evidence: str = "instance", fid: str = "x") -> Fact:
    return Fact(id=fid, kind="experience", claim="c", source_quote=quote, evidence=evidence)


def repeat(*runs: Facts) -> Repeat:
    return Repeat(candidate_id="c", runs=list(runs))


# --------------------------------------------------------------------------
# Matching on the quote, because ids are rewritten every run
# --------------------------------------------------------------------------

def test_the_same_quote_under_a_different_id_is_the_same_fact():
    r = repeat(run(fact("Ran monthly close", fid="a")),
               run(fact("Ran monthly close", fid="totally_different")))
    assert r.agreement == 1.0
    assert len(r.core) == 1 and r.fringe == []


def test_a_quote_only_one_run_noticed_is_fringe_not_core():
    r = repeat(run(fact("Ran monthly close")),
               run(fact("Ran monthly close"), fact("Also chaired the committee")))
    assert len(r.core) == 1 and len(r.fringe) == 1
    assert r.agreement == pytest.approx(0.5)


def test_whitespace_and_case_do_not_make_two_facts():
    r = repeat(run(fact("Ran   monthly close")), run(fact("ran monthly Close")))
    assert r.agreement == 1.0


# --------------------------------------------------------------------------
# The classification, which the cap depends on
# --------------------------------------------------------------------------

def test_a_sentence_that_changes_classification_is_reported():
    """instance vs assertion decides whether the cap fires. A flip is a defect."""
    r = repeat(run(fact("Extreme ownership", "assertion")),
               run(fact("Extreme ownership", "instance")))
    flips = r.evidence_flips()
    assert len(flips) == 1
    assert flips[0][1] == {"assertion": 1, "instance": 1}


def test_a_stable_classification_is_not_reported_as_a_flip():
    r = repeat(run(fact("Built the thing", "instance")),
               run(fact("Built the thing", "instance")))
    assert r.evidence_flips() == []


def test_the_report_says_plainly_when_nothing_flipped():
    s = ExtractionStability(n=2, repeats=[
        repeat(run(fact("Built the thing")), run(fact("Built the thing")))])
    assert "no sentence changed its classification" in s.render()


def test_the_report_leads_with_the_flips_when_there_are_any():
    s = ExtractionStability(n=2, repeats=[
        repeat(run(fact("Extreme ownership", "assertion")),
               run(fact("Extreme ownership", "instance")))])
    text = s.render()
    assert "classified differently between runs" in text
    assert "The cap depends on this" in text


# --------------------------------------------------------------------------
# What was thrown away
# --------------------------------------------------------------------------

def test_the_rejection_rate_is_visible_rather_than_buried():
    """A quote that cannot be found is a signal about the prompt, not noise."""
    r = repeat(run(fact("a"), rejected=1), run(fact("a"), rejected=3))
    assert r.rejected == [1, 3]
    assert r.yield_rate == pytest.approx(2 / 6, abs=1e-4)  # stored rounded to 4dp


def test_a_perfect_run_yields_one():
    assert repeat(run(fact("a"), fact("b"))).yield_rate == 1.0


def test_an_extraction_that_never_completed_does_not_divide_by_zero():
    r = Repeat(candidate_id="c", errors=["run 1: boom"])
    assert r.agreement == 0.0 and r.yield_rate == 0.0 and r.counts == []
    assert "nothing completed" in ExtractionStability(n=1, repeats=[r]).render()


# --------------------------------------------------------------------------
# Disagreeing about the document, versus cutting it differently
# --------------------------------------------------------------------------

def test_a_bullet_split_differently_is_not_a_disagreement():
    """The extractor is told to split compound bullets; two runs may split
    one sentence in two places and mean the same thing."""
    whole = repeat(run(fact("Built X and shipped it to four teams")),
                   run(fact("Built X"), fact("shipped it to four teams")))
    assert whole.agreement < 1.0, "the spans genuinely differ"
    assert whole.coverage == 1.0, "but nothing in the document went unaccounted for"
    assert whole.missed() == []


def test_content_one_run_never_saw_is_still_caught():
    r = repeat(run(fact("Ran monthly close")),
               run(fact("Ran monthly close"), fact("Chaired the audit committee")))
    assert r.coverage < 1.0
    assert any("chaired" in m for m in r.missed())


def test_coverage_and_agreement_agree_when_the_runs_are_identical():
    r = repeat(run(fact("a b c")), run(fact("a b c")))
    assert r.agreement == r.coverage == 1.0


def test_the_report_separates_the_two_numbers():
    s = ExtractionStability(n=2, repeats=[
        repeat(run(fact("Built X and shipped it")),
               run(fact("Built X"), fact("shipped it")))])
    text = s.render()
    assert "not accounted for at all" in text
    assert "the same sentence cut differently" in text
