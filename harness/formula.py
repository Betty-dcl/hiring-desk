"""Choosing the credit scale from the expectations, instead of from taste.

`screen.CREDIT` prices a partial answer. Those numbers decide more of the
ranking than the model does -- measured in `harness/sensitivity.py` -- so
picking them by feel is picking the ranking by feel.

There is a better source. `personas/expectations.toml` was written before the
screener ever ran: each synthetic candidate carries a predicted rank band and
a set of properties whose failure would be a defect. That file is a
specification, and a scale can be tested against it the way any other
implementation is tested against a spec.

So this module searches the scale, rather than assuming it:

1. Take the assessments exactly as the screener produced them. Nothing is
   re-run, so nothing here can be the model changing its mind.
2. Re-score the cohort under every scale on a grid.
3. Check the resulting order against the pre-registered bands.
4. Report the region that satisfies them -- and, when one exists, the point
   furthest from its edges, because a scale that only works at one setting
   is a coincidence rather than a choice.

The honest outcomes are three, and the module says which one happened: no
scale satisfies the spec, one region does, or the whole grid does. The
middle case is the only one that yields a recommendation, and even then the
recommendation is checked against the measured noise before it is offered:
an order that satisfies every band and rests on gaps smaller than the
screener's own drift satisfies the spec by luck.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from screen import Screening

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Scale:
    """What a partial answer is worth. `strong` is the unit; `unknown` is zero."""

    moderate: float
    weak: float
    strong: float = 1.0

    @property
    def credit(self) -> dict[str, float]:
        return {"strong": self.strong, "moderate": self.moderate, "weak": self.weak}

    def __str__(self) -> str:
        return f"{self.strong:g}/{self.moderate:.2f}/{self.weak:.2f}"


def score(s: Screening, scale: Scale) -> float:
    total = sum(a.weight for a in s.assessments)
    if not total:
        return 0.0
    credit = scale.credit
    return sum(a.weight * credit.get(a.strength, 0.0) for a in s.assessments) / total


def bands(expectations: dict[str, Any]) -> dict[str, str]:
    return {k: v["expect_rank_band"] for k, v in expectations.items()
            if isinstance(v, dict) and "expect_rank_band" in v}


def band_allows(band: str, rank: int, n: int) -> bool:
    """Which positions a written band permits, for a cohort of n.

    Read literally and no more tightly than written: "bottom half" is the
    lower half, "middle" is anywhere that is neither first nor last. Reading
    a band more precisely than its author wrote it would let this module
    manufacture a constraint nobody committed to.
    """
    if band == "first":
        return rank == 1
    if band == "bottom half":
        return rank > n / 2
    if band == "middle":
        return 1 < rank < n
    raise ValueError(f"unknown rank band {band!r}")


@dataclass
class Trial:
    """One scale, and what it did to the order."""

    scale: Scale
    order: list[str]
    scores: dict[str, float]
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    def rank(self, cid: str) -> int:
        return self.order.index(cid) + 1

    def margin(self) -> float:
        """The narrowest gap between neighbours in this order.

        The number that says whether a satisfied spec is robust or merely
        lucky: compared against measured drift, a thin margin means the bands
        held by an amount the screener could erase on a re-run.
        """
        vals = [self.scores[c] for c in self.order]
        return round(min((a - b for a, b in zip(vals, vals[1:])), default=0.0), 4)


def check(trial_scores: dict[str, float], expected: dict[str, str],
          musts: list[tuple[str, str]]) -> tuple[list[str], list[str]]:
    """Order the cohort and list what the spec says it got wrong."""
    order = sorted(trial_scores, key=lambda c: -trial_scores[c])
    n = len(order)
    failures = []
    for cid, band in expected.items():
        if cid not in trial_scores:
            continue
        r = order.index(cid) + 1
        if not band_allows(band, r, n):
            failures.append(f"{cid}: {band}, got rank {r} of {n}")
    for above, below in musts:
        if above in trial_scores and below in trial_scores:
            if trial_scores[above] <= trial_scores[below]:
                failures.append(f"{above} must outrank {below}")
    return order, failures


def search(screenings: list[Screening], expected: dict[str, str],
           musts: list[tuple[str, str]], *, steps: int = 21) -> list[Trial]:
    """Every scale on a grid, with `weak` never worth more than `moderate`."""
    out: list[Trial] = []
    for i in range(1, steps + 1):
        m = round(i / steps, 4)
        for j in range(0, steps + 1):
            w = round(j / steps * m, 4)
            scale = Scale(moderate=m, weak=w)
            scores = {s.candidate_id: round(score(s, scale), 6) for s in screenings}
            order, failures = check(scores, expected, musts)
            out.append(Trial(scale=scale, order=order, scores=scores, failures=failures))
    return out


@dataclass
class Result:
    trials: list[Trial]
    noise: dict[str, float] = field(default_factory=dict)

    @property
    def passing(self) -> list[Trial]:
        return [t for t in self.trials if t.ok]

    @property
    def verdict(self) -> str:
        if not self.passing:
            return "no scale satisfies the pre-registered bands"
        if len(self.passing) == len(self.trials):
            return "every scale satisfies them, so they constrain nothing"
        return "a region satisfies them"

    def best(self) -> Trial | None:
        """The passing scale that separates the cohort most.

        Widest narrowest-gap: the order it produces is the hardest to
        overturn. This is about the candidates, not about the constants --
        see `centre` for the other kind of robustness.
        """
        return max(self.passing, key=lambda t: t.margin()) if self.passing else None

    def centre(self) -> Trial | None:
        """The passing scale furthest from the edges of the passing region.

        A scale sitting on the boundary satisfies the spec and would stop
        doing so if someone rounded a constant. Measured in the space the
        choice is actually made in -- `moderate`, and `weak` as a share of
        it, because that ratio is what the spec turned out to constrain.
        """
        if not self.passing:
            return None
        pts = [(t, t.scale.moderate, (t.scale.weak / t.scale.moderate if t.scale.moderate else 0))
               for t in self.passing]
        cm = sum(m for _, m, _ in pts) / len(pts)
        cr = sum(r for _, _, r in pts) / len(pts)
        return min(pts, key=lambda p: (p[1] - cm) ** 2 + (p[2] - cr) ** 2)[0]

    def bounds(self) -> dict[str, tuple[float, float]]:
        ms = [t.scale.moderate for t in self.passing]
        ws = [t.scale.weak for t in self.passing]
        ratios = [round(t.scale.weak / t.scale.moderate, 4) for t in self.passing
                  if t.scale.moderate]
        if not ms:
            return {}
        return {"moderate": (min(ms), max(ms)), "weak": (min(ws), max(ws)),
                "weak/moderate": (min(ratios), max(ratios))}

    def always_failing(self) -> list[str]:
        """Expectations no scale on the grid can meet.

        The most useful output in the file. A band that fails everywhere is
        not a badly chosen constant; it is a demand the assessments cannot
        support, and it belongs upstream or in the spec, not here.
        """
        if self.passing:
            return []
        common = None
        for t in self.trials:
            keys = {f.split(":")[0].split(" must")[0] for f in t.failures}
            common = keys if common is None else (common & keys)
        return sorted(common or [])

    def render(self) -> str:
        lines = [f"{len(self.trials)} scales tried -- {self.verdict}", ""]
        if not self.passing:
            stuck = self.always_failing()
            if stuck:
                lines.append("  these fail under every scale on the grid:")
                for k in stuck:
                    lines.append(f"    {k}")
                lines.append("  -- no constant can fix that; it is upstream of the formula")
            return "\n".join(lines)

        b = self.bounds()
        lines.append(f"  {len(self.passing)} of {len(self.trials)} scales satisfy every band")
        lines.append(f"    moderate      {b['moderate'][0]:.2f} .. {b['moderate'][1]:.2f}")
        lines.append(f"    weak          {b['weak'][0]:.2f} .. {b['weak'][1]:.2f}")
        lines.append(f"    weak/moderate {b['weak/moderate'][0]:.2f} .. "
                     f"{b['weak/moderate'][1]:.2f}")

        t = self.centre()
        assert t
        lines.append("")
        lines.append(f"  furthest from the region's edges: strong/moderate/weak = {t.scale}")
        for c in t.order:
            n = f"  (drift {self.noise[c]:.1%})" if c in self.noise else ""
            lines.append(f"    {t.rank(c)}. {c:<20} {t.scores[c]:>6.1%}{n}")
        widest = self.best()
        assert widest
        if widest.scale != t.scale:
            lines.append(f"  (the scale that separates them most is {widest.scale}, "
                         f"narrowest gap {widest.margin():.1%})")
        lines.append("")
        lines.append(f"  narrowest gap in that order: {t.margin():.1%}")
        worst_noise = max(self.noise.values(), default=0.0)
        if worst_noise and t.margin() < worst_noise:
            lines.append(f"  -- and the screener's own drift reaches {worst_noise:.1%}, so the "
                         f"bands hold by less than the instrument can resolve.")
            lines.append("     The scale is defensible; the order inside the middle is not.")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        t = self.centre()
        return {
            "verdict": self.verdict,
            "tried": len(self.trials),
            "passing": len(self.passing),
            "bounds": {k: list(v) for k, v in self.bounds().items()},
            "recommended": ({"strong": t.scale.strong, "moderate": t.scale.moderate,
                             "weak": t.scale.weak, "order": t.order,
                             "scores": t.scores, "margin": t.margin()} if t else None),
            "noise": self.noise,
            "always_failing": self.always_failing(),
        }


def load_expectations(path: str | Path | None = None) -> dict[str, Any]:
    f = Path(path) if path else ROOT / "personas" / "expectations.toml"
    return tomllib.loads(f.read_text(encoding="utf-8"))


def must_outrank(expectations: dict[str, Any]) -> list[tuple[str, str]]:
    """The `must` lines that state an ordering between two named candidates."""
    out = []
    for cid, block in expectations.items():
        if not isinstance(block, dict):
            continue
        for line in block.get("must", []):
            if " must outrank " in line:
                _, other = line.split(" must outrank ")
                out.append((cid, other.strip().rstrip(".")))
    return out


def save(r: Result, out_dir: str | Path, name: str) -> Path:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.json"
    p.write_text(json.dumps(r.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return p
