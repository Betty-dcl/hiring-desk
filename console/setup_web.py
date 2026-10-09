"""The Set up page: every rule the desk follows, as a question the partners answer.

The desk ships with defaults somebody outside the company picked. Settings
changes them one field at a time, which is the right tool once a team knows
what it wants and the wrong one for finding out. This page is for finding
out: one question per rule, what it means, why the default is what it is,
the options the code actually supports (and the ones it does not, marked
"on request"), and a note box. Each partner answers alone; the answers are
then shown side by side, and what is discussed is where they differ.

Three rules, the same as the rest of the desk:

* **Answers are a record, not a switch.** Saving writes who answered what
  and when, under `runs/desk/setup.json`. Nothing on the desk changes,
  except a rule that Settings can already change, *and* whose box "apply now"
  the person ticked -- then it goes through `desk.save_settings`, the same
  door as the House rules page, with the same checks and the same history.
* **Answer first, then see.** The others' answers follow the desk's blind
  rule (`[votes] blind`): a partner who reads "Ben wants majority" before
  thinking about it has not answered, they have agreed.
* **Nothing claims what it is not.** The "Good to know" part names the law
  that bears on a hiring tool and what the desk does about each point. It is
  not legal advice, and it says so.
* **Untouched is not answered.** No option is ticked for a partner who has
  not ticked it: the default is shown and marked "default", and a question
  left alone is recorded as not answered -- otherwise saving without reading
  would "answer" the default everywhere. "Accept all defaults in this
  section" is there for the partner who has read it and agrees.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import desk
from desk import DeskError

e = html.escape

CSS = """
details.more{margin:10px 0 4px}details.more>summary{cursor:pointer;color:var(--accent);font-size:14px;padding:6px 0}
.su{border-top:1px solid var(--line);padding:4px 0 10px;margin-bottom:10px}
.su>h2{color:var(--ink);font-size:16px;font-weight:600;margin:14px 0 2px;border:0;padding:0;
display:flex;gap:12px;align-items:baseline;flex-wrap:wrap}
.su .q{border-top:1px solid var(--line2);padding:14px 0}
.su .q:first-of-type{border-top:0}
.su h3{font-size:15px;margin:0 0 4px}
.su p{margin:3px 0;font-size:13.5px;color:var(--mute);max-width:700px}
.su p b{color:var(--ink);font-weight:600}
.su .where{font-size:12px;color:var(--faint)}
.su .opts{display:grid;gap:4px;margin:8px 0 6px}
.su label.opt{display:flex;gap:8px;align-items:baseline;font-size:14px;cursor:pointer}
.su label.opt input{margin:0;flex:none}
.su .tag{margin-left:4px;font-size:12px;color:var(--faint)}
.su .tag:before{content:"("}.su .tag:after{content:")"}
.su .tag.req{color:var(--amber)}
.su .val{display:flex;gap:8px;align-items:center;margin:8px 0 6px;font-size:14px;flex-wrap:wrap}
.su .val input[type=number]{width:96px}
.su .val input[type=text]{min-width:260px;flex:1}
.su .foot{display:flex;gap:16px;flex-wrap:wrap;align-items:center;font-size:13px;
color:var(--mute)}
.su .foot label{display:flex;gap:6px;align-items:center;cursor:pointer}
.su .foot input[type=text]{flex:1;min-width:200px;font-size:13px}
.su .fixed{font-size:12.5px;color:var(--amber)}
.su .read{font-size:13px;color:var(--mute);display:flex;gap:6px;align-items:center;
margin-top:6px}
.sutools{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 18px}
.sutools a{font-size:14px;border:1px solid var(--line);background:var(--panel);
border-radius:var(--radius);padding:4px 12px;text-decoration:none;color:var(--ink)}
.sutools a:hover,.sutools a.on{border-color:var(--ink)}
.sutools a.on{font-weight:600}
.ans{display:grid;grid-template-columns:96px 1fr;gap:4px 12px;margin-top:8px;font-size:14px}
.ans .who{color:var(--mute)}
.ans .hid,.ans .none{color:var(--faint)}
.ans small{color:var(--faint)}
.state{font-size:12px;font-weight:400;margin-left:8px;color:var(--mute)}
.state.differ{color:var(--amber)}
.su button.acc{font-size:12.5px;font-weight:400;color:var(--mute);padding:1px 9px}
.su button.acc:hover{color:var(--ink)}
"""

#: The order the page reads in: who and how first, the plumbing last.
THEMES = ("Who votes and how", "The four choices and their names", "When it is settled",
          "The steps after the vote", "Mails to candidates", "The Tuesday recap", "The promise and tracking",
          "Ashby", "Access and deployment", "Data")

#: The questions a single person deciding is never asked: they are about
#: voters, their recap, their reminders, a signature, or the trial-day ideas
#: page, which the desk no longer shows. Their values stay as they are.
SOLO_SKIP = ("voters", "blind", "change_vote", "comment_max", "note_max", "pass_reason_required",
             "pass_reasons", "agreement", "later_date", "go_ahead_alone", "vote_moves",
             "who_advances", "sender", "recap_weekday", "recap_frequency",
             "awaiting_vote_days", "no_reply_days", "link_ttl_hours", "answer_within_days",
             "agreement_votes")

#: A note is a line for the others, like a vote's comment.
NOTE_MAX = desk.COMMENT_MAX
TEXT_MAX = 200


@dataclass(frozen=True)
class Option:
    value: str
    label: str
    #: False: the code does not do this today. It can still be chosen -- that
    #: is the point of asking -- and it is marked "on request".
    built: bool = True


@dataclass(frozen=True)
class Question:
    id: str
    theme: str
    title: str
    means: str
    why: str
    #: "choice", "number" or "text".
    kind: str
    options: tuple[Option, ...] = ()
    #: What desk.toml ships.
    default: str = ""
    #: What the desk does today (Settings may have changed it).
    current: str = ""
    where: str = ""
    unit: str = ""
    lo: float = 0
    hi: float = 0
    #: The Settings key an answer may be applied to ("labels.contact" for a
    #: label), or "" when this rule is not changed from the browser.
    apply: str = ""
    #: True when the value is fixed in the code: another one is on request.
    fixed: bool = False

    def label_of(self, value: str) -> str:
        for o in self.options:
            if o.value == value:
                return o.label + ("" if o.built else " (on request)")
        if self.kind == "number":
            return f"{value} {self.unit}".strip()
        return value


@dataclass(frozen=True)
class Note:
    """A "Good to know" item: nothing to choose, something to have read."""
    id: str
    title: str
    text: str


GOOD_TO_KNOW = (
    Note("ai_act", "Hiring is a high-risk use under the EU AI Act",
         "Annex III of the AI Act lists AI systems used to recruit or select people -- to "
         "filter applications and to evaluate candidates -- as high-risk. That status comes "
         "with obligations on whoever provides and whoever uses such a system: risk "
         "management, records, human oversight, information for the people concerned. The "
         "desk has not been assessed against them and makes no claim to meet them. What it "
         "does is name each obligation and the design choice it led to (docs/DETAILS.md)."),
    Note("art50", "Article 50: say when a machine wrote it",
         "The AI Act's transparency article asks that people know when they deal with an AI "
         "system or read text it produced. The note the desk drafts for a candidate (what "
         "was read, what was missing) says in its first lines that it was written by the "
         "screening system, not by a person."),
    Note("gdpr22", "GDPR Article 22: no decision by a machine alone",
         "A person has the right not to be subject to a decision based solely on automated "
         "processing that significantly affects them. That is why the desk has no "
         "\"reject\" state: nothing is decided until people vote, and the fit percentage "
         "never triggers anything -- no mail, no class, no note."),
    Note("pool", "Keeping someone \"for later\" needs their agreement and an end date",
         "Keeping an application on file past the role it was sent for is a new use of "
         "personal data. It needs a lawful basis -- in practice, usually the person's "
         "consent -- and a retention period. The shipped \"Later\" mail tells the "
         "person and lets them say no; whether that is enough in your case is for your "
         "counsel to say."),
    Note("percent", "The AI percentage is a reading, not a verdict",
         "The fit percentage is a machine's reading of the CV and letter against the "
         "posting. It can be wrong, it drifts between runs (the list says \"any order\" "
         "where it cannot tell people apart), and it reads only paper. The partners' vote "
         "decides."),
    Note("not_advice", "This is not legal advice",
         "This page points at the rules most likely to matter for a hiring tool and says "
         "what the desk does about each. It does not say what they require of your company. "
         "That is a question for your counsel."),
)


def _yes(b: bool) -> str:
    return "yes" if b else "no"


def _n(x: float) -> str:
    return f"{float(x):g}"


YES_NO = (Option("yes", "Yes"), Option("no", "No"))


def questions(cfg: desk.Config, access_mode: str = "pick") -> list[Question]:
    """Every rule, with what ships and what runs today.

    The options are those the code supports; anything else offered is marked
    as not built. Read from the same loaders the desk uses, so a value here is
    never a copy that could drift from the one that runs.
    """
    import ashby
    import metrics

    base = desk.load_config(overrides=False)
    tr = metrics.load_tracking()
    ab = ashby.load_settings()
    q: list[Question] = []
    add = q.append

    # -- Who votes and how ------------------------------------------------
    t = THEMES[0]
    add(Question(
        "voters", t, "Who votes",
        "The people whose votes settle an application. Each one sees the list, votes, and is "
        "named in the recap when an application is waiting on them.",
        "Assumed to be the three founders, who each want a say. It may well be someone else "
        "who looks after applications, or a different set of people. Once agreed, change it "
        "on House rules.",
        "text", default=", ".join(base.voters), current=", ".join(cfg.voters),
        where="desk.toml [team] voters · House rules"))
    add(Question(
        "blind", t, "When you see the others' votes",
        "Whether a voter sees how the others classed an application before classing it "
        "themselves. The notes on an application follow the same rule.",
        "The first opinion in a room anchors the rest: three people who each saw the first "
        "vote are one opinion counted three times. Showing the others right after your own "
        "vote keeps your vote yours and still lets you talk.",
        "choice", (Option("until_you_vote", "After you have voted yourself"),
                   Option("until_all_voted", "Only once everyone has voted"),
                   Option("off", "Straight away")),
        default=base.blind, current=cfg.blind, where="desk.toml [votes] blind · House rules",
        apply="blind"))
    add(Question(
        "change_vote", t, "Changing your vote",
        "Whether a voter can change their class after casting it.",
        "People change their minds after reading the others' notes, and that is the point of "
        "the notes. The latest vote counts; every earlier one stays in the log, and the "
        "agreement measure on Overview uses the first, blind one.",
        "choice", (Option("latest", "Allowed: the latest vote counts, the history is kept"),
                   Option("final", "Not allowed: the first vote is final", built=False)),
        default="latest", current="latest", where="desk.py (votes)"))
    add(Question(
        "comment_max", t, "Length of a vote's comment",
        "Each vote can carry one optional line, shown to the others under the blind rule.",
        "A comment is a line, not a memo. Anything longer is a conversation, and the notes "
        "are there for that.",
        "number", default=_n(desk.COMMENT_MAX), current=_n(desk.COMMENT_MAX), unit="characters",
        lo=40, hi=2000, fixed=True, where="desk.py COMMENT_MAX"))
    add(Question(
        "note_max", t, "Length of a note in the discussion thread",
        "Each application has a short thread, to settle a disagreement in writing rather "
        "than in a meeting. Notes follow the blind rule too.",
        "Long enough for an argument, short enough to be read between two calls.",
        "number", default=_n(desk.NOTE_MAX), current=_n(desk.NOTE_MAX), unit="characters",
        lo=100, hi=5000, fixed=True, where="desk.py NOTE_MAX"))

    # -- The four choices -------------------------------------------------
    t = THEMES[1]
    what = {"contact": "Yes: get in touch", "discuss": "Not sure: the team decides together",
            "later": "Later: talk again on a date", "pass": "No"}
    for m in desk.MEANINGS:
        add(Question(
            f"label_{m}", t, f"The name of “{what[m]}”",
            "What this button is called everywhere on the desk, in the recap and in the "
            "mails' subjects. The meaning is fixed, because the desk acts on it; the word is "
            "yours.",
            "Plain words a partner reads in a second, no emoji. Change it if your team says "
            "it differently.",
            "text", default=base.labels[m], current=cfg.labels[m],
            where=f"desk.toml [votes] {m} · House rules", apply=f"labels.{m}"))
    add(Question(
        "later_needs_date", t, f"“{cfg.label('later')}” needs a date",
        "A voter who keeps someone on file says when to look again.",
        "Later without a date is how people are forgotten. With one, the card wakes up on the "
        "board by itself on that day.",
        "choice", (Option("required", "Required"),
                   Option("optional", "Optional", built=False)),
        default="required", current="required", where="desk.py cast()"))
    add(Question(
        "pass_reason_required", t, f"A reason for “{cfg.label('pass')}”",
        "Whether a voter must give a reason (one click among a few, or their own words) "
        "when they say no.",
        "Optional by default, so a clear no stays one click. Required makes the reasons "
        "countable later, at the price of a second click.",
        "choice", (Option("no", "Optional"), Option("yes", "Required")),
        default=_yes(base.pass_reason_required), current=_yes(cfg.pass_reason_required),
        where="desk.toml [votes] pass_reason_required"))
    add(Question(
        "pass_reasons", t, f"The one-click reasons for “{cfg.label('pass')}”",
        "The short reasons offered under the button, separated here by semicolons. Free text "
        "is always accepted too.",
        "Reasons that say something about the fit, never about the person.",
        "text", default="; ".join(base.pass_reasons), current="; ".join(cfg.pass_reasons),
        where="desk.toml [votes] pass_reasons · House rules"))

    # -- When it is settled -----------------------------------------------
    t = THEMES[2]
    add(Question(
        "agreement", t, "What settles an application",
        "When the votes count as a decision. A settled class prepares the next step (a mail "
        "draft, a wake-up date); anything not settled waits or goes to discussion.",
        f"A disagreement between three people is information, not noise: it goes to "
        f"discussion rather than being outvoted. Majority is faster; one "
        f"“{cfg.label('contact')}” is enough when talking to someone is cheap and missing "
        f"them is not.",
        "choice", (Option("unanimous", "Everyone agrees; any disagreement goes to Discuss"),
                   Option("majority", "A majority agrees (2 of 3)"),
                   Option("any_contact", f"One “{cfg.label('contact')}” is enough to invite; "
                                         f"the rest as unanimous")),
        default=base.agreement, current=cfg.agreement,
        where="desk.toml [votes] agreement · House rules", apply="agreement"))
    add(Question(
        "later_date", t, f"“{cfg.label('later')}” with different dates",
        "Everyone agrees to keep someone on file, but each gave a different date to look "
        "again.",
        "The earliest date, so nobody's \"January\" is quietly pushed to somebody else's "
        "June. Waking up early costs one look; waking up late can cost the person.",
        "choice", (Option("earliest", "The earliest date"),
                   Option("latest", "The latest date")),
        default=base.later_date, current=cfg.later_date,
        where="desk.toml [votes] later_date"))
    add(Question(
        "go_ahead_alone", t, "Can one partner go ahead alone?",
        "When a partner is away, or the candidate will not wait, a voter can move an "
        "application to “Ready to contact” without waiting for the others. The desk records "
        "who went ahead and whose votes were missing; the missing votes can still be cast.",
        "All three vote by default, and nobody is blocked by an absence: a partner who voted "
        "yes may go ahead. Going ahead is always your own yes, so the button never hints at "
        "how a colleague voted.",
        "choice", (Option("after_one_yes", "Yes, after their own yes"),
                   Option("all_voted", "Only once everyone has voted"),
                   Option("owner", "Only a named process owner (the people who move steps, "
                                   "on House rules)")),
        default=base.go_ahead_alone, current=cfg.go_ahead_alone,
        where="desk.toml [votes] go_ahead_alone · House rules", apply="go_ahead_alone"))

    # -- The steps after the vote -------------------------------------------
    import stages
    t = THEMES[3]
    path = "; ".join(stages.names(base)[s] for s in stages.STATES if s != "closed")
    add(Question(
        "vote_moves", t, "What the votes move",
        "Each application has one state, apart from the votes: New, Voting, Team to decide, "
        "Ready to contact, Contacted, each interview, Trial day -- or Talk later "
        "until a date, Answered no, Withdrew. Under each, a line says the next step.",
        "The votes only ever reach a step to do (Ready to contact, Ready to answer no, Talk "
        "later). That three people said yes does not mean anyone wrote: a person writes, "
        "then records \"Contacted\" with the date. The desk sends nothing.",
        "choice", (Option("to_do", "Only a step to do; each step after it is recorded by a "
                                   "person"),
                   Option("auto", "Agreeing on yes records Contacted and sends the mail",
                          built=False)),
        default="to_do", current="to_do", where="stages.py"))
    add(Question(
        "state_names", t, "The names of the states",
        "What each state is called on every row, on the card, in the recap and on Overview, "
        "in this order, separated by semicolons.",
        "Plain words a partner reads in a second. The meanings are fixed, because the desk "
        "acts on them; the words are yours, one by one on House rules.",
        "text", default=path,
        current="; ".join(stages.names(cfg)[s] for s in stages.STATES if s != "closed"),
        where="desk.toml [states.names] · House rules"))
    add(Question(
        "who_advances", t, "Who can move an application to its next step",
        "Who may record \"Contacted\", an interview, a trial day, or a "
        "way out. Each step is recorded under that person's name, with the date.",
        "Any voter: whoever wrote to the candidate records it, without asking anyone. A team "
        "that wants one person to own the follow-up names them on House rules.",
        "choice", (Option("any", "Any voter"),
                   Option("named", "Only some voters, named on House rules"),
                   Option("contacted_by", "Only the person who contacted them", built=False)),
        default="named" if base.advancers else "any",
        current="named" if cfg.advancers else "any",
        where="desk.toml [states] advancers · House rules"))
    add(Question(
        "interviews", t, "How many interviews before the trial day",
        "The interviews a candidate goes through, one after the other, before the trial "
        "day, which is the last step. Each has its own name on House rules.",
        "Four by default. From any interview the trial day can be recorded directly, for a "
        "role that needs fewer, so nobody records an interview that never took place.",
        "number", default=_n(base.interviews), current=_n(cfg.interviews),
        unit="interviews", lo=1, hi=len(stages.ROUNDS),
        where="desk.toml [states] interviews · House rules", apply="interviews"))
    add(Question(
        "plan_ahead_days", t, "An interview or a trial day may be planned up to",
        "A meeting can be recorded ahead of its day, and shows as planned until then. "
        "Anything further ahead is refused as a typo.",
        "Four months: enough for a trial day after a notice period, short enough to catch "
        "\"2062\".",
        "number", default=_n(base.plan_ahead_days), current=_n(cfg.plan_ahead_days),
        unit="days", lo=1, hi=730, where="desk.toml [states] plan_ahead_days"))

    # -- Mails --------------------------------------------------------------
    t = THEMES[4]
    add(Question(
        "mail_words", t, "The words of the mails",
        "Three mails, one per outcome: invitation, later, not this time. Each is filled in "
        "with the person's first name and the role. No \"we have your application\": Ashby "
        "already sends it.",
        "The shipped texts are examples in our tone, not yours. The desk says so on every "
        "draft until someone writes their own on House rules.",
        "choice", (Option("examples", "Keep the shipped examples for now"),
                   Option("ours", "We write our own on House rules, before the first real mail")),
        default="examples",
        current="examples" if all(cfg.is_example(m) for m in ("contact", "later",
                                                               "pass")) else "ours",
        where="desk.toml [mail.templates] · House rules"))
    add(Question(
        "mail_mode", t, "How a mail leaves",
        "What happens once a class is settled.",
        "A draft that opens in your own mail app, from your own address; a person reads it "
        "and sends it. Sending on its own would need a mail account, and would be the one "
        "place the desk acts outside the company without a person.",
        "choice", (Option("draft", "A draft; a person sends it"),
                   Option("auto", "Sent automatically once a class is settled", built=False)),
        default="draft", current=cfg.mail_mode, where="desk.toml [mail] mode"))
    add(Question(
        "attach_note", t, "Attach the reading note to a no",
        "The note says what the application was read against and what it did not answer. It "
        "contains no decision and says a machine wrote it.",
        "A no with a reason is the promise of the desk. Turn it off if you would rather "
        "answer in your own words only.",
        "choice", YES_NO, default=_yes(base.attach_note_on_pass),
        current=_yes(cfg.attach_note_on_pass), where="desk.toml [mail] attach_note_on_pass"))
    import drafting
    add(Question(
        "ai_drafts", t, "Let the AI draft messages?",
        "A \"Draft with AI\" button beside the template's draft, once the team has decided "
        "(Ready to contact, Ready to answer no, Talk later). It writes from the outcome, the "
        "role, the first name, the voters' comments, the candidate's own sentences and your "
        "template. The draft lands in the box to edit; nothing is sent.",
        "Off by default: every other part of the desk works without a model. Cost: your "
        "existing Claude subscription (NBH_BACKEND), nothing more. Numbers, links and names "
        "not in the sources are flagged; a draft touching a protected subject is refused; "
        "who asked is recorded. Tone and emphasis stay yours to check.",
        "choice", YES_NO, default="no", current=_yes(drafting.enabled()),
        where="desk.toml [mail] ai_drafts"))
    add(Question(
        "sender", t, "The signature",
        "The name at the end of every mail.",
        "Empty by default: the company name is used until someone writes theirs.",
        "text", default=base.sender, current=cfg.sender,
        where="desk.toml [mail] sender · House rules", apply="sender"))
    add(Question(
        "languages", t, "Languages",
        "The language the mails are written in.",
        "One set of mails, in the language you write them in.",
        "choice", (Option("one", "One language"),
                   Option("per_person", "A version per language, chosen per person",
                          built=False)),
        default="one", current="one", where="desk.toml [mail.templates]"))

    # -- The Tuesday recap ----------------------------------------------------
    t = THEMES[5]
    add(Question(
        "recap_weekday", t, "Recap day",
        "One message a week: new applications, what waits for whose vote, who was written to "
        "and has not replied, the wake-ups due.",
        "Tuesday: far enough from Monday's meetings to be read, early enough to act on "
        "within the week.",
        "choice", tuple(Option(w, w.title()) for w in desk.WEEKDAYS),
        default=base.recap_weekday, current=cfg.recap_weekday,
        where="desk.toml [recap] weekday · House rules", apply="recap_weekday"))
    add(Question(
        "recap_frequency", t, "How often",
        "How often the recap is prepared.",
        "Weekly matches the pace of a small team's applications.",
        "choice", (Option("weekly", "Weekly"),
                   Option("fortnightly", "Every two weeks", built=False),
                   Option("daily", "A short daily digest", built=False)),
        default="weekly", current="weekly", where="desk.toml [recap]"))
    add(Question(
        "awaiting_vote_days", t, "Remind about a missing vote after",
        "An application waiting this long for someone's vote is named in the recap, with "
        "whose vote it waits for.",
        "Six days: under a week, so nothing waits two recaps.",
        "number", default=_n(base.awaiting_vote_days), current=_n(cfg.awaiting_vote_days),
        unit="days", lo=1, hi=60, where="desk.toml [recap] awaiting_vote_days"))
    add(Question(
        "no_reply_days", t, "Flag a candidate who has not replied after",
        "Someone written to this many days ago with no reply recorded is named in the recap.",
        "Eight days: a working week and a weekend, then a nudge.",
        "number", default=_n(base.no_reply_days), current=_n(cfg.no_reply_days),
        unit="days", lo=1, hi=60, where="desk.toml [recap] no_reply_days"))
    add(Question(
        "link_ttl_hours", t, "One-click vote links in the recap stay valid for",
        "The recap draft can carry a link per application, for one voter, usable once. "
        "Opening it never votes: it asks to confirm.",
        "A week: until the next recap.",
        "number", default=_n(metrics.Tracking().link_ttl_hours), current=_n(tr.link_ttl_hours),
        unit="hours", lo=1, hi=24 * 31, where="desk.toml [tracking] link_ttl_hours"))

    # -- The promise and tracking ---------------------------------------------
    t = THEMES[6]
    add(Question(
        "answer_within_days", t, "The promise: an answer within",
        "Every applicant hears back within this many days of applying. Overview measures how "
        "often the promise was kept.",
        "Two weeks: long enough for three busy people to vote, short enough to mean "
        "something to the person waiting.",
        "number", default=_n(metrics.Tracking().answer_within_days),
        current=_n(tr.answer_within_days), unit="days", lo=1, hi=120,
        where="desk.toml [tracking] answer_within_days"))
    add(Question(
        "acknowledgment_counts", t, "Does \"we have your application\" count as the answer?",
        "Whether the acknowledgment mail stops the clock of the promise.",
        "No: it says \"you will hear from us\", so it is not the answer.",
        "choice", YES_NO, default=_yes(metrics.Tracking().acknowledgment_counts),
        current=_yes(tr.acknowledgment_counts),
        where="desk.toml [tracking] acknowledgment_counts"))
    add(Question(
        "agreement_votes", t, "Which vote measures how much the voters agree",
        "Overview shows how often each pair of voters agrees.",
        "The first vote, cast before seeing the others: the latest one has seen them, and "
        "would measure the conversation rather than each person's bar.",
        "choice", (Option("first", "The first vote (blind)"),
                   Option("latest", "The latest vote")),
        default=metrics.Tracking().agreement_votes, current=tr.agreement_votes,
        where="desk.toml [tracking] agreement_votes"))
    add(Question(
        "min_n_percent", t, "No percentage over fewer than",
        "Below this many applications, Overview prints counts, not percentages.",
        "\"100%\" over three applications says less than \"3 of 3\".",
        "number", default=_n(metrics.Tracking().min_n_percent), current=_n(tr.min_n_percent),
        unit="applications", lo=1, hi=100, where="desk.toml [tracking] min_n_percent"))

    # -- Ashby --------------------------------------------------------------------
    t = THEMES[7]
    add(Question(
        "ashby_connect", t, "Connect Ashby",
        "Whether applications arrive from Ashby. Without it they are added by hand or from a "
        "CSV export. The connector is built from Ashby's published API and has never run "
        "against a live account.",
        "Off: nothing happens without your API key, and nothing is connected until you "
        "decide.",
        "choice", (Option("off", "Not connected"),
                   Option("pull", "Connected, applications pulled on demand"),
                   Option("webhook", "Connected, applications arrive as they come (needs a "
                                     "public address and a shared secret)")),
        default="off", current="off", where="ASHBY_API_KEY · desk.toml [ashby]"))
    add(Question(
        "ashby_back", t, "What goes back into Ashby",
        "Once the voters agree, a note on the candidate in Ashby, sent by a named person "
        "(`ashby.py push --send --as <you>`).",
        "A private, silent note (no notification), only for an agreed class, only when a "
        "person sends it. Never automatic.",
        "choice", (Option("note", "A private note, sent by a named person"),
                   Option("nothing", "Nothing goes back"),
                   Option("auto", "A note sent automatically on agreement", built=False)),
        default="note", current="note",
        where="desk.toml [ashby] note_private, notify"))
    add(Question(
        "ashby_score", t, "The AI percentage in the Ashby note",
        "Whether the note carries the fit percentage.",
        "Yes, labelled as a machine's reading of the paper, not the decision.",
        "choice", YES_NO, default=_yes(ashby.Settings().note_with_score),
        current=_yes(ab.note_with_score), where="desk.toml [ashby] note_with_score"))
    add(Question(
        "ashby_comments", t, "The voters' comments in the Ashby note",
        "Whether the one-line comments on the votes go into Ashby.",
        "No: they were written for the other voters, and stay on the desk.",
        "choice", YES_NO, default=_yes(ashby.Settings().note_with_comments),
        current=_yes(ab.note_with_comments), where="desk.toml [ashby] note_with_comments"))

    # -- Access and deployment -------------------------------------------------
    t = THEMES[8]
    add(Question(
        "access_mode", t, "Who can open the desk",
        "How the desk knows who is looking.",
        "A name menu on one laptop is enough to try it, and it refuses to listen beyond that "
        "laptop. For the team: your company sign-in (Google, Microsoft, Cloudflare Access) "
        "in front of it, with the list of allowed emails. See DEPLOY.md.",
        "choice", (Option("pick", "A name menu, on one computer (to try it)"),
                   Option("header", "Behind our company sign-in, for the team")),
        default="pick", current=access_mode, where="desk.toml [access] mode"))
    add(Question(
        "ai_percent", t, "Where the AI percentage comes from",
        "The desk itself never calls an AI model. The fit percentage is optional and comes "
        "from a separate screening run.",
        "A Claude subscription someone on the team already pays: no extra cost. A paid API "
        "key only if you decide so, with a data processing agreement.",
        "choice", (Option("none", "No percentage: applications by hand, classed by people"),
                   Option("subscription", "From a subscription someone already pays"),
                   Option("api", "From a paid API key", built=False)),
        default="subscription", current="subscription", where="DEPLOY.md"))

    # -- Data -------------------------------------------------------------------------
    from console import logo
    said, why = logo.configured(desk.ROOT), logo.problem(desk.ROOT)
    add(Question(
        "logo", t, "Your logo in the header",
        "A small image in the top-left corner of every page, instead of the black square. "
        "A file on this machine: put it in local-assets/, which is never committed."
        + (f" Today: {why}." if why else ""),
        "No logo by default: the desk ships with nobody's brand. PNG, JPEG, WebP or SVG, "
        "200 KB at most.",
        "text", default="", current=said, where="desk.toml [team] logo · DEPLOY.md"))
    t = THEMES[9]
    add(Question(
        "retention", t, "How long applications are kept",
        "Applications, votes and notes are personal data. Today nothing is deleted "
        "automatically.",
        "How long is your choice and your counsel's; the desk does not invent a number. "
        "DEPLOY.md asks for a retention period in any real deployment.",
        "choice", (Option("manual", "Nothing deleted automatically; we decide case by case"),
                   Option("6m", "Deleted 6 months after the last decision", built=False),
                   Option("12m", "Deleted 12 months after the last decision", built=False)),
        default="manual", current="manual", where="DEPLOY.md · runs/"))
    add(Question(
        "pool_consent", t, f"Consent before “{cfg.label('later')}”",
        "Keeping someone on file for a later role keeps their data past the role they "
        "applied for.",
        "The shipped mail tells them and lets them say no. Asking for an explicit yes first "
        "is stricter, and is not built yet.",
        "choice", (Option("tell", "Tell them, and let them say no (shipped mail)"),
                   Option("ask", "Ask for an explicit yes before keeping them", built=False)),
        default="tell", current="tell", where="desk.toml [mail.templates] later"))
    if cfg.solo:
        # One person deciding: nobody to vote with, wait for, sign for or recap to.
        q = [x for x in q if x.id not in SOLO_SKIP]
    return q


# --------------------------------------------------------------------------
# The record
# --------------------------------------------------------------------------

def _path() -> Path:
    return desk.ROOT / "runs" / "desk" / "setup.json"


def load() -> dict[str, Any]:
    f = _path()
    rec = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    rec.setdefault("answers", {})
    rec.setdefault("read", {})
    rec.setdefault("history", [])
    return rec


def _clean(text: str, limit: int, what: str) -> str:
    text = " ".join(str(text).split())
    if len(text) > limit:
        raise DeskError(f"{what} is {limit} characters at most")
    return text


def parse(form: dict[str, str], qs: list[Question]) -> tuple[dict[str, dict[str, Any]],
                                                              list[str], list[str]]:
    """The form, as (answers, ids to apply now, Good-to-know ids read). Refuses junk.

    Every value is checked against what the question allows. A form is text
    anyone could type, and the record is read later as what a partner said.
    """
    answers: dict[str, dict[str, Any]] = {}
    apply: list[str] = []
    accept = form.get("accept", "")
    for q in qs:
        keep = form.get(f"keep_{q.id}") == "1"
        note = _clean(form.get(f"note_{q.id}", ""), NOTE_MAX, f"the note on “{q.title}”")
        said = form.get(f"q_{q.id}")
        #: An empty box is a question left alone, not an answer of "".
        if said is not None and q.kind != "choice" and not said.strip():
            said = None
        if said is None and not keep and accept == q.theme:
            # "Accept all defaults in this section": what ships, said so.
            answers[q.id] = {"answer": q.default, "keep": False, "note": note,
                             "default": True}
            continue
        if keep:
            value = q.current
        elif said is None:
            continue
        elif q.kind == "choice":
            if said not in {o.value for o in q.options}:
                raise DeskError(f"“{q.title}”: {said!r} is not one of the options")
            value = said
        elif q.kind == "number":
            try:
                x = float(said.strip().replace(",", "."))
            except ValueError:
                raise DeskError(f"“{q.title}”: {said!r} is not a number") from None
            if not q.lo <= x <= q.hi:
                raise DeskError(f"“{q.title}”: between {q.lo:g} and {q.hi:g} {q.unit}")
            value = _n(x)
        else:
            value = _clean(said, TEXT_MAX, f"“{q.title}”")
        answers[q.id] = {"answer": value, "keep": keep, "note": note}
        if q.apply and form.get(f"apply_{q.id}") == "1" and value != q.current:
            apply.append(q.id)
    read = [n.id for n in GOOD_TO_KNOW if form.get(f"read_{n.id}") == "1"]
    return answers, apply, read


def settings_values(apply: list[str], answers: dict[str, dict[str, Any]],
                    qs: list[Question], cfg: desk.Config) -> dict[str, Any]:
    """What "apply now" sends to `desk.save_settings`: only keys Settings already edits."""
    by_id = {q.id: q for q in qs}
    out: dict[str, Any] = {}
    for qid in apply:
        key, value = by_id[qid].apply, answers[qid]["answer"]
        if key.startswith("labels."):
            # The whole set: save_settings keeps one labels value, and sending
            # one name alone would drop a name changed earlier.
            out.setdefault("labels", dict(cfg.labels))[key.split(".", 1)[1]] = value
        else:
            out[key] = value
    unknown = {k for k in out} - set(desk.EDITABLE)
    if unknown:  # a question pointing at a key Settings does not edit: a bug here
        raise DeskError(f"not editable here: {', '.join(sorted(unknown))}")
    return out


def save(form: dict[str, str], viewer: str, cfg: desk.Config,
         access_mode: str = "pick", now: datetime | None = None) -> str:
    """Record one partner's answers; apply what they asked to apply. Returns a message.

    The settings go first, through the House rules page's own door: if they
    cannot run, nothing is recorded, and the partner is told why.
    """
    viewer = cfg.voter(viewer)
    qs = questions(cfg, access_mode)
    answers, apply, read = parse(form, qs)
    values = settings_values(apply, answers, qs, cfg)
    if values:
        desk.save_settings(values, by=viewer)
    at = (now or datetime.now(timezone.utc)).isoformat()
    with desk.locked():
        rec = load()
        mine = rec["answers"].setdefault(viewer, {})
        changed = []
        for qid, a in answers.items():
            before = mine.get(qid)
            same = before is not None and all(before.get(k) == a[k] for k in a)
            if not same:
                changed.append(qid)
            mine[qid] = {**a, "at": before["at"] if same else at,
                         **({"applied": at} if qid in apply else
                            {"applied": before["applied"]} if same and before.get("applied")
                            else {})}
        seen = rec["read"].setdefault(viewer, {})
        for nid in read:
            seen.setdefault(nid, at)
        rec["history"].append({"by": viewer, "at": at, "changed": changed, "applied": apply})
        f = _path()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
                     newline="\n")
    n = len(answers)
    left = sum(1 for q in qs if q.id not in mine)
    msg = f"{n} answer{'s' if n != 1 else ''} saved under your name"
    if left:
        msg += f"; {left} not answered yet"
    if apply:
        msg += f"; applied now: {', '.join(apply)}"
    return msg


def may_see_others(viewer: str, rec: dict[str, Any], cfg: desk.Config) -> bool:
    """The desk's blind rule, on answers: a partner answers before reading the others."""
    answered = {v for v, a in rec["answers"].items() if a}
    return (cfg.blind == "off"
            or (cfg.blind == "until_you_vote" and viewer in answered)
            or (cfg.blind == "until_all_voted" and all(v in answered for v in cfg.voters)))


def compare(q: Question, rec: dict[str, Any], cfg: desk.Config) -> str:
    """"differ", "change" (all agree on something new), "agree", or "open"."""
    said = [rec["answers"].get(v, {}).get(q.id) for v in cfg.voters]
    values = {a["answer"] for a in said if a}
    if len(values) > 1:
        return "differ"
    if not values or any(a is None for a in said):
        return "open"
    return "change" if values != {q.current} else "agree"


STATE = {"differ": "answers differ", "change": "all agree on a change",
         "agree": "all keep it", "open": "not everyone has answered"}


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------

def _tools(d: Any, viewer: str, on: str) -> str:
    """The strip above House rules and its questions: two ways to the same settings."""
    q = d._q(viewer)
    links = (("rules", f"/settings{q}", "The rules"), ("form", f"/setup{q}", "Guided questions"),
             ("check", f"/check{q}", "Check"), ("retention", f"/retention{q}", "Retention"))
    # One person on the desk: nobody else's answers to compare, nothing to send round.
    if not d.cfg.solo:
        links += (("answers", f"/setup/answers{q}", "Everyone's answers, side by side"),
                  ("md", f"/setup.md{q}", "Download as Markdown"))
    return '<div class="sutools">' + "".join(
        f'<a class="{"on" if k == on else ""}" href="{h}">{e(t)}</a>' for k, h, t in links) + \
        "</div>"


#: The questions shown open. Every other rule keeps its default and is folded
#: under "more settings" in its section.
ESSENTIAL = ("voters", "blind", "agreement", "go_ahead_alone", "interviews", "ai_drafts",
             "sender", "recap_weekday", "access_mode", "logo")


def page(d: Any, viewer: str) -> str:
    """The questionnaire, pre-filled with this partner's answers or what runs today."""
    cfg = d.cfg
    rec = load()
    mine = rec["answers"].get(viewer, {})
    qs = questions(cfg, d.access.mode)
    asked = sum(1 for x in qs if x.id in ESSENTIAL)
    out = [f'<h1>House rules</h1>{_tools(d, viewer, "form")}'
           f'<p class="lead">The same settings, asked as questions: the {asked} choices that '
           f'matter, one '
           f'question each. Everything else already has a sensible default and is folded under '
           f'<b>more settings</b>. A question you leave alone keeps its '
           f'<span class="tag def">default</span>; an option marked '
           f'<span class="tag req">on request</span> is not built yet. Nothing on the desk '
           f'changes unless you tick <b>apply now</b>.</p>',
           f'<form method="post" action="/setup"><input type="hidden" name="t" '
           f'value="{d.token}"><input type="hidden" name="as" value="{e(viewer)}">'
           f'<input type="hidden" name="back" value="/setup">'
           # Enter in a box presses the form's first button. Without this one,
           # that would be the first section's "Accept all defaults".
           f'<button hidden tabindex="-1"></button>']
    for theme in THEMES:
        main = [question(q, mine.get(q.id), cfg.solo) for q in qs
                if q.theme == theme and q.id in ESSENTIAL]
        rest = [question(q, mine.get(q.id), cfg.solo) for q in qs
                if q.theme == theme and q.id not in ESSENTIAL]
        if not main and not rest:
            continue  # nothing left to ask in it (one person deciding)
        more = (f'<details class="more"><summary>{len(rest)} more setting'
                f'{"s" if len(rest) != 1 else ""}, already on their defaults</summary>'
                f'{"".join(rest)}</details>' if rest else "")
        out.append(f'<section class="su"><h2>{e(theme)}<button class="acc" name="accept" '
                   f'value="{e(theme)}" title="Saves the page; every question left alone in '
                   f'this section takes its default">Accept all defaults in this section'
                   f'</button></h2>{"".join(main)}{more}</section>')
    # The "good to know" notes on the law are no longer shown on this page
    # (06/10): they stay in GOOD_TO_KNOW, on the answers page and in the export.
    out.append('<div class="save"><button>Save my answers</button><span class="sub">Kept with '
               'your name and the date. Nothing is applied unless you ticked “apply now”.'
               '</span></div></form>')
    return "".join(out)


def question(q: Question, mine: dict[str, Any] | None, solo: bool = False) -> str:
    """One rule: the explanation, the options, keep as is, apply now, a note.

    Only this partner's own earlier answer is filled in. The default and what
    runs today are marked beside the options, never ticked on their behalf.
    """
    chosen = mine["answer"] if mine else None
    name = f"q_{q.id}"
    if q.kind == "choice":
        opts = []
        for o in q.options:
            tags = ""
            if o.value == q.default:
                tags += '<span class="tag def">default</span>'
            if o.value == q.current and q.current != q.default:
                tags += '<span class="tag now">now</span>'
            if not o.built:
                tags += '<span class="tag req">on request</span>'
            opts.append(f'<label class="opt"><input type="radio" name="{name}" '
                        f'value="{e(o.value)}"{" checked" if o.value == chosen else ""}>'
                        f'<span>{e(o.label)}{tags}</span></label>')
        field = f'<div class="opts">{"".join(opts)}</div>'
    elif q.kind == "number":
        default = (f'<span class="tag def">default {e(q.default)}</span>'
                   if q.default else "")
        now = (f'<span class="tag now">now {e(q.current)}</span>'
               if q.current != q.default else "")
        fixed = ('<span class="fixed">Set in the code: another value is on request.</span>'
                 if q.fixed else "")
        field = (f'<div class="val"><input type="number" name="{name}" '
                 f'value="{e(chosen or "")}" placeholder="{e(q.current)}" '
                 f'min="{q.lo:g}" max="{q.hi:g}" step="any"> {e(q.unit)}{default}{now}'
                 f'{fixed}</div>')
    else:
        default = (f'<span class="tag def">default: {e(q.default)}</span>'
                   if q.default != q.current else '<span class="tag def">default</span>'
                   if q.default else "")
        field = (f'<div class="val"><input type="text" name="{name}" '
                 f'value="{e(chosen or "")}" maxlength="{TEXT_MAX}" '
                 f'placeholder="{e(q.current or q.default)}">{default}</div>')
    keep = mine is not None and mine.get("keep")
    apply = (f'<label><input type="checkbox" name="apply_{q.id}" value="1"> apply now '
             f'{"(changes House rules)" if solo else "(changes House rules for everyone)"}'
             f'</label>' if q.apply else "")
    note = mine.get("note", "") if mine else ""
    open_ = "" if mine else '<span class="tag na">not answered</span>'
    return (f'<div class="q" id="q-{q.id}"><h3>{e(q.title)}{open_}</h3>'
            f'<p><b>What this means:</b> {e(q.means)}</p>'
            f'<p><b>Why the default:</b> {e(q.why)}</p>'
            f'<div class="where">Where: {e(q.where)}</div>{field}'
            f'<div class="foot"><label><input type="checkbox" name="keep_{q.id}" value="1"'
            f'{" checked" if keep else ""}> keep as is</label>{apply}'
            # Alone, there is nobody to leave a note for.
            + ("" if solo else
               f'<input type="text" name="note_{q.id}" maxlength="{NOTE_MAX}" value="{e(note)}" '
               f'placeholder="a note for the others (optional)">')
            + "</div></div>")


def _said(q: Question, a: dict[str, Any]) -> str:
    s = "keep as is (" + q.label_of(a["answer"]) + ")" if a.get("keep") else q.label_of(
        a["answer"])
    if a.get("default"):
        s += " (the default, accepted for the section)"
    if a.get("applied"):
        s += " — applied"
    return s


def answers(d: Any, viewer: str) -> str:
    """Each rule with each partner's answer; where they differ comes first."""
    cfg = d.cfg
    rec = load()
    qs = questions(cfg, d.access.mode)
    see = may_see_others(viewer, rec, cfg)
    head = (f'<h1>Your call: everyone’s answers</h1><p class="lead">For each rule, what each '
            f'of you answered. What is worth a conversation is where the answers differ.</p>'
            f'{_tools(d, viewer, "answers")}')
    if not see:
        why = ("once you have saved yours" if cfg.blind == "until_you_vote"
               else "once everyone has saved theirs")
        head += (f'<div class="flash">The others’ answers show {why} — the desk’s blind rule, '
                 f'so that nobody answers by agreeing.</div>')
    states = {q.id: compare(q, rec, cfg) for q in qs} if see else {}
    differ = [q for q in qs if states.get(q.id) == "differ"]
    if see:
        lis = "".join(f'<li><a href="#a-{q.id}">{e(q.title)}</a></li>' for q in differ)
        head += (f'<h2>To discuss</h2><div class="sec"><ul>'
                 f'{lis or "<li class=nothing>Nowhere: your answers agree or are not all in.</li>"}'
                 f'</ul></div>')
    out = [head]
    for theme in THEMES:
        items = []
        for q in (x for x in qs if x.theme == theme):
            rows = []
            for v in cfg.voters:
                a = rec["answers"].get(v, {}).get(q.id)
                who = "You" if v == viewer else e(v)
                if a is None:
                    text = '<span class="none">not answered</span>'
                elif v != viewer and not see:
                    text = '<span class="hid">answered — hidden until you answer</span>'
                else:
                    note = f' — “{e(a["note"])}”' if a.get("note") else ""
                    text = f'{e(_said(q, a))}{note} <small>{e(a["at"][:10])}</small>'
                rows.append(f'<span class="who">{who}</span><span>{text}</span>')
            st = states.get(q.id)
            badge = f'<span class="state {st}">{STATE[st]}</span>' if st else ""
            items.append(f'<div class="q" id="a-{q.id}"><h3>{e(q.title)}{badge}</h3>'
                         f'<p>Now: <b>{e(q.label_of(q.current))}</b></p>'
                         f'<div class="ans">{"".join(rows)}</div></div>')
        out.append(f'<section class="su"><h2>{e(theme)}</h2>{"".join(items)}</section>')
    read = []
    for n in GOOD_TO_KNOW:
        who = [v for v in cfg.voters if n.id in rec["read"].get(v, {})]
        read.append(f'<li>{e(n.title)} — read by {e(", ".join(who)) if who else "nobody yet"}'
                    f'</li>')
    out.append(f'<h2>Good to know</h2><div class="sec"><ul>{"".join(read)}</ul></div>')
    return "".join(out)


def markdown(d: Any, viewer: str, now: datetime) -> tuple[bytes, str]:
    """Every question, its options and the answers, as text to mail or print.

    The same blind rule as the page: an export is a way to read, so it may
    not show what the page would hide.
    """
    cfg = d.cfg
    rec = load()
    qs = questions(cfg, d.access.mode)
    see = may_see_others(viewer, rec, cfg)
    company = cfg.company or "the team"
    lines = [f"# Hiring desk: set up for {company}", "",
             f"Exported by {viewer} on {now.date().isoformat()}. One question per rule the "
             f"desk follows: what it means, why the default, the options. Options marked "
             f"*on request* are not built yet.", ""]
    if not see:
        lines += ["*The others' answers are not in this export: they show once you have "
                  "answered yourself (the desk's blind rule).*", ""]
    for theme in THEMES:
        lines += [f"## {theme}", ""]
        for q in (x for x in qs if x.theme == theme):
            lines += [f"### {q.title}", "", f"**What this means:** {q.means}", "",
                      f"**Why the default:** {q.why}", "", f"Where: {q.where}", ""]
            if q.kind == "choice":
                for o in q.options:
                    tags = [t for t, on in (("default", o.value == q.default),
                                            ("now", o.value == q.current != q.default),
                                            ("on request", not o.built)) if on]
                    mark = "x" if o.value == q.current else " "
                    lines.append(f"- [{mark}] {o.label}" + (f" ({', '.join(tags)})" if tags
                                                            else ""))
            else:
                lines.append(f"- Now: {q.label_of(q.current) or '(empty)'}"
                             f"; default: {q.label_of(q.default) or '(empty)'}"
                             + ("; set in the code, another value on request" if q.fixed
                                else ""))
            lines += ["", "Answers:"]
            for v in cfg.voters:
                a = rec["answers"].get(v, {}).get(q.id)
                if a is None:
                    said = "not answered"
                elif v != viewer and not see:
                    said = "answered (hidden until you answer)"
                else:
                    said = _said(q, a) + (f' -- "{a["note"]}"' if a.get("note") else "") + \
                        f" ({a['at'][:10]})"
                lines.append(f"- {v}: {said}")
            if see:
                lines.append(f"- **{STATE[compare(q, rec, cfg)]}**")
            lines.append("")
    lines += ["## Good to know", ""]
    for n in GOOD_TO_KNOW:
        who = [v for v in cfg.voters if n.id in rec["read"].get(v, {})]
        lines += [f"### {n.title}", "", n.text, "",
                  f"Read by: {', '.join(who) if who else 'nobody yet'}", ""]
    name = f"hiring-desk-setup-{now.date().isoformat()}.md"
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8"), name

