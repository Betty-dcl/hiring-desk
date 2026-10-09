"""The agent-to-agent exchange protocol.

Two principals sit on either side of a company boundary: a candidate and a
hiring company. Neither talks to the other. Each is represented by an agent
holding a private mandate, and the two agents exchange here.

What makes this a protocol rather than two chatbots in a room:

1. Every message is a typed *act*, not free text. One act per turn.
2. Every act about substance is anchored to a criterion from a shared agenda,
   so the exchange is auditable line by line.
3. The agenda is a ledger with an explicit terminal state. The exchange ends
   when every criterion is settled or the turn budget runs out -- never
   because a model decided it felt done.
4. Neither side may write to the other's ledger. An agent records what it
   heard; it cannot assert that the counterparty is satisfied.
5. Both sides are owed a verdict. That is the whole point: the candidate
   never goes unanswered.

Everything that can be computed is computed here, in Python. The model
chooses which act to play and phrases it; it never scores, never counts
turns, and never decides that the exchange is over.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Iterable


class Side(str, Enum):
    """Which principal an agent speaks for."""

    CANDIDATE = "candidate"
    COMPANY = "company"

    @property
    def other(self) -> "Side":
        return Side.COMPANY if self is Side.CANDIDATE else Side.CANDIDATE


class Act(str, Enum):
    """The closed set of moves an agent may play.

    Keeping this closed is what makes the transcript scoreable. A model that
    can only pick from seven acts cannot quietly invent a new kind of move
    halfway through the exchange.
    """

    OPEN = "open"          # state why you are here, once, at the start
    PROBE = "probe"        # ask about one named criterion
    DISCLOSE = "disclose"  # answer with evidence on one named criterion
    WITHHOLD = "withhold"  # decline to answer, with a reason -- a legitimate move
    PROPOSE = "propose"    # propose a shape: scope, level, arrangement
    COUNTER = "counter"    # counter a live proposal
    FLAG = "flag"          # raise a blocker or a mismatch
    CLOSE = "close"        # ask to conclude and have verdicts rendered


#: Acts that must name a criterion. Anchoring these is what keeps the
#: exchange auditable -- an unanchored question is just conversation.
ANCHORED_ACTS = frozenset({Act.PROBE, Act.DISCLOSE, Act.WITHHOLD})

#: Acts that may only be played once per side, at the top of the exchange.
OPENING_ACTS = frozenset({Act.OPEN})


class Strength(str, Enum):
    """How well a disclosure actually covered the criterion it answered.

    The model assigns this, because it is a judgement about language. The
    weighted score built on top of it is computed in code, because that is
    arithmetic and models are unreliable at arithmetic under pressure.
    """

    STRONG = "strong"    # concrete, specific, first-hand evidence
    PARTIAL = "partial"  # relevant but thin, second-hand, or adjacent
    NONE = "none"        # did not actually address the criterion

    @property
    def credit(self) -> float:
        return {"strong": 1.0, "partial": 0.5, "none": 0.0}[self.value]


class Status(str, Enum):
    """Terminal and non-terminal states of a single criterion."""

    OPEN = "open"          # not yet asked about
    ASKED = "asked"        # probed, awaiting a disclosure
    RESOLVED = "resolved"  # answered with evidence
    WITHHELD = "withheld"  # the candidate declined, on the record
    BLOCKED = "blocked"    # a hard mismatch was flagged

    @property
    def is_settled(self) -> bool:
        return self in (Status.RESOLVED, Status.WITHHELD, Status.BLOCKED)


@dataclass(frozen=True)
class Criterion:
    """One thing the company actually needs to know, with its weight.

    Weights come from the role mandate and are never invented by an agent.
    `hard` marks a criterion that cannot be traded away: if it ends up
    blocked, no amount of strength elsewhere rescues the fit.
    """

    id: str
    question: str
    weight: float
    hard: bool = False

    def __post_init__(self) -> None:
        if self.weight < 0:
            raise ValueError(f"criterion {self.id}: weight must be >= 0")
        if not self.id or " " in self.id:
            raise ValueError(f"criterion id must be a non-empty slug, got {self.id!r}")


@dataclass
class Evidence:
    """What one side recorded hearing from the other about one criterion."""

    criterion_id: str
    strength: Strength
    quote: str
    turn: int

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "strength": self.strength.value}


@dataclass
class Message:
    """A single act on the wire, plus the reasoning behind it.

    `reasoning` is kept because a trace you cannot read is a trace you cannot
    trust -- and because the judge scores the reasoning, not only the words
    that came out.
    """

    turn: int
    speaker: Side
    act: Act
    body: str
    criterion_id: str | None = None
    reasoning: str = ""

    def __post_init__(self) -> None:
        if self.act in ANCHORED_ACTS and not self.criterion_id:
            raise ProtocolError(f"{self.act.value} must name a criterion")
        if self.act not in ANCHORED_ACTS and self.criterion_id:
            raise ProtocolError(f"{self.act.value} must not name a criterion")
        if not self.body.strip():
            raise ProtocolError(f"{self.act.value} has an empty body")

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn": self.turn,
            "speaker": self.speaker.value,
            "act": self.act.value,
            "body": self.body,
            "criterion_id": self.criterion_id,
            "reasoning": self.reasoning,
        }

    def render(self) -> str:
        """One line of the transcript, as the agents will read it back.

        History is handed to the model as readable text rather than as a
        native message array -- see `recherche-orbio.md` §3.2 for the
        measurement that motivated this, and `harness/` for our own re-test.
        """
        anchor = f" [{self.criterion_id}]" if self.criterion_id else ""
        return f"turn {self.turn} | {self.speaker.value} | {self.act.value}{anchor}: {self.body}"


class ProtocolError(Exception):
    """An act that the protocol does not permit.

    Raised rather than swallowed: an agent that plays an illegal move is a
    finding, not a fallback.
    """


@dataclass
class Ledger:
    """The company side's record of where each criterion stands.

    Only the company writes here. The candidate's agent can say whatever it
    likes; what counts is what the company recorded hearing. That asymmetry
    is deliberate -- it is what stops a persuasive counterparty from marking
    its own homework.
    """

    criteria: dict[str, Criterion]
    status: dict[str, Status] = field(default_factory=dict)
    evidence: dict[str, Evidence] = field(default_factory=dict)

    @classmethod
    def from_criteria(cls, criteria: Iterable[Criterion]) -> "Ledger":
        by_id: dict[str, Criterion] = {}
        for c in criteria:
            if c.id in by_id:
                raise ValueError(f"duplicate criterion id: {c.id}")
            by_id[c.id] = c
        return cls(criteria=by_id, status={cid: Status.OPEN for cid in by_id})

    def require(self, criterion_id: str) -> Criterion:
        try:
            return self.criteria[criterion_id]
        except KeyError:
            raise ProtocolError(
                f"unknown criterion {criterion_id!r}; the agenda is fixed by the role mandate"
            ) from None

    def mark_asked(self, criterion_id: str) -> None:
        self.require(criterion_id)
        if self.status[criterion_id] is Status.OPEN:
            self.status[criterion_id] = Status.ASKED

    def record(self, ev: Evidence) -> None:
        """Record a disclosure. Strongest answer wins; weak answers never downgrade."""
        self.require(ev.criterion_id)
        prior = self.evidence.get(ev.criterion_id)
        if prior is None or ev.strength.credit > prior.strength.credit:
            self.evidence[ev.criterion_id] = ev
        self.status[ev.criterion_id] = Status.RESOLVED

    def mark_withheld(self, criterion_id: str) -> None:
        self.require(criterion_id)
        self.status[criterion_id] = Status.WITHHELD

    def mark_blocked(self, criterion_id: str) -> None:
        self.require(criterion_id)
        self.status[criterion_id] = Status.BLOCKED

    @property
    def unsettled(self) -> list[str]:
        return [cid for cid, st in self.status.items() if not st.is_settled]

    @property
    def is_complete(self) -> bool:
        return not self.unsettled

    @property
    def has_hard_block(self) -> bool:
        return any(
            self.status[cid] is Status.BLOCKED and c.hard for cid, c in self.criteria.items()
        )

    def coverage(self) -> float:
        """Share of total weight that reached a settled state, 0.0-1.0."""
        total = sum(c.weight for c in self.criteria.values())
        if total == 0:
            return 0.0
        settled = sum(c.weight for cid, c in self.criteria.items() if self.status[cid].is_settled)
        return settled / total

    def fit_score(self) -> float:
        """Weighted evidence strength over total weight, 0.0-1.0.

        Deliberately arithmetic, deliberately here and not in a prompt. A
        hard blocker collapses the score to zero rather than averaging away,
        because a hard criterion is not a weighted opinion.
        """
        if self.has_hard_block:
            return 0.0
        total = sum(c.weight for c in self.criteria.values())
        if total == 0:
            return 0.0
        earned = sum(
            self.criteria[cid].weight * ev.strength.credit for cid, ev in self.evidence.items()
        )
        return earned / total

    def snapshot(self) -> dict[str, Any]:
        """A comparable state dump, for the before/after diff the judge reads."""
        return {
            "status": {cid: st.value for cid, st in sorted(self.status.items())},
            "evidence": {
                cid: self.evidence[cid].to_dict() for cid in sorted(self.evidence)
            },
            "coverage": round(self.coverage(), 4),
            "fit_score": round(self.fit_score(), 4),
        }


@dataclass
class Exchange:
    """The bounded conversation itself: transcript, ledger, turn budget.

    Termination is decided here and nowhere else. An agent may *ask* to close
    by playing CLOSE; whether the exchange actually ends is this object's
    call.
    """

    ledger: Ledger
    max_turns: int = 24
    transcript: list[Message] = field(default_factory=list)
    close_requested: set[Side] = field(default_factory=set)
    ended_reason: str | None = None

    @property
    def turn(self) -> int:
        return len(self.transcript)

    @property
    def next_speaker(self) -> Side:
        """Strict alternation. The company opens, because it owns the agenda.

        The candidate already applied -- that happened outside this exchange.
        What the company convenes here is the conversation about its own
        criteria, so it speaks first and the candidate answers. Alternation
        then falls out naturally: open, open, probe, disclose, probe, ...
        """
        if not self.transcript:
            return Side.COMPANY
        return self.transcript[-1].speaker.other

    def has_opened(self, side: Side) -> bool:
        return any(m.speaker is side and m.act is Act.OPEN for m in self.transcript)

    def legal_acts(self, side: Side) -> set[Act]:
        """What this side may legally play right now.

        Exposed so the agent's prompt can be built from the same source of
        truth that validation uses -- the model is told the rules rather than
        punished for not guessing them.
        """
        if not self.has_opened(side):
            return {Act.OPEN}
        acts = set(Act) - OPENING_ACTS
        if side is Side.COMPANY:
            # The company asks and judges; it does not disclose about itself
            # against the candidate's agenda, and it does not withhold.
            acts -= {Act.DISCLOSE, Act.WITHHOLD}
        else:
            # The candidate answers; probing the company's own agenda back at
            # it would let the candidate drive the ledger.
            acts -= {Act.PROBE}
        return acts

    def validate(self, msg: Message) -> None:
        if self.ended_reason:
            raise ProtocolError("exchange has ended")
        if msg.speaker is not self.next_speaker:
            raise ProtocolError(
                f"out of turn: expected {self.next_speaker.value}, got {msg.speaker.value}"
            )
        if msg.turn != self.turn + 1:
            raise ProtocolError(f"turn number must be {self.turn + 1}, got {msg.turn}")
        legal = self.legal_acts(msg.speaker)
        if msg.act not in legal:
            raise ProtocolError(
                f"{msg.speaker.value} may not play {msg.act.value} here; "
                f"legal: {sorted(a.value for a in legal)}"
            )
        if msg.criterion_id:
            self.ledger.require(msg.criterion_id)

    def append(self, msg: Message, evidence_strength: Strength | None = None) -> None:
        """Commit an act, then let it move the ledger.

        Ledger effects are applied here rather than by the agent, so a side
        cannot change the record except through a legal move.
        """
        self.validate(msg)
        self.transcript.append(msg)

        if msg.act is Act.PROBE:
            self.ledger.mark_asked(msg.criterion_id)  # type: ignore[arg-type]
        elif msg.act is Act.DISCLOSE:
            if evidence_strength is None:
                raise ProtocolError("a disclosure must be recorded with an evidence strength")
            self.ledger.record(
                Evidence(
                    criterion_id=msg.criterion_id,  # type: ignore[arg-type]
                    strength=evidence_strength,
                    quote=msg.body,
                    turn=msg.turn,
                )
            )
        elif msg.act is Act.WITHHOLD:
            self.ledger.mark_withheld(msg.criterion_id)  # type: ignore[arg-type]
        elif msg.act is Act.CLOSE:
            self.close_requested.add(msg.speaker)

        self._check_end()

    def _check_end(self) -> None:
        if self.ledger.has_hard_block:
            self.ended_reason = "hard blocker"
        elif self.close_requested == {Side.CANDIDATE, Side.COMPANY}:
            self.ended_reason = "both sides closed"
        elif self.ledger.is_complete and Side.COMPANY in self.close_requested:
            self.ended_reason = "agenda settled"
        elif self.turn >= self.max_turns:
            self.ended_reason = "turn budget exhausted"

    @property
    def is_over(self) -> bool:
        return self.ended_reason is not None

    def render_transcript(self) -> str:
        """The transcript as the agents read it back, oldest first."""
        return "\n".join(m.render() for m in self.transcript) or "(nothing said yet)"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ended_reason": self.ended_reason,
            "turns": self.turn,
            "max_turns": self.max_turns,
            "transcript": [m.to_dict() for m in self.transcript],
            "ledger": self.ledger.snapshot(),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)
