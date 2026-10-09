"""Deterministic protocol tests. No model, no network, no API key.

These exist to prove the rules hold on their own, before any agent is
allowed near them. Every test below is a rule from protocol.py's docstring.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from nbh.protocol import (
    Act,
    Criterion,
    Evidence,
    Exchange,
    Ledger,
    Message,
    ProtocolError,
    Side,
    Status,
    Strength,
)


def agenda() -> Ledger:
    return Ledger.from_criteria(
        [
            Criterion("ships_real_things", "Have they shipped something real, alone?", 3.0),
            Criterion("madrid", "Can they work on-site in Madrid?", 2.0, hard=True),
            Criterion("owns_ambiguity", "Are they comfortable with no scope handed to them?", 2.0),
        ]
    )


def opened(max_turns: int = 24) -> Exchange:
    """An exchange with both sides past their opening act. Company speaks next."""
    ex = Exchange(ledger=agenda(), max_turns=max_turns)
    ex.append(Message(1, Side.COMPANY, Act.OPEN, "Founding GTM, Madrid, on-site."))
    ex.append(Message(2, Side.CANDIDATE, Act.OPEN, "Here on the open door."))
    return ex


# --- rule 1: one typed act per turn, strict alternation -------------------

def test_company_opens_because_it_owns_the_agenda():
    assert Exchange(ledger=agenda()).next_speaker is Side.COMPANY


def test_alternation_falls_out_naturally_after_the_openings():
    assert opened().next_speaker is Side.COMPANY


def test_speaking_out_of_turn_is_refused():
    ex = Exchange(ledger=agenda())
    ex.append(Message(1, Side.COMPANY, Act.OPEN, "Hello."))
    with pytest.raises(ProtocolError, match="out of turn"):
        ex.append(Message(2, Side.COMPANY, Act.FLAG, "Me again."))


def test_turn_numbers_must_be_contiguous():
    ex = Exchange(ledger=agenda())
    with pytest.raises(ProtocolError, match="turn number"):
        ex.append(Message(7, Side.COMPANY, Act.OPEN, "Hello."))


def test_opening_act_comes_first_and_only_once():
    ex = Exchange(ledger=agenda())
    with pytest.raises(ProtocolError, match="may not play"):
        ex.append(Message(1, Side.COMPANY, Act.PROBE, "Madrid?", criterion_id="madrid"))


# --- rule 2: substance is anchored to a criterion -------------------------

def test_probe_without_a_criterion_is_malformed():
    with pytest.raises(ProtocolError, match="must name a criterion"):
        Message(3, Side.COMPANY, Act.PROBE, "Tell me about yourself.")


def test_unanchored_acts_may_not_carry_a_criterion():
    with pytest.raises(ProtocolError, match="must not name a criterion"):
        Message(3, Side.COMPANY, Act.FLAG, "Concern.", criterion_id="madrid")


def test_criteria_are_fixed_by_the_mandate():
    ex = opened()
    with pytest.raises(ProtocolError, match="unknown criterion"):
        ex.append(Message(3, Side.COMPANY, Act.PROBE, "Salary?", criterion_id="invented"))


# --- rule 3: termination is the exchange's call, not an agent's -----------

def test_turn_budget_ends_the_exchange():
    ex = Exchange(ledger=agenda(), max_turns=2)
    ex.append(Message(1, Side.COMPANY, Act.OPEN, "Hi."))
    ex.append(Message(2, Side.CANDIDATE, Act.OPEN, "Hi."))
    assert ex.is_over and ex.ended_reason == "turn budget exhausted"


def test_one_sided_close_does_not_end_anything():
    ex = opened()
    ex.append(Message(3, Side.COMPANY, Act.CLOSE, "I have enough."))
    assert not ex.is_over


def test_both_sides_closing_ends_it():
    ex = opened()
    ex.append(Message(3, Side.COMPANY, Act.CLOSE, "I have enough."))
    ex.append(Message(4, Side.CANDIDATE, Act.CLOSE, "Agreed."))
    assert ex.is_over and ex.ended_reason == "both sides closed"


def test_hard_blocker_ends_it_immediately():
    ex = opened()
    ex.append(Message(3, Side.COMPANY, Act.PROBE, "On-site Madrid?", criterion_id="madrid"))
    ex.ledger.mark_blocked("madrid")
    ex.append(Message(4, Side.CANDIDATE, Act.FLAG, "I cannot relocate."))
    assert ex.is_over and ex.ended_reason == "hard blocker"


# --- rule 4: neither side may write the other's ledger --------------------

def test_company_cannot_disclose_and_candidate_cannot_probe():
    ex = opened()
    assert Act.DISCLOSE not in ex.legal_acts(Side.COMPANY)
    assert Act.WITHHOLD not in ex.legal_acts(Side.COMPANY)
    assert Act.PROBE not in ex.legal_acts(Side.CANDIDATE)


def test_a_disclosure_must_be_recorded_with_a_strength():
    ex = opened()
    ex.append(Message(3, Side.COMPANY, Act.PROBE, "Shipped what?", criterion_id="ships_real_things"))
    with pytest.raises(ProtocolError, match="evidence strength"):
        ex.append(Message(4, Side.CANDIDATE, Act.DISCLOSE, "Lots.", criterion_id="ships_real_things"))


def test_a_weak_second_answer_never_downgrades_a_strong_one():
    ex = opened()
    ex.append(Message(3, Side.COMPANY, Act.PROBE, "Shipped?", criterion_id="ships_real_things"))
    ex.append(
        Message(4, Side.CANDIDATE, Act.DISCLOSE, "Built and shipped X solo.", criterion_id="ships_real_things"),
        evidence_strength=Strength.STRONG,
    )
    ex.append(Message(5, Side.COMPANY, Act.PROBE, "Say more?", criterion_id="ships_real_things"))
    ex.append(
        Message(6, Side.CANDIDATE, Act.DISCLOSE, "I helped out.", criterion_id="ships_real_things"),
        evidence_strength=Strength.NONE,
    )
    assert ex.ledger.evidence["ships_real_things"].strength is Strength.STRONG


# --- the arithmetic stays in code ----------------------------------------

def test_withholding_settles_a_criterion_without_earning_credit():
    ex = opened()
    ex.append(Message(3, Side.COMPANY, Act.PROBE, "Madrid?", criterion_id="madrid"))
    ex.append(Message(4, Side.CANDIDATE, Act.WITHHOLD, "Not saying yet.", criterion_id="madrid"))
    assert ex.ledger.status["madrid"] is Status.WITHHELD
    assert ex.ledger.status["madrid"].is_settled
    assert ex.ledger.fit_score() == 0.0  # settled is not the same as satisfied


def test_fit_score_is_weighted_and_hard_blocks_collapse_it():
    led = agenda()
    led.record(Evidence("ships_real_things", Strength.STRONG, "shipped X", 4))
    led.record(Evidence("owns_ambiguity", Strength.PARTIAL, "somewhat", 6))
    assert led.fit_score() == pytest.approx((3.0 * 1.0 + 2.0 * 0.5) / 7.0)

    led.mark_blocked("madrid")
    assert led.fit_score() == 0.0


def test_coverage_counts_settled_weight_not_answered_questions():
    led = agenda()
    led.mark_withheld("madrid")
    assert led.coverage() == pytest.approx(2.0 / 7.0)


def test_snapshot_is_stable_and_diffable():
    ex = opened()
    before = ex.ledger.snapshot()
    ex.append(Message(3, Side.COMPANY, Act.PROBE, "Shipped?", criterion_id="ships_real_things"))
    ex.append(
        Message(4, Side.CANDIDATE, Act.DISCLOSE, "Shipped X solo.", criterion_id="ships_real_things"),
        evidence_strength=Strength.STRONG,
    )
    after = ex.ledger.snapshot()
    assert before["status"]["ships_real_things"] == "open"
    assert after["status"]["ships_real_things"] == "resolved"
    assert after["fit_score"] > before["fit_score"]
