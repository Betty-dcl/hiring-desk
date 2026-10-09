"""The Tracking tab, the new recap page, and the vote-link confirmation.

Kept out of `desk_web.py` so the desk's page code stays what it was: that
file only routes here. Everything is rendered on the server -- the charts are
inline SVG built from the numbers in `metrics.py`, with no script, no CDN and
nothing fetched -- and every chart has the same numbers beside it in words or
in a table, because a chart a screen reader cannot read, or a partner cannot
quote, is decoration.

Written for an associate with a minute: the answer to "are we keeping the
promise?" is the first line of the page, the names of whoever it is being
broken for are the second, and the statistics come after, each with its n.
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any
from urllib.parse import quote

import desk
import metrics
import tuesday
import votelink
from desk import MEANINGS, DeskError, votes

e = html.escape

CSS = """
.nb{max-width:980px}
.nb h1{font-size:12px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;
color:var(--accent);margin:8px 0 10px}
.nb-head{font-size:34px;line-height:1.15;font-weight:600;letter-spacing:-.01em;color:var(--ink);
margin:0;max-width:760px}
.nb-head .nb-n{font-variant-numeric:tabular-nums}
.nb-promise{font-size:15px;color:var(--mute);margin:10px 0 0}
.nb-figs{display:flex;flex-wrap:wrap;margin:30px 0 8px;border-top:1px solid var(--ink);
border-bottom:1px solid var(--line)}
.nb-figs>div{flex:1 1 140px;padding:16px 20px 14px 0;display:flex;flex-direction:column}
.nb-figs>div+div{padding-left:20px;border-left:1px solid var(--line2)}
.nb-fig{font-size:44px;font-weight:300;line-height:1;color:var(--ink);font-variant-numeric:tabular-nums}
.nb-lab{font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--mute);margin-top:8px}
.nb-sec{margin-top:34px}
.nb-sec h2{font-size:12px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;
color:var(--mute);margin:0 0 6px;padding-bottom:8px;border-bottom:1px solid var(--line)}
.nb-waits{list-style:none;margin:0;padding:0;counter-reset:w}
.nb-waits li{display:grid;grid-template-columns:2em minmax(0,1.2fr) minmax(0,1fr) auto;gap:12px;
align-items:baseline;padding:10px 0;border-bottom:1px solid var(--line2);counter-increment:w}
.nb-waits li::before{content:counter(w);color:var(--faint);font-size:12px;font-variant-numeric:tabular-nums}
.nb-waits a{color:var(--ink);font-weight:600;text-decoration:none;font-size:15px}
.nb-waits a:hover{text-decoration:underline}
.nb-role{color:var(--mute);font-size:13.5px}
.nb-days{font-variant-numeric:tabular-nums;font-size:15px;color:var(--ink);text-align:right}
.nb-more,.nb-note{font-size:13px;color:var(--mute);margin:10px 0 0}
.nb-table{font-size:14px}
.nb-table thead th{font-size:11.5px;letter-spacing:.03em;text-transform:none}
.nb-table td.z{color:var(--line)}
.nb-table tbody th{font-weight:500;color:var(--ink)}
.nb-table tr:hover td,.nb-table tr:hover th{background:var(--accent-soft)}
@media (max-width:720px){.nb-head{font-size:26px}.nb-fig{font-size:34px}
.nb-waits li{grid-template-columns:1.6em minmax(0,1fr) auto}.nb-role{display:none}}
.tk-lead{font-size:15px;margin:4px 0 2px}
.tk-lead b{font-variant-numeric:tabular-nums}
table.tk tr.total th,table.tk tr.total td{font-weight:600;border-top:1px solid var(--ink)}
.tk-note{color:var(--mute);font-size:13px;margin:4px 0 0}
.tk-fig{margin-top:12px}
.tk-fig figcaption{font-size:13px;color:var(--mute);margin-bottom:6px}
.tk-fig svg{display:block;width:100%;height:auto;overflow:visible}
.tk-fig svg text{font:11px -apple-system,"Segoe UI",sans-serif;fill:var(--mute)}
.tk-fig svg .lbl{fill:var(--ink);font-size:12px}
.tk-fig svg .grid{stroke:var(--line2);stroke-width:1}
.tk-fig svg .axis{stroke:var(--line);stroke-width:1}
.tk-fig svg .km{fill:none;stroke:var(--accent);stroke-width:2;stroke-linejoin:round}
.tk-fig svg .cens{stroke:var(--accent);stroke-width:2}
.tk-fig svg .promise{stroke:var(--amber);stroke-width:1.5;stroke-dasharray:4 3}
.tk-fig svg .promise-t{fill:var(--amber)}
.tk-fig svg .bar{fill:var(--accent)}
.tk-fig svg .bar.faint{fill:var(--line)}
.tk-fig svg .ci{stroke:var(--ink);stroke-width:2;stroke-linecap:round}
.tk-fig svg .dot{fill:var(--ink);stroke:var(--panel);stroke-width:2}
.tk-fig svg .dot.small{fill:var(--panel);stroke:var(--ink);stroke-width:2}
.tk-fig svg .hit{fill:transparent}
table.tk{border-collapse:collapse;width:100%;font-size:13.5px;font-variant-numeric:tabular-nums}
table.tk th,table.tk td{padding:6px 8px;border-top:1px solid var(--line2);text-align:right}
table.tk th:first-child,table.tk td:first-child{text-align:left;padding-left:0;min-width:9em}
table.tk thead th{color:var(--mute);font-weight:600;font-size:12px;border-top:0;
border-bottom:1px solid var(--line);vertical-align:bottom}
table.tk small{color:var(--faint)}
.tk-scroll{overflow-x:auto}
table.cm{border-collapse:collapse;font-size:12.5px;font-variant-numeric:tabular-nums}
table.cm th{font-weight:500;color:var(--mute);padding:2px 6px;text-align:left}
table.cm thead th{text-align:center;font-size:11.5px;vertical-align:bottom;line-height:1.25;
padding:2px 3px}
table.cm tbody th{white-space:nowrap}
table.cm td{width:52px;height:30px;text-align:center;color:var(--ink);border:1px solid var(--line2)}
table.cm td.diag{font-weight:600}
.tk-pairs{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:10px}
.tk-warn{color:var(--amber)}
.tk-late li b{color:var(--amber)}
.sec li .me{color:var(--ink);font-weight:600}
.sec li small{color:var(--faint);margin-left:4px}
.sec li .late{color:var(--amber);font-size:12px;margin-right:4px}
.vls{display:inline-flex;gap:4px;flex-wrap:wrap;margin-left:8px;vertical-align:middle}
a.vl{font-size:12.5px;text-decoration:none;border:1px solid var(--line);
border-radius:var(--radius);padding:2px 9px;color:var(--ink)}
a.vl:hover{border-color:var(--ink)}
.confirm{max-width:560px}
.tk-cap{text-align:left}.tk-cap.sp{margin-bottom:6px}
.tk-gap{margin-top:14px}.tk-m0{margin:0}.tk-m10{margin:10px 0 0}
.confirm p.foot{margin-top:12px}
.confirm .opt{display:grid;gap:8px;margin-top:12px}
.confirm label{font-size:13px;color:var(--mute);display:grid;gap:4px}
@media (max-width:720px){figure.tk-fig{overflow-x:auto}.tk-fig svg{min-width:560px}}
"""


def _style() -> str:
    # The rules above are part of the desk's one stylesheet (the CSP allows no
    # second style element); kept as a hook so the page layout reads the same.
    return ""


# --------------------------------------------------------------------------
# Charts: SVG strings, built from numbers, readable without the picture
# --------------------------------------------------------------------------

def _ticks(hi: float) -> list[float]:
    step = 1 if hi <= 8 else 2 if hi <= 16 else 7 if hi <= 60 else 14 if hi <= 120 else 30
    out, x = [], 0.0
    while x <= hi + 1e-9:
        out.append(x)
        x += step
    return out


def km_chart(pr: metrics.Promise) -> str:
    """Share of applications still without an answer, by days since they arrived."""
    W, H, L, R, T, B = 640, 210, 44, 16, 14, 30
    pw, ph = W - L - R, H - T - B
    longest = pr.longest or 0
    hi = max(pr.within_days * 1.25, longest * 1.05, 1.0)

    def x(d: float) -> float:
        return L + pw * d / hi

    def y(s: float) -> float:
        return T + ph * (1 - s)

    grid = "".join(f'<line class="grid" x1="{L}" x2="{W - R}" y1="{y(s):.1f}" '
                   f'y2="{y(s):.1f}"/><text x="{L - 6}" y="{y(s) + 4:.1f}" '
                   f'text-anchor="end">{s:.0%}</text>' for s in (0, .5, 1))
    xt = "".join(f'<text x="{x(d):.1f}" y="{H - 10}" text-anchor="middle">{d:g}</text>'
                 for d in _ticks(hi))
    pts, prev = [f"M{x(0):.1f},{y(1):.1f}"], 1.0
    steps = []
    for t, s in pr.curve[1:]:
        pts.append(f"H{x(t):.1f}V{y(s):.1f}")
        steps.append(f'<circle class="hit" cx="{x(t):.1f}" cy="{y(s):.1f}" r="8">'
                     f'<title>after {metrics.fmt_days(t)}: {s:.0%} still without an answer'
                     f'</title></circle>')
        prev = s
    pts.append(f"H{x(longest):.1f}")
    cens = []
    for c, d in pr.waiting:
        s = next((v for t, v in reversed(pr.curve) if t <= d), 1.0)
        cens.append(f'<g><line class="cens" x1="{x(d):.1f}" x2="{x(d):.1f}" '
                    f'y1="{y(s) - 5:.1f}" y2="{y(s) + 5:.1f}"/><rect class="hit" '
                    f'x="{x(d) - 6:.1f}" y="{y(s) - 9:.1f}" width="12" height="18">'
                    f'<title>{e(c.name)}: {d:.0f} days, still waiting</title></rect></g>')
    px = x(pr.within_days)
    promise = (f'<line class="promise" x1="{px:.1f}" x2="{px:.1f}" y1="{T}" y2="{T + ph}"/>'
               f'<text class="promise-t" x="{px + 4:.1f}" y="{T + 10}">'
               f'promise: {pr.within_days:g} days</text>')
    desc = (f"Step curve. {len(pr.answered)} answered, {len(pr.waiting)} still waiting. "
            f"Median time to a first answer counting those waiting: "
            f"{metrics.fmt_days(pr.km_median) if pr.km_median is not None else 'not reached'}.")
    return (f'<svg viewBox="0 0 {W} {H}" role="img" aria-labelledby="km-t km-d">'
            f'<title id="km-t">Share of applications still without an answer, by days since '
            f'they arrived</title><desc id="km-d">{e(desc)}</desc>{grid}'
            f'<line class="axis" x1="{L}" x2="{W - R}" y1="{T + ph}" y2="{T + ph}"/>{xt}'
            f'<text x="{W - R}" y="{H}" text-anchor="end">days since applying</text>'
            f'{promise}<path class="km" d="{"".join(pts)}"/>{"".join(cens)}{"".join(steps)}'
            f'</svg>')


def funnel_chart(row: metrics.FunnelRow, cfg: desk.Config, t: metrics.Tracking) -> str:
    names = _stage_names(cfg)
    W, rowh, L = 640, 34, 120
    H = rowh * len(row.counts) + 6
    top = max(row.counts.values()) or 1
    out, prev = [], None
    for i, s in enumerate(row.counts):
        k = row.counts[s]
        w = (W - L - 190) * k / top
        yy = 4 + i * rowh
        pct = "" if not prev else " · " + metrics.share(k, prev, t) + " of the step before"
        out.append(f'<g><text class="lbl" x="{L - 10}" y="{yy + 18}" text-anchor="end">'
                   f'{e(names[s])}</text>'
                   f'<rect class="bar{" faint" if not k else ""}" x="{L}" y="{yy + 5}" '
                   f'width="{max(w, 2):.1f}" height="18" rx="4"/>'
                   f'<text class="lbl" x="{L + max(w, 2) + 8:.1f}" y="{yy + 18}">{k}'
                   f'<tspan fill="var(--mute)">{e(pct)}</tspan></text>'
                   f'<title>{e(names[s])}: {k}{e(pct)}</title></g>')
        prev = k
    return (f'<svg viewBox="0 0 {W} {H}" role="img" aria-labelledby="fn-t">'
            f'<title id="fn-t">Applications reaching each step: '
            + e(", ".join(f"{names[s]} {row.counts[s]}" for s in row.counts))
            + f'</title>{"".join(out)}</svg>')


def kappa_chart(ag: metrics.Agreement) -> str:
    """Kappa per pair, and for everyone, with its interval, on one scale from -1 to 1."""
    rows = [(f"{p.a} & {p.b}", p.kappa, p.ci, p.n) for p in ag.pairs]
    if ag.fleiss_n or len(ag.pairs) >= 3:
        rows.append(("everyone (Fleiss)", ag.fleiss, ag.fleiss_ci, ag.fleiss_n))
    W, rowh, L, R = 640, 30, 150, 110
    H = rowh * len(rows) + 30
    pw = W - L - R

    def x(k: float) -> float:
        return L + pw * (max(-1.0, min(1.0, k)) + 1) / 2

    axis = "".join(f'<line class="grid" x1="{x(k):.1f}" x2="{x(k):.1f}" y1="4" '
                   f'y2="{H - 22}"/><text x="{x(k):.1f}" y="{H - 8}" text-anchor="middle">'
                   f'{k:g}</text>' for k in (-1, -.5, 0, .5, 1))
    axis += (f'<text x="{x(0):.1f}" y="{H - 22 + 12}" text-anchor="middle" '
             f'dy="-16">chance</text>')
    out = []
    for i, (name, k, ci, n) in enumerate(rows):
        yy = 4 + i * rowh + rowh / 2
        few = n < ag.min_n
        mark = ""
        if ci:
            mark += (f'<line class="ci" x1="{x(ci[0]):.1f}" x2="{x(ci[1]):.1f}" '
                     f'y1="{yy:.1f}" y2="{yy:.1f}"/>')
        if k is not None:
            mark += (f'<circle class="dot{" small" if few else ""}" cx="{x(k):.1f}" '
                     f'cy="{yy:.1f}" r="5"/>')
            said = f"kappa {k:+.2f}" + (f", interval {ci[0]:+.2f} to {ci[1]:+.2f}" if ci
                                         else ", no interval")
        else:
            mark += (f'<text x="{x(0) + 8:.1f}" y="{yy + 4:.1f}">undefined</text>')
            said = "kappa undefined"
        right = f"n={n}" + (" · too few" if few else "")
        out.append(f'<g><text class="lbl" x="{L - 10}" y="{yy + 4:.1f}" text-anchor="end">'
                   f'{e(name)}</text>{mark}<text x="{W - R + 10}" y="{yy + 4:.1f}"'
                   f'{" class=promise-t" if few else ""}>{e(right)}</text>'
                   f'<title>{e(name)}: {e(said)}, n={n}</title></g>')
    return (f'<svg viewBox="0 0 {W} {H}" role="img" aria-labelledby="kp-t">'
            f'<title id="kp-t">Agreement beyond chance, per pair of voters: '
            + e("; ".join(f"{r[0]} {metrics.fmt_kappa(r[1])} (n={r[3]})" for r in rows))
            + f'</title>{axis}{"".join(out)}</svg>')


def confusion(p: metrics.PairAgreement, cfg: desk.Config) -> str:
    top = max(p.table.values()) or 1
    head = "".join(f"<th scope=col>{e(cfg.label(m))}</th>" for m in MEANINGS)
    body = []
    for a in MEANINGS:
        cells = []
        for b in MEANINGS:
            k = p.table[(a, b)]
            # Six steps of shade, as classes: the CSP refuses style attributes.
            heat = 0 if not k else 1 + round(4 * k / top)
            cells.append(f'<td class="h{heat}{" diag" if a == b else ""}" '
                         f'title="{e(p.a)} {e(cfg.label(a))}, {e(p.b)} {e(cfg.label(b))}: '
                         f'{k}">{k or ""}</td>')
        body.append(f"<tr><th scope=row>{e(cfg.label(a))}</th>{''.join(cells)}</tr>")
    obs = f"{p.observed:.0%}" if p.observed is not None else "--"
    return (f'<div class="tk-fig"><div class="tk-scroll"><table class="cm">'
            f'<caption class="tk-note tk-cap sp">'
            f'<b>{e(p.a)}</b> down, <b>{e(p.b)}</b> across · {p.n} rated by both · '
            f'same class {obs}</caption><thead><tr><th></th>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div></div>')


# --------------------------------------------------------------------------
# The page
# --------------------------------------------------------------------------

def _stage_names(cfg: desk.Config) -> dict[str, str]:
    return metrics.stage_names(cfg)


def _funnel_table(rows: list[metrics.FunnelRow], cfg: desk.Config, t: metrics.Tracking,
                  what: str) -> str:
    names = _stage_names(cfg)
    shown = metrics.stages_for(cfg)
    head = "".join(f"<th scope=col>{e(names[s])}</th>" for s in shown)
    body = []
    for r in rows:
        cells, prev = [], None
        for s in shown:
            k = r.counts[s]
            pct = "" if prev is None else f" <small>{e(metrics.share(k, prev, t))}</small>"
            cells.append(f"<td>{k}{pct}</td>")
            prev = k
        after = (f"{r.after_trial['closed']} closed, {r.after_trial['open']} open"
                 if r.counts["trial_day"] else "--")
        inf = f" <small>{r.inferred} inferred</small>" if r.inferred else ""
        body.append(f"<tr><th scope=row>{e(r.group)}{inf}</th>{''.join(cells)}"
                    f"<td>{e(after)}</td></tr>")
    return (f'<div class="tk-fig tk-scroll"><table class="tk"><caption class="tk-note tk-cap">'
            f'{e(what)}</caption><thead><tr><th scope=col></th>'
            f'{head}<th scope=col>After the trial day</th></tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


#: The columns of the Tracking table when a team runs a single interview: how
#: far applications got, then two ways out. `columns(cfg)` is the team's own.
COLUMNS = ("received", "voted", "to_contact", "contacted", "replied", "first_call", "trial_day",
           "talk_later", "answered_no")


def columns(cfg: desk.Config) -> tuple[str, ...]:
    """The table's columns, with one per interview this team runs."""
    import stages
    return (COLUMNS[:5] + stages.rounds(cfg) + COLUMNS[6:])


def counts(d: Any, now: datetime) -> tuple[list[tuple[str, dict[str, int]]], list[Any], float]:
    """Per role, then a total: whole numbers, no statistics.

    Each step from Applied to Hired counts the applications that reached it
    (they stay counted once they move on, so the row reads as a funnel); Talk
    later and Answered no count where applications are now. Read from the
    same log and the same states as every other page.
    """
    import stages
    cfg = d.cfg
    t = metrics.load_tracking()
    pipes, reg, titles = desk._pipelines(), desk.load_registry(desk._registry_path()), \
        desk._titles()
    cs = metrics.cases(pipes, reg, cfg, t, now=now)
    rows: dict[str, dict[str, int]] = {}
    for c in cs:
        role = titles.get(c.posting_id) or c.posting_id
        row = rows.setdefault(role, {k: 0 for k in columns(cfg)})
        top = metrics._stage(c, cfg)
        for s in metrics.STAGES[:top + 1]:
            if s in row:
                row[s] += 1
        st = stages.derive(pipes[c.posting_id].standing(c.candidate_ids[-1]), cfg, now)
        if st.id in ("talk_later", "answered_no"):
            row[st.id] += 1
    out = sorted(rows.items())
    out.append(("Total", {k: sum(r[k] for _, r in out) for k in columns(cfg)}))
    # Everyone still without an answer, longest wait first. No deadline is
    # claimed: the team never promised one, so the page states the wait itself.
    waiting = sorted(metrics.promise(cs, t, now).waiting, key=lambda x: -x[1])
    return out, waiting, t.answer_within_days


def tracking(d: Any, viewer: str, now: datetime) -> str:
    """For information: how many applied to each role, and how far they went.

    Whole numbers, read in a glance; nothing here asks anyone to hurry. The
    statistics (delays, Kaplan-Meier, agreement between voters) stay in
    `metrics.py` and `python metrics.py report`.
    """
    import stages
    cfg = d.cfg
    try:
        rows, _late, _n = counts(d, now)
    except DeskError as err:
        return f'<div class="flash">{e(str(err))}</div>'
    total = rows[-1][1] if rows else {}
    roles = len(rows) - 1 if rows else 0
    interviewed = total.get(stages.ROUNDS[0], 0)
    figures = "".join(
        f'<div><span class="nb-fig">{v}</span><span class="nb-lab">{e(k)}</span></div>'
        for k, v in (("Applications", total.get("received", 0)),
                     ("Roles", roles),
                     ("Decided", total.get("voted", 0)),
                     ("Said yes", total.get("to_contact", 0)),
                     ("Interviewed", interviewed)))
    sn = stages.names(cfg)
    cols = columns(cfg)
    head = {"received": "Applied", "voted": "Decided" if cfg.solo else "Voted",
            **{k: sn[k] for k in cols[2:]}}
    if cfg.solo:
        # "Decided" is any of the four; "Said yes" is the yes among them.
        head["to_contact"] = "Said yes"
    th = "".join(f"<th scope=col>{e(head[k])}</th>" for k in cols)
    body = "".join(
        f'<tr{" class=total" if role == "Total" else ""}><th scope=row>{e(role)}</th>'
        # A zero is said quietly: the eye goes to what happened.
        + "".join(f'<td{" class=z" if not r[k] else ""}>{r[k]}</td>' for k in cols) + "</tr>"
        for role, r in rows)
    note = ('<p class="nb-note">Each column counts everyone who got that far. '
            f'<b>{e(head["voted"])}</b>: everyone sorted, whatever the choice. '
            '<b>Said yes</b>: the ones said yes to.</p>' if cfg.solo else
            '<p class="nb-note">Each column counts everyone who got that far.</p>')
    return (f'<div class="nb"><h1>Overview</h1>'
            f'<p class="nb-head">Applications, by role</p>'
            f'<p class="nb-promise">For information: how many people applied to each role, '
            f'and how far they went.</p>'
            f'<div class="nb-figs">{figures}</div>'
            f'<section class="nb-sec"><h2>By role</h2><div class="tk-scroll">'
            f'<table class="tk nb-table"><thead><tr><th scope=col>Role</th>{th}</tr></thead>'
            f'<tbody>{body}</tbody></table></div>{note}</section></div>')


# --------------------------------------------------------------------------
# The recap, and its links
# --------------------------------------------------------------------------

def recap(d: Any, viewer: str, now: datetime) -> str:
    cfg = d.cfg
    t = metrics.load_tracking()
    w = tuesday.build(desk._pipelines(), desk.load_registry(desk._registry_path()), cfg, t,
                      viewer=viewer, now=now, titles=desk._titles())
    link = tuesday.links_for(viewer, cfg, t, base="", now=now)

    def person(i: tuesday.Item) -> str:
        return f'<a href="{d._person_link(i.person_id, viewer)}">{e(i.name)}</a>'

    out = [_style(), f'<h1>Catch-up</h1><div class="sub">{e(now.strftime("%A %d %B"))} · since '
                     f'{e(w.since.strftime("%A %d %B"))} · <a href="/recap.eml'
                     f'{d._q(viewer)}">as a draft for your mail app</a></div>']
    for title, lines in tuesday.sections(w, cfg, link, person):
        lis = "".join(f'<li class="names">{x}</li>' for x in lines)
        out.append(f'<h2>{e(title)}</h2><div class="sec"><ul>{lis}</ul></div>')
    return "".join(out)


def recap_draft(cfg: desk.Config, viewer: str, now: datetime) -> tuple[bytes, str]:
    t = metrics.load_tracking()
    w = tuesday.build(desk._pipelines(), desk.load_registry(desk._registry_path()), cfg, t,
                      viewer=viewer, now=now, titles=desk._titles())
    link = tuesday.links_for(viewer, cfg, t, base=t.base_url, now=now)
    m = tuesday.draft(w, cfg, link, to=tuesday.address_of(viewer), base=t.base_url)
    return bytes(m), f"recap_{now.date().isoformat()}_{desk.slug(viewer)}.eml"


def link_voter(token: str, cfg: desk.Config) -> str | None:
    """Whose link this is, if it is a valid one -- for the page frame only."""
    try:
        return votelink.read(token, cfg).voter
    except DeskError:
        return None


def confirm(d: Any, token: str, viewer: str | None, now: datetime) -> tuple[int, str]:
    """(status, body): the page a vote link opens. It shows; it never votes."""
    cfg = d.cfg
    try:
        link = votelink.read(token, cfg, now=now)
        if viewer is not None and viewer != link.voter:
            raise votelink.LinkError(f"this link is {link.voter}'s; you are signed in as "
                                     f"{viewer}")
        used = votelink.is_used(link)
    except DeskError as err:
        return 403, (f'<div class="card confirm"><h3>This link cannot be used</h3>'
                     f'<p class="sub">{e(str(err))}</p><p><a href="/">Open the desk</a></p>'
                     f'</div>')
    pipe = desk._pipelines().get(link.posting)
    reg = desk.load_registry(desk._registry_path())
    p = reg.person_of(link.candidate)
    name = p.name if p else link.candidate
    role = desk._titles().get(link.posting) or link.posting
    back = d._person_link(p.person_id, link.voter) if p else "/"
    if used:
        return 410, (f'<div class="card confirm"><h3>This link has been used</h3><p class="sub">'
                     f'Each link works once. <a href="{back}">{e(name)} on the desk</a>.</p>'
                     f'</div>')
    if pipe is None or link.candidate not in pipe.candidates:
        return 404, '<div class="card confirm"><h3>Nothing on the desk for this link</h3></div>'
    s = pipe.standing(link.candidate)
    if s.state == "closed":
        return 409, (f'<div class="card confirm"><h3>{e(name)} is closed</h3><p class="sub">'
                     f'Reopen it on the desk to class it.</p></div>')
    mine = votes(s).get(link.voter)
    already = (f'<p class="sub">You voted <b>{e(cfg.label(mine.meaning))}</b>; '
               f'confirming changes it.</p>' if mine else "")
    hidden = (f'<input type="hidden" name="t" value="{d.token}">'
              + (f'<input type="hidden" name="as" value="{e(link.voter)}">'
                 if d.access.mode == "pick" else ""))
    one = link.meaning != votelink.ANY
    choices = [link.meaning] if one else list(MEANINGS)
    opts = []
    if "later" in choices:
        need = cfg.later_needs_date
        opts.append(f'<label>Back on ({"needed" if need else "optional"} for '
                    f'{e(cfg.label("later"))})'
                    f'<input type="date" name="until"{" required" if one and need else ""}>'
                    f'</label>')
    if "pass" in choices:
        reasons = "".join(f"<option>{e(r)}</option>" for r in cfg.pass_reasons)
        req = " required" if one and cfg.pass_reason_required else ""
        opts.append(f'<label>Reason{"" if req else " (optional)"}, for '
                    f'{e(cfg.label("pass"))}<select name="reason"{req}><option value="">--'
                    f'</option>{reasons}</select></label>')
    opts.append(f'<label>Comment (optional)<input type="text" name="comment" '
                f'maxlength="{desk.COMMENT_MAX}"></label>')
    if one:
        buttons = (f'<button class="go" name="label" value="{link.meaning}">Confirm: '
                   f'{e(cfg.label(link.meaning))}</button>')
        ask = f"<b>{e(cfg.label(link.meaning))}</b> for <b>{e(name)}</b>?"
    else:
        buttons = "".join(f'<button class="{m}" name="label" value="{m}">'
                          f'{e(cfg.label(m))}</button>' for m in MEANINGS)
        buttons = f'<div class="votes">{buttons}</div>'
        ask = f"Your vote on <b>{e(name)}</b>"
    return 200, (f'<div class="card confirm"><h3>{ask}</h3><div class="sub">{e(role)} · as '
                 f'{e(link.voter)} · <a href="{back}">open the card first</a></div>{already}'
                 f'<form method="post" action="/v/{e(token)}">{hidden}'
                 f'<div class="opt">{"".join(opts)}</div>'
                 f'<div class="buttons">{buttons}</div></form>'
                 f'<p class="sub foot">Nothing was recorded by opening this '
                 f'page. The link works once.</p></div>')


def redeem(d: Any, token: str, form: dict[str, str], viewer: str | None,
           now: datetime | None = None) -> str:
    """Cast the vote; return the person's page to go back to. Raises DeskError."""
    cfg = d.cfg
    label = form.get("label", "")
    link, _ = votelink.redeem(token, cfg, meaning=label, viewer=viewer,
                              until=form.get("until", ""), comment=form.get("comment", ""),
                              reason=form.get("reason", ""), now=now)
    p = desk.load_registry(desk._registry_path()).person_of(link.candidate)
    return f"/person/{quote(p.person_id)}" if p else "/"
