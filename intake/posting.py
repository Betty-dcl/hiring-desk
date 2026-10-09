"""Turn a job posting into an agenda, without inventing anything.

The agenda is the part of this system that decides what a person is measured
against. Getting it from a model is convenient and it is also the most
dangerous step in the pipeline, because a plausible-sounding criterion that
nobody wrote is indistinguishable, downstream, from one they did.

So the extractor is not trusted. It is required to show its work:

1. Every criterion must carry a `source_quote` -- a verbatim span from the
   posting. `verify()` then goes and looks for that span in the text. A
   criterion whose quote is not found is dropped, and the drop is recorded
   rather than silently swallowed, because the rate at which that happens is
   a property of the extractor worth knowing.

2. `hard` is never inferred. A criterion is a gate only if the posting says
   so in words, and the words go in `hard_quote`, and that quote is verified
   the same way. Causa Prima's Founders' Associate posting says "This is a
   hard requirement, not a buzzword" about one item and nothing of the kind
   about the rest; their Marketing Lead posting says it about none. That
   distinction is in their text, and it is not ours to improve on.

3. Weights are not extracted at all. No prose reliably encodes how much a
   requirement matters relative to another, and a model asked for a number
   will produce one. Section membership sets a default, every criterion is
   flagged `needs_confirmation`, and a human sets the real weight before
   anything runs. See `intake/setup.py`.

What comes out is a draft, and it is named a draft in the type system.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: Where a criterion was found. The section sets the default weight, which is
#: the only thing about a posting's layout that carries meaning we can use.
SECTIONS = {
    "requirement": 2.0,
    "nice_to_have": 1.0,
    "responsibility": 0.0,  # context for reading answers, never scored
}

#: A posting that says this about itself has told us its list is not a set of
#: gates, and the extractor is not allowed to overrule it.
WISHLIST_MARKERS = (
    "wishlist, not a checklist",
    "not a checklist",
    "don't tick every box",
    "don’t tick every box",
    "apply anyway",
)


def normalise(text: str) -> str:
    """Whitespace- and punctuation-insensitive form, for quote matching.

    Models reflow whitespace, convert straight quotes to curly ones and back,
    and turn hyphens into en dashes. None of that makes a quote invented, and
    failing a real quote over a dash would train the next person to loosen
    the check until it stops working.
    """
    text = unicodedata.normalize("NFKD", text)
    text = (text.replace("’", "'").replace("‘", "'")
                .replace("“", '"').replace("”", '"')
                .replace("—", "-").replace("–", "-"))
    return re.sub(r"\s+", " ", text).strip().lower()


@dataclass
class Posting:
    """A job ad, as published, with where it came from."""

    id: str
    title: str
    company: str
    location: str
    text: str
    source_url: str = ""
    fetched_at: str = ""

    @classmethod
    def from_markdown(cls, path: str | Path, **meta: str) -> "Posting":
        """Load one of the saved postings.

        The file's first heading is the title and the italic line beneath it
        is the provenance note, so a posting on disk carries its own source
        rather than relying on a filename.
        """
        p = Path(path)
        raw = p.read_text(encoding="utf-8")
        title = next((l.lstrip("# ").strip() for l in raw.splitlines() if l.startswith("# ")), p.stem)
        body = re.sub(r"^#.*$", "", raw, count=1, flags=re.M)
        body = re.sub(r"^\*Source.*?\*$", "", body, count=1, flags=re.M).strip()
        return cls(
            id=meta.get("id", p.stem.replace("-", "_")),
            title=meta.get("title", title),
            company=meta.get("company", ""),
            location=meta.get("location", ""),
            text=body,
            source_url=meta.get("source_url", ""),
            fetched_at=meta.get("fetched_at", ""),
        )

    @property
    def declares_wishlist(self) -> bool:
        """Did the posting tell us its own list is not a set of gates?"""
        low = normalise(self.text)
        return any(normalise(m) in low for m in WISHLIST_MARKERS)

    def contains(self, quote: str) -> bool:
        return bool(quote) and normalise(quote) in normalise(self.text)


@dataclass
class DraftCriterion:
    """One thing a posting asks for, and the words it asked in."""

    id: str
    question: str
    source_quote: str
    section: str
    looks_like: str = ""
    hard: bool = False
    hard_quote: str = ""
    weight: float = 0.0
    needs_confirmation: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Rejected:
    """A criterion the extractor produced and verification threw out.

    Kept, and reported. An extractor's hallucination rate is a number you
    want on the record, and one that is deleted rather than counted is a
    number nobody will ever look at again.
    """

    id: str
    reason: str
    claimed_quote: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DraftAgenda:
    """What a posting yields, before a human has confirmed anything."""

    posting_id: str
    title: str
    criteria: list[DraftCriterion] = field(default_factory=list)
    rejected: list[Rejected] = field(default_factory=list)
    wishlist_declared: bool = False
    extracted_at: str = ""
    model: str = ""

    @property
    def confirmed(self) -> bool:
        return all(not c.needs_confirmation for c in self.criteria)

    @property
    def scored(self) -> list[DraftCriterion]:
        return [c for c in self.criteria if c.section != "responsibility"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "posting_id": self.posting_id,
            "title": self.title,
            "wishlist_declared": self.wishlist_declared,
            "extracted_at": self.extracted_at,
            "model": self.model,
            "criteria": [c.to_dict() for c in self.criteria],
            "rejected": [r.to_dict() for r in self.rejected],
            "extraction_yield": {
                "kept": len(self.criteria),
                "rejected": len(self.rejected),
                "rate": (round(len(self.criteria) / (len(self.criteria) + len(self.rejected)), 3)
                         if (self.criteria or self.rejected) else None),
            },
        }

    def render(self) -> str:
        lines = [f"{self.title} -- {len(self.scored)} scored criteria", ""]
        for c in self.criteria:
            gate = "  [GATE]" if c.hard else ""
            flag = "  (weight unconfirmed)" if c.needs_confirmation else ""
            lines.append(f"- {c.id} [{c.section}] weight {c.weight:g}{gate}{flag}")
            lines.append(f"    {c.question}")
            lines.append(f'    from: "{c.source_quote[:110]}{"..." if len(c.source_quote) > 110 else ""}"')
            if c.hard:
                lines.append(f'    gate declared by: "{c.hard_quote}"')
        if self.rejected:
            lines += ["", f"rejected {len(self.rejected)} criteria that did not survive verification:"]
            for r in self.rejected:
                lines.append(f"  - {r.id}: {r.reason}")
                lines.append(f'      claimed: "{r.claimed_quote[:110]}"')
        if self.wishlist_declared:
            lines += ["", "the posting declares its own list a wishlist, not a checklist --",
                      "no criterion is a gate unless the posting separately says so."]
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Asking the model
# --------------------------------------------------------------------------

SYSTEM = """\
You read one job posting and list what it asks of a person. You are not
writing a job description, assessing anyone, or improving on the posting.

For every item you list you must give `source_quote`: a span copied
character-for-character out of the posting. It will be checked against the
posting, and an item whose quote is not found there is discarded. Do not
paraphrase into the quote, do not stitch two sentences together, and do not
quote a heading. If you cannot support an item with a contiguous quote, the
posting did not ask for it, so leave it out.

Sections:
  requirement     what they ask the person to be or have
  nice_to_have    what they say is a bonus, preferred, or nice to have
  responsibility  what the person would do in the job

`hard` is true only where the posting itself says in words that the item is a
requirement that cannot be traded off -- "this is a hard requirement", "must
have", "non-negotiable". Put those exact words in `hard_quote`. Enthusiasm is
not hardness. A posting listing something first is not hardness. If the
posting says its list is a wishlist and not a checklist, nothing in it is
hard unless that specific item is separately marked.

Write `question` as the question an interviewer would actually put to a
person to establish the item. Write `looks_like` as what a strong answer
would contain, drawn from the posting, not from your own idea of the role.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "criteria": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "short snake_case identifier, unique in this posting"},
                    "question": {"type": "string"},
                    "source_quote": {"type": "string", "description": "verbatim span copied from the posting"},
                    "section": {"type": "string", "enum": list(SECTIONS)},
                    "looks_like": {"type": "string"},
                    "hard": {"type": "boolean"},
                    "hard_quote": {"type": "string", "description": "verbatim words declaring it non-negotiable; empty when hard is false"},
                },
                "required": ["id", "question", "source_quote", "section"],
            },
        }
    },
    "required": ["criteria"],
}


def extract(client: Any, posting: Posting, *, model: str, max_tokens: int = 4000) -> DraftAgenda:
    """Ask for the agenda, then verify every word of it against the posting."""
    raw = client.structured(
        model=model,
        system=SYSTEM,
        user=(
            f"# Posting: {posting.title}"
            + (f" at {posting.company}" if posting.company else "")
            + f"\n\n{posting.text}"
        ),
        schema=SCHEMA,
        tool_name="list_what_the_posting_asks",
        tool_description="List what this posting asks of a person, each item quoting the posting.",
        max_tokens=max_tokens,
    )
    return verify(posting, raw.get("criteria", []), model=model)


def verify(posting: Posting, items: list[dict[str, Any]], *, model: str = "") -> DraftAgenda:
    """Keep only what the posting actually says. Pure, and testable without a model."""
    agenda = DraftAgenda(
        posting_id=posting.id,
        title=posting.title,
        wishlist_declared=posting.declares_wishlist,
        extracted_at=datetime.now(timezone.utc).isoformat(),
        model=model,
    )
    seen: set[str] = set()

    for item in items:
        cid = str(item.get("id", "")).strip()
        quote = str(item.get("source_quote", "")).strip()
        section = str(item.get("section", "")).strip()

        if not cid:
            agenda.rejected.append(Rejected("(unnamed)", "no id", quote))
            continue
        if cid in seen:
            agenda.rejected.append(Rejected(cid, "duplicate id", quote))
            continue
        if section not in SECTIONS:
            agenda.rejected.append(Rejected(cid, f"unknown section {section!r}", quote))
            continue
        if not posting.contains(quote):
            agenda.rejected.append(
                Rejected(cid, "source_quote does not appear in the posting", quote)
            )
            continue

        hard = bool(item.get("hard"))
        hard_quote = str(item.get("hard_quote", "")).strip()
        if hard and not posting.contains(hard_quote):
            # The item survives; only its claim to be a gate does not.
            agenda.rejected.append(
                Rejected(f"{cid}.hard", "hard_quote does not appear in the posting", hard_quote)
            )
            hard, hard_quote = False, ""

        seen.add(cid)
        agenda.criteria.append(
            DraftCriterion(
                id=cid,
                question=str(item.get("question", "")).strip(),
                source_quote=quote,
                section=section,
                looks_like=str(item.get("looks_like", "")).strip(),
                hard=hard,
                hard_quote=hard_quote,
                weight=SECTIONS[section],
                needs_confirmation=True,
            )
        )
    return agenda


# --------------------------------------------------------------------------
# Writing it out
# --------------------------------------------------------------------------

def _toml_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def to_toml(agenda: DraftAgenda, posting: Posting) -> str:
    """A role mandate `nbh.mandates.load_role` can read.

    Provenance is written into the file as comments rather than kept in a
    sidecar, so that the thing an operator opens and edits is the same thing
    that says where each line came from.
    """
    head = [
        f"# Role mandate for {posting.title}, extracted from the posting.",
        "#",
        f"# Source: {posting.source_url or 'the posting text in intake/postings/'}",
        f"# Extracted: {agenda.extracted_at} by {agenda.model or 'n/a'}",
        "#",
        "# Every criterion below quotes the posting, and every quote was checked",
        "# against it. Weights are NOT from the posting -- they are section",
        "# defaults, and they are wrong until a human sets them. See intake/setup.py.",
    ]
    if agenda.wishlist_declared:
        head += ["#",
                 "# This posting declares its own list a wishlist, not a checklist.",
                 "# Nothing here is a gate unless the posting separately said so."]
    if agenda.rejected:
        head += ["#", f"# {len(agenda.rejected)} extracted items were discarded in verification:"]
        head += [f"#   - {r.id}: {r.reason}" for r in agenda.rejected]

    out = [
        *head, "",
        "[role]",
        f'id = "{_toml_escape(posting.id)}"',
        f'title = "{_toml_escape(posting.title)}"',
        f'company = "{_toml_escape(posting.company)}"',
        f'location = "{_toml_escape(posting.location)}"',
        'summary = """',
        posting.text.strip()[:1500].replace("\\", "\\\\").replace('"""', '\\"\\"\\"'),
        '"""',
        "",
    ]
    for c in agenda.scored:
        out += [
            f'# from the posting: "{c.source_quote}"',
        ]
        if c.hard:
            out.append(f'# declared a gate by: "{c.hard_quote}"')
        out += [
            "[[criteria]]",
            f'id = "{_toml_escape(c.id)}"',
            f'question = "{_toml_escape(c.question)}"',
            f"weight = {c.weight:g}  # section default -- confirm before use",
        ]
        if c.hard:
            out.append("hard = true")
        if c.looks_like:
            out.append(f'looks_like = "{_toml_escape(c.looks_like)}"')
        out.append("")
    return "\n".join(out)


def save(agenda: DraftAgenda, posting: Posting, out_dir: str | Path) -> dict[str, Path]:
    """Write the mandate and the full provenance record side by side."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    toml_path = d / f"role_{posting.id}.toml"
    json_path = d / f"role_{posting.id}.provenance.json"
    toml_path.write_text(to_toml(agenda, posting), encoding="utf-8")
    json_path.write_text(
        json.dumps({"posting": {k: v for k, v in asdict(posting).items() if k != "text"},
                    **agenda.to_dict()}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return {"mandate": toml_path, "provenance": json_path}


def load(path: str | Path) -> DraftAgenda:
    """Read back a provenance record written by `save`."""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    return DraftAgenda(
        posting_id=d["posting_id"],
        title=d["title"],
        criteria=[DraftCriterion(**c) for c in d.get("criteria", [])],
        rejected=[Rejected(**r) for r in d.get("rejected", [])],
        wishlist_declared=d.get("wishlist_declared", False),
        extracted_at=d.get("extracted_at", ""),
        model=d.get("model", ""),
    )
