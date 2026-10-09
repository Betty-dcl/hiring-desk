"""Erasing on the page: at the bottom of a person's page, and the Retention page.

Both ask twice: a reason, and a box that says it cannot be undone. The rules
and what is removed are erase.py's; this file only draws them.
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any

import erase
from desk import Config, DeskError, Person

e = html.escape

CSS = """
.erase{margin-top:28px;border:1px solid var(--line);border-radius:12px;background:var(--panel)}
.erase>summary{cursor:pointer;padding:12px 16px;font-size:14px;color:var(--no)}
.erase form{padding:0 16px 16px;display:flex;flex-direction:column;gap:10px;max-width:640px}
.erase label{font-size:14px;color:var(--ink);display:flex;gap:8px;align-items:flex-start}
.erase .sub{font-size:13px;color:var(--mute)}
.erase button,.ret button.danger{background:var(--no);color:var(--panel);border-color:var(--no)}
.ret table{border-collapse:collapse;width:100%;max-width:980px;background:var(--panel);
border:1px solid var(--line2);border-radius:12px;font-size:14px}
.ret td,.ret th{padding:9px 12px;border-top:1px solid var(--line2);text-align:left}
.ret thead th{border-top:none;font-size:12.5px;color:var(--mute);font-weight:600}
.ret form{display:flex;flex-direction:column;gap:12px}
.ret .confirm{display:flex;gap:8px;align-items:center;font-size:14px}
"""


def _hidden(d: Any, viewer: str, back: str) -> str:
    return (f'<input type="hidden" name="t" value="{d.token}">'
            f'<input type="hidden" name="as" value="{e(viewer)}">'
            f'<input type="hidden" name="back" value="{e(back)}">')


def _reasons(selected: str = "asked") -> str:
    return "".join(f'<option value="{k}"{" selected" if k == selected else ""}>{e(v)}</option>'
                   for k, v in erase.REASONS.items())


def section(d: Any, person: Person, viewer: str) -> str:
    """At the bottom of a person's page, folded."""
    n = len(person.applications)
    return (f'<details class="erase"><summary>Erase this person</summary>'
            f'<form method="post" action="/erase">{_hidden(d, viewer, "/")}'
            f'<input type="hidden" name="person" value="{e(person.person_id)}">'
            f'<p class="sub">Everything about {e(person.name)} is deleted: '
            f'{n} application{"s" if n != 1 else ""}, every step and note, their CV and '
            f'answers, and their link to Ashby, which will not bring them back. Only a line '
            f'saying that an erasure was made (when, by whom, why) is kept, without their name. '
            f'Backups made before today still hold them until they roll over.</p>'
            f'<label>Why <select name="reason">{_reasons()}</select></label>'
            f'<label><input type="checkbox" name="sure" value="1" required> I understand: this '
            f'cannot be undone.</label>'
            f'<button>Erase {e(person.name)}</button></form></details>')


def page(d: Any, viewer: str, now: datetime) -> str:
    """Everyone past the retention period, ticked by a person, erased together."""
    from console.setup_web import _tools
    cfg: Config = d.cfg
    months, pool = erase.retention()
    rows = erase.due(cfg, now)
    lead = (f'<p class="lead">Applications are kept {months} months after the last thing '
            f'that happened to them ({pool} months in the pool, where people agreed to be '
            f'kept); someone in process is never listed. Nothing is erased on its own: tick '
            f'the people, then erase them. The length is in desk.toml [retention].</p>')
    if not rows:
        return (f'<h1>House rules</h1>{_tools(d, viewer, "retention")}{lead}'
                f'<div class="list"><div class="empty"><b>Nobody is past the retention '
                f'period.</b></div></div>')
    body = "".join(
        f'<tr><td><input type="checkbox" name="pick" value="{e(r.person.person_id)}" '
        f'checked aria-label="Erase {e(r.person.name)}"></td>'
        f'<td><a href="{e(d._person_link(r.person.person_id, viewer))}">{e(r.person.name)}</a>'
        f'</td><td>{r.last.date().isoformat()}</td><td>{e(r.where)}</td></tr>' for r in rows)
    return (f'<h1>House rules</h1>{_tools(d, viewer, "retention")}{lead}'
            f'<div class="ret"><form method="post" action="/erase/batch">'
            f'{_hidden(d, viewer, "/retention")}<input type="hidden" name="reason" '
            f'value="retention"><table><thead><tr><th></th><th>Who</th><th>Last activity</th>'
            f'<th>Where</th></tr></thead><tbody>{body}</tbody></table>'
            f'<label class="confirm"><input type="checkbox" name="sure" value="1" required> '
            f'I understand: the ticked people are erased for good.</label>'
            f'<div><button class="danger">Erase the ticked people</button></div></form></div>')


def handle(path: str, f: dict[str, str], picks: list[str], viewer: str, cfg: Config) -> str:
    if f.get("sure") != "1":
        raise DeskError("nothing erased: tick the box that says it cannot be undone")
    reason = f.get("reason", "asked")
    who = picks if path == "/erase/batch" else [f.get("person", "")]
    if not [w for w in who if w]:
        raise DeskError("nothing erased: nobody was ticked")
    done, still = 0, []
    for pid in who:
        r = erase.erase(pid, viewer, reason, cfg)
        done += 1
        still += r.remaining
    said = f"{done} person{'s' if done != 1 else ''} erased"
    if still:
        said += f"; still mentioned in {', '.join(sorted(set(still)))}"
    return said
