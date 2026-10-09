"""How much of the ranking is the evidence, and how much is the scale?

`screen.CREDIT` says a `moderate` is worth 0.6 of a criterion and a `weak`
0.25. Those two numbers were chosen, not derived, and they are applied to
every candidate on every posting. This module asks what happens if they had
been chosen differently.

The answer, measured on the first cohort, was not the one expected: moving
the scale moved scores by 4 to 16 points, which is *more* than the model's
own drift on identical input, and it reordered the middle of the field. A
keyword-stuffer sat third on one scale and fifth on another, on identical
assessments. Nothing about the candidate changed; the constants did.

That makes the scale a policy, not an implementation detail. "How much is a
partial answer worth?" is a hiring question -- a company that wants proven
instances should pay less for a half-answer than one hiring for potential --
and a policy belongs to the people who own the consequences. What this module
can do is show them the consequence before they choose, and show it in the
only unit that matters: does the order change, and by how much.

Everything here is arithmetic over screenings already on disk. No model, no
cost, no new run.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from screen import CREDIT, Screening

ROOT = Path(__file__).resolve().parent.parent

#: Scales worth comparing. `current` is what the code ships; the others are
#: the defensible neighbours, not strawmen -- each one is a scale a real
#: hiring team could argue for.
SCALES: dict[str, dict[str, float]] = {
    "current": dict(CREDIT),
    #: Partial evidence is worth little. The scale of a team that wants
    #: instances and treats a half-answer as close to nothing.
    "strict": {"strong": 1.0, "moderate": 0.5, "weak": 0.15},
    #: Partial evidence is worth most of the way. The scale of a team hiring
    #: for potential.
    "generous": {"strong": 1.0, "moderate": 0.7, "weak": 0.35},
    #: Equal steps. The scale nobody argues for and everybody assumes.
    "linear": {"strong": 1.0, "moderate": 2 / 3, "weak": 1 / 3},
}


def score_under(s: Screening, credit: dict[str, float]) -> float:
    """The same screening, scored on a different scale.

    Re-scored rather than re-run: the assessments are fixed, so any change
    here is the scale and nothing else.
    """
    total = sum(a.weight for a in s.assessments)
    if not total:
        return 0.0
    earned = sum(a.weight * credit.get(a.strength, 0.0) for a in s.assessments)
    return round(earned / total, 4)


@dataclass
class Sensitivity:
    """One cohort, scored on every scale."""

    posting_id: str
    scales: dict[str, dict[str, float]]
    scores: dict[str, dict[str, float]] = field(default_factory=dict)
    #: Measured model drift per candidate, when a stability record exists.
    #: The comparison that gives the whole thing its meaning.
    noise: dict[str, float] = field(default_factory=dict)

    def swing(self, candidate_id: str) -> float:
        """Widest gap between scales for one candidate."""
        vals = list(self.scores[candidate_id].values())
        return round(max(vals) - min(vals), 4) if vals else 0.0

    def order(self, scale: str) -> list[str]:
        return sorted(self.scores, key=lambda c: -self.scores[c][scale])

    @property
    def orders(self) -> dict[str, list[str]]:
        return {name: self.order(name) for name in self.scales}

    @property
    def order_is_stable(self) -> bool:
        seen = list(self.orders.values())
        return all(o == seen[0] for o in seen)

    def movement(self) -> dict[str, int]:
        """The most places each candidate moves across the scales."""
        out = {}
        for c in self.scores:
            positions = [o.index(c) for o in self.orders.values()]
            out[c] = max(positions) - min(positions)
        return out

    def louder_than_noise(self) -> list[str]:
        """Candidates the scale moves further than the model's own drift.

        For these, which scale was chosen matters more than anything the
        screener did -- and a reader of a single number would never know.
        """
        return [c for c in self.scores
                if c in self.noise and self.swing(c) > self.noise[c]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "posting_id": self.posting_id,
            "scales": self.scales,
            "scores": self.scores,
            "noise": self.noise,
            "swing": {c: self.swing(c) for c in self.scores},
            "movement": self.movement(),
            "orders": self.orders,
            "order_is_stable": self.order_is_stable,
            "louder_than_noise": self.louder_than_noise(),
        }

    def render(self) -> str:
        names = list(self.scales)
        lines = [f"{self.posting_id} -- same assessments, {len(names)} credit scales", ""]
        lines.append("  " + "candidate".ljust(20)
                     + "".join(n.rjust(11) for n in names)
                     + "swing".rjust(9) + "noise".rjust(9))
        for c in self.order(names[0]):
            row = "".join(f"{self.scores[c][n]:>10.1%} " for n in names)
            noise = f"{self.noise[c]:>8.1%}" if c in self.noise else "       --"
            lines.append(f"  {c:<20}{row}{self.swing(c):>8.1%}{noise}")

        lines.append("")
        louder = self.louder_than_noise()
        if louder:
            lines.append(f"  the scale moves {len(louder)} of {len(self.scores)} candidates "
                         f"further than the model's own drift:")
            for c in louder:
                lines.append(f"    {c}: scale {self.swing(c):.1%} vs drift {self.noise[c]:.1%}")
        else:
            lines.append("  no candidate is moved further by the scale than by model drift")

        lines.append("")
        if self.order_is_stable:
            lines.append("  every scale produces the same order")
        else:
            lines.append("  THE ORDER DEPENDS ON THE SCALE:")
            for name, o in self.orders.items():
                lines.append(f"    {name:<10} {' > '.join(o)}")
            moved = {c: n for c, n in self.movement().items() if n}
            lines.append("    places moved: "
                         + ", ".join(f"{c} {n}" for c, n in sorted(moved.items(), key=lambda t: -t[1])))
        return "\n".join(lines)


def sensitivity(posting_id: str, screenings: list[Screening],
                scales: dict[str, dict[str, float]] | None = None,
                noise: dict[str, float] | None = None) -> Sensitivity:
    scales = scales or SCALES
    out = Sensitivity(posting_id=posting_id, scales=scales, noise=dict(noise or {}))
    for s in screenings:
        out.scores[s.candidate_id] = {n: score_under(s, c) for n, c in scales.items()}
    return out


def save(s: Sensitivity, out_dir: str | Path, name: str) -> Path:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.json"
    p.write_text(json.dumps(s.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return p
