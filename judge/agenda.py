"""Many judged exchanges, one page for the person who has to review them.

Nobody reads two hundred judgements. What a reviewer needs is the order to
read them in: which principle keeps coming back, on how many exchanges, and
two or three places to look first. Grouping is by principle and nothing
cleverer -- a clustering a reviewer cannot predict is one more thing to
check.

Arithmetic over records on disk. No model, no cost.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from judge.judge import Judgement, load_principles

EXAMPLES = 3


def build(judgements: list[Judgement]) -> dict[str, Any]:
    real = [j for j in judgements if not j.seed]
    by: dict[str, list[tuple[Judgement, Any]]] = defaultdict(list)
    for j in real:
        for f in j.findings:
            by[f.principle_id].append((j, f))
    axes = {p.id: p.axis for p in load_principles()}
    items = []
    for pid, hits in by.items():
        traces = sorted({j.trace for j, _ in hits})
        items.append({
            "principle_id": pid, "axis": axes.get(pid, "?"),
            "exchanges": len(traces), "findings": len(hits),
            "look_first": [{"trace": j.trace, "at": f.at, "why": f.why}
                           for j, f in hits[:EXAMPLES]],
        })
    items.sort(key=lambda i: (-i["exchanges"], -i["findings"], i["principle_id"]))
    quiet = sum(1 for j in real if not j.findings)
    return {"exchanges": len(real), "with_nothing_found": quiet,
            "principles": items, "left_out_seeded": len(judgements) - len(real),
            "dropped_by_quote_check": sum(len(j.dropped) for j in real)}


def render(a: dict[str, Any]) -> str:
    lines = [f"# Review agenda -- {a['exchanges']} exchange(s) judged", ""]
    if not a["principles"]:
        lines.append("The judge named nothing. Read a sample anyway: the judge's measured catch "
                     "rate is in `python -m judge report`, and it is not 100%.")
    for i, p in enumerate(a["principles"], 1):
        lines.append(f"{i}. **{p['principle_id']}** ({p['axis']}) -- {p['exchanges']} exchange(s), "
                     f"{p['findings']} finding(s)")
        for ex in p["look_first"]:
            lines.append(f"   - {ex['trace']} at {', '.join(ex['at'])}: {ex['why']}")
    lines += ["",
              f"{a['with_nothing_found']} exchange(s) with nothing found -- not reviewed by "
              f"anyone, which is different from cleared.",
              f"{a['dropped_by_quote_check']} finding(s) dropped because their quote was not in "
              f"the trace.",
              "The judge is a regression net. It does not grade an exchange and it decides "
              "nothing about a candidate."]
    if a["left_out_seeded"]:
        lines.append(f"{a['left_out_seeded']} seeded run(s) left out: planted defects are not "
                     f"part of anyone's review.")
    return "\n".join(lines)
