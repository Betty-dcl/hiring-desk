"""Run the harness.

    # check contracts against traces already on disk -- no model, no cost
    ./.venv/bin/python -m harness check runs/*.json

    # run the same scenario five times and report what did not hold
    ./.venv/bin/python -m harness batch --template ines_open_door -n 5

    # screen the same cohort three times: is the ranking a measurement?
    ./.venv/bin/python -m harness stability --posting founders_associate -n 3

    # re-read a measurement already on disk -- free, no model
    ./.venv/bin/python -m harness stability --report runs/stability/founders_associate_n3_personas.json

    # did a change to the screener make it steadier? -- free, no model
    ./.venv/bin/python -m harness stability --compare before.json after.json

    # same facts under four names, plus the anonymised baseline
    ./.venv/bin/python -m harness bias --candidate ines_abadi -n 3

    # how much of the ranking is the credit scale rather than the evidence? -- free
    ./.venv/bin/python -m harness sensitivity --posting founders_associate

    # which credit scale the pre-registered expectations actually allow -- free
    ./.venv/bin/python -m harness formula --posting founders_associate

    # every failure ever found, replayed against today's code -- free
    ./.venv/bin/python -m harness regress

    # every number in the README, recomputed from runs/ with its interval -- free
    ./.venv/bin/python -m harness report

The `check` path is free and deterministic, and it is the one to reach for
first: most of what this harness knows how to catch, it catches by reading a
trace that already exists.
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

from harness import contracts
from harness.runner import ROOT, Template, batch

#: The scenarios the harness knows how to run. A template is a frozen
#: scenario, so adding one here is the only way to add a run -- there is no
#: path that quietly runs something undeclared.
TEMPLATES = {
    "ines_open_door": Template(
        id="ines_open_door",
        role_path="mandates/role_causa_prima_open_door.toml",
        candidate_path="mandates/candidate_ines.toml",
    ),
}


def _check(paths: list[str]) -> int:
    files: list[Path] = []
    for p in paths:
        files.extend(Path(f) for f in sorted(glob.glob(p)) if f.endswith(".json"))
    if not files:
        print("no traces matched", file=sys.stderr)
        return 2

    worst = 0
    for f in files:
        payload = json.loads(f.read_text(encoding="utf-8"))
        report = contracts.check(payload)
        mark = "ok  " if report.passed else "FAIL"
        print(f"{mark} {f}")
        for r in report.failures:
            print(f"       {r.contract}: {r.detail}")
            worst = 1
    print(f"\n{len(files)} traces, {sum(1 for f in files if contracts.check(json.loads(f.read_text(encoding='utf-8'))).passed)} clean")
    return worst


def _batch(name: str, n: int, backend: str, max_turns: int | None) -> int:
    if name not in TEMPLATES:
        print(f"unknown template {name!r}. Known: {', '.join(TEMPLATES)}", file=sys.stderr)
        return 2
    template = TEMPLATES[name]
    if max_turns:
        template = Template(**{**template.__dict__, "max_turns": max_turns})

    from nbh.llm import ClaudeCodeClient, Client, LLMError

    def make_client():
        return ClaudeCodeClient() if backend == "claude-code" else Client()

    try:
        make_client()
    except LLMError as e:
        print(f"\n{e}\n", file=sys.stderr)
        return 2

    def progress(i: int, total: int, report) -> None:
        if report is None:
            print(f"  run {i}/{total}: did not complete")
            return
        bad = report.failures
        print(f"  run {i}/{total}: {'clean' if not bad else f'{len(bad)} contract(s) broke'}")

    print(f"{template.id}: {n} runs, {template.max_turns} turns each, backend {backend}")
    b = batch(make_client, template, n)
    print()
    print(b.render())

    summary = ROOT / "runs" / template.id / "_summary.json"
    summary.write_text(json.dumps(b.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nsummary: {summary.relative_to(ROOT)}")
    return 0 if b.n and not b.errors else 1


def _stability_report(path: str) -> int:
    """Re-read a stability record and say where its ranking stops holding.

    The record keeps every individual score, so this needs no model and no
    re-run: an old measurement can answer a question asked after it was
    taken.
    """
    from harness.drift import DEFAULT_SWAP, pool
    from harness.screener import places_worth_reading, resolution, separation

    f = Path(path)
    if not f.exists():
        print(f"no such record: {path}", file=sys.stderr)
        return 2
    d = json.loads(f.read_text(encoding="utf-8"))
    rows = [(c["candidate_id"], c["mean"], c["spread"])
            for c in d.get("candidates", []) if c.get("runs")]
    if not rows:
        print("that record has no completed runs", file=sys.stderr)
        return 2

    pairs = separation(rows)
    print(f"{d.get('posting_id')} -- {d.get('n')} runs, taken {d.get('at', '?')}")
    print(f"resolution: {resolution(rows):.1%}  "
          f"(the widest range one candidate moved over identical input)")
    #: The range above is this record's own rule, and it is what the table
    #: below is graded by. The desk groups people by a different number -- the
    #: drift pooled over every repeat on disk, read with Student's t -- and the
    #: README quotes that one, so it is printed here too: two figures for "the
    #: order stops meaning much" should never meet a reader unexplained.
    posting = d.get("posting_id")
    gap = pool(posting, ROOT).gap(DEFAULT_SWAP) if posting else None
    if gap is not None:
        print(f"any order below: {gap:.1%}  (single screenings swap more than "
              f"1 time in {round(1 / DEFAULT_SWAP)}: the desk's band)")
    print()
    for pr in pairs:
        mark = "ok      " if pr.separated else "NOT SEP."
        print(f"  {mark} {pr.above:<18} > {pr.below:<18} "
              f"gap {pr.gap:>6.1%}   noise {pr.noise:>6.1%}")
    worth = places_worth_reading(pairs)
    print()
    print(f"  settled places: {worth} of {len(rows)}")
    return 0


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


def _stability_derive(path: str, without: list[str], out: str | None) -> int:
    """Write a new record with some candidates left out, and say so in it.

    Not an edit. The raw per-run scores of the candidates that remain are
    copied untouched; only the derived figures -- resolution, separation,
    settled places -- are recomputed, because they were computed over a
    cohort that is no longer the cohort. The new file records where it came
    from and who was dropped, so that a reader can tell a derived record from
    a measured one without being told.
    """
    from harness.screener import places_worth_reading, resolution, separation

    f = Path(path)
    if not f.exists():
        print(f"no such record: {path}", file=sys.stderr)
        return 2
    d = json.loads(f.read_text(encoding="utf-8"))
    drop = set(without)
    kept = [c for c in d.get("candidates", []) if c["candidate_id"] not in drop]
    if not kept:
        print("that would leave nobody", file=sys.stderr)
        return 2

    rows = [(c["candidate_id"], c["mean"], c["spread"]) for c in kept if c.get("runs")]
    pairs = separation(rows)
    out_d = {
        **d,
        "candidates": kept,
        "unstable_criteria": [u for u in d.get("unstable_criteria", [])
                              if u["candidate_id"] not in drop],
        "resolution": resolution(rows),
        "places_worth_reading": places_worth_reading(pairs),
        "separation": [pr.to_dict() for pr in pairs],
        "orders": [[c for c in o if c not in drop] for o in d.get("orders", [])],
        "rank_movement": {k: v for k, v in d.get("rank_movement", {}).items()
                          if k not in drop},
        "derived_from": f.name,
        #: A count and a reason, not a name: who was left out of a cohort is
        #: rarely anyone else's business, and the fact of derivation is what
        #: a reader needs.
        "dropped": len([c for c in d.get("candidates", []) if c["candidate_id"] in drop]),
        "derivation_note": ("per-run scores copied unchanged; cohort-level figures "
                            "recomputed for the remaining candidates"),
    }
    target = Path(out) if out else f.with_name(f.stem + "_derived.json")
    target.write_text(json.dumps(out_d, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{len(kept)} of {len(d.get('candidates', []))} candidates kept -> {target}")
    print(f"resolution {out_d['resolution']:.1%}, "
          f"settled places {out_d['places_worth_reading']}/{len(rows)}")
    return 0


def _stability_compare(before_path: str, after_path: str) -> int:
    """Two measurements of the same cohort, and what moved between them.

    The point of a harness is to tell you whether a change helped. Both
    records are already on disk, so this answers that for nothing -- and it
    answers it in the terms that matter: did the instability shrink, and did
    the ranking gain places it can actually defend?
    """
    from harness.screener import places_worth_reading, resolution, separation

    records = []
    for path in (before_path, after_path):
        f = Path(path)
        if not f.exists():
            print(f"no such record: {path}", file=sys.stderr)
            return 2
        records.append(json.loads(f.read_text(encoding="utf-8")))
    before, after = records

    def rows(d):
        return [(c["candidate_id"], c["mean"], c["spread"])
                for c in d.get("candidates", []) if c.get("runs")]

    def unstable_by_criterion(d):
        out = {}
        for u in d.get("unstable_criteria", []):
            out.setdefault(u["criterion_id"], []).append(u["candidate_id"])
        return out

    def calls(d):
        return sum(len(c.get("per_criterion", {})) for c in d.get("candidates", []))

    b_rows, a_rows = rows(before), rows(after)
    print(f"{before.get('posting_id')}: {before.get('at', '?')[:16]}  ->  "
          f"{after.get('at', '?')[:16]}")
    print()
    print(f"  {'candidate':<20} {'mean':>14} {'spread':>16}")
    a_by_id = {r[0]: r for r in a_rows}
    for cid, mean, spread in sorted(b_rows, key=lambda r: -r[1]):
        if cid not in a_by_id:
            print(f"  {cid:<20} {mean:>6.0%} ->      -")
            continue
        _, a_mean, a_spread = a_by_id[cid]
        arrow = "steadier" if a_spread < spread else "noisier" if a_spread > spread else "same"
        print(f"  {cid:<20} {mean:>6.0%} -> {a_mean:>5.0%} "
              f"{spread:>8.1%} -> {a_spread:>5.1%}  {arrow}")

    b_unstable = sum(len(v) for v in unstable_by_criterion(before).values())
    a_unstable = sum(len(v) for v in unstable_by_criterion(after).values())
    print()
    print(f"  criterion calls that moved: {b_unstable}/{calls(before)} -> "
          f"{a_unstable}/{calls(after)}")
    print(f"  resolution:                 {resolution(b_rows):.1%} -> {resolution(a_rows):.1%}")
    print(f"  settled places:             {places_worth_reading(separation(b_rows))}/{len(b_rows)}"
          f" -> {places_worth_reading(separation(a_rows))}/{len(a_rows)}")

    b_by_crit, a_by_crit = unstable_by_criterion(before), unstable_by_criterion(after)
    #: A criterion present in both records looks unchanged to a set
    #: difference, while having gone from wobbling on one candidate to
    #: wobbling on four. That is the loudest thing a comparison can be asked,
    #: so it is asked by candidate count, not by membership.
    spread_wider = sorted(
        (k, len(b_by_crit[k]), len(a_by_crit[k]))
        for k in set(b_by_crit) & set(a_by_crit)
        if len(a_by_crit[k]) != len(b_by_crit[k])
    )
    if spread_wider:
        print("  same criterion, more or fewer people:")
        for k, nb, na in spread_wider:
            print(f"    {k:<26} {nb} -> {na} candidates")

    fixed = set(b_by_crit) - set(a_by_crit)
    broke = set(a_by_crit) - set(b_by_crit)
    if fixed:
        print(f"  steady now:                 {', '.join(sorted(fixed))}")
    if broke:
        #: Named separately and never netted off against the fixed ones: a
        #: change that steadies two criteria and unsettles two others has
        #: moved the problem, not solved it.
        print(f"  newly unsteady:             {', '.join(sorted(broke))}")
    return 0


def _stability(posting: str, n: int, backend: str, label: str | None = None,
               facts_dir: str = "runs/facts") -> int:
    """Screen the cohort n times against one agenda, and report the drift.

    The agenda and the facts are read from disk -- both were extracted once,
    and re-extracting them here would measure the intake instead of the
    screener.
    """
    agenda_path = ROOT / "mandates" / "generated" / f"role_{posting}.provenance.json"
    if not agenda_path.exists():
        known = sorted(
            p.name[len("role_"):-len(".provenance.json")]
            for p in (ROOT / "mandates" / "generated").glob("role_*.provenance.json")
        )
        print(f"no agenda for {posting!r}. Known: {', '.join(known)}", file=sys.stderr)
        return 2

    from intake.cv import load as load_facts
    from intake.posting import load as load_agenda
    from harness.screener import save, stability
    from nbh.llm import AGENT_MODEL, ClaudeCodeClient, Client, LLMError

    agenda = load_agenda(agenda_path)
    cohort = [load_facts(f) for f in sorted((ROOT / facts_dir).glob("facts_*.json"))]
    if not cohort:
        print("no extracted facts in runs/facts", file=sys.stderr)
        return 2

    make_client = ClaudeCodeClient if backend == "claude-code" else Client
    try:
        make_client()
    except LLMError as e:
        print(f"\n{e}\n", file=sys.stderr)
        return 2

    print(f"{posting}: {len(cohort)} candidates x {n} runs, backend {backend}, "
          f"facts from {facts_dir}")
    s = stability(
        make_client, agenda, cohort, n=n, model=AGENT_MODEL,
        on_run=lambda i, total, cid: print(f"  {cid} {i}/{total}", flush=True),
    )
    name = f"{posting}_n{n}" + (f"_{label}" if label else "")
    out = save(s, ROOT / "runs" / "stability", name)
    print()
    print(s.render())
    print(f"\nwritten: {out.relative_to(ROOT)}")
    #: A run that never completed is not a stable result, it is a missing
    #: one -- so a cohort with any error exits non-zero.
    return 0 if not any(r.errors for r in s.repeats) else 1


def _bias(candidate: str, posting: str, n: int, backend: str) -> int:
    """What the name is worth, measured against the screener's own noise."""
    from harness.counterfactual import BM2004, save, under_names
    from intake.cv import load as load_facts
    from intake.posting import load as load_agenda
    from nbh.llm import AGENT_MODEL, ClaudeCodeClient, Client, LLMError

    agenda_path = ROOT / "mandates" / "generated" / f"role_{posting}.provenance.json"
    facts_path = ROOT / "runs" / "facts" / f"facts_{candidate}.json"
    for path in (agenda_path, facts_path):
        if not path.exists():
            print(f"missing: {path.relative_to(ROOT)}", file=sys.stderr)
            return 2

    make_client = ClaudeCodeClient if backend == "claude-code" else Client
    try:
        make_client()
    except LLMError as e:
        print(f"\n{e}\n", file=sys.stderr)
        return 2

    calls = (len(BM2004) + 1) * n
    print(f"{candidate} on {posting}: {len(BM2004)} names + anonymised, "
          f"{n} runs each = {calls} calls")
    c = under_names(
        make_client, load_agenda(agenda_path), load_facts(facts_path), BM2004, n=n,
        model=AGENT_MODEL,
        on_run=lambda who, i, total: print(f"  {who} {i}/{total}", flush=True),
    )
    out = save(c, ROOT / "runs" / "bias", f"{candidate}_{posting}_n{n}")
    print()
    print(c.render())
    print(f"\nwritten: {out.relative_to(ROOT)}")
    failed = [u for u in [*c.under, c.blind] if u and u.errors]
    return 0 if not failed else 1


def _sensitivity(posting: str) -> int:
    """Re-score the cohort on several credit scales. No model, no cost."""
    import glob

    from harness.sensitivity import save, sensitivity
    from screen import load as load_screening

    files = [f for f in sorted(glob.glob(str(ROOT / "runs" / "screenings" / f"*_{posting}.json")))
             if not Path(f).name.startswith(("ranking_", "reviewed_"))]
    if not files:
        print(f"no screenings for {posting!r} in runs/screenings", file=sys.stderr)
        return 2
    screenings = [load_screening(f) for f in files]

    #: The measured drift, if a stability record is there. Without it the
    #: swing is a number with nothing to be compared against.
    noise: dict[str, float] = {}
    stability = _drift_record(posting)
    if stability is not None:
        d = json.loads(stability.read_text(encoding="utf-8"))
        noise = {c["candidate_id"]: c["spread"] for c in d.get("candidates", []) if c.get("runs")}

    s = sensitivity(posting, screenings, noise=noise)
    print(s.render())
    out = save(s, ROOT / "runs" / "sensitivity", posting)
    print(f"\nwritten: {out.relative_to(ROOT)}")
    #: A ranking that depends on a constant nobody chose deliberately is a
    #: finding, so it exits non-zero.
    return 0 if s.order_is_stable else 1


def _formula(posting: str, exclude: list[str]) -> int:
    """Search the credit scale against the expectations written before the runs."""
    import glob

    from harness.formula import (Result, bands, load_expectations, must_outrank,
                                 save, search)
    from screen import load as load_screening

    files = [f for f in sorted(glob.glob(str(ROOT / "runs" / "screenings" / f"*_{posting}.json")))
             if not Path(f).name.startswith(("ranking_", "reviewed_"))
             and not Path(f).name.endswith(f"_{posting}_with_letter.json")]
    screenings = [load_screening(f) for f in files]
    screenings = [s for s in screenings if s.candidate_id not in exclude]
    if len(screenings) < 2:
        print("need at least two screenings", file=sys.stderr)
        return 2

    spec = load_expectations()
    expected = {k: v for k, v in bands(spec).items()
                if k in {s.candidate_id for s in screenings}}
    if not expected:
        print("no pre-registered bands cover this cohort", file=sys.stderr)
        return 2

    noise: dict[str, float] = {}
    drift = _drift_record(posting)
    if drift is not None:
        d = json.loads(drift.read_text(encoding="utf-8"))
        noise = {c["candidate_id"]: c["spread"] for c in d.get("candidates", [])
                 if c.get("runs") and c["candidate_id"] not in exclude}

    print(f"{posting}: {len(screenings)} candidates, "
          f"{len(expected)} pre-registered bands")
    if exclude:
        print(f"excluded: {', '.join(exclude)}")
    print()
    r = Result(trials=search(screenings, expected, must_outrank(spec)), noise=noise)
    print(r.render())
    out = save(r, ROOT / "runs" / "formula", posting)
    print(f"\nwritten: {out.relative_to(ROOT)}")
    return 0 if r.passing else 1


def _extraction(candidates: list[str], n: int, workers: int = 4) -> int:
    """Is the upstream of everything else steady? Costs one call per run."""
    from harness.extraction import save, stability
    from intake.cv import Profile
    from nbh.llm import AGENT_MODEL, ClaudeCodeClient, LLMError

    profiles = []
    for cid in candidates:
        f = ROOT / "personas" / f"{cid}.md"
        if not f.exists():
            print(f"no document for {cid!r}: {f.relative_to(ROOT)}", file=sys.stderr)
            return 2
        profiles.append(Profile.from_file(f, id=cid))

    try:
        ClaudeCodeClient()
    except LLMError as e:
        print(f"\n{e}\n", file=sys.stderr)
        return 2

    print(f"{len(profiles)} document(s) x {n} extractions = {len(profiles) * n} calls, "
          f"{workers} at a time")
    s = stability(ClaudeCodeClient, profiles, n=n, model=AGENT_MODEL, workers=workers,
                  on_run=lambda cid, i, total: print(f"  {cid} {i}/{total}", flush=True))
    print()
    print(s.render())
    out = save(s, ROOT / "runs" / "extraction", f"n{n}")
    print(f"\nwritten: {out.relative_to(ROOT)}")
    return 0


def _regress(only_list: bool) -> int:
    from harness import regress

    if only_list:
        print(regress.listing(regress.cases()))
        return 0
    results, shelf = regress.run()
    print(regress.render(results, shelf))
    return 0 if all(r.passed for r in results) and not shelf else 1


def _report(out: str, simulate: bool, stdout: bool) -> int:
    """Every README number against the records, as markdown and JSON. Free."""
    from harness import report

    rep = report.build(ROOT, simulate=simulate)
    if stdout:
        print(report.render(rep))
    else:
        j, m = report.write(rep, ROOT / out if not Path(out).is_absolute() else Path(out))
        bad = [c for c in rep["claims"] if not c["holds"]]
        print(f"{len(rep['claims'])} claims, {len(bad)} do not hold as written:")
        for c in bad:
            print(f"  {c['doc']}: {c['quote']!r}")
            print(f"      measured: {c['measured']}")
        print(f"\nwritten: {m.relative_to(ROOT) if m.is_relative_to(ROOT) else m}, "
              f"{j.relative_to(ROOT) if j.is_relative_to(ROOT) else j}")
    #: A disputed claim is a finding, not a failure: it is listed, with the
    #: text that would hold. Only a claim that stopped matching the data
    #: without being listed is an error.
    unexpected = {c["id"] for c in rep["claims"] if not c["holds"]} ^ set(report.DISPUTED)
    return 1 if unexpected else 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="harness", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="run the contracts against traces on disk (free)")
    c.add_argument("paths", nargs="+")

    b = sub.add_parser("batch", help="run a template n times and measure what held")
    b.add_argument("--template", default="ines_open_door")
    b.add_argument("-n", type=int, default=5)
    b.add_argument("--backend", choices=("claude-code", "api"), default="claude-code")
    b.add_argument("--max-turns", type=int, default=None)

    st = sub.add_parser("stability", help="screen one cohort n times and measure the drift")
    st.add_argument("--posting", default="founders_associate")
    st.add_argument("-n", type=int, default=3)
    st.add_argument("--backend", choices=("claude-code", "api"), default="claude-code")
    st.add_argument("--report", metavar="PATH", default=None,
                    help="re-read a saved record instead of running (free)")
    st.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"), default=None,
                    help="two saved records: what a change to the screener did (free)")
    st.add_argument("--facts-dir", default="runs/facts",
                    help="a different cohort: the same people with a letter, say")
    st.add_argument("--without", nargs="+", default=None,
                    help="with --report: write a record without these candidates")
    st.add_argument("--save", default=None, help="where the derived record goes")
    st.add_argument("--label", default=None,
                    help="suffix for the record, so a re-run does not bury the last one")

    bi = sub.add_parser("bias", help="same facts under several names: what does the name do?")
    bi.add_argument("--candidate", default="ines_abadi")
    bi.add_argument("--posting", default="founders_associate")
    bi.add_argument("-n", type=int, default=3)
    bi.add_argument("--backend", choices=("claude-code", "api"), default="claude-code")
    bi.add_argument("--report", metavar="RECORD",
                    help="re-read a name measurement already on disk -- free, no model")

    se = sub.add_parser("sensitivity", help="how much does the credit scale decide? (free)")
    se.add_argument("--posting", default="founders_associate")

    fo = sub.add_parser("formula", help="which credit scale the expectations allow (free)")
    fo.add_argument("--posting", default="founders_associate")
    fo.add_argument("--exclude", nargs="*", default=[],
                    help="candidates to leave out of the search")

    ex = sub.add_parser("extraction", help="same document N times: are the facts fixed?")
    ex.add_argument("--candidates", nargs="+", default=["ines_abadi", "mara_velichko"])
    ex.add_argument("-n", type=int, default=3)
    ex.add_argument("--workers", type=int, default=4)

    rg = sub.add_parser("regress", help="replay every failure ever found (free)")
    rg.add_argument("--list", action="store_true", help="say what is in the library")

    rp = sub.add_parser("report", help="every README number, recomputed from runs/ (free)")
    rp.add_argument("--out", default="runs/report", help="where claims.md and claims.json go")
    rp.add_argument("--no-simulation", action="store_true",
                    help="skip the permutation-power simulation (a few seconds)")
    rp.add_argument("--stdout", action="store_true", help="print the markdown, write nothing")

    args = ap.parse_args()
    if args.cmd == "report":
        return _report(args.out, not args.no_simulation, args.stdout)
    if args.cmd == "regress":
        return _regress(args.list)
    if args.cmd == "extraction":
        return _extraction(args.candidates, args.n, args.workers)
    if args.cmd == "formula":
        return _formula(args.posting, args.exclude)
    if args.cmd == "sensitivity":
        return _sensitivity(args.posting)
    if args.cmd == "check":
        return _check(args.paths)
    if args.cmd == "bias" and args.report:
        from harness.counterfactual import load as load_bias
        print(load_bias(args.report).render())
        return 0
    if args.cmd == "bias":
        return _bias(args.candidate, args.posting, args.n, args.backend)
    if args.cmd == "stability":
        if args.compare:
            return _stability_compare(*args.compare)
        if args.report and args.without:
            return _stability_derive(args.report, args.without, args.save)
        if args.report:
            return _stability_report(args.report)
        return _stability(args.posting, args.n, args.backend, args.label, args.facts_dir)
    return _batch(args.template, args.n, args.backend, args.max_turns)


if __name__ == "__main__":
    raise SystemExit(main())
