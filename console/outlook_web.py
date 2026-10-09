"""Outlook on the page: the switch and the connection on House rules, a "Check
Outlook" button on the Interested tab, and "they wrote back" on a row.

The rules are `outlook.py`'s; this file only draws them. Every button is a
form posted with the page's token, like every other one on the desk.
"""

from __future__ import annotations

import html
from typing import Any

import outlook
import stages
from console import security
from desk import Application, Config

e = html.escape

CSS = """
.olk{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:0 0 12px;font-size:13.5px;
color:var(--mute)}
.olk form{display:inline}
.olk-code{font-size:20px;font-weight:650;letter-spacing:.08em;color:var(--ink)}
.wrote{font-size:14px;color:var(--ink);display:flex;gap:8px;align-items:baseline;flex-wrap:wrap;
margin-top:10px}
.wrote b{font-weight:600}.wrote span{color:var(--mute);font-size:13px}
"""


def _form(d: Any, viewer: str, action: str, words: str, back: str, cls: str = "") -> str:
    return (f'<form method="post" action="{action}"><input type="hidden" name="t" '
            f'value="{d.token}"><input type="hidden" name="as" value="{e(viewer)}">'
            f'<input type="hidden" name="back" value="{e(back)}">'
            f'<button{f" class={chr(34)}{cls}{chr(34)}" if cls else ""}>{e(words)}</button>'
            f'</form>')


def switch(cfg: Config) -> str:
    """The on/off choice, inside the House rules form."""
    on = cfg.outlook
    return (f'<div><label class="f">Look in Outlook for replies</label><select name="outlook">'
            f'<option value="off"{"" if on else " selected"}>Off</option>'
            f'<option value="on"{" selected" if on else ""}>On: a “Check Outlook” button '
            f'on Interested</option></select></div>')


def section(d: Any, viewer: str) -> str:
    """Below the House rules form: what it does, and the connection itself."""
    cfg: Config = d.cfg
    s = outlook.load_settings(_root())
    what = ('<p class="lead">When it is on, “Check Outlook” asks your mailbox one question '
            'per person you wrote to and are waiting on: did a mail come from their address '
            'since? The desk keeps the time and a link to open it, nothing else. It cannot '
            'read what they wrote, and it never ticks “Replied, OK” for you: a reply can be '
            'a no. Before turning it on, tell candidates in your privacy notice that replies '
            'are noted in this tool (docs/OUTLOOK.md).</p>')
    back = "/settings"
    if not cfg.outlook:
        body = '<p class="sub">Off. Turn it on above, then save.</p>'
    elif not s.ready:
        body = ('<p class="sub">On, but the desk is not registered with Microsoft yet. Your '
                'Microsoft admin does it once (docs/OUTLOOK.md, five minutes) and writes its '
                'client id in desk.toml under [outlook].</p>')
    elif outlook.connected():
        last = outlook.last_check()
        body = (f'<div class="olk">Connected to your mailbox · last check '
                f'{e(last[:16].replace("T", " ")) if last else "never"} UTC'
                f'{_form(d, viewer, "/outlook/disconnect", "Disconnect and forget", back)}'
                f'</div>')
    elif outlook.pending():
        p = outlook.pending()
        uri = security.href(p.get("verification_uri", "")) or "https://microsoft.com/devicelogin"
        body = (f'<div class="olk">Open <a href="{e(uri)}" target="_blank" rel="noopener '
                f'noreferrer">{e(uri)}</a>, sign in with your work account and type '
                f'<span class="olk-code">{e(p.get("user_code", ""))}</span>'
                f'{_form(d, viewer, "/outlook/finish", "I have signed in", back, "go")}</div>')
    else:
        body = (f'<div class="olk">Not connected. '
                f'{_form(d, viewer, "/outlook/connect", "Connect my Outlook", back, "go")}'
                f'</div>')
    return f'<div class="set"><h2>Outlook</h2>{what}{body}</div>'


def bar(d: Any, viewer: str, back: str) -> str:
    """Above the Interested list: one button that asks the mailbox, when it may."""
    cfg: Config = d.cfg
    if not cfg.outlook or not outlook.load_settings(_root()).ready:
        return ""
    if not outlook.connected():
        return (f'<div class="olk">Outlook is on but not connected: '
                f'<a href="/settings{d._q(viewer)}">connect it on House rules</a>.</div>')
    last = outlook.last_check()
    when = f"last check {last[:16].replace('T', ' ')} UTC" if last else "never checked"
    return (f'<div class="olk">{_form(d, viewer, "/outlook/sync", "Check Outlook for replies", back)}'
            f'<span>{e(when)}</span></div>')


def wrote(d: Any, app: Application, st: stages.State) -> str:
    """"They wrote back on 6 Oct · Open in Outlook": on a row waiting on a reply."""
    if not d.cfg.outlook or st.id != "contacted":
        return ""
    r = outlook.reply(app.posting_id, app.candidate_id)
    if not r:
        return ""
    link = security.href(r.get("link", ""))
    open_ = (f'<a href="{e(link)}" target="_blank" rel="noopener noreferrer">Open in Outlook'
             f'</a>' if link else "")
    return (f'<div class="wrote"><b>&#x2709; They wrote back on {e(stages.short(r["at"]))}'
            f'</b>{open_}<span>Read it: if it is an OK, tick below.</span></div>')


def handle(path: str, cfg: Config) -> str:
    """The four buttons. Raises outlook.OutlookError with why not."""
    s = outlook.load_settings(_root())
    if path == "/outlook/connect":
        p = outlook.connect(s)
        return f"type {p['user_code']} on {p['verification_uri']}"
    if path == "/outlook/finish":
        outlook.finish(s)
        return "Outlook connected"
    if path == "/outlook/disconnect":
        outlook.disconnect()
        return "Outlook disconnected, every hint deleted"
    n, asked = outlook.sync(cfg, s)
    return (f"{n} of the {asked} people you are waiting on wrote back" if asked else
            "nobody is waiting on a reply")


def _root() -> Any:
    import desk
    return desk.ROOT / "desk.toml"
