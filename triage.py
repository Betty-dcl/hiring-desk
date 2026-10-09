#!/usr/bin/env python3
"""Score a cohort against one posting, and keep everyone in view.

    # every candidate whose facts are on file, scored against a posting
    ./.venv/bin/python triage.py score --posting founders_associate

    # the board: state, score, who read it, what is going stale, what is due
    ./.venv/bin/python triage.py board --posting founders_associate

    # where one number came from, line by line
    ./.venv/bin/python triage.py explain --candidate ines_abadi --posting founders_associate

    # what changed about someone since they last applied
    ./.venv/bin/python triage.py changes --before runs/facts/facts_x.json \\
        --after runs/facts_with_letter/facts_x.json

`score` is the one that costs anything: one model call per candidate. It runs
them in parallel and **resumes**: a candidate already scored against this
posting is skipped, and each result is written the moment it arrives. A batch
that dies at the twenty-seventh CV -- a spend limit, a dropped network -- keeps
the twenty-six and continues where it stopped. The others read what is already
on disk.

The reason `score` is worth running on people who applied months ago: the
facts were extracted once and kept, so scoring an archive against a posting
opened today needs no re-reading of anybody's CV. A candidate who was wrong
for one role is often right for the next one, and the usual reason nobody
checks is that the last role's work was thrown away with the PDF.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import asdict, dataclass
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from intake.cv import changed, load as load_facts
from intake.letter import motivation
from intake.posting import load as load_agenda
import settings as settings_mod
from pipeline import Event, Pipeline, load as load_pipeline, save as save_pipeline
from screen import (Ranking, explain, leverage, load as load_screening, next_questions,
                    save as save_screening, screen)


def _agenda_path(posting: str) -> Path:
    return ROOT / "mandates" / "generated" / f"role_{posting}.provenance.json"


def _pipeline_path(posting: str) -> Path:
    return ROOT / "runs" / "pipeline" / f"{posting}.json"


def _drift_record(posting: str) -> Path | None:
    """The stability record to read drift from, if one has been taken.

    Matched by prefix rather than by an exact name: records carry labels
    (`_rubric_v1`, `_personas`) and a hard-coded filename rots the first time
    someone labels a run.
    """
    d = ROOT / "runs" / "stability"
    if not d.exists():
        return None
    found = sorted(d.glob(f"{posting}_n*.json"))
    #: A plain measurement before any variant of it.
    found.sort(key=lambda f: ("rubric_v2" in f.name, "with_letter" in f.name, f.name))
    return found[0] if found else None


def _spreads(posting: str) -> dict[str, float]:
    """Measured drift, when the stability harness has been run."""
    f = _drift_record(posting)
    if not f:
        return {}
    d = json.loads(f.read_text(encoding="utf-8"))
    return {c["candidate_id"]: c["spread"] for c in d.get("candidates", []) if c.get("runs")}


def any_order_noise(posting: str, candidate: str, setting: Any = None) -> float | None:
    """The gap under which the desk shows this candidate's neighbours as "any order".

    By default it is measured: the gap at which two single screenings of the
    same input would come out in the wrong order one time in twenty, from
    every identical-input repeat on disk (`harness/drift.py`), the same for
    everyone on the posting -- the drift belongs to the screener, not to the
    person. `[any_order]` in desk.toml can choose the older rule (each
    person's own range, nobody unmeasured ever grouped) or a fixed number of
    points. None means nothing measured and nothing is grouped.
    """
    from harness.drift import any_order_setting, measured_gap

    s = setting or any_order_setting()
    if s.rule == "spread":
        return _spreads(posting).get(candidate)
    if s.rule == "fixed":
        return s.fixed
    return measured_gap(posting, s.swap)


def _open_pipeline(posting: str) -> Pipeline:
    p = _pipeline_path(posting)
    return load_pipeline(p) if p.exists() else Pipeline(posting_id=posting)


def _screening_path(candidate: str, posting: str) -> Path:
    return ROOT / "runs" / "screenings" / f"{candidate}_{posting}.json"


def to_do(cohort: list[Any], posting: str, again: bool = False) -> tuple[list[Any], list[Any]]:
    """Split a cohort into what still needs a call and what is already on disk.

    Pure, so the resume rule can be tested without spending anything. The rule
    is deliberately dumb -- a screening file exists, so that candidate is done
    -- because a clever rule that re-runs work after a crash is worse than no
    resume at all.
    """
    if again:
        return list(cohort), []
    todo, done = [], []
    for facts in cohort:
        (done if _screening_path(facts.candidate_id, posting).exists() else todo).append(facts)
    return todo, done


def _score(posting: str, facts_dir: str, only: list[str], workers: int = 4,
           again: bool = False) -> int:
    agenda_file = _agenda_path(posting)
    if not agenda_file.exists():
        print(f"no agenda for {posting!r}", file=sys.stderr)
        return 2
    #: The posting says what it asks for; a person says what it is worth.
    #: Two records, applied here, kept apart on disk.
    chosen = settings_mod.load(posting)
    agenda = chosen.apply(load_agenda(agenda_file))
    if not chosen.answered:
        print("no weights or scale have been set for this posting -- scoring with the")
        print("extractor's defaults. `python configure.py --posting "
              f"{posting} --by <name>` sets them.")

    files = sorted((ROOT / facts_dir).glob("facts_*.json"))
    cohort = [load_facts(f) for f in files]
    if only:
        cohort = [c for c in cohort if c.candidate_id in only]
    if not cohort:
        print(f"no extracted facts in {facts_dir}", file=sys.stderr)
        return 2

    from nbh.llm import AGENT_MODEL, LLMError, default_client
    try:
        default_client()
    except LLMError as e:
        print(f"\n{e}\n", file=sys.stderr)
        return 2

    pipe = _open_pipeline(posting)
    spreads = _spreads(posting)
    known = set(pipe.candidates)

    todo, done = to_do(cohort, posting, again)
    if done:
        print(f"{len(done)} already scored against {posting}, skipping "
              f"(use --again to redo)")
    if not todo:
        print("nothing left to do")
        print()
        print(pipe.render())
        return 0

    print(f"{posting}: {len(todo)} candidate(s), one call each, {workers} at a time")

    def work(facts):
        #: A client per task: the CLI backend keeps a usage tape and a
        #: temporary directory, and sharing either across threads would make
        #: the cost figures meaningless.
        s = screen(default_client(), agenda, facts, model=AGENT_MODEL,
                   credit=chosen.credit())
        #: Written here, inside the worker, so a batch that dies keeps every
        #: result that had already arrived.
        save_screening(s, ROOT / "runs" / "screenings", f"{facts.candidate_id}_{posting}")
        return facts, s

    screenings, failures = [], []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(work, f): f for f in todo}
        for fut in as_completed(futures):
            facts = futures[fut]
            try:
                facts, s = fut.result()
            except Exception as e:  # noqa: BLE001
                failures.append((facts.candidate_id, f"{type(e).__name__}: {e}"))
                print(f"  {facts.candidate_id:<20}  FAILED -- {type(e).__name__}")
                continue
            screenings.append(s)
            if facts.candidate_id not in known:
                #: A candidate this posting has not seen before is new to it
                #: even if they have applied to the company three times.
                pipe.record(facts.candidate_id, "received", at=facts.extracted_at)
            detail = {"score": s.score, "coverage": s.coverage}
            if facts.candidate_id in spreads:
                detail["spread"] = spreads[facts.candidate_id]
            m = motivation(facts)
            if m.specific or m.generic:
                detail["motivation"] = {"specific": len(m.specific),
                                        "generic": len(m.generic)}
            pipe.record(facts.candidate_id, "screened", detail=detail)
            print(f"  {facts.candidate_id:<20} {s.score:>6.1%}  "
                  f"coverage {s.coverage:>4.0%}")

    save_pipeline(pipe, _pipeline_path(posting))
    if failures:
        print()
        print(f"  {len(failures)} did not complete -- rerun the same command to "
              f"pick them up:")
        for cid, why in failures:
            print(f"    {cid}: {why[:100]}")
    r = Ranking(posting_id=posting, screenings=screenings) if screenings else None
    if r is not None:
        save_screening(r, ROOT / "runs" / "screenings", f"ranking_{posting}")
    print()
    print(pipe.render())
    return 0


def _board(posting: str) -> int:
    p = _pipeline_path(posting)
    if not p.exists():
        print(f"nothing tracked for {posting!r} yet -- run `score` first", file=sys.stderr)
        return 2
    print(load_pipeline(p).render())
    return 0


def _explain(candidate: str, posting: str, facts_dir: str) -> int:
    f = ROOT / "runs" / "screenings" / f"{candidate}_{posting}.json"
    if not f.exists():
        print(f"no screening for {candidate} against {posting}", file=sys.stderr)
        return 2
    s = load_screening(f)
    chosen = settings_mod.load(posting)
    print(explain(s, spread=_spreads(posting).get(candidate), credit=chosen.credit()))
    if not chosen.answered:
        print()
        print("  The weights above are the extractor's defaults: nobody has said what")
        print("  this company values more than what. That is the largest single")
        print("  assumption in this number.")
    agenda_file = _agenda_path(posting)
    print()
    print(next_questions(s, load_agenda(agenda_file) if agenda_file.exists() else None))

    facts_file = ROOT / facts_dir / f"facts_{candidate}.json"
    if facts_file.exists():
        m = motivation(load_facts(facts_file))
        if m.specific or m.generic:
            print()
            print("  " + m.render().replace("\n", "\n  "))
    return 0


def _trace(candidate: str, posting: str, facts_dir: str, only: str) -> int:
    """Where each judgement came from, down to both documents."""
    from screen import trace
    f = _screening_path(candidate, posting)
    if not f.exists():
        print(f"no screening for {candidate} against {posting}", file=sys.stderr)
        return 2
    facts_file = ROOT / facts_dir / f"facts_{candidate}.json"
    agenda_file = _agenda_path(posting)
    print(trace(load_screening(f),
                load_facts(facts_file) if facts_file.exists() else None,
                load_agenda(agenda_file) if agenda_file.exists() else None,
                only=only))
    return 0


def _reply(candidate: str, posting: str, facts_dir: str, company: str) -> int:
    """The note that goes back to the applicant. No model, no cost."""
    from reply import note
    f = _screening_path(candidate, posting)
    if not f.exists():
        print(f"no screening for {candidate} against {posting}", file=sys.stderr)
        return 2
    facts_file = ROOT / facts_dir / f"facts_{candidate}.json"
    agenda_file = _agenda_path(posting)
    pipe = _open_pipeline(posting)
    print(note(load_screening(f),
               load_facts(facts_file) if facts_file.exists() else None,
               load_agenda(agenda_file) if agenda_file.exists() else None,
               company=company, spread=_spreads(posting).get(candidate),
               reviewer_pending=not pipe.standing(candidate).seen))
    return 0


def _changes(before: str, after: str) -> int:
    old, new = load_facts(before), load_facts(after)
    if old.candidate_id != new.candidate_id:
        print(f"two different people: {old.candidate_id} and {new.candidate_id}",
              file=sys.stderr)
        return 2
    print(f"{new.candidate_id}: what changed since the last application")
    print()
    print(changed(old, new).render())
    return 0


def _html(posting: str, out: str | None) -> int:
    """One file that opens by double-click. Free: it reads what is on disk."""
    from console.render import render
    from datetime import datetime, timezone

    p = _pipeline_path(posting)
    if not p.exists():
        print(f"nothing tracked for {posting!r} yet -- run `score` first", file=sys.stderr)
        return 2
    pipe = load_pipeline(p)
    now = datetime.now(timezone.utc)

    spreads = _spreads(posting)
    #: The resolution of this cohort: below it, the page draws a band instead
    #: of an order -- the desk's any-order rule, so the board and the desk
    #: agree. Falls back to zero, which draws everyone separately and is the
    #: honest default when nothing has been measured.
    resolution = max((x for x in (any_order_noise(posting, c) for c in pipe.candidates)
                      if x is not None), default=0.0)

    detail = {}
    for cid in pipe.candidates:
        f = _screening_path(cid, posting)
        if not f.exists():
            continue
        s = load_screening(f)
        agenda_file = _agenda_path(posting)
        agenda = load_agenda(agenda_file) if agenda_file.exists() else None
        from screen import trace
        text = explain(s, spread=spreads.get(cid)) + "\n\n" + next_questions(s, agenda)
        facts_file = ROOT / "runs" / "facts" / f"facts_{cid}.json"
        facts = load_facts(facts_file) if facts_file.exists() else None
        if facts is not None:
            m = motivation(facts)
            if m.specific or m.generic:
                text += "\n\n  " + m.render().replace("\n", "\n  ")
        #: The whole chain, in the same panel: a number nobody can walk back
        #: is a number nobody should act on.
        text += "\n\n" + "-" * 66 + "\n\n" + trace(s, facts, agenda)
        #: And what the day should look at, once a day has been designed.
        #: Without a design the brief would only list questions, which the
        #: panel above already does.
        from trial import _design_path, _load_brief
        if _design_path(posting).exists():
            b = _load_brief(cid, posting, "runs/facts")
            if b is not None:
                text += "\n\n" + "-" * 66 + "\n\n" + b.render()
        detail[cid] = {"explain": text}

    #: The grid, with the one thing a scorecard never carries: which cells
    #: did not hold when the same facts were screened again.
    moved: dict[str, set[str]] = {}
    drift = _drift_record(posting)
    if drift:
        for u in json.loads(drift.read_text(encoding="utf-8")).get("unstable_criteria", []):
            moved.setdefault(u["candidate_id"], set()).add(u["criterion_id"])

    criteria: list[str] = []
    grid_rows = []
    for st in pipe.to_dict()["standings"]:
        cid = st["candidate_id"]
        f = _screening_path(cid, posting)
        if not f.exists():
            continue
        sc = load_screening(f)
        if not criteria:
            criteria = [a.criterion_id for a in sc.assessments]
        grid_rows.append({"candidate_id": cid, "cells": {
            a.criterion_id: {"strength": a.strength, "capped": a.capped_from,
                             "moved": a.criterion_id in moved.get(cid, set())}
            for a in sc.assessments}})

    page = render(pipe.to_dict(), detail, resolution,
                  grid={"criteria": criteria, "rows": grid_rows},
                  stale=[s.candidate_id for s in pipe.stale(now)],
                  due=[s.candidate_id for s in pipe.due(now)])
    target = Path(out) if out else ROOT / "runs" / "board" / f"{posting}.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(page, encoding="utf-8")
    print(f"written: {target}")
    print("open it by double-clicking; it needs no server and makes no network calls")
    return 0


def _park(candidate: str, posting: str, until: str, by: str, reason: str) -> int:
    pipe = _open_pipeline(posting)
    try:
        pipe.add(Event(candidate_id=candidate, kind="revisit", by=by, reason=reason,
                       detail={"on": until}))
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    save_pipeline(pipe, _pipeline_path(posting))
    print(f"{candidate} parked until {until}; it will surface on the board that day")
    return 0


# --------------------------------------------------------------------------
# The line under a name
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Brief:
    """What a partner reads before deciding whether to open the file.

    Everything in it is already in the screening -- no model is called -- and
    each part is something a person can check against the CV in one glance:
    what is strong and the sentence it rests on, the condition nobody has
    answered, the question to ask first, and how much of the posting the
    paper simply does not speak to.
    """

    strong: tuple[tuple[str, str], ...]  # (criterion label, the CV's own claim)
    #: Strong on languages and nothing else. Said, because a row that listed
    #: "Fluent english" as a strength would read as praise.
    only_languages: bool
    open_gates: tuple[str, ...]
    ask_first: str
    silent: float  # share of the weight the documents do not speak to
    spread: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def lines(self) -> list[str]:
        """The same brief as plain text, for the card in a terminal."""
        out = [f'strong: {lab} -- "{claim}"' if claim else f"strong: {lab}"
               for lab, claim in self.strong]
        if self.only_languages:
            out.append("strong on languages only")
        elif not self.strong:
            out.append("nothing strong on paper")
        out += [f"{g}: unanswered, and it is a condition" for g in self.open_gates]
        if self.silent >= 0.25:
            out.append(f"the paper is silent on {self.silent:.0%} of the posting")
        if self.ask_first:
            out.append(f"ask first: {self.ask_first}")
        return out


def label(criterion_id: str) -> str:
    """`nice_fintech_saas_cfo` -> `Fintech saas cfo`, for a line a person reads."""
    words = criterion_id.removeprefix("nice_").split("_")
    words = [{"ai": "AI", "cfo": "CFO", "saas": "SaaS"}.get(w, w) for w in words if w]
    out = " ".join(words)
    return out[:1].upper() + out[1:]


def brief(candidate: str, posting: str, facts_dir: str = "runs/facts",
          limit: int = 2) -> Brief | None:
    """The line under a name on the desk, or None when nothing was screened."""
    f = _screening_path(candidate, posting)
    if not f.exists():
        return None
    s = load_screening(f)
    ff = ROOT / facts_dir / f"facts_{candidate}.json"
    facts = {x.id: x for x in load_facts(ff).facts} if ff.exists() else {}

    def rests_on(a: Any) -> Any:
        return next((facts[i] for i in a.fact_ids if i in facts), None)

    def plain(a: Any) -> tuple:
        # A language is true and tells a partner nothing; a thing done tells
        # them most. Order what is strong by how much it says, then weight.
        f = rests_on(a)
        return (f is not None and f.kind == "language",
                f is None or f.evidence != "instance", -a.weight, a.criterion_id)

    ranked = sorted((a for a in s.assessments if a.strength == "strong"), key=plain)
    strong = [a for a in ranked if not plain(a)[0]]
    agenda_file = _agenda_path(posting)
    levers = leverage(s, load_agenda(agenda_file) if agenda_file.exists() else None)
    return Brief(
        strong=tuple((label(a.criterion_id), f.claim if (f := rests_on(a)) else "")
                     for a in strong[:limit]),
        only_languages=bool(ranked) and not strong,
        open_gates=tuple(label(g) for g in s.open_gates),
        ask_first=next((l.asks for l in levers if l.kind == "unanswered" and l.asks), ""),
        silent=1.0 - s.coverage,
        spread=_spreads(posting).get(candidate),
    )


def bands(scored: list[tuple[str, float, float | None]]) -> list[list[str]]:
    """Group a ranking into the places it can actually tell apart.

    `scored` is (candidate, score, spread). Sorted by score, a candidate joins
    the band above when the gap to the one above is no larger than the drift
    either of them shows on identical input -- the same rule the stability
    report prints as NOT SEP. With no drift measured, nothing is merged: the
    desk does not invent a noise figure it was never given.
    """
    ordered = sorted(scored, key=lambda t: (-t[1], t[0]))
    out: list[list[str]] = []
    prev: tuple[str, float, float | None] | None = None
    for c in ordered:
        noise = max((x for x in (c[2], prev[2] if prev else None) if x is not None), default=None)
        if prev is not None and noise is not None and prev[1] - c[1] <= noise:
            out[-1].append(c[0])
        else:
            out.append([c[0]])
        prev = c
    return out


def main() -> int:
    ap = argparse.ArgumentParser(prog="triage", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sc = sub.add_parser("score", help="screen a cohort against one posting (costs calls)")
    sc.add_argument("--posting", required=True)
    sc.add_argument("--facts-dir", default="runs/facts")
    sc.add_argument("--only", nargs="*", default=[])
    sc.add_argument("--workers", type=int, default=4)
    sc.add_argument("--again", action="store_true",
                    help="rescore candidates already done (default: skip them)")

    bo = sub.add_parser("board", help="everyone, and what is going quiet (free)")
    bo.add_argument("--posting", required=True)

    ex = sub.add_parser("explain", help="where one number came from (free)")
    ex.add_argument("--candidate", required=True)
    ex.add_argument("--posting", required=True)
    ex.add_argument("--facts-dir", default="runs/facts")

    tr = sub.add_parser("trace", help="where each judgement came from (free)")
    tr.add_argument("--candidate", required=True)
    tr.add_argument("--posting", required=True)
    tr.add_argument("--facts-dir", default="runs/facts")
    tr.add_argument("--only", default="", help="one criterion id")

    rp = sub.add_parser("reply", help="the note back to the applicant (free)")
    rp.add_argument("--candidate", required=True)
    rp.add_argument("--posting", required=True)
    rp.add_argument("--facts-dir", default="runs/facts")
    rp.add_argument("--company", default="")

    ch = sub.add_parser("changes", help="what is different since last time (free)")
    ch.add_argument("--before", required=True)
    ch.add_argument("--after", required=True)

    ht = sub.add_parser("html", help="one self-contained page for the board (free)")
    ht.add_argument("--posting", required=True)
    ht.add_argument("--out", default=None)

    pk = sub.add_parser("park", help="revisit this one on a date, with a reason")
    pk.add_argument("--candidate", required=True)
    pk.add_argument("--posting", required=True)
    pk.add_argument("--until", required=True, help="ISO date, e.g. 2027-03-01")
    pk.add_argument("--by", required=True, help="who decided")
    pk.add_argument("--reason", required=True)

    a = ap.parse_args()
    if a.cmd == "score":
        return _score(a.posting, a.facts_dir, a.only, a.workers, a.again)
    if a.cmd == "board":
        return _board(a.posting)
    if a.cmd == "explain":
        return _explain(a.candidate, a.posting, a.facts_dir)
    if a.cmd == "html":
        return _html(a.posting, a.out)
    if a.cmd == "trace":
        return _trace(a.candidate, a.posting, a.facts_dir, a.only)
    if a.cmd == "reply":
        return _reply(a.candidate, a.posting, a.facts_dir, a.company)
    if a.cmd == "changes":
        return _changes(a.before, a.after)
    return _park(a.candidate, a.posting, a.until, a.by, a.reason)


if __name__ == "__main__":
    raise SystemExit(main())
