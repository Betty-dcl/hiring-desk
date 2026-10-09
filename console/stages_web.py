"""Where an application stands, on the page: a few words on each row, and at the
top of the application's page the state, the next step and one button forward.

Kept out of `desk_web.py`, which only calls in here, so the desk's page code
stays what it was. The rules are in `stages.py`; this file only shows them.

* **On every row, the state in words** -- "Voting · 2 of 3", "Contacted
  3 Oct by Ana", "Talk later -- until 12 Jan". Words, not colours.
* **At the top of the page, what comes next**: the state, "Next step: ...",
  then the actions. The dated history is at the bottom of the page.
* **One button forward.** The next step, a date that can be set back
  ("contacted yesterday"), and the ways out folded behind it. The form says
  which state the person was looking at: if a colleague moved it meanwhile,
  nothing is written.
"""

from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import desk
import stages
from desk import Application, Config, DeskError, outcome, votes
from pipeline import Standing

e = html.escape

CSS = """
.stp{font-size:13px;color:var(--mute);margin-right:6px;white-space:nowrap}
.stg{margin-top:10px}
.stg .nxt{font-size:15px;display:flex;gap:6px 14px;flex-wrap:wrap;align-items:baseline}
.stg .nxt b{font-weight:600;color:var(--ink)}
.stg .nxt span{color:var(--mute);font-size:14px}
.stg .out{margin-top:4px;font-size:13px;color:var(--mute)}
.stg .act{margin-top:10px}
.stepf{margin-top:12px}
.stepf .row1{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.stepf .row1 label{font-size:13px;color:var(--mute);display:flex;gap:6px;align-items:center}
.stepf .exits{display:flex;gap:6px;flex-wrap:wrap}
.stepf .exits button{font-size:13px;color:var(--mute)}
.stepf .exits button:hover{color:var(--ink)}
.stepf .panel label{font-size:13px;color:var(--mute);display:flex;gap:6px;align-items:center}
.stg .who-moves{font-size:12.5px;color:var(--faint);margin-top:8px}
.goa{margin-top:12px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.goa .sub{max-width:560px}
.writer{margin-top:12px}
.writer>summary{list-style:none;display:inline-block;cursor:pointer;background:var(--ink);
color:var(--bg);border:1px solid var(--ink);border-radius:var(--radius);padding:4px 12px;font-size:14px}
.writer>summary::-webkit-details-marker{display:none}
.writer[open]>summary{background:var(--panel);color:var(--mute);border-color:var(--line)}
.writer label{display:block;font-size:12px;color:var(--faint);margin:8px 0 4px}
.writer input[type=text],.writer textarea{width:100%;font-size:14px;border:1px solid var(--line);
border-radius:var(--radius);padding:6px 8px;background:var(--panel);color:var(--ink)}
.writer textarea{min-height:200px;resize:vertical;line-height:1.5}
"""

#: Which tab a state lives under, after the votes' own tabs.
TABS = ("todo", "waiting", "discuss", "write", "process", "later", "closed", "all")
#: One person deciding works in phases, in the order an application goes
#: through them: what has just arrived and is left to sort, then the people
#: they want (each followed on its row, from the first message to the trial
#: day), what they are not sure of, the no's to send, and everyone. The pool has a
#: page of its own (console/pool_web.py), across every role.
SOLO_TABS = ("todo", "interested", "discuss", "no", "all")


def tabs_for(cfg: Config) -> tuple[str, ...]:
    """The tabs shown, in order: a team's, or one person's phases.

    A choice switched off has no tab (what was put there is still under All);
    each choice of the team's own has one, after the pool.
    """
    if not cfg.solo:
        return tuple(TABS)
    from console import choices_web
    on = choices_web.shown(cfg)
    out = [t for t in SOLO_TABS if not (t in ("discuss", "later") and t not in on)]
    at = out.index("no")
    return tuple(out[:at] + [f"x{i}" for i in range(len(choices_web.own(cfg)))] + out[at:])


def first_tab(cfg: Config) -> str:
    return tabs_for(cfg)[0]


def kind(st: stages.State) -> str:
    """A family of states, for the colour of the word on a row."""
    if st.id == "hired":
        return "hired"
    if st.ended:
        return "end"
    if st.id == "talk_later":
        return "later"
    if st.id in ("to_contact", "to_answer_no", "team_to_decide"):
        return "todo"
    if st.id in stages.TALKING:
        return "process"
    return "vote"


def pill(st: stages.State, cfg: Config) -> str:
    """The state in words, plain text: no colour carries a meaning the words do not."""
    return (f'<span class="stp k-{kind(st)}" title="where it stands">'
            f'{e(stages.said(st, cfg))}</span>')


def tab_of(st: stages.State, s: Standing, viewer: str, cfg: Config) -> str:
    """The list's tab: the votes' phase by whose turn it is, then the state itself."""
    if st.ended:
        return "closed"
    if st.id == "talk_later":
        from console import choices_web
        mine = choices_web.own_of(s, viewer, cfg)
        return choices_web.tab_id(cfg, mine) if mine else "later"
    if st.id == "trial_day" and not st.planned:
        # The trial day has happened: the desk's part is over.
        return "closed"
    if cfg.solo and (st.id in stages.TALKING or st.id == "to_contact"):
        return "interested"
    if cfg.solo and st.id == "to_answer_no":
        return "no"
    if st.id in stages.TALKING:
        return "process"
    if st.id in ("to_contact", "to_answer_no"):
        return "write"
    if st.id == "team_to_decide":
        return "discuss"
    vs = votes(s)
    if outcome(vs, cfg).settled:
        return "discuss"
    return "todo" if viewer not in vs else "waiting"


def tab_names(cfg: Config) -> dict[str, str]:
    n = stages.names(cfg)
    # One person deciding has nothing to wait for: the first tab is what is
    # left to sort, and the "waiting" tab is not shown (desk_web.py).
    return {"todo": "To sort" if cfg.solo else "Your vote",
            "waiting": "Waiting for the others to vote",
            "discuss": n["team_to_decide"],
            "write": f"{n['to_contact']} · {n['to_answer_no']}", "process": "In process",
            "later": n["talk_later"], "closed": "Closed",
            "interested": "Interested", "no": cfg.label("pass"),
            **{f"x{i}": x for i, x in enumerate(cfg.extra_choices)},
            # Everyone who ever applied, decided or not, closed or not: the
            # role and the period above the tabs narrow it down.
            "all": "All"}


EMPTY = {
    "write": ("Nobody to write to.",
              "When the votes agree, the application waits here until a person writes."),
    "process": ("Nobody in process.",
                "Once someone is contacted, their interviews and trial day are here."),
    "later": ("Nobody to talk to later.",
              "An application parked until a date waits here, and comes back on its day."),
}
#: The same, said to one person deciding alone.
SOLO_EMPTY = {
    "write": ("Nobody to write to.",
              "Once you have decided, the application waits here until you have written."),
}


def section(d: Any, app: Application, s: Standing, viewer: str, back: str,
            now: datetime, votes_html: str = "") -> str:
    """The top of an application on its page: where it stands, the next step, the actions.

    In the order a partner acts: the state and what comes next, then the
    vote (while votes are open), then "go ahead", the message to write and
    the step to record. The dated history is at the bottom of the page.
    """
    cfg: Config = d.cfg
    st = stages.derive(s, cfg, now)
    from console.list_web import state_text
    nxt = stages.next_line(st, s, cfg)
    out = (f'<div class="nxt"><b>{e(state_text(st, s, cfg))}</b>'
           f'<span>Next step: {e(nxt)}</span></div>')
    if st.note and st.id != "votes_in":
        out += f'<div class="out">{e(st.note)}</div>'
    write = writer(d, app, st, viewer) if stages.meaning_of(st, cfg) else ""
    form = _form(d, app, st, viewer, back, now)
    return (f'<div class="stg">{out}{votes_html}{_go_ahead(d, app, s, st, viewer, back, now)}'
            f'{write}{form}</div>')


def _hidden(d: Any, app: Application, st: stages.State, viewer: str, back: str) -> str:
    return (f'<input type="hidden" name="t" value="{d.token}">'
            f'<input type="hidden" name="as" value="{e(viewer)}">'
            f'<input type="hidden" name="posting" value="{e(app.posting_id)}">'
            f'<input type="hidden" name="candidate" value="{e(app.candidate_id)}">'
            f'<input type="hidden" name="from" value="{e(st.id)}">'
            f'<input type="hidden" name="back" value="{e(back)}">')


def _go_ahead(d: Any, app: Application, s: Standing, st: stages.State, viewer: str,
              back: str, now: datetime, brief: bool = False) -> str:
    """"Contact now without waiting": one partner starts, the others vote later.

    Before the viewer's own yes it is one button that records that yes and goes
    ahead. It shows whatever the others voted, so it tells nothing about them.
    """
    cfg: Config = d.cfg
    if cfg.solo or st.id not in ("new", "votes_in", "team_to_decide"):
        return ""  # one person deciding waits for nobody
    # On a list row the button stays quiet; on the page it is the dark one.
    cls = "" if brief else ' class="go"'
    mine = votes(s).get(viewer)
    if mine is None or mine.meaning != "contact":
        if cfg.go_ahead_alone == "all_voted" or (
                cfg.go_ahead_alone == "owner" and viewer not in cfg.advancers):
            return ""
        sub = ("" if brief else
               f'<span class="sub">Records your “{e(cfg.label("contact"))}” and moves it to '
               f'{e(stages.name("to_contact", cfg))} now, under your name. The others can '
               f'still vote.</span>')
        return (f'<form method="post" action="/go-ahead" class="goa">'
                f'{_hidden(d, app, st, viewer, back)}<input type="hidden" name="yes" value="1">'
                f'<button{cls}>Contact now without waiting for the others</button>'
                f'{sub}</form>')
    why = stages.why_not_go_ahead(s, viewer, cfg, now)
    if why:
        return f'<div class="who-moves">Going ahead alone: {e(why)}.</div>'
    missing = outcome(votes(s), cfg).missing
    said = (f"without waiting for {', '.join(missing)}" if missing
            else "although the votes differ")
    sub = ("" if brief else
           f'<span class="sub">Moves it to {e(stages.name("to_contact", cfg))} now, {e(said)}, '
           f'under your name. Missing votes can still be cast.</span>')
    return (f'<form method="post" action="/go-ahead" class="goa">'
            f'{_hidden(d, app, st, viewer, back)}'
            f'<button{cls}>Contact now without waiting for the others</button>'
            f'{sub}</form>')


def writer(d: Any, app: Application, st: stages.State, viewer: str) -> str:
    """"Write the message": the draft for this state, editable here, opened in your mail.

    The text starts from `stages.draft` -- the team's template for this
    outcome, with the first name and the role. Edits stay in the page; "Open
    in my mail" carries them. Nothing is sent from the desk, and nothing is
    recorded until someone marks it below.
    """
    from desk import DeskError
    cfg: Config = d.cfg
    try:
        m, _ = stages.draft(app.candidate_id, app.posting_id, cfg)
    except DeskError as err:
        return f'<div class="who-moves">{e(str(err))}</div>'
    subject, body, to = str(m["Subject"]), m.get_content(), str(m["To"] or "")
    href = f"mailto:{quote(to)}?subject={quote(subject)}&body={quote(body)}"
    meaning = stages.meaning_of(st, cfg)
    ex = ('<div class="ex">This starts from the example text, not yours yet. Write your own '
          f'under <a href="/settings{d._q(viewer)}">House rules</a>.</div>'
          if cfg.is_example(meaning) else "")
    no_to = ("" if to else '<div class="ex">No email on file: add the address in your mail '
             'app.</div>')
    word = {"pass": "answer", "follow_up": "reminder",
            "next_interview": "invitation to the next interview"}.get(meaning, "message")
    if meaning in stages.MEETINGS:
        word = f"invitation to the {stages.name(meaning, cfg).lower()}"
    from console import drafting_web
    ai = drafting_web.box(d, app, viewer, to)
    return (f'<details class="writer"{" open" if ai else ""}><summary>Write the {word}'
            f'</summary>{ai}'
            f'<div class="mailbox" data-mailto data-to="{e(to)}">'
            f'<label>Subject</label><input type="text" data-m="s" value="{e(subject)}">'
            f'<label>Message</label><textarea data-m="b">{e(body)}</textarea>{ex}{no_to}'
            f'<div class="buttons"><a class="primary" data-m="open" href="{e(href)}">Open in my '
            f'mail</a><button type="button" data-copy="{e(subject + chr(10) + chr(10) + body)}">'
            f'Copy the text</button><a href="/draft?posting={quote(app.posting_id)}&candidate='
            f'{quote(app.candidate_id)}{d._q(viewer, "&")}">.eml</a>'
            f'{drafting_web.button(d, app, viewer, "")}</div>'
            f'<div class="sub">Nothing is sent from here. Once you have sent it, mark it below.'
            f'</div></div></details>')


def go_ahead_handle(f: dict[str, str], viewer: str, cfg: Config) -> str:
    if f.get("yes") == "1":
        # One click from a partner who had not said yes: their yes first, then
        # ahead. The state moves with the vote, so there is nothing to expect.
        posting, cid = f.get("posting", ""), f.get("candidate", "")
        pipe = desk._pipelines().get(posting)
        mine = (votes(pipe.standing(cid)).get(viewer)
                if pipe is not None and cid in pipe.candidates else None)
        if mine is None or mine.meaning != "contact":
            desk.vote_now(posting, cid, viewer, "contact", cfg)
        stages.go_ahead_now(posting, cid, viewer, cfg)
        return f"{stages.name('to_contact', cfg)}, you went ahead without waiting"
    stages.go_ahead_now(f.get("posting", ""), f.get("candidate", ""), viewer, cfg,
                        expect=f.get("from", ""))
    return f"{stages.name('to_contact', cfg)}, you went ahead without waiting"


def _form(d: Any, app: Application, st: stages.State, viewer: str, back: str,
          now: datetime) -> str:
    cfg: Config = d.cfg
    ok = stages.allowed(st, cfg)
    if not ok:
        return ""
    if cfg.advancers and viewer not in cfg.advancers:
        return (f'<div class="who-moves">Steps are moved by {e(", ".join(cfg.advancers))} '
                f'on this desk (House rules).</div>')
    today = now.astimezone(timezone.utc).date()
    hidden = _hidden(d, app, st, viewer, back)

    def reason(x: str) -> str:
        # One box per way out, each with its own name: a hidden panel's box
        # is still sent, and the first of two same-named boxes would win.
        return (f'<input type="text" name="reason_{x}" maxlength="280" '
                f'placeholder="a line on why (optional)">')
    if ok == ["reopen"]:
        return (f'<form method="post" action="/step" class="stepf">{hidden}<div class="row1">'
                f'{reason("reopen")}<button class="go" name="step" value="reopen">Reopen</button>'
                f'</div></form>')
    main, exits = ok[0], ok[1:]
    if main in stages.EXITS:
        main, exits = None, ok
    # From an interview that is not the last, the trial day may come next: a
    # second, quieter button, not a way out.
    skip = main is not None and "trial_day" in exits
    exits = [x for x in exits if x not in stages.MEETINGS]
    row = []
    if main is not None or any(x != "talk_later" for x in exits):
        ahead = (cfg.plan_ahead_days if main in stages.MEETINGS else stages.SLACK_DAYS)
        hint = ("a later day plans it" if main in stages.MEETINGS else "set it back if it "
                "was earlier")
        row.append(f'<label>On <input type="date" name="on" value="{today.isoformat()}" '
                   f'max="{(today + timedelta(days=ahead)).isoformat()}" '
                   f'title="{e(hint)}"></label>')
    if main is not None:
        label = ("It happened: record it" if main == st.id and st.planned
                 else stages.action(main, cfg))
        row.append(f'<button class="go" name="step" value="{main}">{e(label)}</button>')
    if skip:
        row.append(f'<button name="step" value="trial_day" title="When this role needs no '
                   f'further interview">Straight to the '
                   f'{e(stages.name("trial_day", cfg).lower())}</button>')
    panels = []
    if exits:
        btns = "".join(f'<button type="button" data-open="{x}">'
                       f'{e(stages.ACTION[x] if x == "stop" else stages.name(x, cfg))}</button>'
                       for x in exits)
        row.append(f'<span class="exits">{btns}</span>')
        for x in exits:
            word = stages.name(x, cfg)
            until = ""
            # The pool has no date unless the team wants one.
            if x == "talk_later" and cfg.later_needs_date:
                until = (f'<label>Until{"" if cfg.later_needs_date else " (optional)"} '
                         f'<input type="date" name="until" '
                         f'min="{(today + timedelta(days=1)).isoformat()}"></label>')
            word = stages.ACTION[x] if x == "stop" else word
            panels.append(f'<div class="panel" data-for="{x}">{until}{reason(x)}'
                          f'<button class="go" name="step" value="{x}">{e(word)}</button>'
                          f'</div>')
    # Enter in a box presses the form's first button: here, one that records nothing.
    return (f'<form method="post" action="/step" class="stepf">{hidden}'
            f'<button hidden tabindex="-1" name="step" value=""></button>'
            f'<div class="row1">{"".join(row)}</div>{"".join(panels)}</form>')


def handle(f: dict[str, str], viewer: str, cfg: Config) -> str:
    """The /step form: record it under the lock, or raise with why not."""
    step = f.get("step", "")
    if not step:
        raise DeskError("nothing recorded: press the button of the step you mean")
    on = f.get("until", "") if step == "talk_later" else f.get("on", "")
    stages.advance_now(f.get("posting", ""), f.get("candidate", ""), step, viewer, cfg,
                       on=on, expect=f.get("from", ""), reason=f.get(f"reason_{step}", ""),
                       hour=f.get("time", "") if step in stages.MEETINGS else "",
                       note=f.get("note", "") if step in stages.MEETINGS else "")
    if step == "stop":
        return f"stopped: the answer to send is under “{cfg.label('pass')}”"
    return f"{stages.name(step, cfg) if step != 'reopen' else 'reopened'}, recorded"
