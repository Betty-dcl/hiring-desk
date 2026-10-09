"""What a partner looks at before a trial day: what they built, what they want.

The people this desk was built for say it plainly: "we couldn't care less
what their resume says they've solved before". Every hire does a paid trial
day, and the bet is placed on "someone who builds without us asking". So the
lines under a name are not a summary of the CV. They are:

  built        what there is to open -- a repository, a demo, a thing made.
               Failing that, the sentence where they say they built
               something; failing that, "no link given".
  ai           AI in their work, the one hard requirement, at the level the
               paper reaches -- described with an occasion, in their words,
               only claimed, or silent -- and the sentence it rests on.
               Never "shown": only the trial day shows it.
  wants        what they would want to own. The open door asks for exactly
               that, and the trial task is chosen from it.
  role         their current or last role, as the CV names it, marked "check
               it". Never a number of years: that is a proxy for age.

The one rule: the desk never rephrases a person. Every line is a link, or one
sentence copied out of their own document with the document named ("from
letter", "from CV"), or "not found -- open the letter". A line that reads
"not said" when they said it in other words is a misreading a partner acts
on; so is a model's summary ("Candidate describes self as an AI-native
operator") printed in quotation marks as if she had written it. Both used to
happen. `verbatim` is the gate every sentence passes on its way to the card:
whatever produced it -- the records on file, the patterns below, a later
extraction -- a sentence that is not in the document is not shown.

Where a sentence comes from, in order:

  1. the records written when the application arrived (`runs/facts/`: facts
     quoted from the CV and the letter, each quote verified at the time; the
     screening that cites them; `runs/wants/` if that extraction was run).
     The quote is widened to the whole sentence around it, in the document.
  2. a search of the letter, then the CV, with no model: a link, a sentence
     naming a tool, a sentence in WANTS.
  3. not found.

No model is called. Nothing here is a judgement a model formed about a
person.
"""

from __future__ import annotations

import functools
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent

#: Where the code lives. Anything else with an https:// address is "a site".
CODE_HOSTS = ("github.com", "gitlab.com", "huggingface.co", "bitbucket.org", "codeberg.org")
PROFILE_HOSTS = ("linkedin.com",)

URL = re.compile(r"https://[^\s<>()\[\]\"'`]+")

#: A sentence that says what they would take on, in English, French, Spanish
#: or German. Each alternative needs the first person *and* a wish -- a
#: conditional, a "want", a goal -- so that a sentence that merely mentions
#: ownership ("I owned the close") stays a past fact, and "customers want to
#: own their data" stays someone else's wish. The adverb slot is a closed
#: list on purpose: an open one lets "I'd never want to" through.
_ADV = r"(?:(?:really|most|also|much|very much|genuinely|especially|particularly|ideally|then|first)\s+)?"
_DO = (r"(?:own|take|build|run|lead|drive|focus|go after|fix|set up|tackle|"
       r"automate|pick up|work on)")
WANTS = re.compile("|".join([
    # English
    rf"\bi(?:'d| would) {_ADV}(?:want|like|love|be keen|be glad|be happy) to\b",
    rf"\bi {_ADV}want to\b",
    rf"\bi(?:'m| am) {_ADV}(?:keen|eager|hoping|looking|aiming) to\b",
    rf"\bi(?:'m| am) {_ADV}(?:excited|interested) (?:about|in|by) "
    rf"(?:owning|building|taking|running|leading|driving|the chance to|the opportunity to)\b",
    r"\bi (?:hope|aim) to\b",
    rf"\bi(?:'d| would) {_ADV}{_DO}\b",
    r"\bmy (?:goal|aim|ambition) (?:here |in this role )?(?:is|would be)\b",
    r"\bwhat (?:i'd|i would) (?:\w+ )?(?:own|want|like|love|do|take|build)\b",
    r"\bwhat (?:excites|interests) me (?:most |the most )?(?:is|would be)\b",
    rf"\bwhere i (?:can|could) {_DO}\b",
    r"\bi see myself (?:owning|building|running|leading|taking)\b",
    r"\bmake a dent\b|\bi'm after\b|\bhand me\b|\bgive me\b",
    # French
    r"\bj'(?:aimerais|adorerais|ai envie de)\b",
    r"\bje (?:voudrais|souhaite|souhaiterais|veux|serais ravie? de)\b",
    r"\bmon (?:objectif|ambition|souhait) (?:est|serait)\b",
    r"\bce qui m'intéresse(?: le plus)?,? c'est\b",
    # Spanish
    r"\bme (?:gustaría|encantaría)\b",
    r"\b(?:quiero|quisiera|deseo|busco)\b",
    r"\bmi (?:objetivo|meta|ambición) (?:es|sería)\b",
    r"\blo que (?:más )?me interesa(?: más)? es\b",
    # German
    r"\bich (?:möchte|würde gerne?|will)\b",
    r"\bmein ziel (?:ist|wäre)\b",
]), re.I)
#: What a wish-shaped phrase is followed by when it is politeness or
#: framing, not something to own: "I'd like to thank you", "je souhaite
#: postuler", "quiero que sepan...". Checked right after each phrase, so in
#: "I'd like to thank you, and I'd like to own the close" the second counts.
POLITE = re.compile(
    r"\s*(?:to |de |d'|me )?(?:thank|apply|hear|meet|chat|talk|discuss|introduce|express|"
    r"share|mention|say|highlight|point out|stress|emphasi[sz]e|note|add|learn more|"
    r"know|be honest|be clear|be transparent|be upfront|take this opportunity|"
    r"join you|be considered|a call|an interview|feedback|vous|bien\b|remercier|postuler|"
    r"rencontrer|échanger|souligner|préciser|dire|agradec|postular|presentar|conocer|"
    r"destacar|señalar|mencionar|decir|danken|bewerben|vorstellen|kennenlernen|"
    r"that\b|que\b)", re.I)
#: A wish-shaped sentence about the past: "previously I'd take on the close
#: every month" is a habit they had, not one they want.
PAST = re.compile(r"\b(?:previously|used to|back then|at the time|last year|when i was|"
                  r"would have|i'd have|auparavant|antes|früher)\b", re.I)

#: A tool named, not a quality claimed: "I use Claude Code daily" can be
#: checked on the day; "I am genuinely AI-native" cannot, and is not matched.
AI_TOOL = re.compile(
    r"\b(claude code|claude|codex|cursor|copilot|chatgpt|gpt-?\d\w*|llms?|"
    r"coding agents?|ai agents?|langchain|langgraph|n8n|lovable|replit|windsurf|"
    r"vibe-?cod\w*)\b", re.I)
FIRST_PERSON = re.compile(r"\b(I|I'm|I've|I'd|we|we've)\b")
SENTENCE = re.compile(r"(?<=[.!?])\s+|\n\s*[-*•]\s+|\n{2,}")
#: Making something, said in the past: the verb the line "built" rests on.
MADE = re.compile(r"\b(built|builds|shipped|launched|coded|automated|deployed|rebuilt|"
                  r"created|developed|designed|wrote)\b", re.I)
#: In their documents, the sentence that says they made something: a bullet
#: that starts with the verb, or "I"/"we" with the verb close behind.
SAYS_MADE = re.compile(r"^(?:built|shipped|launched|coded|automated|deployed|rebuilt|created|"
                       r"developed|designed)\b|\b(?:i|we)\s+(?:\w+\s+){0,2}?(?:built|shipped|"
                       r"launched|coded|automated|deployed|rebuilt|created|developed|designed)\b",
                       re.I)
#: Not a role, and close to what may not be asked: a career break, leave,
#: care for a family member. A card that put "Full-time carer" under a name
#: would print a protected circumstance as the first thing a partner reads.
NOT_A_ROLE = re.compile(r"career break|sabbatical|parental|maternity|paternity|leave|"
                        r"carer|caregiv|care for|gap|unemploy|congé|excedencia|elternzeit|"
                        r"cuidad", re.I)
#: A year or a count of years. The role is shown without either: years of
#: experience are a proxy for age, which an EU employer may not select on.
YEARS = re.compile(r"\b(?:19|20)\d\d\b|\byears?\b|\bans\b|\baños\b|\bjahre", re.I)
NOW = re.compile(r"present|current|today|now|actuel|aujourd|actual|hoy|heute|seit", re.I)
EXPERIENCE_HEAD = re.compile(
    r"^\W*(?:work |professional )?(?:experience|employment|career|expérience(?:s)? "
    r"professionnelle(?:s)?|expérience(?:s)?|experiencia(?: profesional)?|berufserfahrung)\W*$",
    re.I)

#: How a source is named on the card.
FROM = {"letter": "from their “Why us”", "cv": "from CV", "other": "from their other document"}


@dataclass(frozen=True)
class Link:
    url: str
    kind: str  # "code", "site" or "profile"

    @property
    def host(self) -> str:
        return re.sub(r"^https://(www\.)?", "", self.url).split("/")[0]


@dataclass(frozen=True)
class Signals:
    links: tuple[Link, ...]
    #: Sentences where they say they built something, copied from the
    #: document named at the same place in `built_from`. Occasions first.
    built: tuple[str, ...]
    wants: tuple[str, ...]
    #: "described" (the screening found an occasion), "in_their_words" (a
    #: sentence in their documents names a tool, and the screening did not
    #: score it), "claimed" or "silent" -- and the sentence it rests on.
    ai: str
    ai_rests_on: str
    built_from: tuple[str, ...] = ()
    wants_from: tuple[str, ...] = ()
    ai_from: str = ""
    #: Current or last role as the CV words it; empty when not found.
    role: str = ""
    role_from: str = ""
    #: Which documents are on file, so "not found" can say which to open.
    has_letter: bool = False

    @property
    def to_open(self) -> tuple[Link, ...]:
        return tuple(l for l in self.links if l.kind != "profile")

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "links": [asdict(l) for l in self.links]}

    @property
    def wants_missing(self) -> str:
        """What "not found" tells a partner to do about it."""
        return ("not found -- open their “Why us”" if self.has_letter
                else "not found -- no “Why us” answer, and not in the CV")

    def lines(self) -> list[str]:
        """Plain text, for the card in a terminal."""
        def said(text: str, src: str) -> str:
            return f'"{text}" ({FROM.get(src, "from " + src)})'

        if self.to_open:
            built = "built: " + "  ".join(l.url for l in self.to_open)
        elif self.built:
            built = f"built: no link given -- {said(self.built[0], self.built_from[0])}"
        else:
            built = "built: no link given"
        lvl = {"described": "described", "in_their_words": "in their words",
               "claimed": "claimed only", "silent": "silent"}[self.ai]
        ai = (f"AI in their work: {lvl} -- {said(self.ai_rests_on, self.ai_from)}"
              if self.ai_rests_on else f"AI in their work: {lvl} -- not stated")
        wants = (f"wants to own: {said(self.wants[0], self.wants_from[0])}" if self.wants
                 else f"wants to own: {self.wants_missing}")
        out = [built, ai, wants]
        if self.role:
            out.append(f'current role: "{self.role}" (from CV -- check it)')
        return out


# -- the gate ----------------------------------------------------------------

def _flat(text: str) -> str:
    """Whitespace collapsed, and accents in one canonical form -- nothing else.

    NFC only joins a letter and its accent that a file may store apart ("è"
    as e + grave); it never changes a word. Quotes, dashes and case are left
    alone: a sentence shown in quotation marks is the document's, character
    for character.
    """
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text or "")).strip()


def verbatim(quote: str, document: str) -> bool:
    """Is this sentence in the document, as written, up to line breaks?"""
    q = _flat(quote)
    return bool(q) and q in _flat(document)


def sentences(text: str) -> list[str]:
    """Their sentences, as written, with line breaks and bullets taken off."""
    out = []
    for s in SENTENCE.split(text or ""):
        s = re.sub(r"\s+", " ", s).strip(" -*•")
        if s and not s.startswith("#"):
            out.append(s)
    return out


def sentence_of(quote: str, text: str) -> str:
    """The whole sentence a verified quote sits in -- or the quote, if none.

    A quote recorded at intake is often a fragment ("Four people use it");
    a fragment out of context reads as a different claim. The match is the
    tolerant one intake verified the quote with (dashes, curly quotes), but
    what comes back is the document's own sentence, not the quote.
    """
    from intake.posting import normalise
    q = normalise(quote)
    if not q:
        return ""
    for s in sentences(text):
        if q in normalise(s):
            return s
    return quote


# -- links ---------------------------------------------------------------

def classify(url: str) -> Link:
    url = url.rstrip(".,;:!?")
    host = re.sub(r"^https://(www\.)?", "", url).split("/")[0].lower()
    if any(host == h or host.endswith("." + h) for h in PROFILE_HOSTS):
        return Link(url, "profile")
    if any(host == h or host.endswith("." + h) for h in CODE_HOSTS):
        return Link(url, "code")
    return Link(url, "site")


def links_in(texts: list[str], given: list[str]) -> tuple[Link, ...]:
    """Links they gave, then links written in their documents, once each."""
    seen: dict[str, Link] = {}
    for u in list(given) + [m.group(0) for t in texts for m in URL.finditer(t)]:
        l = classify(u)
        seen.setdefault(l.url.rstrip("/"), l)
    order = {"code": 0, "site": 1, "profile": 2}
    return tuple(sorted(seen.values(), key=lambda l: order[l.kind]))


# -- wants -----------------------------------------------------------------

def is_wish(sentence: str) -> bool:
    """Does this sentence say what they would take on?

    Every wish-shaped phrase in it is tried; one that is only politeness
    ("I'd like to thank you") does not count, and the next one may. A
    sentence that places itself in the past is not a wish at all.
    """
    s = sentence.replace("’", "'")
    if PAST.search(s):
        return False
    return any(not POLITE.match(s, m.end()) for m in WANTS.finditer(s))


def wants_in(text: str, limit: int = 2) -> tuple[str, ...]:
    """Their sentences about what they would take on, copied as written."""
    out: list[str] = []
    for s in sentences(text):
        if is_wish(s) and s not in out:
            out.append(s)
    return tuple(out[:limit])


# -- reading the files -------------------------------------------------------

def _text(path: Path) -> str:
    """A document's text, read once per version of the file.

    Every page view draws every card, and a card reads its documents: without
    this, one heavy PDF is parsed again for each partner at each click.
    """
    try:
        st = path.stat()
    except OSError:
        return ""
    return _read_once(str(path), st.st_mtime_ns, st.st_size)


#: Room for a few thousand documents: a desk with hundreds of applications a
#: month draws its list from this, and a smaller cache would re-read the PDFs
#: of everyone who fell out of it at every click.
@functools.lru_cache(maxsize=4096)
def _read_once(path: str, _mtime: int, _size: int) -> str:
    from intake.cv import Profile
    try:
        return Profile.from_file(path).text
    except Exception:  # noqa: BLE001 -- a file the desk cannot read adds nothing, it does not fail
        return ""


def _facts(candidate: str) -> dict[str, Any]:
    """Facts on file for this person, by id: the CV's, and the letter's if read.

    `runs/facts_with_letter/` holds the CV's facts and the letter's together;
    where both exist, it is the larger record of the same person.
    """
    from intake.cv import load as load_facts
    out: dict[str, Any] = {}
    for d in ("facts", "facts_with_letter"):
        f = ROOT / "runs" / d / f"facts_{candidate}.json"
        if f.exists():
            for x in load_facts(f).facts:
                out[x.id] = x
    return out


def _ai(candidate: str, posting: str, facts: dict[str, Any]) -> tuple[str, Any]:
    """The AI level, read off the screening -- and the fact it rests on."""
    from screen import load as load_screening
    from triage import _screening_path
    f = _screening_path(candidate, posting)
    if not f.exists():
        return "silent", None
    s = load_screening(f)
    a = next((x for x in s.assessments if x.criterion_id.startswith("ai_")), None)
    if a is None or a.unknown:
        return "silent", None
    rest = next((facts[i] for i in a.fact_ids if i in facts), None)
    if a.strength == "strong" and rest is not None and rest.evidence == "instance":
        return "described", rest
    return "claimed", rest


def _built(facts: dict[str, Any]) -> list[Any]:
    """Occasions of making something, the verb in their own words first."""
    occasions = [f for f in facts.values() if f.evidence == "instance"]
    first = [f for f in occasions if MADE.search(f.source_quote)]
    return first + [f for f in occasions if f.kind == "project" and f not in first]


def _role(facts: dict[str, Any]) -> list[str]:
    """Role titles on file, current first, in the CV's own order otherwise.

    A title is an experience fact with a period beside it. Its quote is
    shown, never its period, and never a quote with a year in it.
    """
    titles = [f for f in facts.values()
              if f.kind == "experience" and f.period and f.source == "cv"]
    titles.sort(key=lambda f: not NOW.search(f.period))  # stable: CV order kept
    return [f.source_quote for f in titles]


def role_in(cv: str) -> str:
    """The first line under an Experience heading, without its dates.

    Used only when no record was written at intake. A CV with no such heading
    gives nothing, and the card shows nothing: a guess at a role, printed
    under a name, is worse than an empty line.
    """
    lines = (cv or "").splitlines()
    for i, line in enumerate(lines):
        if not EXPERIENCE_HEAD.match(line.strip()):
            continue
        for nxt in lines[i + 1:]:
            t = nxt.strip()
            if not t or set(t) <= set("-=_*#"):
                continue
            # "**Chief of Staff — Talvera** (Jan 2024 – present, Madrid)"
            t = re.split(r"\s*[(|]|\s+\d", t, maxsplit=1)[0]
            t = t.strip(" *_-•#,")
            return t if 2 < len(t) <= 80 else ""
    return ""


def _usable_role(r: str) -> bool:
    return bool(r) and not YEARS.search(r) and not NOT_A_ROLE.search(r)


# -- the card ----------------------------------------------------------------

def tool_sentence(texts: list[str]) -> str:
    """The first sentence in their documents that names an AI tool."""
    for t in texts:
        for sent in sentences(t):
            # Someone doing something with it, in the first person -- not a
            # skills list that names every tool going.
            if (AI_TOOL.search(sent) and FIRST_PERSON.search(sent)
                    and sent.count(",") < 4):
                return sent
    return ""


def _wants_on_file(candidate: str) -> list[tuple[str, str]]:
    """(quote, source) from `intake/wants.py`, if it was ever run for them."""
    f = ROOT / "runs" / "wants" / f"wants_{candidate}.json"
    if not f.exists():
        return []
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [(str(w.get("source_quote", "")), str(w.get("source", "")))
            for w in d.get("wants", []) if isinstance(w, dict)]


def signals(person: Any, candidate: str, posting: str) -> Signals:
    """The lines under a name. Free: files and records already on disk."""
    from desk import _files_dir
    docs = person.documents_for(posting)
    texts: dict[str, str] = {}
    for d in docs:  # the latest of each kind wins, as on the person's page
        texts[d.kind] = _text(_files_dir() / d.stored) or texts.get(d.kind, "")
    order = ("letter", "other", "cv")

    def checked(cands: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """Only what their own document says, with the document it is in.

        The one gate between anything this module produced and the card.
        Without it, a record from an older version of a CV, or a pattern
        that trimmed a sentence wrongly, would be printed in quotation marks
        as their words.
        """
        out: list[tuple[str, str]] = []
        for q, src in cands:
            if src in texts and verbatim(q, texts[src]) and (q, src) not in out:
                out.append((q, src))
        return out

    facts = _facts(candidate)

    def from_fact(f: Any) -> tuple[str, str]:
        src = "letter" if f.source == "letter" else "cv"
        return sentence_of(f.source_quote, texts.get(src, "")), src

    # AI in their work
    level, rest = _ai(candidate, posting, facts)
    ai = checked([from_fact(rest)] if rest is not None else [])
    if level != "described":
        for src in order:
            said = tool_sentence([texts.get(src, "")])
            if said and checked([(said, src)]):
                level, ai = "in_their_words", [(said, src)]
                break

    # Built: links are listed apart; this is the sentence for when there is none.
    built = checked([from_fact(f) for f in _built(facts)])
    if not built:
        built = checked([(s, src) for src in order for s in sentences(texts.get(src, ""))
                         if SAYS_MADE.search(s)])

    # Wants to own: the letter is where it is written; a CV rarely says it.
    wants = checked([(sentence_of(q, texts.get(src, "")), src)
                     for q, src in _wants_on_file(candidate)])
    if not wants:
        wants = checked([(s, src) for src in order for s in wants_in(texts.get(src, ""))])

    # Current role: from the CV only, and only a title.
    roles = [(r, "cv") for r in _role(facts) + [role_in(texts.get("cv", ""))]
             if _usable_role(r)]
    role = checked(roles)

    return Signals(
        links=links_in(list(texts.values()), person.all_links),
        built=tuple(q for q, _ in built[:2]),
        wants=tuple(q for q, _ in wants[:2]),
        ai=level,
        ai_rests_on=ai[0][0] if ai else "",
        built_from=tuple(src for _, src in built[:2]),
        wants_from=tuple(src for _, src in wants[:2]),
        ai_from=ai[0][1] if ai else "",
        role=role[0][0] if role else "",
        role_from="cv" if role else "",
        has_letter=bool(texts.get("letter", "").strip()),
    )
