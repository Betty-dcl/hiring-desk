"""Turn a cover letter into facts, without letting it become a scoring channel.

A letter is not a short CV. Almost every sentence in one is the candidate
characterising themselves, and `screen.py` already caps what
self-characterisation can earn -- so feeding a letter in as another CV
produces either nothing at all or, without the cap, the easiest way there is
to score well: write fluently and recite the posting back. That is the Mara
failure, measured: 76% and second place, on a document with no occasions in
it.

But a letter does three things a CV structurally cannot, and dropping it
loses all three:

  instance      an occasion that is not on the CV. Told with a date, a
                place, a number or an outcome -- a real event that the CV
                had no room for.
  explanation   the account of something the CV leaves open: a gap, a
                pivot, a title that does not mean what it looks like. This
                is the one that matters most, because the alternative is an
                `unknown` that the candidate could have answered and was
                never asked.
  motivation    why this company, this role. Specific knowledge of what the
                company does is the only evidence of it that exists before
                an interview.
  restatement   the CV again, or the posting's own words handed back. Kept
                and labelled rather than dropped, so that a letter made
                entirely of these is visible as such instead of merely
                scoring nothing for reasons nobody can see.

The quote discipline is the same as everywhere else: every fact carries a
span copied from the letter, the span is verified against the document, and a
fact whose span is not found is discarded and counted.

What this module does NOT do is decide how much a letter fact is worth --
that is `screen.LETTER_CAP`, applied in Python after the model has answered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from intake.cv import EVIDENCE, KINDS, Fact, Facts, Profile
from intake.posting import Rejected

#: What a sentence in a letter is doing. The list is short on purpose: a
#: taxonomy fine enough to be interesting is fine enough to argue about.
ROLES = ("instance", "explanation", "motivation", "restatement")


@dataclass
class Letter(Profile):
    """A cover letter as submitted. Same contract as `Profile`: the scorer
    never sees the text, only the facts drawn out of it."""


SYSTEM = """\
You read one cover letter and list what it states. You are not assessing the
person, comparing them to a role, or deciding whether anything is impressive.

For every fact, give `source_quote`: a span copied character-for-character
from the letter. It is checked against the document, and a fact whose quote
is not found is discarded. Do not paraphrase into the quote.

A cover letter is mostly the candidate describing themselves, and that is
expected. Do not inflate it. Your job is to separate the few sentences that
carry something new from the many that do not.

Give every fact a `role`:

  instance      an occasion, told with enough specifics that a reader could
                ask "when, and what exactly?" and find the answer in the
                sentence. A thing built, run, shipped or delivered; a date,
                a number, a named place or system.
  explanation   an account of something a CV would leave open -- a gap
                between dates, a change of field, a title that does not mean
                what it appears to, a missing qualification.
  motivation    why this company or this role, including anything specific
                about what the company does. Not "I admire your mission".
  restatement   a claim the CV already makes, or the posting's own language
                handed back. Most flattering sentences are this.

Also give `evidence`, the same test used everywhere: `instance` if something
happened, `assertion` if it is the candidate characterising themselves. A
sentence can be a `motivation` role and an `assertion` at once; most are.

When a sentence could be two roles, choose the less generous one. A
restatement dressed in a specific-sounding noun is still a restatement.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "short snake_case identifier, unique in this letter"},
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "claim": {"type": "string", "description": "what the letter states, in plain words"},
                    "source_quote": {"type": "string", "description": "verbatim span copied from the letter"},
                    "role": {"type": "string", "enum": list(ROLES)},
                    "evidence": {"type": "string", "enum": list(EVIDENCE)},
                    "answers": {"type": "string",
                                "description": "for an explanation: what the CV leaves open, in a few words"},
                },
                "required": ["id", "kind", "claim", "source_quote", "role", "evidence"],
            },
        },
    },
    "required": ["facts"],
}


def extract(client: Any, letter: Letter, *, model: str, max_tokens: int = 4000) -> Facts:
    raw = client.structured(
        model=model,
        system=SYSTEM,
        user=f"# Cover letter\n\n{letter.text}",
        schema=SCHEMA,
        tool_name="list_what_the_letter_states",
        tool_description="List what this letter states, each item quoting the document.",
        max_tokens=max_tokens,
    )
    return verify(letter, raw.get("facts", []), model=model)


def verify(letter: Letter, items: list[dict[str, Any]], *, model: str = "") -> Facts:
    """Keep only what the letter actually says. Pure, testable without a model."""
    out = Facts(
        candidate_id=letter.id,
        name=letter.name,
        extracted_at=datetime.now(timezone.utc).isoformat(),
        model=model,
    )
    seen: set[str] = set()
    for item in items:
        fid = str(item.get("id", "")).strip()
        quote = str(item.get("source_quote", "")).strip()
        kind = str(item.get("kind", "other")).strip()
        role = str(item.get("role", "")).strip()

        if not fid:
            out.rejected.append(Rejected("(unnamed)", "no id", quote))
            continue
        if fid in seen:
            out.rejected.append(Rejected(fid, "duplicate id", quote))
            continue
        if kind not in KINDS:
            out.rejected.append(Rejected(fid, f"unknown kind {kind!r}", quote))
            continue
        if role not in ROLES:
            #: An unlabelled sentence is not dropped -- it is the least
            #: generous role, because that is the one a missing label most
            #: often hides.
            role = "restatement"
        if not letter.contains(quote):
            out.rejected.append(Rejected(fid, "source_quote does not appear in the letter", quote))
            continue

        evidence = str(item.get("evidence", "")).strip()
        if evidence not in EVIDENCE:
            evidence = "assertion"
        #: A role of `instance` and an evidence of `assertion` is a
        #: contradiction the model is allowed to produce and the code is not
        #: allowed to keep: the conservative reading wins.
        if role == "instance" and evidence != "instance":
            role = "restatement"

        seen.add(fid)
        out.facts.append(
            Fact(
                id=fid if fid.startswith("letter_") else f"letter_{fid}",
                kind=kind,
                claim=str(item.get("claim", "")).strip(),
                source_quote=quote,
                period=str(item.get("period", "")).strip(),
                evidence=evidence,
                source="letter",
                role=role,
            )
        )
    return out


@dataclass
class Motivation:
    """What a letter shows about wanting *this* job, counted and never scored.

    Motivation is self-description, and self-description is capped in
    `screen.py` so that writing well earns nothing. Folding it into the fit
    percentage would break the one property that makes the percentage worth
    reading.

    So it is reported beside the score, as a count with its quotes attached.
    Two numbers of two different natures, never averaged: a reader can say
    "her fit is 62% and she knows three specific things about us", which is
    an argument a human can weigh. Averaged into one number, that argument
    disappears.
    """

    specific: list[Fact] = field(default_factory=list)
    generic: list[Fact] = field(default_factory=list)

    @property
    def counts(self) -> tuple[int, int]:
        return len(self.specific), len(self.generic)

    def to_dict(self) -> dict[str, Any]:
        return {
            "specific": [f.to_dict() for f in self.specific],
            "generic": [f.to_dict() for f in self.generic],
            "is_not_a_score": "counted beside the fit measurement, never inside it",
        }

    def render(self) -> str:
        n, g = self.counts
        head = (f"motivation: {n} specific reference(s) to what the company does, "
                f"{g} generic")
        lines = [head]
        for f in self.specific:
            lines.append(f'    + "{f.source_quote[:100]}"')
        for f in self.generic:
            lines.append(f'    - "{f.source_quote[:100]}"')
        lines.append("    (a count, not a score -- it is never added to the percentage)")
        return "\n".join(lines)


def motivation(facts: Facts) -> Motivation:
    """Split the letter's motivation from its flattery.

    The classification is the extractor's, made under a written definition --
    "why this company or this role, including anything specific about what
    the company does. Not 'I admire your mission'" -- and every item keeps
    its quote, so a reader who disagrees can see exactly what was counted.
    """
    out = Motivation()
    for f in facts.facts:
        if not f.from_letter:
            continue
        if f.role == "motivation":
            out.specific.append(f)
        elif f.role == "restatement":
            out.generic.append(f)
    return out


def merge(cv_facts: Facts, letter_facts: Facts) -> Facts:
    """One set of facts for the screener, with every letter fact still labelled.

    The CV comes first and keeps its ids. Nothing is deduplicated: a letter
    that repeats the CV is a fact about the letter, and collapsing the two
    would hide it.
    """
    if cv_facts.candidate_id != letter_facts.candidate_id:
        raise ValueError(
            f"these are two different people: {cv_facts.candidate_id!r} and "
            f"{letter_facts.candidate_id!r}"
        )
    taken = {f.id for f in cv_facts.facts}
    kept = [f for f in letter_facts.facts if f.id not in taken]
    return Facts(
        candidate_id=cv_facts.candidate_id,
        name=cv_facts.name or letter_facts.name,
        facts=[*cv_facts.facts, *kept],
        rejected=[*cv_facts.rejected, *letter_facts.rejected],
        extracted_at=cv_facts.extracted_at,
        model=cv_facts.model,
    )


def load_letter(path: str | Path, *, id: str = "", name: str = "") -> Letter:
    p = Path(path)
    return Letter.from_file(p, id=id, name=name)  # type: ignore[return-value]
