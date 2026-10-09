"""Run the judge.

    # one exchange, one call on the smaller model
    ./.venv/bin/python -m judge run runs/exchange_ines_abadi.json

    # plant eight defects and see which ones it names -- nine calls per pass
    ./.venv/bin/python -m judge seeded --trace runs/exchange_ines_abadi.json -n 1

    # re-read a seeded measurement already on disk -- free, no model
    ./.venv/bin/python -m judge report runs/judge/seeded_exchange_ines_abadi_n1.json

    # many judgements -> the order a person should review them in -- free
    ./.venv/bin/python -m judge agenda "runs/judge/exchange_*.json"
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

from judge import agenda, seeds
from judge.judge import judge, load, save

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "runs" / "judge"


def _client():
    from nbh.llm import default_client
    return default_client()


def _model() -> str:
    from nbh.llm import JUDGE_MODEL
    return JUDGE_MODEL


def _run(path: str) -> int:
    trace = json.loads(Path(path).read_text(encoding="utf-8"))
    name = Path(path).stem
    j = judge(_client(), trace, model=_model(), trace_name=name)
    out = save(j, OUT / f"{name}.json")
    for f in j.findings:
        print(f"{f.principle_id} at {', '.join(f.at)}\n    {f.why}")
    print(f"\n{len(j.findings)} finding(s), {len(j.dropped)} dropped by the quote check -> {out}")
    return 0


def _seeded(path: str, n: int) -> int:
    trace = json.loads(Path(path).read_text(encoding="utf-8"))
    name = Path(path).stem
    out = OUT / f"seeded_{name}_n{n}.json"
    rec = seeds.measure(_client(), trace, model=_model(), trace_name=name, n=n, out=out,
                        progress=lambda s: print(s, file=sys.stderr, flush=True))
    rec["trace_path"] = str(Path(path).resolve().relative_to(ROOT))
    out.write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
    print(seeds.render(seeds.report(rec, trace)))
    print(f"\n-> {out}")
    return 1 if rec.get("errors") else 0


def _report(path: str) -> int:
    rec = seeds.load_record(path)
    # A record cut short by an incident has no trace_path yet; the name is enough.
    where = rec.get("trace_path") or f"runs/{rec['trace']}.json"
    trace = json.loads((ROOT / where).read_text(encoding="utf-8"))
    print(seeds.render(seeds.report(rec, trace)))
    return 0


def _agenda(patterns: list[str]) -> int:
    files = sorted({f for p in patterns for f in glob.glob(p)})
    judged = [load(f) for f in files if not Path(f).name.startswith("seeded_")]
    if not judged:
        print("no judgements matched", file=sys.stderr)
        return 2
    print(agenda.render(agenda.build(judged)))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m judge")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="judge one recorded exchange (one call)")
    r.add_argument("trace")
    s = sub.add_parser("seeded", help="plant defects, measure what the judge names")
    s.add_argument("--trace", default="runs/exchange_ines_abadi.json")
    s.add_argument("-n", type=int, default=1)
    rp = sub.add_parser("report", help="re-read a seeded measurement (free)")
    rp.add_argument("record")
    ag = sub.add_parser("agenda", help="judgements -> a review agenda (free)")
    ag.add_argument("paths", nargs="+")
    a = ap.parse_args()
    if a.cmd == "run":
        return _run(a.trace)
    if a.cmd == "seeded":
        return _seeded(a.trace, a.n)
    if a.cmd == "report":
        return _report(a.record)
    return _agenda(a.paths)


if __name__ == "__main__":
    raise SystemExit(main())
