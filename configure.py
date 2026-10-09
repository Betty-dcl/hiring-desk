#!/usr/bin/env python3
"""Ask the few things a posting never says -- after showing what they change.

    ./.venv/bin/python configure.py --posting founders_associate --by "<name>"
    ./.venv/bin/python configure.py --posting founders_associate --show

Two rules shape this file.

**Show the consequence before asking.** Anybody can pick a number for "what is
a partial answer worth". Almost nobody can picture what it does to a ranking,
and this repository has measured that it does more than the model does. So the
scale question arrives with the table of what each option would have done to
the cohort already scored.

**Never invent an answer.** Skipping a question leaves the extractor's default
in place, and the default is reported as unanswered rather than quietly
becoming a decision. A settings file with three answers and an honest gap
beats one with twelve answers somebody guessed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import settings as settings_mod
from intake.posting import load as load_agenda

#: The scales offered by name, so a choice is a position rather than a number
#: somebody typed. `harness/formula.py` searched these bounds against
#: expectations written before the first run; `current` is what ships.
NAMED_SCALES = {
    "strict": ({"strong": 1.0, "moderate": 0.5, "weak": 0.15},
               "a half-answer earns half, a thin hint earns almost nothing"),
    "current": ({"strong": 1.0, "moderate": 0.6, "weak": 0.25},
                "what this repository ships, and what its own search rejects"),
    "searched": ({"strong": 1.0, "moderate": 0.86, "weak": 0.12},
                 "the centre of the region that satisfies the pre-registered bands"),
    "generous": ({"strong": 1.0, "moderate": 0.7, "weak": 0.35},
                 "for hiring on potential rather than on proof"),
}


def _consequences(posting: str) -> str:
    """What each scale would have done to the cohort already on disk."""
    import glob

    from harness.sensitivity import score_under
    from screen import load as load_screening

    files = [f for f in sorted(glob.glob(str(ROOT / "runs" / "screenings" / f"*_{posting}.json")))
             if not Path(f).name.startswith(("ranking_", "reviewed_"))]
    if not files:
        return "  (no cohort scored yet, so there is nothing to show you)"
    screenings = [load_screening(f) for f in files]

    lines = ["  what each option would have done to the cohort already scored:", ""]
    lines.append("    " + "candidate".ljust(20) + "".join(n.rjust(11) for n in NAMED_SCALES))
    for s in sorted(screenings, key=lambda s: -s.score):
        row = "".join(f"{score_under(s, c):>10.0%} " for c, _ in NAMED_SCALES.values())
        lines.append(f"    {s.candidate_id:<20}{row}")
    lines.append("")
    for name, (_, why) in NAMED_SCALES.items():
        lines.append(f"    {name:<10} {why}")
    return "\n".join(lines)


def _ask(prompt: str, default: str = "") -> str:
    try:
        got = input(prompt).strip()
    except EOFError:
        return default
    return got or default


def _interactive(posting: str, by: str) -> int:
    agenda = load_agenda(ROOT / "mandates" / "generated"
                         / f"role_{posting}.provenance.json")
    s = settings_mod.load(posting)
    s.answered_by = by

    print(f"\n{posting}: {len(agenda.criteria)} criteria extracted from your posting.")
    print("Answer what you can. Anything you skip keeps the extractor's default,")
    print("and the report will say so rather than pretend you chose it.\n")

    print(_consequences(posting))
    print()
    choice = _ask(f"  which scale? [{'/'.join(NAMED_SCALES)}] or blank to leave it: ")
    if choice in NAMED_SCALES:
        s.scale = dict(NAMED_SCALES[choice][0])
        print(f"  -> {choice}\n")
    elif choice:
        print(f"  '{choice}' is not one of them; leaving the scale unanswered\n")

    print("  Now the weights. 1 is nice to have, 3 is the reason you are hiring.")
    print("  Blank keeps the extracted default and marks the criterion unanswered.\n")
    for c in agenda.criteria:
        gate = "  [the posting declares this a condition]" if c.hard else ""
        print(f'  {c.id}{gate}\n      "{c.source_quote[:96]}"')
        got = _ask(f"      weight (default {c.weight:g}): ")
        if got:
            try:
                s.weights[c.id] = float(got)
            except ValueError:
                print("      not a number; left unanswered")
        print()

    extra = _ask("  Any criterion that is a condition rather than a preference? "
                 "(ids, comma-separated, blank for none): ")
    if extra:
        s.gates = [x.strip() for x in extra.split(",") if x.strip()]
    s.notes = _ask("  One line on anything the questions missed: ")

    f = settings_mod.save(s)
    print(f"\nwritten: {f.relative_to(ROOT)}")
    left = s.unanswered_criteria(agenda)
    if left:
        print(f"{len(left)} criteria still on the extractor's default: {', '.join(left)}")
        print("That is recorded, not hidden -- the report says which scores rest on it.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="configure", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--posting", required=True)
    ap.add_argument("--by", default="", help="who is answering; required to save")
    ap.add_argument("--show", action="store_true", help="print the current answers and exit")
    a = ap.parse_args()

    if a.show:
        print(settings_mod.load(a.posting).render())
        return 0
    if not a.by:
        print("--by is required: a settings file is a claim about a company, so it "
              "records who made it", file=sys.stderr)
        return 2
    return _interactive(a.posting, a.by)


if __name__ == "__main__":
    raise SystemExit(main())
