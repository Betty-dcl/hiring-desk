"""The lines under a name, as HTML: a link, their sentence, or "not found".

Kept out of `desk_web.py` so that what the card says lives next to the code
that decides it (`signals.py`), and the page only places it. No CSS of its
own: the classes are the ones `desk_web.CSS` already styles (`.sig`, `.none`,
`.lvl`, `.warn`, `a.open`), so the one hashed stylesheet does not change.

Wording is the card's contract with a partner who has no time:

  - a sentence in quotation marks is the candidate's, character for
    character, and says which document it is in;
  - nothing in quotation marks was written by the desk or by a model;
  - where nothing was found, the line says so and says what to open.
"""

from __future__ import annotations

import html
from typing import Any

from console import security
from signals import FROM

e = html.escape

LEVEL = {"described": "described", "in_their_words": "in their words",
         "claimed": "claimed only", "silent": "silent"}


def _said(text: str, src: str, *, full: bool, n: int = 150) -> str:
    """Their sentence, shortened on the page only, whole on hover."""
    short = text if full or len(text) <= n else text[:n].rsplit(" ", 1)[0] + " …"
    where = FROM.get(src, f"from {src}")
    return f'<q title="{e(text)}">{e(short)}</q> <span class="none src">{e(where)}</span>'


def card(sg: Any, *, full: bool = False) -> str:
    """Built, AI in their work, wants to own, current role."""
    if sg.to_open:
        built = "".join(
            f'<a class="open" href="{e(security.href(l.url))}" target="_blank" '
            f'rel="noopener noreferrer">'
            f'{e(l.host + (l.url.split(l.host, 1)[1] if full else ""))} ↗</a>'
            for l in sg.to_open)
    elif sg.built:
        built = (f'<span class="none">no link given ·</span> '
                 f'{_said(sg.built[0], sg.built_from[0], full=full)}')
    else:
        built = '<span class="none">no link given</span>'

    lvl = f'<span class="lvl {e(sg.ai)}">{LEVEL[sg.ai]}</span>'
    if sg.ai_rests_on:
        ai = lvl + _said(sg.ai_rests_on, sg.ai_from, full=full)
    elif sg.ai == "silent":
        ai = lvl + '<span class="warn">not stated</span>'
    else:
        ai = lvl + '<span class="none">not stated</span>'

    wants = (_said(sg.wants[0], sg.wants_from[0], full=full, n=170) if sg.wants
             else f'<span class="none">{e(sg.wants_missing.replace(" -- ", ", "))}</span>')

    rows = [("Built", built), ("AI in their work", ai), ("Wants to own", wants)]
    if sg.role:
        rows.append(("Current role", f'<q>{e(sg.role)}</q> '
                                     f'<span class="none src">(from CV, check it)</span>'))
    return ('<div class="sig">'
            + "".join(f'<span class="k">{k}</span><div>{v}</div>' for k, v in rows)
            + '</div>')
