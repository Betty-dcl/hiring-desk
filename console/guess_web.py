"""The AI guess page: how the percentage is counted, and everyone's numbers.

A percentage beside a name is trusted or dismissed on sight. This page lets a
partner check it instead: the four parts, what each weighs, and for every
application on the desk what was found in the documents.

Nothing here calls a model. The number is arithmetic over the CV and the
letter (`match.py`).
"""

from __future__ import annotations

import html
from typing import Any

import desk
import match

e = html.escape

CSS = """
.gs p{max-width:720px;margin:6px 0}
.gs ol{max-width:720px;margin:6px 0;padding-left:22px}.gs li{margin:4px 0}
.gs .formula{background:var(--panel);border:1px solid var(--line2);border-radius:var(--radius);
padding:12px 16px;margin:10px 0;max-width:720px;font-size:16px;font-weight:600}
.gs .formula small{display:block;font-weight:400;color:var(--mute);font-size:13px;margin-top:4px}
.gs table{border-collapse:collapse;background:var(--panel);border:1px solid var(--line2);
border-radius:var(--radius);margin:8px 0}
.gs th,.gs td{text-align:left;padding:8px 14px;border-top:1px solid var(--line2);font-size:14px;
vertical-align:top}
.gs thead th{border-top:0;color:var(--mute);font-weight:600;font-size:13px}
.gs td.n,.gs th.n{text-align:right;font-variant-numeric:tabular-nums}
.gs td.g{font-weight:650;font-size:16px}
.gs tr.found td{border-top:0;padding-top:0;color:var(--mute);font-size:13px}
.gs .scroll{overflow-x:auto}
"""

#: The four parts, in the order they are counted: (key, name, what earns it).
PARTS = (
    ("keywords", "What the posting asks for",
     "Each criterion is read from the posting's own sentence (“Top-tier consulting, banking, "
     "VC/PE, or an early-stage startup”). A word counts by how specific it is to this role: one "
     "every posting uses (“system”, “design”) earns nothing. A criterion is met in full once a "
     "third of what it asks is in the CV or the “Why us” answer, and not at all when it names "
     "something only this role asks for (Python, PostgreSQL, consulting) and none of it is "
     "there. Criteria count by their weight; a word counts once. A requirement the posting "
     "itself calls hard, with nothing found for it, caps the whole number at 50%. Where the "
     "posting asks for years (“5+ years designing backend platforms”), the dated jobs of the "
     "CV in that field are added up: asked 5, shown 1, the criterion is worth a fifth; no date "
     "in the CV, it is held at half. Years count only where the posting itself asks for them. "
     "An open application has no posting to compare to: this part is left out."),
    ("motivation", "Motivation",
     "Four things the answer to “Why us” does, a quarter each: there is one; it speaks of the company's "
     "subject; it says what the person would want to own; it ties that to their own work. "
     "It is a short answer to one question, so it weighs least."),
    ("proof", "Proof",
     "What can be opened or checked: a link to something built (40%), sentences that say "
     "they made something (30%), figures (30%). The first counts most: each further one "
     "closes half of what is left."),
    ("ai", "AI in their work",
     "Half for naming a tool (Claude Code, Cursor, agents...), half for naming it in a "
     "sentence where something was made with it."),
)


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def page(d: Any, viewer: str) -> str:
    reg, titles = desk.load_registry(desk._registry_path()), desk._titles()
    parts = "".join(f"<tr><th>{e(name)}</th><td class=n>{_pct(match.WEIGHTS[k])}</td>"
                    f"<td>{e(what)}</td></tr>" for k, name, what in PARTS)
    formula = " + ".join(f"{_pct(match.WEIGHTS[k])} x {name}" for k, name, _ in PARTS)
    rows = []
    for person in reg.people.values():
        for app in person.applications:
            m = match.of(person, app.posting_id)
            if m is None:
                continue
            cells = "".join(f'<td class=n>{_pct(m.parts[k]) if k in m.parts else "none"}</td>'
                            for k, _, _ in PARTS)
            found = "; ".join(f"{name}: {', '.join(m.found[k])}"
                              for k, name, _ in PARTS if m.found.get(k))
            # Said first: why the number is held down, when it is.
            if m.found.get("years"):
                found = "Years: " + "; ".join(m.found["years"]) + ". " + found
            if m.found.get("missing"):
                found = "Held at 50%, " + "; ".join(m.found["missing"]) + ". " + found
            if app.posting_id in match.open_roles():
                found = "No posting to compare to (an open application). " + found
            rows.append((-m.total, (
                f'<tr><th><a href="{d._person_link(person.person_id, viewer)}">'
                f'{e(person.name)}</a></th>'
                f'<td>{e(titles.get(app.posting_id, app.posting_id))}</td>{cells}'
                f'<td class="n g">{_pct(m.total)}</td></tr>'
                f'<tr class="found"><td colspan="7">{e(found) or "Nothing found."}</td></tr>')))
    table = "".join(r for _, r in sorted(rows, key=lambda x: x[0])) or (
        '<tr><td colspan="7" class="nothing">No application with a CV or a “Why us” answer yet.</td></tr>')
    head = "".join(f"<th class=n>{e(name)}</th>" for _, name, _ in PARTS)
    return (
        '<div class="gs"><h1>AI guess</h1>'
        '<p class="lead"><b>Speculative: a rough idea, nothing more. It is neither verifiable '
        'nor reliable, and it says nothing a reading of the CV would not.</b> A count of '
        'what is written in the CV and the “Why us” answer, set against the posting. It '
        'orders the list and never decides anything: your decisions do. This page shows how '
        'the number is counted.</p>'
        '<h2>The four parts</h2>'
        f'<div class="scroll"><table><thead><tr><th>Part</th><th class=n>Weighs</th>'
        f'<th>What earns it</th></tr></thead><tbody>{parts}</tbody></table></div>'
        '<h2>The formula</h2>'
        f'<div class="formula">AI guess = {e(formula)}'
        '<small>Each part goes from 0% to 100%. A posting with no criteria on file leaves the '
        'keywords out, and the three other parts share the total.</small></div>'
        '<h2>Everyone on the desk</h2>'
        '<p>Under each name: what was found in their documents, so every point can be checked '
        'against the CV and the “Why us” answer.</p>'
        f'<div class="scroll"><table><thead><tr><th>Name</th><th>Role</th>{head}'
        f'<th class=n>AI guess</th></tr></thead><tbody>{table}</tbody></table></div>'
        '<h2>What it does not do</h2><ol>'
        '<li>It never rejects anyone: there is no threshold, and no application is hidden.</li>'
        '<li>It counts words on paper. A person who did the thing and did not write it down '
        'scores low; a person who writes well scores high. Read the documents.</li>'
        '<li>Two guesses a few points apart are the same guess.</li></ol></div>')
