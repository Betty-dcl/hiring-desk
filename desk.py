#!/usr/bin/env python3
"""The hiring desk: one card per person, three votes, a Tuesday recap, the drafts.

Everything else in this repository is about whether a score can be trusted.
This file is about the person who has to do something with it -- between a
bank call and the Monday deck, with two partners who each want a say and are
never in the same room.

    # bring every screened application onto the desk, one person per human
    ./.venv/bin/python desk.py sync

    # the card, as one voter sees it
    ./.venv/bin/python desk.py card --as Recruiter --candidate sylvia_hartmann --posting founders_associate

    # class it
    ./.venv/bin/python desk.py vote --as Recruiter --candidate sylvia_hartmann \\
        --posting founders_associate --label later --until 2027-01-15 --comment "after the round"

    # Tuesday
    ./.venv/bin/python desk.py recap --as Recruiter

    # the mail, ready for a person to send
    ./.venv/bin/python desk.py draft --candidate sylvia_hartmann --posting founders_associate

    # the same, in a browser, on this machine only
    ./.venv/bin/python desk.py serve

Four rules, each enforced here:

**A card is as short as a decision needs.** Name, role, the machine's fit
percentage, and four buttons. Everything behind the number is one command
away (`triage.py explain`), and none of it is in the way.

**Vote first, then see.** Each voter classes an application before the
others' votes are shown to them -- the first opinion in a room anchors every
opinion after it, and three people who each saw the first vote are one vote
counted three times. When the rule bites, and whether it applies at all, is a
setting in `desk.toml`.

**One person, one card.** Somebody who applies to two roles, or comes back in
six months, is one person with a history -- not two cards that two partners
answer separately. Same email is the same person. The same name alone is
only *probably* the same person, and a human confirms it: merging two
strangers' records is worse than showing one person twice.

**The desk prepares; a person acts.** A settled class produces a mail draft.
Sending it is a person's act, recorded under their name. No mail leaves on a
machine's say-so, and nothing is ever triggered by the fit percentage -- only
by a class people chose.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import tomllib
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any

# Run as a script this file is "__main__", while the console imports "desk":
# one module under both names, so there is a single DeskError and a single Config.
if __name__ == "__main__":
    sys.modules.setdefault("desk", sys.modules["__main__"])

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from pipeline import Event, Pipeline, PipelineError, Standing, after
from trial import PROTECTED, _hits

#: What a vote can mean. Fixed, because the desk acts on each one; what each
#: is *called* is a setting. In the order they are shown: from the warmest to
#: the coldest, so "let's discuss" sits beside the yes it leans towards.
MEANINGS = ("contact", "discuss", "later", "pass")
#: The names this desk shipped with before, read as the same meanings: a
#: command typed from an old note, or a log someone wrote by hand, still works.
LEGACY_LABELS = {"interview": "contact", "keep on file": "later", "discuss": "discuss",
                 "not interested": "pass", "yes, let's talk": "contact",
                 "maybe, let's discuss": "discuss"}
BLIND = ("until_you_vote", "until_all_voted", "off")
AGREEMENT = ("unanimous", "majority", "any_contact")
#: Which date a settled "later" wakes up on when the voters gave different ones.
LATER_DATE = ("earliest", "latest")
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

#: A comment is a line, not a memo. Anything longer belongs in a conversation.
COMMENT_MAX = 280


class DeskError(ValueError):
    """Something the desk refuses to record."""


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

@dataclass
class Config:
    voters: list[str]
    labels: dict[str, str]
    blind: str = "until_you_vote"
    agreement: str = "unanimous"
    pass_reasons: list[str] = field(default_factory=list)
    pass_reason_required: bool = False
    later_date: str = "earliest"
    recap_weekday: str = "tuesday"
    awaiting_vote_days: float = 6
    no_reply_days: float = 8
    mail_mode: str = "draft"
    company: str = ""
    sender: str = ""
    attach_note_on_pass: bool = True
    subjects: dict[str, str] = field(default_factory=dict)
    templates: dict[str, str] = field(default_factory=dict)
    #: What desk.toml ships: shown in grey where nobody has written their own,
    #: and used until somebody does -- marked as the example it is.
    example_subjects: dict[str, str] = field(default_factory=dict)
    example_templates: dict[str, str] = field(default_factory=dict)
    #: What each state of an application is called (stages.py). Empty: the defaults.
    state_names: dict[str, str] = field(default_factory=dict)
    #: Who may move an application to its next state. Empty: any voter.
    advancers: list[str] = field(default_factory=list)
    #: How far ahead an interview or a trial day may be planned, in days.
    plan_ahead_days: int = 120
    #: How many interviews come before the trial day (stages.ROUNDS).
    interviews: int = 4
    #: How long an interview lasts, in minutes, on the calendar and in the
    #: "Add to Google Calendar" link. A trial day lasts the day.
    meeting_minutes: int = 45
    #: The time zone the times typed on the desk are in.
    timezone: str = "Europe/Madrid"
    #: Whether "later" must carry the day to come back. False: someone can be
    #: kept in the pool with no date -- liked, not for now -- and found again
    #: under that state, in its own tab.
    later_needs_date: bool = True
    #: When one partner may go ahead without waiting for the others' votes
    #: (stages.GO_AHEAD): after their own yes (default), once all have voted,
    #: or only the people named in `advancers`.
    go_ahead_alone: str = "after_one_yes"
    #: One person deciding: "Not sure" or "Keep in the pool" switched off.
    choices_off: list[str] = field(default_factory=list)
    #: One person deciding: choices of their own, each keeping people aside in a
    #: tab of its own, as the pool does (console/choices_web.py).
    extra_choices: list[str] = field(default_factory=list)
    #: Mails the team does not write at all (no reminder, say): never offered.
    mails_off: list[str] = field(default_factory=list)
    #: Look in the Outlook mailbox of the person who runs hiring for replies
    #: from the people written to (outlook.py). Off unless the team turns it on.
    outlook: bool = False

    @property
    def solo(self) -> bool:
        """One person decides: a vote is the decision, and nothing waits on anyone else."""
        return len(self.voters) == 1

    #: Every interview is invited to with the same mail, "interview", whose
    #: {interview} says which one. A team may still give one interview words
    #: of its own (desk.toml [mail.templates] interview_2 = ...).
    INTERVIEWS = ("first_call", "interview_2", "interview_3", "interview_4", "interview_5",
                  "interview_6")

    def _chain(self, meaning: str) -> tuple[str, ...]:
        """Where the words for a mail are looked for, in order."""
        if meaning in self.INTERVIEWS:
            #: "next_interview" is the general invitation's older name.
            return (meaning, "interview", "next_interview")
        return (meaning,)

    def _own(self, meaning: str, kind: str) -> str:
        mine, example = ((self.subjects, self.example_subjects) if kind == "subject" else
                         (self.templates, self.example_templates))
        for m in self._chain(meaning):
            if mine.get(m):
                return mine[m]
            if example.get(m):
                return example[m]
        return ""

    def subject(self, meaning: str) -> str:
        return self._own(meaning, "subject") or "{role}"

    def template(self, meaning: str) -> str:
        return self._own(meaning, "template")

    def is_example(self, meaning: str) -> bool:
        """True while the words for this mail are still ours, not theirs."""
        for m in self._chain(meaning):
            if self.templates.get(m):
                return False
            if self.example_templates.get(m):
                return True
        return True

    def __post_init__(self) -> None:
        problems = []
        if len(self.voters) != len({v.lower() for v in self.voters}) or not self.voters:
            problems.append("team.voters must name at least one person, each once")
        if self.blind not in BLIND:
            problems.append(f"votes.blind must be one of {', '.join(BLIND)}")
        if self.agreement not in AGREEMENT:
            problems.append(f"votes.agreement must be one of {', '.join(AGREEMENT)}")
        if self.later_date not in LATER_DATE:
            problems.append(f"votes.later_date must be one of {', '.join(LATER_DATE)}")
        if self.recap_weekday not in WEEKDAYS:
            problems.append(f"recap.weekday must be a day of the week")
        if set(self.labels) != set(MEANINGS):
            problems.append(f"votes needs a name for each of {', '.join(MEANINGS)}")
        names = [n.lower() for n in self.labels.values()]
        if len(names) != len(set(names)):
            problems.append("two meanings share a name; nobody could tell which was meant")
        from stages import DEFAULT_NAMES, ROUNDS, names as state_names
        unknown = set(self.state_names) - set(DEFAULT_NAMES)
        if unknown:
            problems.append(f"states: no state called {', '.join(sorted(unknown))}")
        shown = [str(n).strip().lower() for n in state_names(self).values()]
        if len(shown) != len(set(shown)):
            problems.append("two states share a name; nobody could tell which was meant")
        try:
            few = not 1 <= int(self.interviews) <= len(ROUNDS)
        except (TypeError, ValueError):
            few = True
        if few:
            problems.append(f"states.interviews must be a number between 1 and {len(ROUNDS)}")
        else:
            self.interviews = int(self.interviews)
        voters = {v.lower() for v in self.voters}
        strangers = [a for a in self.advancers if a.lower() not in voters]
        if strangers:
            problems.append(f"states.advancers must be voters: {', '.join(strangers)}")
        from stages import GO_AHEAD
        if self.go_ahead_alone not in GO_AHEAD:
            problems.append(f"votes.go_ahead_alone must be one of {', '.join(GO_AHEAD)}")
        elif self.go_ahead_alone == "owner" and not self.advancers:
            problems.append('votes.go_ahead_alone = "owner" needs states.advancers to name them')
        if not 1 <= int(self.plan_ahead_days) <= 730:
            problems.append("states.plan_ahead_days must be between 1 and 730")
        if not 5 <= int(self.meeting_minutes) <= 600:
            problems.append("states.meeting_minutes must be between 5 and 600")
        if set(self.choices_off) - {"discuss", "later"}:
            problems.append("votes.off can switch off \"discuss\" and \"later\" only: yes and "
                            "no are always there")
        if len({x.lower() for x in self.extra_choices}) != len(self.extra_choices) or \
                len(self.extra_choices) > 6:
            problems.append("votes.own: at most six choices of your own, each named once")
        if problems:
            raise DeskError("desk.toml: " + "; ".join(problems))

    def label(self, meaning: str) -> str:
        return self.labels.get(meaning, meaning)

    def meaning(self, said: str) -> str:
        """Accept a meaning or its configured name, in any case."""
        s = said.strip().lower()
        for m, name in self.labels.items():
            if s in (m, name.lower()):
                return m
        #: Only after every current name: a team that reused an old word for
        #: another meaning means that meaning by it.
        if s in LEGACY_LABELS:
            return LEGACY_LABELS[s]
        raise DeskError(f"{said!r} is not a class here. Use one of: "
                        f"{', '.join(self.labels.values())}")

    def voter(self, said: str) -> str:
        for v in self.voters:
            if v.lower() == said.strip().lower():
                return v
        raise DeskError(f"{said!r} does not vote on this desk. Voters: "
                        f"{', '.join(self.voters)} (set in desk.toml)")


#: The fields a mail can carry. Filled by name, and nothing else: a template
#: edited in the browser is text somebody typed, and Python's str.format on it
#: would let "{first_name.__class__}" reach into the program.
#: {day} and {time} are those of the meeting an invitation is for, once it is
#: planned; until then they show as [day] and [time], blanks to fill in.
FIELDS = ("first_name", "last_name", "role", "company", "sender", "interview", "day", "time")
FIELD = re.compile(r"\{(" + "|".join(FIELDS) + r")\}")


def fill_in(text: str, values: dict[str, str]) -> str:
    """Replace the known {fields}; leave every other brace exactly as typed."""
    return FIELD.sub(lambda m: values.get(m.group(1), ""), text)


def _settings_path() -> Path:
    return ROOT / "runs" / "desk" / "settings.json"


#: What the settings page may change, and nothing else. Access, Ashby and the
#: mail mode stay in desk.toml: they decide who gets in and what leaves the
#: company, which is not a form field.
EDITABLE = ("voters", "labels", "blind", "agreement", "pass_reasons", "recap_weekday",
            "company", "sender", "subjects", "templates", "state_names", "advancers",
            "go_ahead_alone", "interviews", "outlook", "mails_off", "choices_off",
            "extra_choices")


def load_config(path: str | Path | None = None, *, overrides: bool = True) -> Config:
    """desk.toml, then whatever the team changed on the settings page."""
    base = _from_toml(path)
    if not overrides:
        return base
    f = _settings_path()
    if not f.exists():
        return base
    return _apply(base, json.loads(f.read_text(encoding="utf-8")).get("values", {}))


def _apply(base: Config, values: dict[str, Any]) -> Config:
    d = asdict(base)
    for k, v in values.items():
        if k not in EDITABLE:
            continue
        if k in ("subjects", "templates"):
            d[k] = {m: t for m, t in {**d[k], **v}.items() if str(t).strip()}
        elif k in ("labels", "state_names"):
            d[k] = {**d[k], **{m: n for m, n in v.items() if str(n).strip()}}
        else:
            d[k] = v
    return Config(**d)


def save_settings(values: dict[str, Any], *, by: str) -> Config:
    """Keep what the team changed, with who changed it. Refuses what cannot run."""
    if not by.strip():
        raise DeskError("a change to the settings needs the name of whoever made it")
    unknown = set(values) - set(EDITABLE)
    if unknown:
        raise DeskError(f"not editable here: {', '.join(sorted(unknown))}")
    with locked():
        f = _settings_path()
        rec = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"values": {}}
        old = rec.get("values", {})
        merged = {**old, **values}
        #: Mail words are kept mail by mail: a save that does not show one
        #: (an interview no longer run) does not erase it.
        for k in ("subjects", "templates"):
            if k in values and isinstance(old.get(k), dict):
                merged[k] = {**old[k], **values[k]}
        cfg = _apply(load_config(overrides=False), merged)  # raises if it cannot run
        rec["values"] = merged
        rec.setdefault("history", []).append(
            {"by": by, "at": datetime.now(timezone.utc).isoformat(), "changed": sorted(values)})
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return cfg


def _from_toml(path: str | Path | None = None) -> Config:
    raw = tomllib.loads(Path(path or ROOT / "desk.toml").read_text(encoding="utf-8"))
    team, votes = raw.get("team", {}), raw.get("votes", {})
    recap, mail = raw.get("recap", {}), raw.get("mail", {})
    states = raw.get("states", {})
    return Config(
        voters=list(team.get("voters", [])),
        labels={m: str(votes.get(m, m)) for m in MEANINGS},
        blind=votes.get("blind", "until_you_vote"),
        agreement=votes.get("agreement", "unanimous"),
        pass_reasons=list(votes.get("pass_reasons", [])),
        pass_reason_required=bool(votes.get("pass_reason_required", False)),
        later_date=str(votes.get("later_date", "earliest")),
        recap_weekday=str(recap.get("weekday", "tuesday")).lower(),
        awaiting_vote_days=float(recap.get("awaiting_vote_days", 6)),
        no_reply_days=float(recap.get("no_reply_days", 8)),
        mail_mode=mail.get("mode", "draft"),
        company=mail.get("company", ""),
        sender=mail.get("sender", ""),
        attach_note_on_pass=bool(mail.get("attach_note_on_pass", True)),
        example_subjects=dict(mail.get("subjects", {})),
        example_templates={k: v.strip("\n") for k, v in mail.get("templates", {}).items()},
        state_names={k: str(v) for k, v in states.get("names", {}).items()},
        advancers=[str(a) for a in states.get("advancers", [])],
        plan_ahead_days=int(states.get("plan_ahead_days", 120)),
        meeting_minutes=int(states.get("meeting_minutes", 45)),
        timezone=str(states.get("timezone", "Europe/Madrid")),
        interviews=states.get("interviews", 4),
        later_needs_date=bool(votes.get("later_needs_date", True)),
        go_ahead_alone=str(votes.get("go_ahead_alone", "after_one_yes")),
        outlook=bool(raw.get("outlook", {}).get("enabled", False)),
        mails_off=[str(m) for m in mail.get("off", [])],
        choices_off=[str(m) for m in votes.get("off", [])],
        extra_choices=[str(m) for m in votes.get("own", [])],
    )


# --------------------------------------------------------------------------
# People: one card per human, across roles and across time
# --------------------------------------------------------------------------

def normalise_name(name: str) -> str:
    """Accent-, case- and spacing-insensitive, for matching only."""
    folded = unicodedata.normalize("NFKD", name)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", folded).strip().lower()


@dataclass
class Application:
    posting_id: str
    candidate_id: str

    def key(self) -> tuple[str, str]:
        return (self.posting_id, self.candidate_id)


@dataclass
class Document:
    """A file somebody attached: the CV, the letter, anything else."""

    posting_id: str
    kind: str
    stored: str
    original: str
    added_by: str = ""
    at: str = ""


@dataclass
class Person:
    person_id: str
    name: str
    email: str = ""
    applications: list[Application] = field(default_factory=list)
    #: The profile link -- LinkedIn, usually. One, because a card is short.
    link: str = ""
    documents: list[Document] = field(default_factory=list)
    #: Everything else they pointed at: a repository, a demo, a thing they
    #: built. For the people this desk was built for, the most important line
    #: on the card -- "we couldn't care less what their resume says".
    links: list[str] = field(default_factory=list)

    @property
    def all_links(self) -> list[str]:
        return ([self.link] if self.link else []) + [l for l in self.links if l != self.link]

    @property
    def first_name(self) -> str:
        return self.name.split()[0] if self.name.strip() else ""

    def documents_for(self, posting_id: str) -> list[Document]:
        """Files for one application, then the ones that came with any other.

        A CV sent for one role is still that person's CV when they apply for
        the next; it is shown, and labelled with where it came from.
        """
        own = [d for d in self.documents if d.posting_id == posting_id]
        return own + [d for d in self.documents if d.posting_id != posting_id
                      and not any(o.kind == d.kind for o in own)]

    def to_dict(self) -> dict[str, Any]:
        return {"person_id": self.person_id, "name": self.name, "email": self.email,
                "link": self.link, "links": list(self.links),
                "applications": [asdict(a) for a in self.applications],
                "documents": [asdict(d) for d in self.documents]}


@dataclass
class Registry:
    people: dict[str, Person] = field(default_factory=dict)
    #: Same name, no shared email: probably one person, confirmed by a human.
    possible: list[dict[str, str]] = field(default_factory=list)
    #: Every merge, with who confirmed it -- a merge can be wrong, and the
    #: record of it is how someone later works out which.
    merges: list[dict[str, str]] = field(default_factory=list)

    def person_of(self, candidate_id: str) -> Person | None:
        for p in self.people.values():
            if any(a.candidate_id == candidate_id for a in p.applications):
                return p
        return None

    def add(self, candidate_id: str, posting_id: str, name: str, email: str = "") -> Person:
        """Place one application on a person, creating them if needed.

        In order: the same extracted candidate is the same person; the same
        email is the same person; otherwise a new person -- and if their name
        matches someone already here, the pair is *suggested*, never merged.
        """
        app = Application(posting_id, candidate_id)
        p = self.person_of(candidate_id)
        if p is None and email:
            p = next((x for x in self.people.values()
                      if x.email and x.email.lower() == email.lower()), None)
        if p is None:
            pid = candidate_id
            while pid in self.people:
                pid += "_"
            p = Person(pid, name, email)
            twin = next((x for x in self.people.values()
                         if normalise_name(x.name) == normalise_name(name) and name.strip()),
                        None)
            self.people[pid] = p
            if twin is not None and not self._suggested(twin.person_id, pid):
                self.possible.append({"a": twin.person_id, "b": pid,
                                      "why": f"same name: {name}"})
        if not any(a.key() == app.key() for a in p.applications):
            p.applications.append(app)
        if email and not p.email:
            p.email = email
        return p

    def _suggested(self, a: str, b: str) -> bool:
        return any({x["a"], x["b"]} == {a, b} for x in self.possible)

    def merge(self, keep: str, into_it: str, *, by: str) -> Person:
        if not by.strip():
            raise DeskError("a merge needs the name of whoever confirmed it")
        if keep not in self.people or into_it not in self.people or keep == into_it:
            raise DeskError(f"cannot merge {into_it!r} into {keep!r}")
        a, b = self.people[keep], self.people.pop(into_it)
        for app in b.applications:
            if not any(x.key() == app.key() for x in a.applications):
                a.applications.append(app)
        a.email = a.email or b.email
        self.possible = [x for x in self.possible if into_it not in (x["a"], x["b"])]
        self.merges.append({"kept": keep, "merged": into_it, "by": by,
                            "at": datetime.now(timezone.utc).isoformat()})
        return a

    def to_dict(self) -> dict[str, Any]:
        return {"people": [p.to_dict() for p in self.people.values()],
                "possible_duplicates": self.possible, "merges": self.merges}


def load_registry(path: str | Path) -> Registry:
    f = Path(path)
    if not f.exists():
        return Registry()
    d = json.loads(f.read_text(encoding="utf-8"))
    r = Registry(possible=d.get("possible_duplicates", []), merges=d.get("merges", []))
    for p in d.get("people", []):
        r.people[p["person_id"]] = Person(
            p["person_id"], p["name"], p.get("email", ""),
            [Application(**a) for a in p.get("applications", [])],
            link=p.get("link", ""),
            documents=[Document(**d) for d in p.get("documents", [])],
            links=list(p.get("links", [])))
    return r


def save_registry(r: Registry, path: str | Path) -> Path:
    f = Path(path)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(r.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return f


EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def email_of(facts: Any) -> str:
    """An address stated in the CV, if the extractor kept one."""
    for f in getattr(facts, "facts", []) or []:
        m = EMAIL.search(f.source_quote or "") or EMAIL.search(f.claim or "")
        if m:
            return m.group(0)
    return ""


# --------------------------------------------------------------------------
# Votes
# --------------------------------------------------------------------------

@dataclass
class Vote:
    voter: str
    meaning: str
    at: str
    until: str = ""
    comment: str = ""
    reason: str = ""


def votes(s: Standing) -> dict[str, Vote]:
    """Each voter's latest vote. Earlier ones stay in the log, not in the count."""
    out: dict[str, Vote] = {}
    for e in s.events:
        if e.kind == "voted":
            d = e.detail
            out[e.by] = Vote(e.by, d["label"], e.at, d.get("until", ""),
                             d.get("comment", ""), d.get("reason", ""))
    return out


@dataclass
class Outcome:
    #: "voting" until the rule can settle it; then "agreed" or "disagreed".
    status: str
    #: What was agreed -- a meaning -- or "discuss" when they disagreed.
    meaning: str | None = None
    until: str = ""
    voted: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def settled(self) -> bool:
        return self.status != "voting"


def outcome(vs: dict[str, Vote], cfg: Config) -> Outcome:
    """What the votes add up to, under the configured rule. Derived, never stored."""
    voted = [v for v in cfg.voters if v in vs]
    missing = [v for v in cfg.voters if v not in vs]
    meanings = [vs[v].meaning for v in voted]
    asked = [vs[v].until for v in voted if vs[v].meaning == "later" and vs[v].until]
    until = (max if cfg.later_date == "latest" else min)(asked, default="")

    if cfg.agreement == "any_contact" and "contact" in meanings:
        return Outcome("agreed", "contact", voted=voted, missing=missing)
    if cfg.agreement == "majority":
        for m in MEANINGS:
            if meanings.count(m) * 2 > len(cfg.voters):
                return Outcome("agreed", m, until if m == "later" else "", voted, missing)
        if "discuss" in meanings:
            # Someone asked for the team: it goes there now, not once all have voted.
            return Outcome("disagreed", "discuss", voted=voted, missing=missing)
        if missing:
            return Outcome("voting", voted=voted, missing=missing)
        return Outcome("disagreed", "discuss", voted=voted, missing=missing)
    if "discuss" in meanings or len(set(meanings)) > 1:
        # Under "everyone agrees", one "not sure" or two different votes can no
        # longer end in agreement: it goes to the team now, without waiting for
        # the missing votes (which can still be cast).
        return Outcome("disagreed", "discuss", voted=voted, missing=missing)
    if missing:
        return Outcome("voting", voted=voted, missing=missing)
    if len(set(meanings)) == 1:
        m = meanings[0]
        #: By default the earliest date anyone asked for, so nobody's "look again in
        #: January" is silently pushed to somebody else's June.
        return Outcome("agreed", m, until if m == "later" else "", voted, missing)
    return Outcome("disagreed", "discuss", voted=voted, missing=missing)


def visible_to(viewer: str, vs: dict[str, Vote], cfg: Config) -> dict[str, Vote | None]:
    """The other voters, and each vote this viewer may see (None = hidden)."""
    everyone_voted = all(v in vs for v in cfg.voters)
    show = (cfg.blind == "off"
            or (cfg.blind == "until_you_vote" and viewer in vs)
            or (cfg.blind == "until_all_voted" and everyone_voted))
    return {v: (vs.get(v) if show else None) for v in cfg.voters if v != viewer}


#: A note is for the others, not a report. Longer belongs in a conversation.
NOTE_MAX = 600


def may_read_notes(viewer: str, s: Standing, cfg: Config) -> bool:
    """Notes follow the vote's blind rule: "I'd pass on her" is a vote in prose."""
    vs = votes(s)
    return (cfg.blind == "off"
            or (cfg.blind == "until_you_vote" and viewer in vs)
            or (cfg.blind == "until_all_voted" and all(v in vs for v in cfg.voters)))


@dataclass
class Note:
    by: str
    at: str
    text: str | None  # None: hidden from this viewer until they vote


def notes(s: Standing, viewer: str, cfg: Config) -> list[Note]:
    """The thread on one application, oldest first, as this viewer may read it."""
    open_ = may_read_notes(viewer, s, cfg)
    return [Note(e.by, e.at, e.detail["text"] if (open_ or e.by == viewer) else None)
            for e in s.events if e.kind == "noted"]


def write_note(pipe: Pipeline, candidate_id: str, by: str, text: str, cfg: Config) -> Event:
    by = cfg.voter(by)
    text = text.strip()
    if candidate_id not in pipe.candidates:
        raise DeskError(f"{candidate_id} has no application for {pipe.posting_id}")
    if not text:
        raise DeskError("an empty note says nothing")
    if len(text) > NOTE_MAX:
        raise DeskError(f"a note is {NOTE_MAX} characters at most -- longer is a conversation")
    hits = _hits(text, PROTECTED)
    if hits:
        raise DeskError(f"the note touches a protected subject ({', '.join(hits)}) and cannot "
                        f"go on the record")
    return pipe.add(Event(candidate_id=candidate_id, kind="noted", by=by,
                          detail={"text": text}))


def cast(pipe: Pipeline, candidate_id: str, voter: str, label: str, cfg: Config, *,
         until: str = "", comment: str = "", reason: str = "",
         now: datetime | None = None) -> list[Event]:
    """Record one vote, and what it settles. Refuses rather than guesses.

    Returns every event written. When this vote settles an application as
    "later", the date goes on the record as a `revisit` in the voters' names,
    so the board wakes it -- a "later" that is only a label is a "never".
    """
    voter = cfg.voter(voter)
    meaning = cfg.meaning(label)
    comment = re.sub(r"\s+", " ", comment).strip()
    reason = re.sub(r"\s+", " ", reason).strip()
    if candidate_id not in pipe.candidates:
        raise DeskError(f"{candidate_id} has no application for {pipe.posting_id}")
    if pipe.standing(candidate_id).state == "closed":
        raise DeskError(f"{candidate_id} is closed for {pipe.posting_id}; reopen it first")
    if meaning == "later":
        if not until and cfg.later_needs_date:
            raise DeskError(f"'{cfg.label('later')}' needs a date. Later without a date is "
                            f"how people are forgotten.")
        if until:
            try:
                datetime.fromisoformat(until)
            except ValueError as e:
                raise DeskError(f"{until!r} is not a date (YYYY-MM-DD)") from e
    elif until:
        raise DeskError(f"a date only means something with '{cfg.label('later')}'")
    if meaning == "pass" and cfg.pass_reason_required and not reason:
        raise DeskError(f"'{cfg.label('pass')}' needs a reason here. One of: "
                        f"{', '.join(cfg.pass_reasons)} -- or your own words")
    if reason and meaning != "pass":
        raise DeskError(f"a reason goes with '{cfg.label('pass')}' only; use --comment")
    if len(comment) > COMMENT_MAX:
        raise DeskError(f"a comment is one line, {COMMENT_MAX} characters at most")
    for text, what in ((comment, "comment"), (reason, "reason")):
        hits = _hits(text, PROTECTED)
        if hits:
            raise DeskError(f"the {what} touches a protected subject ({', '.join(hits)}) "
                            f"and cannot go on the record")

    before = outcome(votes(pipe.standing(candidate_id)), cfg)
    detail: dict[str, Any] = {"label": meaning}
    if until:
        detail["until"] = until
    if comment:
        detail["comment"] = comment
    if reason:
        detail["reason"] = reason
    at = (now or datetime.now(timezone.utc)).isoformat()
    written = [pipe.add(Event(candidate_id=candidate_id, kind="voted", by=voter,
                              detail=detail, at=at))]

    after_vs = votes(pipe.standing(candidate_id))
    after = outcome(after_vs, cfg)
    #: Kept with no date: nothing to wake, so nothing is put on the board. The
    #: application is found under its state, not by a date.
    if after.meaning == "later" and after.settled and after.until and (
            before.meaning, before.until) != (after.meaning, after.until):
        backers = [v for v in after.voted if after_vs[v].meaning == "later"]
        why = "; ".join(f"{v}: {after_vs[v].comment}" for v in backers if after_vs[v].comment)
        written.append(pipe.add(Event(
            candidate_id=candidate_id, kind="revisit", by=", ".join(backers),
            reason=why or f"voted {cfg.label('later')} by {', '.join(backers)}",
            detail={"on": after.until}, at=at)))
    return written


# --------------------------------------------------------------------------
# The card
# --------------------------------------------------------------------------

def card(person: Person, app: Application, s: Standing, cfg: Config, viewer: str,
         titles: dict[str, str], history: list[tuple[Application, Outcome]] | None = None) -> str:
    """The card as one voter sees it: their vote, then the others'."""
    viewer = cfg.voter(viewer)
    vs = votes(s)
    fit = "--" if s.score is None else f"{s.score:.0%}"
    if s.spread:
        fit += f"  (+/-{s.spread:.0%})"
    lines = [f"{person.name} · {titles.get(app.posting_id, app.posting_id)}",
             f"AI guess against the posting: {fit}"]
    from signals import signals
    lines += [f"  {x}" for x in signals(person, app.candidate_id, app.posting_id).lines()] + [""]

    mine = vs.get(viewer)
    buttons = "  ".join(f"[{cfg.label(m)}]" for m in MEANINGS)
    if mine is None:
        lines.append("Your class: not yet")
        lines.append(f"  {buttons}")
    else:
        lines.append(f"Your class: {_said(mine, cfg)}")

    others = visible_to(viewer, vs, cfg)
    parts = []
    for v, vote in others.items():
        if v not in vs:
            parts.append(f"{v}: not yet")
        elif vote is None:
            parts.append(f"{v}: voted")
        else:
            parts.append(f"{v}: {_said(vote, cfg)}")
    lines.append("")
    lines.append("  ·  ".join(parts))
    if any(v is None and o in vs for o, v in others.items()):
        lines.append("(hidden until you vote)" if cfg.blind == "until_you_vote"
                     else "(hidden until everyone has voted)")

    o = outcome(vs, cfg)
    if o.settled:
        lines.append("")
        if o.status == "agreed":
            tail = f" -- back on {o.until}" if o.until else ""
            lines.append(f"Settled: {cfg.label(o.meaning)}{tail}")
        else:
            lines.append("Not agreed -- to settle together")
    for past, po in history or []:
        state = (cfg.label(po.meaning) if po.settled and po.meaning else "in progress")
        lines.append(f"Also applied: {titles.get(past.posting_id, past.posting_id)} -- {state}")
    return "\n".join(lines)


def _said(v: Vote, cfg: Config) -> str:
    out = cfg.label(v.meaning)
    if v.until:
        out += f" (until {v.until})"
    if v.reason:
        out += f" -- {v.reason}"
    if v.comment:
        out += f' -- "{v.comment}"'
    return out


# --------------------------------------------------------------------------
# The recap
# --------------------------------------------------------------------------

@dataclass
class Recap:
    since: datetime
    now: datetime
    new: dict[str, list[str]] = field(default_factory=dict)
    awaiting: dict[str, list[tuple[str, float]]] = field(default_factory=dict)
    no_reply: list[tuple[str, float]] = field(default_factory=list)
    wake: list[tuple[str, str, str]] = field(default_factory=list)
    disagreed: list[str] = field(default_factory=list)
    decided: dict[str, list[str]] = field(default_factory=dict)
    drafts_waiting: list[str] = field(default_factory=list)
    returning: list[str] = field(default_factory=list)
    possible_duplicates: list[str] = field(default_factory=list)
    viewer: str = ""

    def render(self, cfg: Config) -> str:
        day = self.now.strftime("%A %d/%m")
        lines = [f"{day} -- since {self.since.strftime('%A %d/%m')}", ""]
        b = lines.append
        total = sum(len(v) for v in self.new.values())
        if total:
            per = ", ".join(f"{len(v)} {k}" for k, v in self.new.items())
            b(f"- {total} new application(s) ({per})")
        else:
            b("- no new applications")
        for voter, items in self.awaiting.items():
            whose = "your" if voter == self.viewer else f"{voter}'s"
            names = ", ".join(n for n, _ in items)
            oldest = max(d for _, d in items)
            b(f"- {len(items)} waiting for {whose} vote, up to {oldest:.0f} days: {names}")
        for name, days in self.no_reply:
            b(f"- {name}: written to {days:.0f} days ago, no reply")
        for name, on, why in self.wake:
            b(f"- Wake-up: {name}, put in {cfg.label('later')} until {on} -- \"{why}\"")
        if self.disagreed:
            b(f"- {len(self.disagreed)} to settle together: "
              f"{', '.join(self.disagreed)}")
        settled = [f"{len(v)} {cfg.label(k)}" for k, v in self.decided.items() if v]
        if settled:
            b(f"- Settled this week: {', '.join(settled)}")
        if self.drafts_waiting:
            b(f"- Mail drafts waiting to be sent: {', '.join(self.drafts_waiting)}")
        for r in self.returning:
            b(f"- Applied again: {r}")
        for d in self.possible_duplicates:
            b(f"- Possibly the same person, to confirm: {d}")
        if len(lines) == 3 and not total:
            b("- nothing else needs anyone this week")
        return "\n".join(lines)


def last_recap_day(now: datetime, weekday: str) -> datetime:
    """The most recent recap day strictly before today, at midnight UTC."""
    target = WEEKDAYS.index(weekday)
    back = (now.weekday() - target) % 7 or 7
    d = (now - timedelta(days=back)).date()
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def recap(pipes: dict[str, Pipeline], reg: Registry, cfg: Config, *,
          now: datetime | None = None, since: datetime | None = None,
          viewer: str = "", titles: dict[str, str] | None = None) -> Recap:
    now = now or datetime.now(timezone.utc)
    since = since or last_recap_day(now, cfg.recap_weekday)
    titles = titles or {}
    r = Recap(since=since, now=now, viewer=cfg.voter(viewer) if viewer else "")

    def name(cid: str) -> str:
        p = reg.person_of(cid)
        return p.name if p else cid

    for pid, pipe in pipes.items():
        title = titles.get(pid, pid)
        for cid in pipe.candidates:
            s = pipe.standing(cid)
            received = next((e.when for e in s.events if e.kind == "received"), None)
            if received and received >= since:
                r.new.setdefault(title, []).append(name(cid))
            if s.state == "closed":
                continue
            vs = votes(s)
            o = outcome(vs, cfg)
            if not o.settled and received:
                age = (now - received).total_seconds() / 86400
                if age >= cfg.awaiting_vote_days:
                    for v in o.missing:
                        r.awaiting.setdefault(v, []).append((name(cid), age))
            if o.status == "disagreed" or (o.settled and o.meaning == "discuss"):
                r.disagreed.append(name(cid))
            if o.settled and o.meaning in ("contact", "later", "pass"):
                last_vote = max(vs[v].at for v in o.voted)
                voted_at = datetime.fromisoformat(last_vote)
                if voted_at.tzinfo is None:  # same reading as Event.when
                    voted_at = voted_at.replace(tzinfo=timezone.utc)
                if voted_at >= since:
                    r.decided.setdefault(o.meaning, []).append(name(cid))
                wrote = any(e.kind == "contacted" and e.at >= last_vote for e in s.events)
                if not wrote:
                    r.drafts_waiting.append(name(cid))

            last_contact = next((e for e in reversed(s.events) if e.kind == "contacted"), None)
            if last_contact is not None:
                answered = after(s.events, last_contact, ("replied", "met", "closed"))
                days = (now - last_contact.when).total_seconds() / 86400
                if not answered and days >= cfg.no_reply_days:
                    r.no_reply.append((name(cid), days))

            when = s.revisit_on
            if when and when <= now + timedelta(days=7):
                ev = next(e for e in reversed(s.events) if e.kind == "revisit")
                r.wake.append((name(cid), when.date().isoformat(), ev.reason))

    for p in reg.people.values():
        if len(p.applications) > 1:
            newest = p.applications[-1]
            pipe = pipes.get(newest.posting_id)
            got = (next((e.when for e in pipe.standing(newest.candidate_id).events
                         if e.kind == "received"), None) if pipe else None)
            if got and got >= since:
                r.returning.append(f"{p.name} -- "
                                   + ", ".join(titles.get(a.posting_id, a.posting_id)
                                               for a in p.applications))
    for x in reg.possible:
        a, b = reg.people.get(x["a"]), reg.people.get(x["b"])
        if a and b:
            r.possible_duplicates.append(f"{a.person_id} and {b.person_id} ({x['why']})")
    return r


# --------------------------------------------------------------------------
# Mail drafts
# --------------------------------------------------------------------------

def mail_fields(person: Person, role: str, cfg: Config,
                when: dict[str, str] | None = None) -> dict[str, str]:
    parts = person.name.split()
    when = when or {}
    return {"first_name": person.first_name, "last_name": " ".join(parts[1:]),
            "role": role, "company": cfg.company, "sender": cfg.sender or cfg.company,
            "interview": when.get("interview") or "interview",
            "day": when.get("day") or "[day]", "time": when.get("time") or "[time]"}


def draft(person: Person, role: str, meaning: str, cfg: Config, *,
          note: str = "", when: dict[str, str] | None = None) -> EmailMessage:
    """An unsent message a mail client opens as a draft. Nothing is sent here."""
    if cfg.mail_mode != "draft":
        raise DeskError("only 'draft' mode exists: sending on its own would need a mail "
                        "account, and would be the one place the desk acts outside the "
                        "company without a person")
    text = cfg.template(meaning)
    if not text:
        raise DeskError(f"no mail written for {cfg.label(meaning)!r} yet -- see House rules")
    fill = mail_fields(person, role, cfg, when)
    #: Paragraphs flow: a template is wrapped for whoever edits desk.toml, and
    #: those line breaks mean nothing once a name of another length is in it.
    paragraphs = re.split(r"\n\s*\n", fill_in(text, fill).strip())
    body = "\n\n".join(" ".join(l.strip() for l in p.splitlines()) for p in paragraphs) + "\n"
    if note and meaning == "pass" and cfg.attach_note_on_pass:
        body += "\n" + "-" * 60 + "\n\n" + note + "\n"
    m = EmailMessage()
    #: Headers are one line each, whatever was typed into the settings or read
    #: off a CV: a line break in a subject, a name or a company would otherwise
    #: either crash the draft or, in a laxer mail library, add a Bcc.
    m["To"] = person.email if EMAIL.fullmatch(person.email or "") else ""
    m["Subject"] = " ".join(fill_in(cfg.subject(meaning), fill).split())
    #: Apple Mail and Outlook open a file carrying this header as a draft to
    #: edit and send, rather than as a message that was received.
    m["X-Unsent"] = "1"
    m.set_content(body)
    return m


# --------------------------------------------------------------------------
# Tracking: what happened, and what happens next
# --------------------------------------------------------------------------

def next_step(s: Standing, cfg: Config, now: datetime | None = None) -> str:
    """One line: the next thing this application is waiting for, and on whom.

    A board that shows states tells a reader where things are. What they act
    on is what is next, and who it is waiting for -- so that is what each row
    leads with.
    """
    now = now or datetime.now(timezone.utc)
    if s.state == "closed":
        return "closed"
    vs = votes(s)
    o = outcome(vs, cfg)
    when = s.revisit_on
    #: Votes first: an open disagreement or a missing vote is what the team
    #: owes now, whatever an older parking says about later.
    if not o.settled and vs:
        return "waiting for " + ", ".join(o.missing)
    if o.settled and o.meaning == "discuss":
        return "to settle together"
    if when:
        if when <= now:
            return "wake-up due now"
        if not (o.settled and o.meaning == "later"):
            return f"wakes up on {when.date().isoformat()}"
    if not o.settled:
        return "waiting for " + ", ".join(o.missing)
    last_vote = max(vs[v].at for v in o.voted)
    contacted = [e for e in s.events if e.kind == "contacted" and e.at >= last_vote]
    if not contacted:
        return f"mail to send ({cfg.label(o.meaning)})"
    if o.meaning == "later" and when:
        return f"wakes up on {when.date().isoformat()}"
    if o.meaning == "later" and not o.until:
        return "kept, with no date"
    replied = after(s.events, contacted[-1], ("replied", "met"))
    if replied:
        return "replied -- next conversation to arrange"
    days = int((now - contacted[-1].when).total_seconds() // 86400)
    return f"no reply for {days} day{'s' if days != 1 else ''}"


#: How each event reads in a person's history. Kept next to the code that
#: writes them, so a new kind cannot appear without a sentence.
TIMELINE = {
    "received": "applied",
    "screened": "screened by the machine",
    "read": "read by {by}",
    "voted": "{by} classed it",
    "go_ahead": "{by} went ahead without waiting",
    "noted": "{by} wrote a note",
    "flagged": "flagged by {by}",
    "tagged": "tagged {tag}",
    "untagged": "untagged {tag}",
    "contacted": "{by} wrote to them",
    "replied": "they replied",
    "met": "{by} met them",
    "offered": "{by} made an offer",
    "closed": "closed by {by}",
    "reopened": "reopened by {by}",
    "revisit": "put aside until {on} ({by})",
}


@dataclass
class Moment:
    at: str
    posting_id: str
    kind: str
    text: str
    detail: str = ""


def timeline(person: Person, pipes: dict[str, Pipeline], cfg: Config, viewer: str = "",
             titles: dict[str, str] | None = None) -> list[Moment]:
    """Everything that happened to one person, across every role, newest first.

    Votes follow the same blind rule as the card: a viewer who has not yet
    classed an application sees that a colleague voted, not how. A history
    page that leaked votes would make the blind rule a formality.
    """
    titles = titles or {}
    out: list[Moment] = []
    for app in person.applications:
        pipe = pipes.get(app.posting_id)
        if pipe is None:
            continue
        s = pipe.standing(app.candidate_id)
        shown = visible_to(viewer, votes(s), cfg) if viewer else {}
        for e in s.events:
            text = TIMELINE.get(e.kind, e.kind).format(
                by=e.by or "someone", tag=e.tag, on=e.detail.get("on", "?"))
            detail = ""
            if e.kind == "screened" and "score" in e.detail:
                detail = f"AI guess {float(e.detail['score']):.0%}"
            elif e.kind == "voted":
                hidden = viewer and e.by != viewer and shown.get(e.by) is None
                if not hidden:
                    v = Vote(e.by, e.detail["label"], e.at, e.detail.get("until", ""),
                             e.detail.get("comment", ""), e.detail.get("reason", ""))
                    detail = _said(v, cfg)
                else:
                    detail = "hidden until you vote"
            elif e.kind == "noted":
                hidden = viewer and e.by != viewer and not may_read_notes(viewer, s, cfg)
                detail = "hidden until you vote" if hidden else e.detail.get("text", "")
            elif e.kind in ("closed", "revisit", "flagged", "reopened") and e.reason:
                detail = e.reason
            elif e.kind == "contacted" and e.detail.get("about"):
                about = e.detail["about"]
                detail = f"about: {cfg.label(about) if about in MEANINGS else about}"
            elif e.kind == "go_ahead" and e.detail.get("missing"):
                detail = "missing votes: " + ", ".join(e.detail["missing"])
            elif e.kind == "met":
                from stages import name as state_name, step_of
                detail = state_name(step_of(e), cfg) + (
                    f", planned for {e.detail['on']}" if str(e.detail.get("on", "")) > e.at[:10]
                    else "")
            if e.kind == "closed" and e.detail.get("exit"):
                from stages import name as state_name
                detail = state_name(str(e.detail["exit"]), cfg) + (
                    f" -- {e.reason}" if e.reason and e.reason != state_name(
                        str(e.detail["exit"]), cfg) else "")
            #: The parking put back after the "later" mail is bookkeeping, not
            #: an event anyone did; showing it twice reads like two decisions.
            if (e.kind == "revisit" and out and out[-1].kind in ("revisit", "contacted")
                    and any(m.kind == "revisit" and m.text == text for m in out)):
                continue
            #: A step dated back ("contacted yesterday") sits on that day.
            when = (str(e.detail["on"])[:10] if e.kind != "revisit" and e.detail.get("on")
                    and str(e.detail["on"])[:10] < e.at[:10] else e.at)
            out.append(Moment(when, app.posting_id, e.kind, text, detail))
    return sorted(out, key=lambda m: m.at, reverse=True)


# --------------------------------------------------------------------------
# Who is looking
# --------------------------------------------------------------------------

@dataclass
class Access:
    #: "pick": whoever opens the page chooses a name. One laptop, no one else.
    #: "header": a sign-in proxy in front (Google, Microsoft, Cloudflare Access)
    #: puts the signed-in email in a header, and only mapped emails get in.
    mode: str = "pick"
    header: str = "X-Forwarded-Email"
    emails: dict[str, str] = field(default_factory=dict)
    #: Header mode: the addresses the sign-in proxy connects from (IPs or
    #: networks). Every other peer is refused before the header is read -- the
    #: header is only a claim, and anyone reaching the port can type it.
    trusted_proxies: list[str] = field(default_factory=list)
    #: The desk's public address ("https://desk.company.example"), when the proxy
    #: rewrites Host. Forms posted from any other origin are refused.
    origin: str = ""


def load_access(path: str | Path | None = None, cfg: Config | None = None) -> Access:
    raw = tomllib.loads(Path(path or ROOT / "desk.toml").read_text(encoding="utf-8"))
    a = raw.get("access", {})
    acc = Access(mode=a.get("mode", "pick"), header=a.get("header", "X-Forwarded-Email"),
                 emails={k.lower(): v for k, v in a.get("emails", {}).items()},
                 trusted_proxies=[str(x) for x in a.get("trusted_proxies", [])],
                 origin=str(a.get("origin", "")))
    if acc.mode not in ("pick", "header"):
        raise DeskError("access.mode must be 'pick' or 'header'")
    if cfg is not None:
        for v in acc.emails.values():
            cfg.voter(v)
    return acc


def viewer_from(acc: Access, cfg: Config, headers: dict[str, str], asked: str = "") -> str:
    """Who is looking. Refuses rather than guessing.

    In header mode the name in the URL is ignored entirely: the proxy's
    signed-in email is the only thing that says who someone is, and a page
    that let `?as=` override it would let anyone vote as anyone.
    """
    if acc.mode == "header":
        got = next((v for k, v in headers.items() if k.lower() == acc.header.lower()), "")
        #: Google's IAP sends "accounts.google.com:name@company.example".
        email = got.strip().lower().rsplit(":", 1)[-1]
        if not email or email not in acc.emails:
            raise DeskError("not signed in as anyone who votes on this desk")
        return acc.emails[email]
    try:
        return cfg.voter(asked) if asked else cfg.voters[0]
    except DeskError:
        return cfg.voters[0]


# --------------------------------------------------------------------------
# Loading the desk from disk, safely with three people at once
# --------------------------------------------------------------------------

def _registry_path() -> Path:
    return ROOT / "runs" / "desk" / "people.json"


class locked:
    """One writer at a time, across threads and processes.

    Three people clicking within the same second is the normal case for this
    desk, not an edge case. Without this, two votes read the same file, each
    adds its own, and the second write erases the first -- a lost vote that
    nobody would ever notice.
    """

    _thread = __import__("threading").Lock()

    def __enter__(self) -> "locked":
        self._thread.acquire()
        f = ROOT / "runs" / "desk" / ".lock"
        f.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(f, "w")
        _os_lock(self._fh)
        return self

    def __exit__(self, *_: Any) -> None:
        _os_unlock(self._fh)
        self._fh.close()
        self._thread.release()


def _os_lock(fh: Any) -> None:
    """An exclusive lock between processes: flock on Unix, msvcrt on Windows.

    msvcrt's blocking mode gives up after ten seconds and raises, so it is
    polled instead: waiting behind a slow writer is correct, failing a vote
    because someone else was saving is not.
    """
    try:
        import fcntl
    except ImportError:
        import msvcrt
        import time
        fh.seek(0)
        while True:
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                time.sleep(0.02)
    fcntl.flock(fh, fcntl.LOCK_EX)


def _os_unlock(fh: Any) -> None:
    try:
        import fcntl
    except ImportError:
        import msvcrt
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        return
    fcntl.flock(fh, fcntl.LOCK_UN)


def _pipelines() -> dict[str, Pipeline]:
    from pipeline import load
    d = ROOT / "runs" / "pipeline"
    return {f.stem: load(f) for f in sorted(d.glob("*.json"))} if d.exists() else {}


def _titles() -> dict[str, str]:
    out = {}
    for f in (ROOT / "mandates" / "generated").glob("role_*.provenance.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        out[d.get("posting_id", f.stem)] = d.get("title", "")
    return out


def _facts(cid: str) -> Any:
    from intake.cv import load
    f = ROOT / "runs" / "facts" / f"facts_{cid}.json"
    return load(f) if f.exists() else None


def sync(pipes: dict[str, Pipeline], reg: Registry) -> list[str]:
    """Put every application in every pipeline onto a person. Idempotent."""
    notes = []
    for pid, pipe in pipes.items():
        for cid in pipe.candidates:
            if reg.person_of(cid) and any(a.key() == (pid, cid)
                                          for a in reg.person_of(cid).applications):
                continue
            facts = _facts(cid)
            name = (facts.name if facts else "") or cid.replace("_", " ").title()
            before = len(reg.people)
            p = reg.add(cid, pid, name, email_of(facts) if facts else "")
            notes.append(f"{p.name}: {pid}" + ("" if len(reg.people) > before
                                               else "  (already on the desk -- same person)"))
    return notes


def _history(reg: Registry, pipes: dict[str, Pipeline], app: Application,
             cfg: Config) -> list[tuple[Application, Outcome]]:
    p = reg.person_of(app.candidate_id)
    out = []
    for a in (p.applications if p else []):
        if a.key() != app.key() and a.posting_id in pipes:
            out.append((a, outcome(votes(pipes[a.posting_id].standing(a.candidate_id)), cfg)))
    return out


def _save(pipe: Pipeline) -> None:
    from pipeline import save
    save(pipe, ROOT / "runs" / "pipeline" / f"{pipe.posting_id}.json")


def vote_now(posting: str, cid: str, viewer: str, label: str, cfg: Config, **kw: Any
             ) -> list[Event]:
    """Read, vote and write under the lock, so a colleague's vote is never lost."""
    with locked():
        pipe = _pipelines().get(posting)
        if pipe is None:
            raise DeskError(f"nothing on the desk for {posting!r}")
        written = cast(pipe, cid, viewer, label, cfg, **kw)
        _save(pipe)
        return written


def draft_for(cid: str, posting: str, kind: str, cfg: Config) -> tuple[EmailMessage, str]:
    """The draft for an application's settled class, and a file name for it."""
    pipes, reg = _pipelines(), load_registry(_registry_path())
    pipe, person = pipes.get(posting), reg.person_of(cid)
    if pipe is None or person is None:
        raise DeskError(f"{cid} is not on the desk for {posting}")
    o = outcome(votes(pipe.standing(cid)), cfg)
    if kind == "received":
        # Older desks drafted an acknowledgment. The applicant tracking system
        # sends it; a second one from here would only repeat it.
        raise DeskError("no acknowledgment mail here: Ashby already sends \"we have your "
                        "application\". Drafts are for an outcome: contact, later or pass")
    meaning = cfg.meaning(kind) if kind else (o.meaning if o.status == "agreed" else "")
    if meaning in ("", "discuss"):
        raise DeskError(f"{person.name} is not settled ({o.status}); a draft follows a "
                        f"class, not the other way round")
    note = pass_note(cid, posting, cfg) if meaning == "pass" else ""
    m = draft(person, _titles().get(posting, posting), meaning, cfg, note=note)
    return m, f"{cid}_{posting}_{meaning}.eml"


def pass_note(cid: str, posting: str, cfg: Config) -> str:
    """The reading note that goes with a no: "" when off, or nothing was screened."""
    if not cfg.attach_note_on_pass:
        return ""
    from reply import note as reply_note
    from intake.posting import load as load_agenda
    from screen import load as load_screening
    from triage import _agenda_path, _screening_path
    sf, af = _screening_path(cid, posting), _agenda_path(posting)
    if not sf.exists():
        return ""
    return reply_note(load_screening(sf), _facts(cid), load_agenda(af) if af.exists() else None,
                      company=cfg.company, reviewer_pending=False)


def mark(pipe: Pipeline, cid: str, what: str, by: str, cfg: Config) -> list[Event]:
    """A person did something outside the desk: sent the mail, or got a reply.

    The desk records it; it never claims it. Sending the settled mail also
    does what the class means -- a pass closes, a later stays parked.
    """
    if cid not in pipe.candidates:
        raise DeskError(f"{cid} is not on the desk for {pipe.posting_id}")
    if what == "replied":
        return [pipe.add(Event(candidate_id=cid, kind="replied", by=by))]
    if what != "sent":
        raise DeskError(f"unknown mark {what!r}")
    o = outcome(votes(pipe.standing(cid)), cfg)
    written = [pipe.add(Event(candidate_id=cid, kind="contacted", by=by,
                              detail={"about": o.meaning or "received"}))]
    if o.status == "agreed" and o.meaning == "pass":
        reasons = "; ".join(v.reason for v in votes(pipe.standing(cid)).values() if v.reason)
        written.append(pipe.add(Event(
            candidate_id=cid, kind="closed", by=by,
            reason=f"{cfg.label('pass')} by {', '.join(o.voted)}"
                   + (f": {reasons}" if reasons else ""))))
    if o.status == "agreed" and o.meaning == "later":
        #: Writing to someone counts as acting on them, which ends a parking.
        #: The "later" mail is the one message that must not: it says "we
        #: will come back", so the date is put back after it.
        parked = next((e for e in reversed(pipe.standing(cid).events) if e.kind == "revisit"),
                      None)
        if parked is not None and o.until:
            written.append(pipe.add(Event(candidate_id=cid, kind="revisit", by=parked.by,
                                          reason=parked.reason, detail=dict(parked.detail))))
    return written


def note_now(posting: str, cid: str, by: str, text: str, cfg: Config) -> Event:
    with locked():
        pipe = _pipelines().get(posting)
        if pipe is None:
            raise DeskError(f"nothing on the desk for {posting!r}")
        ev = write_note(pipe, cid, by, text, cfg)
        _save(pipe)
        return ev


def mark_now(posting: str, cid: str, what: str, by: str, cfg: Config) -> list[Event]:
    with locked():
        pipe = _pipelines().get(posting)
        if pipe is None:
            raise DeskError(f"nothing on the desk for {posting!r}")
        written = mark(pipe, cid, what, by, cfg)
        _save(pipe)
        return written


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", normalise_name(name)).strip("_") or "candidate"


#: What can be attached. Documents people read, not anything a browser would
#: run: an uploaded .html served back from the desk's own address could act as
#: whoever opens it.
DOC_TYPES = {".pdf": "application/pdf", ".txt": "text/plain; charset=utf-8",
             ".md": "text/plain; charset=utf-8",
             ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
             ".doc": "application/msword", ".rtf": "application/rtf"}
DOC_KINDS = ("cv", "letter", "other")
MAX_DOC_BYTES = 10 * 1024 * 1024


def _files_dir() -> Path:
    return ROOT / "runs" / "desk" / "files"


def check_document(kind: str, original: str, data: bytes) -> str:
    """Refuse a file the desk cannot vouch for; return its extension."""
    if kind not in DOC_KINDS:
        raise DeskError(f"a document is one of {', '.join(DOC_KINDS)}")
    ext = Path(original).suffix.lower()
    if ext not in DOC_TYPES:
        raise DeskError(f"{original!r}: only {', '.join(sorted(DOC_TYPES))} files")
    if not data:
        raise DeskError(f"{original!r} is empty")
    if len(data) > MAX_DOC_BYTES:
        raise DeskError(f"{original!r} is over {MAX_DOC_BYTES // (1024 * 1024)} MB")
    if ext == ".pdf" and not data.startswith(b"%PDF"):
        raise DeskError(f"{original!r} is named .pdf and is not a PDF")
    return ext


def attach(reg: Registry, person_id: str, posting_id: str, kind: str, original: str,
           data: bytes, *, by: str = "") -> Document:
    """Store one file against one person's application."""
    p = reg.people.get(person_id)
    if p is None:
        raise DeskError(f"nobody called {person_id!r} on the desk")
    ext = check_document(kind, original, data)
    n = 1 + sum(1 for d in p.documents if d.posting_id == posting_id and d.kind == kind)
    stored = f"{slug(person_id)}/{slug(posting_id)}_{kind}_{n}{ext}"
    target = _files_dir() / stored
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    doc = Document(posting_id, kind, stored, Path(original).name, by,
                   datetime.now(timezone.utc).isoformat())
    p.documents.append(doc)
    return doc


def document_file(reg: Registry, person_id: str, stored: str) -> tuple[Path, Document]:
    """The file behind a document -- only one the registry says this person has."""
    p = reg.people.get(person_id)
    doc = next((d for d in (p.documents if p else []) if d.stored == stored), None)
    if doc is None:
        raise DeskError("no such document")
    f = (_files_dir() / doc.stored).resolve()
    if _files_dir().resolve() not in f.parents or not f.exists():
        raise DeskError("no such document")
    return f, doc


def _link(link: str) -> str:
    link = link.strip()
    if link and not re.match(r"^https://[^\s/]+\.[^\s]+$", link):
        raise DeskError(f"{link!r} is not an https:// link")
    return link


def _links(said: str) -> list[str]:
    """One or several https:// links, separated by spaces, commas or lines."""
    out: list[str] = []
    for part in re.split(r"[\s,]+", said or ""):
        if part and (l := _link(part)) not in out:
            out.append(l)
    return out


def _place_links(person: Person, links: list[str]) -> None:
    """The first LinkedIn is the profile; everything else is something to open."""
    for l in links:
        if not person.link and "linkedin.com" in l:
            person.link = l
        elif l != person.link and l not in person.links:
            person.links.append(l)


def add_application(pipe: Pipeline, reg: Registry, name: str, *, email: str = "",
                    link: str = "", now: datetime | None = None) -> Person:
    """Put an application on the desk by hand. No model, no cost.

    The desk does not need the AI at all: a card without a fit percentage is
    still a card, and three people can class it. The percentage can be added
    later, by whoever has a way to run the screener -- see DEPLOY.md.
    """
    name = re.sub(r"\s+", " ", name).strip()
    email = email.strip()
    if not name:
        raise DeskError("an application needs a name")
    if email and not EMAIL.fullmatch(email):
        raise DeskError(f"{email!r} is not an email address")
    known = (next((p for p in reg.people.values()
                   if email and p.email.lower() == email.lower()), None))
    if known is not None and any(a.posting_id == pipe.posting_id for a in known.applications):
        raise DeskError(f"{known.name} has already applied for this role")
    cid = known.person_id if known is not None else slug(name)
    base, n = cid, 2
    while known is None and (cid in pipe.candidates or cid in reg.people):
        cid, n = f"{base}_{n}", n + 1
    links = _links(link)
    pipe.add(Event(candidate_id=cid, kind="received",
                   at=(now or datetime.now(timezone.utc)).isoformat(),
                   detail={"added_by_hand": True}))
    person = reg.add(cid, pipe.posting_id, name, email)
    _place_links(person, links)
    return person


def add_now(posting: str, name: str, email: str = "", *, link: str = "",
            files: list[tuple[str, str, bytes]] | None = None, by: str = "") -> Person:
    """Add an application with its files, all or nothing."""
    if posting not in _titles() and posting not in _pipelines():
        raise DeskError(f"no posting called {posting!r}")
    with locked():
        from pipeline import Pipeline as P
        pipe = _pipelines().get(posting) or P(posting_id=posting)
        reg = load_registry(_registry_path())
        for kind, original, data in files or []:
            # Checked before anything is written, so a bad file does not leave
            # a card behind with nothing attached.
            check_document(kind, original, data)
        person = add_application(pipe, reg, name, email=email, link=link)
        for kind, original, data in files or []:
            attach(reg, person.person_id, posting, kind, original, data, by=by)
        _save(pipe)
        save_registry(reg, _registry_path())
        return person


def attach_now(person_id: str, posting: str, kind: str, original: str, data: bytes,
               by: str = "") -> Document:
    with locked():
        reg = load_registry(_registry_path())
        doc = attach(reg, person_id, posting, kind, original, data, by=by)
        save_registry(reg, _registry_path())
        return doc


#: Column names an export is likely to use, lower-cased. Ashby, a
#: spreadsheet, a form tool -- all say roughly the same few things.
CSV_COLUMNS = {
    "name": ("name", "full name", "candidate", "candidate name"),
    "first": ("first name", "firstname", "given name"),
    "last": ("last name", "lastname", "surname", "family name"),
    "email": ("email", "e-mail", "email address", "candidate email"),
    "link": ("linkedin", "linkedin url", "linkedin profile", "profile", "link", "website"),
    "cv": ("cv", "resume", "cv path", "resume path", "cv file", "resume file"),
    "posting": ("posting", "job", "role", "job title", "position"),
}


def import_csv(path: str | Path, posting: str = "", *, by: str = "") -> list[str]:
    """Bring an export in: one line per application, a report per line.

    A line that cannot be read is reported and skipped; a person already on
    the desk for that role is reported and left alone. Nothing is guessed:
    a role is matched on its exact title or id, or the line is refused.
    """
    import csv
    f = Path(path)
    text = f.read_text(encoding="utf-8-sig")
    rows = list(csv.DictReader(text.splitlines()))
    if not rows:
        raise DeskError(f"{f.name}: no rows")
    cols = {k.strip().lower(): k for k in rows[0].keys() if k}

    def col(key: str) -> str | None:
        return next((cols[c] for c in CSV_COLUMNS[key] if c in cols), None)

    c_name, c_first, c_last = col("name"), col("first"), col("last")
    if not c_name and not (c_first and c_last):
        raise DeskError(f"{f.name}: no name column (tried {', '.join(CSV_COLUMNS['name'])})")
    titles = _titles()
    by_title = {t.lower(): pid for pid, t in titles.items()}
    report = []
    for i, r in enumerate(rows, 2):
        name = (r.get(c_name) or "") if c_name else f"{r.get(c_first, '')} {r.get(c_last, '')}"
        role = posting or (r.get(col("posting") or "", "") or "").strip()
        pid = role if role in titles else by_title.get(role.lower(), "")
        if not pid:
            report.append(f"line {i}: {name.strip() or '?'} -- unknown role {role!r}, skipped")
            continue
        files = []
        cv = (r.get(col("cv") or "", "") or "").strip()
        if cv:
            #: A CV is read from the export's own folder and nowhere else: a
            #: cell is text somebody else wrote, and "../../.ssh/id_rsa.txt"
            #: would otherwise be attached to a card for anyone to download.
            cvf = (f.parent / cv).resolve()
            if f.parent.resolve() not in cvf.parents:
                report.append(f"line {i}: {name.strip()} -- CV {cv!r} is outside the export's "
                              f"folder, skipped")
                continue
            if not cvf.exists():
                report.append(f"line {i}: {name.strip()} -- CV {cv!r} not found, skipped")
                continue
            files.append(("cv", cvf.name, cvf.read_bytes()))
        try:
            p = add_now(pid, name, (r.get(col("email") or "", "") or "").strip(),
                        link=(r.get(col("link") or "", "") or "").strip(), files=files, by=by)
            report.append(f"line {i}: {p.name} -- added to {titles.get(pid, pid)}")
        except DeskError as e:
            report.append(f"line {i}: {name.strip() or '?'} -- {e}")
    return report


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

def _cmd_sync() -> int:
    with locked():
        reg = load_registry(_registry_path())
        notes = sync(_pipelines(), reg)
        save_registry(reg, _registry_path())
    for n in notes:
        print(f"  {n}")
    print(f"{len(reg.people)} people on the desk, "
          f"{sum(len(p.applications) for p in reg.people.values())} applications")
    for x in reg.possible:
        print(f"  possibly the same person: {x['a']} and {x['b']} ({x['why']}) -- "
              f"`desk.py merge --keep {x['a']} --merge {x['b']} --by <you>` if so")
    return 0


def _cmd_add(name: str, posting: str, email: str, link: str = "", cv: str = "",
             letter: str = "") -> int:
    files = []
    for kind, path in (("cv", cv), ("letter", letter)):
        if path:
            f = Path(path)
            if not f.exists():
                print(f"{path}: no such file", file=sys.stderr)
                return 2
            files.append((kind, f.name, f.read_bytes()))
    try:
        p = add_now(posting, name, email, link=link, files=files)
    except DeskError as e:
        print(e, file=sys.stderr)
        return 2
    extra = (f" -- already on the desk for {len(p.applications) - 1} other role(s)"
             if len(p.applications) > 1 else "")
    print(f"{p.name}: on the desk for {posting}{extra}")
    return 0


def _cmd_card(viewer: str, cid: str, posting: str, cfg: Config) -> int:
    pipes, reg = _pipelines(), load_registry(_registry_path())
    pipe = pipes.get(posting)
    person = reg.person_of(cid)
    if pipe is None or cid not in pipe.candidates or person is None:
        print(f"{cid} is not on the desk for {posting} -- `desk.py sync` first",
              file=sys.stderr)
        return 2
    app = Application(posting, cid)
    print(card(person, app, pipe.standing(cid), cfg, viewer, _titles(),
               _history(reg, pipes, app, cfg)))
    print(f"\nnext: {next_step(pipe.standing(cid), cfg)}")
    return 0


def _cmd_vote(viewer: str, cid: str, posting: str, label: str, until: str, comment: str,
              reason: str, cfg: Config) -> int:
    try:
        written = vote_now(posting, cid, viewer, label, cfg, until=until, comment=comment,
                           reason=reason)
    except (DeskError, PipelineError) as e:
        print(e, file=sys.stderr)
        return 2
    _cmd_card(viewer, cid, posting, cfg)
    if any(e.kind == "revisit" for e in written):
        print(f"On the board to come back on {written[-1].detail['on']}.")
    return 0


def _cmd_recap(viewer: str, cfg: Config, days: int | None) -> int:
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days) if days else None
    print(recap(_pipelines(), load_registry(_registry_path()), cfg, now=now, since=since,
                viewer=viewer, titles=_titles()).render(cfg))
    return 0


def _cmd_draft(cid: str, posting: str, kind: str, cfg: Config) -> int:
    try:
        m, name = draft_for(cid, posting, kind, cfg)
    except DeskError as e:
        print(e, file=sys.stderr)
        return 2
    target = ROOT / "runs" / "desk" / "drafts" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(bytes(m))
    print(m.as_string())
    print(f"\nwritten: {target.relative_to(ROOT)} -- opens as a draft; a person sends it")
    print(f"once sent: desk.py sent --candidate {cid} --posting {posting} --by <you>")
    return 0


def _cmd_mark(cid: str, posting: str, what: str, by: str, cfg: Config) -> int:
    try:
        mark_now(posting, cid, what, cfg.voter(by) if by in cfg.voters else by, cfg)
    except (DeskError, PipelineError) as e:
        print(e, file=sys.stderr)
        return 2
    print(f"recorded: {'they replied' if what == 'replied' else f'{by} wrote to {cid}'}")
    return 0


def _cmd_timeline(viewer: str, person_id: str, cfg: Config) -> int:
    reg = load_registry(_registry_path())
    p = reg.people.get(person_id) or reg.person_of(person_id)
    if p is None:
        print(f"nobody called {person_id!r} on the desk", file=sys.stderr)
        return 2
    titles = _titles()
    print(f"{p.name}" + (f" <{p.email}>" if p.email else ""))
    for m in timeline(p, _pipelines(), cfg, cfg.voter(viewer) if viewer else "", titles):
        extra = f" -- {m.detail}" if m.detail else ""
        print(f"  {m.at[:10]}  {titles.get(m.posting_id, m.posting_id):<22} {m.text}{extra}")
    return 0


def _cmd_merge(keep: str, merge: str, by: str) -> int:
    with locked():
        reg = load_registry(_registry_path())
        try:
            p = reg.merge(keep, merge, by=by)
        except DeskError as e:
            print(e, file=sys.stderr)
            return 2
        save_registry(reg, _registry_path())
    print(f"{p.name}: one person, {len(p.applications)} applications (confirmed by {by})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="desk", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=None, help="another desk.toml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sync", help="every screened application onto a person (free)")

    ad = sub.add_parser("add", help="an application by hand, no AI (free)")
    ad.add_argument("--name", required=True)
    ad.add_argument("--posting", required=True)
    ad.add_argument("--email", default="")
    ad.add_argument("--link", default="",
                    help="LinkedIn, a repository, a demo: https:// links, space-separated")
    ad.add_argument("--cv", default="", help="path to the CV (pdf, docx, txt, md)")
    ad.add_argument("--letter", default="", help="path to the cover letter")

    im = sub.add_parser("import", help="applications from a CSV export, no AI (free)")
    im.add_argument("--csv", required=True)
    im.add_argument("--posting", default="", help="for every line; else a role column")

    c = sub.add_parser("card", help="one card, as one voter sees it")
    c.add_argument("--as", dest="viewer", required=True)
    c.add_argument("--candidate", required=True)
    c.add_argument("--posting", required=True)

    v = sub.add_parser("vote", help="class an application")
    v.add_argument("--as", dest="viewer", required=True)
    v.add_argument("--candidate", required=True)
    v.add_argument("--posting", required=True)
    v.add_argument("--label", required=True, help="contact | later | discuss | pass, or "
                                                  "the names set in desk.toml")
    v.add_argument("--until", default="", help="with later: YYYY-MM-DD")
    v.add_argument("--comment", default="")
    v.add_argument("--reason", default="", help="with pass")

    r = sub.add_parser("recap", help="what needs anyone this week")
    r.add_argument("--as", dest="viewer", default="")
    r.add_argument("--days", type=int, default=None,
                   help="look back this many days instead of to the last recap day")

    t = sub.add_parser("timeline", help="everything that happened to one person")
    t.add_argument("--person", required=True)
    t.add_argument("--as", dest="viewer", default="")

    d = sub.add_parser("draft", help="the mail for a settled application, as a draft")
    d.add_argument("--candidate", required=True)
    d.add_argument("--posting", required=True)
    d.add_argument("--kind", default="", help="override: contact | later | pass")

    s = sub.add_parser("sent", help="a person sent the mail: record it")
    s.add_argument("--candidate", required=True)
    s.add_argument("--posting", required=True)
    s.add_argument("--by", required=True)

    rp = sub.add_parser("replied", help="the candidate answered: record it")
    rp.add_argument("--candidate", required=True)
    rp.add_argument("--posting", required=True)

    m = sub.add_parser("merge", help="confirm two records are one person")
    m.add_argument("--keep", required=True)
    m.add_argument("--merge", required=True)
    m.add_argument("--by", required=True)

    sv = sub.add_parser("serve", help="the desk in a browser")
    sv.add_argument("--port", type=int, default=8765)
    sv.add_argument("--host", default="127.0.0.1",
                    help="0.0.0.0 only inside a container, behind a sign-in proxy")

    a = ap.parse_args()
    try:
        cfg = load_config(a.config)
        if a.cmd == "sync":
            return _cmd_sync()
        if a.cmd == "add":
            return _cmd_add(a.name, a.posting, a.email, a.link, a.cv, a.letter)
        if a.cmd == "import":
            for line in import_csv(a.csv, a.posting):
                print(f"  {line}")
            return 0
        if a.cmd == "card":
            return _cmd_card(a.viewer, a.candidate, a.posting, cfg)
        if a.cmd == "vote":
            return _cmd_vote(a.viewer, a.candidate, a.posting, a.label, a.until, a.comment,
                             a.reason, cfg)
        if a.cmd == "recap":
            return _cmd_recap(a.viewer, cfg, a.days)
        if a.cmd == "timeline":
            return _cmd_timeline(a.viewer, a.person, cfg)
        if a.cmd == "draft":
            return _cmd_draft(a.candidate, a.posting, a.kind, cfg)
        if a.cmd == "sent":
            return _cmd_mark(a.candidate, a.posting, "sent", a.by, cfg)
        if a.cmd == "replied":
            return _cmd_mark(a.candidate, a.posting, "replied", "", cfg)
        if a.cmd == "merge":
            return _cmd_merge(a.keep, a.merge, a.by)
        from console.desk_web import serve
        serve(a.host, a.port, cfg, load_access(a.config, cfg),
              reload=lambda: load_config(a.config))
        return 0
    except DeskError as e:
        print(e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
