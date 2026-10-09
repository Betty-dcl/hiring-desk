"""The Check page: check.py's health check, on the desk, read only."""

from __future__ import annotations

import html
from typing import Any

e = html.escape

CSS = """
.ck{border-collapse:collapse;width:100%;max-width:980px;font-size:14px;background:var(--panel);
border:1px solid var(--line2);border-radius:12px;overflow:hidden}
.ck td{padding:10px 14px;border-top:1px solid var(--line2);vertical-align:top}
.ck tr:first-child td{border-top:none}
.ck td.lv{width:64px;font-weight:700;font-size:12px;letter-spacing:.05em}
.ck td.lv.OK{color:var(--yes)}.ck td.lv.WARN{color:var(--amber)}.ck td.lv.FAIL{color:var(--no)}
.ck td.what{width:300px;font-weight:600;color:var(--ink)}
.ck td.detail{color:var(--mute)}
"""


def page(d: Any, viewer: str) -> str:
    import check
    from console.setup_web import _tools
    results = check.run()
    fails = sum(r.level == "FAIL" for r in results)
    warns = sum(r.level == "WARN" for r in results)
    said = ("Everything is in order." if not fails and not warns else
            f"{fails} problem{'s' if fails != 1 else ''} to fix, "
            f"{warns} thing{'s' if warns != 1 else ''} to look at.")
    rows = "".join(f'<tr><td class="lv {r.level}">{r.level}</td><td class="what">{e(r.what)}'
                   f'</td><td class="detail">{e(r.detail)}</td></tr>' for r in results)
    return (f'<h1>House rules</h1>{_tools(d, viewer, "check")}'
            f'<p class="lead">{e(said)} This page only reads: the same check as '
            f'<code>python check.py</code>.</p><table class="ck">{rows}</table>')
