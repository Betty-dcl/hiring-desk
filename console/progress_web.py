"""Progress: how far each person you said yes to has gone, and where it stopped.

At the top, the way through with its counts and arrows:

    12 to contact → 10 contacted → 7 replied → 5 first interview → 2 second → 1 trial day

with, under each step, how many left the process there. Below, one line per
person: a dot for every step reached (with its day), a hollow one for a
meeting planned, and a cross where they left, with why. Read from the same
log and the same states as every other page; nothing here can be changed,
only looked at. A click on a name opens the person.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import quote

import desk
import stages
from desk import Config, Person

e = html.escape

CSS = """
.pg-funnel{display:flex;align-items:stretch;gap:0;margin:6px 0 22px;overflow-x:auto}
.pg-step{background:var(--panel);border:1px solid var(--line2);border-radius:12px;padding:10px 6px;
flex:1 1 0;min-width:76px;text-align:center}
.pg-step b{display:block;font-size:26px;font-weight:650;color:var(--ink);font-variant-numeric:tabular-nums}
.pg-step span{font-size:12.5px;color:var(--mute)}
.pg-step small{display:block;font-size:12px;color:var(--no);margin-top:4px;min-height:15px}
.pg-arrow{align-self:center;color:var(--faint);font-size:18px;padding:0 3px;flex:0 0 auto}
.pg-tools{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:10px}
.pg-tools a{font-size:13.5px;text-decoration:none;color:var(--mute);padding:4px 10px;
border:1px solid var(--line);border-radius:var(--radius);background:var(--panel)}
.pg-tools a.on{background:var(--ink);color:var(--panel);border-color:var(--ink)}
.pg-scroll{overflow-x:auto}
.pg{display:grid;gap:0;background:var(--panel);border:1px solid var(--line2);border-radius:12px;
padding:8px 0;min-width:720px}
.pg>div{padding:6px 4px;border-top:1px solid var(--line2);font-size:13px;display:flex;
flex-direction:column;align-items:center;justify-content:center;position:relative}
.pg>.pg-h{border-top:none;font-size:12px;color:var(--mute);text-align:center}
.pg>.pg-name{align-items:flex-start;padding-left:14px}
.pg-name a{font-weight:600;color:var(--ink);text-decoration:none}.pg-name a:hover{text-decoration:underline}
.pg-name span{font-size:12px;color:var(--faint)}
.pg>.pg-where{align-items:flex-start;font-size:12.5px;color:var(--mute);padding-right:12px}
.pg-where b{color:var(--ink);font-weight:600}.pg-where .out{color:var(--no);font-weight:600}
.pg-cell::before{content:"";position:absolute;left:0;right:0;top:50%;border-top:2px dashed var(--line)}
.pg-cell.reached::before{border-top:3px solid var(--yes)}
.pg-cell.first::before{left:50%}.pg-cell.last::before{right:50%}
.pg-cell.cut::before{right:50%}
.pg-dot{position:relative;width:14px;height:14px;border-radius:50%;background:var(--panel);
border:2px solid var(--line);z-index:1}
.pg-cell.reached .pg-dot{background:var(--yes);border-color:var(--yes)}
.pg-cell.now .pg-dot{background:var(--accent);border-color:var(--accent);box-shadow:0 0 0 4px var(--accent-soft)}
.pg-cell.planned .pg-dot{background:var(--panel);border-color:var(--accent)}
.pg-cell.out .pg-dot{background:var(--no);border-color:var(--no);border-radius:3px;
transform:rotate(45deg)}
.pg-cell small{position:relative;z-index:1;font-size:11px;color:var(--faint);margin-top:3px;
background:var(--panel);padding:0 3px}
"""


@dataclass
class Line:
    """One person's way through, as the page draws it."""

    person: Person
    posting: str
    marks: dict[str, stages.Mark]
    state: stages.State
    furthest: int
    #: Why they left, when they did ("Not for us", "Withdrew", "In the pool"), else "".
    out: str = ""
    planned: list[str] = field(default_factory=list)


def steps(cfg: Config) -> list[str]:
    return ["to_contact", "contacted", "replied", *stages.rounds(cfg), "trial_day"]


def _out(st: stages.State, cfg: Config) -> str:
    if st.id in ("to_answer_no", "answered_no", "closed"):
        return cfg.label("pass")
    if st.id == "withdrew":
        return "Withdrew"
    if st.id == "talk_later":
        return cfg.label("later")
    return ""


def lines(cfg: Config, now: datetime, role: str = "") -> list[Line]:
    """Everyone who got a yes, with how far they went."""
    way = steps(cfg)
    reg = desk.load_registry(desk._registry_path())
    out = []
    for posting, pipe in desk._pipelines().items():
        if role and posting != role:
            continue
        for cid in pipe.candidates:
            s = pipe.standing(cid)
            marks, st = stages.journey(s, cfg, now)
            got = {m.id: m for m in marks if m.id in way and m.status != "ahead"}
            if "to_contact" not in got and st.id != "to_contact":
                continue  # never said yes to: not on this page
            if st.id == "to_contact" and "to_contact" not in got:
                got["to_contact"] = stages.Mark("to_contact", st.on, st.by, "now")
            furthest = max(way.index(k) for k in got)
            done_trial = st.id == "trial_day" and not st.planned
            out.append(Line(reg.person_of(cid) or Person(cid, cid), posting, got, st, furthest,
                            "Trial day done" if done_trial else _out(st, cfg)))
    # Still going first, the furthest first; then those who left, the furthest first.
    out.sort(key=lambda x: (bool(x.out), -x.furthest, x.person.name.lower()))
    return out


def page(d: Any, viewer: str, now: datetime, role: str = "", show: str = "all") -> str:
    """The funnel, the filters, then a line per person."""
    cfg: Config = d.cfg
    way = steps(cfg)
    titles = desk._titles()
    every = lines(cfg, now, role)
    show = show if show in ("all", "going", "out") else "all"
    shown = [x for x in every if show == "all" or (show == "going") == (not x.out)]

    # The funnel: how many reached each step, and how many left right there.
    reached = [sum(1 for x in every if x.furthest >= i) for i in range(len(way))]
    left = [sum(1 for x in every if x.out and x.out != "Trial day done" and x.furthest == i)
            for i in range(len(way))]
    boxes = []
    for i, k in enumerate(way):
        if i:
            boxes.append('<span class="pg-arrow">&rarr;</span>')
        boxes.append(f'<div class="pg-step"><b>{reached[i]}</b><span>'
                     f'{e(_short(k, cfg))}</span><small>'
                     f'{f"{left[i]} left here" if left[i] else ""}</small></div>')

    q = d._q(viewer, "&")
    base = "/progress?" + (f"role={quote(role)}&" if role else "")
    tools = "".join(
        f'<a class="{"on" if v == show else ""}" href="{base}show={v}{q}">{w}</a>'
        for v, w in (("all", f"Everyone ({len(every)})"),
                     ("going", f"Still going ({sum(1 for x in every if not x.out)})"),
                     ("out", f"Left ({sum(1 for x in every if x.out)})")))
    roles = "".join(f'<option value="{e(pid)}"{" selected" if pid == role else ""}>'
                    f'{e(t or pid)}</option>' for pid, t in sorted(titles.items(),
                                                                   key=lambda kv: kv[1]))
    who = (f'<input type="hidden" name="as" value="{e(viewer)}">'
           if d.access.mode == "pick" else "")
    pick = (f'<form method="get" action="/progress" class="rolef">{who}'
            f'<input type="hidden" name="show" value="{show}"><label>Role <select name="role" '
            f'data-submit><option value="">All roles</option>{roles}</select></label>'
            f'<noscript><button>Show</button></noscript></form>')

    head =['<div class="pg-h"></div>'] + [
        f'<div class="pg-h">{e(_short(k, cfg))}</div>' for k in way] + ['<div class="pg-h"></div>']
    rows = []
    for x in shown:
        rows.append(f'<div class="pg-name"><a href="{e(d._person_link(x.person.person_id, viewer))}">'
                    f'{e(x.person.name)}</a><span>{e(titles.get(x.posting, x.posting))}</span></div>')
        for i, k in enumerate(way):
            rows.append(_cell(x, k, i, len(way)))
        rows.append(f'<div class="pg-where">{_where(x, cfg)}</div>')
    n = len(way)
    body = (f'<div class="pg-scroll"><div class="pg n{n}">{"".join(head + rows)}</div></div>'
            if shown else '<div class="list"><div class="empty"><b>Nobody here yet.</b>'
            'People you say yes to appear here, with each step they reach.</div></div>')
    return (f'<h1>Progress</h1><p class="lead">Everyone you said yes to: how far each one has '
            f'gone, and where it stopped. A green dot is a step done, an orange one where '
            f'they are now (hollow: planned), a red one where they left.</p>'
            f'<div class="pg-funnel">{"".join(boxes)}</div>'
            f'<div class="pg-tools">{tools}{pick}</div>{body}')


def _short(k: str, cfg: Config) -> str:
    return {"to_contact": "Said yes", "replied": "Replied OK"}.get(k, stages.name(k, cfg))


def _where(x: Line, cfg: Config) -> str:
    if x.out == "Trial day done":
        return "<b>Trial day done</b>"
    if x.out:
        last = steps(cfg)[x.furthest]
        return f'<span class="out">{e(x.out)}</span> after {e(_short(last, cfg).lower())}'
    st = x.state
    if st.id in stages.MEETINGS and st.planned:
        return (f'<b>{e(stages.name(st.id, cfg))}</b> on {e(stages.short(st.on))}'
                + (f" at {e(st.time)}" if st.time else ""))
    return f'<b>{e(_short(st.id, cfg))}</b>' + (f" since {e(stages.short(st.on))}"
                                                if st.on else "")


def _cell(x: Line, k: str, i: int, n: int) -> str:
    """One step on one person's line: reached, where they are, planned, or where they left."""
    m = x.marks.get(k)
    cls = ["pg-cell"] + (["first"] if i == 0 else []) + (["last"] if i == n - 1 else [])
    gone = bool(x.out) and x.out != "Trial day done"
    if i < x.furthest:
        cls.append("reached")
    elif i == x.furthest:
        # The line stops at their dot: where they are, or where they left.
        cls += ["reached", "cut",
                "out" if gone else "planned" if m is not None and m.status == "planned"
                else "now" if not x.out else ""]
    date = stages.short(m.on) if m is not None and m.on else ""
    return (f'<div class="{" ".join(c for c in cls if c)}"><span class="pg-dot"></span>'
            f'<small>{e(date)}</small></div>')


#: The grid's columns depend on the team's number of interviews (one to six):
#: one layout per number, in the stylesheet, since the page sets no style itself.
CSS += "".join(
    f".pg.n{n}{{grid-template-columns:minmax(170px,1.3fr) repeat({n},minmax(64px,1fr)) "
    f"minmax(150px,1.2fr)}}" for n in range(5, 11))
