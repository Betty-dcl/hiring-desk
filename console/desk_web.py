"""The desk in a browser.

Standard library only: no framework, no build step, nothing to install beyond
Python. It is meant to be read and changed by whoever runs it, in an
afternoon, which a framework would make harder rather than easier.

Two ways to know who is looking, set in `desk.toml` under `[access]`:

* `pick` -- a name menu. On one laptop, for trying it. Anyone who can reach
  the page can vote as anyone, so it binds to 127.0.0.1 and says so.
* `header` -- behind a sign-in proxy (Google, Microsoft, Cloudflare Access),
  which puts the signed-in email in a header. Only mapped emails get in, and
  the name menu is gone.

Every form carries a token minted when the server starts, so another site
cannot make a signed-in partner's browser cast a vote they never clicked.

Three design rules, because the person using this has ten minutes:

* **What needs you comes first**, on every page: a strip of counts at the top,
  and inside each list the rows waiting on the viewer before the rest.
* **The votes read at a glance**: one dot per voter, coloured by class once
  it may be seen, outlined while it has not been cast.
* **A button appears when it can be pressed.** Class buttons while it is your
  turn, the mail once a class is settled, "they replied" once someone wrote.
"""

from __future__ import annotations

import html
import secrets
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

import desk
from console import security
from desk import (DOC_TYPES, MAX_DOC_BYTES, MEANINGS, NOTE_MAX, Access, Application,
                  Config, DeskError, Person, next_step, notes, outcome, timeline,
                  visible_to, votes)
from pipeline import PipelineError, Standing

e = html.escape

CSS = """
:root{--bg:#f6f3ee;--panel:#ffffff;--ink:#1c1b19;--mute:#55514b;--faint:#6f6a62;
--line:#d9d2c6;--line2:#e9e3d9;--accent:#b4492a;--accent-soft:#f1ece4;
--amber:#8a4600;--amber-soft:#ffffff;--radius:8px}
@media (prefers-color-scheme:dark){:root{--bg:#171614;--panel:#1f1e1b;--ink:#ece8e1;
--mute:#bcb6ab;--faint:#9d978c;--line:#3f3c36;--line2:#2c2a26;--accent:#e8906f;
--accent-soft:#262420;--amber:#e0a55c;--amber-soft:#1f1e1b}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.55 Inter,"Segoe UI Variable Text","Segoe UI",system-ui,-apple-system,"Helvetica Neue",Arial,sans-serif}
a{color:var(--accent)}
header{background:var(--panel);border-bottom:1px solid var(--line2)}
.bar{max-width:1040px;margin:0 auto;padding:12px 20px;display:flex;align-items:center;gap:14px;
flex-wrap:wrap}
.brand{font-weight:600;font-size:17px;margin-right:auto;display:flex;align-items:center;gap:12px}
.brand i{width:12px;height:12px;background:var(--ink);display:inline-block}
.brand img.logo{height:44px;width:auto;max-width:260px;object-fit:contain;display:block}
.brand small{color:var(--faint);font-weight:400;font-size:13px}
nav{display:flex;gap:0;flex-wrap:wrap}
nav a{padding:6px 11px;text-decoration:none;color:var(--mute);font-size:14px;white-space:nowrap;
border-bottom:2px solid transparent}
nav a.on{color:var(--ink);border-bottom-color:var(--accent)}
nav a:hover{color:var(--ink)}
.who{color:var(--mute);font-size:13px;display:flex;align-items:center;gap:6px}
select,input,button,textarea{font:inherit;color:var(--ink)}
select,input{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);
padding:7px 10px;font-size:14px}
button{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);
padding:7px 14px;font-size:14px;cursor:pointer}
button:hover{border-color:var(--ink);background:var(--accent-soft)}
main{max-width:1040px;margin:0 auto;padding:26px 20px 70px}
h1{font-size:28px;font-weight:650;letter-spacing:-.01em;margin:2px 0 4px}
h2{font-size:16px;font-weight:600;color:var(--ink);margin:34px 0 10px}
.sub{color:var(--mute);font-size:13px;margin-top:1px}
p.lead{color:var(--mute);font-size:14px;margin:2px 0 18px;max-width:680px}
.nothing{color:var(--faint)}
.flash{padding:8px 0;margin-bottom:14px;color:var(--amber);border-bottom:1px solid var(--line)}
.flash.ok{color:var(--ink)}
a.back{display:inline-block;font-size:13px;color:var(--mute);text-decoration:none;margin-bottom:8px}
a.back:hover{color:var(--ink)}
footer{max-width:1040px;margin:0 auto;padding:0 20px 30px;color:var(--faint);font-size:12px}
.tools{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:0 0 12px}
.tools form.search{margin-left:auto}.tools input[type=search]{width:220px}
.rolef label{display:flex;gap:6px;align-items:center;font-size:13px;color:var(--mute)}
.found{margin:-4px 0 12px}
details.add>summary{cursor:pointer;color:var(--mute);font-size:13px}
details.add .panel{border:1px solid var(--line)}
.tabs{display:flex;gap:0;flex-wrap:wrap;margin:0 0 14px;border-bottom:1px solid var(--line)}
.tabs a{text-decoration:none;padding:8px 9px;color:var(--mute);font-size:13.5px;
border-bottom:2px solid transparent;margin-bottom:-1px}
.tabs a.on{color:var(--ink);border-color:var(--accent);font-weight:600}
.tabs a:hover{color:var(--ink)}
.tabs em{font-style:normal;color:var(--faint);margin-left:5px;font-variant-numeric:tabular-nums}
.row{background:var(--panel);border:1px solid var(--line2);border-radius:12px;padding:18px 22px;
margin-top:10px}
.r1{display:grid;grid-template-columns:minmax(0,1fr) 180px 130px 112px;gap:14px;align-items:baseline}
.who3{min-width:0}
.name{font-weight:600;color:var(--ink);text-decoration:none;font-size:18px}
.name:hover{text-decoration:underline}
.role{margin-left:10px;font-size:14px;color:var(--mute)}
.row .standing{font-size:14px;color:var(--ink)}
.fit{font-size:13px;color:var(--mute);font-variant-numeric:tabular-nums;white-space:nowrap}
.fit b{font-size:22px;font-weight:650;color:var(--ink);margin-left:6px}.fit.none{color:var(--faint)}
.fit small{margin-left:6px;color:var(--faint);font-size:12px}
a.more{font-size:14px;text-align:right;white-space:nowrap;text-decoration:none}
a.more:hover{text-decoration:underline}
.links{display:flex;gap:18px;flex-wrap:wrap;font-size:14px;margin-top:6px}
.links a{text-decoration:none}.links a:hover{text-decoration:underline}
.links .none{color:var(--faint)}.links a.more-m{display:none}
a.multi,span.multi{font-size:12px;color:var(--faint);margin-left:8px;white-space:nowrap}
.act{margin-top:14px}
.votes{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.votes button.mine{background:var(--ink);color:var(--panel);border-color:var(--ink)}
.votes input.cmt{flex:1;min-width:180px;max-width:340px}
.yours{font-size:13px;color:var(--mute)}.yours b{color:var(--ink);font-weight:600}
.panel{display:none;gap:8px;flex-wrap:wrap;align-items:center;margin-top:8px;padding:8px 0}
.panel.open{display:flex}.panel input[type=text]{flex:1;min-width:180px}
.go{background:var(--ink);color:var(--bg);border:1px solid var(--ink);
border-radius:var(--radius);padding:7px 16px;cursor:pointer;font-size:14px;text-decoration:none}
details.change{display:inline}
details.change>summary{list-style:none;cursor:pointer;display:inline;color:var(--accent);font-size:13px;
text-decoration:underline;text-underline-offset:3px}
details.change>summary::-webkit-details-marker{display:none}
details.change[open]>summary{display:none}
.said{font-size:13px;color:var(--mute);margin-top:6px}
.said b{color:var(--ink);font-weight:600}
.band{padding:14px 2px 0;font-size:13px;color:var(--mute)}
.empty{color:var(--mute);padding:28px 0;text-align:left}
.empty b{display:block;color:var(--ink);font-weight:600;margin-bottom:4px}
.legend{color:var(--faint);font-size:13px;margin:16px 0 0}
.card{background:var(--panel);border:1px solid var(--line2);border-radius:12px;padding:22px 26px 18px;
margin-top:18px}
.card h3{margin:0;font-size:20px;font-weight:600}
.card .top{display:flex;justify-content:space-between;gap:12px;align-items:baseline;flex-wrap:wrap}
.card .top>div{flex:1;min-width:0}
.card .top .fit{margin-left:2px}.card .top .fit b{font-size:18px;margin-left:4px}
.card h2{margin-top:22px}
.vt{border-collapse:collapse;width:100%;font-size:13.5px}
.vt th,.vt td{text-align:left;padding:6px 10px 6px 0;border-top:1px solid var(--line2);
vertical-align:top}
.vt th{font-weight:600;width:110px}
.vt td.v{width:240px}
.vt .hid,.vt .none{color:var(--faint)}
.vt small{color:var(--faint)}
.buttons{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.buttons a,.buttons button{font-size:14px;border:1px solid var(--line);background:var(--panel);
border-radius:var(--radius);padding:7px 14px;text-decoration:none;cursor:pointer;color:var(--ink)}
.buttons .go,.buttons .primary{background:var(--ink);color:var(--bg);border-color:var(--ink)}
details.why{margin-top:14px}
details.why summary{cursor:pointer;color:var(--mute);font-size:13px}
pre{white-space:pre-wrap;font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
border:1px solid var(--line2);padding:10px;overflow-x:auto}
ol.tl{list-style:none;padding:0;margin:0}
ol.tl li{display:grid;grid-template-columns:96px 1fr;gap:12px;padding:6px 0;
border-top:1px solid var(--line2);font-size:13.5px}
ol.tl li:first-child{border-top:0}
ol.tl time{color:var(--faint);font-variant-numeric:tabular-nums;font-size:13px}
ol.tl .d{color:var(--mute)}ol.tl .r{color:var(--faint);font-size:12.5px}
.sec ul{list-style:none;padding:0;margin:0}
.sec li{padding:8px 0;border-top:1px solid var(--line2);font-size:14px}
.sec li:first-child{border-top:0}
.sec li b{font-variant-numeric:tabular-nums}
.names a{text-decoration:none}.names a:hover{text-decoration:underline}
.thread{margin-top:10px}
.thread h4{margin:0 0 6px;font-size:15px;font-weight:600}
.note{display:block;margin-bottom:8px;font-size:13.5px}
.note .meta{color:var(--faint);font-size:12px}.note .body{white-space:pre-wrap}
.note.hid .body{color:var(--faint)}
.thread form{display:flex;gap:8px;margin-top:6px}
.thread textarea{flex:1;font-size:14px;border:1px solid var(--line);border-radius:var(--radius);
padding:8px 10px;background:var(--panel);color:var(--ink);min-height:38px;resize:vertical}
.count{font-size:12px;color:var(--faint);margin-left:8px}
.doclist{display:flex;gap:6px 16px;flex-wrap:wrap;align-items:baseline;font-size:14px}
.doclist small{color:var(--faint)}
details.up{display:inline-block}details.up summary{cursor:pointer;color:var(--mute);font-size:13px}
.brief{font-size:13px;color:var(--mute);margin-top:6px;line-height:1.45}
.brief b{color:var(--ink);font-weight:600}
.brief q{quotes:"“" "”"}
.brief .gate{color:var(--amber);font-weight:600}
.brief .ask{display:block;color:var(--mute);margin-top:2px}
.brief .ask:before{content:"Ask first: ";color:var(--faint)}
.sig{display:grid;grid-template-columns:130px 1fr;gap:6px 12px;font-size:14px;line-height:1.5}
.sig .k{color:var(--mute);font-size:13px}
.sig q{quotes:"“" "”";color:var(--ink)}
.sig .none{color:var(--faint)}
.sig .warn{color:var(--amber)}
.sig a.open{margin-right:12px}
.sig .lvl{font-size:12px;color:var(--mute);margin-right:6px}
.sig .lvl:after{content:" ·"}
@media (max-width:720px){.sig{grid-template-columns:1fr;gap:0 12px}.sig .k{padding-top:8px}}
.set{border-top:1px solid var(--line);padding:14px 0 18px;margin-bottom:6px}
.set h2{font-size:16px;margin:0 0 4px;border:0;padding:0}
.set p.lead{margin:0 0 12px;color:var(--mute);font-size:13px}
.set label.f{display:block;font-size:12px;color:var(--faint);margin:12px 0 4px}
.set input[type=text],.set textarea,.set select{width:100%;font-size:14px;border:1px solid var(--line);
border-radius:var(--radius);padding:6px 8px;background:var(--panel);color:var(--ink)}
.set textarea{min-height:150px;resize:vertical;line-height:1.5}
.set ::placeholder{color:var(--faint);opacity:1}
.set .two{display:grid;grid-template-columns:1fr 1fr;gap:0 16px}
.set .fields{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px}
.set .fields button{font-size:12px;padding:1px 7px;color:var(--mute)}
.set .mail{border-top:1px solid var(--line2);padding-top:14px;margin-top:16px}
.set .mail h3{font-size:14px;margin:0;display:flex;gap:8px;align-items:baseline}
.set textarea.short{min-height:112px}
.tag{font-size:12px;color:var(--mute);font-weight:400}
.tag.own{color:var(--ink)}
.preview{white-space:pre-wrap;background:var(--accent-soft);border-radius:var(--radius);padding:10px 14px;
font-size:13px;color:var(--mute);margin-top:8px}
.preview b{color:var(--ink)}
.save{position:sticky;bottom:0;background:var(--bg);padding:12px 0;display:flex;gap:12px;
align-items:center;border-top:1px solid var(--line)}
.save button{background:var(--ink);color:var(--bg);border:1px solid var(--ink);padding:6px 16px}
.mailbox{border:1px solid var(--line);border-radius:var(--radius);padding:12px 14px;margin-top:10px;font-size:13px}
.mailbox .subj{font-weight:600}
.mailbox pre{white-space:pre-wrap;font:inherit;margin:6px 0 0;padding:0;border:0;color:var(--mute)}
.mailbox .ex{color:var(--amber);font-size:12px;margin-top:6px}
.mailbox .buttons{margin-top:8px}
@media (max-width:720px){.set .two{grid-template-columns:1fr}}
.also{font-size:13px;color:var(--mute);margin-top:2px}
.also a{font-weight:600}
.aihint{font-size:12px;color:var(--faint)}
form.inline{display:inline}
.docs{display:inline-flex;gap:10px;flex-wrap:wrap}
:root{--yes:#1c7a3e;--unsure:#b05f00;--pool:#1d5fae;--no:#b3261e}
@media (prefers-color-scheme:dark){:root{--yes:#6fcf8f;--unsure:#f0b35a;--pool:#86b7f2;--no:#f2877e}}
.dec{display:inline-block;font-size:13.5px;font-weight:600;white-space:nowrap;color:var(--panel);
padding:2px 10px;border-radius:6px}
.dec.d-contact{background:var(--yes)}.dec.d-discuss{background:var(--unsure)}
.dec.d-later{background:var(--pool)}.dec.d-pass{background:var(--no)}
.dec.d-own{background:var(--mute)}
.standing .dec{display:table;margin-top:5px}
.legend .dec{margin-right:10px}
.doc.none{color:var(--faint);font-size:13px}
@media (max-width:720px){.r1{grid-template-columns:minmax(0,1fr) auto;gap:2px 12px}
.r1 .who3{grid-column:1 / -1}.r1 .standing{grid-column:1}.r1 .fit{grid-column:2;text-align:right}
.r1 a.more{display:none}.links a.more-m{display:inline}.tools form{flex:1 1 100%}
.links{margin-top:4px}.vt td.v{width:auto}.vt th{width:72px}
.tools form.search{margin-left:0}.tools input[type=search]{width:100%}}
"""

JS = """
let lastField=null;
document.querySelectorAll('.set [data-mail]').forEach(el=>el.addEventListener('focus',()=>lastField=el));
document.querySelectorAll('.set .fields button').forEach(b=>b.addEventListener('click',ev=>{
  ev.preventDefault();const box=b.closest('.mail');
  const el=(lastField&&box.contains(lastField))?lastField:box.querySelector('textarea');
  const tok='{'+b.dataset.f+'}';
  if(!el.value){el.value=el.placeholder;}
  const i=el.selectionStart??el.value.length;
  el.value=el.value.slice(0,i)+tok+el.value.slice(el.selectionEnd??i);
  el.focus();el.selectionStart=el.selectionEnd=i+tok.length;el.dispatchEvent(new Event('input'));}));
const SAMPLE=JSON.parse((document.getElementById('sample')||{dataset:{}}).dataset.sample||'{}');
document.querySelectorAll('select[data-submit],input[data-submit]').forEach(el=>el.addEventListener('change',()=>el.form.submit()));
function flow(t){return t.trim().split(/\\n\\s*\\n/).map(p=>p.split('\\n').map(x=>x.trim()).join(' ')).join('\\n\\n');}
function fill(t){return t.replace(/[{](first_name|last_name|role|company|sender|interview|day|time)[}]/g,(m,k)=>SAMPLE[k]??m);}
document.querySelectorAll('.set .mail').forEach(box=>{
  const s=box.querySelector('input[data-mail]'),t=box.querySelector('textarea'),
        pv=box.querySelector('.preview'),tag=box.querySelector('.tag');
  const draw=()=>{const sub=s.value||s.placeholder,body=t.value||t.placeholder;
    pv.innerHTML='';const b=document.createElement('b');b.textContent=fill(sub);pv.append(b,
      document.createTextNode('\\n\\n'+flow(fill(body))));
    if(tag){const own=!!t.value.trim();tag.textContent=own?'your words':'example';
      tag.classList.toggle('own',own);}};
  s.addEventListener('input',draw);t.addEventListener('input',draw);draw();});
document.querySelectorAll('[data-copy]').forEach(b=>b.addEventListener('click',ev=>{
  ev.preventDefault();navigator.clipboard.writeText(b.dataset.copy).then(()=>{
    const was=b.textContent;b.textContent='Copied';setTimeout(()=>b.textContent=was,1400);});}));
document.querySelectorAll('[data-open]').forEach(b=>b.addEventListener('click',ev=>{
  ev.preventDefault();const f=b.closest('form');
  f.querySelectorAll('.panel').forEach(p=>p.classList.toggle('open',
    p.dataset.for===b.dataset.open&&!p.classList.contains('open')));
  const i=f.querySelector('.panel.open input[type=date],.panel.open select');if(i)i.focus();}));
document.querySelectorAll('[data-mailto]').forEach(box=>{
  const s=box.querySelector('[data-m=s]'),t=box.querySelector('[data-m=b]'),
        a=box.querySelector('[data-m=open]'),c=box.querySelector('[data-copy]');
  const draw=()=>{const q=encodeURIComponent;
    a.href='mailto:'+q(box.dataset.to)+'?subject='+q(s.value)+'&body='+q(t.value);
    if(c)c.dataset.copy=s.value+'\\n\\n'+t.value;};
  s.addEventListener('input',draw);t.addEventListener('input',draw);});
"""

#: The votes' tabs, then one per family of states (console/stages_web.py).
TABS = ("todo", "waiting", "discuss", "write", "process", "later", "closed", "all",
        "interested", "no")

#: The words on a document button, and what is said when there is none.
DOC_OPEN = {"cv": "Open CV", "letter": "Open their “Why us”"}
DOC_MISSING = {"cv": "no CV", "letter": "no “Why us” answer"}

#: What the fit percentage is, said wherever it is shown.
AI_HINT = ("speculative: a rough count of what the CV and the “Why us” answer say, not "
           "reliable; your decision is what counts")


def settings_from_form(f: dict[str, str]) -> dict[str, Any]:
    """The settings form, as the values `desk.save_settings` keeps. Empty = the example."""
    lines = lambda k: [x.strip() for x in f.get(k, "").splitlines() if x.strip()]  # noqa: E731
    out: dict[str, Any] = {
        "labels": {m: f.get(f"label_{m}", "").strip() for m in desk.MEANINGS},
        # Only the mails on the page: one not shown (an interview the team no
        # longer runs) keeps its words.
        "subjects": {m: f.get(f"subject_{m}", "").strip() for m, _ in Desk.MAILS
                     if f"subject_{m}" in f},
        "templates": {m: f.get(f"template_{m}", "").replace("\r\n", "\n").strip()
                      for m, _ in Desk.MAILS if f"template_{m}" in f},
        "blind": f.get("blind", "until_you_vote"),
        "agreement": f.get("agreement", "unanimous"),
        "go_ahead_alone": f.get("go_ahead_alone", "after_one_yes"),
        "recap_weekday": f.get("recap_weekday", "tuesday"),
        "company": f.get("company", "").strip(),
        "sender": f.get("sender", "").strip(),
    }
    voters = [v.strip() for v in f.get("voters", "").split(",") if v.strip()]
    if voters:
        out["voters"] = voters
    import stages
    out["state_names"] = {s: f.get(f"state_{s}", "").strip() for s in stages.DEFAULT_NAMES}
    # Empty means any voter: kept as an empty list, so clearing it is a choice too.
    out["advancers"] = [v.strip() for v in f.get("advancers", "").split(",") if v.strip()]
    if f.get("interviews", "").strip().isdigit():
        out["interviews"] = int(f["interviews"])
    if f.get("pass_reasons", "").strip():
        out["pass_reasons"] = lines("pass_reasons")
    if "outlook" in f:
        out["outlook"] = f["outlook"] == "on"
    if "extra_choices" in f:
        # One person's House rules: the choices switched on, and their own.
        from console import choices_web
        out["choices_off"] = [m for m in choices_web.SWITCHABLE
                              if f.get(f"usechoice_{m}") != "1"]
        out["extra_choices"] = choices_web.clean(lines("extra_choices"))
    if "template_contact" in f:
        # The House rules form: a mail whose box is unticked is never offered.
        out["mails_off"] = [m for m, _ in Desk.MAILS if f.get(f"use_{m}") != "1"]
    return out


def _drift(posting: str, candidate: str) -> float | None:
    #: The any-order gap (desk.toml [any_order]): measured from every repeat
    #: on disk by default, not this one person's range over three runs.
    from triage import any_order_noise
    return any_order_noise(posting, candidate)


def _banded(items: list[tuple[tuple, str, str, float | None]]) -> list[str]:
    """Rows in order, with a line over each run the fit cannot separate.

    Two neighbours share a band when the gap between them is no larger than
    the drift either shows on identical input -- the rule the stability report
    prints as NOT SEP. A band never crosses from "yours" to "not yours", and
    with no drift measured nothing is banded: no noise figure is invented.
    """
    from triage import bands
    out: list[str] = []
    i = 0
    while i < len(items):
        j = i
        while j < len(items) and items[j][0][0] == items[i][0][0]:
            j += 1
        seg = items[i:j]
        by_id = {it[2]: it for it in seg}
        scored = [(it[2], -it[0][1], it[3]) for it in seg if it[0][1] != 1]
        unscored = [it for it in seg if it[0][1] == 1]
        for band in bands(scored):
            if len(band) > 1:
                out.append(f'<div class="band">The AI guess cannot tell the next {len(band)} apart: '
                           f'any order.</div>')
            out.extend(by_id[k][1] for k in band)
        out.extend(it[1] for it in unscored)
        i = j
    return out


def _tab_of(s: Standing, viewer: str, cfg: Config) -> str:
    import stages
    from console.stages_web import tab_of
    return tab_of(stages.derive(s, cfg), s, viewer, cfg)


def _received(s: Standing) -> datetime | None:
    return next((ev.when for ev in s.events if ev.kind == "received"), None)


def _days_ago(s: Standing, now: datetime) -> str:
    got = _received(s)
    if got is None:
        return ""
    d = int((now - got).total_seconds() // 86400)
    return "applied today" if d == 0 else f"applied {d} day{'s' if d > 1 else ''} ago"


def _matches(q: str, person: Person, role: str) -> bool:
    n = desk.normalise_name
    hay = n(" ".join([person.name, person.email, role]))
    return all(w in hay for w in n(q).split())


def _say(nxt: str, viewer: str) -> str:
    """The next step, in the viewer's words: their own turn first."""
    if nxt.startswith("waiting for "):
        who = nxt[len("waiting for "):].split(", ")
        if viewer in who:
            rest = [w for w in who if w != viewer]
            return "needs your vote" + (f" · {len(rest)} more" if rest else "")
    return nxt


#: What an empty tab says: why it is empty, and where to look instead.
EMPTY = {
    "todo": ("Nothing waits for your vote.",
             "The other tabs show where the rest stand."),
    "waiting": ("Nothing is waiting on the others.",
                "Applications you voted on stay here until everyone has."),
    "discuss": ("Nothing to settle together.",
                "An application lands here when the votes do not agree."),
    "closed": ("Nothing closed.", "An application closes once its answer has been sent, "
               "when the person withdraws, or when they are hired."),
    "all": ("No application.", "Add one by hand below, or connect Ashby (ashby.py)."),
}


#: The same, said to one person deciding alone.
SOLO_EMPTY = {
    "todo": ("Nothing left to sort.", "A new application lands here, best AI guess first."),
    "discuss": ("Nothing to look at again.",
                "An application lands here when you were not sure."),
    "later": ("Nobody in the pool.",
              "People you like, at the wrong time: they wait here until you take them up."),
}
#: The phases only one person deciding has (console/stages_web.py SOLO_TABS).
EMPTY.update({
    "interested": ("Nobody to follow yet.",
                   "Say yes to someone under To sort: they come here, and you follow them "
                   "step by step, from the first message to the trial day."),
    "no": ("No answer to send.",
           "A no lands here with its answer ready. Once it is sent, they leave the list."),
})

#: Rows drawn per page of a tab. A tab with hundreds of rows is not read, it
#: is searched; and every row drawn opens that person's documents.
PAGE = 50


def _empty(tab: str, q: str = "", solo: bool = False) -> str:
    if q:
        return (f"<b>No application matches &ldquo;{e(q)}&rdquo;.</b>"
                f"Search looks at the name, the email and the role.")
    head, hint = {**EMPTY, **(SOLO_EMPTY if solo else {})}.get(tab, ("Nothing here.", ""))
    return f"<b>{e(head)}</b>{e(hint)}"


#: The filters above the Interested list: the step each person is at.
STEPS_SHOWN = (("", "All"), ("to_contact", "To contact"), ("contacted", "Contacted"),
               ("replied", "Replied OK"), ("interviews", "In interviews"),
               ("trial_day", "Trial day"))


def _step_group(st: Any) -> str:
    """Which filter of the Interested list a state falls under."""
    if st.id in _stages.ROUNDS:
        return "interviews"
    return st.id if st.id in ("to_contact", "contacted", "replied", "trial_day") else ""


def _needs(nxt: str, viewer: str) -> bool:
    """Does this next step wait on the person looking?"""
    if nxt.startswith("waiting for "):
        return viewer in nxt[len("waiting for "):].split(", ")
    return nxt.startswith(("mail to send", "wake-up due", "to settle"))


# The Tracking and trial-day pages bring their own rules. They join the one
# stylesheet: the CSP allows a single hashed <style>, so a second one would
# be refused by the browser.
from console import tracking_web as _tracking_web  # noqa: E402
from console import setup_web as _setup_web  # noqa: E402
from console import stages_web as _stages_web  # noqa: E402
import stages as _stages  # noqa: E402
import match as _match  # noqa: E402

CSS += _tracking_web.CSS + _setup_web.CSS + _stages_web.CSS
from console import drafting_web as _drafting_web  # noqa: E402
CSS += _drafting_web.CSS
from console import guess_web as _guess_web  # noqa: E402
CSS += _guess_web.CSS
EMPTY.update(_stages_web.EMPTY)
SOLO_EMPTY.update(_stages_web.SOLO_EMPTY)
from console import batch_web as _batch_web  # noqa: E402
CSS += _batch_web.CSS
from console import track_web as _track_web  # noqa: E402
CSS += _track_web.CSS
from console import outlook_web as _outlook_web  # noqa: E402
CSS += _outlook_web.CSS
CSS += (".steps-f{display:flex;flex-wrap:wrap;gap:6px;margin:-4px 0 12px}"
        ".steps-f a{font-size:13px;text-decoration:none;color:var(--mute);padding:4px 11px;"
        "border:1px solid var(--line);border-radius:999px;background:var(--panel)}"
        ".steps-f a.on{background:var(--ink);color:var(--panel);border-color:var(--ink)}"
        ".steps-f em{font-style:normal;margin-left:6px;opacity:.7;font-variant-numeric:tabular-nums}")
CSS += (".set label.use{display:flex;gap:8px;align-items:center;font-size:13.5px;"
        "color:var(--ink);margin:4px 0 8px}.mail.off input[type=text],.mail.off textarea,"
        ".mail.off .preview,.mail.off .fields{opacity:.45}")
from console import pool_web as _pool_web  # noqa: E402
CSS += _pool_web.CSS
from console import check_web as _check_web  # noqa: E402
from console import erase_web as _erase_web  # noqa: E402
CSS += _erase_web.CSS
CSS += _check_web.CSS
from console import progress_web as _progress_web  # noqa: E402
CSS += _progress_web.CSS
from console import calendar_web as _calendar_web  # noqa: E402
CSS += _calendar_web.CSS
JS += _calendar_web.JS
JS += _batch_web.JS


class Desk:
    """The page renderer. Holds nothing between requests but settings."""

    def __init__(self, cfg: Config, access: Access):
        self.cfg, self.access = cfg, access
        self.token = secrets.token_urlsafe(16)

    # -- frame ------------------------------------------------------------

    def page(self, viewer: str, tab: str, body: str, flash: str = "", ok: bool = False,
             title: str = "") -> str:
        q = self._q(viewer)
        # "Your call" is a part of House rules: the same settings, asked as questions.
        on = "settings" if tab == "setup" else tab
        nav = "".join(f'<a class="{"on" if on == t else ""}" href="{href}{q}">{label}</a>'
                      for t, href, label in (("desk", "/", "At the door"),
                                             ("progress", "/progress", "Progress"),
                                             ("pool", "/pool", "Pool"),
                                             ("calendar", "/calendar", "Calendar"),
                                             ("recap", "/recap", "Catch-up"),
                                             ("tracking", "/tracking", "Overview"),
                                             ("guess", "/guess", "AI guess"),
                                             ("settings", "/settings", "House rules"))
                      # One person deciding sees everything on the list itself,
                      # by role and in colour: a page saying who waits on whom
                      # has nobody else to name. A team keeps it.
                      if not (self.cfg.solo and t == "recap")
                      # The pool is a page for one person; a team keeps it as a tab.
                      and not (not self.cfg.solo and t == "pool"))
        if self.access.mode == "pick":
            opts = "".join(f'<option{" selected" if v == viewer else ""}>{e(v)}</option>'
                           for v in self.cfg.voters)
            who = (f'<form method="get" class="who">Looking as '
                   f'<select name="as" data-submit>{opts}</select></form>'
                   # One person on the desk: no menu and no name. The page is
                   # theirs; a placeholder name in the corner only looks odd.
                   if not self.cfg.solo else "")
            foot = ("Running on this machine only, with no sign-in. To put it behind your "
                    "company sign-in, see DEPLOY.md." if self.cfg.solo else
                    "Running on this machine only, with no sign-in: whoever opens it picks a "
                    "name. For a team, see DEPLOY.md.")
        else:
            who = f'<span class="who">Signed in as <b>{e(viewer)}</b></span>'
            foot = "Signed in through your company account."
        msg = (f'<div class="flash{" ok" if ok else ""}">{e(flash)}</div>' if flash else "")
        name = f"{title} · Hiring desk" if title else "Hiring desk"
        # The team's logo (desk.toml [team] logo, console/logo.py), as a data:
        # URI in an <img> -- never inlined markup, even for an SVG.
        from console import logo as _logo
        uri = _logo.load(desk.ROOT)[0]
        mark = (f'<img class="logo" src="{e(uri)}" alt="{e(self.cfg.company or "Company")} logo">'
                if uri else "<i></i>")
        # The logo already says the company's name: do not print it twice.
        named = "" if uri else f'<small>{e(self.cfg.company)}</small>'
        return security.bind_tokens(
                f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
                f'<meta name="viewport" content="width=device-width,initial-scale=1">'
                f'<title>{e(name)}</title><style>{CSS}</style></head><body>'
                f'<header><div class="bar"><div class="brand">{mark}Hiring desk'
                f'{named}</div><nav>{nav}</nav>{who}</div></header>'
                f'<main>{msg}{body}</main><footer>{foot}</footer>'
                f'<script>{JS}</script></body></html>', self.token, viewer)

    def _q(self, viewer: str, sep: str = "?") -> str:
        return f"{sep}as={quote(viewer)}" if self.access.mode == "pick" else ""

    def _person_link(self, person_id: str, viewer: str) -> str:
        return f"/person/{quote(person_id)}{self._q(viewer)}"

    # -- small parts ------------------------------------------------------

    def _note_count(self, s: Standing) -> str:
        n = sum(1 for ev in s.events if ev.kind == "noted")
        return f'<span class="count">{n} note{"s" if n != 1 else ""}</span>' if n else ""

    def thread(self, app: Application, s: Standing, viewer: str, back: str) -> str:
        """The notes on one application, and a box to add one."""
        items = []
        for n in notes(s, viewer, self.cfg):
            cls = "hid" if n.text is None else ""
            body = "hidden until you vote" if n.text is None else e(n.text)
            items.append(f'<div class="note {cls}"><div class="meta">{e(n.by)} · '
                         f'{e(n.at[:10])}</div><div class="body">{body}</div></div>')
        form = ""
        if s.state != "closed":
            form = (f'<form method="post" action="/note"><input type="hidden" name="t" '
                    f'value="{self.token}"><input type="hidden" name="as" value="{e(viewer)}">'
                    f'<input type="hidden" name="posting" value="{e(app.posting_id)}">'
                    f'<input type="hidden" name="candidate" value="{e(app.candidate_id)}">'
                    f'<input type="hidden" name="back" value="{e(back)}">'
                    f'<textarea name="text" maxlength="{NOTE_MAX}" placeholder="'
                    + ("A note to keep with this application" if self.cfg.solo else
                       "A note for the others: what would change your mind?")
                    + '" required></textarea>'
                    f'<button class="go">Post</button></form>')
        return (f'<div class="thread"><h4>Notes</h4>{"".join(items)}{form}</div>'
                if items or form else "")

    def doc_links(self, person: Person, posting_id: str, viewer: str) -> str:
        """CV, letter and profile, one click each. What a partner needs to class.

        The CV and the letter are always named, present or not: a missing
        letter is something to know before voting, and a button that is simply
        absent reads as "the page forgot", not as "they sent none".
        """
        out = []
        seen: set[str] = set()
        for d in person.documents_for(posting_id):
            if d.kind in seen:
                continue
            seen.add(d.kind)
            out.append(f'<a class="doc" href="/file/{quote(person.person_id)}/{quote(d.stored)}'
                       f'{self._q(viewer)}" target="_blank" rel="noopener" '
                       f'title="{e(d.original)}">{DOC_OPEN.get(d.kind, "Open file")}</a>')
        out += [f'<span class="doc none">{DOC_MISSING[k]}</span>'
                for k in DOC_MISSING if k not in seen]
        if security.href(person.link):
            name = "LinkedIn" if "linkedin.com" in person.link else "Profile"
            out.append(f'<a href="{e(security.href(person.link))}" target="_blank" rel="noopener noreferrer">'
                       f'{name}</a>')
        return f'<span class="docs">{"".join(out)}</span>' if out else ""

    def _dot(self, v: str, vs: dict[str, Any], viewer: str,
             shown: dict[str, Any]) -> tuple[str, str]:
        """(css class, what it says) for one voter's dot."""
        if v not in vs:
            return "none", "not yet"
        if v != viewer and shown.get(v) is None:
            return "hidden", "voted, hidden until you vote"
        return vs[v].meaning, desk._said(vs[v], self.cfg)

    def dots(self, vs: dict[str, Any], viewer: str) -> str:
        """One dot per voter, the viewer first. Colour only when it may be seen."""
        shown = visible_to(viewer, vs, self.cfg)
        out = []
        for v in [viewer] + [x for x in self.cfg.voters if x != viewer]:
            cls, said = self._dot(v, vs, viewer, shown)
            you = " you" if v == viewer else ""
            out.append(f'<span class="av {cls}{you}" title="{e(v)}: {e(said)}">'
                       f'{e(v[:1].upper())}</span>')
        return f'<div class="dots">{"".join(out)}</div>'

    def legend(self) -> str:
        from console import choices_web
        key = "".join(f'<span class="dec d-{m}">{e(self.cfg.label(m))}</span>'
                      for m in choices_web.shown(self.cfg))
        key += "".join(f'<span class="dec d-own">{e(x)}</span>' for x in choices_web.own(self.cfg))
        return (f'<div class="legend">{key}</div>'
                f'<div class="legend">AI guess: {e(AI_HINT)}. How it is counted is on the '
                f'AI guess tab.</div>')

    def vote_form(self, app: Application, vs: dict[str, Any], viewer: str,
                  back: str = "", s: Standing | None = None) -> str:
        cfg = self.cfg
        mine = vs.get(viewer)
        hidden = (f'<input type="hidden" name="t" value="{self.token}">'
                  f'<input type="hidden" name="as" value="{e(viewer)}">'
                  f'<input type="hidden" name="posting" value="{e(app.posting_id)}">'
                  f'<input type="hidden" name="candidate" value="{e(app.candidate_id)}">'
                  f'<input type="hidden" name="back" value="{e(back)}">')
        # One person sorting hundreds says no far more often than anything
        # else: for them a no is one click, and the comment box takes a reason.
        direct = cfg.solo and not cfg.pass_reason_required
        from console import choices_web
        kept = choices_web.own_of(s, viewer, cfg) if s is not None else ""
        buttons = []
        for m in choices_web.shown(cfg):
            cls = f"{m} mine" if mine is not None and mine.meaning == m and not kept else m
            # The pool takes no date unless the team asks for one: then one click.
            if (m == "later" and cfg.later_needs_date) or (m == "pass" and not direct):
                buttons.append(f'<button class="{cls}" data-open="{m}">{e(cfg.label(m))}'
                               f'</button>')
            else:
                buttons.append(f'<button class="{cls}" name="label" value="{m}">'
                               f'{e(cfg.label(m))}</button>')
        # The team's own choices, after the four: each keeps people aside in its tab.
        for i, x in enumerate(choices_web.own(cfg)):
            buttons.append(f'<button class="own{" mine" if x == kept else ""}" name="label" '
                           f'value="x:{i}">{e(x)}</button>')
        reasons = "".join(f"<option>{e(r)}</option>" for r in cfg.pass_reasons)
        # One comment box, beside the buttons, sent with whichever is pressed:
        # a second box in a panel would be a second field of the same name.
        comment = (f'<input type="text" class="cmt" name="comment" '
                   f'maxlength="{desk.COMMENT_MAX}" placeholder="Comment (optional, one line)" '
                   f'aria-label="Comment, optional">')
        later = (f'<div class="panel" data-for="later">Back on '
                 f'<input type="date" name="until">'
                 f'<button class="go" name="label" value="later">'
                 f'{e(cfg.label("later"))}</button></div>' if cfg.later_needs_date else "")
        passp = (f'<div class="panel" data-for="pass"><select name="reason">'
                 f'<option value="">reason (optional)</option>{reasons}</select>'
                 f'<button class="go" name="label" value="pass">{e(cfg.label("pass"))}</button>'
                 f'</div>')
        # Enter in the comment box presses the form's first button: here, one
        # that records nothing, rather than the first vote.
        return (f'<form method="post" action="/vote">{hidden}'
                f'<button hidden tabindex="-1" name="label" value=""></button>'
                f'<div class="votes">{"".join(buttons)}{comment}</div>{later}'
                f'{"" if direct else passp}</form>')

    def actions(self, app: Application, s: Standing, viewer: str, back: str) -> str:
        """The class buttons: open on the viewer's turn, folded away otherwise."""
        if s.state == "closed":
            return ""
        vs = votes(s)
        o = outcome(vs, self.cfg)
        form = self.vote_form(app, vs, viewer, back, s)
        if viewer not in vs and not o.settled:
            return f'<div class="act">{form}</div>'
        word = "decision" if self.cfg.solo else "vote"
        label = f"Change my {word}" if viewer in vs else "Vote anyway"
        yours = ""
        if viewer in vs:
            v = vs[viewer]
            from console import choices_web
            said = (choices_web.own_of(s, viewer, self.cfg) or self.cfg.label(v.meaning)) + (
                f" (until {v.until})" if v.until else "")
            why = ", ".join(x for x in (v.reason, f"“{v.comment}”" if v.comment else "") if x)
            yours = (f'<span class="yours">Your {word}: <b>{e(said)}</b>'
                     f'{", " + e(why) if why else ""} · </span>')
        return (f'<div class="act">{yours}<details class="change"><summary>{label}</summary>'
                f'{form}</details></div>')

    # -- the list ---------------------------------------------------------

    def applications(self, viewer: str, tab: str, now: datetime, q: str = "",
                     role: str = "", page: int = 1, since: int = 0, step: str = "") -> str:
        pipes, reg, titles = desk._pipelines(), desk.load_registry(desk._registry_path()), \
            desk._titles()
        role = role if role in titles or role in pipes else ""
        rows: dict[str, list[tuple]] = {t: [] for t in (*TABS, *_stages_web.tabs_for(self.cfg))}
        per_role: dict[str, int] = {}
        from console.list_web import PERIODS, period_filter, role_filter
        # The period is one of those offered, counted back from now, on the
        # day they applied. Anything else typed in the address is "any time".
        since = since if since in [n for n, _ in PERIODS] else 0
        oldest = now - timedelta(days=since) if since else None
        # Back to this very list after a vote: same tab, role, period and search.
        step = step if tab == "interested" and step in dict(STEPS_SHOWN) else ""
        where = f"/?tab={tab}" + (f"&role={quote(role)}" if role else "") + \
            (f"&since={since}" if since else "") + (f"&q={quote(q)}" if q else "") + \
            (f"&step={step}" if step else "")
        for pid, pipe in pipes.items():
            for cid in pipe.candidates:
                s = pipe.standing(cid)
                person = reg.person_of(cid) or Person(cid, cid)
                if q and not _matches(q, person, titles.get(pid, pid)):
                    continue
                if oldest is not None:
                    # An application with no arrival on record has no day to
                    # be in the period by: it shows under "Any time" only.
                    arrived = _received(s)
                    if arrived is None or arrived < oldest:
                        continue
                st = _stages.derive(s, self.cfg, now)
                t = _stages_web.tab_of(st, s, viewer, self.cfg)
                if t != "closed":
                    per_role[pid] = per_role.get(pid, 0) + 1
                if role and pid != role:
                    continue
                nxt = _stages.next_step(s, self.cfg, now)
                mine = t != "closed" and (_needs(nxt, viewer) or st.id == "team_to_decide")
                got = _received(s)
                # Yours first, then by fit: the order a partner reads in. Where
                # the fit cannot tell two people apart, the list says so below.
                seen = _match.of(person, pid)
                key = (not mine, -(seen.total if seen is not None else -1),
                       -(got.timestamp() if got else 0))
                item = (key, pid, cid, person, s, st, nxt, mine)
                rows[t].append(item)
                # "All" is everyone, the closed ones too: the whole history,
                # with what the viewer decided in its colour on each row.
                rows["all"].append(item)
        names = _stages_web.tab_names(self.cfg)
        qs = (f"&q={quote(q)}" if q else "") + (f"&role={quote(role)}" if role else "") + \
            (f"&since={since}" if since else "")
        # One person deciding never waits on anyone: that tab would stay empty.
        tabs = "".join(
            f'<a class="{"on" if t == tab else ""}" href="/?tab={t}{qs}{self._q(viewer, "&")}">'
            f'{e(names[t])}<em>{len(rows[t])}</em></a>' for t in _stages_web.tabs_for(self.cfg)
            if not (self.cfg.solo and t == "waiting"))
        # Only the page shown is drawn: with hundreds of applications, drawing
        # every row of every tab is what makes a list slow.
        # On Interested, the step they are at: who was contacted, who replied...
        chips = ""
        if tab == "interested":
            here = list(rows[tab])
            counted = {k: sum(1 for it in here if _step_group(it[5]) == k)
                       for k, _ in STEPS_SHOWN}
            counted[""] = len(here)
            base = ("/?tab=interested" + (f"&role={quote(role)}" if role else "")
                    + (f"&since={since}" if since else "") + (f"&q={quote(q)}" if q else ""))
            chips = '<div class="steps-f">' + "".join(
                f'<a class="{"on" if k == step else ""}" href="{base}{f"&step={k}" if k else ""}'
                f'{self._q(viewer, "&")}">{e(w)}<em>{counted[k]}</em></a>'
                for k, w in STEPS_SHOWN) + "</div>"
            if step:
                rows[tab] = [it for it in here if _step_group(it[5]) == step]
        found_rows = sorted(rows[tab], key=lambda it: it[0])
        pages = max(1, -(-len(found_rows) // PAGE))
        page = min(max(page, 1), pages)
        here = where + (f"&page={page}" if page > 1 else "")
        shown = found_rows[(page - 1) * PAGE:page * PAGE]
        # The tools to answer several at once belong to the tab of answers to
        # write, not to every list a "no" appears in.
        to_answer = (sum(1 for it in shown if it[5].id == "to_answer_no")
                     if tab in ("write", "no") else 0)
        drawn = []
        for key, pid, cid, person, s, st, nxt, mine in shown:
            html_row = self.row(person, Application(pid, cid), s, viewer, titles, now,
                                nxt=nxt, mine=mine, pipes=pipes, st=st, back=here,
                                tick=st.id == "to_answer_no" and to_answer > 1)
            # The number is a count of words (match.py): the same documents
            # always give the same number, so there is no run-to-run drift to
            # group neighbours by. `_drift` belongs to the model screener.
            drawn.append((key, html_row, f"{pid}/{cid}", None))
        ordered = _banded(drawn)
        batch = _batch_web.bar(self, viewer, here, to_answer) if to_answer > 1 else ""
        if tab == "interested":
            # The people waited on, asked about in one click when Outlook is on.
            batch = _outlook_web.bar(self, viewer, here) + batch
        turn = _batch_web.pager(
            lambda n: f"{where}{f'&page={n}' if n > 1 else ''}{self._q(viewer, '&')}",
            page, pages, len(found_rows), PAGE)
        body = (f'{batch}<div class="list">{"".join(ordered)}</div>{turn}' if ordered else
                f'<div class="list"><div class="empty">{_empty(tab, q, self.cfg.solo)}</div>'
                f'</div>')
        roles = "".join(f'<option value="{e(pid)}">{e(t or pid)}</option>'
                        for pid, t in sorted(titles.items(), key=lambda kv: kv[1]))
        accept = ",".join(sorted(DOC_TYPES))
        add = (f'<details class="add"><summary>+ Add an application by hand</summary>'
               f'<form method="post" action="/add" enctype="multipart/form-data" '
               f'class="panel open">'
               f'<input type="hidden" name="t" value="{self.token}">'
               f'<input type="hidden" name="as" value="{e(viewer)}">'
               f'<input type="text" name="name" placeholder="First and last name" required>'
               f'<input type="email" name="email" placeholder="email (optional)">'
               f'<textarea name="link" rows="2" placeholder="Links: LinkedIn, GitHub, a demo -- '
               f'one per line (optional)"></textarea>'
               f'<select name="posting">{roles}</select>'
               f'<label class="sub">CV <input type="file" name="cv" accept="{accept}"></label>'
               f'<label class="sub">“Why us” answer <input type="file" name="letter" '
               f'accept="{accept}"></label>'
               f'<button class="go">Add</button></form></details>')
        who = (f'<input type="hidden" name="as" value="{e(viewer)}">'
               if self.access.mode == "pick" else "")
        keep = f'<input type="hidden" name="role" value="{e(role)}">' if role else ""
        keep += f'<input type="hidden" name="since" value="{since}">' if since else ""
        search = (f'<form method="get" class="search">{who}'
                  f'<input type="hidden" name="tab" value="all">{keep}'
                  f'<input type="search" name="q" value="{e(q)}" '
                  f'placeholder="Search name, email, role" aria-label="Search"></form>')
        found = (f'<div class="sub found">Showing matches for '
                 f'&ldquo;{e(q)}&rdquo; · <a href="/{self._q(viewer)}">clear</a></div>'
                 if q else "")
        rf = (role_filter(self, viewer, tab, role, q, titles, per_role, since)
              + period_filter(self, viewer, tab, role, q, since))
        # No strip of big numbers: the tabs carry the counts, once.
        return (f'<div class="tools">{rf}{search}</div>'
                f'{found}<div class="tabs">{tabs}</div>{chips}{body}{add}{self.legend()}')

    def signals(self, person: Person, app: Application, *, full: bool = False) -> str:
        """Built, AI in their work, wants to own, current role: see console/card_web.py."""
        from console.card_web import card
        from signals import signals
        return card(signals(person, app.candidate_id, app.posting_id), full=full)

    def why(self, app: Application, *, full: bool = False) -> str:
        """The line under a name: what is strong, what is open, what to ask."""
        from triage import brief
        b = brief(app.candidate_id, app.posting_id)
        if b is None:
            return ""
        bits = []
        for lab, claim in (b.strong if full else b.strong[:1]):
            bits.append(f"<b>{e(lab)}</b>" + (f" <q>{e(claim)}</q>" if claim else ""))
        if b.only_languages:
            bits.append("strong on languages only")
        elif not b.strong:
            bits.append("nothing strong on paper")
        for g in b.open_gates:
            bits.append(f'<span class="gate">{e(g)}: unanswered, and it is a condition</span>')
        if b.silent >= 0.25:
            bits.append(f"the paper is silent on {b.silent:.0%} of the posting")
        ask = f'<span class="ask">{e(b.ask_first)}</span>' if b.ask_first else ""
        return f'<div class="brief">{" · ".join(bits)}{ask}</div>'

    # -- one person, several applications ---------------------------------

    def _multi(self, person: Person, viewer: str) -> str:
        """A badge when this person has applied more than once, to their one page."""
        n = len(person.applications)
        if n < 2:
            return ""
        return (f'<a class="multi" href="{self._person_link(person.person_id, viewer)}" '
                f'title="One person, one page: every application is on it">'
                f'{n} applications</a>')

    def also(self, person: Person, app: Application, titles: dict[str, str],
             pipes: dict[str, Any] | None = None) -> list[tuple[str, str, str]]:
        """The person's other applications: (posting id, title, date applied).

        Read from the registry, not guessed from names: the registry is where a
        human confirmed that two applications are one person.
        """
        others = [a for a in person.applications if a.key() != app.key()]
        if not others:
            return []
        pipes = desk._pipelines() if pipes is None else pipes
        out = []
        for a in others:
            pipe = pipes.get(a.posting_id)
            got = (_received(pipe.standing(a.candidate_id))
                   if pipe is not None and a.candidate_id in pipe.candidates else None)
            out.append((a.posting_id, titles.get(a.posting_id, a.posting_id),
                        got.date().isoformat() if got else "a date not recorded"))
        return out

    def _also_line(self, person: Person, app: Application, viewer: str,
                   titles: dict[str, str], pipes: dict[str, Any] | None = None,
                   *, here: bool = False) -> str:
        """"Also applied to <role> on <date>", each one a link to that card."""
        items = []
        for pid, title, on in self.also(person, app, titles, pipes):
            href = (f"#app-{quote(pid)}" if here else
                    f"{self._person_link(person.person_id, viewer)}#app-{quote(pid)}")
            items.append(f'Also applied to <a href="{e(href)}">{e(title)}</a> on {e(on)}')
        return f'<div class="also">{" · ".join(items)}</div>' if items else ""

    def row(self, person: Person, app: Application, s: Standing, viewer: str,
            titles: dict[str, str], now: datetime, *, nxt: str = "", mine: bool = False,
            back: str = "", pipes: dict[str, Any] | None = None,
            st: Any = None, tick: bool = False) -> str:
        """One line per application: see console/list_web.py for what is on it, and why."""
        from console import list_web
        return list_web.row(self, person, app, s, viewer, titles, now, mine=mine, back=back,
                            st=st, tick=tick)

    # -- one person -------------------------------------------------------

    def person(self, viewer: str, person_id: str, now: datetime) -> str | None:
        """"More details": one person, every application they made, then their history."""
        pipes, reg, titles = desk._pipelines(), desk.load_registry(desk._registry_path()), \
            desk._titles()
        p = reg.people.get(person_id)
        if p is None:
            return None
        roles = ", ".join(titles.get(a.posting_id, a.posting_id) for a in p.applications)
        n = len(p.applications)
        count = (f' · <span class="multi">{n} applications, one page</span>' if n > 1 else "")
        out = [f'<a class="back" href="/{self._q(viewer)}">&larr; At the door</a>'
               f'<h1>{e(p.name)}</h1><div class="sub">'
               f'{e(p.email) if p.email else "no email on file"} · {e(roles)}{count}</div>']
        for app in p.applications:
            pipe = pipes.get(app.posting_id)
            if pipe is not None:
                out.append(self.application_card(p, app, pipe.standing(app.candidate_id),
                                                 viewer, titles, now, pipes=pipes))
        out.append('<h2>History</h2><ol class="tl">')
        for m in timeline(p, pipes, self.cfg, viewer, titles):
            d = f' <span class="d">· {e(m.detail)}</span>' if m.detail else ""
            role = (f'<div class="r">{e(titles.get(m.posting_id, m.posting_id))}</div>'
                    if len(p.applications) > 1 else "")
            out.append(f'<li><time>{e(m.at[:10])}</time><div>{e(m.text)}{d}{role}</div></li>')
        out.append("</ol>")
        out.append(_erase_web.section(self, p, viewer))
        return "".join(out)

    def vote_table(self, s: Standing, viewer: str) -> str:
        """Each voter's vote and comment, the viewer first; hidden as the blind rule says."""
        cfg = self.cfg
        vs = votes(s)
        shown = visible_to(viewer, vs, cfg)
        until = "you vote" if cfg.blind == "until_you_vote" else "everyone has voted"
        rows = []
        for v in [viewer] + [x for x in cfg.voters if x != viewer]:
            who = "You" if v == viewer else e(v)
            if v not in vs:
                rows.append(f'<tr><th>{who}</th><td class="v none" colspan="2">not voted yet'
                            f'</td></tr>')
                continue
            if v != viewer and shown.get(v) is None:
                rows.append(f'<tr><th>{who}</th><td class="v hid" colspan="2">voted, hidden '
                            f'until {until}</td></tr>')
                continue
            vote = vs[v]
            label = cfg.label(vote.meaning) + (f" (until {vote.until})" if vote.until else "")
            why = ", ".join(x for x in (vote.reason, vote.comment) if x)
            said = e(why) if why else '<span class="none">no comment</span>'
            rows.append(f'<tr><th>{who}</th><td class="v">'
                        f'{e(label)} <small>{e(vote.at[:10])}</small></td><td>{said}</td></tr>')
        return f'<table class="vt">{"".join(rows)}</table>'

    def documents(self, p: Person, app: Application, viewer: str,
                  titles: dict[str, str]) -> str:
        """The files and every link they gave, each one click; then a way to attach one."""
        docs = []
        kinds = set()
        for d in p.documents_for(app.posting_id):
            kinds.add(d.kind)
            label = DOC_OPEN.get(d.kind, "Open file")
            where = ("" if d.posting_id == app.posting_id else
                     f' <small>sent for {e(titles.get(d.posting_id, d.posting_id))}</small>')
            docs.append(f'<span><a class="doc" href="/file/{quote(p.person_id)}/'
                        f'{quote(d.stored)}{self._q(viewer)}" target="_blank" rel="noopener" '
                        f'title="{e(d.original)}">{label}</a> <small>{e(d.original)}</small>'
                        f'{where}</span>')
        docs += [f'<span class="doc none">{DOC_MISSING[k]}</span>'
                 for k in DOC_MISSING if k not in kinds]
        from signals import signals
        for link in signals(p, app.candidate_id, app.posting_id).links:
            if security.href(link.url):
                docs.append(f'<a href="{e(security.href(link.url))}" target="_blank" '
                            f'rel="noopener noreferrer">{e(link.url)}</a>')
        accept = ",".join(sorted(DOC_TYPES))
        upload = (f'<details class="up"><summary>+ attach a file</summary>'
                  f'<form method="post" action="/attach" enctype="multipart/form-data" '
                  f'class="panel open"><input type="hidden" name="t" value="{self.token}">'
                  f'<input type="hidden" name="as" value="{e(viewer)}">'
                  f'<input type="hidden" name="person" value="{e(p.person_id)}">'
                  f'<input type="hidden" name="posting" value="{e(app.posting_id)}">'
                  f'<select name="kind"><option value="cv">CV</option>'
                  f'<option value="letter">“Why us” answer</option><option value="other">Other</option>'
                  f'</select><input type="file" name="file" accept="{accept}" required>'
                  f'<button class="go">Attach</button></form></details>')
        return f'<div class="doclist">{"".join(docs)}{upload}</div>'

    def application_card(self, p: Person, app: Application, s: Standing, viewer: str,
                         titles: dict[str, str], now: datetime,
                         pipes: dict[str, Any] | None = None) -> str:
        """One application on "More details", in the order a partner uses it.

        Where it stands and what to do first; then the documents; then what
        each partner voted and wrote (hidden until the reader has voted);
        then the candidate's own sentences; then the trial day. The dated
        history closes the page, after every application.
        """
        cfg = self.cfg
        vs = votes(s)
        o = outcome(vs, cfg)
        nxt = _stages.next_step(s, cfg, now)
        back = f"/person/{quote(p.person_id)}"
        seen = _match.of(p, app.posting_id)
        fit = "none" if seen is None else f"{seen.total:.0%}"
        buttons = []
        mailbox = ""
        if (s.state != "closed" and o.status == "agreed" and o.meaning == "later"
                and nxt.startswith("mail to send")):
            # A yes or a no is written from the state above. The "later" mail
            # keeps its own button: it is sent, and nothing moves.
            mailbox = self.mailbox(p, app, o.meaning, viewer)
            buttons.append(self._mark(app, viewer, "sent", "I sent it"))
        # A reply is a step now ("They replied: OK", in the state above): a
        # second button saying the same would record the same.
        why = self._explain(app)
        hint = (f' <span class="aihint">{e(AI_HINT)}</span>' if seen is not None else "")
        blind = ""
        if viewer not in vs and cfg.blind == "until_you_vote" and len(cfg.voters) > 1:
            blind = ('<div class="sub">The others&#x27; votes and comments show once you have '
                     'voted.</div>')
        if cfg.solo:
            return self._solo_card(p, app, s, viewer, titles, now, pipes, fit, why)
        top = _stages_web.section(self, app, s, viewer, back, now,
                                  votes_html=self.actions(app, s, viewer, back))
        return (f'<div class="card" id="app-{e(app.posting_id)}"><div class="top"><div><h3>'
                f'{e(titles.get(app.posting_id, app.posting_id))}</h3><div class="sub">'
                f'{e(_days_ago(s, now))} · <span class="fit" title="{e(AI_HINT)}">AI guess <b>{fit}</b></span>{hint}'
                f'</div>{self._also_line(p, app, viewer, titles, pipes, here=True)}</div></div>'
                f'{top}{mailbox}'
                + (f'<div class="buttons">{"".join(buttons)}</div>' if buttons else "")
                + f'<h2>Documents and links</h2>{self.documents(p, app, viewer, titles)}'
                + f'<h2>{"Your decision" if cfg.solo else "Votes and comments"}</h2>'
                + f'{blind}{self.vote_table(s, viewer)}'
                + self.thread(app, s, viewer, back)
                + f'<h2>In their own words</h2>{self.signals(p, app, full=True)}'
                + (f'<details class="why"><summary>The optional model reading of the paper (not the number above)</summary>'
                   f'{self.why(app, full=True)}<pre>{e(why)}</pre></details>' if why else "")
                + "</div>")

    def _decide_here(self, app: Application, s: Standing, viewer: str, back: str) -> str:
        """On the person's page: the decision buttons always open, with a comment.

        Deciding, or changing one's mind, without going back to the list.
        """
        if s.state == "closed":
            return ""
        vs = votes(s)
        mine = vs.get(viewer)
        from console import choices_web
        said = ''
        if mine is not None:
            name = choices_web.own_of(s, viewer, self.cfg) or self.cfg.label(mine.meaning)
            why = f", “{mine.comment}”" if mine.comment else ""
            said = (f'<span class="yours">Your decision: <b>{e(name)}</b>{e(why)}. '
                    f'Change it here:</span>')
        return f'<div class="act here">{said}{self.vote_form(app, vs, viewer, back, s)}</div>'

    def _solo_card(self, p: Person, app: Application, s: Standing, viewer: str,
                   titles: dict[str, str], now: datetime, pipes: dict[str, Any] | None,
                   fit: str, why: str) -> str:
        """One application on "More details", for one person deciding: short.

        The same row as on the list, in full: the way through with the one
        thing to do now, every note, then the documents and their own words.
        No table of a single vote, no second form saying what the first says.
        """
        cfg = self.cfg
        back = f"/person/{quote(p.person_id)}"
        st = _stages.derive(s, cfg, now)
        from console import track_web
        from console.list_web import state_text
        if track_web.followed(st):
            top = track_web.tracker(self, app, s, st, viewer, back, now, every=True)
            notes = ""
        elif st.id == "to_answer_no":
            top = track_web.answer(self, app, st, viewer, back)
            notes = self.thread(app, s, viewer, back)
        else:
            # To sort, to think about, in the pool, closed: where it stands, and
            # the step a person can record from there (back from the pool...).
            top = (f'<div class="stg"><div class="nxt"><b>{e(state_text(st, s, cfg))}</b></div>'
                   f'{_stages_web._form(self, app, st, viewer, back, now)}</div>')
            o = outcome(votes(s), cfg)
            if (s.state != "closed" and o.status == "agreed" and o.meaning == "later"
                    and _stages.next_step(s, cfg, now).startswith("mail to send")):
                # Kept in the pool: the "not now, and not no" mail, then "I sent it".
                top += (self.mailbox(p, app, "later", viewer) + '<div class="buttons">'
                        + self._mark(app, viewer, "sent", "I sent it") + "</div>")
            notes = self.thread(app, s, viewer, back)
        return (f'<div class="card" id="app-{e(app.posting_id)}"><div class="top"><div><h3>'
                f'{e(titles.get(app.posting_id, app.posting_id))}</h3><div class="sub">'
                f'{e(_days_ago(s, now))} · <span class="fit" title="{e(AI_HINT)}">AI guess '
                f'<b>{fit}</b></span></div>'
                f'{self._also_line(p, app, viewer, titles, pipes, here=True)}</div></div>'
                f'{self._decide_here(app, s, viewer, back)}{top}'
                f'<h2>Documents and links</h2>{self.documents(p, app, viewer, titles)}'
                f'{notes}'
                f'<h2>In their own words</h2>{self.signals(p, app, full=True)}'
                + (f'<details class="why"><summary>The optional model reading of the paper '
                   f'(not the number above)</summary>{self.why(app, full=True)}'
                   f'<pre>{e(why)}</pre></details>' if why else "")
                + "</div>")

    def _mark(self, app: Application, viewer: str, what: str, label: str) -> str:
        return (f'<form method="post" action="/mark" class="inline">'
                f'<input type="hidden" name="t" value="{self.token}">'
                f'<input type="hidden" name="as" value="{e(viewer)}">'
                f'<input type="hidden" name="posting" value="{e(app.posting_id)}">'
                f'<input type="hidden" name="candidate" value="{e(app.candidate_id)}">'
                f'<input type="hidden" name="what" value="{what}">'
                f'<button>{e(label)}</button></form>')

    def _explain(self, app: Application) -> str:
        from screen import explain, load as load_screening
        from triage import _screening_path
        f = _screening_path(app.candidate_id, app.posting_id)
        return explain(load_screening(f)) if f.exists() else ""

    # -- settings ---------------------------------------------------------

    #: One mail per outcome. No "we have your application": the applicant
    #: tracking system already sends it, and two would reach the candidate.
    #: Each meeting has its own invitation, with {day} and {time}; only the
    #: interviews the team runs are shown (`mails`). Every one is read back from
    #: the form, so lowering the number of interviews loses no one's words.
    MAILS = (("contact", None),
             ("follow_up", "When someone you wrote to has not replied"),
             ("interview", "The invitation to an interview: the same words for each, "
                           "{interview} says which"),
             ("trial_day", "After the last interview: the invitation to the trial day"),
             ("later", None), ("pass", None))

    def mails(self) -> list[tuple[str, str | None]]:
        """The mails on House rules: every interview shares one invitation."""
        return list(self.MAILS)

    def settings(self, viewer: str) -> str:
        """Every word the desk says and every rule it follows, theirs to change."""
        cfg = self.cfg
        base = desk.load_config(overrides=False)
        tok = (f'<input type="hidden" name="t" value="{self.token}">'
               f'<input type="hidden" name="as" value="{e(viewer)}">')

        def text(name: str, value: str, placeholder: str) -> str:
            return (f'<input type="text" name="{name}" value="{e(value)}" '
                    f'placeholder="{e(placeholder)}">')

        def pick(name: str, value: str, options: tuple[tuple[str, str], ...]) -> str:
            return (f'<select name="{name}">' + "".join(
                f'<option value="{k}"{" selected" if k == value else ""}>{e(v)}</option>'
                for k, v in options) + '</select>')

        def use(m: str) -> str:
            # One person deciding may switch "not sure" and "the pool" off.
            if not cfg.solo or m not in ("discuss", "later"):
                return ""
            on = m not in cfg.choices_off
            return (f'<label class="use"><input type="checkbox" name="usechoice_{m}" '
                    f'value="1"{" checked" if on else ""}> offer this choice</label>')
        buttons = "".join(
            f'<div><label class="f">{e(what)}</label>'
            f'{text("label_" + m, cfg.labels[m] if cfg.labels[m] != base.labels[m] else "", base.labels[m])}'
            f'{use(m)}</div>'
            for m, what in (("contact", "Yes: get in touch"),
                            ("discuss", "Not sure: look again later" if cfg.solo else
                             "Not sure: the team decides together"),
                            ("later", "Keep aside, in the pool" if cfg.solo else
                             "Later: talk again on a date"), ("pass", "No")))
        if cfg.solo:
            buttons += (
                f'<div><label class="f">Your own choices, one per line</label>'
                f'<textarea name="extra_choices" class="short" placeholder="Another role&#10;'
                f'Next year">{e(chr(10).join(cfg.extra_choices))}</textarea>'
                f'<div class="sub">Each one keeps people aside in a tab of its own, as the '
                f'pool does. Remove a line to remove the choice: the people in it go back '
                f'to the pool.</div></div>')
        chips = "".join(f'<button data-f="{f}">{f.replace("_", " ")}</button>'
                        for f in desk.FIELDS)
        mails = []
        for m, title in self.mails():
            title = title or (f"When you decide “{cfg.label(m)}”" if cfg.solo else
                              f"When you agree on “{cfg.label(m)}”")
            used = m not in cfg.mails_off
            mails.append(
                f'<div class="mail{"" if used else " off"}"><h3>{e(title)} '
                f'<span class="tag"></span></h3>'
                f'<label class="use"><input type="checkbox" name="use_{m}" value="1"'
                f'{" checked" if used else ""}> Use this mail (untick: it is never '
                f'offered)</label>'
                f'<label class="f">Subject</label>'
                f'<input type="text" data-mail name="subject_{m}" '
                f'value="{e(cfg.subjects.get(m, ""))}" placeholder="{e(base.example_subjects.get(m, ""))}">'
                f'<label class="f">Mail</label>'
                f'<textarea data-mail name="template_{m}" '
                f'placeholder="{e(base.example_templates.get(m, ""))}">'
                f'{e(cfg.templates.get(m, ""))}</textarea>'
                f'<div class="fields">{chips}</div>'
                f'<div class="preview"></div></div>')
        sample = {"first_name": "Pablo", "last_name": "García", "role": "Founders' Associate",
                  "interview": "second interview", "day": "Tuesday 14 October",
                  "time": "10:30",
                  "company": cfg.company or "your company", "sender": cfg.sender or cfg.company}
        import json as _json
        between = (
            f'<div><label class="f">When a voter sees the others\' votes</label>'
            f'{pick("blind", cfg.blind, (("until_you_vote", "after casting their own"), ("until_all_voted", "once everyone has voted"), ("off", "straight away")))}</div>'
            f'<div><label class="f">One partner may go ahead without waiting</label>'
            f'{pick("go_ahead_alone", cfg.go_ahead_alone, (("after_one_yes", "after their own yes"), ("all_voted", "only once everyone has voted"), ("owner", "only the people who move steps")))}</div>'
            f'<div><label class="f">When a decision is settled</label>'
            f'{pick("agreement", cfg.agreement, (("unanimous", "everyone agrees"), ("majority", "more than half agree"), ("any_contact", "one yes is enough to meet them")))}</div>')
        if cfg.solo:
            # One person decides: the rules between voters have nobody to rule.
            # They are kept as they are, unseen, for the day a second name is added.
            between = "".join(f'<input type="hidden" name="{k}" value="{e(v)}">' for k, v in (
                ("blind", cfg.blind), ("go_ahead_alone", cfg.go_ahead_alone),
                ("agreement", cfg.agreement)))
        team = (
            f'<div class="two"><div><label class="f">'
            f'{"Who decides" if cfg.solo else "Who votes, comma-separated"}</label>'
            f'{text("voters", ", ".join(cfg.voters), ", ".join(base.voters))}</div>'
            f'<div><label class="f">Signature at the end of every mail</label>'
            f'{text("sender", cfg.sender, base.sender or "e.g. Ana, for the team")}</div>'
            f'{between}'
            f'<div><label class="f">Recap day</label>'
            f'{pick("recap_weekday", cfg.recap_weekday, tuple((w, w.title()) for w in desk.WEEKDAYS))}</div>'
            f'<div><label class="f">Company name in mails</label>'
            f'{text("company", cfg.company, base.company)}</div>'
            f'{_outlook_web.switch(cfg)}</div>'
            # One person deciding says no in one click: no list of reasons to pick from.
            + ("" if cfg.solo and not cfg.pass_reason_required else
               f'<label class="f">One-click reasons for &ldquo;{e(cfg.label("pass"))}&rdquo;, one per line</label>'
               f'<textarea name="pass_reasons" class="short" '
               f'placeholder="{e(chr(10).join(base.pass_reasons))}">'
               f'{e(chr(10).join(cfg.pass_reasons) if cfg.pass_reasons != base.pass_reasons else "")}'
               f'</textarea>'))
        solo_keep = "".join(
            f'<input type="hidden" name="{k}" value="{e(v)}">' for k, v in (
                ("voters", ", ".join(cfg.voters)), ("sender", cfg.sender),
                ("recap_weekday", cfg.recap_weekday), ("company", cfg.company))) + between
        import stages
        sbase, sdesc = stages.names(base), stages.names(cfg)
        order = ("first", "second", "third", "fourth", "fifth", "sixth")
        talks = tuple((r, f"The {order[i]} interview") for i, r in enumerate(stages.rounds(cfg)))
        steps = (f'<div><label class="f">Interviews before the trial day</label>'
                 + pick("interviews", str(cfg.interviews),
                        tuple((str(i), str(i)) for i in range(1, len(stages.ROUNDS) + 1)))
                 + '</div>')
        steps += "".join(
            f'<div><label class="f">{e(what)}</label>'
            f'{text("state_" + s, sdesc[s] if sdesc[s] != sbase[s] else "", sbase[s])}</div>'
            for s, what in ((
                # One person deciding: no votes coming in, nothing for a team.
                ("new", "Not sorted yet"), ("team_to_decide", "You were not sure"),
                ("to_contact", "You said yes: to write to"),
                ("contacted", "You wrote to them"),
                ("replied", "They replied with an OK"),
                *talks,
                ("trial_day", "The trial day: the last step"),
                ("to_answer_no", "You said no: to answer"),
                ("answered_no", "The no was sent"),
                ("talk_later", "Talk again on a date"),
                ("withdrew", "They withdrew")) if cfg.solo else (
                ("new", "Nobody has voted yet"), ("votes_in", "The votes are coming in"),
                ("team_to_decide", "The votes differ, or someone is unsure"),
                ("to_contact", "The votes said yes: someone writes"),
                ("contacted", "Someone wrote to them"),
                ("replied", "They replied with an OK"),
                *talks,
                ("trial_day", "The trial day: the last step"),
                ("to_answer_no", "The votes said no: someone writes"),
                ("answered_no", "The no was sent"),
                ("talk_later", "Talk again on a date"),
                ("withdrew", "They withdrew"))))
        # One person on the desk moves every step: the question is not asked.
        steps += (f'<input type="hidden" name="advancers" value="{e(", ".join(cfg.advancers))}">'
                  if cfg.solo else
                  f'<div><label class="f">Who can move a step, comma-separated (empty: any '
                  f'voter)</label>{text("advancers", ", ".join(cfg.advancers), "any voter")}'
                  f'</div>')
        # Short choices first, the three long mails last: a partner changing
        # who votes should not have to scroll past every template to find it.
        return (f'<div id="sample" hidden data-sample="{e(_json.dumps(sample))}"></div>'
                f'<h1>House rules</h1>{_setup_web._tools(self, viewer, "rules")}'
                f'<p class="lead">Every word the desk says and every rule '
                f'it follows. Grey text is the shipped example, used until you write your '
                f'own.</p>'
                f'<form method="post" action="/settings">{tok}'
                # One person deciding: who decides, the signature and the company are
                # kept as they are, unseen; nobody needs to read them each time.
                + (solo_keep if cfg.solo else
                   f'<div class="set"><h2>Team and rules</h2><p class="lead">Who votes, when '
                   f'they see each other’s votes, and when a decision counts as made.</p>'
                   f'{team}</div>')
                + f'<div class="set"><h2>{"Your choices" if cfg.solo else "The four choices"}'
                f'</h2><p class="lead">What the {"" if cfg.solo else "vote "}'
                f'buttons are called, everywhere on the desk. Leave one empty to keep the '
                f'grey name.{" Yes and No are always there." if cfg.solo else ""}</p>'
                f'<div class="two">{buttons}</div></div>'
                + (f'<div class="set"><h2>The steps after the decision</h2><p class="lead">'
                   f'Where an application stands: how many interviews come before the trial '
                   f'day, and what each state is called. Your decision only ever makes a step '
                   f'to do; every step after that is recorded by a person, with a date. Leave '
                   f'a name empty to keep the grey one.</p>'
                   if cfg.solo else
                   f'<div class="set"><h2>The steps after the vote</h2><p class="lead">Where an '
                   f'application stands, apart from the votes: what each state is called, and '
                   f'who moves it on. The votes only ever reach a step to do; every step after '
                   f'that is recorded by a person, with a date. Leave a name empty to keep the '
                   f'grey one.</p>')
                + f'<div class="two">{steps}</div></div>'
                f'<div class="set"><h2>Mails</h2><p class="lead">Fields in braces are filled '
                f'in per person; the preview shows them for a made-up Pablo García. Nothing '
                f'is ever sent from here: each mail opens in your own mail app, from your '
                f'own address.</p>{"".join(mails)}</div>'
                + (f'<div class="set"><h2>Options</h2><div class="two">'
                   f'{_outlook_web.switch(cfg)}</div></div>' if cfg.solo else "")
                + f'<div class="save"><button>Save</button><span class="sub">Saved changes '
                f'apply straight away, for everyone, and are kept with who made them.</span>'
                f'</div></form>{_outlook_web.section(self, viewer)}')

    def mailbox(self, p: Person, app: Application, meaning: str, viewer: str) -> str:
        """The mail for a settled class: open it in your own mail app, or copy it."""
        try:
            m, fname = desk.draft_for(app.candidate_id, app.posting_id, "", self.cfg)
        except DeskError as err:
            return f'<div class="mailbox"><span class="sub">{e(str(err))}</span></div>'
        subject, body, to = str(m["Subject"]), m.get_content(), str(m["To"] or "")
        href = (f'mailto:{quote(to)}?subject={quote(subject)}&body={quote(body)}')
        ex = ('<div class="ex">This is the example text, not yours yet. Write your own under '
              f'<a href="/settings{self._q(viewer)}">House rules</a>.</div>'
              if self.cfg.is_example(meaning) else "")
        no_to = "" if to else '<div class="ex">No email on file: add the address in your mail app.</div>'
        back = f"/person/{quote(p.person_id)}"
        return (_drafting_web.box(self, app, viewer, to)
                + f'<div class="mailbox"><div class="subj">{e(subject)}</div><pre>{e(body)}</pre>'
                f'{ex}{no_to}<div class="buttons">'
                f'<a class="primary" href="{e(href)}">Open in my mail</a>'
                f'<button data-copy="{e(subject + chr(10) + chr(10) + body)}">Copy the text</button>'
                f'<a href="/draft?posting={quote(app.posting_id)}&candidate={quote(app.candidate_id)}'
                f'{self._q(viewer, "&")}">.eml</a>'
                f'{_drafting_web.button(self, app, viewer, back)}</div></div>')

    # -- recap ------------------------------------------------------------

    def recap(self, viewer: str, now: datetime) -> str:
        cfg = self.cfg
        reg = desk.load_registry(desk._registry_path())
        r = desk.recap(desk._pipelines(), reg, cfg, now=now, viewer=viewer,
                       titles=desk._titles())
        by_name: dict[str, list[str]] = {}
        for p in reg.people.values():
            by_name.setdefault(p.name, []).append(p.person_id)

        def names(ns: list[str]) -> str:
            out = []
            for n in ns:
                ids = by_name.get(n, [])
                out.append(f'<a href="{self._person_link(ids[0], viewer)}">{e(n)}</a>'
                           if len(ids) == 1 else e(n))
            return f'<span class="names">{", ".join(out)}</span>'

        you, team, week = [], [], []
        for voter, items in r.awaiting.items():
            whose = "your" if voter == viewer else f"{e(voter)}'s"
            line = (f"<b>{len(items)}</b> waiting for {whose} vote, up to "
                    f"{max(d for _, d in items):.0f} days: {names([n for n, _ in items])}")
            (you if voter == viewer else team).append(line)
        if r.drafts_waiting:
            you.append(f"<b>{len(r.drafts_waiting)}</b> mail(s) to send: "
                       f"{names(r.drafts_waiting)}")
        for n, on, why in r.wake:
            you.append(f"Wake-up: {names([n])}, back on {e(on)} -- &ldquo;{e(why)}&rdquo;")
        if r.disagreed:
            team.append(f"<b>{len(r.disagreed)}</b> to settle together: {names(r.disagreed)}")
        for n, days in r.no_reply:
            team.append(f"{names([n])}: written to {days:.0f} days ago, no reply")
        total = sum(len(v) for v in r.new.values())
        week.append(f"<b>{total}</b> new application(s)"
                    + (" -- " + ", ".join(f"{len(v)} {e(k)}" for k, v in r.new.items())
                       if total else ""))
        settled = [f"<b>{len(v)}</b> {e(cfg.label(k))}" for k, v in r.decided.items() if v]
        if settled:
            week.append("Settled: " + ", ".join(settled))
        for line in r.returning:
            week.append(f"Applied again: {e(line)}")
        for d in r.possible_duplicates:
            week.append(f"Possibly the same person, to confirm: {e(d)}")

        def section(title: str, items: list[str], empty: str) -> str:
            lis = "".join(f"<li>{i}</li>" for i in items) or f'<li class="nothing">{empty}</li>'
            return f'<h2>{title}</h2><div class="sec"><ul>{lis}</ul></div>'

        head = (f"{now.strftime('%A %d %B')} · since {r.since.strftime('%A %d %B')}")
        return (f'<h1>Catch-up</h1><div class="sub">{e(head)}</div>'
                + section("For you", you, "Nothing is waiting on you.")
                + section("For the team", team, "Nothing is stuck.")
                + section("This week", week, "A quiet week."))


def serve(host: str, port: int, cfg: Config, access: Access,
          reload: Any = None) -> None:
    if access.mode == "pick" and host not in ("127.0.0.1", "localhost"):
        raise DeskError("with access.mode = \"pick\" anyone who reaches the page can vote as "
                        "anyone; it only binds to 127.0.0.1. Use \"header\" behind a sign-in "
                        "proxy to serve a team.")
    if access.mode == "header" and not access.trusted_proxies:
        raise DeskError("with access.mode = \"header\" the desk must know which address the "
                        "sign-in proxy connects from: set access.trusted_proxies in desk.toml "
                        "(see DEPLOY.md). Without it anyone who reaches the port could send "
                        "the identity header themselves.")
    d = Desk(cfg, access)
    proxies = security.networks(access.trusted_proxies)
    policy = security.csp(CSS, JS)

    class Handler(BaseHTTPRequestHandler):
        # A connection that sends nothing for this long is closed (slowloris).
        timeout = security.TIMEOUT
        server_version, sys_version = "desk", ""

        def version_string(self) -> str:
            # No Python version in the Server header: nothing to look up a CVE for.
            return self.server_version

        def log_message(self, *_: Any) -> None:
            pass

        def _guard(self) -> bool:
            """Who may be answered at all, before anything is read. See console/security.py."""
            try:
                if access.mode == "header":
                    security.check_peer(self.client_address[0], proxies)
                    security.check_identity_header(self.headers, access.header)
                else:
                    security.check_host(self.headers.get("Host", ""))
            except security.Refused as no:
                self._send(no.status, str(no), "text/plain; charset=utf-8")
                return False
            return True

        def _send(self, code: int, body: str | bytes, ctype: str = "text/html; charset=utf-8",
                  extra: dict[str, str] | None = None) -> None:
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            for k, v in {**security.headers(ctype, policy), **(extra or {})}.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _viewer(self, asked: str) -> str | None:
            try:
                return desk.viewer_from(access, cfg, dict(self.headers.items()), asked)
            except DeskError as err:
                self._send(403, str(err), "text/plain; charset=utf-8")
                return None

        def _fresh(self) -> None:
            # Settings saved on the page apply to the next request, for everyone.
            if reload is not None:
                try:
                    d.cfg = reload()
                except DeskError:
                    pass  # keep the last configuration that ran

        def do_GET(self) -> None:
            if not self._guard():
                return
            self._fresh()
            cfg = d.cfg
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            viewer = self._viewer(q.get("as", ""))
            if viewer is None:
                return
            now = datetime.now(timezone.utc)
            flash, ok = q.get("error", "") or q.get("ok", ""), "ok" in q
            if u.path == "/":
                first = _stages_web.first_tab(cfg)
                tab = q.get("tab", first)
                tab = tab if tab in _stages_web.tabs_for(cfg) else first
                page = int(q["page"]) if q.get("page", "").isdigit() else 1
                since = int(q["since"]) if q.get("since", "").isdigit() else 0
                self._send(200, d.page(viewer, "desk",
                                       d.applications(viewer, tab, now, q.get("q", "").strip(),
                                                      q.get("role", "").strip(), page, since,
                                                      q.get("step", "")),
                                       flash, ok))
            elif u.path == "/recap":
                from console import tracking_web
                self._send(200, d.page(viewer, "recap", tracking_web.recap(d, viewer, now),
                                       title="Catch-up"))
            elif u.path == "/tracking":
                from console import tracking_web
                self._send(200, d.page(viewer, "tracking", tracking_web.tracking(d, viewer, now),
                                       title="Overview"))
            elif u.path == "/retention":
                self._send(200, d.page(viewer, "setup", _erase_web.page(d, viewer, now),
                                       flash, ok, title="Retention"))
            elif u.path == "/check":
                self._send(200, d.page(viewer, "setup", _check_web.page(d, viewer),
                                       title="Check"))
            elif u.path == "/pool":
                self._send(200, d.page(viewer, "pool",
                                       _pool_web.page(d, viewer, now, q.get("role", ""),
                                                      q.get("q", ""), q.get("for", "")),
                                       flash, ok, title="Pool"))
            elif u.path == "/progress":
                self._send(200, d.page(viewer, "progress",
                                       _progress_web.page(d, viewer, now, q.get("role", ""),
                                                          q.get("show", "all")),
                                       flash, ok, title="Progress"))
            elif u.path == "/calendar":
                self._send(200, d.page(viewer, "calendar",
                                       _calendar_web.page(d, viewer, q.get("date") or q.get("week", ""),
                                                         now, q.get("view", "month")),
                                       flash, ok, title="Calendar"))
            elif u.path == "/guess":
                self._send(200, d.page(viewer, "guess", _guess_web.page(d, viewer),
                                       title="AI guess"))
            elif u.path == "/recap.eml":
                from console import tracking_web
                data, name = tracking_web.recap_draft(cfg, viewer, now)
                self._send(200, data, "message/rfc822",
                           {"Content-Disposition": f'attachment; filename="{name}"'})
            elif u.path.startswith("/v/"):
                # A vote link from the recap: shown, never acted on by a GET.
                from console import tracking_web
                token = u.path[len("/v/"):]
                who = tracking_web.link_voter(token, cfg) if access.mode == "pick" else None
                code, body = tracking_web.confirm(
                    d, token, None if access.mode == "pick" else viewer, now)
                self._send(code, d.page(who or viewer, "recap", body, title="Confirm"),
                           extra={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})
            elif u.path == "/settings":
                self._send(200, d.page(viewer, "settings", d.settings(viewer), flash, ok,
                                       title="House rules"))
            elif u.path in ("/setup", "/setup/answers"):
                from console import setup_web
                body = (setup_web.page(d, viewer) if u.path == "/setup"
                        else setup_web.answers(d, viewer))
                self._send(200, d.page(viewer, "setup", body, flash, ok, title="Your call"))
            elif u.path == "/setup.md":
                from console import setup_web
                data, name = setup_web.markdown(d, viewer, now)
                self._send(200, data, "text/markdown; charset=utf-8",
                           {"Content-Disposition": security.disposition(name)})
            elif u.path.startswith("/person/"):
                body = d.person(viewer, unquote(u.path[len("/person/"):]), now)
                if body is None:
                    self._send(404, d.page(viewer, "desk", '<div class="empty"><b>Nobody by '
                                                          'that name.</b><a href="/'
                                                          f'{d._q(viewer)}">Back to the '
                                                          'applications</a></div>'))
                else:
                    self._send(200, d.page(viewer, "desk", body, flash, ok))
            elif u.path.startswith("/file/"):
                parts = unquote(u.path[len("/file/"):]).split("/", 1)
                try:
                    f, doc = desk.document_file(desk.load_registry(desk._registry_path()),
                                                parts[0], parts[1] if len(parts) > 1 else "")
                except DeskError:
                    self._send(404, "not found", "text/plain; charset=utf-8")
                    return
                ext = f.suffix.lower()
                self._send(200, f.read_bytes(), DOC_TYPES[ext], {
                    "Content-Disposition": security.disposition(
                        doc.original, inline=ext in security.INLINE)})
            elif u.path == "/draft":
                try:
                    m, name = _stages.draft(q.get("candidate", ""), q.get("posting", ""), cfg)
                except DeskError as err:
                    self._send(400, str(err), "text/plain; charset=utf-8")
                    return
                self._send(200, bytes(m), "message/rfc822",
                           {"Content-Disposition": security.disposition(name)})
            else:
                self._send(404, "not found", "text/plain; charset=utf-8")

        def _form(self) -> tuple[dict[str, str], dict[str, tuple[str, bytes]]]:
            """Fields, and uploaded files as (name, bytes). Standard library only."""
            ctype = self.headers.get("Content-Type", "")
            multipart = ctype.startswith("multipart/form-data")
            n = security.content_length(self.headers, 2 * MAX_DOC_BYTES + 65536 if multipart
                                        else security.MAX_FORM_BYTES)
            body = self.rfile.read(n)
            if not multipart:
                # One value per field, except the ticked rows of a batch: all of them.
                return ({k: "\n".join(v) if k == "pick" else v[0] for k, v in
                         parse_qs(body.decode("utf-8", "replace")).items()}, {})
            from email.parser import BytesParser
            from email.policy import HTTP
            msg = BytesParser(policy=HTTP).parsebytes(
                b"Content-Type: " + ctype.encode("latin-1") + b"\r\n\r\n" + body)
            fields: dict[str, str] = {}
            files: dict[str, tuple[str, bytes]] = {}
            for part in msg.iter_parts():
                name = part.get_param("name", header="content-disposition") or ""
                data = part.get_payload(decode=True) or b""
                filename = part.get_filename()
                if filename is not None:
                    if filename and data:
                        files[name] = (filename, data)
                else:
                    fields[name] = data.decode("utf-8", "replace")
            return fields, files

        def _back(self, f: dict[str, str], viewer: str, **msg: str) -> None:
            where = security.safe_back(f.get("back") or "/")
            params = [f"as={quote(viewer)}"] if access.mode == "pick" else []
            params += [f"{k}={quote(v)}" for k, v in msg.items()]
            self.send_response(303)
            self.send_header("Location", where + ("&" if "?" in where else "?") + "&".join(params)
                             if params else where)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _ashby_webhook(self) -> None:
            # Ashby is not a person on the desk: it is let in by its signature
            # alone, before the form token and the viewer are asked for.
            import os

            import ashby
            try:
                n = security.content_length(self.headers, 1024 * 1024)
            except security.Refused as no:
                self._send(no.status, str(no), "text/plain; charset=utf-8")
                return
            raw = self.rfile.read(n)
            key = os.environ.get("ASHBY_API_KEY", "").strip()
            try:
                code, msg = ashby.on_webhook(
                    raw, dict(self.headers.items()),
                    secret=os.environ.get("ASHBY_WEBHOOK_SECRET", "").strip(),
                    client=ashby.Client.from_env() if key else None,
                    s=ashby.load_settings())
            except (ashby.AshbyError, DeskError) as err:
                code, msg = 502, str(err)
            self._send(code, msg, "text/plain; charset=utf-8")

        def _vote_link(self, token: str, f: dict[str, str]) -> None:
            # Behind a proxy the signed-in person must be the link's voter; on
            # one laptop the link itself says who is voting.
            from console import tracking_web
            viewer = None
            if access.mode == "header":
                viewer = self._viewer("")
                if viewer is None:
                    return
            try:
                back = tracking_web.redeem(d, token, f, viewer)
            except (DeskError, PipelineError) as err:
                who = viewer or tracking_web.link_voter(token, d.cfg) or d.cfg.voters[0]
                self._send(400, d.page(who, "recap", f'<div class="card"><h3>Not recorded'
                                       f'</h3><p class="sub">{e(str(err))}</p><p><a href='
                                       f'"/v/{e(token)}">Back</a></p></div>', title="Vote"),
                           extra={"Cache-Control": "no-store"})
                return
            who = viewer or tracking_web.link_voter(token, d.cfg) or ""
            f["back"] = back
            self._back(f, who, ok="vote recorded, the link is now spent")

        def do_POST(self) -> None:
            if not self._guard():
                return
            self._fresh()
            cfg = d.cfg
            if urlparse(self.path).path == "/webhook/ashby":
                self._ashby_webhook()
                return
            try:
                security.check_origin(self.headers, access.origin)
            except security.Refused as no:
                self._send(no.status, str(no), "text/plain; charset=utf-8")
                return
            try:
                f, uploads = self._form()
            except security.Refused as no:
                self._send(no.status, str(no), "text/plain; charset=utf-8")
                return
            except DeskError as err:
                self._send(413, str(err), "text/plain; charset=utf-8")
                return
            viewer = self._viewer(f.get("as", ""))
            if viewer is None:
                return
            try:
                security.check_token(f.get("t", ""), d.token, viewer)
            except security.Refused as no:
                self._send(no.status, str(no), "text/plain; charset=utf-8")
                return
            path = urlparse(self.path).path
            if path.startswith("/v/"):
                self._vote_link(path[len("/v/"):], f)
                return
            try:
                if path == "/settings":
                    desk.save_settings(settings_from_form(f), by=viewer)
                    f["back"] = "/settings"
                    self._back(f, viewer, ok="saved")
                elif path == "/setup":
                    from console import setup_web
                    msg = setup_web.save(f, viewer, cfg, access.mode)
                    f["back"] = "/setup/answers"
                    self._back(f, viewer, ok=msg)
                elif path == "/add":
                    files = [(k, *uploads[k]) for k in ("cv", "letter") if k in uploads]
                    p = desk.add_now(f.get("posting", ""), f.get("name", ""),
                                     f.get("email", ""), link=f.get("link", ""), files=files,
                                     by=viewer)
                    self._back(f, viewer, ok=f"{p.name} added, ready to sort" if cfg.solo
                               else f"{p.name} added, no AI guess, the three of you "
                                    f"can class it now")
                elif path == "/note":
                    # A note written on a step of the Interested list says which.
                    about, text = f.get("about", "").strip(), f.get("text", "").strip()
                    desk.note_now(f.get("posting", ""), f.get("candidate", ""), viewer,
                                  f"{about}: {text}" if about and text else text, cfg)
                    self._back(f, viewer, ok="note posted")
                elif path in ("/erase", "/erase/batch"):
                    picks = [x for x in f.get("pick", "").splitlines() if x.strip()]
                    self._back(f, viewer, ok=_erase_web.handle(path, f, picks, viewer, cfg))
                elif path == "/pool/takeup":
                    self._back(f, viewer, ok=_pool_web.takeup(f, viewer, cfg))
                elif path == "/plan":
                    self._back(f, viewer, ok=_calendar_web.plan(f, viewer, cfg))
                elif path in ("/outlook/connect", "/outlook/finish", "/outlook/disconnect",
                              "/outlook/sync"):
                    import outlook
                    try:
                        msg = _outlook_web.handle(path, cfg)
                    except outlook.OutlookError as err:
                        raise DeskError(str(err)) from None
                    self._back(f, viewer, ok=msg)
                elif path == "/attach":
                    if "file" not in uploads:
                        raise DeskError("choose a file to attach")
                    desk.attach_now(f.get("person", ""), f.get("posting", ""),
                                    f.get("kind", "other"), *uploads["file"], by=viewer)
                    f["back"] = f"/person/{quote(f.get('person', ''))}"
                    self._back(f, viewer, ok="attached")
                elif path == "/vote":
                    label = f.get("label", "")
                    if not label:
                        raise DeskError("nothing recorded: press the button of your vote")
                    from console import choices_web
                    said = choices_web.record(
                        f.get("posting", ""), f.get("candidate", ""), viewer, label, cfg,
                        until=f.get("until", "") if label == "later" else "",
                        comment=f.get("comment", ""),
                        reason=f.get("reason", "") if label == "pass" else "")
                    # Said with the name: sorting fast, it is what tells you a
                    # click landed on the person you meant.
                    p = desk.load_registry(desk._registry_path()).person_of(
                        f.get("candidate", ""))
                    self._back(f, viewer, ok=f"{said} recorded"
                               + (f" for {p.name}" if p else ""))
                elif path == "/step":
                    msg = _stages_web.handle(f, viewer, cfg)
                    p = desk.load_registry(desk._registry_path()).person_of(
                        f.get("candidate", ""))
                    # A step ticked on the list stays on the list.
                    if f.get("list") != "1":
                        f["back"] = f"/person/{quote(p.person_id)}" if p else "/"
                    self._back(f, viewer, ok=(f"{p.name}: " if p else "") + msg)
                elif path == "/draft-ai":
                    # A model is asked once, for a draft shown to this viewer only.
                    msg = _drafting_web.handle(f, viewer, cfg)
                    p = desk.load_registry(desk._registry_path()).person_of(
                        f.get("candidate", ""))
                    f["back"] = f"/person/{quote(p.person_id)}" if p else "/"
                    self._back(f, viewer, ok=msg)
                elif path == "/go-ahead":
                    msg = _stages_web.go_ahead_handle(f, viewer, cfg)
                    p = desk.load_registry(desk._registry_path()).person_of(
                        f.get("candidate", ""))
                    f["back"] = f"/person/{quote(p.person_id)}" if p else "/"
                    self._back(f, viewer, ok=msg)
                elif path == "/batch":
                    kind, out = _batch_web.handle(f, viewer, cfg)
                    if kind == "csv":
                        self._send(200, out[0], "text/csv; charset=utf-8",
                                   {"Content-Disposition": security.disposition(out[1])})
                    else:
                        self._back(f, viewer, ok=out)
                elif path == "/mark":
                    desk.mark_now(f.get("posting", ""), f.get("candidate", ""),
                                  f.get("what", ""), viewer, cfg)
                    p = desk.load_registry(desk._registry_path()).person_of(
                        f.get("candidate", ""))
                    f["back"] = f"/person/{quote(p.person_id)}" if p else "/"
                    self._back(f, viewer, ok="recorded")
                else:
                    self._send(404, "not found", "text/plain; charset=utf-8")
            except (DeskError, PipelineError) as err:
                self._back(f, viewer, error=str(err))

    httpd = security.Server((host, port), Handler)
    where = "this machine only" if access.mode == "pick" else "behind your sign-in proxy"
    print(f"hiring desk on http://{host}:{port} -- {where}; Ctrl-C to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
