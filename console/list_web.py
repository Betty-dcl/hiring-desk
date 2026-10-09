"""One line per application on the Applications page, and the role filter above it.

Kept out of `desk_web.py`, which calls in here, so the list's contract lives
in one short file. A partner with ten minutes reads a row left to right and
needs nothing else to vote:

  name · the role they applied to · where it stands ("Voting · 2 of 3")
  CV · Letter · GitHub · LinkedIn          AI 62%          More details ->
  the four vote buttons, and one optional line of comment

What they built, what they want to own, AI in their work and the quotes are
on "More details": a row that carried them was a page of text per person,
and the vote buttons fell below the fold.

The row shows no other voter's vote, not even as a shape: the state says
how many have voted ("2 of 3"), never who said what. The blind rule is the
person page's to apply.
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any
from urllib.parse import quote

import stages
from desk import Application, Config, Person, outcome, votes
from pipeline import Standing

e = html.escape

#: What a code host is called on the row. Any other code host is "Code".
CODE_NAMES = {"github.com": "GitHub", "gitlab.com": "GitLab", "huggingface.co": "Hugging Face",
              "bitbucket.org": "Bitbucket", "codeberg.org": "Codeberg"}

#: The words for a document, and for its absence. A missing letter is said:
#: a link simply not there reads as "the page forgot", not "they sent none".
DOC_WORD = {"cv": "CV", "letter": "Why us"}
DOC_NONE = {"cv": "no CV", "letter": "no “Why us” answer"}


def state_text(st: stages.State, s: Standing, cfg: Config, viewer: str = "") -> str:
    """Where it stands, in a few plain words: "Voting · 2 of 3", "Ready to contact".

    The count is of votes cast, never of what they said: on the list, every
    vote is as hidden as the blind rule would have it on the person's page.
    To someone who has voted, it names who is still missing instead: who has
    not voted says nothing about how anyone did.
    """
    if st.id == "votes_in":
        o = outcome(votes(s), cfg)
        if viewer in o.voted and o.missing:
            return f"Waiting for {' and '.join(o.missing)}"
        return f"{stages.name('votes_in', cfg)} · {len(o.voted)} of {len(cfg.voters)}"
    if st.id == "to_contact" and st.note:
        # "back from Talk later", "went ahead without waiting": said on the card.
        return stages.name("to_contact", cfg)
    return stages.said(st, cfg)


def _code_name(host: str) -> str:
    host = host.lower()
    return next((v for k, v in CODE_NAMES.items() if host == k or host.endswith("." + k)),
                "Code")


def links(d: Any, person: Person, app: Application, viewer: str, sg: Any = None,
          more: str = "") -> str:
    """CV · Letter · GitHub · LinkedIn: one click each, in that order.

    GitHub is the first repository they gave or wrote (any code host, named
    as such); LinkedIn is shown when a profile link exists. Neither is
    announced when absent: unlike a letter, nobody was asked for one.
    """
    from console import security
    out = []
    seen: set[str] = set()
    # CV first, then the letter, whatever order they were filed in.
    docs = sorted(person.documents_for(app.posting_id),
                  key=lambda x: list(DOC_WORD).index(x.kind) if x.kind in DOC_WORD else 9)
    for doc in docs:
        if doc.kind in seen or doc.kind not in DOC_WORD:
            continue
        seen.add(doc.kind)
        out.append(f'<a href="/file/{quote(person.person_id)}/{quote(doc.stored)}'
                   f'{d._q(viewer)}" target="_blank" rel="noopener" '
                   f'title="{e(doc.original)}">{DOC_WORD[doc.kind]}</a>')
    out += [f'<span class="none">{DOC_NONE[k]}</span>' for k in DOC_WORD if k not in seen]
    if sg is None:
        from signals import signals
        sg = signals(person, app.candidate_id, app.posting_id)
    code = next((l for l in sg.links if l.kind == "code" and security.href(l.url)), None)
    if code is not None:
        out.append(f'<a href="{e(security.href(code.url))}" target="_blank" '
                   f'rel="noopener noreferrer" title="{e(code.url)}">{_code_name(code.host)}</a>')
    prof = next((l for l in sg.links if l.kind == "profile" and security.href(l.url)), None)
    if prof is not None:
        out.append(f'<a href="{e(security.href(prof.url))}" target="_blank" '
                   f'rel="noopener noreferrer" title="{e(prof.url)}">LinkedIn</a>')
    if more:
        # On a phone the row's "More details" moves here, after the documents.
        out.append(f'<a class="more-m" href="{e(more)}">More details →</a>')
    return f'<span class="links">{"".join(out)}</span>'


def row(d: Any, person: Person, app: Application, s: Standing, viewer: str,
        titles: dict[str, str], now: datetime, *, mine: bool = False, back: str = "",
        st: Any = None, tick: bool = False) -> str:
    cfg: Config = d.cfg
    st = st or stages.derive(s, cfg, now)
    from console.desk_web import AI_HINT
    from console.stages_web import _go_ahead
    import match
    m = match.of(person, app.posting_id)
    fit = (f'<span class="fit" title="{e(AI_HINT)}">AI guess <b>{m.total:.0%}</b></span>'
           if m is not None else
           '<span class="fit none" title="nothing to read on file">no AI guess</span>')
    more = (f'{d._person_link(person.person_id, viewer)}#app-{quote(app.posting_id)}')
    # "Contact now without waiting" belongs with the rows waiting on the others:
    # shown once the viewer has said yes, not on a row they have yet to vote on.
    said = votes(s).get(viewer)
    ahead = (_go_ahead(d, app, s, st, viewer, back, now, brief=True)
             if said is not None and said.meaning == "contact" else "")
    role = titles.get(app.posting_id, app.posting_id)
    # The viewer's own decision, in its colour, under the state: green yes,
    # orange to think about, blue kept, red no. Their own only: on a team's
    # desk a row still says nothing of how a colleague voted.
    from console import choices_web
    kept = choices_web.own_of(s, viewer, cfg) if said is not None else ""
    dec = (f'<span class="dec d-own">{e(kept)}</span>' if kept else
           f'<span class="dec d-{said.meaning}">{e(cfg.label(said.meaning))}</span>'
           if said is not None else "")
    # On the rows to answer no to: a tick box for answering several at once.
    from console import batch_web
    pick = batch_web.tick(app) if tick else ""
    # One person deciding follows a yes on the row itself, step by step; a no
    # to send shows its answer, ready. The decision can still be changed on
    # "More details".
    act = d.actions(app, s, viewer, back)
    if cfg.solo:
        from console import track_web
        if track_web.followed(st):
            act = track_web.tracker(d, app, s, st, viewer, back, now)
        elif st.id == "to_answer_no":
            # Stopped along the way: the decision is not what moved it.
            act = track_web.answer(d, app, st, viewer, back) + (act if not st.by else "")
    return (f'<div class="row{" me" if mine else ""}"><div class="r1">'
            f'<div class="who3"><a class="name" href="{e(more)}">{e(person.name)}</a>'
            f'<span class="role">{e(role)}</span>{d._multi(person, viewer)}</div>'
            f'<span class="standing">{e(state_text(st, s, cfg, viewer))}{dec}</span>{fit}'
            f'<a class="more" href="{e(more)}">More details →</a></div>'
            f'{links(d, person, app, viewer, more=more)}'
            f'{act}{ahead}{pick}</div>')


#: The periods the list can be narrowed to, by the day someone applied: (days, words).
PERIODS = ((0, "Any time"), (7, "The last 7 days"), (30, "The last 30 days"),
           (90, "The last 3 months"), (180, "The last 6 months"), (365, "The last 12 months"))


def period_filter(d: Any, viewer: str, tab: str, role: str, q: str, since: int) -> str:
    """"Applied: any time / the last 30 days ...": a GET form, like the role filter beside it."""
    opts = "".join(f'<option value="{n}"{" selected" if n == since else ""}>{e(words)}</option>'
                   for n, words in PERIODS)
    who = (f'<input type="hidden" name="as" value="{e(viewer)}">'
           if d.access.mode == "pick" else "")
    keep = "".join(f'<input type="hidden" name="{k}" value="{e(v)}">'
                   for k, v in (("role", role), ("q", q)) if v)
    return (f'<form method="get" class="rolef">{who}<input type="hidden" name="tab" '
            f'value="{e(tab)}">{keep}<label>Applied <select name="since" data-submit>'
            f'{opts}</select></label><noscript><button>Show</button></noscript></form>')


def role_filter(d: Any, viewer: str, tab: str, role: str, q: str,
                titles: dict[str, str], counts: dict[str, int], since: int = 0) -> str:
    """"All roles / <role>": a GET form, so the choice is in the address and survives a vote."""
    opts = [f'<option value="">All roles</option>']
    for pid, title in sorted(titles.items(), key=lambda kv: (kv[1] or kv[0]).lower()):
        n = counts.get(pid, 0)
        opts.append(f'<option value="{e(pid)}"{" selected" if pid == role else ""}>'
                    f'{e(title or pid)} ({n})</option>')
    who = (f'<input type="hidden" name="as" value="{e(viewer)}">'
           if d.access.mode == "pick" else "")
    keep = f'<input type="hidden" name="q" value="{e(q)}">' if q else ""
    keep += f'<input type="hidden" name="since" value="{since}">' if since else ""
    return (f'<form method="get" class="rolef">{who}<input type="hidden" name="tab" '
            f'value="{e(tab)}">{keep}<label>Role <select name="role" data-submit>'
            f'{"".join(opts)}</select></label><noscript><button>Show</button></noscript>'
            f'</form>')
