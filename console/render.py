"""Turn the records into one HTML file that opens by double-click.

No server, no build step, no dependency, and no network: the data is inlined
at render time. A browser opening a local file cannot fetch its siblings, and
requiring someone to start a server to look at a list of candidates is how a
tool goes unused.

What the page is not allowed to do, because the rest of the repository spent
a fortnight earning the right to say it:

* it never prints a bare rank. Two candidates closer than the measured drift
  are drawn as one band, because that is what the measurement supports;
* every score carries its interval when one has been measured, and says so is
  unmeasured when it has not;
* motivation is counted beside the score and never inside it;
* what nobody has touched comes first, above whatever scored highest;
* the grid marks the cells that did not hold. A heatmap that colours every
  cell the same way says the screener was equally sure of all of them, and
  the stability records say otherwise.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

STYLE = """
:root {
  --bg: #fbfbfa; --fg: #1a1a18; --muted: #6b6b66; --line: #e4e4e0;
  --card: #ffffff; --warn: #8a4b00; --warn-bg: #fdf3e7; --ok: #2f5d3a;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #16161a; --fg: #ececea; --muted: #9a9a95; --line: #2c2c32;
    --card: #1e1e24; --warn: #e0a86a; --warn-bg: #2a2117; --ok: #8fc79a;
  }
}
* { box-sizing: border-box; }
body { margin: 0; padding: 32px 16px 64px; background: var(--bg); color: var(--fg);
  font: 15px/1.55 ui-sans-serif, -apple-system, "Segoe UI", system-ui, sans-serif; }
main { max-width: 1040px; margin: 0 auto; }
h1 { font-size: 22px; margin: 0 0 4px; letter-spacing: -0.01em; }
.sub { color: var(--muted); margin: 0 0 28px; font-size: 14px; }
section { margin: 0 0 28px; }
h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 0.07em;
  color: var(--muted); font-weight: 600; margin: 0 0 10px; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 10px; }
table { width: 100%; border-collapse: collapse; }
th { text-align: left; font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em;
  color: var(--muted); font-weight: 600; padding: 10px 14px; border-bottom: 1px solid var(--line); }
td { padding: 11px 14px; border-bottom: 1px solid var(--line); vertical-align: top; }
tr:last-child td { border-bottom: 0; }
tr.row { cursor: pointer; }
tr.row:hover { background: color-mix(in srgb, var(--fg) 4%, transparent); }
.who { font-weight: 600; }
.num { font-variant-numeric: tabular-nums; white-space: nowrap; }
.pm { color: var(--muted); font-size: 13px; }
.state { font-size: 12px; padding: 2px 8px; border-radius: 99px; border: 1px solid var(--line);
  color: var(--muted); white-space: nowrap; }
.tag { font-size: 12px; padding: 1px 7px; border-radius: 5px; background: color-mix(in srgb, var(--fg) 7%, transparent);
  margin-right: 4px; white-space: nowrap; }
.band { padding: 8px 14px; font-size: 13px; color: var(--muted);
  background: color-mix(in srgb, var(--fg) 3%, transparent); }
.alert { background: var(--warn-bg); color: var(--warn); border-color: transparent; }
.alert td, .alert th { border-color: color-mix(in srgb, var(--warn) 20%, transparent); }
.note { color: var(--muted); font-size: 13px; margin: 8px 2px 0; }
.grid { border-collapse: collapse; font-size: 12px; }
.grid th.rot { height: 118px; white-space: nowrap; padding: 0 2px; vertical-align: bottom; }
.grid th.rot > div { transform: rotate(-60deg); transform-origin: left bottom;
  width: 18px; color: var(--muted); font-weight: 500; }
.grid td.cell { width: 34px; height: 30px; text-align: center; border: 1px solid var(--bg);
  font-size: 11px; font-variant-numeric: tabular-nums; }
.s-strong { background: color-mix(in srgb, var(--ok) 78%, transparent); color: #fff; }
.s-moderate { background: color-mix(in srgb, var(--ok) 42%, transparent); }
.s-weak { background: color-mix(in srgb, var(--ok) 16%, transparent); }
.s-unknown { background: color-mix(in srgb, var(--fg) 5%, transparent); color: var(--muted); }
.moved { outline: 2px dashed var(--warn); outline-offset: -3px; }
.capped::after { content: "*"; color: var(--warn); font-weight: 700; }
.legend { display: flex; gap: 14px; flex-wrap: wrap; font-size: 12px; color: var(--muted);
  margin: 10px 2px 0; align-items: center; }
.swatch { display: inline-block; width: 12px; height: 12px; border-radius: 3px;
  vertical-align: -2px; margin-right: 4px; }
pre { margin: 0; padding: 14px; overflow-x: auto; font: 12.5px/1.5 ui-monospace, "SF Mono", Menlo, monospace; }
dialog { border: 1px solid var(--line); border-radius: 12px; background: var(--card); color: var(--fg);
  max-width: 760px; width: calc(100% - 32px); padding: 0; }
dialog::backdrop { background: rgba(0,0,0,.45); }
dialog header { padding: 14px 16px; border-bottom: 1px solid var(--line); display: flex;
  justify-content: space-between; align-items: center; }
dialog button { font: inherit; background: none; border: 1px solid var(--line); border-radius: 7px;
  padding: 4px 10px; color: var(--fg); cursor: pointer; }
"""

SCRIPT = """
const DATA = __DATA__;
function esc(s){const d=document.createElement('div');d.textContent=s??'';return d.innerHTML;}
function open_(id){
  const d = DATA.detail[id];
  document.getElementById('dtitle').textContent = id;
  document.getElementById('dbody').innerHTML =
    '<pre>' + esc(d ? d.explain : 'no screening on file for this candidate') + '</pre>';
  document.getElementById('panel').showModal();
}
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('tr.row').forEach(r =>
    r.addEventListener('click', () => open_(r.dataset.id)));
  document.getElementById('close').addEventListener('click',
    () => document.getElementById('panel').close());
});
"""


def _score_cell(st: dict[str, Any]) -> str:
    if st.get("score") is None:
        return '<span class="pm">not scored</span>'
    score = f'{st["score"]:.0%}'
    if st.get("spread"):
        return f'{score} <span class="pm">± {st["spread"]:.0%}</span>'
    return f'{score} <span class="pm">± unmeasured</span>'


def _bands(standings: list[dict[str, Any]], resolution: float) -> list[list[dict]]:
    """Group candidates the measurement cannot tell apart.

    A band, not a rank. Everyone without a score sits in their own band at the
    end: unscored is not last, it is unknown.
    """
    scored = [s for s in standings if s.get("score") is not None]
    unscored = [s for s in standings if s.get("score") is None]
    scored.sort(key=lambda s: -s["score"])
    out: list[list[dict]] = []
    for s in scored:
        if out and abs(out[-1][-1]["score"] - s["score"]) <= resolution:
            out[-1].append(s)
        else:
            out.append([s])
    out.extend([[s] for s in unscored])
    return out


SHORT = {"strong": "S", "moderate": "M", "weak": "w", "unknown": "·"}


def _heatmap(grid: dict[str, Any]) -> str:
    """Criteria across, candidates down, and honesty in the cells.

    Three things are drawn that a scorecard usually flattens: the strength,
    whether it was capped by a rule rather than earned, and whether it held
    still the last time the same input was run more than once. A cell that
    moved between runs is a cell nobody should compare across candidates,
    and it is marked instead of coloured like the rest.
    """
    if not grid.get("criteria") or not grid.get("rows"):
        return ""
    heads = "".join(f'<th class="rot"><div>{html.escape(c)}</div></th>'
                    for c in grid["criteria"])
    body = []
    for row in grid["rows"]:
        cells = []
        for cid in grid["criteria"]:
            cell = row["cells"].get(cid) or {}
            strength = cell.get("strength", "unknown")
            klass = f's-{strength} cell'
            if cell.get("moved"):
                klass += " moved"
            if cell.get("capped"):
                klass += " capped"
            title = strength
            if cell.get("capped"):
                title += f' — capped from {cell["capped"]}'
            if cell.get("moved"):
                title += " — did not hold between runs"
            cells.append(f'<td class="{klass}" title="{html.escape(title)}">'
                         f'{SHORT.get(strength, "?")}</td>')
        body.append(f'<tr><td class="who" style="white-space:nowrap">'
                    f'{html.escape(row["candidate_id"])}</td>{"".join(cells)}</tr>')

    return f"""<section>
<h2>Criterion by criterion</h2>
<div class="card" style="overflow-x:auto;padding:14px">
<table class="grid"><tr><th></th>{heads}</tr>{''.join(body)}</table>
</div>
<div class="legend">
  <span><span class="swatch s-strong"></span>strong</span>
  <span><span class="swatch s-moderate"></span>moderate</span>
  <span><span class="swatch s-weak"></span>weak</span>
  <span><span class="swatch s-unknown"></span>unknown — the document does not say</span>
  <span><span class="swatch moved" style="background:transparent"></span>did not hold between runs</span>
  <span><b style="color:var(--warn)">*</b> capped by a rule, not earned</span>
</div>
<p class="note">A dashed cell was answered differently when the same facts were screened
again. Comparing candidates on one of those is comparing the model with itself.</p>
</section>"""


def render(pipeline: dict[str, Any], detail: dict[str, Any], resolution: float,
           stale: list[str], due: list[str],
           grid: dict[str, Any] | None = None) -> str:
    posting = pipeline.get("posting_id", "")
    standings = pipeline.get("standings", [])
    rows = []
    for band in _bands(standings, resolution):
        if len(band) > 1:
            rows.append(f'<tr><td colspan="6" class="band">these {len(band)} are within '
                        f'{resolution:.0%} of each other — the measurement does not '
                        f'separate them</td></tr>')
        for st in band:
            cid = st["candidate_id"]
            tags = "".join(f'<span class="tag">{html.escape(t)}</span>'
                           for t in st.get("tags", []))
            idle = st.get("idle_days")
            klass = "row alert" if cid in stale or cid in due else "row"
            rows.append(
                f'<tr class="{klass}" data-id="{html.escape(cid)}">'
                f'<td class="who">{html.escape(cid)}</td>'
                f'<td><span class="state">{html.escape(st.get("state", ""))}</span></td>'
                f'<td class="num">{_score_cell(st)}</td>'
                f'<td class="num pm">{"" if idle is None else f"{idle:.0f}d"}</td>'
                f'<td class="pm">{html.escape(", ".join(st.get("read_by", [])) or "unread")}</td>'
                f'<td>{tags}</td></tr>')

    waiting = ""
    if stale or due:
        items = "".join(
            f'<li>{html.escape(c)} — {"parked, now due" if c in due else "untouched"}</li>'
            for c in [*due, *stale])
        waiting = (f'<section><h2>Waiting on you</h2><div class="card alert">'
                   f'<div style="padding:12px 16px"><ul style="margin:0;padding-left:18px">'
                   f'{items}</ul></div></div>'
                   f'<p class="note">Silence is the failure this is named after, so it '
                   f'sits above whatever scored highest.</p></section>')

    body = f"""<main>
<h1>{html.escape(posting)}</h1>
<p class="sub">{len(standings)} applications · click a row for the arithmetic behind its number</p>
{waiting}
<section>
<h2>Everyone</h2>
<div class="card"><table>
<tr><th>candidate</th><th>state</th><th>fit</th><th>idle</th><th>read by</th><th>tags</th></tr>
{''.join(rows)}
</table></div>
<p class="note">A fit percentage against this posting only. It is not a recommendation to
hire or to decline, and nothing in this system has a state that means either.</p>
</section>
{_heatmap(grid or {})}
<dialog id="panel">
  <header><strong id="dtitle"></strong><button id="close">close</button></header>
  <div id="dbody"></div>
</dialog>
</main>"""

    script = SCRIPT.replace("__DATA__", json.dumps({"detail": detail}, ensure_ascii=False))
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{html.escape(posting)} — Hiring desk</title>'
            f'<style>{STYLE}</style></head><body>{body}'
            f'<script>{script}</script></body></html>')
