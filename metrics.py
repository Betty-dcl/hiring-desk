#!/usr/bin/env python3
"""Is the promise kept? Measured from the log, never from a model.

The careers page says every application is read and nobody is left without
an answer. Every other file in this repository asks whether a number can be
trusted; this one asks whether a *sentence* can be. It reads only what the
desk already writes -- the event log in `runs/pipeline/`, the people in
`runs/desk/people.json` -- and turns it into four things a partner can check
in a minute:

    python metrics.py report                  # the promise, the funnel, the agreement
    python metrics.py report --json           # the same, for a script

**The promise.** From an application arriving to the first message a person
sent back: median, 90th percentile, the longest, and the share answered
within N days -- with the names of everyone past N, because a percentage is
where the unanswered hide. Two corrections a naive average gets wrong:

* *Still waiting is not missing.* Somebody who applied three days ago and has
  no answer yet is not a failure and not a success. The share within N days
  is taken over applications at least N days old (the cohort that *could*
  have been answered in time), and the median is also given as a
  Kaplan-Meier estimate, which counts the ones still waiting as "at least
  this long" instead of dropping them -- dropping them makes the desk look
  faster exactly when it is falling behind.
* *Unknown is an answer.* A timestamp without a time zone, a missing
  `received`, a reply dated before the application: each makes that one delay
  "unknown", counted and shown, never guessed.

**The funnel.** Received, fully voted, then the states a person records
(stages.py): To contact, Contacted, First call, Trial day, Offer, Hired -- by
role and by where the application came from, raw counts beside every
percentage, and no percentage at all under a set minimum.

**Agreement between voters.** For each pair, the table of who said what when
the other said what, raw agreement, and Cohen's kappa with a bootstrap
interval; Fleiss' kappa for three or more. It is computed on each voter's
*first* vote by default: under the blind rule that is the one cast before
seeing the others, and a vote changed afterwards measures persuasion, not
the bar. The point is to show where the team does not hold the same bar --
which pair, on which classes -- and it never orders people: the pairs come
in the order `desk.toml` lists the voters, and nothing is sorted by score.

**Time to class.** How long a vote takes, estimated from the gaps between
one voter's consecutive votes in a sitting. Pooled over the team, so it
estimates the task and not a person. Too few observations, and the recap
says it is using the assumption in `desk.toml` instead.

Everything here is calendar time, in UTC, from the timestamps in the log.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import desk  # noqa: E402
import stages  # noqa: E402
from desk import MEANINGS, Config, DeskError, Registry, Vote, outcome  # noqa: E402
from pipeline import Event, Pipeline  # noqa: E402

DAY = 86400.0

#: The steps of the funnel, in order: arrived, voted on by everyone, then the
#: states of stages.py from the votes' "to contact" to a hire. A planned trial
#: day is not one that happened, and is not counted until its day.
STAGES = ("received", "voted") + stages.PATH[2:]


def stages_for(cfg: Config) -> tuple[str, ...]:
    """The funnel as this team runs it: with its own number of interviews."""
    return ("received", "voted") + stages.path(cfg)[2:]


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

@dataclass
class Tracking:
    """`[tracking]` in desk.toml. Each value is a preference, not a fact."""

    #: The promise, in days: "you will hear from us within N days".
    answer_within_days: float = 14
    #: Below this many, a share is not printed as a percentage.
    min_n_percent: int = 5
    #: Below this many applications rated by a pair, agreement carries a warning.
    min_n_agreement: int = 10
    #: Whether the "we have your application" acknowledgment counts as an answer.
    acknowledgment_counts: bool = False
    #: A timestamp with no time zone: "unknown" (the delay is not computed) or
    #: "utc" (read as UTC).
    naive_timestamps: str = "unknown"
    #: Which vote measures the bar: "first" (cast blind) or "latest".
    agreement_votes: str = "first"
    bootstrap: int = 2000
    confidence: float = 0.95
    #: Used for the recap's time estimate until enough votes have been timed.
    minutes_per_vote: float = 1.5
    #: Two votes further apart than this are two sittings, not one.
    session_gap_minutes: float = 20
    min_timed_votes: int = 5
    #: How long a vote link in the recap stays valid.
    link_ttl_hours: float = 168
    #: Where the desk is reached from the recap's links.
    base_url: str = "http://127.0.0.1:8765"

    def __post_init__(self) -> None:
        problems = []
        if self.answer_within_days <= 0:
            problems.append("answer_within_days must be positive")
        if self.min_n_percent < 1 or self.min_n_agreement < 2:
            problems.append("min_n_percent must be >= 1 and min_n_agreement >= 2")
        if self.naive_timestamps not in ("unknown", "utc"):
            problems.append("naive_timestamps must be 'unknown' or 'utc'")
        if self.agreement_votes not in ("first", "latest"):
            problems.append("agreement_votes must be 'first' or 'latest'")
        if not 0.5 <= self.confidence < 1 or self.bootstrap < 100:
            problems.append("confidence must be in [0.5, 1) and bootstrap >= 100")
        if self.minutes_per_vote <= 0 or self.session_gap_minutes <= 0:
            problems.append("minutes_per_vote and session_gap_minutes must be positive")
        if self.link_ttl_hours <= 0:
            problems.append("link_ttl_hours must be positive")
        if problems:
            raise DeskError("desk.toml [tracking]: " + "; ".join(problems))


def load_tracking(path: str | Path | None = None) -> Tracking:
    f = Path(path or desk.ROOT / "desk.toml")
    raw = tomllib.loads(f.read_text(encoding="utf-8")).get("tracking", {}) if f.exists() else {}
    known = Tracking.__dataclass_fields__
    unknown = sorted(set(raw) - set(known))
    if unknown:
        raise DeskError(f"desk.toml [tracking]: unknown setting(s) {', '.join(unknown)}")
    return Tracking(**raw)


# --------------------------------------------------------------------------
# Time, honestly
# --------------------------------------------------------------------------

def instant(at: str, naive: str = "unknown") -> datetime | None:
    """A log timestamp as an instant in UTC, or None when it is not one.

    `2026-09-18T15:00+02:00` and `2026-09-18T13:00Z` are the same moment and
    must compare as such -- the pipeline sorts `at` as text, which is only
    right while every writer uses the same offset. A timestamp with no offset
    is not an instant: it could be any of 26 hours. It is read as UTC only
    when the setting says so.
    """
    try:
        d = datetime.fromisoformat(str(at))
    except (TypeError, ValueError):
        return None
    if d.tzinfo is None:
        return d.replace(tzinfo=timezone.utc) if naive == "utc" else None
    return d.astimezone(timezone.utc)


def days(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / DAY


def quantile(xs: list[float], q: float) -> float | None:
    """Linear interpolation between order statistics (R type 7). None if empty."""
    if not xs:
        return None
    s = sorted(xs)
    h = (len(s) - 1) * q
    lo = math.floor(h)
    return s[lo] + (h - lo) * (s[min(lo + 1, len(s) - 1)] - s[lo])


def wilson(k: int, n: int, confidence: float = 0.95) -> tuple[float, float] | None:
    """Wilson score interval for k successes in n. Honest at small n, unlike k/n +/- ..."""
    if n <= 0:
        return None
    z = _z(confidence)
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, mid - half), min(1.0, mid + half)


def _z(confidence: float) -> float:
    from statistics import NormalDist
    return NormalDist().inv_cdf(0.5 + confidence / 2)


def kaplan_meier(items: Iterable[tuple[float, bool]]) -> list[tuple[float, float]]:
    """Share still without an answer after t days: [(t, S(t))], starting at (0, 1).

    Each item is (days, answered). An item not answered is *censored*: it is
    known to have waited at least that long, and leaves the risk set there
    without counting as an answer.
    """
    data = sorted(items)
    at_risk, s, out = len(data), 1.0, [(0.0, 1.0)]
    i = 0
    while i < len(data):
        t = data[i][0]
        answered = removed = 0
        while i < len(data) and data[i][0] == t:
            answered += data[i][1]
            removed += 1
            i += 1
        if answered and at_risk:
            s *= 1 - answered / at_risk
            out.append((t, s))
        at_risk -= removed
    return out


def km_quantile(curve: list[tuple[float, float]], q: float) -> float | None:
    """The first time at which a share q has been answered; None if never reached."""
    for t, s in curve:
        if s <= 1 - q + 1e-12:
            return t
    return None


# --------------------------------------------------------------------------
# One application, as the promise sees it
# --------------------------------------------------------------------------

@dataclass
class Case:
    """One person's application for one role, with every record behind it.

    Two records confirmed as one person (`desk.py merge`) who both applied for
    the same role are one application here: one person, one promise. The
    earliest arrival starts the clock, and an answer on either record counts.
    """

    posting_id: str
    person_id: str
    name: str
    candidate_ids: list[str]
    source: str = "not recorded"
    received: datetime | None = None
    #: The first message a person sent that counts as an answer.
    answered: datetime | None = None
    #: The first message of any kind, the acknowledgment included.
    contacted: datetime | None = None
    all_voted: datetime | None = None
    agreed: datetime | None = None
    agreed_meaning: str | None = None
    first_votes: dict[str, str] = field(default_factory=dict)
    latest_votes: dict[str, Vote] = field(default_factory=dict)
    changed_votes: int = 0
    closed: bool = False
    closed_at: datetime | None = None
    met: bool = False
    trial_day: bool = False
    #: The states of stages.PATH this application has its own record of.
    steps: set[str] = field(default_factory=set)
    #: How it ended, when it did (stages.ENDED), else "".
    ended: str = ""
    #: Why a delay is unknown, when it is. Shown, never papered over.
    unknown: list[str] = field(default_factory=list)
    #: The time of the first answer cannot be known (an unreadable timestamp,
    #: or an answer dated before the arrival).
    answer_unknown: bool = False

    @property
    def key(self) -> tuple[str, str]:
        return (self.posting_id, self.person_id)

    @property
    def closed_silently(self) -> bool:
        """Ended without anyone writing to them: the silence itself.

        Unless they withdrew: a person who said "no thank you" first is not
        waiting for an answer, and counting them would blame the desk for it.
        """
        return self.closed and self.answered is None and self.ended != "withdrew"

    def settled(self, cfg: Config) -> Any:
        return outcome(self.latest_votes, cfg)


def _ashby_keys(root: Path) -> set[tuple[str, str]]:
    f = root / "runs" / "desk" / "ashby.json"
    if not f.exists():
        return set()
    try:
        apps = json.loads(f.read_text(encoding="utf-8")).get("applications", {})
    except (ValueError, OSError):
        return set()
    return {(a.get("posting", ""), a.get("person_id", "")) for a in apps.values()}


def source_of(events: list[Event], posting: str, person_id: str,
              ashby: set[tuple[str, str]]) -> str:
    """Where an application came from, from what the log and Ashby's state say.

    An explicit `source` on the arrival, or a `source:<name>` tag, wins. Then
    Ashby's own record of what it brought in. Then "by hand". Anything else is
    "not recorded" -- the five demo applications came in through the screener
    before the desk wrote a source, and inventing one would be a guess.
    """
    rec = next((e for e in events if e.kind == "received"), None)
    if rec is not None and str(rec.detail.get("source", "")).strip():
        return str(rec.detail["source"]).strip().lower()
    tag = next((e.tag for e in events if e.kind == "tagged" and e.tag.startswith("source:")),
               "")
    if tag:
        return tag.split(":", 1)[1].strip().lower() or "not recorded"
    if (posting, person_id) in ashby:
        return "ashby"
    if rec is not None and rec.detail.get("added_by_hand"):
        return "by hand"
    return "not recorded"


def _is_answer(e: Event, t: Tracking) -> bool:
    if e.kind == "met":
        return True
    if e.kind != "contacted":
        return False
    #: `mark` writes what the message was about. The acknowledgment says
    #: "you will hear from us": it is a receipt, not the answer promised.
    return t.acknowledgment_counts or e.detail.get("about") != "received"


def build_case(posting: str, person_id: str, name: str, cids: list[str],
               events: list[Event], cfg: Config, t: Tracking, source: str,
               now: datetime | None = None) -> Case:
    now = now or datetime.now(timezone.utc)
    c = Case(posting, person_id, name, list(cids), source=source)
    timed: list[tuple[datetime, int, Event]] = []
    unreadable: list[Event] = []
    for i, e in enumerate(events):
        when = instant(e.at, t.naive_timestamps)
        if when is None:
            unreadable.append(e)
        else:
            timed.append((when, i, e))
    timed.sort(key=lambda x: (x[0], x[1]))

    recs = [w for w, _, e in timed if e.kind == "received"]
    if any(e.kind == "received" for e in unreadable):
        c.unknown.append("the arrival's timestamp has no readable time zone")
    elif recs:
        c.received = min(recs)
    else:
        c.unknown.append("no arrival recorded")

    #: A step dated back by the person who recorded it ("contacted
    #: yesterday") counts from that day -- its end, so it never flatters.
    answers = [stages.instant(e) if "on" in e.detail else w
               for w, _, e in timed if _is_answer(e, t)]
    if any(_is_answer(e, t) for e in unreadable):
        c.unknown.append("a message's timestamp has no readable time zone")
        c.answer_unknown = True
    elif answers:
        c.answered = min(answers)
    contacts = [w for w, _, e in timed if e.kind == "contacted"]
    c.contacted = min(contacts) if contacts else None
    if c.received and c.answered and c.answered < c.received:
        c.unknown.append("answered before it arrived: the clocks disagree")
        c.answer_unknown = True

    # Votes, replayed in time order under the current rule.
    cur: dict[str, Vote] = {}
    vote_evs = [(w, e) for w, _, e in timed if e.kind == "voted"]
    for w, e in vote_evs:
        if e.by not in cfg.voters:
            continue
        label = e.detail["label"]
        c.first_votes.setdefault(e.by, label)
        if e.by in cur and cur[e.by].meaning != label:
            c.changed_votes += 1
        cur[e.by] = Vote(e.by, label, e.at, e.detail.get("until", ""),
                         e.detail.get("comment", ""), e.detail.get("reason", ""))
        if c.all_voted is None and all(v in cur for v in cfg.voters):
            c.all_voted = w
        o = outcome(cur, cfg)
        if c.agreed is None and o.status == "agreed":
            c.agreed, c.agreed_meaning = w, o.meaning
    if any(e.kind == "voted" for e in unreadable):
        c.unknown.append("a vote's timestamp has no readable time zone")
        c.all_voted = c.agreed = None
        for e in unreadable:  # still counted in the final tally, in file order
            if e.kind == "voted" and e.by in cfg.voters:
                c.first_votes.setdefault(e.by, e.detail["label"])
                cur[e.by] = Vote(e.by, e.detail["label"], e.at)
    c.latest_votes = cur
    o = outcome(cur, cfg)
    if o.status == "agreed":
        c.agreed_meaning = o.meaning
    elif o.status != "agreed" and c.agreed_meaning:
        # Agreed once, then someone changed their vote: the time stays on the
        # record (it happened), the meaning is what holds now.
        c.agreed_meaning = None

    state_events = [e for _, _, e in timed] + unreadable
    closes = [(w, e.kind) for w, _, e in timed if e.kind in ("closed", "reopened")]
    c.closed = bool(closes) and closes[-1][1] == "closed"
    c.closed_at = closes[-1][0] if c.closed else None
    c.met = any(e.kind == "met" for e in state_events)
    c.trial_day = any(e.kind == "met" and e.detail.get("round") == "trial_day"
                      for e in state_events)
    today = now.astimezone(timezone.utc).date().isoformat()
    for e in state_events:
        step = stages.step_of(e)
        #: A meeting planned for a later day has not happened yet.
        if step in stages.MEETINGS and stages.day_of(e) > today:
            continue
        if step in stages.PATH:
            c.steps.add(step)
        if e.kind == "closed" and e.detail.get("exit") == "hired":
            c.steps.add("hired")
        if e.kind == "go_ahead":
            #: A partner went ahead without waiting: ready to contact, on
            #: their word, though the votes never agreed.
            c.steps.add("to_contact")
    c.trial_day = "trial_day" in c.steps
    if c.closed:
        last = next(e for _, _, e in reversed(timed) if e.kind == "closed")
        c.ended = str(last.detail.get("exit", "")) or "closed"
    return c


def cases(pipes: dict[str, Pipeline], reg: Registry, cfg: Config, t: Tracking,
          root: Path | None = None, now: datetime | None = None) -> list[Case]:
    """Every application on the desk, one per person and role."""
    ashby = _ashby_keys(root or desk.ROOT)
    groups: dict[tuple[str, str], tuple[str, list[str]]] = {}
    for pid, pipe in pipes.items():
        for cid in pipe.candidates:
            p = reg.person_of(cid)
            person_id, name = (p.person_id, p.name) if p else (cid, cid)
            groups.setdefault((pid, person_id), (name, []))[1].append(cid)
    out = []
    for (pid, person_id), (name, cids) in groups.items():
        evs = [e for e in pipes[pid].events if e.candidate_id in cids]
        out.append(build_case(pid, person_id, name, cids, evs, cfg, t,
                              source_of(evs, pid, person_id, ashby), now))
    return out


# --------------------------------------------------------------------------
# 1. The promise
# --------------------------------------------------------------------------

@dataclass
class Spread:
    """A delay, summarised. Every figure is None when there is nothing to say."""

    n: int
    median: float | None
    p90: float | None
    max: float | None

    @classmethod
    def of(cls, xs: list[float]) -> "Spread":
        return cls(len(xs), quantile(xs, 0.5), quantile(xs, 0.9), max(xs) if xs else None)


@dataclass
class Promise:
    within_days: float
    now: datetime
    #: (case, days to the first answer)
    answered: list[tuple[Case, float]] = field(default_factory=list)
    #: (case, days waited so far) -- open, or closed without a word.
    waiting: list[tuple[Case, float]] = field(default_factory=list)
    unknown: list[tuple[Case, str]] = field(default_factory=list)
    #: Withdrew before anyone answered: not owed an answer any more, so
    #: neither on time nor late. Counted, and named.
    withdrew: list[Case] = field(default_factory=list)
    to_answer: Spread = field(default_factory=lambda: Spread(0, None, None, None))
    to_vote: Spread = field(default_factory=lambda: Spread(0, None, None, None))
    to_agree: Spread = field(default_factory=lambda: Spread(0, None, None, None))
    not_voted: int = 0
    not_agreed: int = 0
    curve: list[tuple[float, float]] = field(default_factory=list)
    km_median: float | None = None
    km_p90: float | None = None
    #: Applications at least N days old: the ones that could have been on time.
    cohort: int = 0
    on_time: int = 0

    @property
    def longest(self) -> float | None:
        xs = [d for _, d in self.answered] + [d for _, d in self.waiting]
        return max(xs) if xs else None

    @property
    def late_open(self) -> list[tuple[Case, float]]:
        """Still without an answer, past the promise. Longest first."""
        return sorted(((c, d) for c, d in self.waiting if d > self.within_days),
                      key=lambda x: -x[1])

    @property
    def late_answered(self) -> list[tuple[Case, float]]:
        return sorted(((c, d) for c, d in self.answered if d > self.within_days),
                      key=lambda x: -x[1])

    @property
    def silent_closes(self) -> list[Case]:
        return [c for c, _ in self.waiting if c.closed_silently]


def promise(cs: list[Case], t: Tracking, now: datetime) -> Promise:
    pr = Promise(t.answer_within_days, now)
    km: list[tuple[float, bool]] = []
    vote_d, agree_d = [], []
    for c in cs:
        if c.ended == "withdrew" and c.answered is None and not c.answer_unknown:
            pr.withdrew.append(c)
            continue
        if c.received is None or c.answer_unknown:
            pr.unknown.append((c, "; ".join(c.unknown) or "unknown"))
        elif c.answered is not None:
            d = days(c.received, c.answered)
            pr.answered.append((c, d))
            km.append((d, True))
        else:
            d = days(c.received, now)
            if d < 0:
                pr.unknown.append((c, "arrives in the future: the clocks disagree"))
                continue
            pr.waiting.append((c, d))
            km.append((d, False))
        if c.received is not None:
            if c.all_voted is not None and c.all_voted >= c.received:
                vote_d.append(days(c.received, c.all_voted))
            elif not c.closed:
                pr.not_voted += 1
            if c.agreed is not None and c.agreed >= c.received:
                agree_d.append(days(c.received, c.agreed))
            elif not c.closed:
                pr.not_agreed += 1
    pr.to_answer = Spread.of([d for _, d in pr.answered])
    pr.to_vote, pr.to_agree = Spread.of(vote_d), Spread.of(agree_d)
    pr.curve = kaplan_meier(km)
    pr.km_median, pr.km_p90 = km_quantile(pr.curve, 0.5), km_quantile(pr.curve, 0.9)
    n_days = t.answer_within_days
    for c, d in pr.answered:
        if days(c.received, now) >= n_days:  # type: ignore[arg-type]
            pr.cohort += 1
            pr.on_time += d <= n_days
    pr.cohort += sum(1 for _, d in pr.waiting if d >= n_days)
    return pr


# --------------------------------------------------------------------------
# 2. The funnel
# --------------------------------------------------------------------------

@dataclass
class FunnelRow:
    group: str
    counts: dict[str, int]
    #: Reached a stage only by evidence of a later one (a trial day with no
    #: recorded agreement to meet). Counted as reached, and said.
    inferred: int = 0
    after_trial: dict[str, int] = field(default_factory=dict)


def _evidence(c: Case, cfg: Config) -> set[str]:
    """The stages this application has its own record of."""
    out = {"received"} | set(c.steps)
    if all(v in c.latest_votes for v in cfg.voters):
        out.add("voted")
    o = c.settled(cfg)
    if o.status == "agreed" and o.meaning == "contact":
        out.add("to_contact")
    return out


def _stage(c: Case, cfg: Config) -> int:
    """The furthest stage this application reached, as an index into STAGES."""
    return max(STAGES.index(s) for s in _evidence(c, cfg))


def funnel(cs: list[Case], cfg: Config, by: Callable[[Case], str] | None = None
           ) -> list[FunnelRow]:
    groups: dict[str, list[Case]] = {}
    for c in cs:
        groups.setdefault(by(c) if by else "all", []).append(c)
    out = []
    for g in sorted(groups):
        row = FunnelRow(g, {s: 0 for s in stages_for(cfg)},
                        after_trial={"closed": 0, "open": 0})
        for c in groups[g]:
            top = _stage(c, cfg)
            #: Only the interviews this team runs are columns; one recorded
            #: beyond them still counts for every step before it.
            reached = [s for s in STAGES[:top + 1] if s in row.counts]
            for s in reached:
                row.counts[s] += 1
            #: Reached a stage only by evidence of a later one: counted as
            #: reached (it was), and said, because nobody recorded it.
            have = _evidence(c, cfg)
            if any(s not in have for s in reached if s != STAGES[top]):
                row.inferred += 1
            if c.trial_day:
                row.after_trial["closed" if c.closed else "open"] += 1
        out.append(row)
    return out


def share(k: int, n: int, t: Tracking) -> str:
    """A percentage, or the reason there is none."""
    if n < t.min_n_percent:
        return f"n too small ({k}/{n})" if n else "--"
    return f"{k / n:.0%}"


# --------------------------------------------------------------------------
# 3. Agreement between voters
# --------------------------------------------------------------------------

@dataclass
class PairAgreement:
    a: str
    b: str
    n: int
    #: (a's meaning, b's meaning) -> applications
    table: dict[tuple[str, str], int]
    observed: float | None
    kappa: float | None
    ci: tuple[float, float] | None


@dataclass
class Agreement:
    basis: str
    pairs: list[PairAgreement]
    fleiss_n: int = 0
    fleiss: float | None = None
    fleiss_ci: tuple[float, float] | None = None
    #: Applications everyone voted on, and how many of them went to Discuss.
    settled: int = 0
    to_discuss: int = 0
    #: How often each class was used, across all voters -- never per voter.
    used: dict[str, int] = field(default_factory=dict)
    changed_votes: int = 0
    min_n: int = 10


def cohen(pairs: list[tuple[str, str]], categories: Iterable[str] = MEANINGS
          ) -> float | None:
    """Cohen's kappa; None when chance agreement is total (both used one class)."""
    n = len(pairs)
    if n == 0:
        return None
    po = sum(a == b for a, b in pairs) / n
    pe = sum((sum(a == m for a, _ in pairs) / n) * (sum(b == m for _, b in pairs) / n)
             for m in categories)
    return None if pe >= 1 - 1e-12 else (po - pe) / (1 - pe)


def fleiss(items: list[list[str]], categories: Iterable[str] = MEANINGS) -> float | None:
    """Fleiss' kappa for items each rated by the same number of raters."""
    if not items:
        return None
    k = len(items[0])
    if k < 2 or any(len(i) != k for i in items):
        return None
    n, cats = len(items), list(categories)
    p_bar = sum((sum(i.count(m) ** 2 for m in cats) - k) / (k * (k - 1))
                for i in items) / n
    pe = sum((sum(i.count(m) for i in items) / (n * k)) ** 2 for m in cats)
    return None if pe >= 1 - 1e-12 else (p_bar - pe) / (1 - pe)


def bootstrap(items: list[Any], stat: Callable[[list[Any]], float | None], *,
              samples: int, confidence: float, seed: int = 0
              ) -> tuple[float, float] | None:
    """Percentile bootstrap over applications. Seeded: the same log, the same interval.

    None when fewer than two applications, or when most resamples leave the
    statistic undefined -- an interval drawn from the few that survive would
    be narrower than the truth.
    """
    if len(items) < 2:
        return None
    rng = random.Random(seed)
    got = []
    for _ in range(samples):
        v = stat([items[rng.randrange(len(items))] for _ in items])
        if v is not None:
            got.append(v)
    if len(got) < samples / 2:
        return None
    a = (1 - confidence) / 2
    return quantile(got, a), quantile(got, 1 - a)  # type: ignore[return-value]


def agreement(cs: list[Case], cfg: Config, t: Tracking) -> Agreement:
    def said(c: Case) -> dict[str, str]:
        if t.agreement_votes == "first":
            return c.first_votes
        return {v: x.meaning for v, x in c.latest_votes.items()}

    ag = Agreement(t.agreement_votes, [], min_n=t.min_n_agreement,
                   used={m: 0 for m in MEANINGS})
    vs = cfg.voters
    # Under a blind rule, only applications every voter has classed: those
    # votes are already visible to everyone. A pair's table that also counted
    # an application still waiting on a third voter would tell that voter,
    # from one refresh of the Tracking tab to the next, how the two classed it.
    ratings = [said(c) for c in cs
               if cfg.blind == "off" or all(v in c.latest_votes for v in vs)]
    for r in ratings:
        for m in r.values():
            ag.used[m] = ag.used.get(m, 0) + 1
    for i, a in enumerate(vs):
        for b in vs[i + 1:]:
            pairs = [(r[a], r[b]) for r in ratings if a in r and b in r]
            table = {(x, y): 0 for x in MEANINGS for y in MEANINGS}
            for x, y in pairs:
                table[(x, y)] += 1
            ag.pairs.append(PairAgreement(
                a, b, len(pairs), table,
                (sum(x == y for x, y in pairs) / len(pairs)) if pairs else None,
                cohen(pairs),
                bootstrap(pairs, cohen, samples=t.bootstrap, confidence=t.confidence)))
    if len(vs) >= 3:
        items = [[r[v] for v in vs] for r in ratings if all(v in r for v in vs)]
        ag.fleiss_n = len(items)
        ag.fleiss = fleiss(items)
        ag.fleiss_ci = bootstrap(items, fleiss, samples=t.bootstrap, confidence=t.confidence)
    for c in cs:
        if all(v in c.latest_votes for v in vs):
            ag.settled += 1
            ag.to_discuss += c.settled(cfg).meaning == "discuss"
        ag.changed_votes += c.changed_votes
    return ag


# --------------------------------------------------------------------------
# 4. How long a vote takes
# --------------------------------------------------------------------------

@dataclass
class VoteTime:
    minutes: float
    #: "observed" or "assumed" -- the recap says which.
    basis: str
    samples: int
    needed: int = 5

    def estimate(self, n_votes: int) -> str:
        total = max(1, round(n_votes * self.minutes))
        how = (f"from {self.samples} timed votes" if self.basis == "observed"
               else f"assuming {self.minutes:g} min a vote")
        return f"~{total} min ({how})"


def vote_time(pipes: dict[str, Pipeline], cfg: Config, t: Tracking) -> VoteTime:
    """Minutes per vote, from the gaps between one voter's votes in a sitting.

    The gap before a vote is the time it took to read that application and
    class it. Only first votes on an application count (changing a vote is
    quicker than making one), gaps longer than a sitting are dropped, and all
    voters are pooled: this estimates the task, not a person.
    """
    per: dict[str, list[tuple[datetime, str]]] = {}
    for pid, pipe in pipes.items():
        for e in pipe.events:
            if e.kind != "voted" or e.by not in cfg.voters:
                continue
            w = instant(e.at, t.naive_timestamps)
            if w is not None:
                per.setdefault(e.by, []).append((w, f"{pid}/{e.candidate_id}"))
    gaps: list[float] = []
    for items in per.values():
        items.sort()
        seen: set[str] = set()
        prev: datetime | None = None
        for w, app in items:
            first = app not in seen
            seen.add(app)
            if prev is not None and first:
                g = (w - prev).total_seconds() / 60
                if 0 < g <= t.session_gap_minutes:
                    gaps.append(g)
            prev = w
    if len(gaps) >= t.min_timed_votes:
        return VoteTime(round(quantile(gaps, 0.5) or t.minutes_per_vote, 1), "observed",
                        len(gaps), t.min_timed_votes)
    return VoteTime(t.minutes_per_vote, "assumed", len(gaps), t.min_timed_votes)


# --------------------------------------------------------------------------
# Everything at once, and in words
# --------------------------------------------------------------------------

@dataclass
class Report:
    now: datetime
    cfg: Config
    t: Tracking
    cases: list[Case]
    promise: Promise
    funnel_all: list[FunnelRow]
    funnel_role: list[FunnelRow]
    funnel_source: list[FunnelRow]
    agreement: Agreement
    vote_time: VoteTime


def report(pipes: dict[str, Pipeline], reg: Registry, cfg: Config, t: Tracking,
           now: datetime | None = None, titles: dict[str, str] | None = None) -> Report:
    now = now or datetime.now(timezone.utc)
    titles = titles or {}
    cs = cases(pipes, reg, cfg, t, now=now)
    return Report(now, cfg, t, cs, promise(cs, t, now), funnel(cs, cfg),
                  funnel(cs, cfg, lambda c: titles.get(c.posting_id) or c.posting_id),
                  funnel(cs, cfg, lambda c: c.source), agreement(cs, cfg, t),
                  vote_time(pipes, cfg, t))


def stage_names(cfg: Config) -> dict[str, str]:
    """The funnel's steps in the team's words: the state names of Settings."""
    n = stages.names(cfg)
    return {"received": "Received", "voted": "Fully voted", **{s: n[s] for s in STAGES[2:]}}


def fmt_days(d: float | None) -> str:
    if d is None:
        return "unknown"
    if d < 1:
        return f"{d * 24:.0f} h"
    return f"{d:.1f} days"


def fmt_kappa(k: float | None) -> str:
    return "undefined" if k is None else f"{k:+.2f}"


def render(r: Report) -> str:
    """The report as text. Every percentage next to its count."""
    t, pr, cfg = r.t, r.promise, r.cfg
    n = t.answer_within_days
    out = [f"The promise -- first answer within {n:g} days (calendar, UTC)", ""]
    b = out.append
    b(f"  applications: {len(r.cases)}  answered: {len(pr.answered)}  "
      f"still waiting: {len(pr.waiting)}  unknown: {len(pr.unknown)}")
    if pr.cohort:
        ci = wilson(pr.on_time, pr.cohort, t.confidence)
        extra = (f"  [{ci[0]:.0%}-{ci[1]:.0%}]" if ci and pr.cohort >= t.min_n_percent
                 else "")
        b(f"  answered within {n:g} days: {pr.on_time} of {pr.cohort} old enough to tell "
          f"-- {share(pr.on_time, pr.cohort, t)}{extra}")
    else:
        b(f"  answered within {n:g} days: nothing is {n:g} days old yet")
    s = pr.to_answer
    b(f"  to first answer, among the answered (n={s.n}): median {fmt_days(s.median)}, "
      f"p90 {fmt_days(s.p90)}, max {fmt_days(s.max)}")
    b(f"  counting those still waiting (Kaplan-Meier): median "
      f"{fmt_days(pr.km_median) if pr.km_median is not None else 'not reached'}, p90 "
      f"{fmt_days(pr.km_p90) if pr.km_p90 is not None else 'not reached'}")
    b(f"  longest wait, answered or not: {fmt_days(pr.longest)}")
    for label, sp, missing in (("to a complete vote", pr.to_vote, pr.not_voted),
                               ("to agreement", pr.to_agree, pr.not_agreed)):
        b(f"  {label} (n={sp.n}): median {fmt_days(sp.median)}, p90 {fmt_days(sp.p90)}, "
          f"max {fmt_days(sp.max)}; {missing} open without it")
    if pr.late_open:
        b(f"\n  past {n:g} days with no answer:")
        for c, d in pr.late_open:
            why = " (closed without a word)" if c.closed_silently else ""
            b(f"    {c.name} -- {c.posting_id}, {d:.0f} days{why}")
    if pr.late_answered:
        b(f"\n  answered, but after {n:g} days:")
        for c, d in pr.late_answered:
            b(f"    {c.name} -- {c.posting_id}, {d:.0f} days")
    if pr.unknown:
        b("\n  delay unknown:")
        for c, why in pr.unknown:
            b(f"    {c.name} -- {why}")
    if pr.withdrew:
        b(f"\n  withdrew before an answer (not counted either way): "
          + ", ".join(c.name for c in pr.withdrew))

    names = stage_names(cfg)
    for title, rows in (("by role", r.funnel_role), ("by source", r.funnel_source)):
        b(f"\nFunnel {title} (each step: count, share of the step before)")
        for row in rows:
            parts, prev = [], None
            for s_ in row.counts:
                k = row.counts[s_]
                parts.append(f"{names[s_]} {k}" + ("" if prev is None
                                                   else f" ({share(k, prev, t)})"))
                prev = k
            tail = (f"; after the trial day: {row.after_trial['closed']} closed, "
                    f"{row.after_trial['open']} open" if row.counts["trial_day"] else "")
            inf = f"; {row.inferred} inferred from a later step" if row.inferred else ""
            b(f"  {row.group}: " + " -> ".join(parts) + tail + inf)

    ag = r.agreement
    b(f"\nAgreement between voters ({ag.basis} votes; never a ranking of people)")
    for p in ag.pairs:
        warn = f"  -- n={p.n}, too few to read" if p.n < ag.min_n else ""
        ci = f" [{p.ci[0]:+.2f}, {p.ci[1]:+.2f}]" if p.ci else " [no interval]"
        obs = f"{p.observed:.0%}" if p.observed is not None else "--"
        b(f"  {p.a} & {p.b}: {p.n} rated by both, agree {obs}, kappa "
          f"{fmt_kappa(p.kappa)}{ci if p.n else ''}{warn}")
        for (x, y), k in sorted(p.table.items()):
            if k and x != y:
                b(f"      {p.a} {cfg.label(x)} / {p.b} {cfg.label(y)}: {k}")
    if len(cfg.voters) >= 3:
        ci = (f" [{ag.fleiss_ci[0]:+.2f}, {ag.fleiss_ci[1]:+.2f}]" if ag.fleiss_ci
              else " [no interval]")
        warn = f"  -- n={ag.fleiss_n}, too few to read" if ag.fleiss_n < ag.min_n else ""
        b(f"  all {len(cfg.voters)} (Fleiss): {ag.fleiss_n} rated by everyone, kappa "
          f"{fmt_kappa(ag.fleiss)}{ci if ag.fleiss_n else ''}{warn}")
    b(f"  went to discussion: {ag.to_discuss} of {ag.settled} fully voted "
      f"({share(ag.to_discuss, ag.settled, t)}); votes changed after being cast: "
      f"{ag.changed_votes}")
    vt = r.vote_time
    b(f"\nTime to class one application: {vt.minutes:g} min "
      f"({'median of ' + str(vt.samples) + ' timed votes' if vt.basis == 'observed' else 'assumed -- ' + str(vt.samples) + ' timed so far'})")
    return "\n".join(out)


def to_dict(r: Report) -> dict[str, Any]:
    pr, ag = r.promise, r.agreement

    def sp(s: Spread) -> dict[str, Any]:
        return {"n": s.n, "median_days": s.median, "p90_days": s.p90, "max_days": s.max}

    return {
        "now": r.now.isoformat(),
        "settings": {k: getattr(r.t, k) for k in Tracking.__dataclass_fields__},
        "promise": {
            "within_days": pr.within_days, "applications": len(r.cases),
            "answered": len(pr.answered), "waiting": len(pr.waiting),
            "unknown": [{"name": c.name, "posting": c.posting_id, "why": w}
                        for c, w in pr.unknown],
            "cohort": pr.cohort, "on_time": pr.on_time,
            "withdrew": [{"name": c.name, "posting": c.posting_id} for c in pr.withdrew],
            "to_first_answer": sp(pr.to_answer), "to_complete_vote": sp(pr.to_vote),
            "to_agreement": sp(pr.to_agree),
            "km_median_days": pr.km_median, "km_p90_days": pr.km_p90,
            "late": [{"name": c.name, "posting": c.posting_id, "days": round(d, 2),
                      "closed_silently": c.closed_silently} for c, d in pr.late_open],
        },
        "funnel": {name: [{"group": f.group, **f.counts, "inferred": f.inferred,
                           "after_trial": f.after_trial} for f in rows]
                   for name, rows in (("all", r.funnel_all), ("role", r.funnel_role),
                                      ("source", r.funnel_source))},
        "agreement": {
            "basis": ag.basis,
            "pairs": [{"a": p.a, "b": p.b, "n": p.n, "observed": p.observed,
                       "kappa": p.kappa, "ci": p.ci,
                       "table": {f"{x}/{y}": k for (x, y), k in p.table.items() if k}}
                      for p in ag.pairs],
            "fleiss": {"n": ag.fleiss_n, "kappa": ag.fleiss, "ci": ag.fleiss_ci},
            "discuss": {"settled": ag.settled, "to_discuss": ag.to_discuss},
            "changed_votes": ag.changed_votes,
        },
        "vote_time": {"minutes": r.vote_time.minutes, "basis": r.vote_time.basis,
                      "samples": r.vote_time.samples},
    }


def load_report(now: datetime | None = None) -> Report:
    cfg = desk.load_config()
    return report(desk._pipelines(), desk.load_registry(desk._registry_path()), cfg,
                  load_tracking(), now, desk._titles())


def main() -> int:
    ap = argparse.ArgumentParser(prog="metrics", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("report", help="the promise, the funnel, the agreement (free)")
    rp.add_argument("--json", action="store_true")
    a = ap.parse_args()
    try:
        r = load_report()
    except DeskError as err:
        print(err, file=sys.stderr)
        return 2
    print(json.dumps(to_dict(r), indent=2, ensure_ascii=False) if a.json else render(r))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
