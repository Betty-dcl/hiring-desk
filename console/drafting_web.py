""""Draft with AI" on the page: a button, and the draft in an editable box.

Shown only when `[mail] ai_drafts = true` (drafting.py). The button posts to
/draft-ai with the form token; the server asks for the draft, keeps it in
memory for the person who asked -- one per application, until the server
restarts -- and sends them back to the page, where it is shown above the
template's draft, with what the check could not find in the sources. Nothing
is sent and nothing is written to the application's log.
"""

from __future__ import annotations

import html
from typing import Any
from urllib.parse import quote

import drafting
from desk import Application, Config

e = html.escape

#: Kept by viewer and application. In memory on purpose: a draft is not a
#: record, and a restart forgetting it costs one more click.
DRAFTS: dict[tuple[str, str, str], drafting.Draft] = {}

CSS = """
.aid{margin-top:10px}
.aid .warn{color:var(--amber);font-size:13px;margin:6px 0}
.aid .warn b{font-weight:600}
"""


def button(d: Any, app: Application, viewer: str, back: str) -> str:
    if not drafting.enabled():
        return ""
    return (f'<form method="post" action="/draft-ai" class="inline aid">'
            f'<input type="hidden" name="t" value="{d.token}">'
            f'<input type="hidden" name="as" value="{e(viewer)}">'
            f'<input type="hidden" name="posting" value="{e(app.posting_id)}">'
            f'<input type="hidden" name="candidate" value="{e(app.candidate_id)}">'
            f'<input type="hidden" name="back" value="{e(back)}">'
            f'<button title="Asks the AI once; the draft lands here, unsent, for you to edit">'
            f'Draft with AI</button></form>')


def box(d: Any, app: Application, viewer: str, to: str) -> str:
    """The AI draft this viewer asked for, editable, with what the check found."""
    if not drafting.enabled():
        return ""
    dr = DRAFTS.get((viewer, app.posting_id, app.candidate_id))
    if dr is None:
        return ""
    href = f"mailto:{quote(to)}?subject={quote(dr.subject)}&body={quote(dr.body)}"
    warn = ""
    if dr.warnings:
        warn = (f'<div class="warn"><b>Not in the sources, check it:</b> '
                f'{e(", ".join(dr.warnings))}</div>')
    return (f'<div class="mailbox aid" data-mailto data-to="{e(to)}">'
            f'<div class="sub">Drafted with AI for {e(dr.by)} on {e(dr.at[:10])}. A draft '
            f'only: read it, change it, send it yourself.</div>{warn}'
            f'<label>Subject</label><input type="text" data-m="s" value="{e(dr.subject)}">'
            f'<label>Message</label><textarea data-m="b">{e(dr.body)}</textarea>'
            f'<div class="buttons"><a class="primary" data-m="open" href="{e(href)}">Open in my '
            f'mail</a><button type="button" data-copy="{e(dr.subject + chr(10) * 2 + dr.body)}">'
            f'Copy the text</button></div></div>')


def handle(f: dict[str, str], viewer: str, cfg: Config, client: Any = None) -> str:
    dr = drafting.draft(f.get("candidate", ""), f.get("posting", ""), cfg, by=viewer,
                        client=client)
    DRAFTS[(viewer, f.get("posting", ""), f.get("candidate", ""))] = dr
    return ("AI draft ready below, check it before sending"
            + (f"; {len(dr.warnings)} item(s) not in the sources" if dr.warnings else ""))
