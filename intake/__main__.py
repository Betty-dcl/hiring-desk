"""Turn a posting into a draft agenda, or a cover letter into facts.

    ./.venv/bin/python -m intake extract intake/postings/founders-associate.md
    ./.venv/bin/python -m intake extract intake/postings/*.md --out mandates/generated

    # a letter, merged onto the CV facts already extracted
    ./.venv/bin/python -m intake letter personas/sylvia_hartmann_letter.md \
        --candidate sylvia_hartmann --out runs/facts_with_letter

What comes out is a draft: the criteria are quoted from the posting and the
quotes are verified, but the weights are section defaults and are wrong until
someone sets them.
"""

from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

from intake.posting import Posting, extract, save

ROOT = Path(__file__).resolve().parent.parent

#: Metadata a posting's text does not carry. Sourced from the board the
#: postings were published on, not inferred from their contents.
BOARD = {
    "company": "Causa Prima",
    "source_url": "https://jobs.ashbyhq.com/causaprima",
    "fetched_at": "2026-09-18",
}


def _letter(path: str, candidate: str, out_dir: str, model: str | None) -> int:
    """Letter -> facts, merged onto the CV facts and written as a new cohort.

    Written beside the CV-only facts rather than over them: the pair is what
    makes the letter's effect measurable, and overwriting the baseline would
    destroy the only thing worth comparing against.
    """
    import json

    from intake.cv import load as load_facts
    from intake.letter import extract as extract_letter, load_letter, merge
    from nbh.llm import AGENT_MODEL, LLMError, default_client

    cv_path = ROOT / "runs" / "facts" / f"facts_{candidate}.json"
    if not cv_path.exists():
        print(f"no CV facts for {candidate!r}: {cv_path.relative_to(ROOT)}", file=sys.stderr)
        return 2
    try:
        client = default_client()
    except LLMError as e:
        print(f"\n{e}\n", file=sys.stderr)
        return 2

    letter = load_letter(path, id=candidate, name="")
    facts = extract_letter(client, letter, model=model or AGENT_MODEL)
    print(f"{len(facts.facts)} facts kept, {len(facts.rejected)} rejected")
    for f in facts.facts:
        print(f"  {f.role:<12} {f.evidence:<10} {f.claim[:70]}")
    for r in facts.rejected:
        print(f"  REJECTED {r.id}: {r.reason}")

    both = merge(load_facts(cv_path), facts)
    d = ROOT / out_dir
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"facts_{candidate}.json"
    p.write_text(json.dumps(both.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n{len(both.facts)} facts in the merged set -> {p.relative_to(ROOT)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="intake", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("extract", help="posting -> draft agenda + provenance")
    e.add_argument("paths", nargs="+")
    e.add_argument("--out", default="mandates/generated")
    e.add_argument("--location", default="Madrid, on-site")
    e.add_argument("--model", default=None)
    e.add_argument("--backend", choices=("claude-code", "api"), default="claude-code")

    l = sub.add_parser("letter", help="cover letter -> facts, merged onto the CV facts")
    l.add_argument("path")
    l.add_argument("--candidate", required=True)
    l.add_argument("--out", default="runs/facts_with_letter")
    l.add_argument("--model", default=None)

    args = ap.parse_args()
    if args.cmd == "letter":
        return _letter(args.path, args.candidate, args.out, args.model)

    from nbh.llm import AGENT_MODEL, ClaudeCodeClient, Client, LLMError

    model = args.model or AGENT_MODEL
    try:
        client = ClaudeCodeClient() if args.backend == "claude-code" else Client()
    except LLMError as err:
        print(f"\n{err}\n", file=sys.stderr)
        return 2

    files: list[Path] = []
    for p in args.paths:
        files.extend(Path(f) for f in sorted(glob.glob(p)))
    if not files:
        print("no postings matched", file=sys.stderr)
        return 2

    for f in files:
        posting = Posting.from_markdown(
            f, id=f.stem.replace("-", "_"), location=args.location, **BOARD
        )
        agenda = extract(client, posting, model=model)
        written = save(agenda, posting, ROOT / args.out)
        print()
        print(agenda.render())
        print()
        print(f"  mandate:    {written['mandate'].relative_to(ROOT)}")
        print(f"  provenance: {written['provenance'].relative_to(ROOT)}")

    u = client.usage.to_dict()
    print(f"\n{u['calls']} calls, {u['input_tokens'] + u['output_tokens']:,} tokens, "
          f"${u['cost_usd']:.2f} at API list price")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
