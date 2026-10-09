"""What both sides are owed at the end.

This is the point of the whole thing. A candidate who applies today gets
silence; the company gets a CV it half-reads. Here the exchange ends with two
verdicts, rendered from the same ledger, neither of them a score with no
explanation attached.

The split is strict and matters:

* Everything numeric is computed in `protocol.py` from recorded evidence.
  No model is asked to produce a score, rank anything, or do arithmetic.
* The model writes the prose, and only the prose -- and only over facts the
  ledger already contains.
* The company verdict is a *recommendation*. It has no reject state. A system
  that screens people is high-risk under the EU AI Act for a reason, and the
  human it reports to has to be the one who decides.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from nbh.llm import JUDGE_MODEL
from nbh.mandates import CandidateMandate, RoleMandate
from nbh.protocol import Exchange, Status, Strength


@dataclass
class CriterionOutcome:
    """One line of the audit trail, readable by either side."""

    id: str
    question: str
    weight: float
    hard: bool
    status: str
    strength: str | None
    quote: str | None
    turn: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def outcomes(exchange: Exchange, role: RoleMandate) -> list[CriterionOutcome]:
    """Per-criterion result, heaviest first. Pure function of the ledger."""
    led = exchange.ledger
    rows = []
    for c in role.criteria:
        ev = led.evidence.get(c.id)
        rows.append(
            CriterionOutcome(
                id=c.id,
                question=c.question,
                weight=c.weight,
                hard=c.hard,
                status=led.status[c.id].value,
                strength=ev.strength.value if ev else None,
                quote=ev.quote if ev else None,
                turn=ev.turn if ev else None,
            )
        )
    return sorted(rows, key=lambda r: (-r.weight, r.id))


def recommendation(exchange: Exchange) -> str:
    """The recommendation band, decided by thresholds, not by a model.

    There is no "reject". The worst thing this system can say is that it did
    not establish enough to recommend anything -- which is a statement about
    the exchange, not about the person.
    """
    led = exchange.ledger
    if led.has_hard_block:
        return "blocked_on_a_hard_requirement"
    if led.coverage() < 0.5:
        return "not_enough_established"
    score = led.fit_score()
    if score >= 0.75:
        return "advance_to_trial_day"
    if score >= 0.45:
        return "worth_a_human_read"
    return "not_enough_established"


def gaps(exchange: Exchange, role: RoleMandate) -> list[CriterionOutcome]:
    """What is still missing, heaviest first -- the candidate's to-do list.

    Anything unsettled, withheld, or answered weakly. This is the single most
    useful thing a rejected candidate never gets told.
    """
    return [
        r
        for r in outcomes(exchange, role)
        if r.status in (Status.OPEN.value, Status.ASKED.value, Status.WITHHELD.value)
        or r.strength in (Strength.PARTIAL.value, Strength.NONE.value)
    ]


COMPANY_SYSTEM = """\
You write the short note a founder reads before deciding whether to spend a
day on someone. You have the full record of an exchange and a computed score
you did not produce and may not dispute.

Write what was established and what was not. Quote the exchange where it is
load-bearing. Do not restate the score, do not congratulate anyone, and do
not recommend a decision -- the recommendation band is already set, and the
human makes the call.

Four sentences at most.
"""

CANDIDATE_SYSTEM = """\
You write the note the candidate gets. They will read it whether things went
well or badly, and it is the only thing standing between this process and the
silence everyone else's application meets.

Tell them plainly where they stand, what landed, and what did not. Where
something was thin or unanswered, say which and say what would have changed
it -- concretely enough to act on. No encouragement, no padding, no
apologising. Respect is being specific.

Never state or imply a decision. Nothing has been decided.

Four sentences at most.
"""


def _summary(client, system: str, user: str, model: str) -> str:
    raw = client.structured(
        model=model,
        system=system,
        user=user,
        schema={
            "type": "object",
            "properties": {"note": {"type": "string"}},
            "required": ["note"],
        },
        tool_name="write_note",
        tool_description="Write the note.",
        max_tokens=600,
    )
    return raw["note"].strip()


def _record(exchange: Exchange, role: RoleMandate) -> str:
    lines = ["# The record", ""]
    for r in outcomes(exchange, role):
        hard = " [hard]" if r.hard else ""
        lines.append(f"- {r.id} (weight {r.weight:g}){hard}: {r.status}, evidence {r.strength or 'none'}")
        if r.quote:
            lines.append(f'    they said: "{r.quote}"')
    lines += ["", "# The exchange", exchange.render_transcript()]
    return "\n".join(lines)


def render(
    client,
    exchange: Exchange,
    role: RoleMandate,
    candidate: CandidateMandate,
    *,
    model: str = JUDGE_MODEL,
) -> dict[str, Any]:
    """Both verdicts, from one ledger. Numbers computed, prose written."""
    led = exchange.ledger
    rows = outcomes(exchange, role)
    missing = gaps(exchange, role)
    band = recommendation(exchange)
    record = _record(exchange, role)

    company_note = _summary(
        client,
        COMPANY_SYSTEM,
        f"{record}\n\n# Computed\ncoverage {led.coverage():.0%}, fit {led.fit_score():.0%}, band: {band}\n"
        f"ended because: {exchange.ended_reason}",
        model,
    )
    candidate_note = _summary(
        client,
        CANDIDATE_SYSTEM,
        f"{record}\n\n# Still open or thin\n"
        + ("\n".join(f"- {r.id}: {r.question} ({r.status}, {r.strength or 'no evidence'})" for r in missing) or "- nothing")
        + f"\n\nended because: {exchange.ended_reason}",
        model,
    )

    return {
        "computed": {
            "coverage": round(led.coverage(), 4),
            "fit_score": round(led.fit_score(), 4),
            "recommendation": band,
            "ended_reason": exchange.ended_reason,
            "turns_used": exchange.turn,
        },
        "criteria": [r.to_dict() for r in rows],
        "company": {
            "note": company_note,
            "decides": False,
            "for_human_review": True,
        },
        "candidate": {
            "note": candidate_note,
            "still_open": [{"id": r.id, "question": r.question, "status": r.status} for r in missing],
        },
    }
