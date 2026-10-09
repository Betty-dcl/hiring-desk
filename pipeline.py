"""Where each application stands, and who last touched it.

Thirty applications is not a volume problem, it is a memory problem. Nobody
loses a candidate because the screener mis-ranked them; they lose one because
a founder read it on a Tuesday, meant to reply, and three weeks passed. The
promise on the careers page -- that every application is read and none
disappears -- is not broken by bad judgement. It is broken by silence.

So this module keeps no opinion and only a record.

**Events, not a status field.** Nothing here is edited in place. Every
movement is an event appended to a log, and a candidate's state is *derived*
by replaying their events. That is not ceremony: a status column tells you a
candidate is "contacted" and nothing else, while a log tells you who
contacted them, when, and what the three states before that were. A question
asked six weeks later -- "why did we stop talking to her?" -- has an answer
that is a file rather than a memory.

**The machine cannot end anything.** The scoring code has no reject state by
design, and this module does not quietly reintroduce one. `closed` is a
human-only event: it requires a named person and a written reason, and the
code refuses it without them. The system records that somebody decided; it
never decides. Same for `read`, `flagged`, `contacted` and `met` -- a machine
claiming a human read something is the exact lie this module exists to
prevent.

**Silence is the thing being measured.** `stale` is the whole point of the
file. An application nobody has touched in N days is the failure mode their
own careers page promises against, and it is invisible in every tracker that
only shows states.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

#: What can happen to an application. Ordered roughly as they occur, though
#: nothing enforces an order: real processes go backwards.
KINDS = (
    "received",    # the application arrived
    "screened",    # the machine scored it against an agenda
    "read",        # a named person actually read it
    "voted",       # a named person classed it: detail={"label", "until"?, "comment"?}
    "go_ahead",    # a voter moved on without waiting for the others: detail={"missing"}
    "noted",       # a named person wrote a note for the others: detail={"text"}
    "flagged",     # a person marked it as standing out, with a reason
    "tagged",      # a free label, added by a person or by an import
    "untagged",
    "contacted",   # somebody wrote to the candidate
    "replied",     # the candidate answered
    "met",         # a conversation happened, with a round: detail={"round", "on"?}
    "offered",     # a person made an offer: detail={"on"?}
    "closed",      # a person ended it, with a reason
    "reopened",
    "revisit",     # a person parked it until a date, with a reason
)

#: Events a machine may never emit. Each of these asserts that a human did
#: something, and the only thing worse than no record is a false one.
HUMAN_ONLY = ("read", "voted", "go_ahead", "noted", "flagged", "contacted", "met", "offered", "closed",
              "reopened", "revisit")

#: Events that are meaningless without a written reason.
NEEDS_REASON = ("flagged", "closed", "reopened", "revisit")

#: The states a candidate can be in, worst-to-best for a reader deciding
#: where to spend the next hour. Derived, never stored.
STATES = ("closed", "revisit", "offered", "met", "replied", "contacted", "flagged", "voted", "read",
          "screened", "new")


def after(events: list["Event"], last: "Event", kinds: tuple[str, ...]) -> bool:
    """Does an event of one of `kinds` come after `last` in the log?

    By order, not by time: two events written in the same tick of the clock
    (about every 15 ms on Windows before Python 3.13) carry the same timestamp,
    and a reply recorded right after the mail would read as written before it.
    """
    for i in range(len(events) - 1, -1, -1):
        if events[i] is last:
            return any(e.kind in kinds for e in events[i + 1:])
    return False


class PipelineError(ValueError):
    """A movement the record refuses to hold."""


@dataclass
class Event:
    """One thing that happened to one application."""

    candidate_id: str
    kind: str
    at: str = ""
    #: The person responsible. Required for anything in HUMAN_ONLY.
    by: str = ""
    reason: str = ""
    tag: str = ""
    #: Free-form, for what a kind needs and the others do not: the score a
    #: `screened` event recorded, the round number of a `met`.
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise PipelineError(f"unknown event {self.kind!r}. Known: {', '.join(KINDS)}")
        if self.kind in HUMAN_ONLY and not self.by.strip():
            raise PipelineError(
                f"{self.kind!r} says a person did something, so it needs `by`. "
                f"The machine does not get to claim it."
            )
        if self.kind in NEEDS_REASON and not self.reason.strip():
            raise PipelineError(f"{self.kind!r} without a reason is not a record, it is a shrug")
        if self.kind in ("tagged", "untagged") and not self.tag.strip():
            raise PipelineError(f"{self.kind!r} needs a tag")
        if self.kind == "noted" and not str(self.detail.get("text", "")).strip():
            raise PipelineError("'noted' needs detail={'text': ...}")
        if self.kind == "voted" and not str(self.detail.get("label", "")).strip():
            raise PipelineError("'voted' needs detail={'label': ...} -- a vote for nothing "
                                "is not a vote")
        if self.kind == "revisit":
            #: "Interesting in six months" is the intention everyone has and
            #: nobody keeps, because it is written as a label and a label is
            #: never read again. Here it is a date, and the board wakes it.
            when = str(self.detail.get("on", "")).strip()
            if not when:
                raise PipelineError(
                    "'revisit' needs detail={'on': '<ISO date>'} -- parking someone "
                    "without a date is how people get forgotten"
                )
            try:
                datetime.fromisoformat(when)
            except ValueError as e:
                raise PipelineError(f"revisit date {when!r} is not a date") from e
        self.at = self.at or datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def when(self) -> datetime:
        #: A hand-edited log or an old export writes "2026-09-10T10:00:00".
        #: Compared with an aware `now` it raised, and took the whole recap
        #: and every card down with it. Read as UTC, like a bare revisit date:
        #: "applied 7 days ago" can be a few hours off, never a crash. The
        #: Tracking tab (metrics.instant) is stricter and does not guess.
        when = datetime.fromisoformat(self.at)
        return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


@dataclass
class Standing:
    """One candidate's position, derived from their events."""

    candidate_id: str
    events: list[Event] = field(default_factory=list)

    @property
    def state(self) -> str:
        """The furthest thing that has happened, with `closed` winning.

        Deliberately not a stack of stages to advance through: an application
        that was contacted and then closed is closed, and one that was closed
        and reopened is back to whatever it had reached.
        """
        kinds = [e.kind for e in self.events]
        if not kinds:
            return "new"
        last_close = max((i for i, k in enumerate(kinds) if k == "closed"), default=-1)
        last_open = max((i for i, k in enumerate(kinds) if k == "reopened"), default=-1)
        if last_close > last_open:
            return "closed"
        seen = set(kinds[last_open + 1:] if last_open >= 0 else kinds)
        #: Parked and then acted on is not parked. `revisit_on` already holds
        #: that rule, and the state must not contradict it.
        if "revisit" in seen and self.revisit_on is None:
            seen.discard("revisit")
        for s in STATES:
            if s in seen:
                return s
        return "new"

    @property
    def tags(self) -> list[str]:
        out: list[str] = []
        for e in self.events:
            if e.kind == "tagged" and e.tag not in out:
                out.append(e.tag)
            elif e.kind == "untagged" and e.tag in out:
                out.remove(e.tag)
        return out

    @property
    def score(self) -> float | None:
        """The most recent score recorded by a `screened` event."""
        for e in reversed(self.events):
            if e.kind == "screened" and "score" in e.detail:
                return float(e.detail["score"])
        return None

    @property
    def coverage(self) -> float | None:
        """The share of the criteria the paper spoke to at all, when recorded."""
        for e in reversed(self.events):
            if e.kind == "screened" and "score" in e.detail:
                return float(e.detail["coverage"]) if "coverage" in e.detail else None
        return None

    @property
    def guess(self) -> tuple[float, float, float] | None:
        """The score as a range: (floor, middle, ceiling).

        `score` counts a criterion the paper is silent on as zero. Silence is
        not absence: a CV is not written against the posting. So the score is
        the floor (everything unsaid is missing), the ceiling adds the silent
        weight in full (everything unsaid is there), and the middle gives the
        silent weight half credit -- the neutral value when nothing is known.
        With full coverage the three are the same number.
        """
        score = self.score
        if score is None:
            return None
        silent = max(0.0, 1.0 - (self.coverage if self.coverage is not None else 1.0))
        high = min(1.0, score + silent)
        return score, min(1.0, score + silent / 2), high

    @property
    def spread(self) -> float | None:
        """The measured noise around that score, when it was measured.

        A score whose interval is unknown is reported as unknown rather than
        as zero: `harness/screener.py` exists because that interval is often
        wider than the gaps people read meaning into.
        """
        for e in reversed(self.events):
            if e.kind == "screened" and "spread" in e.detail:
                return float(e.detail["spread"])
        return None

    @property
    def revisit_on(self) -> datetime | None:
        """When this application asked to be looked at again.

        Only counts if it is still the last word: somebody who was parked and
        then contacted is not parked any more.
        """
        for e in reversed(self.events):
            if e.kind == "revisit":
                #: A person types "2027-03-01", not an instant. A bare date is
                #: read as UTC midnight so it can be compared with anything
                #: else in this file.
                when = datetime.fromisoformat(str(e.detail["on"]))
                return when if when.tzinfo else when.replace(tzinfo=timezone.utc)
            if e.kind in ("contacted", "met", "offered", "closed", "replied"):
                return None
        return None

    def revisit_due(self, now: datetime | None = None) -> bool:
        when = self.revisit_on
        return bool(when and when <= (now or datetime.now(timezone.utc)))

    @property
    def read_by(self) -> list[str]:
        #: Somebody who voted on an application has read it; listing them as
        #: not having done so would make the note to the candidate say "a
        #: person has not read this yet" after three people did.
        out: list[str] = []
        for e in self.events:
            if e.kind in ("read", "voted") and e.by not in out:
                out.append(e.by)
        return out

    @property
    def seen(self) -> bool:
        return bool(self.read_by)

    @property
    def last_touch(self) -> datetime | None:
        return max((e.when for e in self.events), default=None)

    def idle_days(self, now: datetime | None = None) -> float | None:
        last = self.last_touch
        if last is None:
            return None
        return round(((now or datetime.now(timezone.utc)) - last).total_seconds() / 86400, 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "state": self.state,
            "tags": self.tags,
            "score": self.score,
            "spread": self.spread,
            "seen": self.seen,
            "revisit_on": self.revisit_on.isoformat() if self.revisit_on else None,
            "read_by": self.read_by,
            "last_touch": self.last_touch.isoformat() if self.last_touch else None,
            "idle_days": self.idle_days(),
            "events": [e.to_dict() for e in self.events],
        }


@dataclass
class Pipeline:
    """Every application for one posting, and everything that happened to them."""

    posting_id: str
    events: list[Event] = field(default_factory=list)
    #: An application untouched for longer than this is surfaced. Their own
    #: promise is the reason there is a number here at all.
    stale_after_days: float = 7.0

    def add(self, event: Event) -> Event:
        self.events.append(event)
        return event

    def record(self, candidate_id: str, kind: str, **kw: Any) -> Event:
        return self.add(Event(candidate_id=candidate_id, kind=kind, **kw))

    def standing(self, candidate_id: str) -> Standing:
        return Standing(candidate_id,
                        sorted((e for e in self.events if e.candidate_id == candidate_id),
                               key=lambda e: e.at))

    @property
    def candidates(self) -> list[str]:
        out: list[str] = []
        for e in self.events:
            if e.candidate_id not in out:
                out.append(e.candidate_id)
        return out

    def board(self, now: datetime | None = None) -> list[Standing]:
        """Everyone, ordered by where a reader's next hour is best spent.

        Open before closed; unread before read, because an unread application
        is the one the promise is about; then by score, with unscored last.
        """
        def key(s: Standing) -> tuple:
            return (s.state == "closed", s.seen, -(s.score if s.score is not None else -1))
        return sorted((self.standing(c) for c in self.candidates), key=key)

    def stale(self, now: datetime | None = None) -> list[Standing]:
        """Open applications nobody has touched in `stale_after_days`."""
        now = now or datetime.now(timezone.utc)
        out = [s for s in self.board() if s.state != "closed"
               and (s.idle_days(now) or 0) >= self.stale_after_days
               #: Somebody parked until March is not forgotten, they are
               #: waiting. They come back through `due`, on their date.
               and not (s.revisit_on and not s.revisit_due(now))]
        return sorted(out, key=lambda s: -(s.idle_days(now) or 0))

    def due(self, now: datetime | None = None) -> list[Standing]:
        """Parked applications whose date has arrived."""
        return [s for s in self.board() if s.state != "closed" and s.revisit_due(now)]

    def by_state(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {s: [] for s in STATES}
        for s in self.board():
            out[s.state].append(s.candidate_id)
        return {k: v for k, v in out.items() if v}

    def by_tag(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for s in self.board():
            for t in s.tags:
                out.setdefault(t, []).append(s.candidate_id)
        return out

    def render(self, now: datetime | None = None) -> str:
        now = now or datetime.now(timezone.utc)
        lines = [f"{self.posting_id} -- {len(self.candidates)} applications", ""]
        lines.append(f"  {'candidate':<20} {'state':<10} {'score':>10} {'idle':>6}  "
                     f"{'read by':<14} tags")
        for s in self.board():
            score = ("--" if s.score is None
                     else f"{s.score:.0%}" + (f" +/-{s.spread:.0%}" if s.spread else ""))
            idle = s.idle_days(now)
            mark = "!" if (idle or 0) >= self.stale_after_days and s.state != "closed" else " "
            lines.append(f" {mark}{s.candidate_id:<20} {s.state:<10} {score:>10} "
                         f"{(f'{idle:.0f}d' if idle is not None else '--'):>6}  "
                         f"{(', '.join(s.read_by) or '--'):<14} {', '.join(s.tags)}")

        counts = {k: len(v) for k, v in self.by_state().items()}
        lines.append("")
        lines.append("  " + "  ".join(f"{k}: {n}" for k, n in counts.items()))

        due = self.due(now)
        if due:
            lines.append("")
            lines.append(f"  {len(due)} application(s) you parked, now due:")
            for s in due:
                when = s.revisit_on
                lines.append(f"    {s.candidate_id} -- parked until "
                             f"{when.date().isoformat() if when else '?'}")

        late = self.stale(now)
        lines.append("")
        if late:
            lines.append(f"  {len(late)} application(s) nobody has touched in "
                         f"{self.stale_after_days:g} days:")
            for s in late:
                lines.append(f"    {s.candidate_id} -- {s.idle_days(now):.0f} days, "
                             f"state {s.state}")
        else:
            lines.append(f"  nothing has been sitting untouched for "
                         f"{self.stale_after_days:g} days")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "posting_id": self.posting_id,
            "stale_after_days": self.stale_after_days,
            "standings": [s.to_dict() for s in self.board()],
        }


def save(p: Pipeline, path: str | Path) -> Path:
    """Append-only on disk too: the log is the file, the board is derived."""
    f = Path(path)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(
        json.dumps({"posting_id": p.posting_id,
                    "stale_after_days": p.stale_after_days,
                    "events": [e.to_dict() for e in p.events]},
                   indent=2, ensure_ascii=False),
        encoding="utf-8")
    return f


def load(path: str | Path) -> Pipeline:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    p = Pipeline(posting_id=d["posting_id"],
                 stale_after_days=d.get("stale_after_days", 7.0))
    for e in d.get("events", []):
        p.add(Event(**e))
    return p


def from_screenings(posting_id: str, screenings: Iterable[Any],
                    spreads: dict[str, float] | None = None) -> Pipeline:
    """Seed a pipeline from screenings already on disk.

    Each becomes `received` then `screened`, carrying the score and, when the
    stability harness has measured one, its interval.
    """
    p = Pipeline(posting_id=posting_id)
    spreads = spreads or {}
    for s in screenings:
        p.record(s.candidate_id, "received", at=s.screened_at)
        detail: dict[str, Any] = {"score": s.score, "coverage": s.coverage}
        if s.candidate_id in spreads:
            detail["spread"] = spreads[s.candidate_id]
        p.record(s.candidate_id, "screened", at=s.screened_at, detail=detail)
    return p
