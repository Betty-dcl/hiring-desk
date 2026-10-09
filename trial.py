#!/usr/bin/env python3
"""The trial day: prepared from what the paper left open, and read back.

Every candidate who gets far enough spends a paid day working with the team
before any offer. That day is the decision method -- a conversation can be
performed for forty-five minutes, a day of work cannot -- and it usually
arrives with no agenda beyond "see how they do". Everything this system has
learned about a candidate by then is exactly what the day needs: which
criteria the documents answered, which they only claimed, which they did not
touch, and which the screener could not make up its mind about.

    # once per posting: the day's exercises, the same for everyone (one call)
    ./.venv/bin/python trial.py design --posting founders_associate

    # per candidate: where to look during that day (free)
    ./.venv/bin/python trial.py brief --candidate sylvia_hartmann --posting founders_associate

    # the card the people running the day fill in (free)
    ./.venv/bin/python trial.py card --candidate sylvia_hartmann --posting founders_associate

    # the filled card, back onto the record under a name (free)
    ./.venv/bin/python trial.py record --card runs/trial/cards/<file>.toml --by "<who>"

    # the day against the paper, across everyone who has had one (free)
    ./.venv/bin/python trial.py calibrate --posting founders_associate

Four decisions carry it:

**The day is the same for everyone.** The exercises are designed once per
posting, from the posting's own responsibilities, and every candidate gets the
same ones. What changes from one brief to the next is where the observers
look, never what the candidate is asked to do. A day built around each
person's gaps would give two candidates two different tests and make their
results impossible to set side by side -- the posting itself says "structured
trial days", and this is what the word has to mean.

**The one model call never sees a candidate.** `design` is handed the posting
and nothing else. The per-candidate brief is assembled in Python from records
already on disk, so no model forms a view of a person anywhere in this file.

**What the paper established is still checked.** A verified quote proves the
CV says the words and never that they are true -- the limit written under
every trace. So a criterion settled on paper is not skipped; it gets one
question about the occasion the CV cites, asking for the part a CV never
carries.

**The day's result is the first ground truth this system has had.** Every
harness here measures consistency: the screener against itself, a name against
another name. None of them can say whether the screener was *right*, because
nothing in the repository knows. A filled trial-day card does. `calibrate`
sets the day's findings against the paper's, criterion by criterion, and says
how often the paper overclaimed, how often the assertion cap cost somebody
something real, and how much the paper missed outright -- with an interval,
and with the sample size printed beside it.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import tomllib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from intake.cv import Facts
from intake.posting import DraftAgenda, normalise
from screen import CREDIT, DECLARATIVE_KINDS, STRENGTHS, Screening

#: Working minutes in the day, exercises and conversation together; breaks
#: are not in it. A default, not a claim about any company's schedule.
DEFAULT_DAY = 360

#: The shortest exercise the day keeps. The design prompt asks for fewer,
#: longer exercises, and the first design still came back with a fifteen-minute
#: one -- so the rule is here, where it holds, rather than only in the prompt.
#: Nobody shows range in a quarter of an hour; they show whether they panic.
MIN_EXERCISE_MINUTES = 30

#: A declared condition should be seen more than once. Read in a single
#: exercise, one bad hour -- a tool that will not start, a nervous morning --
#: leaves the one thing the posting calls non-negotiable unanswered or, worse,
#: answered wrongly.
MIN_GATE_READINGS = 2

#: The part of the day that is different for each candidate: one conversation,
#: where the questions no exercise can answer are asked. Everything else is
#: common.
DEFAULT_SLOT = 30

#: What one question costs in that conversation, including the follow-up that
#: makes it worth asking.
MINUTES_PER_QUESTION = 5

#: Why a criterion is still open after the paper screen, in the order the day
#: should care about them. The order is the whole of the prioritisation: a
#: declared condition nobody has answered comes before anything that is merely
#: weak, and a criterion the screener could not decide comes before one it
#: decided thinly, because the second is at least a reading.
REASONS = (
    "open gate",               # the posting declares it, the documents are silent
    "silent",                  # the documents do not speak to it
    "self-description only",   # the claim is there, no occasion is
    "the letter only",         # only the cover letter speaks to it
    "moved between reads",     # the screener gave different answers on identical input
    "thin",                    # an occasion, but a slight one
    "partial",                 # an occasion that bears on it without establishing it
    "on paper",                # established by an occasion; the day confirms it is theirs
)

#: What the day must not ask about. Nothing here is on any agenda this system
#: extracts, and an exercise that reaches for it is discarded whole -- the
#: design is candidate-free, so there is no legitimate reason for one to.
#: Work authorisation is deliberately absent: it can be a real, lawful
#: condition of a role, and `settings.py` is where a company says whether it is.
#:
#: Several patterns are narrower than the word, on purpose. "Invoice age",
#: "health insurance" in a payroll run, "political risk" in a market analysis
#: and "top-tier partners" are all ordinary material for a finance role, and a
#: filter that discards a sound exercise over them trains whoever runs it to
#: switch the filter off.
PROTECTED = (
    r"\b(your|their|his|her|candidate'?s) age\b", r"\bhow old\b", r"\bdate of birth\b",
    r"\bmarri", r"\bspouse\b", r"\bchildren\b", r"\bkids\b",
    r"\bpregnan", r"\bmaternity\b", r"\bfamily plans?\b",
    r"\breligio", r"\bchurch\b", r"\bethnic", r"\bracial\b", r"\bnationality\b",
    r"\bcitizenship\b", r"\bwhere are you (really )?from\b",
    r"\b(your|their|his|her) health\b", r"\bhealth (condition|issue|problem)s?\b",
    r"\bdisabilit", r"\bmedical (condition|history)\b", r"\bsexual",
    r"\bunion member", r"\bpolitical (view|affiliation|opinion|belief)s?\b",
)

#: Words that turn an observation into a verdict. The card records what was
#: seen; the decision is a person's and happens elsewhere.
DECISION_WORDS = ("reject", "hire", "not a fit", "shortlist", "pass on",
                  "unsuccessful", "we have decided")

#: Below this many paired observations in a bucket, `calibrate` prints the
#: count and refuses to call it a rate.
MIN_PAIRS = 10

#: Strengths in order, for "within one step". Unknown is not on the ladder: it
#: is the absence of a reading, and it is counted apart.
LADDER = ("weak", "moderate", "strong")


def _hits(text: str, patterns: tuple[str, ...]) -> list[str]:
    return [p for p in patterns if re.search(p, text, re.I)]


def _clip(text: str, n: int) -> str:
    """One line, at most n characters, and visibly cut when it was."""
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= n else text[:n - 3].rstrip() + "..."


# --------------------------------------------------------------------------
# The day's design: once per posting, the same for everyone
# --------------------------------------------------------------------------

@dataclass
class Observation:
    """One criterion an exercise lets an observer see, and what they would see."""

    criterion_id: str
    strong: str
    thin: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Exercise:
    """A piece of the job, given to every candidate the same way."""

    id: str
    title: str
    responsibility_id: str
    minutes: int
    material: str
    observes: list[Observation] = field(default_factory=list)
    #: The posting's own sentence for the responsibility this is a piece of,
    #: copied in when the design is verified, so the brief can show that the
    #: exercise tests the work described and not a puzzle.
    from_posting: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "observes": [o.to_dict() for o in self.observes]}


@dataclass
class Design:
    posting_id: str
    exercises: list[Exercise] = field(default_factory=list)
    #: criterion id -> why a day cannot show it. Those go to the conversation.
    not_observable: dict[str, str] = field(default_factory=dict)
    #: What verification threw out, with why. Kept, like every other drop.
    rejected: list[dict[str, str]] = field(default_factory=list)
    #: Scored criteria no kept exercise observes and the design did not
    #: declare unobservable either. They are asked directly, and named here so
    #: the gap in the design is visible rather than papered over.
    uncovered: list[str] = field(default_factory=list)
    day_minutes: int = DEFAULT_DAY
    slot_minutes: int = DEFAULT_SLOT
    designed_at: str = ""
    model: str = ""
    #: The model's answer exactly as it came back, so verification can be
    #: re-run for free when a rule changes -- the same reason every exchange
    #: here is replayable.
    answer: dict[str, Any] = field(default_factory=dict)
    #: Declared conditions, so the design can say which are under-read.
    gates: list[str] = field(default_factory=list)

    @property
    def minutes(self) -> int:
        return sum(e.minutes for e in self.exercises)

    def readings(self) -> dict[str, int]:
        """How many exercises let an observer see each criterion."""
        out: dict[str, int] = {}
        for e in self.exercises:
            for o in e.observes:
                out[o.criterion_id] = out.get(o.criterion_id, 0) + 1
        return out

    @property
    def under_read_gates(self) -> list[str]:
        r = self.readings()
        return [g for g in self.gates
                if g not in self.not_observable and r.get(g, 0) < MIN_GATE_READINGS]

    def observing(self, criterion_id: str) -> list[Exercise]:
        return [e for e in self.exercises
                if any(o.criterion_id == criterion_id for o in e.observes)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "posting_id": self.posting_id,
            "designed_at": self.designed_at,
            "model": self.model,
            "sees_candidates": False,
            "same_for_every_candidate": True,
            "day_minutes": self.day_minutes,
            "slot_minutes": self.slot_minutes,
            "exercise_minutes": self.minutes,
            "exercises": [e.to_dict() for e in self.exercises],
            "not_observable": self.not_observable,
            "uncovered": self.uncovered,
            "readings": self.readings(),
            "gates": self.gates,
            "under_read_gates": self.under_read_gates,
            "rejected": self.rejected,
            "answer": self.answer,
        }

    def render(self) -> str:
        lines = [f"{self.posting_id} -- the trial day, the same for every candidate", ""]
        at = 0
        for e in self.exercises:
            lines.append(f"  +{at // 60}:{at % 60:02d}  {e.minutes:>3} min  {e.title}  [{e.id}]")
            if e.from_posting:
                lines.append(f'              a piece of: "{e.from_posting[:80]}"')
            lines.append(f"              shows: {', '.join(o.criterion_id for o in e.observes)}")
            at += e.minutes
        lines.append(f"  +{at // 60}:{at % 60:02d}  {self.slot_minutes:>3} min  "
                     f"conversation, different for each candidate")
        lines.append("")
        lines.append(f"  {self.minutes + self.slot_minutes} of {self.day_minutes} working "
                     f"minutes used")
        r = self.readings()
        if r:
            lines.append("")
            lines.append("  how many times the day sees each criterion:")
            for cid, n in sorted(r.items(), key=lambda kv: (-kv[1], kv[0])):
                gate = "  [declared condition]" if cid in self.gates else ""
                lines.append(f"    {n}  {cid}{gate}")
        for g in self.under_read_gates:
            lines.append("")
            lines.append(f"  WARNING: {g} is a declared condition and the day reads it "
                         f"{r.get(g, 0)} time(s).")
            lines.append("  One bad hour decides it. Give it a second exercise, or accept")
            lines.append("  that its reading is a single observation and say so on the card.")
        if self.not_observable:
            lines.append("")
            lines.append("  a day cannot show these, so they are asked in the conversation:")
            for cid, why in self.not_observable.items():
                lines.append(f"    {cid}: {why}")
        if self.uncovered:
            lines.append("")
            lines.append(f"  the design missed these, so they are asked directly: "
                         f"{', '.join(self.uncovered)}")
        if self.rejected:
            lines.append("")
            lines.append(f"  {len(self.rejected)} item(s) discarded in verification:")
            for r in self.rejected:
                lines.append(f"    {r['id']}: {r['reason']}")
        return "\n".join(lines)


SYSTEM = """\
You design the exercises for a paid trial day: one day a candidate spends
working with a team before any offer is made. The same exercises will be given
to every candidate for this posting, so that what differs between two
candidates' days is the candidates, not the day.

You are given what the job involves -- the posting's responsibilities, each in
the posting's own words -- and what the posting measures: the criteria, each
with what a strong answer contains.

Build the day as a sample of the job:

- Every exercise is a piece of one responsibility, named by its id. The day
  tests the work the posting describes. No puzzles, no brainteasers.
- Every exercise names the criteria it lets an observer see, and for each one
  says what the observer would see if it is strong and what they would see if
  it is thin. Write behaviour -- something done, produced or said in the room
  -- not a trait. "Asks which entity the invoice belongs to before posting it"
  is observable. "Detail-oriented" is not.
- Material is self-contained and invented: a fictional company, fictional
  numbers, fictional people. A candidate is never handed the company's real,
  live problem. A trial day is not free consulting, paid or not.
- Some criteria cannot be seen in one day -- years of experience, a past
  fundraising, a language spoken elsewhere. List them in not_observable with
  one line on why. Do not stretch an exercise to pretend it observes them.
- Stay inside the budget in minutes. Fewer, longer exercises beat many short
  ones: a person cannot show range in fifteen minutes. Nothing under 30.
- A criterion the posting declares a hard requirement must be observable in
  at least two exercises, built differently. One hour is not enough to settle
  the one thing the posting calls non-negotiable: a bad hour would decide it.
- Spread the observation by what the posting weighs, not by what is easy to
  see. A criterion that shows up everywhere does not need a sixth reading.
- Nothing about age, family, health, religion, origin, nationality or anything
  else that is not on the posting. An exercise that touches any of it is
  discarded whole.

You are not assessing anybody. There is no candidate in front of you.
"""


def _schema(responsibility_ids: list[str], criterion_ids: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "exercises": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "short snake_case id"},
                        "title": {"type": "string"},
                        "responsibility_id": {"type": "string", "enum": responsibility_ids},
                        "minutes": {"type": "integer"},
                        "material": {"type": "string",
                                     "description": "what the candidate is handed, invented"},
                        "observes": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "criterion_id": {"type": "string", "enum": criterion_ids},
                                    "strong": {"type": "string"},
                                    "thin": {"type": "string"},
                                },
                                "required": ["criterion_id", "strong", "thin"],
                            },
                        },
                    },
                    "required": ["id", "title", "responsibility_id", "minutes",
                                 "material", "observes"],
                },
            },
            "not_observable": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "criterion_id": {"type": "string", "enum": criterion_ids},
                        "why": {"type": "string"},
                    },
                    "required": ["criterion_id", "why"],
                },
            },
        },
        "required": ["exercises", "not_observable"],
    }


def _render_posting(agenda: DraftAgenda) -> str:
    resp = [c for c in agenda.criteria if c.section == "responsibility"]
    lines = ["# What the job involves"]
    for c in resp:
        lines.append(f'- {c.id}: "{c.source_quote}"')
        if c.looks_like:
            lines.append(f"    in practice: {c.looks_like}")
    lines += ["", "# What the posting measures"]
    for c in agenda.scored:
        gate = "  [the posting declares this a hard requirement]" if c.hard else ""
        lines.append(f'- {c.id}{gate}: "{c.source_quote}"')
        if c.looks_like:
            lines.append(f"    a strong answer contains: {c.looks_like}")
    return "\n".join(lines)


def design(client: Any, agenda: DraftAgenda, *, model: str,
           day_minutes: int = DEFAULT_DAY, slot_minutes: int = DEFAULT_SLOT,
           max_tokens: int = 6000) -> Design:
    """Ask for the day's exercises, then verify every one of them.

    The model is handed the posting and nothing else. No facts, no screening,
    no name -- the day is designed before anybody is in it.
    """
    resp = [c.id for c in agenda.criteria if c.section == "responsibility"]
    if not resp:
        raise ValueError(f"{agenda.posting_id}: the posting has no responsibilities to "
                         f"build a day from")
    budget = day_minutes - slot_minutes
    raw = client.structured(
        model=model,
        system=SYSTEM,
        user=(f"{_render_posting(agenda)}\n\n"
              f"# Budget\n{budget} minutes of exercises. A {slot_minutes}-minute "
              f"conversation is kept apart and is not yours to fill."),
        schema=_schema(resp, [c.id for c in agenda.scored]),
        tool_name="design_trial_day",
        tool_description="Design the trial day's exercises for this posting.",
        max_tokens=max_tokens,
    )
    d = verify(agenda, raw, day_minutes=day_minutes, slot_minutes=slot_minutes)
    d.model = model
    return d


def verify(agenda: DraftAgenda, raw: dict[str, Any], *, day_minutes: int = DEFAULT_DAY,
           slot_minutes: int = DEFAULT_SLOT) -> Design:
    """Keep what holds, discard what does not, and say which and why.

    Pure, so every rule is testable without a model. The rules:

    * an exercise is a piece of a responsibility the posting actually lists;
    * it observes at least one criterion the posting actually scores;
    * it touches nothing protected, anywhere in its text;
    * what an observer would see is behaviour, not a verdict;
    * the day fits its budget -- exercises past it are dropped in the order
      given, and named, rather than squeezed.
    """
    resp = {c.id: c for c in agenda.criteria if c.section == "responsibility"}
    scored = {c.id: c for c in agenda.scored}
    budget = day_minutes - slot_minutes
    out = Design(posting_id=agenda.posting_id, day_minutes=day_minutes,
                 slot_minutes=slot_minutes,
                 designed_at=datetime.now(timezone.utc).isoformat(),
                 answer=raw, gates=[c.id for c in agenda.scored if c.hard])

    used, seen = 0, set()
    for item in raw.get("exercises", []) or []:
        eid = str(item.get("id", "")).strip() or f"exercise_{len(seen) + 1}"
        rid = str(item.get("responsibility_id", "")).strip()
        try:
            minutes = int(item.get("minutes", 0))
        except (TypeError, ValueError):
            minutes = 0

        def drop(reason: str) -> None:
            out.rejected.append({"id": eid, "reason": reason})

        if eid in seen:
            drop("duplicate id")
            continue
        seen.add(eid)
        if rid not in resp:
            drop(f"{rid!r} is not a responsibility this posting lists")
            continue
        if minutes <= 0:
            drop("no duration")
            continue
        if minutes < MIN_EXERCISE_MINUTES:
            drop(f"{minutes} minutes is under the {MIN_EXERCISE_MINUTES}-minute floor")
            continue

        observes, strays = [], []
        for o in item.get("observes", []) or []:
            cid = str(o.get("criterion_id", "")).strip()
            if cid not in scored:
                strays.append(cid)
                continue
            if any(x.criterion_id == cid for x in observes):
                continue
            observes.append(Observation(cid, str(o.get("strong", "")).strip(),
                                        str(o.get("thin", "")).strip()))
        if strays:
            out.rejected.append({"id": f"{eid}/observes",
                                 "reason": f"not criteria of this posting: {', '.join(strays)}"})
        if not observes:
            drop("observes nothing the posting scores")
            continue

        text = " ".join([str(item.get("title", "")), str(item.get("material", ""))]
                        + [f"{o.strong} {o.thin}" for o in observes])
        touched = _hits(text, PROTECTED)
        if touched:
            drop(f"touches a protected subject ({', '.join(touched)})")
            continue
        verdicts = [w for w in DECISION_WORDS
                    if any(w in f"{o.strong} {o.thin}".lower() for o in observes)]
        if verdicts:
            drop(f"what an observer sees is written as a verdict ({', '.join(verdicts)})")
            continue
        if used + minutes > budget:
            drop(f"over the day's budget ({used} + {minutes} > {budget} minutes)")
            continue

        used += minutes
        out.exercises.append(Exercise(
            id=eid, title=str(item.get("title", "")).strip(), responsibility_id=rid,
            minutes=minutes, material=str(item.get("material", "")).strip(),
            observes=observes, from_posting=resp[rid].source_quote,
        ))

    observed = {o.criterion_id for e in out.exercises for o in e.observes}
    for item in raw.get("not_observable", []) or []:
        cid = str(item.get("criterion_id", "")).strip()
        why = str(item.get("why", "")).strip()
        if cid not in scored:
            out.rejected.append({"id": f"not_observable/{cid}",
                                 "reason": "not a criterion of this posting"})
            continue
        if not why:
            out.rejected.append({"id": f"not_observable/{cid}",
                                 "reason": "declared unobservable with no reason"})
            continue
        if cid in observed:
            #: An exercise that shows it beats a sentence saying nothing can.
            continue
        out.not_observable[cid] = why

    out.uncovered = [c for c in scored if c not in observed and c not in out.not_observable]
    return out


def save_design(d: Design, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(d.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_design(path: str | Path) -> Design:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return Design(
        posting_id=raw["posting_id"],
        exercises=[Exercise(
            id=e["id"], title=e["title"], responsibility_id=e["responsibility_id"],
            minutes=e["minutes"], material=e.get("material", ""),
            observes=[Observation(**o) for o in e.get("observes", [])],
            from_posting=e.get("from_posting", ""),
        ) for e in raw.get("exercises", [])],
        not_observable=raw.get("not_observable", {}),
        rejected=raw.get("rejected", []),
        uncovered=raw.get("uncovered", []),
        day_minutes=raw.get("day_minutes", DEFAULT_DAY),
        slot_minutes=raw.get("slot_minutes", DEFAULT_SLOT),
        designed_at=raw.get("designed_at", ""),
        model=raw.get("model", ""),
        answer=raw.get("answer", {}),
        gates=raw.get("gates", []),
    )


def answer_of(d: Design) -> dict[str, Any]:
    """The answer a design was verified from.

    The stored one when there is one. A design recorded before answers were
    kept is rebuilt from what survived verification -- which is the right
    input for re-applying a *stricter* rule, and the wrong one for loosening
    a rule, because whatever was dropped the first time is gone.
    """
    if d.answer:
        return d.answer
    return {
        "exercises": [{"id": e.id, "title": e.title, "responsibility_id": e.responsibility_id,
                       "minutes": e.minutes, "material": e.material,
                       "observes": [o.to_dict() for o in e.observes]}
                      for e in d.exercises],
        "not_observable": [{"criterion_id": c, "why": w} for c, w in d.not_observable.items()],
    }


# --------------------------------------------------------------------------
# The brief: per candidate, free, assembled from the record
# --------------------------------------------------------------------------

@dataclass
class Probe:
    """One criterion, and what the day has to do about it for this person."""

    criterion_id: str
    weight: float
    hard: bool
    paper: str
    reasons: list[str]
    asked_as: str = ""
    question: str = ""
    looks_like: str = ""
    #: The candidate's own words the paper rests on: (quote, where, how read,
    #: kind of fact).
    claimed: list[tuple[str, str, str, str]] = field(default_factory=list)
    #: The strengths the screener gave on identical input, when it wavered.
    wavered: dict[str, int] = field(default_factory=dict)
    gain: float = 0.0

    @property
    def settle(self) -> bool:
        """Open on paper, as opposed to established and only to be confirmed."""
        return self.reasons != ["on paper"]

    @property
    def why(self) -> str:
        return ", ".join(self.reasons)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "settle": self.settle,
                "claimed": [{"quote": q, "where": w, "read_as": r, "kind": k}
                            for q, w, r, k in self.claimed]}


def probes(s: Screening, agenda: DraftAgenda | None = None, facts: Facts | None = None,
           *, wavered: dict[str, dict[str, int]] | None = None) -> list[Probe]:
    """Every criterion, with why it is open -- or that it is not.

    No criterion is left out. One the paper settled is still listed, because
    a verified quote is not a true one; it just goes to the end, marked as a
    confirmation rather than a question.
    """
    wavered = wavered or {}
    by_crit = {c.id: c for c in (agenda.criteria if agenda else [])}
    by_fact = {f.id: f for f in (facts.facts if facts else [])}
    total = s.total_weight or 1.0

    out: list[Probe] = []
    for a in s.assessments:
        reasons: list[str] = []
        if a.unknown:
            reasons.append("open gate" if a.hard else "silent")
        if a.rests_on == "assertions only" or a.capped_why == "assertions only":
            reasons.append("self-description only")
        if a.rests_on == "the letter only":
            reasons.append("the letter only")
        if a.criterion_id in wavered:
            reasons.append("moved between reads")
        if a.strength == "weak" and not reasons:
            reasons.append("thin")
        if a.strength == "moderate" and not reasons:
            reasons.append("partial")
        if not reasons:
            reasons.append("on paper")
        reasons.sort(key=REASONS.index)

        c = by_crit.get(a.criterion_id)
        claimed = []
        for fid in a.fact_ids:
            f = by_fact.get(fid)
            if f is not None:
                claimed.append((f.source_quote, "the letter" if f.from_letter else "the CV",
                                f.evidence, f.kind))
        out.append(Probe(
            criterion_id=a.criterion_id, weight=a.weight, hard=a.hard,
            paper=a.strength if not a.capped_from else f"{a.strength} (capped from {a.capped_from})",
            reasons=reasons,
            asked_as=c.source_quote if c else "",
            question=c.question if c else "",
            looks_like=c.looks_like if c else "",
            claimed=claimed,
            wavered=wavered.get(a.criterion_id, {}),
            gain=round(a.weight * (CREDIT["strong"] - CREDIT.get(a.strength, 0.0)) / total, 4),
        ))

    def order(p: Probe) -> tuple:
        # Open before confirmed; a declared gate before anything else open;
        # then the criterion that could move the number most.
        return (not p.settle, not p.hard, REASONS.index(p.reasons[0]), -p.gain, -p.weight)

    return sorted(out, key=order)


def _unasked_line(p: Probe) -> str:
    """What to do in the conversation about one criterion."""
    if p.settle:
        return f'ask: "{p.question}"' if p.question else "ask directly"
    if not p.claimed:
        return "established on paper; ask for the occasion behind it"
    quote, where, _, kind = p.claimed[0]
    said = f'{where} says: "{_clip(quote, 90)}"'
    if kind == "language":
        # A language is not confirmed by talking about it.
        return f"{said} -- hold part of this conversation in it"
    if kind in DECLARATIVE_KINDS:
        # A degree or a certificate is checked on a document, not in a room.
        return f"{said} -- checked on a document if it matters, not a question for the day"
    return (f"{said} -- ask for what a CV never carries: what was there before, "
            f"who else was in it, what went wrong")


def _looking_for(p: Probe) -> str:
    """One line on what this person's paper leaves the observer to find."""
    q = f'"{_clip(p.claimed[0][0], 80)}"' if p.claimed else ""
    moved = (" (" + ", ".join(f"{k} x{v}" for k, v in sorted(p.wavered.items())) + ")"
             if p.wavered else "")
    return {
        "open gate": "the posting declares this a condition and the documents are "
                     "silent -- the first thing to see",
        "silent": "the documents do not speak to it; this is the first reading anyone gets",
        "self-description only": f"the documents claim it in their own words {q}; "
                                 f"watch for the occasion",
        "the letter only": f"only the cover letter speaks to it {q}; first sight of it "
                           f"outside a document written for this posting",
        "moved between reads": f"the screener gave different answers on identical input"
                               f"{moved} -- treat the paper as undecided",
        "thin": f"an occasion, but a slight one {q}",
        "partial": f"an occasion that bears on it without establishing it {q}",
        "on paper": f"established on paper {q}; confirm it is theirs",
    }[p.reasons[0]]


@dataclass
class Brief:
    candidate_id: str
    posting_id: str
    score: float
    coverage: float
    probes: list[Probe]
    design: Design | None = None
    spread: float | None = None
    slot_minutes: int = DEFAULT_SLOT
    #: What an agent-to-agent exchange, when one was run, left unsettled.
    exchange_open: list[dict[str, str]] = field(default_factory=list)

    def conversation(self) -> tuple[list[Probe], list[Probe]]:
        """What goes in the one slot that is this candidate's own, and what
        does not fit.

        In, in this order: every open criterion no exercise shows (declared
        gates first); then the paper's claims no exercise shows, heaviest
        first; then the claims an exercise already puts to the test, which
        are worth a question only if time is left. What overflows is
        returned, not dropped -- a brief that quietly omits a question is how
        a criterion ends up never asked.
        """
        exercised = (lambda cid: bool(self.design and self.design.observing(cid)))
        open_unexercised = [p for p in self.probes if p.settle and not exercised(p.criterion_id)]
        confirm = sorted([p for p in self.probes if not p.settle], key=lambda p: -p.weight)
        queue = (open_unexercised
                 + [p for p in confirm if not exercised(p.criterion_id)]
                 + [p for p in confirm if exercised(p.criterion_id)])
        fits = max(0, self.slot_minutes // MINUTES_PER_QUESTION)
        return queue[:fits], queue[fits:]

    def to_dict(self) -> dict[str, Any]:
        asked, overflow = self.conversation()
        return {
            "candidate_id": self.candidate_id,
            "posting_id": self.posting_id,
            "decides": False,
            "recommends": False,
            "paper_score": self.score,
            "paper_coverage": self.coverage,
            "spread": self.spread,
            "designed": self.design is not None,
            "probes": [p.to_dict() for p in self.probes],
            "conversation": [p.criterion_id for p in asked],
            "did_not_fit": [p.criterion_id for p in overflow],
            "exchange_left_open": self.exchange_open,
        }

    def render(self) -> str:
        n_open = sum(p.settle for p in self.probes)
        lines = [
            f"Trial day brief -- {self.candidate_id}, {self.posting_id}",
            "",
            "For the people running the day. It says where to look, not what to",
            "conclude: it contains no recommendation, and the system that wrote it has",
            "no state meaning yes or no.",
            "",
        ]
        spread = f" (+/-{self.spread:.0%} on identical input)" if self.spread is not None else ""
        lines.append(f"  The paper: {self.score:.0%}{spread}, speaking to {self.coverage:.0%} "
                     f"of the weight.")
        lines.append(f"  {n_open} of {len(self.probes)} criteria are open after it. "
                     f"The day is where they get answered.")
        gates = [p.criterion_id for p in self.probes if p.reasons[0] == "open gate"]
        if gates:
            lines.append(f"  Declared conditions still open: {', '.join(gates)} -- "
                         f"a question for the day, never a verdict.")
        lines.append("")

        by_id = {p.criterion_id: p for p in self.probes}
        if self.design and self.design.exercises:
            lines.append("THE DAY -- the same exercises for every candidate")
            lines.append("")
            at = 0
            for e in self.design.exercises:
                lines.append(f"  +{at // 60}:{at % 60:02d}  {e.minutes} min  {e.title}")
                if e.from_posting:
                    lines.append(f'         a piece of the job: "{e.from_posting[:78]}"')
                here = [by_id[o.criterion_id] for o in e.observes if o.criterion_id in by_id]
                watch = sorted([p for p in here if p.settle],
                               key=lambda p: (not p.hard, REASONS.index(p.reasons[0]), -p.gain))
                known = [p for p in here if not p.settle]
                if watch:
                    lines.append("         for this candidate, watch:")
                for p in watch:
                    o = next(o for o in e.observes if o.criterion_id == p.criterion_id)
                    gate = "  [declared condition]" if p.hard else ""
                    lines.append(f"           {p.criterion_id}{gate}")
                    lines.append(f"              why: {_looking_for(p)}")
                    if o.strong:
                        lines.append(f"              strong looks like: {o.strong}")
                    if o.thin:
                        lines.append(f"              thin looks like:   {o.thin}")
                if known:
                    lines.append(f"         also visible here, settled on paper -- the exercise "
                                 f"is the check: {', '.join(p.criterion_id for p in known)}")
                lines.append("")
                at += e.minutes
        else:
            lines.append("THE DAY -- not designed yet")
            lines.append("")
            lines.append("  No exercises exist for this posting, so every open criterion falls")
            lines.append("  to the conversation below and most will not fit. `trial.py design`")
            lines.append("  builds the day once, from the posting, for every candidate.")
            lines.append("")

        asked, overflow = self.conversation()
        lines.append(f"THE CONVERSATION -- {self.slot_minutes} min, this candidate's own")
        lines.append("")
        if not asked:
            lines.append("  nothing to ask that the exercises do not already show")
        for p in asked:
            gate = "  [declared condition]" if p.hard else ""
            kind = (f"paper {p.paper}; {p.why}" if p.settle
                    else f"confirm: {p.why}"
                    + ("; an exercise also shows it" if self.design
                       and self.design.observing(p.criterion_id) else ""))
            lines.append(f"  {p.criterion_id}{gate}  ({kind})")
            lines.append(f"      {_unasked_line(p)}")
            if p.settle and p.criterion_id in (self.design.not_observable if self.design else {}):
                lines.append(f"      no exercise can show it: "
                             f"{self.design.not_observable[p.criterion_id]}")
        if overflow:
            lines.append("")
            lines.append(f"  did not fit in {self.slot_minutes} minutes, and still unasked: "
                         f"{', '.join(p.criterion_id for p in overflow)}")
        lines.append("")

        if self.exchange_open:
            lines.append("WHAT THE EXCHANGE LEFT OPEN")
            lines.append("")
            for x in self.exchange_open:
                lines.append(f"  {x['id']} ({x['status']}): {x['question']}")
            lines.append("")

        lines.append("RULES OF THE DAY")
        lines.append("")
        lines.append("  - Write what you saw before you choose a strength. The card refuses a")
        lines.append("    strength with no observation, and an observation that is the rubric")
        lines.append("    copied back.")
        lines.append("  - Every candidate does the same exercises against the same anchors.")
        lines.append("    This brief changes where you look, never what they are asked to do.")
        lines.append("  - A criterion the day did not show stays unknown. Unknown is not low.")
        lines.append("  - Nothing about age, family, health, religion or origin. None of it is")
        lines.append("    on the agenda.")
        return "\n".join(lines)


def build(s: Screening, agenda: DraftAgenda | None = None, facts: Facts | None = None, *,
          design: Design | None = None, wavered: dict[str, dict[str, int]] | None = None,
          spread: float | None = None, exchange: dict[str, Any] | None = None,
          slot_minutes: int | None = None) -> Brief:
    """Assemble one candidate's brief. No model, no cost."""
    open_x = []
    if exchange:
        status = exchange.get("exchange", {}).get("ledger", {}).get("status", {})
        questions = {c["id"]: c.get("question", "")
                     for c in exchange.get("verdicts", {}).get("criteria", [])}
        open_x = [{"id": cid, "status": st, "question": questions.get(cid, "")}
                  for cid, st in status.items() if st not in ("resolved",)]
    slot = slot_minutes if slot_minutes is not None else (
        design.slot_minutes if design else DEFAULT_SLOT)
    return Brief(candidate_id=s.candidate_id, posting_id=s.posting_id, score=s.score,
                 coverage=s.coverage, probes=probes(s, agenda, facts, wavered=wavered),
                 design=design, spread=spread, slot_minutes=slot, exchange_open=open_x)


# --------------------------------------------------------------------------
# The card: filled in by the people running the day, read back under a name
# --------------------------------------------------------------------------

def card(b: Brief) -> str:
    """A TOML file with one entry per criterion, open ones first.

    The reviewer is not a field. It is given on the command line when the card
    is recorded, so a card cannot be filed under somebody else's name by
    editing it.
    """
    out = [
        f"# Trial day card -- {b.candidate_id}, {b.posting_id}",
        "#",
        "# For each criterion: write what you saw in `observed`, in your words,",
        "# THEN choose `strength`. A strength with no observation is refused, and",
        "# so is an observation that repeats the rubric back.",
        "#",
        "# strength: strong | moderate | weak | unknown   (leave empty if the day",
        "# did not show it -- it stays as the paper had it, and unknown is not low)",
        "#",
        "# Nothing about age, family, health, religion or origin.",
        "",
        f'candidate = "{b.candidate_id}"',
        f'posting = "{b.posting_id}"',
        "# Optional. Your overall read of the day, 0-100, weighed beside the evidence.",
        'impression = ""',
        'note = ""',
        "",
    ]
    for p in b.probes:
        where = [e.id for e in b.design.observing(p.criterion_id)] if b.design else []
        out.append(f"[criteria.{p.criterion_id}]")
        out.append(f"# the posting: {json.dumps(p.asked_as[:100], ensure_ascii=False)}")
        out.append(f'paper = "{p.paper}"')
        out.append(f'why_open = "{p.why}"')
        out.append(f'seen_in = {json.dumps(where or ["conversation"])}')
        out.append('observed = ""')
        out.append('strength = ""')
        out.append("")
    return "\n".join(out)


class CardError(ValueError):
    """A card that cannot go on the record. Carries every problem at once."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("\n".join(problems))


@dataclass
class Entry:
    criterion_id: str
    paper: str
    why_open: str
    observed: str
    strength: str


def _is_rubric(observed: str, *rubric: str) -> bool:
    """True when an observation is the rubric copied back rather than a sighting.

    Checked on the normalised text, both ways round, and only for long enough
    strings that the match means something: "strong" containing "strong" is
    not a copy.
    """
    o = normalise(observed)
    if len(o) < 20:
        return False
    return any(r and len(normalise(r)) >= 20 and (o in normalise(r) or normalise(r) in o)
               for r in rubric)


def read_card(text: str, agenda: DraftAgenda | None = None,
              design: Design | None = None) -> tuple[dict[str, Any], list[Entry]]:
    """Parse and check a filled card. Refuses the whole card on any problem.

    All-or-nothing on purpose: a card half-recorded is a record nobody can
    trust about which half it holds.
    """
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise CardError([f"the card is not valid TOML: {e}"]) from e

    by_crit = {c.id: c for c in (agenda.criteria if agenda else [])}
    anchors: dict[str, list[str]] = {}
    for e in (design.exercises if design else []):
        for o in e.observes:
            anchors.setdefault(o.criterion_id, []).extend([o.strong, o.thin])

    problems: list[str] = []
    impression = raw.get("impression", "")
    if impression not in ("", None):
        try:
            impression = float(impression)
        except (TypeError, ValueError):
            problems.append(f"impression {impression!r} is not a number")
            impression = None
        else:
            if not 0 <= impression <= 100:
                problems.append(f"impression {impression:g} is outside 0-100")
    else:
        impression = None

    entries: list[Entry] = []
    for cid, e in (raw.get("criteria") or {}).items():
        observed = str(e.get("observed", "")).strip()
        strength = str(e.get("strength", "")).strip().lower()
        if by_crit and cid not in by_crit:
            problems.append(f"{cid}: not a criterion of this posting")
            continue
        if strength and strength not in STRENGTHS:
            problems.append(f"{cid}: {strength!r} is not a strength "
                            f"({' | '.join(STRENGTHS)})")
        if strength and not observed:
            problems.append(f"{cid}: a strength with nothing observed. Write what you saw "
                            f"first -- that is the only part anyone else can check.")
        c = by_crit.get(cid)
        if observed and _is_rubric(observed, *(anchors.get(cid, [])),
                                   *([c.looks_like, c.source_quote, c.question] if c else [])):
            problems.append(f"{cid}: the observation repeats the rubric. Say what the "
                            f"candidate did, not what a strong answer contains.")
        hits = _hits(observed, PROTECTED)
        if hits:
            problems.append(f"{cid}: the observation touches a protected subject "
                            f"({', '.join(hits)}) -- it cannot go on the record")
        entries.append(Entry(cid, str(e.get("paper", "")), str(e.get("why_open", "")),
                             observed, strength))

    if not any(e.observed for e in entries) and not problems:
        problems.append("nothing was observed on this card. An empty card recorded as a "
                        "trial day would say the day happened and saw nothing -- fill it "
                        "in, or do not record it.")
    for w in DECISION_WORDS:
        if w in str(raw.get("note", "")).lower():
            problems.append(f"note: {w!r} records a decision. The card records what was "
                            f"seen; the decision is made, and written, elsewhere.")
    if problems:
        raise CardError(problems)
    head = {"candidate": raw.get("candidate", ""), "posting": raw.get("posting", ""),
            "impression": impression, "note": str(raw.get("note", "")).strip()}
    return head, entries


def outcome(s: Screening, entries: list[Entry], *, by: str,
            reasons: dict[str, str] | None = None) -> dict[str, Any]:
    """The day against the paper, one row per criterion, for `calibrate`.

    The paper side comes from the screening record, not from the card: a card
    is a text file somebody edited, and the comparison is only worth anything
    if the half it is compared against cannot have been.
    """
    reasons = reasons or {}
    by_entry = {e.criterion_id: e for e in entries}
    rows = []
    for a in s.assessments:
        e = by_entry.get(a.criterion_id)
        rows.append({
            "criterion_id": a.criterion_id,
            "weight": a.weight,
            "hard": a.hard,
            "paper": a.strength,
            "capped_from": a.capped_from,
            "rests_on": a.rests_on,
            "why_open": reasons.get(a.criterion_id, ""),
            "day": (e.strength or None) if e else None,
            "observed": e.observed if e else "",
        })
    return {"candidate_id": s.candidate_id, "posting_id": s.posting_id, "by": by,
            "at": datetime.now(timezone.utc).isoformat(), "decides": False, "rows": rows}


# --------------------------------------------------------------------------
# Calibration: the day as ground truth for the paper
# --------------------------------------------------------------------------

def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% interval for a proportion. Honest at small n, where k/n is not."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


@dataclass
class Rate:
    label: str
    k: int
    n: int

    @property
    def enough(self) -> bool:
        return self.n >= MIN_PAIRS

    def render(self) -> str:
        if self.n == 0:
            return f"  {self.label:<52} no pairs yet"
        lo, hi = wilson(self.k, self.n)
        tail = (f"{self.k / self.n:>5.0%}  [{lo:.0%}-{hi:.0%}]" if self.enough
                else f"  --   (n below {MIN_PAIRS}: a count, not a rate)")
        return f"  {self.label:<52} {self.k:>3}/{self.n:<3} {tail}"


@dataclass
class Calibration:
    posting_id: str
    days: int
    rates: list[Rate]
    #: paper strength -> day strength -> count
    table: dict[str, dict[str, int]]
    examples: dict[str, list[str]]

    def to_dict(self) -> dict[str, Any]:
        return {"posting_id": self.posting_id, "days": self.days,
                "min_pairs": MIN_PAIRS,
                "rates": [{**asdict(r), "interval": wilson(r.k, r.n) if r.n else None}
                          for r in self.rates],
                "table": self.table, "examples": self.examples}

    def render(self) -> str:
        lines = [f"{self.posting_id} -- the trial day against the paper, "
                 f"{self.days} day(s) recorded", ""]
        if not self.days:
            lines += [
                "  No trial day has been recorded yet, so there is nothing to compare.",
                "",
                "  This is the one measurement the repository cannot make on its own.",
                "  Every other harness here measures consistency -- the screener against",
                "  itself, one name against another. Only a day with a person in it says",
                "  whether the paper was right.",
            ]
            return "\n".join(lines)
        cols = ("strong", "moderate", "weak", "unknown")
        corner = "paper \\ day"  # outside the braces: Python 3.11 has no PEP 701
        lines.append(f"  {corner:<14}" + "".join(f"{c:>10}" for c in cols))
        for paper in cols:
            row = self.table.get(paper, {})
            lines.append(f"  {paper:<14}" + "".join(f"{row.get(c, 0):>10}" for c in cols))
        lines.append("")
        for r in self.rates:
            lines.append(r.render())
        for label, ids in self.examples.items():
            if ids:
                lines.append("")
                lines.append(f"  {label}:")
                for x in ids[:8]:
                    lines.append(f"    {x}")
        lines += [
            "",
            "  'unknown' on the day means the day did not show it either. Those rows",
            "  are counted in the table and left out of every rate: an absence of a",
            "  reading agrees or disagrees with nothing.",
        ]
        return "\n".join(lines)


def calibrate(posting_id: str, outcomes: list[dict[str, Any]]) -> Calibration:
    """Where the day and the paper agreed, and which way they did not.

    Four questions, each a rate with an interval:

    * **agreement** -- where both gave a reading, the same one, and within a
      step;
    * **the paper overclaimed** -- it established a criterion on an occasion
      and the day saw less. The failure that costs a company a day;
    * **the cap cost something real** -- the assertion or letter cap knocked a
      criterion down, and the day saw at least what the screener first said.
      The failure that costs a candidate the day. The cap was set by one
      persona's CV; this is the first place it can be shown too harsh;
    * **the paper missed it** -- silent on paper, and the day saw it. What
      reading documents cannot reach at all.
    """
    table: dict[str, dict[str, int]] = {}
    agree = within = both = 0
    over_k = over_n = cap_k = cap_n = miss_k = miss_n = 0
    examples: dict[str, list[str]] = {"the paper overclaimed": [],
                                      "the cap cost something the day found": [],
                                      "the paper missed it": []}
    for o in outcomes:
        for r in o.get("rows", []):
            day = r.get("day")
            if not day:
                continue
            paper = r["paper"]
            table.setdefault(paper, {}).setdefault(day, 0)
            table[paper][day] += 1
            tag = f"{o['candidate_id']}/{r['criterion_id']}: paper {paper}, day {day}"

            if paper in LADDER and day in LADDER:
                both += 1
                agree += paper == day
                within += abs(LADDER.index(paper) - LADDER.index(day)) <= 1
                if paper in ("strong", "moderate") and r.get("rests_on") == "instances":
                    over_n += 1
                    if LADDER.index(day) < LADDER.index(paper):
                        over_k += 1
                        examples["the paper overclaimed"].append(tag)
            if r.get("capped_from") and day in LADDER:
                cap_n += 1
                if LADDER.index(day) >= LADDER.index(r["capped_from"]):
                    cap_k += 1
                    examples["the cap cost something the day found"].append(
                        f"{tag} (capped from {r['capped_from']})")
            if paper == "unknown" and day != "unknown":
                miss_n += 1
                if day in ("strong", "moderate"):
                    miss_k += 1
                    examples["the paper missed it"].append(tag)

    return Calibration(
        posting_id=posting_id,
        days=len(outcomes),
        rates=[
            Rate("same reading, where both gave one", agree, both),
            Rate("within one step, where both gave one", within, both),
            Rate("the paper overclaimed (established, day saw less)", over_k, over_n),
            Rate("the cap cost something the day found real", cap_k, cap_n),
            Rate("silent on paper, the day saw it (moderate+)", miss_k, miss_n),
        ],
        table=table,
        examples=examples,
    )


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

def _design_path(posting: str) -> Path:
    return ROOT / "runs" / "trial" / f"design_{posting}.json"


def _card_path(candidate: str, posting: str) -> Path:
    return ROOT / "runs" / "trial" / "cards" / f"{candidate}_{posting}.toml"


def _outcome_path(candidate: str, posting: str) -> Path:
    return ROOT / "runs" / "trial" / "outcomes" / f"{candidate}_{posting}.json"


def _wavered(posting: str, candidate: str) -> dict[str, dict[str, int]]:
    """Criteria the screener answered differently on identical input."""
    from triage import _drift_record
    f = _drift_record(posting)
    if not f:
        return {}
    for c in json.loads(f.read_text(encoding="utf-8")).get("candidates", []):
        if c.get("candidate_id") == candidate:
            return {cid: pc.get("seen", {}) for cid, pc in c.get("per_criterion", {}).items()
                    if not pc.get("stable", True)}
    return {}


def _load_brief(candidate: str, posting: str, facts_dir: str,
                slot: int | None = None) -> Brief | None:
    from intake.cv import load as load_facts
    from intake.posting import load as load_agenda
    from screen import load as load_screening
    from triage import _agenda_path, _screening_path, _spreads

    f = _screening_path(candidate, posting)
    if not f.exists():
        print(f"no screening for {candidate} against {posting} -- "
              f"`triage.py score` first", file=sys.stderr)
        return None
    agenda_file = _agenda_path(posting)
    facts_file = ROOT / facts_dir / f"facts_{candidate}.json"
    design_file = _design_path(posting)
    exchange_file = ROOT / "runs" / f"exchange_{candidate}.json"
    return build(
        load_screening(f),
        load_agenda(agenda_file) if agenda_file.exists() else None,
        load_facts(facts_file) if facts_file.exists() else None,
        design=load_design(design_file) if design_file.exists() else None,
        wavered=_wavered(posting, candidate),
        spread=_spreads(posting).get(candidate),
        exchange=(json.loads(exchange_file.read_text(encoding="utf-8"))
                  if exchange_file.exists() else None),
        slot_minutes=slot,
    )


def _cmd_design(posting: str, day: int, slot: int, again: bool,
                reverify: bool = False) -> int:
    from intake.posting import load as load_agenda
    from triage import _agenda_path

    target = _design_path(posting)
    if reverify:
        if not target.exists():
            print(f"nothing to re-verify for {posting!r}", file=sys.stderr)
            return 2
        old = load_design(target)
        d = verify(load_agenda(_agenda_path(posting)), answer_of(old),
                   day_minutes=old.day_minutes, slot_minutes=old.slot_minutes)
        d.model, d.designed_at = old.model, old.designed_at
        save_design(d, target)
        print(d.render())
        print(f"\nre-verified against the current rules, no model call: "
              f"{target.relative_to(ROOT)}")
        return 0
    if target.exists() and not again:
        print(load_design(target).render())
        print(f"\n(already designed: {target.relative_to(ROOT)} -- --again to redo)")
        return 0
    agenda_file = _agenda_path(posting)
    if not agenda_file.exists():
        print(f"no agenda for {posting!r}", file=sys.stderr)
        return 2
    from nbh.llm import AGENT_MODEL, LLMError, default_client
    try:
        client = default_client()
        d = design(client, load_agenda(agenda_file), model=AGENT_MODEL,
                   day_minutes=day, slot_minutes=slot)
    except (LLMError, ValueError) as e:
        print(f"\n{e}\n", file=sys.stderr)
        return 2
    save_design(d, target)
    print(d.render())
    print(f"\nwritten: {target.relative_to(ROOT)}")
    return 0


def _cmd_brief(candidate: str, posting: str, facts_dir: str, slot: int | None,
               as_json: bool) -> int:
    b = _load_brief(candidate, posting, facts_dir, slot)
    if b is None:
        return 2
    print(json.dumps(b.to_dict(), indent=2, ensure_ascii=False) if as_json else b.render())
    return 0


def _cmd_card(candidate: str, posting: str, facts_dir: str) -> int:
    b = _load_brief(candidate, posting, facts_dir)
    if b is None:
        return 2
    target = _card_path(candidate, posting)
    if target.exists():
        print(f"a card already exists: {target.relative_to(ROOT)} -- not overwriting "
              f"what somebody may have started filling in", file=sys.stderr)
        return 2
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(card(b), encoding="utf-8")
    print(f"written: {target.relative_to(ROOT)}")
    return 0


def _cmd_record(card_file: str, by: str, facts_dir: str) -> int:
    import human
    from intake.posting import load as load_agenda
    from pipeline import Event
    from screen import load as load_screening
    from triage import (_agenda_path, _open_pipeline, _pipeline_path, _screening_path,
                        save_pipeline)

    if not by.strip():
        print("--by is required: a trial day is something a person observed", file=sys.stderr)
        return 2
    text = Path(card_file).read_text(encoding="utf-8")
    try:
        head = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        print(f"the card is not valid TOML, nothing was written: {e}", file=sys.stderr)
        return 2
    candidate, posting = head.get("candidate", ""), head.get("posting", "")
    sf = _screening_path(candidate, posting)
    if not sf.exists():
        print(f"no screening for {candidate} against {posting}", file=sys.stderr)
        return 2
    agenda_file = _agenda_path(posting)
    design_file = _design_path(posting)
    try:
        head, entries = read_card(
            text, load_agenda(agenda_file) if agenda_file.exists() else None,
            load_design(design_file) if design_file.exists() else None)
    except CardError as e:
        print("the card was not recorded -- nothing was written:", file=sys.stderr)
        for p in e.problems:
            print(f"  {p}", file=sys.stderr)
        return 2

    s = load_screening(sf)
    overrides = [human.Override(e.criterion_id, e.strength, f"trial day: {e.observed}", by)
                 for e in entries if e.strength]
    review = human.Review(
        candidate_id=candidate, posting_id=posting, reviewer=by,
        impression=(head["impression"] / 100 if head["impression"] is not None else None),
        note=head["note"] or "trial day", overrides=overrides)
    reviewed = human.apply(s, review)
    human.save(reviewed, ROOT / "runs" / "reviews", f"{candidate}_{posting}_trial_day")

    b = _load_brief(candidate, posting, facts_dir)
    reasons = {p.criterion_id: p.why for p in b.probes} if b else {}
    out = outcome(s, entries, by=by, reasons=reasons)
    target = _outcome_path(candidate, posting)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    pipe = _open_pipeline(posting)
    pipe.add(Event(candidate_id=candidate, kind="met", by=by,
                   detail={"round": "trial_day",
                           "observed": sum(1 for e in entries if e.strength)}))
    save_pipeline(pipe, _pipeline_path(posting))

    print(reviewed.render())
    print()
    seen = sum(1 for e in entries if e.strength and e.strength != "unknown")
    print(f"  recorded under {by}: {seen} criteria read on the day, "
          f"{len(s.assessments) - seen} left as the paper had them")
    print(f"  written: {target.relative_to(ROOT)}")
    return 0


def _cmd_calibrate(posting: str, as_json: bool) -> int:
    d = ROOT / "runs" / "trial" / "outcomes"
    outs = [json.loads(f.read_text(encoding="utf-8"))
            for f in sorted(d.glob(f"*_{posting}.json"))] if d.exists() else []
    c = calibrate(posting, outs)
    print(json.dumps(c.to_dict(), indent=2, ensure_ascii=False) if as_json else c.render())
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="trial", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    de = sub.add_parser("design", help="the day's exercises, once per posting (one call)")
    de.add_argument("--posting", required=True)
    de.add_argument("--day", type=int, default=DEFAULT_DAY, help="minutes in the day")
    de.add_argument("--slot", type=int, default=DEFAULT_SLOT,
                    help="minutes of per-candidate conversation")
    de.add_argument("--again", action="store_true")
    de.add_argument("--reverify", action="store_true",
                    help="re-apply the current rules to the recorded answer (free)")

    br = sub.add_parser("brief", help="where to look, for one candidate (free)")
    br.add_argument("--candidate", required=True)
    br.add_argument("--posting", required=True)
    br.add_argument("--facts-dir", default="runs/facts")
    br.add_argument("--slot", type=int, default=None)
    br.add_argument("--json", action="store_true")

    ca = sub.add_parser("card", help="the card the observers fill in (free)")
    ca.add_argument("--candidate", required=True)
    ca.add_argument("--posting", required=True)
    ca.add_argument("--facts-dir", default="runs/facts")

    re_ = sub.add_parser("record", help="a filled card, onto the record (free)")
    re_.add_argument("--card", required=True)
    re_.add_argument("--by", required=True, help="who observed")
    re_.add_argument("--facts-dir", default="runs/facts")

    cb = sub.add_parser("calibrate", help="the day against the paper (free)")
    cb.add_argument("--posting", required=True)
    cb.add_argument("--json", action="store_true")

    a = ap.parse_args()
    if a.cmd == "design":
        return _cmd_design(a.posting, a.day, a.slot, a.again, a.reverify)
    if a.cmd == "brief":
        return _cmd_brief(a.candidate, a.posting, a.facts_dir, a.slot, a.json)
    if a.cmd == "card":
        return _cmd_card(a.candidate, a.posting, a.facts_dir)
    if a.cmd == "record":
        return _cmd_record(a.card, a.by, a.facts_dir)
    return _cmd_calibrate(a.posting, a.json)


if __name__ == "__main__":
    raise SystemExit(main())
