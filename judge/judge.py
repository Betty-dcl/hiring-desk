"""Read one recorded exchange against the principles, and keep only what holds.

One model call per trace, on the smaller model (`nbh.llm.JUDGE_MODEL`). What
comes back is checked in Python before anything is kept:

* the principle is one of `principles.toml`;
* every location it names exists -- a turn, an evidence row, a verdict;
* every quote is found, verbatim, at one of those locations;
* a cross-turn principle names at least two locations, because a
  contradiction with nothing to contradict is an opinion.

A finding that fails any of these is dropped and counted, with the reason.
The raw answer is kept, so the checks can be re-run on it for free.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from intake.posting import normalise

HERE = Path(__file__).resolve().parent
PRINCIPLES = HERE / "principles.toml"


@dataclass(frozen=True)
class Principle:
    id: str
    axis: str
    cross_turn: bool
    text: str
    not_this: str


def load_principles(path: Path = PRINCIPLES) -> list[Principle]:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    out = [Principle(p["id"], p["axis"], bool(p.get("cross_turn")), p["text"].strip(),
                     p.get("not_this", "").strip()) for p in raw.get("principle", [])]
    ids = [p.id for p in out]
    if len(ids) != len(set(ids)):
        raise ValueError("two principles share an id")
    return out


def fingerprint(principles: list[Principle]) -> str:
    """Which wording of the principles a record was judged under."""
    blob = json.dumps([asdict(p) for p in principles], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


# -- what the judge reads --------------------------------------------------


def locations(trace: dict[str, Any]) -> dict[str, str]:
    """Every place a finding may point at, with the text found there."""
    ex = trace.get("exchange", {})
    v = trace.get("verdicts", {})
    out: dict[str, str] = {}
    for m in ex.get("transcript", []):
        out[f"turn:{m['turn']}"] = m.get("body", "")
    for row in v.get("criteria", []):
        if row.get("quote"):
            out[f"evidence:{row['id']}"] = (f"{row['id']} recorded as {row.get('strength')} "
                                           f"from turn {row.get('turn')}: {row['quote']}")
    for side in ("company", "candidate"):
        note = (v.get(side) or {}).get("note", "")
        if note:
            out[f"verdict:{side}"] = note
    return out


def render(trace: dict[str, Any]) -> str:
    ex = trace.get("exchange", {})
    v = trace.get("verdicts", {})
    lines = ["# Transcript", ""]
    for m in ex.get("transcript", []):
        crit = f" · {m['criterion_id']}" if m.get("criterion_id") else ""
        lines += [f"[turn:{m['turn']}] {m['speaker']} · {m['act']}{crit}", m.get("body", ""), ""]
    rows = [r for r in v.get("criteria", []) if r.get("quote")]
    if rows:
        lines += ["# Evidence the company recorded", ""]
        for r in rows:
            lines += [f"[evidence:{r['id']}] criterion: {r.get('question', r['id'])}",
                      f"recorded as {r.get('strength')}, from turn {r.get('turn')}",
                      f"quote: {r['quote']}", ""]
    for side, title in (("company", "Verdict for the company"), ("candidate", "Note to the candidate")):
        note = (v.get(side) or {}).get("note", "")
        if note:
            lines += [f"# {title} [verdict:{side}]", note, ""]
    return "\n".join(lines)


SYSTEM = """\
You review one recorded exchange between two agents: one speaking for a
company, one for a job candidate. A separate program has already checked
everything that can be checked mechanically. You are asked only the questions
below, and each one is narrow on purpose.

For each problem you find, name the principle, the locations it involves
(`turn:N`, `evidence:<id>`, `verdict:company`, `verdict:candidate`, exactly as
they appear in brackets), and quote the words that show it -- copied exactly
from those locations, no paraphrase. A principle marked cross-turn needs at
least two locations: the earlier words and the later ones.

Report only what the text shows. An exchange with nothing wrong is common,
and an empty list is a complete answer. Do not report something because a
principle seems to invite it, and do not score or grade the exchange.
"""


def _principles_block(principles: list[Principle]) -> str:
    out = []
    for p in principles:
        tag = " (cross-turn)" if p.cross_turn else ""
        out.append(f"- `{p.id}`{tag}: {p.text}\n  Not this: {p.not_this}")
    return "\n".join(out)


def _schema(principle_ids: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "principle_id": {"type": "string", "enum": principle_ids},
                        "at": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                        "quotes": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                        "why": {"type": "string"},
                    },
                    "required": ["principle_id", "at", "quotes", "why"],
                },
            },
        },
        "required": ["findings"],
    }


# -- what is kept ----------------------------------------------------------


@dataclass
class Finding:
    principle_id: str
    at: list[str]
    quotes: list[str]
    why: str


@dataclass
class Judgement:
    trace: str
    judged_at: str
    model: str
    principles: str
    findings: list[Finding] = field(default_factory=list)
    dropped: list[dict[str, Any]] = field(default_factory=list)
    answer: dict[str, Any] = field(default_factory=dict)
    #: Set when the trace was altered on purpose -- see `seeds.py`.
    seed: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace": self.trace, "seed": self.seed, "judged_at": self.judged_at,
            "model": self.model, "principles": self.principles,
            "decides": False, "is_a_quality_score": False,
            "findings": [asdict(f) for f in self.findings],
            "dropped": self.dropped, "answer": self.answer,
        }


def verify(trace: dict[str, Any], answer: dict[str, Any],
           principles: list[Principle]) -> tuple[list[Finding], list[dict[str, Any]]]:
    """Pure: the same answer on the same trace keeps the same findings."""
    by_id = {p.id: p for p in principles}
    where = locations(trace)
    norm = {k: normalise(v) for k, v in where.items()}
    kept: list[Finding] = []
    dropped: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()

    for item in answer.get("findings", []) or []:
        pid = str(item.get("principle_id", "")).strip()
        at = [str(a).strip() for a in item.get("at", []) or [] if str(a).strip()]
        quotes = [str(q).strip() for q in item.get("quotes", []) or [] if str(q).strip()]

        def drop(reason: str) -> None:
            dropped.append({"principle_id": pid, "at": at, "reason": reason})

        if pid not in by_id:
            drop("not a principle in principles.toml")
            continue
        unreal = [a for a in at if a not in where]
        if unreal:
            drop(f"no such location: {', '.join(unreal)}")
            continue
        if not at or not quotes:
            drop("no location or no quote")
            continue
        lost = [q for q in quotes if not any(normalise(q) in norm[a] for a in at)]
        if lost:
            drop(f"quote not found at the locations it names: {lost[0][:80]!r}")
            continue
        if by_id[pid].cross_turn and len(set(at)) < 2:
            drop("cross-turn principle with a single location")
            continue
        key = (pid, tuple(sorted(set(at))))
        if key in seen:
            continue
        seen.add(key)
        kept.append(Finding(pid, at, quotes, str(item.get("why", "")).strip()))
    return kept, dropped


def judge(client: Any, trace: dict[str, Any], *, model: str, trace_name: str = "",
          principles: list[Principle] | None = None, seed: str = "",
          max_tokens: int = 3000) -> Judgement:
    principles = principles or load_principles()
    answer = client.structured(
        model=model,
        system=SYSTEM,
        user=f"# Principles\n{_principles_block(principles)}\n\n{render(trace)}",
        schema=_schema([p.id for p in principles]),
        tool_name="report_findings",
        tool_description="List what the exchange shows against the principles, or nothing.",
        max_tokens=max_tokens,
    )
    kept, dropped = verify(trace, answer, principles)
    return Judgement(trace=trace_name, judged_at=datetime.now(timezone.utc).isoformat(),
                     model=model, principles=fingerprint(principles), findings=kept,
                     dropped=dropped, answer=answer, seed=seed)


def save(j: Judgement, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(j.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load(path: str | Path) -> Judgement:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return Judgement(trace=raw["trace"], judged_at=raw["judged_at"], model=raw["model"],
                     principles=raw["principles"],
                     findings=[Finding(**f) for f in raw.get("findings", [])],
                     dropped=raw.get("dropped", []), answer=raw.get("answer", {}),
                     seed=raw.get("seed", ""))
