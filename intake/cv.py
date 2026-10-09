"""Turn a CV into structured facts, each one quoted from the document.

Two decisions here, both taken from the published work on bias in hiring
models rather than from taste.

**The scorer never sees this document.** It sees the facts this module
extracts. Scoring a CV as prose means scoring everything in it -- the name,
the school, the address, the gaps, the phrasing -- and those are the proxy
variables through which a model reproduces a pattern nobody wrote down. The
separation is not advice in a README, it is enforced by the types: `Profile`
holds `text`, `Facts` does not, and `screen.py` is handed `Facts`.

**A missing fact stays missing.** Every extraction may return UNKNOWN, and
UNKNOWN is a real answer that survives to the verdict. A model asked whether
a CV shows five years of experience will produce an estimate rather than
admit the document does not say, and the estimate will be drawn from what CVs
like this one usually contain -- which is the bias, arriving by a different
door.

Quotes are verified against the document exactly as posting quotes are
verified against the posting, by `intake.posting.normalise`. A fact that
cannot be found is discarded and counted.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from intake import hostile
from intake.posting import Rejected, normalise

#: What a fact is about. Deliberately coarse: a taxonomy fine enough to be
#: interesting is fine enough to disagree about, and the categories here only
#: exist so a screener can find the right facts, not to say anything.
KINDS = ("experience", "project", "skill", "education", "credential", "language", "other")

#: Whether a fact reports something that happened, or the candidate
#: describing themselves. This is the single most load-bearing field in the
#: file. A quote proves the CV says the words; it never proves the words are
#: true, and a CV that recites a posting's own requirements back at it
#: produces quotes that verify perfectly. See `personas/mara_velichko.md` and
#: the cap in `screen.py`.
EVIDENCE = ("instance", "assertion")


@dataclass
class Profile:
    """A CV as submitted. Holds the text, and is never handed to the scorer."""

    id: str
    name: str
    text: str
    source: str = ""
    submitted_at: str = ""

    @classmethod
    def from_file(cls, path: str | Path, *, id: str = "", name: str = "") -> "Profile":
        p = Path(path)
        if p.suffix.lower() == ".pdf":
            # Only what is drawn on the page. `intake.pdf.scan` reports what
            # was left out, and why, from the same read.
            from intake import pdf
            raw = pdf.visible_text(p.read_bytes())
        else:
            raw = p.read_text(encoding="utf-8", errors="replace")
        # What a reader would see off the page, and only that. `intake.hostile`
        # reports the same spans it removes here, so a document that was read
        # partially is a document whose owner can be told which part.
        raw = hostile.visible_text(raw)
        if p.suffix.lower() == ".tex":
            raw = strip_latex(raw)
        return cls(
            id=id or p.stem.lower().replace(" ", "_").replace("-", "_"),
            name=name or p.stem.replace("_", " "),
            text=raw.strip(),
            source=str(p.name),
            submitted_at=datetime.now(timezone.utc).isoformat(),
        )

    def contains(self, quote: str) -> bool:
        return bool(quote) and normalise(quote) in normalise(self.text)


def strip_latex(raw: str) -> str:
    """Enough LaTeX handling to read a CV, and no more.

    A real parser is the wrong tool: what is wanted is the sentences a human
    would read off the rendered page, so that a quote verified against this
    text is a quote a person could find in the PDF.
    """
    # Everything before \begin{document} is setup, never content.
    start = re.search(r"\\begin\{document\}", raw)
    if start:
        raw = raw[start.end():]
    raw = re.sub(r"(?m)(?<!\\)%.*$", "", raw)

    # Accents written as macros are letters, and a quote has to match the
    # rendered page rather than the source: Ab\'adi must read as Abadi.
    for macro, plain in (
        (r"\\'([aeiouAEIOUcnyC])", r"\1"), (r'\\"([aeiouAEIOUyY])', r"\1"),
        (r"\\`([aeiouAEIOU])", r"\1"), (r"\\\^([aeiouAEIOU])", r"\1"),
        (r"\\~([anoANO])", r"\1"), (r"\\c\{c\}", "c"), (r"\\&", "&"),
    ):
        raw = re.sub(macro, plain, raw)
    raw = re.sub(r"\\(?:usepackage|documentclass|definecolor|newcommand|renewcommand|geometry|hypersetup|includegraphics|vspace|hspace|setlength|titleformat|pagestyle|input|include)\b.*?(?:\n|$)", "", raw)
    raw = re.sub(r"\\begin\{[^}]*\}(\[[^\]]*\])?|\\end\{[^}]*\}", "\n", raw)
    raw = re.sub(r"\\(?:textbf|textit|emph|underline|texttt|textsc|large|Large|small|href\{[^}]*\})\s*\{", "{", raw)
    raw = re.sub(r"\\item\b", "\n- ", raw)
    raw = re.sub(r"\\\\|\\newline|\\par", "\n", raw)
    raw = re.sub(r"\\[a-zA-Z@]+\*?", " ", raw)
    raw = raw.replace("{", "").replace("}", "").replace("~", " ").replace("&", " ")
    raw = re.sub(r"[ \t]+", " ", raw)
    return re.sub(r"\n{3,}", "\n\n", raw)


@dataclass
class Fact:
    """One thing the document says, and where it says it."""

    id: str
    kind: str
    claim: str
    source_quote: str
    period: str = ""
    #: Conservative by default: anything not positively identified as an
    #: instance is treated as the candidate's own characterisation.
    evidence: str = "assertion"
    #: Which document this came from. A CV is written once and reused; a
    #: letter is written for this posting, which makes it more informative
    #: about intent and less trustworthy as evidence. `screen.py` caps what a
    #: criterion can earn when only the letter supports it.
    source: str = "cv"
    #: For letter facts only: what the sentence is doing. See
    #: `intake/letter.py`.
    role: str = ""

    @property
    def from_letter(self) -> bool:
        return self.source == "letter"

    @property
    def is_instance(self) -> bool:
        return self.evidence == "instance"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def render(self) -> str:
        when = f" ({self.period})" if self.period else ""
        #: The screener is told which document a fact came from, because the
        #: rules differ and a rule applied invisibly is a rule nobody can
        #: argue with.
        where = f" [from the letter, {self.role}]" if self.from_letter else ""
        return (f"[{self.id}] {self.kind}/{self.evidence}{when}{where}: {self.claim}\n"
                f"    document says: \"{self.source_quote}\"")


@dataclass
class Facts:
    """What a screener is allowed to see. No document text, by construction."""

    candidate_id: str
    name: str
    facts: list[Fact] = field(default_factory=list)
    rejected: list[Rejected] = field(default_factory=list)
    extracted_at: str = ""
    model: str = ""

    def by_id(self, fid: str) -> Fact | None:
        return next((f for f in self.facts if f.id == fid), None)

    def render(self) -> str:
        """The facts as the screener reads them, and only the facts.

        The name is not in here: `screen.screen` adds it above this block
        when it was asked not to anonymise, so that the one place a name can
        reach a model is the one place that decides to send it.
        """
        return "\n".join(f.render() for f in self.facts) or "(no facts extracted)"

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "name": self.name,
            "extracted_at": self.extracted_at,
            "model": self.model,
            "facts": [f.to_dict() for f in self.facts],
            "rejected": [r.to_dict() for r in self.rejected],
            "extraction_yield": {
                "kept": len(self.facts),
                "rejected": len(self.rejected),
                "rate": (round(len(self.facts) / (len(self.facts) + len(self.rejected)), 3)
                         if (self.facts or self.rejected) else None),
            },
        }


SYSTEM = """\
You read one CV and list what it states. You are not assessing the person,
comparing them to a role, or deciding whether anything is impressive.

For every fact you list, give `source_quote`: a span copied
character-for-character from the CV. It is checked against the document, and
a fact whose quote is not found is discarded. Do not paraphrase into the
quote and do not join separate lines into one.

Record only what the document says. If it does not give a duration, leave
`period` empty -- do not compute one. If a bullet claims an outcome without a
number, record the claim as written and do not supply a number. Seniority,
scope and quality that the document does not state are not yours to add.

Split compound bullets. "Built X and shipped Y to Z users" is at least two
facts, because a screener may need one without the other.

Classify every fact as `evidence`:

  instance   something that happened. A thing built, shipped, run or
             delivered; a named system, employer, client, tool or place; a
             number; a date; a stated outcome. "Built an internal tool with
             Claude Code that four people use."
  assertion  the candidate characterising themselves -- their qualities,
             habits, standards or style. "I move fast." "Extreme ownership."
             "Writing that doesn't need editing." "Genuinely AI-native."

The test is whether a reader could ask "when, and what exactly?" and find the
answer already in the sentence. A skills list is an assertion: naming a tool
is not an occasion of using it. A job title is an instance of employment and
an assertion about nothing else.

Classify what the sentence is, not whether you believe it.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "the person's name exactly as the CV writes it"},
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "short snake_case identifier, unique in this CV"},
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "claim": {"type": "string", "description": "what the document states, in plain words"},
                    "source_quote": {"type": "string", "description": "verbatim span copied from the CV"},
                    "period": {"type": "string", "description": "only if the document states one"},
                    "evidence": {"type": "string", "enum": list(EVIDENCE),
                                 "description": "instance = something that happened; assertion = the candidate describing themselves"},
                },
                "required": ["id", "kind", "claim", "source_quote", "evidence"],
            },
        },
    },
    "required": ["facts"],
}


def extract(client: Any, profile: Profile, *, model: str, max_tokens: int = 6000) -> Facts:
    raw = client.structured(
        model=model,
        system=SYSTEM,
        user=f"# CV\n\n{profile.text}",
        schema=SCHEMA,
        tool_name="list_what_the_cv_states",
        tool_description="List what this CV states, each item quoting the document.",
        max_tokens=max_tokens,
    )
    return verify(profile, raw.get("facts", []), name=raw.get("name", ""), model=model)


def verify(profile: Profile, items: list[dict[str, Any]], *, name: str = "", model: str = "") -> Facts:
    """Keep only what the document actually says. Pure, testable without a model."""
    out = Facts(
        candidate_id=profile.id,
        name=(name or profile.name).strip(),
        extracted_at=datetime.now(timezone.utc).isoformat(),
        model=model,
    )
    seen: set[str] = set()
    for item in items:
        fid = str(item.get("id", "")).strip()
        quote = str(item.get("source_quote", "")).strip()
        kind = str(item.get("kind", "other")).strip()

        if not fid:
            out.rejected.append(Rejected("(unnamed)", "no id", quote))
            continue
        if fid in seen:
            out.rejected.append(Rejected(fid, "duplicate id", quote))
            continue
        if kind not in KINDS:
            out.rejected.append(Rejected(fid, f"unknown kind {kind!r}", quote))
            continue
        if not profile.contains(quote):
            out.rejected.append(Rejected(fid, "source_quote does not appear in the CV", quote))
            continue

        # An unrecognised class is not a reason to drop a real fact, but it
        # must not silently become the permissive value.
        evidence = str(item.get("evidence", "")).strip()
        if evidence not in EVIDENCE:
            evidence = "assertion"

        seen.add(fid)
        out.facts.append(
            Fact(
                id=fid,
                kind=kind,
                claim=str(item.get("claim", "")).strip(),
                source_quote=quote,
                period=str(item.get("period", "")).strip(),
                evidence=evidence,
            )
        )
    return out


@dataclass
class Changes:
    """What is different about a person since they last applied.

    Somebody who applies twice is not two candidates, and re-scoring them
    from zero throws away the only interesting thing: what they went and did
    in between. Matching is on the quote rather than on the generated id,
    because ids are written fresh on every extraction and quotes are not.
    """

    added: list[Fact] = field(default_factory=list)
    gone: list[Fact] = field(default_factory=list)
    kept: list[Fact] = field(default_factory=list)

    @property
    def unchanged(self) -> bool:
        return not self.added and not self.gone

    def to_dict(self) -> dict[str, Any]:
        return {"added": [f.to_dict() for f in self.added],
                "gone": [f.to_dict() for f in self.gone],
                "kept": len(self.kept)}

    def render(self) -> str:
        if self.unchanged:
            return f"same document as last time ({len(self.kept)} facts, nothing new)"
        lines = [f"{len(self.added)} new, {len(self.gone)} no longer stated, "
                 f"{len(self.kept)} unchanged", ""]
        for f in self.added:
            lines.append(f"  + [{f.kind}/{f.evidence}] {f.claim[:88]}")
        for f in self.gone:
            lines.append(f"  - [{f.kind}/{f.evidence}] {f.claim[:88]}")
        return "\n".join(lines)


def changed(old: Facts, new: Facts) -> Changes:
    """Compare two extractions of the same person."""
    def key(f: Fact) -> str:
        return normalise(f.source_quote)

    before = {key(f): f for f in old.facts}
    after = {key(f): f for f in new.facts}
    return Changes(
        added=[f for k, f in after.items() if k not in before],
        gone=[f for k, f in before.items() if k not in after],
        kept=[f for k, f in after.items() if k in before],
    )


def save(facts: Facts, out_dir: str | Path) -> Path:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"facts_{facts.candidate_id}.json"
    p.write_text(json.dumps(facts.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return p


def load(path: str | Path) -> Facts:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    return Facts(
        candidate_id=d["candidate_id"],
        name=d.get("name", ""),
        facts=[Fact(**{k: v for k, v in f.items()
                       if k in Fact.__dataclass_fields__})
               for f in d.get("facts", [])],
        rejected=[Rejected(**r) for r in d.get("rejected", [])],
        extracted_at=d.get("extracted_at", ""),
        model=d.get("model", ""),
    )
