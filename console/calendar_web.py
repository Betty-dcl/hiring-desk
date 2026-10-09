"""The Calendar: a week of interviews, to see where the next ones could fit.

For the person who runs hiring, not for the partners: nobody has to keep their
availability anywhere. The page shows, for one week:

* every interview and trial day on the desk, at its day and time, each a
  link to the person;
* on the side, the people waiting for an interview to be planned: those who
  replied OK, those whose interview has happened and who go on, and those
  written to who have not replied yet;
* one form: who, which day, what time. A click on an empty slot of the week
  fills in the day and the time; "Put it in" records the interview as planned,
  exactly as the person's row would.

Every planned meeting also has an "Add to Google Calendar" link: Google's own
page opens with the event filled in (title, day, time, the candidate as a
guest), and the person saves it there, or not. The desk reads no calendar and
writes to none.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urlencode

import desk
import stages
from desk import Config, DeskError, Person

e = html.escape

#: A meeting of your own, outside the steps ("call with the CTO"): a label on
#: the application, with its day; it moves nothing.
MEETING_TAG = "meeting"

#: The hours drawn on the week, in half hours. A meeting set outside them is
#: still shown, in the first or the last row.
FIRST_HOUR, LAST_HOUR = 8, 20

CSS = """
.cal-bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:14px}
.cal-bar h1{margin:0 8px;font-size:22px}
.cal-btn{display:inline-block;border:1px solid var(--line);border-radius:var(--radius);
padding:5px 12px;text-decoration:none;color:var(--ink);background:var(--panel);font-size:14px}
.cal-btn:hover{border-color:var(--ink)}
.cal-jump input{font-size:14px}
.cal-seg{margin-left:auto;display:inline-flex;border:1px solid var(--line);border-radius:var(--radius);
overflow:hidden}
.cal-seg a{padding:5px 14px;text-decoration:none;color:var(--mute);font-size:14px;background:var(--panel)}
.cal-seg a.on{background:var(--ink);color:var(--panel)}
.cal-wrap{display:grid;grid-template-columns:minmax(0,1fr) 280px;gap:18px;align-items:start}
.cal{border-collapse:collapse;width:100%;table-layout:fixed;font-size:12.5px;background:var(--panel)}
.cal th{font-weight:600;font-size:13px;padding:6px 4px;border-bottom:1px solid var(--line);
text-align:left;color:var(--ink)}
.cal th.today{color:var(--accent)}
.cal th.hr,.cal td.hr{width:46px;color:var(--faint);font-weight:400;font-size:11.5px;
vertical-align:top;padding:2px 4px}
.cal td{border-top:1px solid var(--line2);border-left:1px solid var(--line2);height:22px;
padding:1px 3px;vertical-align:top}
.cal td[data-day]{cursor:pointer}.cal td[data-day]:hover{background:var(--accent-soft)}
.cal td.picked{background:var(--accent-soft);outline:2px solid var(--accent);outline-offset:-2px}
.cal td.past{background:var(--bg)}
.cal tr.half td{border-top-style:dotted}
.cal .ev{display:block;background:var(--ink);color:var(--panel);border-radius:5px;padding:2px 5px;
margin:1px 0;text-decoration:none;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cal .ev.trial{background:var(--yes)}.cal .ev.own{background:var(--pool)}.cal .ev.done{opacity:.55}
.cal .ev small{opacity:.8;margin-left:4px}
.cal-side{background:var(--panel);border:1px solid var(--line2);border-radius:12px;padding:14px 16px}
.cal-side h2{margin:0 0 6px;font-size:15px}
.cal-side h3{margin:12px 0 4px;font-size:13px;color:var(--mute)}
.cal-side ul{list-style:none;margin:0;padding:0;font-size:13.5px}.cal-side li{margin:3px 0}
.cal-side li span{color:var(--faint);font-size:12.5px}
.cal-side form{display:flex;flex-direction:column;gap:8px;margin-top:12px}
.cal-side select,.cal-side input{width:100%}
.cal.month td{height:96px;vertical-align:top}
.cal.month td.out{background:var(--bg)}.cal.month td.out .num{color:var(--faint)}
.cal.month .num{display:block;font-size:12.5px;color:var(--mute);text-decoration:none;margin:2px 0}
.cal.month td.today{background:var(--accent-soft);box-shadow:inset 0 0 0 2px var(--accent)}
.cal.month td.today .num{display:inline-block;color:var(--panel);background:var(--accent);font-weight:700;
border-radius:50%;width:22px;height:22px;line-height:22px;text-align:center}
.cal td.tcol{background:var(--accent-soft)}
.cal th.today{background:var(--accent);color:var(--panel)}
.gcal{font-size:13px;white-space:nowrap}
@media (max-width:900px){.cal-wrap{grid-template-columns:1fr}.cal-scroll{overflow-x:auto}
.cal{min-width:640px}}
"""

#: A click on an empty slot fills the form's day and time.
JS = """
document.querySelectorAll('.cal td[data-day]').forEach(td=>td.addEventListener('click',ev=>{
  if(ev.target.closest('a'))return;const f=document.getElementById('planf');if(!f)return;
  f.querySelector('[name=on]').value=td.dataset.day;f.querySelector('[name=time]').value=td.dataset.time;
  document.querySelectorAll('.cal td.picked').forEach(x=>x.classList.remove('picked'));
  td.classList.add('picked');const w=f.querySelector('[data-filter]');(w||f.querySelector('select')).focus();}));
document.querySelectorAll('input[data-filter]').forEach(i=>{
  const s=i.form.querySelector('select[name='+i.dataset.filter+']');
  i.addEventListener('input',()=>{const q=i.value.trim().toLowerCase();let first=null;
    [...s.options].forEach(o=>{const ok=!q||o.text.toLowerCase().includes(q);o.hidden=!ok;
      if(ok&&!first)first=o;});
    const cur=s.selectedOptions[0];if(first&&(!cur||cur.hidden))first.selected=true;});});
document.querySelectorAll('#planf [name=other]').forEach(i=>i.addEventListener('input',()=>{
  if(i.value.trim())i.form.querySelector('[name=what]').value='other';}));
"""


@dataclass
class Meeting:
    """One interview or trial day on record, as the week draws it."""

    person: Person
    posting: str
    step: str
    day: date
    time: str
    planned: bool
    #: "with Ana or Ben": said with it when it was planned.
    note: str = ""
    #: For a meeting of your own ("other"): what it is called.
    label: str = ""


@dataclass
class ToFit:
    """Somebody whose next interview is to be planned."""

    person: Person
    posting: str
    candidate_id: str
    step: str
    since: str
    why: str


def gcal(person: Person, role: str, step: str, day: str, hour: str, cfg: Config) -> str:
    """Google Calendar's "new event" page, filled in. Nothing is created until saved there."""
    d = date.fromisoformat(day[:10])
    if hour:
        start = datetime.combine(d, datetime.strptime(hour, "%H:%M").time())
        end = start + (timedelta(hours=8) if step == "trial_day"
                       else timedelta(minutes=cfg.meeting_minutes))
        dates = f"{start:%Y%m%dT%H%M%S}/{end:%Y%m%dT%H%M%S}"
    else:
        dates = f"{d:%Y%m%d}/{d + timedelta(days=1):%Y%m%d}"  # the whole day
    what = stages.name(step, cfg)
    q = {"action": "TEMPLATE", "text": f"{what}: {person.name} ({role})", "dates": dates,
         "ctz": cfg.timezone,
         "details": f"{what} for the {role} position"
                    + (f" at {cfg.company}" if cfg.company else "") + "."}
    if desk.EMAIL.fullmatch(person.email or ""):
        q["add"] = person.email
    return "https://calendar.google.com/calendar/render?" + urlencode(q, quote_via=quote)


def gcal_link(d: Any, app: Any, st: stages.State) -> str:
    """"Add to Google Calendar", for a planned meeting on a row."""
    if not (st.id in stages.MEETINGS and st.planned):
        return ""
    person = desk.load_registry(desk._registry_path()).person_of(app.candidate_id)
    if person is None:
        return ""
    role = desk._titles().get(app.posting_id, app.posting_id)
    url = gcal(person, role, st.id, st.on, st.time, d.cfg)
    return (f'<a class="gcal" href="{e(url)}" target="_blank" rel="noopener noreferrer" '
            f'title="Opens Google Calendar with the event filled in; nothing is saved until '
            f'you save it there">Add to Google Calendar</a>')


def _monday(said: str, now: datetime) -> date:
    try:
        d = date.fromisoformat(said[:10])
    except ValueError:
        d = now.astimezone(timezone.utc).date()
    return d - timedelta(days=d.weekday())


def collect(cfg: Config, now: datetime) -> tuple[list[Meeting], list[ToFit]]:
    """Every meeting on record, and everyone waiting for their next one to be planned."""
    reg, titles = desk.load_registry(desk._registry_path()), desk._titles()
    meetings, fit = [], []
    for posting, pipe in desk._pipelines().items():
        for cid in pipe.candidates:
            s = pipe.standing(cid)
            person = reg.person_of(cid) or Person(cid, cid)
            st = stages.derive(s, cfg, now)
            latest: dict[str, Any] = {}
            for ev in s.events:
                step = stages.step_of(ev)
                if step in stages.MEETINGS:
                    latest[step] = ev
                elif ev.kind == "tagged" and ev.tag == MEETING_TAG:
                    try:
                        day = date.fromisoformat(str(ev.detail.get("on", ""))[:10])
                    except ValueError:
                        continue
                    meetings.append(Meeting(
                        person, posting, "other", day, str(ev.detail.get("time", "")),
                        day > now.astimezone(timezone.utc).date(),
                        str(ev.detail.get("note", "")), str(ev.detail.get("label", ""))))
            for step, ev in latest.items():
                try:
                    day = date.fromisoformat(stages.day_of(ev))
                except ValueError:
                    continue
                meetings.append(Meeting(person, posting, step, day,
                                        str(ev.detail.get("time", "")),
                                        day > now.astimezone(timezone.utc).date(),
                                        str(ev.detail.get("note", ""))))
            if st.ended:
                continue
            nxt = _next_meeting(st, cfg)
            if nxt:
                why = {"replied": "replied OK", "contacted": "no reply yet"}.get(
                    st.id, f"after the {stages.name(st.id, cfg).lower()}")
                fit.append(ToFit(person, posting, cid, nxt, st.on, why))
    # Ready to plan first, the longest waiting first.
    fit.sort(key=lambda t: (t.why == "no reply yet", t.since))
    return meetings, fit


def _next_meeting(st: stages.State, cfg: Config) -> str:
    """The meeting to plan next for this state, or "" when none is."""
    if st.id in ("replied", "contacted"):
        return stages.ROUNDS[0]
    if st.id in stages.ROUNDS and not st.planned:
        return stages.forward(st.id, cfg) or "trial_day"
    return ""


def _day(said: str, now: datetime) -> date:
    """The day the page is about: "2026-10-14", or a month "2026-10" (its first day)."""
    said = (said or "").strip()
    try:
        return date.fromisoformat(said[:10]) if len(said) >= 10 else \
            date.fromisoformat(said[:7] + "-01")
    except ValueError:
        return now.astimezone(timezone.utc).date()


def _shift(d: date, months: int) -> date:
    """The same day, a number of months away (the last day of a shorter month)."""
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    last = (date(y + m // 12, m % 12 + 1, 1) - timedelta(days=1)).day
    return date(y, m, min(d.day, last))


def page(d: Any, viewer: str, said: str, now: datetime, view: str = "month") -> str:
    """A week (by half hours) or a month (by days), with a toolbar like a calendar's."""
    cfg: Config = d.cfg
    view = view if view in ("week", "month") else "month"
    day = _day(said, now)
    today = now.astimezone(timezone.utc).date()
    meetings, fit = collect(cfg, now)
    titles = desk._titles()

    def chip(m: Meeting, short: bool = False) -> str:
        cls = "ev" + (" trial" if m.step == "trial_day" else "") + (
            "" if m.planned or m.day >= today else " done")
        role = titles.get(m.posting, m.posting)
        label = f"{m.time + ' ' if m.time else ''}{m.person.name}"
        what = m.label if m.step == "other" else stages.name(m.step, cfg)
        said = " · ".join(x for x in (what, role, m.note) if x)
        return (f'<a class="{cls}{" own" if m.step == "other" else ""}" '
                f'href="{e(d._person_link(m.person.person_id, viewer))}" title="{e(said)}">'
                f'{e(label)}' + ("" if short else f'<small>{e(what)}</small>')
                + (f'<small>{e(m.note)}</small>' if m.note and not short else "") + "</a>")

    if view == "month":
        grid, title, step = _month(day, today, meetings, chip, d, viewer), \
            f"{day.strftime('%B')} {day.year}", "month"
        prev_d, next_d = _shift(day, -1), _shift(day, 1)
    else:
        monday = day - timedelta(days=day.weekday())
        grid = _week(monday, today, meetings, chip)
        end = monday + timedelta(days=6)
        title = (f"{monday.day} – {end.day} {end.strftime('%B')} {end.year}"
                 if monday.month == end.month else
                 f"{monday.day} {monday.strftime('%b')} – {end.day} {end.strftime('%b')} "
                 f"{end.year}")
        prev_d, next_d = monday - timedelta(days=7), monday + timedelta(days=7)
        step = "week"
    q = d._q(viewer, "&")

    def href(x: date, v: str = view) -> str:
        return f"/calendar?view={v}&date={x.isoformat()}{q}"

    who = (f'<input type="hidden" name="as" value="{e(viewer)}">'
           if d.access.mode == "pick" else "")
    jump = (f'<form method="get" action="/calendar" class="cal-jump">{who}'
            f'<input type="hidden" name="view" value="{view}">'
            f'<input type="month" name="date" value="{day.strftime("%Y-%m")}" data-submit '
            f'aria-label="Go to a month"><noscript><button>Go</button></noscript></form>')
    seg = "".join(f'<a class="{"on" if v == view else ""}" href="{e(href(day, v))}">{w}</a>'
                  for v, w in (("week", "Week"), ("month", "Month")))
    bar = (f'<div class="cal-bar"><a class="cal-btn" href="{e(href(today))}#today">Today</a>'
           f'<a class="cal-btn" href="{e(href(prev_d))}" title="Previous {step}" '
           f'aria-label="Previous {step}">&#x2039;</a>'
           f'<a class="cal-btn" href="{e(href(next_d))}" title="Next {step}" '
           f'aria-label="Next {step}">&#x203A;</a>'
           f'<h1>{e(title)}</h1>{jump}<span class="cal-seg">{seg}</span></div>')
    back = f"/calendar?view={view}&date={day.isoformat()}"
    return (f'{bar}<div class="cal-wrap">{grid}{_side(d, viewer, fit, back, titles)}</div>'
            f'<p class="sub">Click a free slot (or a day, by month) to fill in the form, '
            f'choose who, and put it in: the interview is planned on their row, with its '
            f'invitation ready. Nothing is sent, and no calendar is read or written.</p>')


def _week(monday: date, today: date, meetings: list[Meeting], chip: Any) -> str:
    days = [monday + timedelta(days=i) for i in range(7)]
    week_m = [m for m in meetings if monday <= m.day < monday + timedelta(days=7)]
    shown = [x for x in days if x.weekday() < 5 or any(m.day == x for m in week_m)]
    slots = [f"{h:02d}:{m:02d}" for h in range(FIRST_HOUR, LAST_HOUR) for m in (0, 30)]

    def slot_of(t: str) -> str:
        h, m = int(t[:2]), int(t[3:5])
        if h < FIRST_HOUR:
            return slots[0]
        if h >= LAST_HOUR:
            return slots[-1]
        return f"{h:02d}:{0 if m < 30 else 30:02d}"

    head = "".join(('<th class="today" id="today">' if x == today else "<th>")
                   + f'{x.strftime("%a")} {x.day}</th>' for x in shown)
    rows = []
    allday = [[m for m in week_m if m.day == x and not m.time] for x in shown]
    if any(allday):
        rows.append('<tr><td class="hr">all day</td>' + "".join(
            f'<td>{"".join(chip(m) for m in ms)}</td>' for ms in allday) + "</tr>")
    for t in slots:
        cells = []
        for x in shown:
            here = "".join(chip(m) for m in week_m if m.day == x and m.time
                           and slot_of(m.time) == t)
            # A slot in the past is not offered: an interview is planned ahead.
            col = ' class="tcol"' if x == today else ""
            cells.append(f'<td{col} data-day="{x.isoformat()}" data-time="{t}">{here}</td>'
                         if x >= today else f'<td class="past">{here}</td>')
        rows.append(f'<tr class="{"half" if t.endswith("30") else ""}"><td class="hr">'
                    f'{t if t.endswith("00") else ""}</td>{"".join(cells)}</tr>')
    return (f'<div class="cal-scroll"><table class="cal"><thead><tr><th class="hr"></th>{head}'
            f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


def _month(day: date, today: date, meetings: list[Meeting], chip: Any, d: Any,
           viewer: str) -> str:
    first = day.replace(day=1)
    start = first - timedelta(days=first.weekday())
    nxt = _shift(first, 1)
    q = d._q(viewer, "&")
    weeks = []
    x = start
    while x < nxt or x.weekday() != 0:
        cells = []
        for _ in range(7):
            here = sorted((m for m in meetings if m.day == x), key=lambda m: m.time or "")
            cls = " ".join(c for c in ("out" if x.month != first.month else "",
                                       "today" if x == today else "",
                                       "past" if x < today else "") if c)
            num = (f'<a class="num" href="/calendar?view=week&date={x.isoformat()}{q}" '
                   f'title="This week">{x.day}</a>')
            body = "".join(chip(m, short=True) for m in here)
            attrs = f' data-day="{x.isoformat()}" data-time=""' if x >= today else ""
            here_id = ' id="today"' if x == today else ""
            cells.append(f'<td class="{cls}"{here_id}{attrs}>{num}{body}</td>')
            x += timedelta(days=1)
        weeks.append(f'<tr>{"".join(cells)}</tr>')
    head = "".join(f"<th>{w}</th>" for w in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"))
    return (f'<div class="cal-scroll"><table class="cal month"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(weeks)}</tbody></table></div>')


def _others(cfg: Config, fit: list[ToFit], now: datetime) -> list[ToFit]:
    """Everyone else on Interested: for a meeting of your own, outside the steps."""
    from console import track_web
    reg = desk.load_registry(desk._registry_path())
    seen = {(t.posting, t.candidate_id) for t in fit}
    out = []
    for posting, pipe in desk._pipelines().items():
        for cid in pipe.candidates:
            if (posting, cid) in seen:
                continue
            st = stages.derive(pipe.standing(cid), cfg, now)
            if track_web.followed(st):
                out.append(ToFit(reg.person_of(cid) or Person(cid, cid), posting, cid, "",
                                 st.on, stages.name(st.id, cfg).lower()))
    return out


def _side(d: Any, viewer: str, fit: list[ToFit], back: str,
          titles: dict[str, str]) -> str:
    """"To fit in", then the form: who (typed to filter), which meeting, day, time, a note."""
    cfg: Config = d.cfg
    others = _others(cfg, fit, datetime.now(timezone.utc))
    groups: dict[str, list[ToFit]] = {}
    for t in fit:
        groups.setdefault("Waiting for their reply" if t.why == "no reply yet"
                          else "Ready to plan", []).append(t)
    lists = "".join(
        f'<h3>{e(g)}</h3><ul>' + "".join(
            f'<li><a href="{e(d._person_link(t.person.person_id, viewer))}">{e(t.person.name)}'
            f'</a> <span>{e(stages.name(t.step, cfg))} · {e(t.why)}</span></li>' for t in ts)
        + "</ul>" for g, ts in sorted(groups.items(), key=lambda kv: kv[0] != "Ready to plan"))
    if not fit:
        lists = ('<p class="sub">Nobody is waiting for an interview to be planned. People who '
                 'reply OK, or go on after an interview, come here.</p>')
    everyone = sorted(fit + others, key=lambda t: t.person.name.lower())
    if not everyone:
        return f'<div class="cal-side"><h2>To fit in</h2>{lists}</div>'
    opts = "".join(
        f'<option value="{e(t.posting)}/{e(t.candidate_id)}">{e(t.person.name)} · '
        f'{e(titles.get(t.posting, t.posting))}'
        + (f' · next: {e(stages.name(t.step, cfg).lower())}' if t.step else "") + "</option>"
        for t in everyone)
    kinds = ([("next", "Their next step")]
             + [(r, stages.name(r, cfg)) for r in stages.rounds(cfg)]
             + [("trial_day", stages.name("trial_day", cfg)), ("other", "Something else…")])
    what = "".join(f'<option value="{k}">{e(w)}</option>' for k, w in kinds)
    form = (f'<form method="post" action="/plan" id="planf"><input type="hidden" name="t" '
            f'value="{d.token}"><input type="hidden" name="as" value="{e(viewer)}">'
            f'<input type="hidden" name="back" value="{e(back)}">'
            f'<label class="f">Who</label>'
            f'<input type="search" data-filter="who" placeholder="Type a first name" '
            f'aria-label="Filter by name">'
            f'<select name="who" size="6">{opts}</select>'
            f'<label class="f">Which meeting</label><select name="what">{what}</select>'
            f'<input type="text" name="other" maxlength="80" '
            f'placeholder="Or write it: call with the CTO, coffee…">'
            f'<label class="f">Day</label><input type="date" name="on" required>'
            f'<label class="f">Time</label><input type="time" name="time">'
            f'<label class="f">Note</label><input type="text" name="note" maxlength="140" '
            f'placeholder="Who will be there: Ana or Ben">'
            f'<button class="go">Put it in</button></form>')
    return f'<div class="cal-side"><h2>To fit in</h2>{lists}{form}</div>'


def plan(f: dict[str, str], viewer: str, cfg: Config) -> str:
    """The form: plan a meeting for the person chosen, as their row would.

    "Their next step" is what the row would plan; an interview or the trial
    day is planned if the steps allow it from where they are; "something
    else" is a meeting of your own, on the calendar and the person's history,
    which moves nothing.
    """
    posting, _, cid = f.get("who", "").partition("/")
    pipe = desk._pipelines().get(posting)
    if pipe is None or cid not in pipe.candidates:
        raise DeskError("choose who: type a first name, then pick them")
    on = f.get("on", "").strip()
    if not on:
        raise DeskError("choose a day: click a slot of the week, or type it")
    hour, note = f.get("time", "").strip(), f.get("note", "").strip()
    what = f.get("what", "next")
    other = " ".join(f.get("other", "").split())
    person = desk.load_registry(desk._registry_path()).person_of(cid)
    who = person.name if person else cid
    at = f" at {hour}" if hour else ""
    if what == "other" or (other and what == "next"):
        if not other:
            raise DeskError("write what the meeting is, in the box under “Which meeting”")
        _own_meeting(pipe, posting, cid, viewer, cfg, other, on, hour, note)
        return f"{who}: {other} on {stages.short(on)}{at}"
    st = stages.derive(pipe.standing(cid), cfg)
    step = _next_meeting(st, cfg) if what == "next" else what
    if not step or step not in stages.MEETINGS:
        raise DeskError(f"nothing to plan next: this application is "
                        f"{stages.name(st.id, cfg)}. Choose a meeting, or write your own")
    stages.advance_now(posting, cid, step, viewer, cfg, on=on, hour=hour, note=note,
                       expect=st.id)
    return (f"{who}: {stages.name(step, cfg).lower()} on {stages.short(on)}{at}, "
            f"the invitation is ready on their row")


def _own_meeting(pipe: Any, posting: str, cid: str, by: str, cfg: Config, label: str,
                 on: str, hour: str, note: str) -> None:
    from pipeline import Event
    day = stages._date(on, "the meeting").isoformat()
    detail = {"label": stages._line(label), "on": day}
    if hour:
        detail["time"] = stages._time(hour)
    if note:
        detail["note"] = stages._line(note)
    with desk.locked():
        pipe = desk._pipelines()[posting]
        pipe.add(Event(candidate_id=cid, kind="tagged", tag=MEETING_TAG, by=cfg.voter(by),
                       at=datetime.now(timezone.utc).isoformat(), reason=label,
                       detail=detail))
        desk._save(pipe)
