"""What they would want to own, found by a model when the application arrives.

NOT RUN. Written and tested with a fake client; whether it runs, and on whom,
is the team's decision -- and the demo would have to be screened again for its
cards to show it.

Why it exists. The card's "Wants to own" line is found today by patterns
(`signals.WANTS`), with no model. Patterns find the phrasings someone thought
of: on sentences written to break them they find three wishes in eight
(`tests/wants_corpus.py`, STRESS). "The close is what I'd sink my teeth
into", "Ownership of the investor update is something I'd welcome": a reader
sees a wish in each, a pattern does not, and the card then says "not found".
It says it honestly -- "not found -- open the letter" -- but a partner should
not have to.

What it adds, and what it cannot change. The model is asked for spans only,
copied from the documents; it never writes the line on the card. Every span
is checked against the document before it is kept (`verify`), and again by
`signals.verbatim` before it is shown. So the worst a model can do here is
pick the wrong sentence of theirs -- never put words in their mouth. It is
read by `signals` before the patterns, and falls back to them.

Cost: one short call per application, at arrival, on the letter and the CV.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from intake.posting import normalise

SOURCES = ("letter", "cv", "other")

SYSTEM = """\
You read a job application: a cover letter and a CV. List the sentences in
which the candidate says what they would want to own, take on, build or be
responsible for if they joined.

For each, give `source_quote`: the span copied character-for-character from
the document, and `source`: which document it is in. It is checked against
the document, and a span that is not found is discarded. Do not paraphrase,
do not shorten inside the span, do not join two sentences.

Do NOT list:
  - what they did or owned in the past ("I owned the close for two entities");
  - politeness ("I'd like to thank you", "I'd love to hear from you");
  - why they like the company ("what draws me to you is...");
  - what someone else wants ("the founders want to automate the close");
  - qualities they claim ("I take extreme ownership of everything").

If there is no such sentence, return an empty list. An empty list is a
correct answer; it is shown to the team as "not found".
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "wants": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_quote": {"type": "string",
                                     "description": "verbatim span copied from the document"},
                    "source": {"type": "string", "enum": list(SOURCES)},
                },
                "required": ["source_quote", "source"],
            },
        },
    },
    "required": ["wants"],
}


@dataclass
class Wants:
    candidate_id: str
    wants: list[dict[str, str]] = field(default_factory=list)
    #: Spans the model gave that are not in the document: counted, not shown.
    rejected: list[dict[str, str]] = field(default_factory=list)
    extracted_at: str = ""
    model: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def verify(candidate_id: str, texts: dict[str, str], items: list[dict[str, Any]], *,
           model: str = "") -> Wants:
    """Keep only spans their own documents contain. Pure, testable without a model."""
    out = Wants(candidate_id, extracted_at=datetime.now(timezone.utc).isoformat(), model=model)
    for item in items:
        q = str(item.get("source_quote", "")).strip()
        src = str(item.get("source", "")).strip()
        doc = texts.get(src, "")
        if q and src in SOURCES and normalise(q) in normalise(doc):
            if {"source_quote": q, "source": src} not in out.wants:
                out.wants.append({"source_quote": q, "source": src})
        else:
            out.rejected.append({"source_quote": q, "source": src,
                                 "why": "not in the document named"})
    return out


def extract(client: Any, candidate_id: str, texts: dict[str, str], *, model: str,
            max_tokens: int = 1500) -> Wants:
    """One call. `texts` maps "letter"/"cv"/"other" to the document's text."""
    user = "\n\n".join(f"# {k}\n\n{texts[k]}" for k in SOURCES if texts.get(k, "").strip())
    raw = client.structured(
        model=model,
        system=SYSTEM,
        user=user,
        schema=SCHEMA,
        tool_name="list_what_they_want_to_own",
        tool_description="List the sentences where the candidate says what they would want to own.",
        max_tokens=max_tokens,
    )
    return verify(candidate_id, texts, raw.get("wants", []), model=model)


def save(w: Wants, out_dir: str | Path) -> Path:
    """Where `signals` looks: runs/wants/wants_<candidate>.json."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"wants_{w.candidate_id}.json"
    p.write_text(json.dumps(w.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return p
