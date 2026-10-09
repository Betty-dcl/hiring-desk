#!/usr/bin/env python3
"""Run one exchange, end to end, and write the trace.

    ANTHROPIC_API_KEY=sk-... ./.venv/bin/python run.py
    ./.venv/bin/python run.py --replay runs/<file>.json     # no key needed

Every run lands in runs/ as a single JSON file holding the transcript, the
reasoning behind every act, the ledger before and after, both verdicts, any
protocol violations, and what it cost. That file is the unit the harness
replays and the console reads -- there is no other source of truth.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from nbh import verdict as verdict_mod
from nbh.agent import run_exchange
from nbh.llm import (
    AGENT_MODEL,
    JUDGE_MODEL,
    ClaudeCodeClient,
    Client,
    LLMError,
    ReplayClient,
)
from nbh.mandates import load_candidate, load_role


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--role", default="mandates/role_causa_prima_open_door.toml")
    ap.add_argument("--candidate", default="mandates/candidate_ines.toml")
    ap.add_argument("--max-turns", type=int, default=24)
    ap.add_argument("--model", default=AGENT_MODEL)
    ap.add_argument("--assessor-model", default=JUDGE_MODEL)
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument(
        "--backend",
        choices=("claude-code", "api"),
        default="claude-code",
        help=(
            "where the model comes from. claude-code (default) shells out to the "
            "Claude Code CLI and needs no API key; api needs ANTHROPIC_API_KEY and "
            "gets a schema the server enforces."
        ),
    )
    ap.add_argument("--replay", metavar="RUN.json", help="replay a recorded run instead of calling a model")
    ap.add_argument("--out", default=None, help="where to write the trace (default: runs/<timestamp>.json)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    role = load_role(ROOT / args.role)
    candidate = load_candidate(ROOT / args.candidate)

    if args.replay:
        client = ReplayClient.from_run(ROOT / args.replay)
    else:
        try:
            if args.backend == "claude-code":
                client = ClaudeCodeClient(temperature=args.temperature)
            else:
                client = Client(temperature=args.temperature)
        except LLMError as e:
            print(f"\n{e}\n", file=sys.stderr)
            print("To run with no key and no model at all, replay a recorded trace:", file=sys.stderr)
            print("  ./.venv/bin/python run.py --replay runs/<file>.json", file=sys.stderr)
            return 2

    run = run_exchange(
        client,
        role,
        candidate,
        max_turns=args.max_turns,
        model=args.model,
        assessor_model=args.assessor_model,
    )
    verdicts = verdict_mod.render(client, run.exchange, role, candidate, model=args.assessor_model)

    if not args.quiet:
        print(f"\n{role.company} -- {role.title}")
        print(f"candidate: {candidate.name}\n")
        for m in run.exchange.transcript:
            anchor = f" [{m.criterion_id}]" if m.criterion_id else ""
            print(f"  {m.turn:>2} {m.speaker.value:<9} {m.act.value:<8}{anchor}")
            print(f"     {m.body}")
        c = verdicts["computed"]
        print(f"\nended: {c['ended_reason']} after {c['turns_used']} turns")
        print(f"coverage {c['coverage']:.0%} | fit {c['fit_score']:.0%} | {c['recommendation']}")
        if run.violations:
            print(f"protocol violations: {len(run.violations)}")
        print(f"\nto the company:\n  {verdicts['company']['note']}")
        print(f"\nto the candidate:\n  {verdicts['candidate']['note']}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out) if args.out else ROOT / "runs" / f"{stamp}_{candidate.id}_{role.id}.json"
    out = out if out.is_absolute() else Path.cwd() / out
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {
            "at": datetime.now(timezone.utc).isoformat(),
            "role": role.id,
            "candidate": candidate.id,
            "backend": "replay" if args.replay else args.backend,
            "model": args.model,
            "assessor_model": args.assessor_model,
            "temperature": args.temperature,
            "replayed_from": args.replay,
        },
        **run.to_dict(),
        "verdicts": verdicts,
        "usage": client.usage.to_dict(),
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    if not args.quiet:
        shown = out.relative_to(ROOT) if out.is_relative_to(ROOT) else out
        print(f"\ntrace: {shown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
