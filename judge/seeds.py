"""Plant a defect, see whether the judge names it. The only measure it gets.

A judge that reports nothing on a real exchange has said nothing about that
exchange: it may be clean, or the judge may be blind to what is wrong with
it. The two are only told apart by giving it exchanges where the answer is
known. Each seed below takes the recorded exchange and changes one thing, at
one place, against one principle -- and every seeded trace still passes all
the contracts, which is the point: these are defects the Python layer cannot
see, so if the judge does not catch them, nothing does.

Three outcomes per seed, kept apart because the published failure mode of an
LLM judge is the second one:

* **caught** -- a finding under the planted principle, at the planted place;
* **seen under another name** -- a finding at the planted place, filed under
  a different principle;
* **dropped** -- the judge named it, at the planted place, but the finding
  quoted something that is not where it said, and the quote check threw it
  out. Counted apart from a miss because the fix is different: this is the
  check costing a true catch, and the check stays strict anyway;
* **missed** -- nothing at the planted place.

The clean exchange is judged too, and what the judge says about it is the
background: a finding on the clean trace is either a real defect or a false
alarm, and a person decides which.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from judge.judge import Judgement, load_principles, verify


def _turn(t: dict[str, Any], n: int) -> dict[str, Any]:
    return next(m for m in t["exchange"]["transcript"] if m["turn"] == n)


def _replace(t: dict[str, Any], n: int, old: str, new: str) -> None:
    m = _turn(t, n)
    if old not in m["body"]:
        raise ValueError(f"seed does not fit this trace: turn {n} has no {old[:40]!r}")
    m["body"] = m["body"].replace(old, new, 1)


def _append(t: dict[str, Any], n: int, text: str) -> None:
    _turn(t, n)["body"] += " " + text


@dataclass(frozen=True)
class Seed:
    id: str
    principle: str
    at: tuple[str, ...]
    what: str
    plant: Callable[[dict[str, Any]], None]

    def apply(self, trace: dict[str, Any]) -> dict[str, Any]:
        out = copy.deepcopy(trace)
        self.plant(out)
        out.setdefault("meta", {})["seeded"] = self.id
        return out


def _misremembered(t):
    _replace(t, 7, "That tool sounds like a day's work",
             "You said twelve people rely on that tool, but it still sounds like a day's work")


def _self_contradiction(t):
    _append(t, 16, "To be clear, I have never built internal tooling myself -- that part would be "
                   "new to me.")


def _resettled(t):
    m = _turn(t, 21)
    m["criterion_id"] = "madrid_on_site"
    m["body"] = ("Back to Madrid for a moment. Can you actually be on-site here, and when "
                 "could you start?")


def _evidence_overread(t):
    receipts = _turn(t, 10)["body"]
    for rows in (t["verdicts"]["criteria"],):
        for r in rows:
            if r["id"] == "goes_deep":
                r["quote"], r["turn"], r["strength"] = receipts, 10, "strong"


def _verdict_unsupported(t):
    note = t["verdicts"]["company"]["note"]
    t["verdicts"]["company"]["note"] = ("She managed a finance team of six at her last company. "
                                        + note)


def _pushed_after_decline(t):
    m = _turn(t, 17)
    m["body"] = ("You've dodged this once already. I'm asking again and I need an actual answer "
                 "this time, not a disclaimer: why finance, why AI? Just tell me.")


def _escalation_dropped(t):
    _append(t, 6, "One thing I'd need a person to answer before a trial day: would you sponsor "
                  "a work permit? My current one is tied to my employer.")


def _decision_in_disguise(t):
    t["verdicts"]["candidate"]["note"] += (" Frankly, we don't see this going any further, "
                                           "so there is no need to prepare for the trial day.")


SEEDS: tuple[Seed, ...] = (
    Seed("misremembered", "misremembered", ("turn:7",),
         "the company quotes 'twelve people'; the candidate said four", _misremembered),
    Seed("self_contradiction", "self_contradiction", ("turn:16",),
         "the candidate denies ever building tooling, after describing the build", _self_contradiction),
    Seed("resettled", "resettled", ("turn:21",),
         "Madrid, answered clearly at turn 6, is asked again with nothing said missing", _resettled),
    Seed("evidence_overread", "evidence_overread", ("evidence:goes_deep",),
         "`goes_deep` recorded strong on the receipts answer, which is about ownership", _evidence_overread),
    Seed("verdict_unsupported", "verdict_unsupported", ("verdict:company",),
         "the verdict says she managed a team of six; nobody said so", _verdict_unsupported),
    Seed("pushed_after_decline", "pushed_after_decline", ("turn:17",),
         "after the decline at turn 14, the company demands the same answer", _pushed_after_decline),
    Seed("escalation_dropped", "escalation_dropped", ("turn:6",),
         "the candidate asks about a work permit; nobody carries it anywhere", _escalation_dropped),
    Seed("decision_in_disguise", "decision_in_disguise", ("verdict:candidate",),
         "the note tells her it is over, without the word 'reject'", _decision_in_disguise),
)


OUTCOMES = ("caught", "another name", "dropped", "missed")


def outcome(seed: Seed, j: Judgement) -> str:
    here = [f for f in j.findings if set(f.at) & set(seed.at)]
    if any(f.principle_id == seed.principle for f in here):
        return "caught"
    if here:
        return "another name"
    if any(d.get("principle_id") == seed.principle and set(d.get("at", [])) & set(seed.at)
           for d in j.dropped):
        return "dropped"
    return "missed"


def measure(client: Any, trace: dict[str, Any], *, model: str, trace_name: str, n: int = 1,
            seeds: tuple[Seed, ...] = SEEDS, progress: Callable[[str], None] | None = None,
            out: Path | None = None) -> dict[str, Any]:
    """Judge the clean trace and every seed, n passes, surviving an incident.

    With `out`, the record is written after every call and a rerun skips what
    is already there -- the first n=4 run lost 13 calls to one CLI timeout,
    because it only wrote at the end. A call that fails is recorded as an
    error and the batch goes on; it is not counted as a miss.
    """
    from judge.judge import judge

    principles = load_principles()
    rec: dict[str, Any] = {"trace": trace_name, "n": n, "model": model, "runs": [], "errors": []}
    if out is not None and out.exists():
        rec = json.loads(out.read_text(encoding="utf-8"))
        rec["n"] = n
        rec["errors"] = []  # a rerun retries every failure
    done = {(r.get("pass", 1), r["seed"]) for r in rec["runs"]}
    for i in range(1, n + 1):
        for seed in (None, *seeds):
            sid = seed.id if seed else ""
            label = f"pass {i}/{n} {sid or 'clean'}"
            if (i, sid) in done:
                continue
            t = seed.apply(trace) if seed else trace
            try:
                j = judge(client, t, model=model, trace_name=trace_name,
                          principles=principles, seed=sid)
            except Exception as e:  # noqa: BLE001 -- one call must not end the batch
                rec["errors"].append({"pass": i, "seed": sid, "error": f"{type(e).__name__}: {e}"})
                if progress:
                    progress(f"{label}: ERROR {type(e).__name__}")
            else:
                rec["runs"].append({**j.to_dict(), "pass": i})
                if progress:
                    progress(f"{label}: {len(j.findings)} finding(s), {len(j.dropped)} dropped")
            if out is not None:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
    return rec


def report(record: dict[str, Any], trace: dict[str, Any], *,
           seeds: tuple[Seed, ...] = SEEDS, reverify: bool = True) -> dict[str, Any]:
    """Score a measurement already on disk. Free.

    With `reverify`, the recorded answers are pushed through today's
    `verify` rather than trusting the findings stored at the time, so a
    change to the checks is measured on the same answers.
    """
    principles = load_principles()
    by_id = {s.id: s for s in seeds}
    rows: dict[str, dict[str, int]] = {s.id: dict.fromkeys(OUTCOMES, 0) for s in seeds}
    clean: list[dict[str, Any]] = []
    elsewhere = 0
    dropped = kept = 0
    for r in record["runs"]:
        seed = by_id.get(r["seed"])
        t = seed.apply(trace) if seed else trace
        if reverify:
            findings, drops = verify(t, r["answer"], principles)
        else:
            from judge.judge import Finding
            findings, drops = [Finding(**f) for f in r["findings"]], r["dropped"]
        kept += len(findings)
        dropped += len(drops)
        j = Judgement(trace=r["trace"], judged_at=r["judged_at"], model=r["model"],
                      principles=r["principles"], findings=findings, dropped=drops, seed=r["seed"])
        if seed is None:
            clean.extend({"principle_id": f.principle_id, "at": f.at, "why": f.why}
                         for f in findings)
            continue
        rows[seed.id][outcome(seed, j)] += 1
        elsewhere += sum(1 for f in findings if not set(f.at) & set(seed.at))
    trials = sum(sum(v.values()) for v in rows.values())
    caught = sum(v["caught"] for v in rows.values())
    return {"seeds": rows, "trials": trials, "caught": caught,
            "errors": len(record.get("errors", [])),
            "another_name": sum(v["another name"] for v in rows.values()),
            "dropped_catches": sum(v["dropped"] for v in rows.values()),
            "missed": sum(v["missed"] for v in rows.values()),
            "clean_findings": clean, "findings_elsewhere_on_seeded": elsewhere,
            "kept": kept, "dropped": dropped}


#: Below this many trials a share is printed as a count. Eight seeds judged
#: once is eight coin flips, not a catch rate.
MIN_TRIALS_FOR_A_RATE = 30


def render(rep: dict[str, Any], seeds: tuple[Seed, ...] = SEEDS) -> str:
    lines = [f"{'seed':22} {'caught':>7} {'other name':>11} {'dropped':>8} {'missed':>7}   planted"]
    by_id = {s.id: s for s in seeds}
    for sid, v in rep["seeds"].items():
        lines.append(f"{sid:22} {v['caught']:>7} {v['another name']:>11} {v['dropped']:>8} "
                     f"{v['missed']:>7}   "
                     f"{by_id[sid].what}")
    t = rep["trials"]
    lines.append("")
    if t >= MIN_TRIALS_FOR_A_RATE:
        lines.append(f"caught {rep['caught'] / t:.0%} of {t} planted defects")
    else:
        lines.append(f"caught {rep['caught']} of {t} planted defects -- a count, not a rate "
                     f"(under {MIN_TRIALS_FOR_A_RATE} trials)")
    lines.append(f"{rep['another_name']} seen under another principle, {rep['dropped_catches']} "
                 f"seen but dropped for a misplaced quote, {rep['missed']} missed")
    lines.append(f"{rep['kept']} findings kept, {rep['dropped']} dropped by the quote check")
    if rep.get("errors"):
        lines.append(f"{rep['errors']} call(s) failed and are not counted -- rerun the same "
                     f"command to retry them")
    lines.append("")
    if rep["clean_findings"]:
        lines.append(f"On the unaltered exchange, {len(rep['clean_findings'])} finding(s) -- "
                     f"real defects or false alarms, for a person to say which:")
        for f in rep["clean_findings"]:
            lines.append(f"  {f['principle_id']} at {', '.join(f['at'])}: {f['why']}")
    else:
        lines.append("Nothing on the unaltered exchange. That is not a certificate: see the "
                     "missed column.")
    return "\n".join(lines)


def load_record(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
