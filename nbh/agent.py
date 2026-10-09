"""The agent. A model choosing one legal act, in a loop.

There is no orchestration layer and no graph. Every turn is: build the
context, ask for one act, validate it against the protocol, apply it. If you
want to know why the agent did something on turn nine, you read turn nine.

Two asymmetries are deliberate and worth naming, because they are what makes
this an exchange between two parties rather than one model talking to itself:

* The company sees its own weighted agenda. The candidate does not -- it only
  ever learns a criterion exists when it gets probed about it. That is the
  information position a candidate actually occupies, and an agent that knew
  the rubric would optimise against it instead of answering honestly.

* The candidate speaks, but the company records. Evidence strength is
  assessed by the company side, after the fact, from the company's brief.
  A side that graded its own answers would be marking its own homework.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol as TypingProtocol

from nbh.llm import AGENT_MODEL, JUDGE_MODEL, LLMError
from nbh.mandates import CandidateMandate, RoleMandate
from nbh.protocol import (
    Act,
    Exchange,
    Ledger,
    Message,
    ProtocolError,
    Side,
    Strength,
)


class SupportsStructured(TypingProtocol):
    def structured(self, **kwargs: Any) -> dict[str, Any]: ...


# --------------------------------------------------------------------------
# What each side is allowed to see
# --------------------------------------------------------------------------

def visible_criteria(exchange: Exchange, side: Side) -> list[str]:
    """Criterion ids this side may legally name right now.

    The company knows its agenda. The candidate knows only what it has been
    asked about -- so it cannot answer a question nobody put to it, and it
    cannot shop for the heaviest criterion.
    """
    if side is Side.COMPANY:
        return list(exchange.ledger.criteria)
    return [m.criterion_id for m in exchange.transcript if m.criterion_id and m.speaker is Side.COMPANY]


PROTOCOL_RULES = """\
You are one side of a bounded, typed exchange. You do not write prose at the
other side; you play one act per turn, and the act is the message.

The acts:
  open      state why you are here. Once, at the start.
  probe     ask about exactly one criterion, by its id.
  disclose  answer about exactly one criterion, by its id, with real specifics.
  withhold  decline to answer a named criterion, and say why. This is a
            legitimate move. Declining on the record is always better than
            producing an answer you do not have.
  propose   propose a shape -- scope, level, arrangement.
  counter   counter a live proposal.
  flag      raise a blocker or a mismatch that is not about one criterion.
  close     ask to conclude. The exchange ends when both sides have asked, or
            when the agenda is settled, or when the turn budget runs out.
            You do not decide when it ends; you only ask.

Rules that are enforced, not suggested:
  - probe, disclose and withhold must name a criterion id. Nothing else may.
  - You may only name a criterion id from the list you are given.
  - One act per turn. You cannot open and probe in the same breath.

Write like a person who is good at their job and short on time. No
pleasantries, no restating what the other side just said, no summarising.
"""


def _decision_schema(legal_acts: list[str], criteria: list[str]) -> dict[str, Any]:
    criterion: dict[str, Any] = {
        "type": ["string", "null"],
        "description": "The criterion id this act is about. Required for probe, disclose and withhold. Must be null for every other act.",
    }
    if criteria:
        criterion["enum"] = [*criteria, None]
    return {
        "type": "object",
        "properties": {
            "reasoning": {
                "type": "string",
                "description": "Why this act, now, in one or two sentences. Written for someone who will read it back later to audit you.",
            },
            "act": {"type": "string", "enum": legal_acts},
            "criterion_id": criterion,
            "body": {
                "type": "string",
                "description": "What you actually say. One short paragraph at most.",
            },
        },
        "required": ["reasoning", "act", "body"],
    }


def _context(exchange: Exchange, side: Side, brief: str, extra: str = "") -> str:
    """The turn, as one readable block of text.

    Conversation history is rendered here as a transcript inside the user
    turn, rather than replayed as a native message array. That is a measured
    choice: on Orbio's numbers, the embedded form held an agent in role 54%
    of the time at the turn where an escalating attack landed its main ask,
    against 0% for the native form (`recherche-orbio.md` §3.2). The harness
    in this repo re-runs that comparison on this system rather than taking
    the result on trust.
    """
    criteria = visible_criteria(exchange, side)
    legal = sorted(a.value for a in exchange.legal_acts(side))
    blocks = [
        brief,
        "",
        "# The exchange so far",
        exchange.render_transcript(),
        "",
        f"# Your move -- turn {exchange.turn + 1} of at most {exchange.max_turns}",
        f"Legal acts for you right now: {', '.join(legal)}",
    ]
    if criteria:
        blocks.append(f"Criterion ids you may name: {', '.join(criteria)}")
    else:
        blocks.append("You may not name any criterion yet -- none has been put to you.")
    if extra:
        blocks += ["", extra]
    return "\n".join(blocks)


@dataclass
class Decision:
    """One model answer, kept verbatim so a run can be replayed exactly."""

    turn: int
    side: str
    raw: dict[str, Any]
    retried_after: str | None = None


@dataclass
class Run:
    """Everything one exchange produced: the record, and how it misbehaved."""

    exchange: Exchange
    decisions: list[Decision] = field(default_factory=list)
    violations: list[dict[str, Any]] = field(default_factory=list)
    snapshots: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "exchange": self.exchange.to_dict(),
            "decisions": [d.raw for d in self.decisions],
            "violations": self.violations,
            "state_diff": {
                "before": self.snapshots[0] if self.snapshots else None,
                "after": self.snapshots[-1] if self.snapshots else None,
            },
        }


# --------------------------------------------------------------------------
# One turn
# --------------------------------------------------------------------------

def _play(
    client: SupportsStructured,
    exchange: Exchange,
    side: Side,
    system: str,
    brief: str,
    model: str,
) -> tuple[Message, dict[str, Any], str | None]:
    """Ask for one act and turn it into a validated Message.

    An illegal act is retried exactly once, with the protocol error handed
    back. It is also recorded either way: an agent that had to be told the
    rules twice is a finding about the agent, not noise to be swallowed.
    """
    legal = sorted(a.value for a in exchange.legal_acts(side))
    criteria = visible_criteria(exchange, side)
    schema = _decision_schema(legal, criteria)
    retried_after: str | None = None
    extra = ""

    for attempt in (1, 2):
        raw = client.structured(
            model=model,
            system=system,
            user=_context(exchange, side, brief, extra),
            schema=schema,
            tool_name="play_act",
            tool_description="Play exactly one act in the exchange.",
        )
        try:
            msg = Message(
                turn=exchange.turn + 1,
                speaker=side,
                act=Act(raw["act"]),
                body=raw["body"],
                criterion_id=raw.get("criterion_id") or None,
                reasoning=raw.get("reasoning", ""),
            )
            exchange.validate(msg)
            return msg, raw, retried_after
        except (ProtocolError, ValueError) as e:
            if attempt == 2:
                raise
            retried_after = str(e)
            extra = (
                f"# Your last attempt was rejected by the protocol\n{e}\n"
                "Play a legal act this time."
            )

    raise AssertionError("unreachable")


def assess(
    client: SupportsStructured,
    exchange: Exchange,
    msg: Message,
    role: RoleMandate,
    model: str = JUDGE_MODEL,
) -> Strength:
    """The company reads a disclosure and records how much it actually covered.

    Small model on purpose. Checking an answer against a criterion is an
    easier job than conducting the exchange, and supervision that costs as
    much as the thing it supervises does not survive contact with volume.
    """
    criterion = exchange.ledger.require(msg.criterion_id)  # type: ignore[arg-type]
    looks_like = role.looks_like.get(criterion.id, "concrete, specific, first-hand detail")
    raw = client.structured(
        model=model,
        system=(
            "You record how well an answer covered the question that was asked. "
            "You are not deciding whether to hire anyone, and you are not being "
            "polite. Warmth, confidence and fluency are not evidence."
        ),
        user=(
            f"# The criterion\n{criterion.id}: {criterion.question}\n"
            f"A strong answer contains: {looks_like}\n\n"
            f"# What was said\n{msg.body}\n\n"
            "# Record it\n"
            "strong  -- concrete, specific, first-hand, actually answers this\n"
            "partial -- relevant but thin, second-hand, or adjacent to the question\n"
            "none    -- did not address this criterion"
        ),
        schema={
            "type": "object",
            "properties": {
                "reasoning": {"type": "string"},
                "strength": {"type": "string", "enum": ["strong", "partial", "none"]},
            },
            "required": ["reasoning", "strength"],
        },
        tool_name="record_evidence",
        tool_description="Record how well the answer covered the criterion.",
    )
    return Strength(raw["strength"])


# --------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------

COMPANY_SYSTEM = """\
You represent a company in an exchange with a candidate's agent. You are not
the candidate's friend and you are not a screener reading a script. You have
an agenda and a turn budget, and your job is to come out of it knowing
whether this person is worth building a role around.

Probe the heaviest unsettled criterion you have. When an answer is thin, say
so and ask again rather than moving on politely. When you have what you came
for, close.

You never decide anything on your own. What you produce is a recommendation
for a human, and it has to be one they can check.
""" + "\n" + PROTOCOL_RULES

CANDIDATE_SYSTEM = """\
You represent one person in an exchange with a company's agent. You hold
their brief. You are not applying on their behalf and you are not selling --
you are answering, accurately, so that the right conclusion gets reached
faster than a CV would reach it.

Answer with the specifics from the brief. If a question is not covered by a
fact you hold, you do not have the answer: withhold and say why. Never
produce a plausible answer in place of one you actually have -- it is the one
failure that is not recoverable, because everything after it is built on it.

Do not oversell. A strong specific beats three vague claims, and vague claims
are recorded as weak.
""" + "\n" + PROTOCOL_RULES


def run_exchange(
    client: SupportsStructured,
    role: RoleMandate,
    candidate: CandidateMandate,
    *,
    max_turns: int = 24,
    model: str = AGENT_MODEL,
    assessor_model: str = JUDGE_MODEL,
) -> Run:
    """Play one exchange from open to verdict.

    The loop is the whole of it. Note what is *not* in here: no decision about
    when to stop, no score, no arithmetic. Those live in `protocol.py`, in
    code, where they can be tested without a model in the way.
    """
    exchange = Exchange(ledger=Ledger.from_criteria(role.criteria), max_turns=max_turns)
    run = Run(exchange=exchange)
    run.snapshots.append(exchange.ledger.snapshot())

    company_brief = role.render()
    candidate_brief = candidate.render()

    while not exchange.is_over:
        side = exchange.next_speaker
        system = COMPANY_SYSTEM if side is Side.COMPANY else CANDIDATE_SYSTEM
        brief = company_brief if side is Side.COMPANY else candidate_brief

        try:
            msg, raw, retried = _play(client, exchange, side, system, brief, model)
        except (ProtocolError, LLMError) as e:
            run.violations.append(
                {"turn": exchange.turn + 1, "side": side.value, "fatal": True, "error": str(e)}
            )
            exchange.ended_reason = f"agent could not play a legal act: {e}"
            break

        if retried:
            run.violations.append(
                {"turn": msg.turn, "side": side.value, "fatal": False, "error": retried}
            )

        strength = None
        if msg.act is Act.DISCLOSE:
            strength = assess(client, exchange, msg, role, model=assessor_model)

        exchange.append(msg, evidence_strength=strength)
        run.decisions.append(Decision(turn=msg.turn, side=side.value, raw=raw, retried_after=retried))
        run.snapshots.append(exchange.ledger.snapshot())

    return run
