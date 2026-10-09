"""The reviewer's judgement, given weight on the record.

A screen that a person cannot argue with is a screen that decides. This
module is where the human gets a lever, and the design constraint is that
pulling it must leave a mark.

Three rules, all enforced here rather than asked for:

**The machine score is never overwritten.** A review produces a second
number beside the first, and the combined figure always reports both parts.
A single blended score with no decomposition is how an ordering stops being
auditable -- nobody can later ask which half moved.

**An override carries a reason or it does not happen.** A reviewer may set
any criterion to any strength, including one the screener called UNKNOWN,
because a person often knows something the paper does not. What they may not
do is change it silently. `why` is required, the reviewer is named, and both
survive into the record.

**A missing review is not a zero.** A candidate nobody has looked at yet has
no human score, and the ranking says so rather than imputing one. This is the
same discipline as UNKNOWN in `screen.py`: absence of judgement is not
judgement of absence, and averaging in a zero for the unreviewed would
quietly punish whoever is at the bottom of the reviewer's queue.

None of this makes the system decide. The output is still an ordering with
its evidence attached, for a human -- now a named one -- to act on.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from screen import CREDIT, STRENGTHS, Screening

#: How much of the combined score the reviewer's overall impression carries,
#: when there is one. A default, not a truth: it belongs in the setup
#: questions a deployment answers for itself, beside the criterion weights.
DEFAULT_HUMAN_WEIGHT = 0.3


class ReviewError(ValueError):
    """A review that cannot be recorded, and so must not be applied."""


@dataclass(frozen=True)
class Override:
    """One criterion a person re-judged, and why."""

    criterion_id: str
    strength: str
    why: str
    reviewer: str
    at: str = ""

    def __post_init__(self) -> None:
        if self.strength not in STRENGTHS:
            raise ReviewError(f"{self.strength!r} is not a strength; use one of {', '.join(STRENGTHS)}")
        if not self.why.strip():
            raise ReviewError(
                f"override of {self.criterion_id!r} has no reason. "
                "An override without a reason cannot be reviewed by anyone else, "
                "which is the only thing that makes it different from a silent edit."
            )
        if not self.reviewer.strip():
            raise ReviewError("an override must name who made it")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Review:
    """What one named person thought, on the record."""

    candidate_id: str
    posting_id: str
    reviewer: str
    impression: float | None = None
    note: str = ""
    overrides: list[Override] = field(default_factory=list)
    at: str = ""

    def __post_init__(self) -> None:
        if not self.reviewer.strip():
            raise ReviewError("a review must name its reviewer")
        if self.impression is not None and not 0.0 <= self.impression <= 1.0:
            raise ReviewError(f"impression {self.impression} is outside 0.0-1.0")
        self.at = self.at or datetime.now(timezone.utc).isoformat()

    @property
    def is_empty(self) -> bool:
        return self.impression is None and not self.overrides

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "posting_id": self.posting_id,
            "reviewer": self.reviewer,
            "impression": self.impression,
            "note": self.note,
            "at": self.at,
            "overrides": [o.to_dict() for o in self.overrides],
        }


@dataclass
class Change:
    """One criterion's before and after, kept for the audit trail."""

    criterion_id: str
    was: str
    now: str
    why: str
    reviewer: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Reviewed:
    """A screening plus a human read, with both halves still visible."""

    screening: Screening
    review: Review | None = None
    human_weight: float = DEFAULT_HUMAN_WEIGHT
    changes: list[Change] = field(default_factory=list)
    #: Score recomputed from the overridden assessments, machine part only.
    adjusted_score: float = 0.0
    adjusted_coverage: float = 0.0

    @property
    def candidate_id(self) -> str:
        return self.screening.candidate_id

    @property
    def machine_score(self) -> float:
        """The screener's own number, before any human touched it."""
        return self.screening.score

    @property
    def reviewed(self) -> bool:
        return self.review is not None and not self.review.is_empty

    @property
    def has_impression(self) -> bool:
        return self.review is not None and self.review.impression is not None

    @property
    def combined(self) -> float:
        """The ordering number.

        With an impression, the reviewer's read and the evidence-based score
        are mixed at the declared weight. Without one, this is just the
        evidence-based score -- not a score penalised for the reviewer's
        silence.
        """
        if not self.has_impression:
            return self.adjusted_score
        w = self.human_weight
        return round((1 - w) * self.adjusted_score + w * float(self.review.impression), 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "posting_id": self.screening.posting_id,
            "machine_score": self.machine_score,
            "adjusted_score": self.adjusted_score,
            "human_impression": self.review.impression if self.has_impression else None,
            "human_weight": self.human_weight if self.has_impression else None,
            "combined": self.combined,
            "reviewed": self.reviewed,
            "reviewer": self.review.reviewer if self.review else None,
            "coverage": self.adjusted_coverage,
            "open_gates": self.screening.open_gates,
            "changes": [c.to_dict() for c in self.changes],
            "decides": False,
            "for_human_review": True,
        }

    def render(self) -> str:
        lines = [f"{self.candidate_id} against {self.screening.posting_id}"]
        lines.append(f"  evidence score   {self.machine_score:.0%}")
        if self.changes:
            lines.append(f"  after overrides  {self.adjusted_score:.0%}")
        if self.has_impression:
            lines.append(
                f"  reviewer         {self.review.impression:.0%} "
                f"({self.review.reviewer}, weight {self.human_weight:.0%})"
            )
            lines.append(f"  combined         {self.combined:.0%}")
        elif self.reviewed:
            #: Overrides without an overall read: somebody did look, and saying
            #: "not yet reviewed" next to their named changes would be false.
            lines.append(f"  reviewer         {self.review.reviewer}, no overall impression given")
        else:
            lines.append("  reviewer         not yet reviewed")
        if self.review and self.review.note:
            lines.append(f"  note             {self.review.note}")
        for c in self.changes:
            lines.append(f"  changed {c.criterion_id}: {c.was} -> {c.now}")
            lines.append(f"      {c.reviewer}: {c.why}")
        return "\n".join(lines)


def apply(screening: Screening, review: Review | None = None, *,
          human_weight: float = DEFAULT_HUMAN_WEIGHT) -> Reviewed:
    """Lay a review over a screening, without destroying either.

    The screening passed in is not modified. What comes back holds the
    original score, the score after the reviewer's overrides, the reviewer's
    own impression, and the list of what changed.
    """
    if not 0.0 <= human_weight <= 1.0:
        raise ReviewError(f"human_weight {human_weight} is outside 0.0-1.0")

    out = Reviewed(screening=screening, review=review, human_weight=human_weight)

    by_id = {a.criterion_id: a for a in screening.assessments}
    strengths = {cid: a.strength for cid, a in by_id.items()}

    if review:
        if review.candidate_id != screening.candidate_id:
            raise ReviewError(
                f"review is for {review.candidate_id!r}, screening is for "
                f"{screening.candidate_id!r}"
            )
        for o in review.overrides:
            if o.criterion_id not in by_id:
                raise ReviewError(f"{o.criterion_id!r} is not on this posting's agenda")
            was = strengths[o.criterion_id]
            if was == o.strength:
                continue
            strengths[o.criterion_id] = o.strength
            out.changes.append(
                Change(criterion_id=o.criterion_id, was=was, now=o.strength,
                       why=o.why, reviewer=o.reviewer)
            )

    total = sum(a.weight for a in screening.assessments)
    earned = sum(by_id[cid].weight * CREDIT.get(st, 0.0) for cid, st in strengths.items())
    spoken = sum(by_id[cid].weight for cid, st in strengths.items() if st != "unknown")
    out.adjusted_score = round(earned / total, 4) if total else 0.0
    out.adjusted_coverage = round(spoken / total, 4) if total else 0.0
    return out


@dataclass
class ReviewedRanking:
    """An ordering that says which parts a person moved, and which nobody read."""

    posting_id: str
    rows: list[Reviewed] = field(default_factory=list)

    def __post_init__(self) -> None:
        """One candidate, one row.

        A duplicate is never a tie -- it is the same person scored twice from
        two files, and it silently doubles their presence in whatever a human
        reads off the top of the list. Caught here because it is cheap to
        catch here and invisible everywhere else.
        """
        seen: set[str] = set()
        dupes = sorted({r.candidate_id for r in self.rows
                        if r.candidate_id in seen or seen.add(r.candidate_id)})
        if dupes:
            raise ReviewError(
                f"the same candidate appears more than once in this ranking: "
                f"{', '.join(dupes)}"
            )

    @property
    def ordered(self) -> list[Reviewed]:
        return sorted(self.rows, key=lambda r: (r.combined, r.adjusted_coverage), reverse=True)

    @property
    def unreviewed(self) -> list[str]:
        return [r.candidate_id for r in self.rows if not r.reviewed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "posting_id": self.posting_id,
            "ranked_at": datetime.now(timezone.utc).isoformat(),
            "decides": False,
            "for_human_review": True,
            "no_reject_state": True,
            "unreviewed": self.unreviewed,
            "order": [
                {"rank": i, **r.to_dict()} for i, r in enumerate(self.ordered, 1)
            ],
        }

    def render(self) -> str:
        lines = [f"{self.posting_id} -- {len(self.rows)} candidates", ""]
        lines.append(f"  {'#':<3} {'candidate':<20} {'evidence':>9} {'reviewer':>9} {'combined':>9}  changed")
        for i, r in enumerate(self.ordered, 1):
            human = f"{r.review.impression:.0%}" if r.has_impression else "--"
            changed = ", ".join(c.criterion_id for c in r.changes) or "-"
            lines.append(
                f"  {i:<3} {r.candidate_id:<20} {r.adjusted_score:>8.0%} "
                f"{human:>9} {r.combined:>8.0%}  {changed[:40]}"
            )
        if self.unreviewed:
            lines += ["", f"  not yet reviewed by anyone: {', '.join(self.unreviewed)}",
                      "  (they are ordered on evidence alone, not scored zero for it)"]
        lines += ["", "  Ordering with its evidence and its overrides attached.",
                  "  No rejection, no decision."]
        return "\n".join(lines)


def save(obj: Reviewed | ReviewedRanking, out_dir: str | Path, name: str) -> Path:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.json"
    p.write_text(json.dumps(obj.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return p
