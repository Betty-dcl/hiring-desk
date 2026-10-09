"""Answering many at once: for a desk that receives hundreds of applications a month.

Sorting stays one decision per person: one click on a row. What does not
hold one by one is the answer owed to everyone who got a no. On the rows that
are Ready to answer no, this file adds:

* **a tick box per row**, and "Tick all on this page";
* **"Download their answers (CSV)"**: one line per ticked person, with the
  address, the first name, the role, and the team's "no" mail filled in for
  them. That is what a mail merge needs, in Gmail, in Outlook or in the
  applicant tracking system. The desk still sends nothing;
* **"Mark as answered"**: for each ticked person, the same record as the step
  taken one by one (`stages.answer_many`), under one lock. An application
  that moved meanwhile is left alone, and named.

Both buttons act on the same ticks, so what was downloaded and what is marked
are the same people.
"""

from __future__ import annotations

import csv
import html
import io
from datetime import datetime, timezone
from typing import Any

import desk
import stages
from desk import Config, DeskError

e = html.escape

CSS = """
.batch{background:var(--panel);border:1px solid var(--line2);border-radius:12px;padding:14px 22px;
margin-top:10px}
.batch .b1{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.batch .b1 b{font-weight:600;margin-right:6px}
.batch .sub{margin-top:8px;max-width:760px}
.tick{display:inline-flex;gap:6px;align-items:center;font-size:13px;color:var(--mute);
margin-top:10px;cursor:pointer}
.tick input{padding:0;width:16px;height:16px}
.pager{display:flex;gap:14px;align-items:baseline;flex-wrap:wrap;margin:16px 2px 0;font-size:14px;
color:var(--mute)}
.pager a{text-decoration:none}.pager a:hover{text-decoration:underline}
"""

JS = """
document.querySelectorAll('[data-tick-all]').forEach(b=>b.addEventListener('click',ev=>{
  ev.preventDefault();const boxes=[...document.querySelectorAll('input[name=pick]')];
  const all=boxes.every(x=>x.checked);boxes.forEach(x=>x.checked=!all);}));
"""

#: The columns of the download, in the order a mail merge reads them.
COLUMNS = ("email", "first_name", "last_name", "role", "subject", "message")


def tick(app: Any) -> str:
    """The tick box on one row. It belongs to the batch form, wherever that form is."""
    return (f'<label class="tick"><input type="checkbox" name="pick" form="batch" '
            f'value="{e(app.posting_id)}/{e(app.candidate_id)}">Answer with the others'
            f'</label>')


def bar(d: Any, viewer: str, back: str, n: int) -> str:
    """The batch tools above the list: shown when the page has someone to answer no to."""
    cfg: Config = d.cfg
    no = stages.name("to_answer_no", cfg)
    return (f'<form id="batch" method="post" action="/batch" class="batch">'
            f'<input type="hidden" name="t" value="{d.token}">'
            f'<input type="hidden" name="as" value="{e(viewer)}">'
            f'<input type="hidden" name="back" value="{e(back)}">'
            f'<div class="b1"><b>{n} on this page: {e(no)}</b>'
            f'<button type="button" data-tick-all>Tick all on this page</button>'
            f'<button name="do" value="csv">Download their answers (CSV)</button>'
            f'<button class="go" name="do" value="answered">Mark as answered</button></div>'
            f'<div class="sub">Tick the people you are answering. The download has one line '
            f'each: address, first name, role, and your “{e(cfg.label("pass"))}” mail filled '
            f'in, ready for a mail merge. Nothing is sent from here: send from your own '
            f'mail, then mark them as answered.</div></form>')


def pager(href: Any, page: int, pages: int, total: int, size: int) -> str:
    """"1 to 50 of 212", with the page before and the page after. Empty on one page."""
    if pages <= 1:
        return ""
    first, last = (page - 1) * size + 1, min(page * size, total)
    prev = f'<a href="{e(href(page - 1))}">← Previous {size}</a>' if page > 1 else ""
    nxt = (f'<a href="{e(href(page + 1))}">Next {min(size, total - last)} →</a>'
           if page < pages else "")
    return (f'<div class="pager">{prev}<span>{first} to {last} of {total}, best AI guess '
            f'first</span>{nxt}</div>')


def picked(f: dict[str, str]) -> list[tuple[str, str]]:
    """The ticked applications, as (posting, candidate). Anything else typed is refused."""
    out = []
    for line in f.get("pick", "").splitlines():
        posting, sep, candidate = line.strip().partition("/")
        if not sep or not posting or not candidate:
            raise DeskError("that is not an application on this desk")
        out.append((posting, candidate))
    if not out:
        raise DeskError("nobody is ticked: tick the people you are answering first")
    if len(out) > stages.BATCH_MAX:
        raise DeskError(f"{len(out)} at once is more than a batch holds ({stages.BATCH_MAX})")
    return list(dict.fromkeys(out))


def _cell(text: str) -> str:
    """A cell a spreadsheet will not run: a leading = + - @ is text, not a formula."""
    text = str(text)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def answers_csv(items: list[tuple[str, str]], cfg: Config,
                now: datetime | None = None) -> tuple[bytes, str]:
    """The "no" mail for each ticked person, one line each, and a file name.

    Only people who are Ready to answer no are written: the file is what will
    be sent, and nobody receives a no the desk has no decision for.
    """
    now = now or datetime.now(timezone.utc)
    pipes, reg, titles = desk._pipelines(), desk.load_registry(desk._registry_path()), \
        desk._titles()
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(COLUMNS)
    n = 0
    for posting, cid in items:
        pipe, person = pipes.get(posting), reg.person_of(cid)
        if pipe is None or person is None or cid not in pipe.candidates:
            raise DeskError(f"{cid} is not on the desk for {posting}")
        st = stages.derive(pipe.standing(cid), cfg, now)
        if st.id != "to_answer_no":
            raise DeskError(f"{person.name} is {stages.name(st.id, cfg)}, not "
                            f"{stages.name('to_answer_no', cfg)}: reload the page")
        role = titles.get(posting, posting)
        m = desk.draft(person, role, "pass", cfg, note=desk.pass_note(cid, posting, cfg))
        fields = desk.mail_fields(person, role, cfg)
        w.writerow([_cell(x) for x in (str(m["To"] or ""), fields["first_name"],
                                       fields["last_name"], role, str(m["Subject"]),
                                       m.get_content().strip())])
        n += 1
    # The byte order mark is what makes Excel read the accents.
    return ("﻿" + buf.getvalue()).encode("utf-8"), f"answers_{now:%Y-%m-%d}_{n}.csv"


def handle(f: dict[str, str], viewer: str, cfg: Config) -> tuple[str, Any]:
    """The /batch form: ("csv", (bytes, name)) or ("ok", message)."""
    items = picked(f)
    if f.get("do") == "csv":
        return "csv", answers_csv(items, cfg)
    if f.get("do") != "answered":
        raise DeskError("nothing recorded: press the button you mean")
    done, refused = stages.answer_many(items, viewer, cfg)
    msg = f"{done} marked as answered"
    if refused:
        msg += f"; {len(refused)} left as they were: " + "; ".join(refused[:3])
        if done == 0:
            raise DeskError(msg)
    return "ok", msg
