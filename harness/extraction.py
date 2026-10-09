"""Does the same document produce the same facts twice?

Everything else in this harness measures what happens *after* extraction: how
steadily the screener judges a fact, how much the credit scale moves a
ranking, what a name does. All of it assumes the facts themselves are fixed.

Nobody checked that assumption. If two extractions of one CV disagree, then
drift attributed to the screener is partly drift from upstream, and the
sentence "the screener hesitates, it does not derail" is measuring the wrong
component.

Three things are asked, in increasing order of how much damage they do:

1. **How many facts, and how many were thrown away?** A quote that cannot be
   found in the document is discarded by `intake/cv.py` and counted. That
   rejection rate is a quality signal on the extraction prompt that nobody
   has been reading.
2. **Which facts come back every time?** Matched on the normalised quote,
   because ids are written fresh on each run and quotes are not. A fact
   present in three runs of three is the document speaking; one present in
   one of three is the model deciding what to notice.
3. **Does the same sentence keep its classification?** This is the one that
   matters. `instance` versus `assertion` decides whether a criterion can
   score above `weak` -- the cap that took a keyword-stuffer from 76% to 24%
   rests entirely on it. A sentence that flips between the two makes that cap
   fire inconsistently, and a cap that fires inconsistently is worse than no
   cap, because it looks like a rule.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from intake.cv import Facts, Profile, extract
from intake.posting import normalise

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Repeat:
    """N extractions of one document."""

    candidate_id: str
    runs: list[Facts] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def counts(self) -> list[int]:
        return [len(f.facts) for f in self.runs]

    @property
    def rejected(self) -> list[int]:
        return [len(f.rejected) for f in self.runs]

    @property
    def yield_rate(self) -> float:
        """Kept over kept-plus-thrown-away, across every run."""
        kept = sum(self.counts)
        lost = sum(self.rejected)
        return round(kept / (kept + lost), 4) if (kept + lost) else 0.0

    def by_quote(self) -> dict[str, list[Any]]:
        out: dict[str, list[Any]] = {}
        for run in self.runs:
            for f in run.facts:
                out.setdefault(normalise(f.source_quote), []).append(f)
        return out

    @property
    def core(self) -> list[str]:
        """Quotes every run produced. The part of the document that is not
        a matter of opinion."""
        n = len(self.runs)
        return [q for q, fs in self.by_quote().items() if len(fs) >= n]

    @property
    def fringe(self) -> list[str]:
        n = len(self.runs)
        return [q for q, fs in self.by_quote().items() if 0 < len(fs) < n]

    @property
    def agreement(self) -> float:
        """Share of distinct quotes every run found *with the same span*.

        Strict on purpose, and too strict on its own: the extractor is told
        to split compound bullets, and two runs that split one sentence
        differently produce two spans that never match while carrying the
        same content. Read this next to `coverage`, not instead of it.
        """
        seen = self.by_quote()
        return round(len(self.core) / len(seen), 4) if seen else 0.0

    def quotes_per_run(self) -> list[list[str]]:
        return [[normalise(f.source_quote) for f in run.facts] for run in self.runs]

    @property
    def coverage(self) -> float:
        """Share of distinct quotes every run accounted for, split or not.

        A span counts as accounted for in a run if that run produced it, or
        produced a span containing it, or produced one inside it. That is the
        difference between "the runs disagree about what this document says"
        and "the runs cut the same sentence in different places" -- and only
        the first is a problem.
        """
        per_run = self.quotes_per_run()
        if not per_run:
            return 0.0
        every = list(self.by_quote())
        if not every:
            return 0.0
        held = 0
        for q in every:
            if all(any(q == o or q in o or o in q for o in run) for run in per_run):
                held += 1
        return round(held / len(every), 4)

    def missed(self) -> list[str]:
        """Spans at least one run did not account for, even loosely."""
        per_run = self.quotes_per_run()
        return [q for q in self.by_quote()
                if not all(any(q == o or q in o or o in q for o in run) for run in per_run)]

    def evidence_flips(self) -> list[tuple[str, dict[str, int]]]:
        """Quotes classified differently between runs. The load-bearing one."""
        out = []
        for q, fs in self.by_quote().items():
            kinds = Counter(f.evidence for f in fs)
            if len(kinds) > 1:
                out.append((q, dict(kinds)))
        return sorted(out, key=lambda t: -sum(t[1].values()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "runs": len(self.runs),
            "counts": self.counts,
            "rejected": self.rejected,
            "yield_rate": self.yield_rate,
            "agreement": self.agreement,
            "coverage": self.coverage,
            "missed": [q[:160] for q in self.missed()],
            #: Kept so the metric can be improved later without paying for
            #: the runs again.
            "quotes_per_run": self.quotes_per_run(),
            "core": len(self.core),
            "fringe": len(self.fringe),
            "evidence_flips": [{"quote": q[:160], "seen": k}
                               for q, k in self.evidence_flips()],
            "errors": self.errors,
        }


@dataclass
class ExtractionStability:
    n: int
    repeats: list[Repeat] = field(default_factory=list)
    at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"n": self.n, "at": self.at or datetime.now(timezone.utc).isoformat(),
                "candidates": [r.to_dict() for r in self.repeats]}

    def render(self) -> str:
        lines = [f"extraction, same document {self.n} times", ""]
        lines.append(f"  {'candidate':<20}{'facts':>14}{'same span':>11}"
                     f"{'coverage':>10}{'kept':>7}{'flips':>7}")
        for r in self.repeats:
            if not r.runs:
                lines.append(f"  {r.candidate_id:<20}  nothing completed")
                continue
            counts = "/".join(str(c) for c in r.counts)
            lines.append(f"  {r.candidate_id:<20}{counts:>14}{r.agreement:>10.0%}"
                         f"{r.coverage:>10.0%}{r.yield_rate:>7.0%}"
                         f"{len(r.evidence_flips()):>7}")

        flips = [(r.candidate_id, q, k) for r in self.repeats
                 for q, k in r.evidence_flips()]
        lines.append("")
        if not flips:
            lines.append("  no sentence changed its classification between runs --")
            lines.append("  the assertion cap fires on the same facts every time")
        else:
            lines.append(f"  {len(flips)} sentence(s) classified differently between "
                         f"runs. The cap depends on this:")
            for cid, q, kinds in flips[:8]:
                shape = ", ".join(f"{k} x{v}" for k, v in sorted(kinds.items()))
                lines.append(f'    {cid}: {shape} -- "{q[:80]}"')

        fringe = sum(len(r.fringe) for r in self.repeats)
        missed = sum(len(r.missed()) for r in self.repeats)
        total = sum(len(r.by_quote()) for r in self.repeats)
        if total:
            lines.append("")
            lines.append(f"  {fringe} of {total} spans were not produced identically by "
                         f"every run ({fringe / total:.0%})")
            lines.append(f"  {missed} of {total} were not accounted for at all -- the rest "
                         f"is the same sentence cut differently ({missed / total:.0%})")
        return "\n".join(lines)


def stability(make_client: Callable[[], Any], profiles: list[Profile], n: int = 3, *,
              model: str, workers: int = 4,
              on_run: Callable[[str, int, int], None] | None = None) -> ExtractionStability:
    """Extract every document n times, in parallel.

    Repeating one document is the case a pool helps most -- the runs are
    independent by construction, since the whole point is that none of them
    may influence another. A client per task, so one run's usage tape is not
    another's.
    """
    out = ExtractionStability(n=n, at=datetime.now(timezone.utc).isoformat())
    by_id = {p.id: Repeat(candidate_id=p.id) for p in profiles}

    def work(profile: Profile, i: int):
        return profile.id, i, extract(make_client(), profile, model=model)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(work, p, i): (p.id, i)
                   for p in profiles for i in range(1, n + 1)}
        for fut in as_completed(futures):
            cid, i = futures[fut]
            try:
                cid, i, facts = fut.result()
            except Exception as e:  # noqa: BLE001
                by_id[cid].errors.append(f"run {i}: {type(e).__name__}: {e}")
                continue
            by_id[cid].runs.append(facts)
            if on_run:
                on_run(cid, len(by_id[cid].runs), n)

    out.repeats = [by_id[p.id] for p in profiles]
    return out


def save(s: ExtractionStability, out_dir: str | Path, name: str) -> Path:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.json"
    p.write_text(json.dumps(s.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return p
