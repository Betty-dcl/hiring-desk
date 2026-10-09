"""The answers a posting never contains, recorded with who gave them.

Two numbers decide more of a ranking than the model does, and neither is in
any job advertisement:

* **the weights** -- what matters more than what. `intake/posting.py` fills
  these with section defaults and says in its own docstring that they are
  wrong until somebody sets them. Until then every score is computed with
  weights nobody chose.
* **the credit scale** -- what a partial answer is worth. Measured in
  `harness/sensitivity.py`: moving it swings scores by up to 16 points, more
  than the screener's own drift, and it reorders the middle of a field.

Neither is a technical detail, and neither belongs to whoever wrote the code.
"How much is a half-answer worth?" is a hiring policy: a team that wants
proven instances should pay less for one than a team hiring for potential.
So this module does not choose. It records a choice, with the name of the
person who made it and the date they made it, and refuses to pretend a
default is a decision.

A settings file is a claim about a company, so it says who made it. An
unanswered file is better than an invented one: what is absent stays at the
extractor's default and is marked as such in the report.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent

#: What ships when nobody has answered. Present so that the difference between
#: "chosen" and "not chosen yet" is visible rather than inferred.
DEFAULT_SCALE = {"strong": 1.0, "moderate": 0.6, "weak": 0.25}


@dataclass
class Settings:
    """One posting's answers, and their provenance."""

    posting_id: str
    #: criterion id -> weight. Missing ids keep the extractor's default.
    weights: dict[str, float] = field(default_factory=dict)
    #: strength -> credit. Empty means nobody has chosen, so the default holds.
    scale: dict[str, float] = field(default_factory=dict)
    #: criterion ids the company declares as conditions, beyond any the
    #: posting itself declared in its own words.
    gates: list[str] = field(default_factory=list)
    answered_by: str = ""
    answered_at: str = ""
    notes: str = ""

    @property
    def answered(self) -> bool:
        return bool(self.answered_by and (self.weights or self.scale or self.gates))

    def credit(self) -> dict[str, float]:
        return dict(self.scale) if self.scale else dict(DEFAULT_SCALE)

    def unanswered_criteria(self, agenda: Any) -> list[str]:
        """Criteria still carrying a default weight. Named, not hidden."""
        return [c.id for c in getattr(agenda, "criteria", []) if c.id not in self.weights]

    def apply(self, agenda: Any) -> Any:
        """A copy of the agenda with the chosen weights and gates in it.

        A copy, so the extracted agenda on disk stays what the posting said
        and the settings stay what a person decided. Two records, two kinds of
        authority, never merged into one file that looks like provenance.
        """
        from copy import deepcopy

        out = deepcopy(agenda)
        for c in out.criteria:
            if c.id in self.weights:
                c.weight = float(self.weights[c.id])
                c.needs_confirmation = False
            if c.id in self.gates and not c.hard:
                c.hard = True
                c.hard_quote = f"declared a condition by {self.answered_by}, not by the posting"
        return out

    def to_dict(self) -> dict[str, Any]:
        return {"posting_id": self.posting_id, "weights": self.weights,
                "scale": self.scale, "gates": self.gates,
                "answered_by": self.answered_by, "answered_at": self.answered_at,
                "notes": self.notes}

    def render(self) -> str:
        if not self.answered:
            return (f"{self.posting_id}: nobody has set the weights or the scale.\n"
                    f"  Every score is being computed with the extractor's defaults,\n"
                    f"  which its own documentation calls wrong until somebody sets them.")
        lines = [f"{self.posting_id}: answered by {self.answered_by} on "
                 f"{self.answered_at[:10]}", ""]
        if self.scale:
            lines.append("  credit scale: " + ", ".join(
                f"{k} {v:g}" for k, v in self.scale.items()))
        for cid, w in sorted(self.weights.items(), key=lambda t: -t[1]):
            gate = "  [condition]" if cid in self.gates else ""
            lines.append(f"    {cid:<30} {w:>5.1f}{gate}")
        if self.notes:
            lines.append(f"  note: {self.notes}")
        return "\n".join(lines)


def path_for(posting_id: str) -> Path:
    return ROOT / "mandates" / "generated" / f"role_{posting_id}.settings.toml"


def load(posting_id: str) -> Settings:
    """Read the answers, or an empty record that says nobody answered."""
    f = path_for(posting_id)
    if not f.exists():
        return Settings(posting_id=posting_id)
    d = tomllib.loads(f.read_text(encoding="utf-8"))
    return Settings(
        posting_id=d.get("posting_id", posting_id),
        weights={k: float(v) for k, v in (d.get("weights") or {}).items()},
        scale={k: float(v) for k, v in (d.get("scale") or {}).items()},
        gates=list(d.get("gates") or []),
        answered_by=d.get("answered_by", ""),
        answered_at=d.get("answered_at", ""),
        notes=d.get("notes", ""),
    )


def save(s: Settings) -> Path:
    """Write the answers as TOML a person can read and edit by hand."""
    s.answered_at = s.answered_at or datetime.now(timezone.utc).isoformat()
    lines = [
        "# What this posting does not say, answered by a person.",
        "#",
        "# Edit this by hand or rerun `python configure.py`. The extracted",
        "# agenda beside it is what the posting said; this file is what your",
        "# company decided. They are kept apart on purpose.",
        "",
        f'posting_id = "{s.posting_id}"',
        f'answered_by = "{s.answered_by}"',
        f'answered_at = "{s.answered_at}"',
    ]
    if s.notes:
        lines.append(f'notes = "{s.notes}"')
    if s.gates:
        lines.append("gates = [" + ", ".join(f'"{g}"' for g in s.gates) + "]")
    if s.scale:
        lines += ["", "[scale]"] + [f"{k} = {v:g}" for k, v in s.scale.items()]
    if s.weights:
        lines += ["", "[weights]"] + [f"{k} = {v:g}" for k, v in
                                      sorted(s.weights.items(), key=lambda t: -t[1])]
    f = path_for(s.posting_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f
