"""The "Interested" tab: each person you want, followed on their own row.

For one person deciding alone. A yes does not leave the page it was given
on to live in a menu of states: the row itself shows the way through, from
the first message to the trial day, and the one thing to do now.

  To contact → Contacted → Replied, OK → First interview → ... → Trial day

* **Ready to contact**: write the message, then tick "Contacted".
* **Contacted**: tick "They replied: OK" when they do (or plan the first
  interview straight away). Writing them a reminder is one click.
* **Replied**: plan the first interview, with its date if there is one.
* **An interview**: its date, notes on it, then an arrow: on to the next
  interview, or "Stop here". Stopping moves them to the no's to send; once
  that answer is marked as sent, they leave the desk.

The pool and "they withdrew" stay one quiet click away on every step. Nothing
is sent from here: every tick says a person did it.

The rules (what may follow what, which date is believable) are `stages.py`'s;
this file only draws them.
"""

from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone
from typing import Any

import stages
from desk import NOTE_MAX, Application, Config
from pipeline import Standing

e = html.escape

CSS = """
.trk{margin-top:14px;border-top:1px solid var(--line2);padding-top:12px}
.trk ol.steps{list-style:none;display:flex;flex-wrap:wrap;gap:4px 0;margin:0;padding:0;
font-size:13px}
.trk ol.steps li{color:var(--faint);white-space:nowrap}
.trk ol.steps li+li::before{content:"\\2192";margin:0 8px;color:var(--line)}
.trk ol.steps li.done{color:var(--mute)}
.trk ol.steps li.done b::before{content:"\\2713  ";color:var(--yes)}
.trk ol.steps li b{font-weight:500}
.trk ol.steps li.now b,.trk ol.steps li.planned b{color:var(--ink);font-weight:650;
border-bottom:2px solid var(--accent)}
.trk ol.steps small{color:var(--faint);margin-left:4px}
.trk .do{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:12px}
.trk .do label{font-size:14px;color:var(--mute);display:flex;gap:6px;align-items:center}
.trk label.tick{display:inline-flex;gap:8px;align-items:center;font-size:15px;color:var(--ink);
cursor:pointer;border:1px solid var(--line);border-radius:var(--radius);padding:6px 12px;
background:var(--panel)}
.trk label.tick input{width:18px;height:18px;margin:0;cursor:pointer}
.trk .when{font-size:15px;color:var(--ink)}
.trk .quiet{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px;align-items:center}
.trk .quiet button{font-size:12.5px;color:var(--mute)}
.trk .quiet span{font-size:12.5px;color:var(--faint)}
.trk button.stop{color:var(--no);border-color:var(--no)}
.trk .notes{margin-top:12px;font-size:13.5px}
.trk .notes .n{color:var(--mute);margin-top:3px}.trk .notes .n i{color:var(--faint);font-style:normal}
.trk .notes form{display:flex;gap:8px;margin-top:6px;flex-wrap:wrap}
.trk .notes input[type=text]{flex:1;min-width:200px}
.trk .writer{margin-top:0}
.ans{margin-top:14px;display:flex;gap:10px;flex-wrap:wrap;align-items:flex-start}
.ans .writer{margin-top:0}
"""

#: The states this row follows: the way through after a yes. What follows the
#: trial day is another process, kept elsewhere.
FOLLOWED = ("to_contact", "contacted", "replied") + stages.ROUNDS + ("trial_day",)


def followed(st: stages.State) -> bool:
    """Is this row one the Interested tab follows step by step?"""
    if st.id == "trial_day":
        return st.planned
    return st.id in FOLLOWED


def _hidden(d: Any, app: Application, st: stages.State, viewer: str, back: str,
            step: str = "") -> str:
    from console.stages_web import _hidden as hidden
    # "list": back to this very list after the click, not to the person's page.
    return (hidden(d, app, st, viewer, back) + '<input type="hidden" name="list" value="1">'
            + (f'<input type="hidden" name="step" value="{e(step)}">' if step else ""))


def _steps(s: Standing, cfg: Config, now: datetime) -> str:
    marks, _ = stages.journey(s, cfg, now)
    out = []
    for m in marks:
        if m.id not in FOLLOWED:
            continue
        when = f"<small>{e(stages.short(m.on))}</small>" if m.on and m.status != "ahead" else ""
        out.append(f'<li class="{m.status}"><b>{e(stages.name(m.id, cfg))}</b>{when}</li>')
    return f'<ol class="steps">{"".join(out)}</ol>'


def _tick(d: Any, app: Application, st: stages.State, viewer: str, back: str, step: str,
          words: str) -> str:
    """A box that records a step when ticked: the date is today."""
    return (f'<form method="post" action="/step">{_hidden(d, app, st, viewer, back, step)}'
            f'<label class="tick"><input type="checkbox" data-submit> {e(words)}</label>'
            f'<noscript><button class="go">Save</button></noscript></form>')


def _plan(d: Any, app: Application, st: stages.State, viewer: str, back: str, step: str,
          words: str, now: datetime, value: str = "", hour: str = "",
          arrow: bool = False, memo: str = "") -> str:
    """"<Interview> on [date] at [time] [Plan it]": with no date, it happened today.

    The day and the time fill the invitation's {day} and {time}.
    """
    cfg: Config = d.cfg
    today = now.astimezone(timezone.utc).date()
    top = (today + timedelta(days=cfg.plan_ahead_days)).isoformat()
    name = stages.name(step, cfg)
    return (f'<form method="post" action="/step" class="do">'
            f'{_hidden(d, app, st, viewer, back)}'
            f'<label>{"&rarr; " if arrow else ""}{e(name)} on <input type="date" name="on" '
            f'value="{e(value)}" max="{top}"></label>'
            f'<label>at <input type="time" name="time" value="{e(hour)}"></label>'
            f'<input type="text" name="note" maxlength="140" value="{e(memo)}" '
            f'placeholder="Note: who will be there" aria-label="Note">'
            f'<button class="go" name="step" value="{e(step)}">{e(words)}</button></form>')


def _quiet(d: Any, app: Application, st: stages.State, viewer: str, back: str) -> str:
    """The ways out, small: stop here, the pool, they withdrew."""
    cfg: Config = d.cfg
    ok = stages.allowed(st, cfg)
    btns = []
    if "stop" in ok:
        btns.append(f'<button class="stop" name="step" value="stop" title="Moves them to '
                    f'“{e(cfg.label("pass"))}”, where the answer is written">'
                    f'&#x2715; {e(stages.ACTION["stop"])}</button>')
    # A pool that needs a date is set on the person's page, where the date is.
    if "talk_later" in ok and not cfg.later_needs_date:
        btns.append(f'<button name="step" value="talk_later">{e(cfg.label("later"))}</button>')
    if "withdrew" in ok:
        btns.append('<button name="step" value="withdrew">They withdrew</button>')
    if not btns:
        return ""
    return (f'<form method="post" action="/step" class="quiet">'
            f'{_hidden(d, app, st, viewer, back)}<span>Or:</span>{"".join(btns)}</form>')


def _notes(d: Any, app: Application, s: Standing, st: stages.State, viewer: str,
           back: str, every: bool = False) -> str:
    """The latest notes (all of them on the person's page), and a line to add one."""
    cfg: Config = d.cfg
    said = [ev for ev in s.events if ev.kind == "noted"]
    said = said if every else said[-3:]
    lines = "".join(f'<div class="n"><i>{e(ev.at[:10])}</i> {e(str(ev.detail.get("text", "")))}'
                    f'</div>' for ev in said)
    about = stages.name(st.id, cfg)
    form = (f'<form method="post" action="/note"><input type="hidden" name="t" '
            f'value="{d.token}"><input type="hidden" name="as" value="{e(viewer)}">'
            f'<input type="hidden" name="posting" value="{e(app.posting_id)}">'
            f'<input type="hidden" name="candidate" value="{e(app.candidate_id)}">'
            f'<input type="hidden" name="back" value="{e(back)}">'
            f'<input type="hidden" name="about" value="{e(about)}">'
            f'<input type="text" name="text" maxlength="{NOTE_MAX - len(about) - 2}" '
            f'placeholder="A note on the {e(about.lower())}" required>'
            f'<button>Add the note</button></form>')
    return f'<div class="notes">{lines}{form}</div>'


def tracker(d: Any, app: Application, s: Standing, st: stages.State, viewer: str,
            back: str, now: datetime, every: bool = False) -> str:
    """The way through on one row, and the one thing to do at the step they are at."""
    from console.stages_web import writer
    cfg: Config = d.cfg
    n = stages.names(cfg)
    do, notes = "", ""
    if st.id == "to_contact":
        do = (f'<div class="do">{writer(d, app, st, viewer)}'
              f'{_tick(d, app, st, viewer, back, "contacted", n["contacted"])}</div>')
    elif st.id == "contacted":
        from console.outlook_web import wrote
        do = (wrote(d, app, st)
              + f'<div class="do">{_tick(d, app, st, viewer, back, "replied", stages.ACTION["replied"])}'
              f'{writer(d, app, st, viewer)}</div>')
    elif st.id == "replied":
        # The invitation can go first, its day and time left blank; or the
        # interview is planned first, and the invitation fills them in.
        do = (_plan(d, app, st, viewer, back, stages.ROUNDS[0], "Plan it", now)
              + f'<div class="do">{writer(d, app, st, viewer)}</div>')
    elif st.id in stages.MEETINGS and st.planned:
        at = (f" at {st.time}" if st.time else "") + (f" · {st.memo}" if st.memo else "")
        from console.calendar_web import gcal_link
        do = (f'<div class="do"><span class="when">{e(n[st.id])}: '
              f'<b>{e(stages.short(st.on))}{e(at)}</b></span>{gcal_link(d, app, st)}'
              f'{writer(d, app, st, viewer)}</div>'
              + _plan(d, app, st, viewer, back, st.id, "Change the date", now, value=st.on,
                      hour=st.time, memo=st.memo))
        notes = _notes(d, app, s, st, viewer, back, every)
    elif st.id in stages.ROUNDS:
        nxt = stages.forward(st.id, cfg) or "trial_day"
        do = _plan(d, app, st, viewer, back, nxt, "Continue", now, arrow=True)
        if nxt != "trial_day":
            do += _plan(d, app, st, viewer, back, "trial_day", "Straight to it", now, arrow=True)
        do += f'<div class="do">{writer(d, app, st, viewer)}</div>'
        notes = _notes(d, app, s, st, viewer, back, every)
    if every and not notes:
        # On the person's page, the notes are always there, whatever the step.
        notes = _notes(d, app, s, st, viewer, back, every)
    return (f'<div class="trk">{_steps(s, cfg, now)}{do}{notes}'
            f'{_quiet(d, app, st, viewer, back)}</div>')


def answer(d: Any, app: Application, st: stages.State, viewer: str, back: str) -> str:
    """On "Not for us": the answer, ready to send, then one click and they leave."""
    from console.stages_web import writer
    return (f'<div class="ans">{writer(d, app, st, viewer)}'
            f'<form method="post" action="/step">{_hidden(d, app, st, viewer, back)}'
            f'<button name="step" value="answered_no">Sent: take them off the list</button>'
            f'</form></div>')
