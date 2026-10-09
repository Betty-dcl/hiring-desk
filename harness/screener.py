"""Is the ranking a measurement, or is it noise?

The screener is deterministic in its arithmetic and stochastic in its
judgement. Credit per strength is a constant, the weighted sum is Python, and
none of that can drift. What can drift is which strength a criterion gets --
and one strength moving from `moderate` to `strong` is worth 0.4 of a
criterion's weight, which on a twelve-criterion agenda is enough to move a
person past someone else.

So this module runs the same facts against the same agenda N times and asks
three questions, in increasing order of how much they matter:

1. **How far does one candidate's score move?** Spread and standard
   deviation over N identical runs.
2. **Which criteria are unstable?** Per-criterion agreement, so instability
   can be attributed rather than merely observed. A criterion that lands on
   three different strengths in three runs is a badly posed question, and
   that is fixable.
3. **Does the order change?** The only question a hiring team actually asks
   of a ranking. A cohort re-ranked N times either comes back in the same
   order or does not, and if it does not, the ordering is a coin toss wearing
   a percentage sign.
4. **Where does it stop being a ranking?** "The order changed" is true and
   almost useless -- a cohort of six can be solid at the top and a lottery at
   the bottom, and it is. So each adjacent pair is asked one question: is the
   distance between these two people larger than the distance either of them
   travels on their own? A pair that fails that is not ranked, it is merely
   printed in an order.

This is pass@k against pass^k, applied to a screen rather than to an agent
loop: any single run can look authoritative, and only the repeat says whether
it was.

Nothing here is free -- every run is a live call per candidate. Start small.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from intake.cv import Facts
from intake.posting import DraftAgenda
from screen import Screening, screen

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Repeat:
    """N screenings of one candidate, and how much they disagreed."""

    candidate_id: str
    runs: list[Screening] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def scores(self) -> list[float]:
        return [s.score for s in self.runs]

    @property
    def spread(self) -> float:
        return round(max(self.scores) - min(self.scores), 4) if self.scores else 0.0

    @property
    def stdev(self) -> float:
        return round(statistics.stdev(self.scores), 4) if len(self.scores) > 1 else 0.0

    @property
    def mean(self) -> float:
        return round(statistics.fmean(self.scores), 4) if self.scores else 0.0

    def per_criterion(self) -> dict[str, dict[str, Any]]:
        """For each criterion: what it was called, and how consistently.

        `agreement` is the share of runs that agreed with the most common
        answer. 1.0 is a criterion the screener reads the same way every
        time; anything near 1/len(strengths) is a question that is not
        really being answered.
        """
        out: dict[str, dict[str, Any]] = {}
        ids = [a.criterion_id for a in self.runs[0].assessments] if self.runs else []
        for cid in ids:
            calls = [
                next((a.strength for a in r.assessments if a.criterion_id == cid), None)
                for r in self.runs
            ]
            calls = [c for c in calls if c]
            if not calls:
                continue
            counts = Counter(calls)
            top, n = counts.most_common(1)[0]
            out[cid] = {
                "modal": top,
                "agreement": round(n / len(calls), 3),
                "seen": dict(counts),
                "stable": len(counts) == 1,
            }
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "runs": len(self.runs),
            "scores": self.scores,
            "mean": self.mean,
            "spread": self.spread,
            "stdev": self.stdev,
            "per_criterion": self.per_criterion(),
            "errors": self.errors,
        }


@dataclass(frozen=True)
class Pair:
    """Two neighbours in a ranking, and whether the gap between them is real."""

    above: str
    below: str
    gap: float
    #: The larger of the two spreads: the distance the noisier of the pair
    #: covers on its own, over identical input.
    noise: float

    @property
    def separated(self) -> bool:
        return self.gap > self.noise

    def to_dict(self) -> dict[str, Any]:
        return {"above": self.above, "below": self.below, "gap": round(self.gap, 4),
                "noise": round(self.noise, 4), "separated": self.separated}


def separation(rows: list[tuple[str, float, float]]) -> list[Pair]:
    """Adjacent pairs of `(candidate_id, mean, spread)`, best first.

    This is the whole finding in three lines of arithmetic: a ranking is a
    measurement exactly where the gap between two people exceeds the distance
    one of them moves against themselves, and is decoration everywhere else.
    Nothing here calls a model, so it costs nothing to ask of any record.
    """
    ranked = sorted(rows, key=lambda r: r[1], reverse=True)
    return [
        Pair(above=a[0], below=b[0], gap=round(a[1] - b[1], 4), noise=max(a[2], b[2]))
        for a, b in zip(ranked, ranked[1:])
    ]


def resolution(rows: list[tuple[str, float, float]]) -> float:
    """The smallest score difference this cohort can actually resolve.

    Two candidates closer together than the widest spread in the cohort are
    not being told apart by the screener; they are being ordered by whatever
    the model happened to say that morning.
    """
    return round(max((r[2] for r in rows), default=0.0), 4)


def places_worth_reading(pairs: list[Pair]) -> int:
    """How many places have a settled occupant, reading from the top.

    The first unseparated pair ends it: if third and fourth cannot be told
    apart, then third place is not a fact about either of them, and neither
    is anything below it. So the count is the index of that pair -- two
    settled places in a cohort whose third comparison fails, and zero in one
    whose first does.
    """
    for i, pair in enumerate(pairs):
        if not pair.separated:
            return i
    return len(pairs) + 1 if pairs else 0


@dataclass
class Stability:
    """A whole cohort, screened N times."""

    posting_id: str
    n: int
    repeats: list[Repeat] = field(default_factory=list)
    at: str = ""

    def orders(self) -> list[list[str]]:
        """The ranking produced by each individual run."""
        out = []
        for i in range(self.n):
            row = [
                (r.candidate_id, r.runs[i].score, r.runs[i].coverage)
                for r in self.repeats
                if len(r.runs) > i
            ]
            row.sort(key=lambda t: (t[1], t[2]), reverse=True)
            out.append([cid for cid, _, _ in row])
        return out

    def rank_movement(self) -> dict[str, int]:
        """The most places each candidate moved across the runs."""
        orders = self.orders()
        moves: dict[str, int] = {}
        for r in self.repeats:
            positions = [o.index(r.candidate_id) for o in orders if r.candidate_id in o]
            moves[r.candidate_id] = (max(positions) - min(positions)) if positions else 0
        return moves

    @property
    def order_is_stable(self) -> bool:
        orders = self.orders()
        return bool(orders) and all(o == orders[0] for o in orders)

    def unstable_criteria(self, threshold: float = 1.0) -> list[tuple[str, str, float]]:
        """(candidate, criterion, agreement) for every criterion that moved."""
        out = []
        for r in self.repeats:
            for cid, v in r.per_criterion().items():
                if v["agreement"] < threshold:
                    out.append((r.candidate_id, cid, v["agreement"]))
        return sorted(out, key=lambda t: t[2])

    def rows(self) -> list[tuple[str, float, float]]:
        """`(candidate_id, mean, spread)` for everyone who actually ran."""
        return [(r.candidate_id, r.mean, r.spread) for r in self.repeats if r.runs]

    def separation(self) -> list[Pair]:
        return separation(self.rows())

    @property
    def resolution(self) -> float:
        return resolution(self.rows())

    @property
    def places_worth_reading(self) -> int:
        return places_worth_reading(self.separation())

    def to_dict(self) -> dict[str, Any]:
        return {
            "posting_id": self.posting_id,
            "n": self.n,
            "at": self.at or datetime.now(timezone.utc).isoformat(),
            "order_is_stable": self.order_is_stable,
            "orders": self.orders(),
            "rank_movement": self.rank_movement(),
            "resolution": self.resolution,
            "places_worth_reading": self.places_worth_reading,
            "separation": [p.to_dict() for p in self.separation()],
            "unstable_criteria": [
                {"candidate_id": c, "criterion_id": k, "agreement": a}
                for c, k, a in self.unstable_criteria()
            ],
            "candidates": [r.to_dict() for r in self.repeats],
        }

    def render(self) -> str:
        lines = [f"{self.posting_id} -- same inputs, {self.n} runs", ""]
        lines.append(f"  {'candidate':<20} {'mean':>6} {'spread':>7} {'sd':>6}  {'moved':>5}")
        moves = self.rank_movement()
        for r in sorted(self.repeats, key=lambda r: r.mean, reverse=True):
            lines.append(
                f"  {r.candidate_id:<20} {r.mean:>5.0%} {r.spread:>7.0%} "
                f"{r.stdev:>6.3f}  {moves.get(r.candidate_id, 0):>5}"
            )

        lines.append("")
        if self.order_is_stable:
            lines.append("  the order was identical in every run")
        else:
            lines.append("  THE ORDER CHANGED between runs:")
            for i, o in enumerate(self.orders(), 1):
                lines.append(f"    run {i}: {' > '.join(o)}")

        pairs = self.separation()
        if pairs:
            lines.append("")
            lines.append(f"  gap between neighbours, against their own noise "
                         f"(resolution: {self.resolution:.0%})")
            for pr in pairs:
                mark = "ok      " if pr.separated else "NOT SEP."
                #: One decimal, because the interesting pairs are the ones
                #: where gap and noise are within a point of each other, and
                #: whole percents round them into looking identical.
                lines.append(f"    {mark} {pr.above:<18} > {pr.below:<18} "
                             f"gap {pr.gap:>6.1%}   noise {pr.noise:>6.1%}")
            worth = self.places_worth_reading
            if worth >= len(self.rows()):
                lines.append("    every place is separated by more than its own noise")
            elif worth == 1:
                lines.append("    only first place is settled; below it the order is noise")
            elif worth:
                lines.append(f"    the top {worth} places are settled; below them the "
                             f"order is noise")
            else:
                lines.append("    no pair is separated: this is a list, not a ranking")

        unstable = self.unstable_criteria()
        lines.append("")
        if not unstable:
            lines.append("  every criterion got the same strength in every run")
        else:
            lines.append(f"  {len(unstable)} criteria did not hold the same strength:")
            for cid, k, agree in unstable[:12]:
                seen = next(r for r in self.repeats if r.candidate_id == cid).per_criterion()[k]["seen"]
                shape = ", ".join(f"{s} x{n}" for s, n in sorted(seen.items()))
                lines.append(f"    {cid}/{k}: {agree:.0%} agreement -- {shape}")
        return "\n".join(lines)


def stability(make_client: Callable[[], Any], agenda: DraftAgenda,
              cohort: list[Facts], n: int = 3, *, model: str,
              on_run: Callable[[int, int, str], None] | None = None) -> Stability:
    """Screen every candidate n times, against the same agenda.

    The client is rebuilt per call so one run's usage tape is not another's,
    and a run that raises is recorded rather than aborting the batch -- the
    rate of failure is part of what is being measured.
    """
    out = Stability(posting_id=agenda.posting_id, n=n, at=datetime.now(timezone.utc).isoformat())
    for facts in cohort:
        rep = Repeat(candidate_id=facts.candidate_id)
        for i in range(1, n + 1):
            if on_run:
                on_run(i, n, facts.candidate_id)
            try:
                rep.runs.append(screen(make_client(), agenda, facts, model=model))
            except Exception as e:  # noqa: BLE001
                rep.errors.append(f"run {i}: {type(e).__name__}: {e}")
        out.repeats.append(rep)
    return out


def save(s: Stability, out_dir: str | Path, name: str) -> Path:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.json"
    p.write_text(json.dumps(s.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return p
