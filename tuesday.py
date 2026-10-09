#!/usr/bin/env python3
"""The Tuesday recap, rewritten: what each person has to do, first.

The first recap was a list of facts about the week -- six new applications,
two waiting, one to settle -- and left each partner to work out which of
them were theirs. This one starts from the reader:

    Ana -- 3 to vote on, 1 team to decide
    Ben -- 1 to vote on (~2 min, ...)
    Cy -- nothing waiting

then, for the reader, each application with a link per class. Then the
promise: who is past the answer deadline, who will be before next Tuesday.
Then the disagreements with the split shown, and the states a person moves
(stages.py): who is to contact, the first calls and trial days this week,
who was written to and is waiting for a reply. The week's news comes last,
because nobody acts on it.

    python tuesday.py --as Recruiter             # the recap, as text
    python tuesday.py --as Recruiter --write     # and a draft .eml for their mail app

**The recap is a file, never a message.** `--write` puts an unsent draft
(`X-Unsent: 1`) in `runs/desk/recaps/`; whoever wants it in an inbox opens it
and sends it to themselves. The desk still sends nothing.

**Links are minted only for the reader's own votes.** A recap addressed to
Ana carries links that vote as Ana, and nobody else's: a recap forwarded to
Ben cannot be used to vote as Ana's colleagues, and a link Ben clicks
in it is refused once he is signed in as himself (see `votelink.py`).

**Nothing blind is revealed.** An application waiting on the reader's vote
is listed by name and age only. The split of a disagreement is shown, but a
disagreement exists only once everyone has voted, so every vote in it is one
the reader is already allowed to see.
"""

from __future__ import annotations

import argparse
import html
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Callable
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import desk  # noqa: E402
import metrics  # noqa: E402
import stages  # noqa: E402
import votelink  # noqa: E402
from desk import MEANINGS, Config, Registry, next_step, outcome, votes  # noqa: E402
from pipeline import Pipeline, after  # noqa: E402

e = html.escape


@dataclass
class Item:
    name: str
    person_id: str
    posting_id: str
    candidate_id: str
    role: str
    #: Days since the application arrived; None when that is unknown.
    age: float | None
    note: str = ""


@dataclass
class Due:
    """What waits on one voter."""

    voter: str
    to_vote: list[Item] = field(default_factory=list)
    to_settle: list[Item] = field(default_factory=list)
    estimate: str = ""

    @property
    def nothing(self) -> bool:
        return not self.to_vote and not self.to_settle


@dataclass
class Weekly:
    now: datetime
    since: datetime
    viewer: str
    due: list[Due]
    within_days: float
    #: Past the promise, still without an answer: (item, days).
    late: list[tuple[Item, float]] = field(default_factory=list)
    #: Will pass it before the next recap unless someone writes.
    soon: list[tuple[Item, float]] = field(default_factory=list)
    #: (item, {voter: meaning})
    disagreed: list[tuple[Item, dict[str, str]]] = field(default_factory=list)
    #: (item, meaning agreed)
    drafts: list[tuple[Item, str]] = field(default_factory=list)
    wake: list[tuple[Item, str, str]] = field(default_factory=list)
    no_reply: list[tuple[Item, float]] = field(default_factory=list)
    #: The votes said yes (or a "talk later" came back), nobody has written yet.
    to_contact: list[Item] = field(default_factory=list)
    #: (item, day, planned) -- from the last recap to a week ahead.
    first_calls: list[tuple[Item, str, bool]] = field(default_factory=list)
    trial_days: list[tuple[Item, str, bool]] = field(default_factory=list)
    #: Contacted, no reply recorded: (item, days since they were written to).
    waiting_reply: list[tuple[Item, float]] = field(default_factory=list)
    no_reply_days: float = 8
    new: dict[str, int] = field(default_factory=dict)
    decided: dict[str, int] = field(default_factory=dict)
    returning: list[str] = field(default_factory=list)
    possible_duplicates: list[str] = field(default_factory=list)
    promise_line: str = ""
    vote_time: metrics.VoteTime | None = None


def build(pipes: dict[str, Pipeline], reg: Registry, cfg: Config, t: metrics.Tracking, *,
          viewer: str = "", now: datetime | None = None, since: datetime | None = None,
          titles: dict[str, str] | None = None) -> Weekly:
    now = now or datetime.now(timezone.utc)
    titles = titles or {}
    viewer = cfg.voter(viewer) if viewer else ""
    old = desk.recap(pipes, reg, cfg, now=now, since=since, viewer=viewer, titles=titles)
    vt = metrics.vote_time(pipes, cfg, t)
    order = ([viewer] if viewer else []) + [v for v in cfg.voters if v != viewer]
    due = {v: Due(v) for v in order}
    w = Weekly(now, old.since, viewer, list(due.values()), t.answer_within_days,
               vote_time=vt, no_reply_days=cfg.no_reply_days)
    #: "This week" for a meeting: since the last recap, and the week ahead.
    window = (old.since.date().isoformat(), (now + timedelta(days=7)).date().isoformat())

    def item(pid: str, cid: str, received: datetime | None) -> Item:
        p = reg.person_of(cid)
        return Item(p.name if p else cid, p.person_id if p else cid, pid, cid,
                    titles.get(pid) or pid,
                    metrics.days(received, now) if received else None)

    for pid, pipe in pipes.items():
        for cid in pipe.candidates:
            s = pipe.standing(cid)
            got = next((metrics.instant(ev.at, t.naive_timestamps) for ev in s.events
                        if ev.kind == "received"), None)
            if s.state == "closed":
                continue
            it = item(pid, cid, got)
            vs = votes(s)
            o = outcome(vs, cfg)
            if not o.settled:
                for v in o.missing:
                    due[v].to_vote.append(it)
            elif o.meaning == "discuss":
                for v in cfg.voters:
                    due[v].to_settle.append(it)
                w.disagreed.append((it, {v: vs[v].meaning for v in cfg.voters if v in vs}))
            nxt = next_step(s, cfg, now)
            st = stages.derive(s, cfg, now)
            if st.id == "to_contact":
                it.note = st.note
                w.to_contact.append(it)
            elif nxt.startswith("mail to send") and o.meaning:
                w.drafts.append((it, o.meaning))
            for e in s.events:
                step = stages.step_of(e)
                on = stages.day_of(e)
                if step in stages.MEETINGS and window[0] <= on <= window[1]:
                    meet = (it, on, on > now.date().isoformat())
                    (w.trial_days if step == "trial_day" else w.first_calls).append(meet)
            if st.id == "contacted":
                last = next(e for e in reversed(s.events) if stages.step_of(e) == "contacted")
                if not after(s.events, last, ("replied",)):
                    w.waiting_reply.append(
                        (it, (now - stages.instant(last)).total_seconds() / 86400))
            when = s.revisit_on
            if when and when <= now + timedelta(days=7):
                ev = next(x for x in reversed(s.events) if x.kind == "revisit")
                w.wake.append((it, when.date().isoformat(), ev.reason))

    for d in due.values():
        d.to_vote.sort(key=lambda i: -(i.age or 0))
        if d.to_vote:
            d.estimate = vt.estimate(len(d.to_vote))

    cs = metrics.cases(pipes, reg, cfg, t)
    pr = metrics.promise(cs, t, now)
    for c, age in pr.waiting:
        #: Closed without anyone writing is not "done": it is the silence
        #: itself, and it stays on the recap until somebody writes.
        it = Item(c.name, c.person_id, c.posting_id, c.candidate_ids[0],
                  titles.get(c.posting_id) or c.posting_id, age,
                  "closed without a word" if c.closed_silently else "")
        if age > t.answer_within_days:
            w.late.append((it, age))
        elif age + 7 > t.answer_within_days:
            w.soon.append((it, age))
    w.late.sort(key=lambda x: -x[1])
    w.soon.sort(key=lambda x: -x[1])
    w.first_calls.sort(key=lambda x: x[1])
    w.trial_days.sort(key=lambda x: x[1])
    w.waiting_reply.sort(key=lambda x: -x[1])
    if pr.cohort:
        w.promise_line = (f"{pr.on_time} of {pr.cohort} answered within "
                          f"{t.answer_within_days:g} days "
                          f"({metrics.share(pr.on_time, pr.cohort, t)}); median to a first "
                          f"answer {metrics.fmt_days(pr.to_answer.median)}")
    else:
        w.promise_line = (f"nothing on the desk is {t.answer_within_days:g} days old yet; "
                          f"{len(pr.answered)} answered so far")

    names = {(p.name): p for p in reg.people.values()}
    waiting = {i.name for i, _ in w.waiting_reply}
    for name, days_ in old.no_reply:
        if name in waiting:
            continue
        p = names.get(name)
        w.no_reply.append((Item(name, p.person_id if p else name, "", "", "", None), days_))
    w.new = {k: len(v) for k, v in old.new.items()}
    w.decided = {k: len(v) for k, v in old.decided.items() if v}
    w.returning = list(old.returning)
    w.possible_duplicates = list(old.possible_duplicates)
    return w


# --------------------------------------------------------------------------
# Words
# --------------------------------------------------------------------------

def _age(i: Item) -> str:
    return "age unknown" if i.age is None else f"{i.age:.0f} d"


def headline(d: Due, cfg: Config, viewer: str) -> str:
    who = "You" if d.voter == viewer else d.voter
    if d.nothing:
        return f"{who} -- nothing waiting"
    bits = []
    if d.to_vote:
        bits.append(f"{len(d.to_vote)} to {'sort' if cfg.solo else 'vote on'}")
    if d.to_settle:
        bits.append(f"{len(d.to_settle)} {stages.name('team_to_decide', cfg).lower()}")
    return f"{who} -- " + ", ".join(bits)


LinkFn = Callable[[Item, str], str]

#: How many applications still to vote on the recap names, oldest first. With
#: hundreds waiting, the recap says how many and shows the oldest; the list
#: itself is the desk's first tab.
RECAP_MAX = 12


def _more(n: int, cfg: Config) -> str:
    where = "To sort" if cfg.solo else "Your vote"
    return f"and {n} more, on the “{where}” tab"


def links_for(viewer: str, cfg: Config, t: metrics.Tracking, *, base: str | None = None,
              now: datetime | None = None, key: bytes | None = None) -> LinkFn:
    """Vote links as the viewer, for the viewer only. Each call mints a new token."""
    root = (base if base is not None else t.base_url).rstrip("/")

    def link(i: Item, meaning: str) -> str:
        tok = votelink.mint(viewer, i.posting_id, i.candidate_id, meaning, cfg,
                            ttl_hours=t.link_ttl_hours, now=now, key=key)
        return f"{root}/v/{tok}"
    return link


def render_text(w: Weekly, cfg: Config, link: LinkFn | None = None) -> str:
    out = [f"{w.now.strftime('%A %d/%m')} -- since {w.since.strftime('%A %d/%m')}", ""]
    b = out.append
    b("Who has what")
    for d in w.due:
        b(f"  {headline(d, cfg, w.viewer)}")
    mine = next((d for d in w.due if d.voter == w.viewer), None)
    if mine and mine.to_vote:
        b("")
        b("To sort (oldest first)" if cfg.solo else "To vote on (oldest first)")
        for i in mine.to_vote[:RECAP_MAX]:
            b(f"  {i.name} -- {i.role}, {_age(i)}")
            if link:
                # One link per application in plain text: four URLs a line
                # is unreadable. The page it opens offers the four classes.
                b(f"      {link(i, votelink.ANY)}")
        if len(mine.to_vote) > RECAP_MAX:
            b(f"  {_more(len(mine.to_vote) - RECAP_MAX, cfg)}")
    if w.disagreed:
        b("")
        b(stages.name("team_to_decide", cfg))
        for i, split in w.disagreed:
            b(f"  {i.name} -- " + ", ".join(f"{v} {cfg.label(m)}" for v, m in split.items()))
    sn = stages.names(cfg)
    if w.to_contact:
        b("")
        b(f"{sn['to_contact']} (anyone can; the desk sends nothing)")
        for i in w.to_contact:
            b(f"  {i.name} -- {i.role}" + (f" ({i.note})" if i.note else ""))
    if w.drafts:
        b("")
        b("Mails ready to send (anyone can)")
        for i, m in w.drafts:
            b(f"  {i.name} -- {cfg.label(m)}")
    for title, items in ((_calls_title(cfg), w.first_calls),
                         (f"{sn['trial_day']}s this week", w.trial_days)):
        if items:
            b("")
            b(title)
            for i, on, planned in items:
                b(f"  {i.name} -- {stages.short(on)}{' (planned)' if planned else ''}")
    if w.waiting_reply:
        b("")
        b("Waiting for a reply")
        for i, d in w.waiting_reply:
            b(f"  {i.name} -- written to {d:.0f} days ago"
              + (" (past {:g} days)".format(w.no_reply_days) if d >= w.no_reply_days else ""))
    if w.wake or w.no_reply:
        b("")
        b("Wake-ups and silences")
        for i, on, why in w.wake:
            b(f"  {i.name} -- back on {on}: \"{why}\"")
        for i, d in w.no_reply:
            b(f"  {i.name} -- written to {d:.0f} days ago, no reply")
    b("")
    b("The week")
    total = sum(w.new.values())
    b(f"  {total} new" + (" (" + ", ".join(f"{n} {k}" for k, n in w.new.items()) + ")"
                          if total else ""))
    if w.decided:
        b("  settled: " + ", ".join(f"{n} {cfg.label(k)}" for k, n in w.decided.items()))
    for r in w.returning:
        b(f"  applied again: {r}")
    for d_ in w.possible_duplicates:
        b(f"  possibly the same person, to confirm: {d_}")
    return "\n".join(out)


def _calls_title(cfg: Config) -> str:
    """The week's interviews: named after the one interview a team runs, or all of them."""
    if cfg.interviews == 1:
        return f"{stages.name('first_call', cfg)}s this week"
    return "Interviews this week"


def sections(w: Weekly, cfg: Config, link: LinkFn | None, person: Callable[[Item], str]
             ) -> list[tuple[str, list[str]]]:
    """The recap as (title, [html line]) -- wrapped by the page or the mail.

    A section with nothing in it is left out: the list already says where each
    application stands, so this page only carries what there is to act on.
    """
    out: list[tuple[str, list[str]]] = []
    who = []
    for d in w.due:
        cls = "me" if d.voter == w.viewer and not d.nothing else ""
        # On the page a colon, not the text recap's two dashes.
        who.append(f'<span class="{cls}">'
                   f'{e(headline(d, cfg, w.viewer).replace(" -- ", ": ", 1))}</span>')
    out.append(("Who has what", who))
    mine = next((d for d in w.due if d.voter == w.viewer), None)
    if mine and mine.to_vote:
        lines = []
        for i in mine.to_vote[:RECAP_MAX]:
            btns = ""
            if link:
                btns = " ".join(f'<a class="vl {m}" href="{e(link(i, m))}">'
                                f'{e(cfg.label(m))}</a>' for m in MEANINGS)
                btns = f'<span class="vls">{btns}</span>'
            lines.append(f"{person(i)} <small>{e(i.role)} · {e(_age(i))}</small> {btns}")
        if len(mine.to_vote) > RECAP_MAX:
            lines.append(e(_more(len(mine.to_vote) - RECAP_MAX, cfg)))
        out.append(("To sort, oldest first" if cfg.solo else "To vote on, oldest first",
                    lines))
    if w.disagreed:
        out.append((stages.name("team_to_decide", cfg),
                    [f"{person(i)} <small>" + e(", ".join(f"{v} {cfg.label(m)}"
                                                          for v, m in s.items())) + "</small>"
                     for i, s in w.disagreed]))
    sn = stages.names(cfg)
    if w.to_contact:
        out.append((sn["to_contact"],
                    [f"{person(i)} <small>{e(i.role)}{' · ' + e(i.note) if i.note else ''}</small>"
                     for i in w.to_contact]))
    if w.drafts:
        out.append(("Mails ready to send",
                    [f"{person(i)} <small>{e(cfg.label(m))}</small>" for i, m in w.drafts]))
    for title, items in ((_calls_title(cfg), w.first_calls),
                         (f"{sn['trial_day']}s this week", w.trial_days)):
        if items:
            out.append((title, [f"{person(i)} <small>{e(stages.short(on))}"
                                f"{' · planned' if planned else ''}</small>"
                                for i, on, planned in items]))
    if w.waiting_reply:
        out.append(("Waiting for a reply",
                    [(f'<b class="late">{d:.0f} d</b> ' if d >= w.no_reply_days else "")
                     + f"{person(i)} <small>written to {d:.0f} days ago</small>"
                     for i, d in w.waiting_reply]))
    wake = [f"{person(i)} <small>back on {e(on)} -- &ldquo;{e(why)}&rdquo;</small>"
            for i, on, why in w.wake]
    wake += [f"{person(i)} <small>written to {d:.0f} days ago, no reply</small>"
             for i, d in w.no_reply]
    if wake:
        out.append(("Wake-ups and silences", wake))
    total = sum(w.new.values())
    week = [f"<b>{total}</b> new" + (" -- " + e(", ".join(f"{n} {k}" for k, n in w.new.items()))
                                     if total else "")]
    if w.decided:
        week.append("Settled: " + e(", ".join(f"{n} {cfg.label(k)}"
                                              for k, n in w.decided.items())))
    week += [f"Applied again: {e(r)}" for r in w.returning]
    week += [f"Possibly the same person, to confirm: {e(d)}" for d in w.possible_duplicates]
    out.append(("The week", week))
    return out


MAIL_CSS = {
    "body": "font:15px/1.5 -apple-system,Segoe UI,sans-serif;color:#1c1c1a;max-width:640px",
    "h2": "font-size:12px;letter-spacing:.07em;text-transform:uppercase;color:#6d6c66;"
          "margin:22px 0 6px",
    "li": "padding:6px 0;border-top:1px solid #efede7",
    "a.vl": "display:inline-block;margin:2px 4px 2px 0;padding:2px 10px;border:1px solid "
            "#c9c7bf;border-radius:6px;color:#1c1c1a;text-decoration:none;font-size:13px",
}


def render_mail_html(w: Weekly, cfg: Config, link: LinkFn | None, base: str) -> str:
    """Inline styles only: mail clients drop <style> blocks and CSS variables."""
    def person(i: Item) -> str:
        return (f'<a href="{e(base.rstrip("/"))}/person/{quote(i.person_id)}" '
                f'style="color:#1c1c1a">{e(i.name)}</a>')
    parts = [f'<div style="{MAIL_CSS["body"]}"><p style="color:#6d6c66">'
             f'{e(w.now.strftime("%A %d %B"))} · since {e(w.since.strftime("%A %d %B"))}</p>']
    for title, lines in sections(w, cfg, link, person):
        lis = "".join(f'<li style="{MAIL_CSS["li"]}">{x}</li>' for x in lines)
        parts.append(f'<h2 style="{MAIL_CSS["h2"]}">{e(title)}</h2>'
                     f'<ul style="list-style:none;padding:0;margin:0">{lis}</ul>')
    parts.append('<p style="color:#9d9b94;font-size:12px">Each link opens the desk and asks '
                 'you to confirm; nothing is recorded by opening it. Links are yours alone, '
                 'work once, and expire.</p></div>')
    return "".join(parts).replace('<a class="vl', f'<a style="{MAIL_CSS["a.vl"]}" class="vl')


def draft(w: Weekly, cfg: Config, link: LinkFn | None, *, to: str = "", base: str = ""
          ) -> EmailMessage:
    """The recap as an unsent draft for the reader's own mail app."""
    m = EmailMessage()
    m["To"] = to
    m["Subject"] = (f"Hiring desk, {w.now.strftime('%A %d/%m')}: "
                    + headline(next((d for d in w.due if d.voter == w.viewer), w.due[0]),
                               cfg, w.viewer).replace("You -- ", ""))
    m["X-Unsent"] = "1"
    m.set_content(render_text(w, cfg, link))
    m.add_alternative(render_mail_html(w, cfg, link, base), subtype="html")
    return m


def address_of(voter: str) -> str:
    try:
        acc = desk.load_access()
    except desk.DeskError:
        return ""
    return next((mail for mail, v in acc.emails.items() if v == voter), "")


def main() -> int:
    ap = argparse.ArgumentParser(prog="tuesday", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as", dest="viewer", default="")
    ap.add_argument("--days", type=int, default=None,
                    help="look back this many days instead of to the last recap day")
    ap.add_argument("--write", action="store_true",
                    help="also write an unsent .eml draft (needs --as)")
    ap.add_argument("--base-url", default=None, help="where the desk is reached from a mail")
    a = ap.parse_args()
    try:
        cfg, t = desk.load_config(), metrics.load_tracking()
        now = datetime.now(timezone.utc)
        w = build(desk._pipelines(), desk.load_registry(desk._registry_path()), cfg, t,
                  viewer=a.viewer, now=now,
                  since=now - timedelta(days=a.days) if a.days else None,
                  titles=desk._titles())
        base = a.base_url or t.base_url
        link = links_for(w.viewer, cfg, t, base=base) if w.viewer else None
        print(render_text(w, cfg, link))
        if a.write:
            if not w.viewer:
                print("--write needs --as: a recap with vote links is one person's",
                      file=sys.stderr)
                return 2
            m = draft(w, cfg, link, to=address_of(w.viewer), base=base)
            target = (desk.ROOT / "runs" / "desk" / "recaps"
                      / f"recap_{now.date().isoformat()}_{desk.slug(w.viewer)}.eml")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(bytes(m))
            print(f"\nwritten: {target.relative_to(desk.ROOT)} -- an unsent draft; "
                  f"nothing was sent")
    except desk.DeskError as err:
        print(err, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
