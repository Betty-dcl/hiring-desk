"""Run the same scenario many times, and report what did not hold.

One run tells you the system can do the thing. It does not tell you the
system does the thing. Those are different claims, and almost every demo
conflates them.

The distinction has a name in the agent literature -- pass@k against pass^k.
pass@k asks whether at least one of k attempts succeeded, which is a
statement about capability and is what a demo shows you. pass^k asks whether
*all* k succeeded, which is a statement about reliability and is the only one
that matters if the thing is going to run unattended against real people.
`arXiv:2406.12045` reports the gap; the point of this module is to measure it
here rather than cite it.

There is a second number this produces that is, for a scoring system,
arguably worse news than a failed contract: the spread of `fit_score` across
identical runs. The score is computed arithmetic over recorded evidence, so
it cannot drift on its own -- but what evidence gets recorded depends on what
the company's agent chose to probe, and that varies. If the same candidate,
against the same agenda, scores 0.59 on one run and 0.81 on the next, the
number is not a measurement. Better to know that and say it.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness import contracts
from nbh import verdict as verdict_mod
from nbh.agent import run_exchange
from nbh.llm import AGENT_MODEL, JUDGE_MODEL
from nbh.mandates import load_candidate, load_role

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Template:
    """A frozen scenario. Cloned per run, never mutated.

    Everything that could vary between runs is pinned here, so that when two
    runs differ, the model is the only thing that could have caused it.
    """

    id: str
    role_path: str
    candidate_path: str
    max_turns: int = 24
    model: str = AGENT_MODEL
    assessor_model: str = JUDGE_MODEL
    boundaries: tuple[contracts.Boundary, ...] = contracts.DEFAULT_BOUNDARIES

    def load(self) -> tuple[Any, Any]:
        return load_role(ROOT / self.role_path), load_candidate(ROOT / self.candidate_path)


@dataclass
class Batch:
    """N runs of one template, and what held across them."""

    template: Template
    traces: list[dict[str, Any]] = field(default_factory=list)
    reports: list[contracts.Report] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.reports)

    def per_contract(self) -> dict[str, dict[str, Any]]:
        """For each contract: how often it held, and both reliability readings.

        `pass_at_k` is true when any run passed -- the demo number.
        `pass_pow_k` is true only when every run passed -- the one to quote.
        """
        out: dict[str, dict[str, Any]] = {}
        for name in contracts.names():
            verdicts = [
                next((r for r in rep.results if r.contract == name), None)
                for rep in self.reports
            ]
            got = [v for v in verdicts if v is not None]
            passes = sum(1 for v in got if v.passed)
            out[name] = {
                "runs": len(got),
                "passed": passes,
                "rate": round(passes / len(got), 3) if got else None,
                "pass_at_k": passes > 0,
                "pass_pow_k": bool(got) and passes == len(got),
                "first_failure": next((v.detail for v in got if not v.passed), None),
            }
        return out

    def _computed(self, key: str) -> list[float]:
        return [
            t.get("verdicts", {}).get("computed", {}).get(key)
            for t in self.traces
            if t.get("verdicts", {}).get("computed", {}).get(key) is not None
        ]

    def score_spread(self) -> dict[str, Any]:
        """How much the computed numbers moved across identical runs."""
        out: dict[str, Any] = {}
        for key in ("fit_score", "coverage"):
            vals = self._computed(key)
            if not vals:
                continue
            out[key] = {
                "n": len(vals),
                "min": round(min(vals), 3),
                "max": round(max(vals), 3),
                "mean": round(statistics.fmean(vals), 3),
                "spread": round(max(vals) - min(vals), 3),
                "stdev": round(statistics.stdev(vals), 3) if len(vals) > 1 else 0.0,
            }
        bands: dict[str, int] = {}
        for t in self.traces:
            b = t.get("verdicts", {}).get("computed", {}).get("recommendation")
            if b:
                bands[b] = bands.get(b, 0) + 1
        out["recommendation"] = {
            "bands": bands,
            "stable": len(bands) <= 1,
        }
        return out

    def usage(self) -> dict[str, Any]:
        calls = sum(t.get("usage", {}).get("calls", 0) for t in self.traces)
        cost = sum(t.get("usage", {}).get("cost_usd", 0.0) for t in self.traces)
        tokens = sum(
            t.get("usage", {}).get("input_tokens", 0) + t.get("usage", {}).get("output_tokens", 0)
            for t in self.traces
        )
        return {"calls": calls, "tokens": tokens, "list_price_usd": round(cost, 4)}

    def to_dict(self) -> dict[str, Any]:
        return {
            "template": self.template.id,
            "n": self.n,
            "reliability": self.per_contract(),
            "score_spread": self.score_spread(),
            "usage": self.usage(),
            "errors": self.errors,
        }

    def render(self) -> str:
        """The report as a person reads it, worst news first."""
        lines = [f"{self.template.id} -- {self.n} runs", ""]
        per = self.per_contract()
        broke = {k: v for k, v in per.items() if not v["pass_pow_k"]}
        held = {k: v for k, v in per.items() if v["pass_pow_k"]}

        if broke:
            lines.append("did not hold on every run:")
            for name, v in sorted(broke.items(), key=lambda kv: kv[1]["rate"] or 0):
                lines.append(f"  {name:<34} {v['passed']}/{v['runs']}   pass@k yes, pass^k NO")
                if v["first_failure"]:
                    lines.append(f"      {v['first_failure']}")
            lines.append("")
        lines.append(f"held on every run: {len(held)}/{len(per)} contracts")
        if held:
            lines.append("  " + ", ".join(sorted(held)))

        spread = self.score_spread()
        lines.append("")
        for key in ("fit_score", "coverage"):
            if key in spread:
                s = spread[key]
                lines.append(
                    f"{key:<10} mean {s['mean']:.2f}  range {s['min']:.2f}-{s['max']:.2f}  "
                    f"spread {s['spread']:.2f}  sd {s['stdev']:.2f}"
                )
        rec = spread.get("recommendation", {})
        if rec.get("bands"):
            shape = ", ".join(f"{k} x{v}" for k, v in sorted(rec["bands"].items()))
            verdict = "stable" if rec["stable"] else "UNSTABLE -- same candidate, different bands"
            lines.append(f"{'band':<10} {shape}  ({verdict})")

        u = self.usage()
        lines.append("")
        lines.append(
            f"cost   {u['calls']} calls, {u['tokens']:,} tokens, "
            f"${u['list_price_usd']:.2f} at API list price"
        )
        if self.errors:
            lines.append("")
            lines.append(f"runs that did not complete: {len(self.errors)}")
            for e in self.errors:
                lines.append(f"  {e}")
        return "\n".join(lines)


def once(client: Any, template: Template) -> dict[str, Any]:
    """One run, as a trace payload identical in shape to `run.py`'s output."""
    role, candidate = template.load()
    run = run_exchange(
        client,
        role,
        candidate,
        max_turns=template.max_turns,
        model=template.model,
        assessor_model=template.assessor_model,
    )
    verdicts = verdict_mod.render(
        client, run.exchange, role, candidate, model=template.assessor_model
    )
    return {
        "meta": {
            "at": datetime.now(timezone.utc).isoformat(),
            "template": template.id,
            "role": role.id,
            "candidate": candidate.id,
            "model": template.model,
            "assessor_model": template.assessor_model,
        },
        **run.to_dict(),
        "verdicts": verdicts,
        "usage": client.usage.to_dict(),
    }


def batch(make_client, template: Template, n: int, *, out_dir: Path | None = None,
          on_run=None) -> Batch:
    """Run a template n times.

    `make_client` is a factory rather than a client, because each run gets a
    fresh usage tape -- otherwise run 5 reports the cost of runs 1 through 5.
    A run that raises is recorded and the batch continues: a harness that
    stops at the first failure cannot tell you a failure's rate, and the rate
    is the whole question.
    """
    b = Batch(template=template)
    out_dir = out_dir or (ROOT / "runs" / template.id)
    out_dir.mkdir(parents=True, exist_ok=True)

    for i in range(1, n + 1):
        try:
            payload = once(make_client(), template)
        except Exception as e:  # noqa: BLE001 -- the rate of failure is the measurement
            b.errors.append(f"run {i}: {type(e).__name__}: {e}")
            if on_run:
                on_run(i, n, None)
            continue
        report = contracts.check(payload, boundaries=template.boundaries)
        payload["contracts"] = report.to_dict()
        (out_dir / f"{i:03d}.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        b.traces.append(payload)
        b.reports.append(report)
        if on_run:
            on_run(i, n, report)
    return b
