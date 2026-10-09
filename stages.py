#!/usr/bin/env python3
"""Where an application stands: one state, apart from the vote.

The vote is what each voter thinks, cast blind. The state is what has been
*done*: written to them, a first call, a trial day, an offer. A partner
looking at a list wants both, and wants them apart -- "Ana and Ben said yes"
is not "somebody has written to her", and a desk that blurs the two is how a
yes turns into three weeks of silence, each partner sure another one wrote.

    New -> Voting -> (Team to decide) -> Ready to contact -> Contacted
        -> First call -> Second interview -> ... -> Trial day
    ways out: Talk later (until a date) · Answered no · Withdrew

How many interviews come before the trial day is the team's (desk.toml
[states] interviews, four by default); the trial day is the last step. With
one person deciding, the vote is simply their decision: the same states, and
nothing waits on anybody else.

Each state says whose move it is, and under it a line says what that move is:
"Ready to contact -- next step: write the message, then mark as contacted".

Four rules, each enforced here:

**Derived, never stored.** The state is read off the event log
(`pipeline.py`) every time it is shown; there is no status field to drift
from what happened. Each step a person records is an event like any other --
`contacted`, `met` (with its round), `offered`, `closed` (with how it ended),
`revisit` -- with who recorded it, when, and the date they say it happened.

**The vote moves nothing but a to-do.** When the votes settle under the
team's rule, the state becomes something to do -- Ready to contact, Ready to
answer no, Talk later -- and nothing else happens. A partner who cannot wait
for an absent one may "go ahead without waiting" after their own yes: the
desk records who went ahead and whose votes were missing, and those votes
can still be cast. Nobody has been written to because
three people clicked "yes". Every step after that is declared by a named
person, one click, with a date they can set back ("contacted yesterday").

**One way forward, a few ways out.** From each state there is one next step
and a short list of exits; anything else is refused with what *is* possible.
A step recorded on a stale page -- a colleague moved it while you were
reading -- is refused rather than written twice.

**A date is a claim, so it is checked.** Not before the application arrived,
not before the step it follows, not in the future for something that has
happened. A meeting may be planned ahead, within a limit; "talk later" needs
a date after today. "2062-10-03" is a typo, not a plan.

Nothing here sends anything. "Contacted" says a person wrote; the desk did not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import desk
from desk import Config, DeskError, outcome, votes
from pipeline import Event, Pipeline, Standing, after
from trial import PROTECTED, _hits

#: The interviews between the first message and the trial day, in order. How
#: many of them a team runs is a setting ([states] interviews); the ids are
#: fixed, so a log stays readable when the number changes.
ROUNDS = ("first_call", "interview_2", "interview_3", "interview_4", "interview_5",
          "interview_6")
#: The way through, in order, with every interview a team could run. The way
#: one team runs it is `path(cfg)`.
PATH = ("new", "votes_in", "to_contact", "contacted", "replied") + ROUNDS + (
    "trial_day", "offer", "hired")
#: The ways out a person records.
EXITS = ("answered_no", "talk_later", "withdrew")
#: Every state an application can be in. "team_to_decide" is the votes
#: disagreeing, or someone unsure; "to_answer_no" is the votes' to-do when they
#: agree on no; "closed" is a close recorded without saying how (by hand, or
#: before the desk had states).
STATES = PATH[:2] + ("team_to_decide",) + PATH[2:] + ("to_answer_no",) + EXITS + ("closed",)
#: States that end the application until somebody reopens it.
ENDED = ("hired", "answered_no", "withdrew", "closed")
#: States the votes reach on their own. Each is a thing to do, never a thing done.
TO_DO = ("to_contact", "to_answer_no", "talk_later")
#: Steps that are a meeting: they may be planned ahead.
MEETINGS = ROUNDS + ("trial_day",)

DEFAULT_NAMES = {
    "new": "New", "votes_in": "Voting", "team_to_decide": "Team to decide",
    "to_contact": "Ready to contact", "contacted": "Contacted", "replied": "Replied, OK",
    "first_call": "First call",
    "interview_2": "Second interview", "interview_3": "Third interview",
    "interview_4": "Fourth interview", "interview_5": "Fifth interview",
    "interview_6": "Sixth interview",
    "trial_day": "Trial day", "offer": "Offer", "hired": "Hired",
    "to_answer_no": "Ready to answer no", "answered_no": "Answered no",
    "talk_later": "Talk later", "withdrew": "Withdrew", "closed": "Closed",
}
#: With one person deciding, nothing is "voting" or "for the team": the same
#: states, said for someone sorting alone.
SOLO_NAMES = {"votes_in": "Sorting", "team_to_decide": "To think about",
              "first_call": "First interview"}
#: The label an application carries while it is kept with no date to come back.
POOL_TAG = "pool"
#: The label of an application stopped along the way: its "no" is still to send.
STOP_TAG = "stop"
#: The label of an application taken up again from the pool.
TAKEUP_TAG = "takeup"

#: The words on the button that records each step.
ACTION = {"contacted": "Mark as contacted", "replied": "They replied: OK",
          "takeup": "Take them up",
          "stop": "Stop here", "first_call": "Record the first call",
          "trial_day": "Record the trial day", "offer": "Record the offer",
          "hired": "Mark as hired", "answered_no": "Mark as answered",
          "talk_later": "Talk later", "withdrew": "Withdrew", "reopen": "Reopen"}

#: When one partner may go ahead without waiting for the others' votes.
GO_AHEAD = ("after_one_yes", "all_voted", "owner")

#: What a person can record. "reopen" is the one step out of an ended state.
STEPS = ("contacted", "replied") + MEETINGS + ("offer", "hired", "answered_no", "talk_later",
                                               "withdrew", "stop", "takeup", "reopen")

#: The one step forward from each state that has a fixed one. An interview's
#: step forward depends on how many the team runs: see `forward`. A planned
#: meeting's step forward is the meeting itself, happened (see `allowed`). The
#: desk stops at the trial day: what follows (offer, hire) is another process,
#: kept elsewhere. Those two steps are still read from older logs, never proposed.
FORWARD = {"to_contact": "contacted", "contacted": "replied", "replied": ROUNDS[0],
           "to_answer_no": "answered_no", "talk_later": "contacted"}

#: The states in which the team is talking to the person.
TALKING = ("contacted", "replied") + MEETINGS + ("offer",)
#: Where "Stop here" may be pressed: the person goes to the no's still to
#: send, and leaves once it is sent. Not after an offer: that is their answer.
STOP_FROM = ("contacted", "replied") + MEETINGS

#: Where each way out may be taken from.
EXIT_FROM = {
    # A no comes from the votes (To answer no, whose step forward it is), or
    # later in the process -- never from an application nobody has voted on:
    # a no is always somebody's recorded decision first.
    "answered_no": TALKING + ("talk_later",),
    "talk_later": ("to_contact",) + TALKING + ("talk_later",),
    # A candidate may withdraw at any point.
    "withdrew": ("new", "votes_in", "team_to_decide", "to_contact", "to_answer_no")
    + TALKING + ("talk_later",),
}

#: "Talk later" further out than this is a typo, or a polite never.
LATER_MAX_DAYS = 730
#: A done step dated after tomorrow is refused. One day, not zero: at 00:30 in
#: Madrid it is still yesterday in UTC, and "today" typed there is tomorrow here.
SLACK_DAYS = 1


@dataclass
class State:
    """One application's state, as derived from its events."""

    id: str
    #: The day it was reached -- or, for a planned meeting, the day it is planned for.
    on: str = ""
    #: Who recorded it ("" when the votes reached it, or when it is "new").
    by: str = ""
    #: A meeting dated after today.
    planned: bool = False
    #: For "talk_later": the day it comes back.
    until: str = ""
    #: For a meeting: the time it was set for ("14:30"), when one was given.
    time: str = ""
    #: For a meeting: a line said with it ("with Ana or Ben").
    memo: str = ""
    #: A "talk later" whose day has come: it is back to "to_contact".
    back: bool = False
    #: Said beside the state: "to settle together" while the votes disagree.
    note: str = ""

    @property
    def ended(self) -> bool:
        return self.id in ENDED


def names(cfg: Config) -> dict[str, str]:
    """What each state is called: desk.toml [states], then Settings, over the defaults."""
    return {**DEFAULT_NAMES, **(SOLO_NAMES if cfg.solo else {}),
            **{k: v for k, v in (cfg.state_names or {}).items()
               if k in DEFAULT_NAMES and str(v).strip()}}


def rounds(cfg: Config) -> tuple[str, ...]:
    """The interviews this team runs before the trial day, in order."""
    return ROUNDS[:cfg.interviews]


def path(cfg: Config, also: Any = ()) -> tuple[str, ...]:
    """The way through as this team runs it, plus any interview a log already holds."""
    keep = set(rounds(cfg)) | set(also)
    return tuple(k for k in PATH if k not in ROUNDS or k in keep)


def forward(state: str, cfg: Config) -> str | None:
    """The one step forward from a state, under this team's number of interviews.

    After the last interview comes the trial day. An interview beyond the
    team's number (the setting was lowered since) also leads to the trial day.
    """
    if state in ROUNDS:
        i = ROUNDS.index(state) + 1
        return ROUNDS[i] if i < cfg.interviews else "trial_day"
    return FORWARD.get(state)


def action(step: str, cfg: Config) -> str:
    """The words on the button that records a step, in the team's own names."""
    if step in MEETINGS:
        return f"Record the {name(step, cfg).lower()}"
    return ACTION[step]


def name(state: str, cfg: Config) -> str:
    return names(cfg).get(state, state)


def _today(now: datetime) -> date:
    return now.astimezone(timezone.utc).date()


def day_of(e: Event) -> str:
    """The day a step happened, as the person said it -- or the day it was written."""
    on = str(e.detail.get("on", "")).strip()
    return on[:10] if on else e.at[:10]


def instant(e: Event) -> datetime:
    """When a step happened, as an instant: the latest moment the record allows.

    A step written on its own day is as precise as its timestamp. One dated
    back ("contacted yesterday") is placed at the end of that day: the
    promise is measured from it, and an estimate that flatters the desk is
    the one kind it may not make.
    """
    at = e.when
    on = str(e.detail.get("on", "")).strip()
    if not on:
        return at
    try:
        d = date.fromisoformat(on[:10])
    except ValueError:
        return at
    end = datetime.combine(d, time(23, 59, 59), tzinfo=timezone.utc)
    return min(at, end)


def step_of(e: Event) -> str | None:
    """The step an event records, if it records one."""
    if e.kind == "contacted":
        #: The acknowledgment, the "later" mail and the "no" mail are messages,
        #: not "we are talking to them": the first changes nothing, the other
        #: two belong to Talk later and Answered no.
        return None if e.detail.get("about") in ("received", "later", "pass") else "contacted"
    if e.kind == "met":
        #: A meeting with no round on record (an older log) is a first call.
        said = str(e.detail.get("round", ""))
        return said if said in MEETINGS else ROUNDS[0]
    if e.kind == "replied":
        return "replied"
    if e.kind == "offered":
        return "offer"
    #: Taken up again from the pool: ready to contact, as after a yes.
    if e.kind == "tagged" and e.tag == TAKEUP_TAG and e.detail.get("step") == "to_contact":
        return "to_contact"
    #: Stopped along the way: the no is still to send.
    if e.kind == "tagged" and e.tag == STOP_TAG and e.detail.get("step") == "to_answer_no":
        return "to_answer_no"
    if e.kind == "revisit":
        return "talk_later"
    #: Kept with no date, as a step a person recorded (see `advance`).
    if e.kind == "tagged" and e.tag == POOL_TAG and e.detail.get("step") == "talk_later":
        return "talk_later"
    return None


def _close_kind(s: Standing, close: Event) -> str:
    said = str(close.detail.get("exit", ""))
    if said in ENDED:
        return said
    #: A close from before the desk had states. "I sent it" on an agreed no
    #: writes the mail, then the close: that is an answered no.
    if any(e.kind == "contacted" and e.detail.get("about") == "pass" for e in s.events):
        return "answered_no"
    return "closed"


def derive(s: Standing, cfg: Config, now: datetime | None = None) -> State:
    """The state, from the log. Steps a person recorded win over what the votes say."""
    now = now or datetime.now(timezone.utc)
    today = _today(now)
    if s.state == "closed":
        close = next(e for e in reversed(s.events) if e.kind == "closed")
        return State(_close_kind(s, close), on=day_of(close), by=close.by)

    vs = votes(s)
    o = outcome(vs, cfg)
    agreed_later = o.status == "agreed" and o.meaning == "later"
    cur: Event | None = None
    for e in s.events:
        step = step_of(e)
        if step is None:
            continue
        #: A parking written by the votes holds only while the votes still
        #: say "later"; one recorded as a step holds until the next step.
        #: ...and only while those votes still give a date: once they keep the
        #: person with no date, an older date is not theirs any more.
        if step == "talk_later" and "step" not in e.detail and not (agreed_later and o.until):
            continue
        #: A reply moves things on only from "contacted": one recorded later
        #: (they answered after an interview) is not a step back.
        if step == "replied" and (cur is None or step_of(cur) != "contacted"):
            continue
        cur = e
    if cur is not None:
        step = step_of(cur)
        if step == "talk_later":
            until = str(cur.detail.get("on", ""))[:10]
            if not until:
                #: Kept with no date: it stays there until a person takes it up.
                return State("talk_later", on=cur.at[:10], by=cur.by)
            #: `revisit_on` already knows that a reply or a meeting after the
            #: parking ends it; the state must not contradict it.
            if s.revisit_on is not None and not s.revisit_due(now):
                return State("talk_later", on=cur.at[:10], by=cur.by, until=until)
            return State("to_contact", on=until, back=True,
                         note=f"back from {name('talk_later', cfg)}")
        on = day_of(cur)
        planned = step in MEETINGS and on > today.isoformat()
        return State(step, on=on, by=cur.by, planned=planned,
                     time=str(cur.detail.get("time", "")) if step in MEETINGS else "",
                     memo=str(cur.detail.get("note", "")) if step in MEETINGS else "")

    received = next((e for e in s.events if e.kind == "received"), None)
    ahead = went_ahead(s)
    if ahead is not None:
        return State("to_contact", on=ahead.at[:10], by=ahead.by,
                     note=_ahead_note(ahead))
    if not vs:
        return State("new", on=received.at[:10] if received else "")
    first = min(v.at for v in vs.values())[:10]
    last = max(vs[v].at for v in o.voted)[:10] if o.voted else first
    if not o.settled:
        return State("votes_in", on=first,
                     note=f"{len(o.voted)} of {len(cfg.voters)} voted")
    if o.meaning == "discuss":
        return State("team_to_decide", on=last)
    if o.meaning == "contact":
        return State("to_contact", on=last)
    if o.meaning == "pass":
        return State("to_answer_no", on=last)
    # Agreed "later" with no date: kept, until a person takes it up again.
    if not o.until:
        return State("talk_later", on=last)
    # Agreed "later" with no parking on record (a log edited by hand): the
    # votes' own date still holds.
    if o.until and o.until > today.isoformat():
        return State("talk_later", on=last, until=o.until)
    return State("to_contact", on=o.until or last, back=True,
                 note=f"back from {name('talk_later', cfg)}")


def went_ahead(s: Standing) -> Event | None:
    """The latest "go ahead without waiting", if no reopen came after it."""
    for e in reversed(s.events):
        if e.kind == "reopened":
            return None
        if e.kind == "go_ahead":
            return e
    return None


def _ahead_note(e: Event) -> str:
    missing = e.detail.get("missing") or []
    return (f"{e.by} went ahead without waiting"
            + (f" for {', '.join(missing)}" if missing else ""))


def why_not_go_ahead(s: Standing, viewer: str, cfg: Config,
                     now: datetime | None = None) -> str:
    """Why this viewer may not go ahead alone now; "" when they may.

    Going ahead is the viewer's own yes, acted on: they must have voted yes
    themselves. That is also what keeps the blind rule whole -- a button that
    appeared because *someone* said yes would tell a partner who has not voted
    how a colleague voted.
    """
    st = derive(s, cfg, now)
    if st.id not in ("votes_in", "team_to_decide"):
        return "only while the votes are still open"
    mine = votes(s).get(viewer)
    if mine is None or mine.meaning != "contact":
        return f"vote “{cfg.label('contact')}” first: going ahead is your yes, acted on"
    if cfg.go_ahead_alone == "all_voted" and outcome(votes(s), cfg).missing:
        return "this desk waits until everyone has voted (House rules)"
    if cfg.go_ahead_alone == "owner" and viewer not in cfg.advancers:
        return f"only {', '.join(cfg.advancers)} may go ahead alone here (House rules)"
    return ""


def go_ahead(pipe: Pipeline, candidate_id: str, by: str, cfg: Config, *,
             expect: str = "", now: datetime | None = None) -> Event:
    """One partner moves to "ready to contact" without waiting for the others.

    Recorded under their name, with whose votes were missing -- names only,
    never how anyone voted, so the record follows the blind rule. The missing
    votes can still be cast afterwards; they are shown, and change nothing.
    """
    now = now or datetime.now(timezone.utc)
    by = cfg.voter(by)
    if candidate_id not in pipe.candidates:
        raise DeskError(f"{candidate_id} has no application for {pipe.posting_id}")
    s = pipe.standing(candidate_id)
    st = derive(s, cfg, now)
    if expect and expect != st.id:
        raise DeskError(f"this application is now {name(st.id, cfg)} -- it moved while you "
                        f"were looking. Nothing recorded; look again.")
    why = why_not_go_ahead(s, by, cfg, now)
    if why:
        raise DeskError(f"cannot go ahead: {why}")
    missing = outcome(votes(s), cfg).missing
    return pipe.add(Event(candidate_id=candidate_id, kind="go_ahead", by=by,
                          at=now.isoformat(), detail={"missing": missing}))


def go_ahead_now(posting: str, candidate_id: str, by: str, cfg: Config, **kw: Any) -> Event:
    with desk.locked():
        pipe = desk._pipelines().get(posting)
        if pipe is None:
            raise DeskError(f"nothing on the desk for {posting!r}")
        ev = go_ahead(pipe, candidate_id, by, cfg, **kw)
        desk._save(pipe)
        return ev


def meaning_of(st: State, cfg: Config) -> str:
    """Which mail goes with a state that asks someone to write: "" when none does.

    Each meeting has its invitation, named after it: the first interview, the
    second, ..., the trial day (the interviews share their words by default,
    desk.Config.template). It is offered before the meeting is planned (its
    day and time left blank, to fill in) and once it is (filled in). A mail
    the team switched off (House rules) is never offered.
    """
    m = _meaning(st, cfg)
    off = m in cfg.mails_off or (m in ROUNDS and "interview" in cfg.mails_off)
    return "" if off else m


def _meaning(st: State, cfg: Config) -> str:
    if st.id in MEETINGS and st.planned:
        return st.id
    if st.id in ROUNDS:
        #: After an interview: the invitation to the next one, and after the
        #: last one, the invitation to the trial day -- never before.
        return forward(st.id, cfg) or "trial_day"
    if st.id == "replied":
        return ROUNDS[0]
    return {"to_contact": "contact", "to_answer_no": "pass",
            # The reminder to someone who has not replied follows a step too.
            "contacted": "follow_up"}.get(st.id, "")


#: Mails that are not the outcome of a vote. They have a template, no label.
STEP_MAILS = ("follow_up", "interview") + MEETINGS


def when(st: State) -> dict[str, str]:
    """The day and time a planned meeting's invitation fills in: "Tuesday 14 October"."""
    if not (st.id in MEETINGS and st.planned):
        return {}
    try:
        d = date.fromisoformat(st.on[:10])
    except ValueError:
        return {}
    return {"day": f"{d.strftime('%A')} {d.day} {d.strftime('%B')}", "time": st.time}


def draft(candidate_id: str, posting: str, cfg: Config,
          now: datetime | None = None) -> tuple[Any, str]:
    """The message this state asks for, as an unsent draft, and a file name.

    The one place a draft is made for a state: the page's "Write the message"
    box starts from it. Today it fills the team's template for the outcome
    (desk.toml / Settings) with the person's first name and the role. A
    drafting assistant can replace this function later; nothing else needs to
    change, and nothing is ever sent from here.
    """
    pipe = desk._pipelines().get(posting)
    if pipe is None or candidate_id not in pipe.candidates:
        raise DeskError(f"{candidate_id} is not on the desk for {posting}")
    st = derive(pipe.standing(candidate_id), cfg, now)
    meaning = meaning_of(st, cfg)
    if not meaning:
        return desk.draft_for(candidate_id, posting, "", cfg)
    if meaning in STEP_MAILS:
        person = desk.load_registry(desk._registry_path()).person_of(candidate_id)
        if person is None:
            raise DeskError(f"{candidate_id} is not on the desk for {posting}")
        m = desk.draft(person, desk._titles().get(posting, posting), meaning, cfg,
                       when={**(when(st) if meaning == st.id else {}),
                             "interview": name(meaning, cfg).lower()})
        return m, f"{candidate_id}_{posting}_{meaning}.eml"
    return desk.draft_for(candidate_id, posting, meaning, cfg)


#: What the next step is, under each state: whose move, and what it is.
def next_line(st: State, s: Standing, cfg: Config) -> str:
    n = names(cfg)
    if st.id == "new":
        return "your decision" if cfg.solo else "everyone votes"
    if st.id == "votes_in":
        missing = outcome(votes(s), cfg).missing
        return f"{', '.join(missing)} to vote" if missing else "everyone votes"
    if st.id == "team_to_decide":
        if cfg.solo:
            return "look again, then change your decision"
        return ("talk it over in the notes, then change a vote -- or a partner who says yes "
                "goes ahead")
    if st.id == "to_contact":
        return "write the message, then mark as contacted"
    if st.id == "to_answer_no":
        return "write the answer, then mark as answered"
    if st.id == "contacted":
        return f"when they reply with an OK, tick “{n['replied']}”"
    if st.id == "replied":
        return f"plan the {n['first_call'].lower()}"
    if st.id in MEETINGS and st.planned:
        return f"on {short(st.on)}: once it has happened, record it"
    if st.id in ROUNDS:
        return f"plan the {n[forward(st.id, cfg)].lower()}"
    if st.id == "trial_day":
        return "write the recap of the day in the notes; what follows is outside this desk"
    if st.id == "offer":
        return "their answer: mark as hired, or withdrew"
    if st.id == "talk_later":
        if not st.until:
            return "no date: it stays here until you take it up again"
        return f"it comes back by itself on {short(st.until)}"
    if st.id == "hired":
        return "none: welcome them"
    return "none"


def allowed(st: State, cfg: Config) -> list[str]:
    """What may be recorded from this state: the step forward first, then the exits.

    From an interview that is not the last, the trial day is allowed too: not
    every role needs every interview, and nobody should have to record one that
    never took place to get past it.
    """
    if st.ended:
        return ["reopen"]
    out = []
    nxt = st.id if st.id in MEETINGS and st.planned else forward(st.id, cfg)
    if nxt:
        out.append(nxt)
    if st.id in ROUNDS and not st.planned:
        #: Any later interview the team runs, then the trial day: the person who
        #: runs hiring knows which come next for this role.
        later = rounds(cfg)[rounds(cfg).index(st.id) + 1:] if st.id in rounds(cfg) else ()
        out += [r for r in later if r not in out]
        if "trial_day" not in out:
            out.append("trial_day")
    #: A reply that came with a date for the interview: no need to tick the
    #: reply first.
    if st.id == "contacted":
        out.append(ROUNDS[0])
    #: From the pool: taken up again, ready to contact, first of all.
    if st.id == "talk_later":
        out.insert(0, "takeup")
    out += [x for x in EXITS if st.id in EXIT_FROM[x] and x not in out]
    if st.id in STOP_FROM:
        out.append("stop")
    return out


# --------------------------------------------------------------------------
# Recording a step
# --------------------------------------------------------------------------

def _date(said: str, what: str) -> date:
    try:
        return date.fromisoformat(said.strip()[:10])
    except ValueError:
        raise DeskError(f"{said!r} is not a date (YYYY-MM-DD) for {what}") from None


def _time(said: str) -> str:
    """'9:30' -> '09:30'. A meeting's hour, as the time box sends it."""
    m = re.fullmatch(r"\s*([01]?\d|2[0-3])[:h.]([0-5]\d)\s*", said or "")
    if not m:
        raise DeskError(f"{said!r} is not a time (HH:MM)")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _line(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) > desk.COMMENT_MAX:
        raise DeskError(f"a reason is one line, {desk.COMMENT_MAX} characters at most")
    hits = _hits(text, PROTECTED)
    if hits:
        raise DeskError(f"the reason touches a protected subject ({', '.join(hits)}) and "
                        f"cannot go on the record")
    return text


def may_move(by: str, cfg: Config) -> None:
    """The rule "who can move a step": any voter, unless the team named some."""
    if cfg.advancers and by not in cfg.advancers:
        raise DeskError(f"{by} does not move steps on this desk; "
                        f"{', '.join(cfg.advancers)} do (House rules)")


def advance(pipe: Pipeline, candidate_id: str, step: str, by: str, cfg: Config, *,
            on: str = "", expect: str = "", reason: str = "", hour: str = "", note: str = "",
            now: datetime | None = None) -> list[Event]:
    """Record one step a person took. Refuses rather than guesses; returns what it wrote.

    `on` is the day it happened (today when empty), or for "talk_later" the
    day to come back. `expect` is the state the person was looking at: if it
    has moved since, nothing is written.
    """
    now = now or datetime.now(timezone.utc)
    today = _today(now)
    by = cfg.voter(by)
    may_move(by, cfg)
    if candidate_id not in pipe.candidates:
        raise DeskError(f"{candidate_id} has no application for {pipe.posting_id}")
    if step not in STEPS:
        raise DeskError(f"{step!r} is not a step. Steps: {', '.join(STEPS)}")
    s = pipe.standing(candidate_id)
    st = derive(s, cfg, now)
    if expect and expect != st.id:
        raise DeskError(f"this application is now {name(st.id, cfg)} -- it moved while you "
                        f"were looking. Nothing recorded; look again.")
    ok = allowed(st, cfg)
    if step not in ok:
        if st.ended:
            raise DeskError(f"this application is {name(st.id, cfg)}: reopen it first")
        said = " or ".join(ACTION[x] if x == "stop" else name(x, cfg)
                           for x in ok) or "nothing a person records"
        raise DeskError(f"from {name(st.id, cfg)} the next step is {said}, "
                        f"not {name(step, cfg) if step != 'reopen' else 'reopen'}")
    reason = _line(reason)
    at = now.isoformat()

    if step == "reopen":
        return [pipe.add(Event(candidate_id=candidate_id, kind="reopened", by=by, at=at,
                               reason=reason or f"reopened by {by}"))]

    d = _date(on, name(step, cfg)) if on.strip() else today
    received = next((e for e in s.events if e.kind == "received"), None)
    if received is not None and d.isoformat() < received.at[:10]:
        raise DeskError(f"{d.isoformat()} is before they applied ({received.at[:10]})")

    if step == "talk_later":
        if not on.strip():
            if cfg.later_needs_date:
                raise DeskError(f"'{name('talk_later', cfg)}' needs the day to come back -- "
                                f"later without a date is how people are forgotten")
            #: Kept with no date. Not a `revisit`, which is a date by
            #: definition: a label, under the name of whoever put them there.
            return [pipe.add(Event(candidate_id=candidate_id, kind="tagged", tag=POOL_TAG,
                                   by=by, at=at,
                                   reason=reason or f"{name('talk_later', cfg)}, set by {by}",
                                   detail={"step": "talk_later"}))]
        if d <= today:
            raise DeskError(f"'{name('talk_later', cfg)}' needs a day after today")
        if d > today + timedelta(days=LATER_MAX_DAYS):
            raise DeskError(f"{d.isoformat()} is more than {LATER_MAX_DAYS // 365} years away")
        return [pipe.add(Event(candidate_id=candidate_id, kind="revisit", by=by, at=at,
                               reason=reason or f"{name('talk_later', cfg)}, set by {by}",
                               detail={"on": d.isoformat(), "step": "talk_later"}))]

    if step == "takeup":
        return [pipe.add(Event(candidate_id=candidate_id, kind="tagged", tag=TAKEUP_TAG,
                               by=by, at=at, reason=reason or f"taken up again by {by}",
                               detail={"step": "to_contact", "on": today.isoformat()}))]

    if step == "stop":
        #: Today, whatever date was planned: stopping is now. The no is a
        #: to-do, written under "Not for us", and the application closes once
        #: it is marked as sent.
        return [pipe.add(Event(candidate_id=candidate_id, kind="tagged", tag=STOP_TAG,
                               by=by, at=at, reason=reason or f"stopped at "
                               f"{name(st.id, cfg)} by {by}",
                               detail={"step": "to_answer_no", "from": st.id,
                                       "on": today.isoformat()}))]

    if step in MEETINGS:
        if d > today + timedelta(days=cfg.plan_ahead_days):
            raise DeskError(f"{d.isoformat()} is more than {cfg.plan_ahead_days} days ahead "
                            f"(Settings: how far ahead a meeting may be planned)")
    elif d > today + timedelta(days=SLACK_DAYS):
        raise DeskError(f"{d.isoformat()} is in the future; '{name(step, cfg)}' is something "
                        f"that has happened")
    #: Not before the step it follows. A re-dated meeting (the same step
    #: again) is checked against the arrival only: it replaces its own date.
    if step != st.id and st.id in PATH[3:] and st.on and d.isoformat() < st.on:
        raise DeskError(f"{d.isoformat()} is before {name(st.id, cfg)} ({st.on})")

    day = d.isoformat()
    if step == "contacted":
        evs = [Event(candidate_id=candidate_id, kind="contacted", by=by, at=at,
                     detail={"about": "contact", "on": day, "step": step})]
    elif step == "replied":
        evs = [Event(candidate_id=candidate_id, kind="replied", by=by, at=at,
                     detail={"on": day, "step": step})]
    elif step in MEETINGS:
        evs = [Event(candidate_id=candidate_id, kind="met", by=by, at=at,
                     detail={"round": step, "on": day, "step": step,
                             **({"time": _time(hour)} if hour.strip() else {}),
                             **({"note": _line(note)} if note.strip() else {})})]
    elif step == "offer":
        evs = [Event(candidate_id=candidate_id, kind="offered", by=by, at=at,
                     detail={"on": day, "step": step})]
    elif step == "answered_no":
        #: The no is a message (it answers the promise) and an end.
        evs = [Event(candidate_id=candidate_id, kind="contacted", by=by, at=at,
                     detail={"about": "pass", "on": day, "step": step}),
               Event(candidate_id=candidate_id, kind="closed", by=by, at=at,
                     reason=reason or name("answered_no", cfg),
                     detail={"exit": step, "on": day})]
    else:  # hired, withdrew
        evs = [Event(candidate_id=candidate_id, kind="closed", by=by, at=at,
                     reason=reason or name(step, cfg), detail={"exit": step, "on": day})]
    return [pipe.add(e) for e in evs]


def advance_now(posting: str, candidate_id: str, step: str, by: str, cfg: Config,
                **kw: Any) -> list[Event]:
    """Read, record and write under the desk's lock: two clicks never both land."""
    with desk.locked():
        pipe = desk._pipelines().get(posting)
        if pipe is None:
            raise DeskError(f"nothing on the desk for {posting!r}")
        written = advance(pipe, candidate_id, step, by, cfg, **kw)
        desk._save(pipe)
        return written


#: One batch records at most this many answers: a page of the list, with room.
BATCH_MAX = 500


def answer_many(items: list[tuple[str, str]], by: str, cfg: Config, *,
                now: datetime | None = None) -> tuple[int, list[str]]:
    """Record "the no was sent" for several applications, under one lock.

    For someone who answers a batch from their own mail. Each application
    must be Ready to answer no, exactly as when it is marked one by one: one
    that moved meanwhile, or was never decided, is left untouched and named.
    Returns how many were recorded, and why each of the others was not.
    """
    if len(items) > BATCH_MAX:
        raise DeskError(f"{len(items)} at once is more than a batch holds ({BATCH_MAX})")
    done, refused = 0, []
    with desk.locked():
        pipes = desk._pipelines()
        touched: dict[str, Pipeline] = {}
        for posting, candidate_id in dict.fromkeys(items):
            pipe = pipes.get(posting)
            if pipe is None:
                refused.append(f"{candidate_id}: nothing on the desk for {posting!r}")
                continue
            try:
                advance(pipe, candidate_id, "answered_no", by, cfg, expect="to_answer_no",
                        now=now)
            except DeskError as err:
                refused.append(f"{candidate_id}: {err}")
                continue
            touched[posting] = pipe
            done += 1
        for pipe in touched.values():
            desk._save(pipe)
    return done, refused


# --------------------------------------------------------------------------
# Reading: the journey on a card, and what is next
# --------------------------------------------------------------------------

@dataclass
class Mark:
    """One state on the way through, as the card's line of states shows it."""

    id: str
    on: str = ""
    by: str = ""
    #: "done", "now", "planned" or "ahead".
    status: str = "ahead"


def journey(s: Standing, cfg: Config, now: datetime | None = None) -> tuple[list[Mark], State]:
    """Each state on the way, with the day it was reached and by whom; and where it is now.

    The latest record of each step counts, so a re-dated meeting shows its
    new date. A step reached only by evidence of a later one (a trial day
    nobody recorded a first call for) shows no date: it is not invented.
    """
    now = now or datetime.now(timezone.utc)
    st = derive(s, cfg, now)
    seen: dict[str, Event] = {}
    for e in s.events:
        step = step_of(e)
        if step in PATH:
            seen[step] = e
        if e.kind == "closed" and e.detail.get("exit") == "hired":
            seen["hired"] = e
    marks: dict[str, Mark] = {}
    received = next((e for e in s.events if e.kind == "received"), None)
    marks["new"] = Mark("new", received.at[:10] if received else "")
    #: This team's interviews, and any other the log already holds.
    way = path(cfg, list(seen) + [st.id])
    vs = votes(s)
    if vs:
        marks["votes_in"] = Mark("votes_in", min(v.at for v in vs.values())[:10])
        o = outcome(vs, cfg)
        if o.status == "agreed" and o.meaning == "contact":
            marks["to_contact"] = Mark("to_contact", max(vs[v].at for v in o.voted)[:10])
    ahead = went_ahead(s)
    if ahead is not None:
        marks["to_contact"] = Mark("to_contact", ahead.at[:10], ahead.by)
    for step, e in seen.items():
        marks[step] = Mark(step, day_of(e), e.by)
    current = st.id if st.id in way else "votes_in" if st.id == "team_to_decide" else None
    furthest = max((way.index(k) for k in marks), default=0)
    if current is not None:
        furthest = max(furthest, way.index(current))
    out = []
    for i, k in enumerate(way):
        m = marks.get(k, Mark(k))
        if k == current:
            m.status = "planned" if st.planned else "now"
            if st.id == "team_to_decide":
                m.id = st.id
            if not m.on:
                m.on, m.by = st.on, st.by
        elif i < furthest or k in marks and i <= furthest:
            m.status = "done"
        out.append(m)
    return out, st


def said(st: State, cfg: Config) -> str:
    """The state in a few words: "Contacted 3 Oct by Ana", "Talk later -- until 12 Jan"."""
    n = name(st.id, cfg)
    if st.id == "talk_later":
        return f"{n} until {short(st.until)}" if st.until else n
    if st.id == "votes_in" and st.note:
        return f"{n} ({st.note})"
    if st.id in ("new", "votes_in", "team_to_decide", "to_contact", "to_answer_no"):
        return n
    when = short(st.on)
    out = f"{n} {'planned ' if st.planned else ''}{when}".rstrip()
    return out + (f" by {st.by}" if st.by else "")


def short(day: str) -> str:
    """'2026-10-03' -> '3 Oct'. Anything that is not a date comes back as it was."""
    try:
        d = date.fromisoformat(day[:10])
    except ValueError:
        return day
    return f"{d.day} {d.strftime('%b')}" + ("" if d.year == date.today().year
                                            else f" {d.year}")


def next_step(s: Standing, cfg: Config, now: datetime | None = None) -> str:
    """What this application waits for. The votes' phase is `desk.next_step`'s."""
    now = now or datetime.now(timezone.utc)
    st = derive(s, cfg, now)
    if st.id in TALKING:
        last = next(e for e in reversed(s.events) if step_of(e) == st.id)
        replied = after(s.events, last, ("replied",))
        if st.id == "contacted":
            if replied:
                return f"replied, {name(ROUNDS[0], cfg).lower()} to arrange"
            days = int((now - instant(last)).total_seconds() // 86400)
            return f"no reply for {days} day{'s' if days != 1 else ''}"
        if st.id == "replied":
            return f"{name(ROUNDS[0], cfg).lower()} to plan"
        if st.planned:
            days = (date.fromisoformat(st.on) - _today(now)).days
            return f"{name(st.id, cfg).lower()} in {days} day{'s' if days != 1 else ''}"
        if st.id in ROUNDS:
            return f"{name(forward(st.id, cfg), cfg).lower()} to plan"
        if st.id == "trial_day":
            return "recap of the day to write in the notes"
        return "waiting for their answer"
    if st.id == "team_to_decide":
        return name("team_to_decide", cfg).lower()
    if st.id == "to_contact" and not st.back:
        return f"mail to send ({cfg.label('contact')})"
    if st.id == "to_answer_no":
        return f"mail to send ({cfg.label('pass')})"
    return desk.next_step(s, cfg, now)
