"""What to send back to the person who applied.

A careers page that says every application is read is making a promise whose
cost grows with the company. At twenty applications it is kept by hand. At
four hundred it is kept badly or not at all, and "we read every one" becomes the
thing the page says rather than the thing that happens.

This writes the note. Three rules make it worth sending rather than worth
apologising for:

**It contains no decision.** Not a yes, not a no, not a maybe, not a ranking,
not a comparison with anybody else. The screener has no reject state and this
note cannot invent one. What a human decides afterwards is their business and
is not in here.

**Every line is traceable.** What was read is quoted from the candidate's own
document. What was not answered is named with the criterion the company wrote,
in the company's words, and with what the document would have had to state.
Nothing is generated: the note is assembled from the assessment record, so it
cannot flatter, cannot invent a strength, and cannot soften a gap that is in
the data.

**It says a machine wrote it.** Article 50 of the AI Act made transparency
about automated processing apply in August 2026, and a note that reads as a
personal letter when it is not is worse than no note. It also says what a
person can do about it.

The one thing it deliberately gives away is the criteria. A candidate who
learns they were measured on `ai_native` and had nothing to point to has been
told something useful and true. The usual objection -- that publishing a
rubric lets people optimise for it -- is answered elsewhere in this system:
writing the words earns `weak`, and has been measured doing so.
"""

from __future__ import annotations

from typing import Any

from intake.cv import Facts
from screen import Screening


def note(s: Screening, facts: Facts | None = None, agenda: Any = None, *,
         company: str = "", spread: float | None = None,
         reviewer_pending: bool = True) -> str:
    """Assemble the note. No model, no generation, nothing not in the record."""
    by_crit = {c.id: c for c in (getattr(agenda, "criteria", []) or [])}
    by_fact = {f.id: f for f in (facts.facts if facts else [])}
    who = f" at {company}" if company else ""

    landed = [a for a in s.assessments if a.strength in ("strong", "moderate")]
    thin = [a for a in s.assessments if a.strength == "weak"]
    silent = [a for a in s.assessments if a.unknown]

    out = [
        f"About your application{who}.",
        "",
        "This note was written by the screening system, not by a person. It "
        "tells you what your",
        "application was read against and what it did not answer. It is not a "
        "decision, and the",
        "system that produced it has no way of making one.",
        "",
    ]

    if landed:
        out.append("What your documents established:")
        out.append("")
        for a in landed:
            c = by_crit.get(a.criterion_id)
            asked = f' — asked for as: "{c.source_quote[:88]}"' if c else ""
            out.append(f"  {a.criterion_id}{asked}")
            for fid in a.fact_ids[:2]:
                f = by_fact.get(fid)
                if f:
                    where = "your letter" if f.from_letter else "your CV"
                    out.append(f'      read from {where}: "{f.source_quote[:88]}"')
        out.append("")

    if thin:
        out.append("What was there but thin — the claim is in your documents, an occasion")
        out.append("that would have shown it is not:")
        out.append("")
        for a in thin:
            why = " (self-description only)" if a.rests_on == "assertions only" else ""
            out.append(f"  {a.criterion_id}{why}")
        out.append("")

    if silent:
        out.append("What your documents did not speak to at all. These are not marks")
        out.append("against you — they are things the papers left open:")
        out.append("")
        for a in silent:
            c = by_crit.get(a.criterion_id)
            out.append(f"  {a.criterion_id}")
            if c is not None and getattr(c, "question", ""):
                out.append(f'      what would answer it: "{c.question[:92]}"')
            elif a.reasoning:
                out.append(f"      what would answer it: {a.reasoning[:92]}")
        out.append("")

    out.append(f"Coverage: your documents spoke to {s.coverage:.0%} of what this role "
               f"asks about.")
    if spread is not None:
        out.append(f"The same documents, read again, move this measurement by about "
                   f"{spread:.0%}.")
    if s.open_gates:
        out.append(f"Conditions this role declares that your documents leave open: "
                   f"{', '.join(s.open_gates)}.")
    out.append("")

    if reviewer_pending:
        out.append("A person has not read this yet. When one does, they can disagree with")
        out.append("any line above, and their disagreement is recorded with their name on it.")
    out.append("")
    out.append("If something here is wrong — a criterion you did answer and we read as")
    out.append("silent, or evidence quoted out of its context — reply and say which line.")
    out.append("That correction goes to a person, not back into this system.")
    return "\n".join(out)
