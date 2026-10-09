"""The percentage beside a name, counted from the words on the page.

No model is called. The number is arithmetic over the CV and the letter, and
every point of it can be traced to something written there:

  keywords     what the posting asks for, criterion by criterion: its words
               found in the documents, each counted by how specific it is to
               this role (a word every posting uses earns nothing), weighted
               by the criterion's weight. A requirement the posting itself
               calls hard, with nothing found for it, caps the total at half.
  motivation   what the answer to "Why us" does: it exists, it speaks of the company's
               subject, it says what they would want to own, it says what
               they made.
  proof        things that can be checked: a link to something built,
               sentences that say they made something, figures.
  ai           AI in their work: a tool named, and a tool named in a sentence
               where something was made with it.

Repeating a word earns nothing: a keyword counts once, and counts of
sentences and figures saturate (each one closes half of what is left). That
is what keeps a document stuffed with the posting's words from running away
with the list. It stays a count of words, not a judgement of a person: it
orders the list, and the votes decide.
"""

from __future__ import annotations

import functools
import re
import tomllib
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import signals

ROOT = Path(__file__).resolve().parent

#: What each part weighs in the total. They add up to 1.
#: What the posting asks for comes first: the number is about this role. The
#: form asks one short question ("Why us?"), not a cover letter, so it weighs
#: least. A role with nothing to compare to (an open application) leaves the
#: first part out, and the three others carry the total between them.
WEIGHTS = {"keywords": 0.55, "proof": 0.20, "ai": 0.15, "motivation": 0.10}
#: Roles read without a posting to compare to (desk.toml [match] open_roles).
OPEN_ROLES_DEFAULT = ("open_application",)
#: In a short answer, the sentence that ties the wish to their own work: the
#: first person, and something done -- "through my own work: building and shipping".
OWN_WORK = re.compile(
    r"\b(?:i|i've|i'm|my|we|we've)\b[^.!?]*\b(?:built|building|build|shipped|shipping|ship|"
    r"launched|launching|made|making|created|creating|wrote|writing|ran|running|led|leading|"
    r"automated|automating|deployed|deploying|designed|designing)\b", re.I)

WORD = re.compile(r"[a-z][a-z0-9+#/-]{2,}")
#: A figure that measures something: a percentage, an amount, a multiple, a count of things.
FIGURE = re.compile(
    r"\d[\d.,]*\s?(?:%|k\b|m\b|bn\b|x\b|€|\$|£)|[€$£]\s?\d|"
    r"\b\d[\d.,]*\+?\s(?:people|clients|customers|users|companies|countries|entities|agents|"
    r"teams?|projects|deals|months|weeks|hours|invoices|markets)\b", re.I)
#: Words that carry no meaning of their own, and the words every question is asked with.
STOP = frozenset("""
the and for with that this from have has had are was were been being not but you your they them
their there here what when where which who whom how why than then into onto over under about
would could should will can may might must shall does did doing done one two three any all some
more most less least very much many few each every other another same such only also just even
tell describe give example case time thing things something someone anyone person people
candidate candidates role roles work worked working job jobs years year long come comes came
show shows shown look looks like kind sort both either neither while during before after
whether without within across through between among against toward towards because since
our out own its it's i'm i've we've don't isn't
experience experienced strong deep depth knowledge skill skills production genuinely prior
similar primary requirement required requirements ideally preferred plus bonus nice ability
able high level top tier solid proven good great real use used using particularly especially
""".split())


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def _stem(w: str) -> str:
    """A crude stem: enough for "shipped" to meet "ship" and "agents" to meet "agent"."""
    for end in ("ations", "ation", "ancy", "ants", "ant", "ings", "ing", "ers", "er", "ed",
                "es", "s"):
        if w.endswith(end) and len(w) - len(end) >= 4:
            return w[:-len(end)]
    return w


def words(text: str) -> set[str]:
    return {_stem(w) for w in WORD.findall(_fold(text)) if w not in STOP}


def _as_written(text: str) -> dict[str, str]:
    """Each stem, with the word as the document writes it: what the page shows."""
    out: dict[str, str] = {}
    for w in WORD.findall(_fold(text)):
        if w not in STOP:
            out.setdefault(_stem(w), w)
    return out


def _half(n: float) -> float:
    """Each one closes half of what is left: 1 -> 0.5, 2 -> 0.75, 3 -> 0.875."""
    return 1 - 0.5 ** n


@dataclass(frozen=True)
class Criterion:
    id: str
    weight: float
    keywords: frozenset[str]
    #: The posting itself calls it a requirement ("This is a hard requirement").
    hard: bool = False
    #: The years of experience the posting asks for in it ("5+ years"), or 0.
    years: float = 0.0


@dataclass(frozen=True)
class Posting:
    company: frozenset[str]
    subject: frozenset[str]
    criteria: tuple[Criterion, ...]


#: "3+ years", "3-4 years", "3–4 years", "five years": the least the posting asks.
_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
        "eight": 8, "nine": 9, "ten": 10}
_ASKED = re.compile(r"\b(\d{1,2}|" + "|".join(_NUM) + r")\s*(?:\+|plus|or more)?\s*"
                    r"(?:(?:-|–|—|to)\s*\d{1,2}\s*)?\+?\s*years?\b", re.I)


def asked_years(quote: str) -> float:
    """The years of experience a posting's sentence asks for, or 0 when it asks none."""
    m = _ASKED.search(quote or "")
    if not m:
        return 0.0
    n = m.group(1).lower()
    return float(_NUM.get(n, n))


_MONTH = (r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|janv|févr|fevr|mars|avr|"
          r"mai|juin|juil|août|aout|déc|ene|abr|ago|dic)[a-zé]*\.?")
#: A period of work: "2019 - 2023", "Jan 2021 – present", "03/2018 – 06/2020".
_PERIOD = re.compile(
    r"(?:" + _MONTH + r"\s+|\d{1,2}/)?((?:19|20)\d\d)\s*(?:-|–|—|to|until|à|a)\s*"
    r"(?:(?:" + _MONTH + r"\s+|\d{1,2}/)?((?:19|20)\d\d)|(present|current|now|today|date|"
    r"aujourd'hui|actuel|presente|actualidad))", re.I)
#: "4 years in consulting", "6 years of experience as an engineer".
_STATED = re.compile(r"\b(\d{1,2}|" + "|".join(_NUM) + r")\+?\s*(?:years?|yrs?|ans|años)\b",
                     re.I)


def periods(text: str, this_year: int) -> list[tuple[float, float, str]]:
    """Each period of work in a CV: (start, end, the lines it sits in)."""
    lines = text.splitlines()
    out = []
    for i, line in enumerate(lines):
        for m in _PERIOD.finditer(line):
            start = float(m.group(1))
            end = float(m.group(2)) if m.group(2) else float(this_year)
            if end < start:
                continue
            # The job's title is often the line above, its description below,
            # down to a blank line or the next dated job.
            below = []
            for nxt in lines[i + 1:i + 6]:
                if not nxt.strip() or _PERIOD.search(nxt):
                    break
                below.append(nxt)
            above = lines[i - 1] if i and not _PERIOD.search(lines[i - 1]) else ""
            window = " ".join([above, line, *below])
            out.append((start, max(end, start + 0.5), window))
    return out


def years_in(text: str, field: frozenset[str], this_year: int) -> float | None:
    """Years of work in a field the CV shows: dated periods, or "N years in ...".

    None when the CV dates nothing at all: unknown is not zero.
    """
    spans = []
    dated = False
    for start, end, window in periods(text, this_year):
        dated = True
        if field & words(window):
            spans.append((start, end))
    stated = 0.0
    for sentence in re.split(r"(?<=[.!?\n])\s+", text):
        for m in _STATED.finditer(sentence):
            dated = True
            if field & words(sentence):
                n = m.group(1).lower()
                stated = max(stated, float(_NUM.get(n, n)))
    if not dated:
        return None
    # Overlapping periods count once.
    total, last = 0.0, -1e9
    for start, end in sorted(spans):
        start = max(start, last)
        if end > start:
            total += end - start
            last = end
    return max(total, stated)


def _quotes(path: str) -> dict[str, str]:
    """Each criterion's words as the posting wrote them (the provenance file beside it)."""
    import json
    f = Path(path).with_suffix(".provenance.json")
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {c.get("id", ""): c.get("source_quote", "") for c in d.get("criteria", [])}


@functools.lru_cache(maxsize=64)
def _posting(path: str, _mtime: int) -> Posting:
    d = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    role = d.get("role", {})
    quotes = _quotes(path)

    def asked(c: dict) -> frozenset[str]:
        # What the posting itself says (its quote) and the criterion's name: not
        # the question written about it, whose filler words are in every posting.
        # A hard requirement is also met by the words of its question: a gate
        # must not close on a phrasing.
        base = quotes.get(c["id"], "") + " " + c["id"].replace("_", " ")
        if not quotes.get(c["id"]) or c.get("hard"):
            base += " " + c.get("question", "") + " " + c.get("looks_like", "")
        return frozenset(words(base))

    crit = tuple(Criterion(c["id"], float(c.get("weight", 1)), asked(c), bool(c.get("hard", False)),
                           asked_years(quotes.get(c["id"], "")))
                 for c in d.get("criteria", []))
    return Posting(frozenset(words(role.get("company", ""))),
                   frozenset(words(role.get("summary", "") + " " + role.get("title", ""))), crit)


#: A requirement declared hard and not found caps the total here.
HARD_CAP = 0.5
#: The share of what a criterion asks for that meets it in full.
COVER = 1 / 3


@functools.lru_cache(maxsize=8)
def _spread(_key: str) -> dict[str, int]:
    """In how many postings on file each criterion word appears."""
    seen: dict[str, int] = {}
    for f in (ROOT / "mandates" / "generated").glob("role_*.toml"):
        post = posting(f.stem[len("role_"):])
        if post is None:
            continue
        for w in set().union(*(c.keywords for c in post.criteria)) if post.criteria else ():
            seen[w] = seen.get(w, 0) + 1
    return seen


#: The postings on file, looked at again at most this often (seconds): a word is
#: asked about thousands of times for one page, the folder changes once a month.
_LOOK_EVERY = 5.0
_seen: dict[str, object] = {"at": -1e9, "key": "", "n": 0}


def _postings_now() -> tuple[str, int]:
    import time
    now = time.monotonic()
    if now - float(_seen["at"]) > _LOOK_EVERY:
        files = sorted((ROOT / "mandates" / "generated").glob("role_*.toml"))
        _seen.update(at=now, n=len(files),
                     key="|".join(f"{f.name}:{f.stat().st_mtime_ns}" for f in files))
    return str(_seen["key"]), int(_seen["n"])


def specific(word: str) -> float:
    """How much a word says about one role: 1 if no other posting asks it, 0 if all do."""
    key, n = _postings_now()
    if n < 2:
        return 1.0
    k = _spread(key).get(word, 1)
    return max(0.0, (n - k) / (n - 1))


def posting(posting_id: str) -> Posting | None:
    f = ROOT / "mandates" / "generated" / f"role_{posting_id}.toml"
    try:
        return _posting(str(f), f.stat().st_mtime_ns)
    except (OSError, tomllib.TOMLDecodeError, KeyError):
        return None


@dataclass
class Match:
    """The total, the four parts, and what each rests on."""
    parts: dict[str, float] = field(default_factory=dict)
    #: What was found, in words a partner can check against the documents.
    found: dict[str, list[str]] = field(default_factory=dict)
    #: At most this, when a hard requirement of the posting finds nothing.
    cap: float | None = None

    @property
    def total(self) -> float:
        w = sum(WEIGHTS[k] for k in self.parts)
        t = round(sum(WEIGHTS[k] * v for k, v in self.parts.items()) / w, 4) if w else 0.0
        return min(t, self.cap) if self.cap is not None else t


def count(cv: str, letter: str, links: list[str], post: Posting | None) -> Match:
    """The arithmetic, on text. Pure: the same documents give the same number."""
    m = Match()
    both = cv + "\n" + letter
    have = _as_written(both)

    # 1. The posting's words. A posting with no criteria on file leaves this
    # part out, and the three others carry the total between them.
    if post is not None and post.criteria:
        earned = total = 0.0
        hits: list[str] = []
        missing: list[str] = []
        years_said: list[str] = []
        from datetime import date
        this_year = date.today().year
        for c in post.criteria:
            # A word every posting asks for ("code", "systems") says nothing about
            # this role: each word found counts by how specific it is to it.
            got = sorted(g for g in c.keywords & have.keys() if specific(g) > 0)
            asked = sum(specific(g) for g in c.keywords)
            found = sum(specific(g) for g in got)
            # Nobody writes a CV in the posting's vocabulary, so a criterion is met
            # in full once a third of what it asks for is found ("consulting,
            # banking, VC/PE, startup": one of them is plenty). One stray word in
            # a criterion about something else ("event" for PostgreSQL) is not.
            share = min(1.0, found / (COVER * asked)) if asked else 0.0
            # What only this role asks for (Python, PostgreSQL; consulting,
            # banking) is the heart of the criterion: when the posting names such
            # a thing and none of it is in the documents, the words around it
            # ("design", "system") do not meet the criterion.
            own = {g for g in c.keywords if specific(g) == 1.0}
            if own and not own & have.keys():
                share = 0.0
            if c.years and share:
                # The years the posting asks for, in the field it names: asked 8,
                # shown 1, the criterion is worth an eighth. No date anywhere in
                # the CV: unknown, held at half, and said.
                field = frozenset(g for g in c.keywords if specific(g) >= 0.5)
                shown = years_in(cv, field, this_year)
                label = c.id.replace("_", " ")
                if shown is None:
                    share = min(share, 0.5)
                    years_said.append(f"{label}: asks {c.years:g}+ years, none stated")
                else:
                    share = min(share, shown / c.years)
                    years_said.append(f"{label}: asks {c.years:g}+ years, about "
                                      f"{shown:g} shown")
            earned += c.weight * share
            total += c.weight
            hits += [have[g] for g in got]
            if c.hard and not got:
                missing.append(c.id.replace("_", " "))
        m.parts["keywords"] = earned / total if total else 0.0
        m.found["keywords"] = sorted(set(hits))
        if years_said:
            m.found["years"] = years_said
        if missing:
            m.cap = HARD_CAP
            m.found["missing"] = [f"a requirement the posting calls hard: {x}" for x in missing]

    # 2. Motivation: four things a short answer to "Why us?" does, a quarter each.
    seen = words(letter)
    said: list[str] = []
    if letter.strip():
        said.append("an answer to “Why us”")
        subject = (post.company | post.subject) if post is not None else frozenset()
        if (post is not None and post.company and post.company <= seen) \
                or len(subject & seen) >= 5:
            said.append("speaks of the company's subject")
        if signals.wants_in(letter, limit=1):
            said.append("says what they would want to own")
        if OWN_WORK.search(letter):
            said.append("ties it to their own work")
    m.parts["motivation"] = len(said) / 4
    m.found["motivation"] = said

    # 3. Proof: what can be opened or checked.
    opened = [l for l in signals.links_in([cv, letter], links) if l.kind in ("code", "site")]
    made = [s for s in signals.sentences(both) if signals.SAYS_MADE.search(s)]
    figures = FIGURE.findall(both)
    m.parts["proof"] = (0.4 * bool(opened) + 0.3 * _half(len(made))
                        + 0.3 * _half(len(figures) / 2))
    m.found["proof"] = ([f"{len(opened)} link{'s' if len(opened) != 1 else ''} to something built"]
                        if opened else []) + (
        [f"{len(made)} sentence{'s' if len(made) != 1 else ''} about something made"]
        if made else []) + ([f"{len(figures)} figure{'s' if len(figures) != 1 else ''}"]
                            if figures else [])

    # 4. AI in their work: a tool named, and a tool named where something was made.
    tools = sorted({t.group(0).lower() for t in signals.AI_TOOL.finditer(both)})
    used = any(signals.AI_TOOL.search(s) and signals.MADE.search(s)
               for s in signals.sentences(both))
    m.parts["ai"] = 0.5 * bool(tools) + 0.5 * used
    m.found["ai"] = tools + (["used to make something"] if used else [])
    return m


def of(person: Any, posting_id: str) -> Match | None:
    """The match for one application, from the documents on file. None without any."""
    from desk import _files_dir
    texts: dict[str, str] = {}
    for d in person.documents_for(posting_id):
        texts[d.kind] = signals._text(_files_dir() / d.stored) or texts.get(d.kind, "")
    cv = texts.get("cv", "")
    letter = "\n".join(t for k, t in texts.items() if k != "cv")
    if not (cv.strip() or letter.strip()):
        return None
    post = None if posting_id in open_roles() else posting(posting_id)
    return count(cv, letter, list(person.all_links), post)


def open_roles() -> tuple[str, ...]:
    """Roles with no posting to compare to: an open application."""
    f = ROOT / "desk.toml"
    try:
        raw = tomllib.loads(f.read_text(encoding="utf-8")).get("match", {})
    except (OSError, tomllib.TOMLDecodeError):
        return OPEN_ROLES_DEFAULT
    return tuple(raw.get("open_roles", OPEN_ROLES_DEFAULT))
