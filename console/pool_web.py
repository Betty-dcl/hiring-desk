"""The pool: people you liked and could not take now, across every role.

A good profile at the wrong time, or someone who did not get through a later
step: the one list on the desk that is worth more as time goes by. When a new
role opens, this is where to look first.

One card per person: the role they applied to, when they were kept and how
far they had gone, why (the comment on the decision, the latest note), their
documents, and one button to take them up again: back to Ready to contact,
on Interested, as after a yes. Whether they were told they are kept (and
agreed to it: their data is kept) is said on the card, never assumed.

The people put under one of the team's own choices have that choice's tab;
they are not here.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote

import desk
import stages
from console import choices_web
from desk import Application, Config, DeskError, Person
from pipeline import Standing

e = html.escape

CSS = """
.pool-head{display:flex;gap:12px;align-items:baseline;flex-wrap:wrap}
.pool-head h1{margin:0}
.pool-tools{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:14px 0 6px}
.pool-tools input[type=search]{min-width:260px}
.pool-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px;
margin-top:12px}
.pool-card{background:var(--panel);border:1px solid var(--line2);border-radius:12px;padding:16px 18px;
display:flex;flex-direction:column;gap:8px}
.pool-card .who a{font-weight:600;font-size:17px;color:var(--ink);text-decoration:none}
.pool-card .who a:hover{text-decoration:underline}
.pool-card .role{font-size:13.5px;color:var(--mute);margin-left:0}
.pool-card .since{font-size:12.5px;color:var(--faint)}
.pool-card .why{font-size:14px;color:var(--ink);border-left:2px solid var(--pool);padding-left:10px}
.pool-card .why i{color:var(--faint);font-style:normal}
.pool-card .links{margin-top:0}
.pool-card .told{font-size:12.5px;color:var(--mute)}
.pool-card .idea{font-size:13px;color:var(--mute);background:var(--accent-soft);border-radius:8px;
padding:6px 9px}
.pool-card .idea b{color:var(--ink);font-weight:600}
.pool-for{font-size:14px;color:var(--mute);max-width:760px;margin:8px 0 0}
.pool-for b{color:var(--ink)}
.pool-card .told.no{color:var(--amber)}
.pool-card form{margin-top:auto;display:flex;gap:8px;align-items:center}
.pool-card form button{white-space:nowrap}
.pool-card .told a{color:inherit}
"""


@dataclass
class Kept:
    person: Person
    app: Application
    s: Standing
    st: stages.State
    #: The furthest step reached before the pool ("" when straight from sorting).
    after: str
    why: str
    told: bool
    #: Another role their papers fit, as an idea only: (posting, AI guess), or None.
    idea: tuple[str, float] | None = None
    #: Their AI guess for the role asked about on "Ideas for a role", if one is.
    fit_for: float | None = None


#: An idea is shown only from this AI guess up, and only when it is about as high
#: as the one for the role they applied to (or higher): a hint worth a look,
#: never a ranking of people.
IDEA_MIN, IDEA_SLACK = 0.5, 0.05


def ideas(person: Person, applied: str, titles: dict[str, str]) -> list[tuple[str, float]]:
    """Other roles their CV and answer read well against, best first. Speculative."""
    import match
    own = match.of(person, applied)
    floor = max(IDEA_MIN, (own.total - IDEA_SLACK) if own is not None else 0.0)
    out = []
    for pid in titles:
        if pid == applied:
            continue
        m = match.of(person, pid)
        if m is not None and m.total >= floor:
            out.append((pid, m.total))
    return sorted(out, key=lambda x: -x[1])


def kept(cfg: Config, viewer: str, now: datetime, role: str = "", q: str = "") -> list[Kept]:
    """Everyone in the pool, newest first; narrowed to a role and to some words."""
    reg = desk.load_registry(desk._registry_path())
    titles = desk._titles()
    q = q.strip().lower()
    out = []
    for posting, pipe in desk._pipelines().items():
        if role and posting != role:
            continue
        for cid in pipe.candidates:
            s = pipe.standing(cid)
            st = stages.derive(s, cfg, now)
            if st.id != "talk_later" or choices_web.own_of(s, viewer, cfg):
                continue
            person = reg.person_of(cid) or Person(cid, cid)
            marks, _ = stages.journey(s, cfg, now)
            reached = [m.id for m in marks if m.status == "done"
                       and m.id in ("contacted", "replied") + stages.MEETINGS]
            after = reached[-1] if reached else ""
            v = desk.votes(s).get(viewer)
            notes = [str(ev.detail.get("text", "")) for ev in s.events if ev.kind == "noted"]
            why = (v.comment if v is not None and v.comment else "") or (notes[-1] if notes
                                                                          else "")
            told = any(ev.kind == "contacted" and ev.detail.get("about") == "later"
                       for ev in s.events)
            if q:
                hay = " ".join([person.name, person.email or "", titles.get(posting, posting),
                                why, *notes]).lower()
                if q not in hay:
                    continue
            idea = ideas(person, posting, titles)
            out.append(Kept(person, Application(posting, cid), s, st, after, why, told,
                            idea[0] if idea else None))
    out.sort(key=lambda k: k.st.on, reverse=True)
    return out


def page(d: Any, viewer: str, now: datetime, role: str = "", q: str = "",
         for_role: str = "") -> str:
    """The pool; or, with a role asked about, the pool read against that role."""
    import match
    cfg: Config = d.cfg
    titles = desk._titles()
    every = kept(cfg, viewer, now)
    for_role = for_role if for_role in titles else ""
    if for_role:
        # Ideas for one role: everyone in the pool, whatever they applied to,
        # by their AI guess for it. A hint for a first look, nothing more.
        shown = kept(cfg, viewer, now, "", q)
        for k in shown:
            m = match.of(k.person, for_role)
            k.fit_for = m.total if m is not None else None
        shown.sort(key=lambda k: -(k.fit_for if k.fit_for is not None else -1))
    else:
        shown = kept(cfg, viewer, now, role, q)
    counts: dict[str, int] = {}
    for k in every:
        counts[k.app.posting_id] = counts.get(k.app.posting_id, 0) + 1
    who = (f'<input type="hidden" name="as" value="{e(viewer)}">'
           if d.access.mode == "pick" else "")
    roles = "".join(f'<option value="{e(pid)}"{" selected" if pid == role else ""}>'
                    f'{e(t or pid)} ({counts.get(pid, 0)})</option>'
                    for pid, t in sorted(titles.items(), key=lambda kv: kv[1]))
    tools = (f'<form method="get" action="/pool" class="pool-tools">{who}'
             f'<label class="sub">Role <select name="role" data-submit><option value="">'
             f'All roles ({len(every)})</option>{roles}</select></label>'
             f'<label class="sub">Ideas for a role <select name="for" data-submit>'
             f'<option value="">none</option>'
             + "".join(f'<option value="{e(pid)}"{" selected" if pid == for_role else ""}>'
                       f'{e(t or pid)}</option>'
                       for pid, t in sorted(titles.items(), key=lambda kv: kv[1]))
             + f'</select></label>'
             f'<input type="search" name="q" value="{e(q)}" placeholder="Search a name, a role, a comment"'
             f' aria-label="Search the pool">'
             f'<noscript><button>Show</button></noscript></form>')
    back = "/pool" + (f"?role={quote(role)}" if role else "") + \
        (("&" if role else "?") + f"q={quote(q)}" if q else "")
    cards = "".join(_card(d, k, viewer, titles, back, for_role) for k in shown)
    asked = (f'<p class="pool-for">Ideas for <b>{e(titles[for_role])}</b>: everyone in the pool, '
             f'whatever they applied to, by their AI guess for this role. Speculative: a '
             f'reason to open a CV, never a reason to write to someone without reading it. '
             f'Nobody is moved.</p>' if for_role else "")
    body = (f'<div class="pool-cards">{cards}</div>' if shown else
            '<div class="list"><div class="empty"><b>' +
            ("Nobody matches." if (role or q) else "Nobody in the pool yet.") +
            f'</b>“{e(cfg.label("later"))}” on an application puts the person here, from any '
            f'role and at any step.</div></div>')
    return (f'<div class="pool-head"><h1>Pool</h1><span class="sub">{len(every)} '
            f'{"person" if len(every) == 1 else "people"}</span></div>'
            f'<p class="lead">People you liked and could not take now: a good profile at the '
            f'wrong time, or someone who did not get through a later step. Every role, newest '
            f'first. When a role opens, look here first.</p>{tools}{asked}{body}')


def _card(d: Any, k: Kept, viewer: str, titles: dict[str, str], back: str,
          for_role: str = "") -> str:
    from console import list_web
    cfg: Config = d.cfg
    link = d._person_link(k.person.person_id, viewer)
    after = (f" after the {stages.name(k.after, cfg).lower()}" if k.after else "")
    why = f'<div class="why">{e(k.why)}</div>' if k.why else ""
    idea = ""
    if for_role:
        idea = (f'<div class="idea">For {e(titles[for_role])}: AI guess '
                f'<b>{k.fit_for:.0%}</b></div>' if k.fit_for is not None else
                '<div class="idea">Nothing on file to read against this role.</div>')
    elif k.idea:
        pid, total = k.idea
        idea = (f'<div class="idea">An idea: could also fit <b>{e(titles.get(pid, pid))}</b> '
                f'(AI guess {total:.0%}, speculative). Mention it if you write to them.</div>')
    told = ('<div class="told">Told, and asked if they agree to be kept.</div>' if k.told else
            f'<div class="told no">Not told yet. <a href="{e(link)}">Write the “not now” '
            f'mail</a></div>')
    return (f'<div class="pool-card"><div class="who"><a href="{e(link)}">{e(k.person.name)}</a>'
            f'</div><div class="role">{e(titles.get(k.app.posting_id, k.app.posting_id))}</div>'
            f'<div class="since">Kept since {e(stages.short(k.st.on))}{e(after)}</div>{why}'
            f'{idea}'
            f'{list_web.links(d, k.person, k.app, viewer)}{told}'
            f'<form method="post" action="/pool/takeup"><input type="hidden" name="t" '
            f'value="{d.token}"><input type="hidden" name="as" value="{e(viewer)}">'
            f'<input type="hidden" name="posting" value="{e(k.app.posting_id)}">'
            f'<input type="hidden" name="candidate" value="{e(k.app.candidate_id)}">'
            f'<input type="hidden" name="back" value="{e(back)}">'
            f'<button class="go" title="Back to {e(stages.name("to_contact", cfg))}, on '
            f'Interested">Take them up</button></form></div>')


def takeup(f: dict[str, str], viewer: str, cfg: Config) -> str:
    """Back to Ready to contact: their yes, and the step out of the pool if one holds them."""
    posting, cid = f.get("posting", ""), f.get("candidate", "")
    pipe = desk._pipelines().get(posting)
    if pipe is None or cid not in pipe.candidates:
        raise DeskError("this application is not on the desk any more")
    choices_web.record(posting, cid, viewer, "contact", cfg)
    st = stages.derive(desk._pipelines()[posting].standing(cid), cfg)
    if st.id == "talk_later":
        stages.advance_now(posting, cid, "takeup", viewer, cfg, expect="talk_later")
    person = desk.load_registry(desk._registry_path()).person_of(cid)
    return (f"{person.name if person else cid}: taken up again, "
            f"{stages.name('to_contact', cfg).lower()} on Interested")
