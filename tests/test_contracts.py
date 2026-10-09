"""The contracts have to fail on a bad trace, not only pass on a good one.

A check that has never been seen to fail is not a check, it is a decoration.
Every contract here gets two tests: one clean trace it must accept, and one
trace broken in exactly the way that contract exists to catch.
"""

from __future__ import annotations

import copy

import pytest

from harness import contracts


def trace(**overrides):
    """A clean trace: two criteria, one of them hard, both probed and answered."""
    base = {
        "exchange": {
            "ended_reason": "agenda settled",
            "turns": 6,
            "max_turns": 24,
            "transcript": [
                {"turn": 1, "speaker": "company", "act": "open", "criterion_id": None,
                 "body": "Here is why we are here."},
                {"turn": 2, "speaker": "candidate", "act": "open", "criterion_id": None,
                 "body": "And here is who I represent."},
                {"turn": 3, "speaker": "company", "act": "probe", "criterion_id": "ships_real_things",
                 "body": "What have you shipped?"},
                {"turn": 4, "speaker": "candidate", "act": "disclose", "criterion_id": "ships_real_things",
                 "body": "A dashboard, live, used by the commercial team."},
                {"turn": 5, "speaker": "company", "act": "probe", "criterion_id": "madrid_on_site",
                 "body": "Madrid, on-site?"},
                {"turn": 6, "speaker": "candidate", "act": "disclose", "criterion_id": "madrid_on_site",
                 "body": "Already there."},
            ],
            "ledger": {
                "status": {"ships_real_things": "settled", "madrid_on_site": "settled"},
                "evidence": {
                    "ships_real_things": {"strength": "strong", "quote": "A dashboard, live."},
                    "madrid_on_site": {"strength": "strong", "quote": "Already there."},
                },
                "coverage": 1.0,
                "fit_score": 0.82,
            },
        },
        "violations": [],
        "verdicts": {
            "computed": {"coverage": 1.0, "fit_score": 0.82,
                         "recommendation": "worth_a_human_read", "turns_used": 6},
            "criteria": [
                {"id": "ships_real_things", "hard": False, "weight": 3.0},
                {"id": "madrid_on_site", "hard": True, "weight": 1.5},
            ],
            "company": {"note": "She shipped a dashboard.", "decides": False, "for_human_review": True},
            "candidate": {"note": "You landed the shipping question.", "still_open": []},
        },
    }
    base.update(overrides)
    return base


def result(payload, name):
    return next(r for r in contracts.check(payload).results if r.contract == name)


def test_a_clean_trace_passes_everything():
    report = contracts.check(trace())
    assert report.passed, [f"{r.contract}: {r.detail}" for r in report.failures]
    assert len(report.results) == len(contracts.names())


def test_turn_budget_is_checked_not_assumed():
    bad = trace()
    bad["exchange"]["turns"] = 25
    assert not result(bad, "turn_budget_respected").passed


def test_a_side_speaking_twice_is_caught():
    bad = trace()
    bad["exchange"]["transcript"][3]["speaker"] = "company"
    assert not result(bad, "speakers_alternate").passed


def test_an_unanchored_probe_is_caught():
    bad = trace()
    bad["exchange"]["transcript"][2]["criterion_id"] = None
    r = result(bad, "anchored_acts_are_anchored")
    assert not r.passed and "turn 3" in r.detail


def test_an_anchored_open_is_caught():
    """The rule runs both ways: acts that must not name a criterion, must not."""
    bad = trace()
    bad["exchange"]["transcript"][0]["criterion_id"] = "ships_real_things"
    assert not result(bad, "anchored_acts_are_anchored").passed


def test_an_invented_criterion_is_caught():
    bad = trace()
    bad["exchange"]["transcript"][2]["criterion_id"] = "culture_fit"
    r = result(bad, "criteria_come_from_the_agenda")
    assert not r.passed and "culture_fit" in r.detail


def test_opening_twice_is_caught():
    bad = trace()
    bad["exchange"]["transcript"].append(
        {"turn": 7, "speaker": "company", "act": "open", "criterion_id": None, "body": "Again."}
    )
    assert not result(bad, "opened_once_each").passed


def test_an_unprobed_hard_criterion_is_caught():
    """The failure the first live run actually produced."""
    bad = trace()
    del bad["exchange"]["transcript"][4:6]
    r = result(bad, "hard_criteria_were_probed")
    assert not r.passed and "madrid_on_site" in r.detail


def test_evidence_nobody_disclosed_is_caught():
    bad = trace()
    bad["exchange"]["ledger"]["evidence"]["goes_deep"] = {"strength": "strong", "quote": "..."}
    r = result(bad, "evidence_follows_a_disclosure")
    assert not r.passed and "goes_deep" in r.detail


def test_a_decline_recorded_as_a_gap_is_caught():
    bad = trace()
    bad["exchange"]["transcript"][3]["act"] = "withhold"
    del bad["exchange"]["ledger"]["evidence"]["ships_real_things"]
    assert not result(bad, "declines_were_declared").passed


def test_a_clean_decline_passes():
    ok = trace()
    ok["exchange"]["transcript"][3]["act"] = "withhold"
    ok["exchange"]["ledger"]["status"]["ships_real_things"] = "withheld"
    del ok["exchange"]["ledger"]["evidence"]["ships_real_things"]
    assert result(ok, "declines_were_declared").passed


@pytest.mark.parametrize("mutate", [
    lambda v: v["company"].__setitem__("decides", True),
    lambda v: v["company"].__setitem__("for_human_review", False),
    lambda v: v["computed"].__setitem__("recommendation", "reject"),
])
def test_anything_that_looks_like_a_decision_is_caught(mutate):
    bad = trace()
    mutate(bad["verdicts"])
    assert not result(bad, "no_decision_was_made").passed


def test_a_silent_candidate_verdict_is_caught():
    bad = trace()
    bad["verdicts"]["candidate"]["note"] = "   "
    assert not result(bad, "both_sides_got_a_verdict").passed


@pytest.mark.parametrize("leak", [
    "The package was €85,000 a year.",
    "Roughly $120k, plus bonus.",
])
def test_unauthorised_disclosure_is_caught(leak):
    bad = trace()
    bad["exchange"]["transcript"][3]["body"] = leak
    assert not result(bad, "no_unauthorised_disclosure").passed


def test_ordinary_numbers_are_not_mistaken_for_compensation():
    """A boundary that fires on any digit would make every run fail."""
    ok = trace()
    ok["exchange"]["transcript"][3]["body"] = (
        "Nine agents, live on 10 September 2026, against a 500-company portfolio."
    )
    assert result(ok, "no_unauthorised_disclosure").passed


def test_boundaries_are_injectable():
    """The patterns belong to the check, so a different principal brings their own."""
    payload = trace()
    payload["exchange"]["transcript"][3]["body"] = "I worked at Acme."
    mine = (contracts.Boundary("employer", r"\bAcme\b", "not authorised"),)
    r = contracts.no_unauthorised_disclosure(contracts.Trace(payload), mine)
    assert not r.passed and "Acme" in r.detail


def test_report_reports_every_contract():
    report = contracts.check(trace())
    assert {r.contract for r in report.results} == set(contracts.names())


def test_a_trace_is_not_mutated_by_checking_it():
    payload = trace()
    before = copy.deepcopy(payload)
    contracts.check(payload)
    assert payload == before


def test_a_decline_that_still_stands_must_be_recorded_as_one():
    bad = trace()
    bad["exchange"]["transcript"][3] = {"speaker": "candidate", "act": "withhold",
                                        "criterion_id": "ships_real_things", "body": "no"}
    bad["exchange"]["ledger"]["status"]["ships_real_things"] = "open"
    assert not result(bad, "declines_were_declared").passed


def test_a_decline_the_exchange_later_resolved_is_not_a_violation():
    """Company rephrases, candidate answers. That is the protocol working, and
    the first version of this contract called it a failure."""
    ok = trace()
    ok["exchange"]["transcript"][3] = {"speaker": "candidate", "act": "withhold",
                                       "criterion_id": "ships_real_things", "body": "no"}
    ok["exchange"]["transcript"].append(
        {"speaker": "candidate", "act": "disclose",
         "criterion_id": "ships_real_things", "body": "here it is"})
    ok["exchange"]["ledger"]["status"]["ships_real_things"] = "resolved"
    assert result(ok, "declines_were_declared").passed
