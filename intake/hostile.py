"""What a document says to the model but not to the reader.

At least 1% of resumes submitted to one large hiring platform carry hidden
instructions aimed at the screener -- white text on white, a zero-size font,
content pushed off the page, or a block buried in PDF metadata. That is
measured, on roughly 200,000 real applications, not hypothesised.

The finding that shapes this module is the second one: **more than 90% of
real attacks issue no instruction at all.** They are dense blocks of the
posting's own vocabulary, hidden from the reader, there to be matched rather
than obeyed. A detector that looks for "ignore all previous instructions"
misses nine attacks in ten.

So two classes are detected, and they are not the same problem:

  instruction   text addressed to a model. Rare, loud, easy to catch.
  stuffing      invisible vocabulary, no sentence around it. Common, quiet,
                and invisible to every detector built for the first class.

**Nothing here is scored, and nothing is silently deleted.** The extractor
already reads what a human would read off the rendered page, so hidden text
never reaches the screener in the first place -- this module's job is to say
what was there, so that a removal is a disclosure rather than a secret. A
candidate whose document was partly ignored is entitled to know which part,
and a company that rejects on this basis needs the span, not an accusation.

**Why the architecture, not this file, is the real defence.** An instruction
that survives into the visible text still has to become a *fact with a quote*
before it can touch a score, and a fact carries its span into the audit
trail. Stuffing that survives still has to be classified `instance` to earn
more than `weak`, and a keyword block is not an instance -- that is the cap
in `screen.py`, measured at 76% -> 24% on `personas/mara_velichko.md`. This
module adds sight, not safety.

PDF is the format most of these arrive in. `intake/pdf.py` reads one with
each character's render attributes -- render mode, size, position, colour and
what was painted behind it -- and `scan_file` below sends a PDF there and
everything else through `scan`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable

#: Characters that occupy no visual space. The tag block (U+E0000..U+E007F)
#: is the interesting one: it round-trips through most pipelines untouched
#: and renders as nothing at all.
INVISIBLE_CHARS = re.compile(
    "[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u206a-\u206f\ufeff\U000e0000-\U000e007f]"
)

#: The style declarations that make an element unreadable. Shared by the
#: scanner, which reports them, and by `visible_text`, which removes what
#: they wrap -- one list, so a mechanism can never be reported without being
#: removed or removed without being reported.
HIDING_STYLE = (r"color\s*:\s*(?:#fff(?:fff)?|white|rgb\(\s*255\s*,\s*255\s*,\s*255\s*\))"
                r"|font-size\s*:\s*0(?:\.0+)?(?:px|pt|em|%)?"
                r"|display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0(?:\.0+)?\b")

#: The whole element: opening tag, the text it hides, and its close.
HIDDEN_ELEMENT = re.compile(
    rf"<(span|div|p|font|section)\b[^>]*(?:style\s*=\s*[\"'][^\"']*(?:{HIDING_STYLE})[^\"']*[\"']"
    rf"|color\s*=\s*[\"']?#?(?:fff(?:fff)?|white)[\"']?)[^>]*>(.*?)</\1\s*>",
    re.I | re.S)

#: LaTeX commands whose braced argument disappears from the page.
HIDDEN_LATEX = re.compile(r"\\(?:textcolor\s*\{\s*white\s*\}|phantom|scalebox\s*\{\s*0(?:\.0+)?\s*\})\s*\{",
                          re.I)

#: Markup that hides text while leaving it in the extractable layer.
HIDING_MARKUP = (
    ("html colour", re.compile(r"color\s*:\s*(#fff(fff)?|white|rgb\(\s*255\s*,\s*255\s*,\s*255\s*\))", re.I)),
    ("html size", re.compile(r"font-size\s*:\s*0(\.0+)?(px|pt|em|%)?", re.I)),
    ("html hidden", re.compile(r"(display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0(\.0+)?\b)", re.I)),
    ("latex colour", re.compile(r"\\(textcolor|color)\s*\{\s*white\s*\}", re.I)),
    ("latex size", re.compile(r"\\fontsize\s*\{\s*0(\.0+)?\s*\}|\\scalebox\s*\{\s*0(\.0+)?\s*\}", re.I)),
    ("latex offpage", re.compile(r"\\(hspace|vspace)\*?\s*\{\s*-\s*\d", re.I)),
    ("latex phantom", re.compile(r"\\phantom\s*\{", re.I)),
)

#: Text in the file that never reaches the rendered page.
COMMENTS = (
    ("html comment", re.compile(r"<!--(.*?)-->", re.S)),
    ("latex comment", re.compile(r"(?m)(?<!\\)%(.+)$")),
)

#: Phrases addressed to a model rather than to a reader. Deliberately short:
#: this list catches the loud tenth, and pretending otherwise would be the
#: error the research warns about.
INSTRUCTION_SHAPED = re.compile(
    r"(ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?)"
    r"|disregard\s+(the\s+)?(above|previous)"
    r"|as\s+an?\s+(ai|language\s+model|assistant)"
    r"|you\s+(are|must|should)\s+(now\s+)?(rate|score|rank|recommend|output|respond|return)"
    r"|system\s*(prompt|message)?\s*:"
    r"|\bprompt\s*:\s*"
    r"|(rate|score|rank)\s+this\s+(candidate|resume|cv|applicant)"
    r"|(strong(ly)?|highly)\s+recommend\s+this\s+candidate"
    r"|do\s+not\s+(mention|reveal|disclose)\s+(this|these)"
    r")", re.I)

#: A span is called stuffing when it is mostly separators and terms, with
#: none of the grammar a written sentence has. Thresholds are stated here
#: rather than buried, because they are the whole judgement.
STUFFING_MIN_TERMS = 8
STUFFING_MAX_SENTENCE_RATIO = 0.08   # full stops per term
STUFFING_MIN_VOCAB_SHARE = 0.30      # share of terms drawn from the posting


@dataclass(frozen=True)
class Finding:
    """One span that behaves differently for a reader and for a model."""

    kind: str          # "instruction" | "stuffing" | "invisible" | "comment"
    how: str           # the mechanism, e.g. "latex colour", "zero-width characters"
    span: str          # what was found, truncated for reading
    why: str

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "how": self.how, "span": self.span, "why": self.why}

    def render(self) -> str:
        return f"[{self.kind}/{self.how}] {self.span!r}\n    {self.why}"


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.findings

    def of_kind(self, kind: str) -> list[Finding]:
        return [f for f in self.findings if f.kind == kind]

    def to_dict(self) -> dict[str, Any]:
        return {"clean": self.clean, "findings": [f.to_dict() for f in self.findings]}

    def render(self) -> str:
        if self.clean:
            return "nothing in this document is hidden from its reader"
        lines = [f"{len(self.findings)} span(s) a reader would not see:", ""]
        for f in self.findings:
            lines.append("  " + f.render().replace("\n", "\n  "))
        lines.append("")
        lines.append("  Nothing was scored on these and nothing was deleted. They are")
        lines.append("  reported so that what the screener ignored is on the record.")
        return "\n".join(lines)


def _clip(s: str, n: int = 90) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _terms(text: str) -> list[str]:
    return re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'+#.-]{1,}", text)


def looks_like_stuffing(text: str, vocabulary: Iterable[str] = ()) -> bool:
    """A block of terms with no sentence around it.

    Two signals together, because either alone is wrong: a skills list is
    legitimately term-dense, and a long sentence can legitimately repeat the
    posting. What is not legitimate is both at once -- many terms, drawn from
    the posting, with no grammar holding them.
    """
    terms = _terms(text)
    if len(terms) < STUFFING_MIN_TERMS:
        return False
    stops = text.count(".") + text.count("!") + text.count("?")
    if stops / len(terms) > STUFFING_MAX_SENTENCE_RATIO:
        return False
    vocab = {v.lower() for v in vocabulary}
    if not vocab:
        #: With no posting to compare against, density alone is not enough to
        #: accuse anyone -- a skills line would be flagged every time.
        return False
    share = sum(1 for t in terms if t.lower() in vocab) / len(terms)
    return share >= STUFFING_MIN_VOCAB_SHARE


def agenda_vocabulary(agenda: Any) -> set[str]:
    """The posting's own words, which are what stuffing is made of.

    Taken from the criteria the posting produced, so the comparison is
    against this employer's language rather than a generic keyword list.
    """
    words: set[str] = set()
    for c in getattr(agenda, "criteria", []):
        for field_name in ("question", "source_quote", "looks_like"):
            for t in _terms(str(getattr(c, field_name, "") or "")):
                if len(t) > 3:
                    words.add(t.lower())
    return words


def scan(raw: str, *, vocabulary: Iterable[str] = ()) -> Report:
    """Everything in this document that a reader would not see."""
    report = Report()
    vocabulary = set(vocabulary)

    hidden_spans: list[str] = []

    if INVISIBLE_CHARS.search(raw):
        found = INVISIBLE_CHARS.findall(raw)
        names = sorted({unicodedata.name(c, f"U+{ord(c):04X}") for c in found})
        report.findings.append(Finding(
            kind="invisible", how="zero-width characters",
            span=f"{len(found)} occurrence(s): {', '.join(names[:3])}",
            why="characters that render as nothing; a reader cannot see them and an "
                "extractor can",
        ))

    #: One hidden region, one finding. White text at zero size trips two
    #: patterns and is one attack; a reader who is shown it twice learns to
    #: skim the report, which is the opposite of the point.
    claimed: list[tuple[int, int]] = []

    def already(i: int) -> bool:
        return any(a <= i < b for a, b in claimed)

    for m in HIDDEN_ELEMENT.finditer(raw):
        body = m.group(2) or ""
        claimed.append((m.start(), m.end()))
        hidden_spans.append(body)
        report.findings.append(Finding(
            kind="invisible", how="hidden element", span=_clip(body),
            why="an element styled so a reader cannot see it, carrying text an "
                "extractor can read",
        ))

    for how, pattern in HIDING_MARKUP:
        for m in pattern.finditer(raw):
            if already(m.start()):
                continue
            tail = raw[m.end(): m.end() + 200]
            claimed.append((m.start(), m.end() + 200))
            hidden_spans.append(tail)
            report.findings.append(Finding(
                kind="invisible", how=how, span=_clip(m.group(0) + " " + tail),
                why="markup that hides the text that follows it while leaving it "
                    "in the extractable layer",
            ))

    for how, pattern in COMMENTS:
        for m in pattern.finditer(raw):
            body = (m.group(1) or "").strip()
            if len(body) < 12:
                continue
            hidden_spans.append(body)
            report.findings.append(Finding(
                kind="comment", how=how, span=_clip(body),
                why="present in the file, absent from the rendered page",
            ))

    #: One span, one finding. A sentence that trips two patterns -- "ignore
    #: previous instructions" and "rate this candidate" in the same breath --
    #: is one attack, and reporting it twice trains a reader to skim.
    reported_to = -1
    for m in INSTRUCTION_SHAPED.finditer(raw):
        if m.start() <= reported_to:
            continue
        line = raw[max(0, m.start() - 60): m.end() + 120]
        reported_to = m.end() + 120
        report.findings.append(Finding(
            kind="instruction", how="addressed to a model", span=_clip(line),
            why="phrasing aimed at the system rather than at a reader",
        ))

    #: Stuffing is only called on spans that were hidden. Dense vocabulary in
    #: plain sight is a CV written badly, or written to a posting, and that is
    #: the screener's problem to price -- not a security finding.
    seen_stuffing: set[str] = set()
    for span in hidden_spans:
        key = " ".join(sorted(set(_terms(span))))[:200]
        if key in seen_stuffing:
            continue
        if looks_like_stuffing(span, vocabulary):
            seen_stuffing.add(key)
            report.findings.append(Finding(
                kind="stuffing", how="hidden vocabulary block", span=_clip(span),
                why="a dense block of the posting's own terms, hidden from the reader, "
                    "with no sentence around it -- the shape of over 90% of real attacks",
            ))

    return report


def visible_text(raw: str) -> str:
    """The document with what a reader cannot see removed.

    Used to feed extraction. The removals are not silent: `scan` reports the
    same spans, and both are meant to be run on the same document.
    """
    out = HIDDEN_ELEMENT.sub(" ", raw)
    out = _drop_braced(out, HIDDEN_LATEX)
    out = INVISIBLE_CHARS.sub("", out)
    for _, pattern in COMMENTS:
        out = pattern.sub("", out)
    return out


def _drop_braced(text: str, opener: re.Pattern[str]) -> str:
    """Remove a LaTeX command and the braced group it hides, nesting included."""
    while True:
        m = opener.search(text)
        if not m:
            return text
        depth, i = 1, m.end()
        while i < len(text) and depth:
            if text[i] == "{" and text[i - 1] != "\\":
                depth += 1
            elif text[i] == "}" and text[i - 1] != "\\":
                depth -= 1
            i += 1
        text = text[: m.start()] + " " + text[i:]


def scan_file(path: Any, *, vocabulary: Iterable[str] = ()) -> Report:
    """`scan` for any document the intake reads, PDF included."""
    from pathlib import Path
    p = Path(path)
    if p.suffix.lower() == ".pdf":
        from intake import pdf
        return pdf.scan(p.read_bytes(), vocabulary=vocabulary)
    return scan(p.read_text(encoding="utf-8", errors="replace"), vocabulary=vocabulary)
