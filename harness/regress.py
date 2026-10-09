"""Every failure found, frozen, and replayed against the code as it is now.

A unit test says what the author thought could go wrong. A case here says
what *did* go wrong, on a run that happened, with the model's own answer kept
on disk. Replaying it costs nothing: the model's answer is read back from the
record and pushed through today's Python rules, so the only thing under test
is the code that is supposed to stop the failure -- which is the thing a
refactor breaks without anyone noticing.

    ./.venv/bin/python -m harness regress           # replay every case
    ./.venv/bin/python -m harness regress --list    # what is in the library

Two rules keep the library honest:

* **It only grows.** `LEDGER` lists every case ever added. A case whose file
  disappears fails the run; a failure that stops mattering is not deleted,
  it stays green.
* **It says where each case came from.** `recorded` means the input is a
  model answer that was actually returned on a real run. `reconstructed`
  means the original answer was not kept, and the case rebuilds the failure
  on real facts with a model answer written to be as bad as the one seen.
  The difference is printed, because a reconstructed case proves the rule
  holds, not that the rule was ever what stopped the model.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
LIBRARY = Path(__file__).resolve().parent / "regressions"
LEDGER = LIBRARY / "LEDGER"

ORIGINS = ("recorded", "reconstructed")


@dataclass
class Case:
    id: str
    found: str
    what_broke: str
    guards: str
    origin: str
    kind: str
    inputs: dict[str, Any] = field(default_factory=dict)
    expect: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None

    @classmethod
    def load(cls, path: Path) -> "Case":
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        missing = [k for k in ("id", "found", "what_broke", "guards", "origin", "kind")
                   if not raw.get(k)]
        if missing:
            raise ValueError(f"{path.name}: missing {', '.join(missing)}")
        if raw["origin"] not in ORIGINS:
            raise ValueError(f"{path.name}: origin must be one of {', '.join(ORIGINS)}")
        if raw["id"] != path.stem:
            raise ValueError(f"{path.name}: id {raw['id']!r} does not match the file name")
        return cls(id=raw["id"], found=str(raw["found"]), what_broke=raw["what_broke"].strip(),
                   guards=raw["guards"].strip(), origin=raw["origin"], kind=raw["kind"],
                   inputs=raw.get("inputs", {}), expect=raw.get("expect", {}), path=path)


@dataclass
class Result:
    case: Case
    problems: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.problems


def _p(rel: str) -> Path:
    return ROOT / rel


# -- replays ---------------------------------------------------------------
#
# Each takes a case and returns what no longer holds, in words. An empty list
# is a pass. They import lazily so that `--list` works without the rest.


def replay_screening(c: Case) -> tuple[Any, Any]:
    """(today's screening of the case's answer, the record it came from).

    Separate from the check so that the statistics report can quote the score
    a case replays to -- "42.5% with the cap" is this function's output, not
    a number copied into a README.
    """
    from intake.cv import load as load_facts
    from intake.posting import load as load_agenda
    from nbh.llm import ReplayClient
    from screen import CREDIT, load as load_screening, screen

    agenda = load_agenda(_p(c.inputs["agenda"]))
    facts = load_facts(_p(c.inputs["facts"]))
    record = load_screening(_p(c.inputs["record"]))

    # What the model said, before any cap: a capped assessment keeps the
    # strength it arrived with, so the original answer is recoverable.
    inflate = c.inputs.get("inflate", "")
    answer = {"assessments": [
        {"criterion_id": a.criterion_id,
         "strength": inflate if (inflate and a.fact_ids) else (a.capped_from or a.strength),
         "fact_ids": a.fact_ids, "reasoning": a.reasoning}
        for a in record.assessments]}
    # The scale the record was scored on, so a later change to the shipped
    # default does not read as a regression of the cap.
    credit = dict(CREDIT)
    for a in record.assessments:
        if a.credit_value is not None and a.strength != "unknown":
            credit[a.strength] = a.credit_value

    got = screen(ReplayClient([answer]), agenda, facts, model=record.model, credit=credit)
    return got, record


def _screening(c: Case) -> list[str]:
    """Push a screener's answer through `screen.screen` as the code is now."""
    got, record = replay_screening(c)
    by = {a.criterion_id: a for a in got.assessments}
    problems: list[str] = []

    for cid, want in (c.expect.get("strength") or {}).items():
        have = by[cid].strength if cid in by else "(missing)"
        if have != want:
            problems.append(f"{cid}: {have}, expected {want}")
    for cid, want in (c.expect.get("capped_why") or {}).items():
        have = by[cid].capped_why if cid in by else "(missing)"
        if have != want:
            problems.append(f"{cid}: capped because {have or 'nothing'!r}, expected {want!r}")
    ladder = ("unknown", "weak", "moderate", "strong")
    for rests_on, ceiling in (c.expect.get("never_above") or {}).items():
        for a in got.assessments:
            if a.rests_on == rests_on and ladder.index(a.strength) > ladder.index(ceiling):
                problems.append(f"{a.criterion_id}: {a.strength} on {rests_on}, "
                                f"ceiling is {ceiling}")
    if "score_max" in c.expect and got.score > c.expect["score_max"]:
        problems.append(f"score {got.score:.1%} above {c.expect['score_max']:.1%}")
    if c.expect.get("same_score") and abs(got.score - record.score) > 1e-9:
        problems.append(f"score {got.score:.2%} on replay, {record.score:.2%} on the record")
    return problems


def _trial_design(c: Case) -> list[str]:
    """Push a recorded day design back through `trial.verify`."""
    import json

    from intake.posting import load as load_agenda
    from trial import verify

    agenda = load_agenda(_p(c.inputs["agenda"]))
    answer = json.loads(_p(c.inputs["design"]).read_text(encoding="utf-8"))["answer"]
    d = verify(agenda, answer)
    problems: list[str] = []

    for eid, why in c.expect.get("rejected") or []:
        if not any(r["id"] == eid and why in r["reason"] for r in d.rejected):
            problems.append(f"{eid} was not rejected for {why!r}")
    if c.expect.get("nothing_rejected") and d.rejected:
        problems.append(f"rejected: {', '.join(r['id'] for r in d.rejected)}")
    if "under_read_gates" in c.expect and d.under_read_gates != c.expect["under_read_gates"]:
        problems.append(f"under-read gates {d.under_read_gates}, "
                        f"expected {c.expect['under_read_gates']}")
    return problems


def _hostile(c: Case) -> list[str]:
    """A document with a payload: is it seen, and did it buy any fact?"""
    from intake import hostile
    from intake.cv import load as load_facts
    from intake.posting import load as load_agenda, normalise

    raw = _p(c.inputs["document"]).read_text(encoding="utf-8")
    agenda = load_agenda(_p(c.inputs["agenda"]))
    report = hostile.scan(raw, vocabulary=hostile.agenda_vocabulary(agenda))
    problems: list[str] = []

    kinds = {f.kind for f in report.findings}
    for k in c.expect.get("kinds") or []:
        if k not in kinds:
            problems.append(f"no {k!r} finding")
    if "facts" in c.inputs:
        # The extraction this case checks read the raw document, payload and
        # all. Every fact it kept must still quote what a reader can see.
        visible = normalise(hostile.visible_text(raw))
        for f in load_facts(_p(c.inputs["facts"])).facts:
            if normalise(f.source_quote) not in visible:
                problems.append(f"{f.id} quotes text a reader cannot see: {f.source_quote!r}")
    return problems


def _card(c: Case) -> list[str]:
    """A filled trial-day card: refused when it should be, and why."""
    from intake.posting import load as load_agenda
    from trial import CardError, read_card

    agenda = load_agenda(_p(c.inputs["agenda"])) if "agenda" in c.inputs else None
    want = c.expect.get("refused_with") or []
    try:
        read_card(c.inputs["card"], agenda)
    except CardError as e:
        said = " ".join(e.problems if hasattr(e, "problems") else [str(e)])
        return [f"refused, but never said {w!r}" for w in want if w not in said]
    return ["the card was accepted"] if want else []


def _blind(c: Case) -> list[str]:
    """The screener's prompt: the name goes in only when asked for."""
    from intake.cv import load as load_facts
    from intake.posting import load as load_agenda
    from screen import screen

    class Listening:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def structured(self, **kw: Any) -> dict[str, Any]:
            self.prompts.append(f"{kw.get('system', '')}\n{kw.get('user', '')}")
            return {"assessments": []}

    agenda = load_agenda(_p(c.inputs["agenda"]))
    facts = load_facts(_p(c.inputs["facts"]))
    problems: list[str] = []
    if not facts.name:
        return ["the facts carry no name, so this case proves nothing"]
    for anonymous, should_see in ((True, False), (False, True)):
        ear = Listening()
        screen(ear, agenda, facts, model="replay", anonymous=anonymous)
        sees = facts.name in ear.prompts[0]
        if sees != should_see:
            problems.append(f"anonymous={anonymous}: the name "
                            f"{'reached' if sees else 'did not reach'} the prompt")
    return problems


def _contracts(c: Case) -> list[str]:
    import json

    from harness import contracts

    payload = json.loads(_p(c.inputs["trace"]).read_text(encoding="utf-8"))
    report = contracts.check(payload)
    problems = [f"{r.contract}: {r.detail}" for r in report.failures]
    if len(report.results) < c.expect.get("at_least", 0):
        problems.append(f"{len(report.results)} contracts ran, "
                        f"expected at least {c.expect['at_least']}")
    return problems


def _judge(c: Case) -> list[str]:
    """A judge's recorded answer, pushed back through today's quote check."""
    import json

    from judge import seeds
    from judge.judge import load_principles, verify

    trace = json.loads(_p(c.inputs["trace"]).read_text(encoding="utf-8"))
    record = json.loads(_p(c.inputs["record"]).read_text(encoding="utf-8"))
    run = next(r for r in record["runs"] if r["seed"] == c.inputs.get("seed", ""))
    seed = next((s for s in seeds.SEEDS if s.id == run["seed"]), None)
    if seed:
        trace = seed.apply(trace)
    kept, dropped = verify(trace, run["answer"], load_principles())
    problems: list[str] = []
    for why in c.expect.get("dropped_with") or []:
        if not any(why in d["reason"] for d in dropped):
            problems.append(f"nothing dropped for {why!r}; kept {len(kept)} finding(s)")
    return problems


def _pdf(c: Case) -> list[str]:
    """A PDF, read as a person reads it."""
    from intake import pdf

    r = pdf.read(_p(c.inputs["document"]).read_bytes())
    problems = [f"{s!r} is not in the text read" for s in c.expect.get("text_contains") or []
                if s not in r.text]
    if "hidden" in c.expect and len(r.hidden) != c.expect["hidden"]:
        problems.append(f"{len(r.hidden)} hidden block(s), expected {c.expect['hidden']}: "
                        + "; ".join(h.why for h in r.hidden[:3]))
    return problems


KINDS: dict[str, Callable[[Case], list[str]]] = {
    "pdf": _pdf,
    "judge": _judge,
    "screening": _screening,
    "trial_design": _trial_design,
    "hostile": _hostile,
    "card": _card,
    "blind": _blind,
    "contracts": _contracts,
}


# -- the library -----------------------------------------------------------


def ledger(path: Path = LEDGER) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    return [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]


def cases(library: Path = LIBRARY) -> list[Case]:
    return [Case.load(p) for p in sorted(library.glob("*.toml"))]


def run(library: Path = LIBRARY) -> tuple[list[Result], list[str]]:
    """Every case, replayed. Returns the results and what is wrong with the
    library itself -- a case gone missing, a case never entered in the ledger."""
    found = cases(library)
    ids = {c.id for c in found}
    book = ledger(library / "LEDGER")
    shelf = [f"{i}: in the ledger, but its file is gone. The library only grows."
             for i in book if i not in ids]
    shelf += [f"{c.id}: not in the ledger. Add it, so its removal would be noticed."
              for c in found if c.id not in book]

    results = []
    for c in found:
        replay = KINDS.get(c.kind)
        if replay is None:
            results.append(Result(c, [f"unknown kind {c.kind!r}"]))
            continue
        try:
            results.append(Result(c, replay(c)))
        except Exception as e:  # a case that cannot run is a failing case
            results.append(Result(c, [f"could not replay: {type(e).__name__}: {e}"]))
    return results, shelf


def render(results: list[Result], shelf: list[str]) -> str:
    lines = []
    for r in results:
        mark = "ok  " if r.passed else "FAIL"
        lines.append(f"{mark} {r.case.id}  [{r.case.origin}, found {r.case.found}]")
        for p in r.problems:
            lines.append(f"       {p}")
        if not r.passed:
            lines.append(f"       what broke then: {r.case.what_broke}")
            lines.append(f"       what should stop it: {r.case.guards}")
    for s in shelf:
        lines.append(f"FAIL {s}")
    held = sum(r.passed for r in results)
    rec = sum(r.case.origin == "recorded" for r in results)
    lines.append("")
    lines.append(f"{held}/{len(results)} held -- {rec} replay a recorded model answer, "
                 f"{len(results) - rec} are reconstructed. No model was called.")
    return "\n".join(lines)


def listing(found: list[Case]) -> str:
    lines = []
    for c in found:
        lines.append(f"{c.id}  [{c.kind}, {c.origin}, found {c.found}]")
        lines.append(f"    broke:  {c.what_broke}")
        lines.append(f"    guard:  {c.guards}")
    return "\n".join(lines)
