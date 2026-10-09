#!/usr/bin/env python3
"""Ashby, in and out: applications onto the desk, settled classes back as a note.

The first connection, in three steps (CLAUDE.md walks through them):

    ASHBY_API_KEY=... python ashby.py check                  # 1. the key works
    ASHBY_API_KEY=... python ashby.py pull --dry-run         # 2. what would come in
    ASHBY_API_KEY=... python ashby.py pull --job "Founders' Associate"   # 3. one role
    python ashby.py verify                                   #    then compare by eye

    # what would come in -- reads Ashby, writes nothing anywhere
    ASHBY_API_KEY=... ./.venv/bin/python ashby.py pull --dry-run

    # bring new applications onto the desk (CV and form answers attached)
    ASHBY_API_KEY=... ./.venv/bin/python ashby.py pull

    # or let it happen by itself: [ashby] auto_pull = true in desk.toml, and
    # daily.py pulls at each start of the desk and every evening at 19:00

    # what would go back -- prints each note, sends nothing
    ./.venv/bin/python ashby.py push

    # send them, as a named person
    ASHBY_API_KEY=... ./.venv/bin/python ashby.py push --send --as Recruiter

Built against Ashby's published API reference (the OpenAPI definitions at
developers.ashbyhq.com, as updated on 2026-05-27). **It has never run against
a live Ashby account.** Every request and response shape it relies on is
listed in `ASSUMES` below, with the one that the reference does not spell
out marked as such; the tests replay those shapes, not real traffic.

Four rules:

**Reading is the default, writing is a person's act.** `pull` only reads
Ashby. `push` prints what it would write until it is given `--send` and the
name of the person sending -- the same rule as the mail drafts. Nothing is
written back on a timer.

**Only what people decided goes back.** A note is written for an application
three voters agreed on, and for nothing else: not a disagreement, not a
vote in progress, never the fit percentage on its own. The percentage can
travel with the class, labelled as a machine's reading of the paper.

**Nothing is guessed.** An Ashby job is matched to a posting on the desk by
its exact title or by a mapping in `desk.toml`; an application for a job the
desk does not know is reported and left in Ashby. A person already on the
desk for that role is left alone.

**A webhook is checked before it is read.** `verify` refuses a request whose
signature does not match -- and refuses every request when no secret is
configured, rather than accepting them all. (The first version of this relay,
skipped the check when the secret was missing.)
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent
API = "https://api.ashbyhq.com"

#: What this connector takes on trust from the reference, in one place, so
#: the first run against a real account knows what to check.
ASSUMES = {
    "auth": "HTTP Basic, the API key as username and an empty password",
    "calls": "POST https://api.ashbyhq.com/<method> with a JSON body, for reads too",
    "errors": "{success: false, errors: [...]} with an optional errorInfo.code",
    "application.list": "syncToken / cursor / limit; results, moreDataAvailable, nextCursor, "
                        "syncToken; tokens and cursors expire after 14 days",
    "application.info": "applicationId + expand ['applicationFormSubmissions']",
    "form answers": "formDefinition.sections[].fields[].field {path, title, type}; "
                    "NOT DOCUMENTED: submittedValues is keyed by that path",
    "candidate.info": "id; resumeFileHandle {id, name, handle}; socialLinks [{type, url}]",
    "file.info": "fileHandle -> results.url, a download link",
    "candidate page": "NOT DOCUMENTED: a candidate opens at https://app.ashbyhq.com/candidates/<id> "
                      "(used by review only, to compare by eye)",
    "candidate.createNote": "candidateId, note {type: text/plain, value}, isPrivate, "
                            "sendNotifications",
    "webhook": "Ashby-Signature: sha256=<hex HMAC-SHA256 of the raw body>; "
               "body {action, data: {application}}",
}

#: Permissions the key needs. Nothing broader.
PERMISSIONS = {"pull": ["candidatesRead"], "push": ["candidatesWrite"]}


class AshbyError(RuntimeError):
    def __init__(self, method: str, errors: Any, code: str = ""):
        self.method, self.errors, self.code = method, errors, code
        super().__init__(f"{method}: {code or errors}")


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

@dataclass
class Settings:
    #: Ashby job title (or id) -> desk posting id, for when the titles differ.
    jobs: dict[str, str] = field(default_factory=dict)
    #: Form questions whose answer is kept as the letter, matched on the
    #: question's title, case-insensitively, by substring.
    letter_questions: list[str] = field(default_factory=lambda: ["cover letter", "why"])
    #: Which Ashby statuses come onto the desk.
    statuses: list[str] = field(default_factory=lambda: ["Active"])
    note_private: bool = True
    notify: bool = False
    #: Send the fit percentage with the class, labelled as a machine reading.
    note_with_score: bool = True
    #: The voters' one-line comments are internal to the desk by default.
    note_with_comments: bool = False
    #: Pull by itself, at each start of the desk and every evening (daily.py).
    #: Off until someone switches it on, once the first connection is checked.
    auto_pull: bool = False


def load_settings(path: str | Path | None = None) -> Settings:
    raw = tomllib.loads(Path(path or ROOT / "desk.toml").read_text(encoding="utf-8"))
    a = raw.get("ashby", {})
    s = Settings()
    for k in ("note_private", "notify", "note_with_score", "note_with_comments", "auto_pull"):
        if k in a:
            setattr(s, k, bool(a[k]))
    if "letter_questions" in a:
        s.letter_questions = [str(x) for x in a["letter_questions"]]
    if "statuses" in a:
        s.statuses = [str(x) for x in a["statuses"]]
    s.jobs = {str(k): str(v) for k, v in (a.get("jobs") or {}).items()}
    return s


# --------------------------------------------------------------------------
# The API
# --------------------------------------------------------------------------

Transport = Callable[[str, dict[str, Any]], dict[str, Any]]
Download = Callable[[str], bytes]


def _http(key: str) -> tuple[Transport, Download]:
    auth = "Basic " + base64.b64encode(f"{key}:".encode()).decode()

    def call(method: str, body: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(
            f"{API}/{method}", data=json.dumps(body).encode(), method="POST",
            headers={"Authorization": auth, "Content-Type": "application/json",
                     "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise AshbyError(method, f"HTTP {e.code}") from e

    def download(url: str) -> bytes:
        # The file link is pre-signed; the API key is not sent to it.
        if not url.startswith("https://"):
            raise AshbyError("file.info", f"refusing a non-https file link")
        from desk import MAX_DOC_BYTES
        with urllib.request.urlopen(url, timeout=60) as r:
            data = r.read(MAX_DOC_BYTES + 1)
        # A file the desk would refuse by hand is refused here before it is held.
        if len(data) > MAX_DOC_BYTES:
            raise AshbyError("file.info", "the file is over the desk's size limit")
        return data

    return call, download


#: Where the key can also be kept: outside the program and its backups, in
#: the person's home folder. The evening task on a Mac does not see the
#: variables a terminal sets, so there the key lives in this file.
KEY_FILE = Path.home() / ".hiringdesk" / "ashby_key"


def key() -> str:
    """The API key: ASHBY_API_KEY, else the key file. Empty when neither is set."""
    k = os.environ.get("ASHBY_API_KEY", "").strip()
    if k:
        return k
    try:
        # utf-8-sig: a file written by PowerShell may start with a byte-order mark.
        return KEY_FILE.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return ""


class Client:
    def __init__(self, transport: Transport, download: Download | None = None):
        self._t, self._d = transport, download

    @classmethod
    def from_env(cls) -> "Client":
        k = key()
        if not k:
            raise AshbyError("setup", f"ASHBY_API_KEY is not set, and there is no {KEY_FILE}")
        return cls(*_http(k))

    def call(self, method: str, body: dict[str, Any]) -> dict[str, Any]:
        r = self._t(method, body)
        if not r.get("success"):
            code = (r.get("errorInfo") or {}).get("code", "")
            errs = r.get("errors") or []
            if not code and errs and isinstance(errs[0], str):
                code = errs[0]
            raise AshbyError(method, errs, code)
        return r

    def download(self, url: str) -> bytes:
        if self._d is None:
            raise AshbyError("file", "no downloader")
        return self._d(url)

    def applications(self, sync_token: str = "", limit: int = 100
                     ) -> tuple[list[dict[str, Any]], str]:
        """Every application changed since `sync_token` (all of them without one).

        A token or cursor Ashby says has expired, or a sync too large to be
        incremental, restarts as a full sync -- which is what the reference
        tells a client to do, and is safe here because adding is idempotent.
        """
        try:
            return self._pages(sync_token, limit)
        except AshbyError as e:
            if sync_token and e.code in ("sync_token_expired", "incremental_sync_too_large",
                                         "next_cursor_expired", "cursor_invalid",
                                         "invalid_next_cursor"):
                return self._pages("", limit)
            raise

    def _pages(self, sync_token: str, limit: int) -> tuple[list[dict[str, Any]], str]:
        out: list[dict[str, Any]] = []
        cursor = ""
        for _ in range(1000):
            body: dict[str, Any] = {"limit": limit}
            if sync_token:
                body["syncToken"] = sync_token
            if cursor:
                body["cursor"] = cursor
            r = self.call("application.list", body)
            out.extend(r.get("results") or [])
            if not r.get("moreDataAvailable"):
                return out, r.get("syncToken") or sync_token
            cursor = r.get("nextCursor") or ""
            if not cursor:
                raise AshbyError("application.list", "more data, but no cursor to fetch it")
        raise AshbyError("application.list", "gave up after 1000 pages")


# --------------------------------------------------------------------------
# In: one Ashby application, as the desk needs it
# --------------------------------------------------------------------------

@dataclass
class Incoming:
    application_id: str
    candidate_id: str
    name: str
    email: str
    job_id: str
    job_title: str
    status: str
    link: str = ""
    files: list[tuple[str, str, bytes]] = field(default_factory=list)
    #: What could not be brought over, in words -- a CV that would not
    #: download is not a reason to lose the application.
    missing: list[str] = field(default_factory=list)


def form_answers(app: dict[str, Any]) -> list[tuple[str, str]]:
    """(question title, answer) for every text answer on the application form."""
    out = []
    for sub in app.get("applicationFormSubmissions") or []:
        values = sub.get("submittedValues") or {}
        for section in (sub.get("formDefinition") or {}).get("sections") or []:
            for entry in section.get("fields") or []:
                f = entry.get("field") or {}
                v = values.get(f.get("path", ""))
                if isinstance(v, str) and v.strip():
                    out.append((str(f.get("title", "")), v.strip()))
    return out


def letter_of(app: dict[str, Any], s: Settings) -> str:
    wanted = [q.lower() for q in s.letter_questions]
    parts = [f"{t}\n\n{a}" for t, a in form_answers(app)
             if any(w in t.lower() for w in wanted)]
    return "\n\n---\n\n".join(parts)


def fetch(client: Client, application_id: str, s: Settings) -> Incoming:
    app = client.call("application.info", {
        "applicationId": application_id, "expand": ["applicationFormSubmissions"]})["results"]
    cand_ref = app.get("candidate") or {}
    cand = client.call("candidate.info", {"id": cand_ref["id"]})["results"]
    email = ((cand.get("primaryEmailAddress") or cand_ref.get("primaryEmailAddress") or {})
             .get("value", ""))
    # Every https link they gave, LinkedIn first: a GitHub or a demo is the
    # thing this desk puts at the top of the card, so it is not dropped here.
    socials = [l for l in cand.get("socialLinks") or []
               if str(l.get("url", "")).startswith("https://")]
    socials.sort(key=lambda l: l.get("type") != "LinkedIn")
    link = " ".join(str(l["url"]).strip() for l in socials)
    job = app.get("job") or {}
    inc = Incoming(application_id=app["id"], candidate_id=cand["id"],
                   name=cand.get("name") or cand_ref.get("name", ""), email=email,
                   job_id=job.get("id", ""), job_title=job.get("title", ""),
                   status=app.get("status", ""), link=link)

    handle = app.get("resumeFileHandle") or cand.get("resumeFileHandle")
    if handle:
        try:
            url = client.call("file.info", {"fileHandle": handle["handle"]})["results"]["url"]
            inc.files.append(("cv", handle.get("name") or "cv.pdf", client.download(url)))
        except (AshbyError, OSError, KeyError) as e:
            inc.missing.append(f"CV not downloaded ({e})")
    else:
        inc.missing.append("no CV on the application")
    letter = letter_of(app, s)
    if letter:
        inc.files.append(("letter", "application-form.txt", letter.encode()))
    return inc


def posting_for(inc: Incoming, titles: dict[str, str], s: Settings) -> str:
    """The desk posting this job is, or "" -- never a near match."""
    for key in (inc.job_id, inc.job_title):
        if key in s.jobs:
            return s.jobs[key]
    by_title = {t.strip().lower(): pid for pid, t in titles.items() if t}
    return by_title.get(inc.job_title.strip().lower(), "")


# --------------------------------------------------------------------------
# What the connector remembers
# --------------------------------------------------------------------------

def _state_path() -> Path:
    return ROOT / "runs" / "desk" / "ashby.json"


def _erased(aid: str, state: dict[str, Any]) -> bool:
    """An application whose person was erased from the desk (erase.py): never again."""
    import hashlib
    return hashlib.sha256(aid.encode("utf-8")).hexdigest() in state.get("erased", [])


def load_state(path: Path | None = None) -> dict[str, Any]:
    p = path or _state_path()
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"sync_token": "", "applications": {}, "pushed": {}}


def save_state(state: dict[str, Any], path: Path | None = None) -> None:
    p = path or _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def pull(client: Client, s: Settings, *, dry_run: bool = False, by: str = "ashby",
         state_path: Path | None = None, job: str = "") -> list[str]:
    """New Ashby applications onto the desk. One line of report per application.

    `job` limits it to one Ashby job title (the first real run, on one role):
    then the sync point is not moved, so the other roles still come in later.

    What came in is saved even when Ashby stops answering part way, so nobody
    is fetched twice; the sync point only moves once every page was read.
    The run is noted in ashby.json as `last_pull` (counts only, no names).
    """
    import desk

    state = load_state(state_path)
    apps, token = client.applications(state.get("sync_token", ""))
    titles = desk._titles()
    report: list[str] = []
    counts = {"added": 0, "left_in_ashby": 0}
    done = False
    try:
        _bring(client, s, apps, state, titles, report, counts, dry_run, by, job)
        done = True
    finally:
        if not dry_run:
            if done and not job:
                state["sync_token"] = token
            state["last_pull"] = {"at": datetime.now(timezone.utc).isoformat(), **counts,
                                  "job": job, "error": "" if done else "stopped part way"}
            save_state(state, state_path)
    return report


def _bring(client: Client, s: Settings, apps: list[dict[str, Any]], state: dict[str, Any],
           titles: dict[str, str], report: list[str], counts: dict[str, int],
           dry_run: bool, by: str, job: str) -> None:
    """The applications of one pull onto the desk, counted as they come."""
    import desk

    for a in apps:
        aid = a.get("id", "")
        if aid in state["applications"] or _erased(aid, state):
            continue
        if a.get("status") not in s.statuses:
            continue
        inc = fetch(client, aid, s)
        if job and inc.job_title.strip().lower() != job.strip().lower():
            continue
        pid = posting_for(inc, titles, s)
        who = inc.name or "?"
        if not pid:
            report.append(f"{who}: job {inc.job_title!r} is not a posting on the desk -- "
                          f"left in Ashby (map it under [ashby.jobs] in desk.toml)")
            counts["left_in_ashby"] += 1
            continue
        extra = f" ({'; '.join(inc.missing)})" if inc.missing else ""
        if dry_run:
            kinds = ", ".join(k for k, _, _ in inc.files) or "no files"
            report.append(f"{who}: would be added to {titles.get(pid, pid)} with {kinds}{extra}")
            continue
        try:
            person = desk.add_now(pid, inc.name, inc.email, link=inc.link,
                                  files=inc.files, by=by)
        except desk.DeskError as e:
            report.append(f"{who}: {e}")
            continue
        state["applications"][aid] = {"candidate_id": inc.candidate_id, "posting": pid,
                                      "person_id": person.person_id,
                                      "at": datetime.now(timezone.utc).isoformat()}
        report.append(f"{who}: added to {titles.get(pid, pid)}{extra}")
        counts["added"] += 1


def auto(s: Settings, client: Client | None = None, state_path: Path | None = None,
         now: datetime | None = None) -> list[str]:
    """The pull that runs by itself (daily.py), only when [ashby] auto_pull is on.

    Never raises: the desk must start, and the backup after it must run, even
    when Ashby or the network is down. The error is kept in ashby.json, where
    check.py and the Check page show it. Reads Ashby only, like `pull`.
    """
    if not s.auto_pull:
        return []
    started = (now or datetime.now(timezone.utc)).isoformat()
    try:
        pull(client or Client.from_env(), s, state_path=state_path)
    except Exception as e:  # noqa: BLE001 -- nothing here may stop the desk starting
        state = load_state(state_path)
        last = state.get("last_pull") or {}
        if last.get("at", "") < started:
            # Ashby did not answer at all: this run brought nobody.
            last = {"added": 0, "left_in_ashby": 0, "job": ""}
        last.update(at=datetime.now(timezone.utc).isoformat(), error=str(e) or type(e).__name__)
        state["last_pull"] = last
        save_state(state, state_path)
        return [f"Ashby: not reached ({last['error']}). The applications wait there; "
                f"the next start of the desk tries again."]
    last = load_state(state_path).get("last_pull") or {}
    n, kept = last.get("added", 0), last.get("left_in_ashby", 0)
    return [f"Ashby: {n} new application{'' if n == 1 else 's'} on the desk"
            + (f"; {kept} for a role the desk does not know, left in Ashby" if kept else "")]


def check(client: Client) -> list[str]:
    """Step one of a first connection: does the key work, and may it read candidates?"""
    r = client.call("application.list", {"limit": 1})
    seen = len(r.get("results") or [])
    return ["the key works: Ashby answered application.list"
            + (" with an application" if seen else " (no application yet)"),
            f"next: python ashby.py pull --dry-run   (reads Ashby, writes nothing)"]


#: Where a candidate opens in Ashby, to compare a card with the source by eye.
#: An assumption (see ASSUMES): the app's address for a candidate.
CANDIDATE_URL = "https://app.ashbyhq.com/candidates/{id}"


def review(limit: int = 5, state_path: Path | None = None) -> list[str]:
    """Step three: the last people brought in, with what is on file, to compare with Ashby.

    For each: the role, the CV (its name and size, or MISSING), the answer to
    the form, and the candidate's address in Ashby. Reads the desk only.
    """
    import desk
    state = load_state(state_path)
    reg = desk.load_registry(desk._registry_path())
    titles = desk._titles()
    files = desk._files_dir()
    rows = sorted(state["applications"].items(), key=lambda kv: kv[1].get("at", ""),
                  reverse=True)[:limit]
    out = []
    for aid, a in rows:
        p = reg.people.get(a.get("person_id", ""))
        if p is None:
            out.append(f"{aid}: on the Ashby list, but nobody on the desk -- MISSING")
            continue
        docs = {d.kind: d for d in p.documents_for(a.get("posting", ""))}
        def said(kind: str) -> str:
            d = docs.get(kind)
            if d is None:
                return "none"
            f = files / d.stored
            return (f"{d.original} ({f.stat().st_size // 1024} KB)" if f.exists()
                    else f"{d.original} -- MISSING ON DISK")
        out.append(f"{p.name} | {titles.get(a.get('posting', ''), a.get('posting', ''))} | "
                   f"CV: {said('cv')} | form: {said('letter')} | "
                   f"{CANDIDATE_URL.format(id=a.get('candidate_id', ''))}")
    return out or ["nobody brought in from Ashby yet"]


# --------------------------------------------------------------------------
# Out: a settled class, as a note on the candidate
# --------------------------------------------------------------------------

def note_text(person: Any, posting: str, out: Any, vs: dict[str, Any], cfg: Any,
              s: Settings, title: str = "", screening: Any = None) -> str:
    cls = cfg.label(out.meaning)
    lines = [f"Hiring desk -- {title or posting}: {cls}"
             + (f" (look again on {out.until})" if out.until else "")]
    agreed = [v for v in out.voted if vs[v].meaning == out.meaning]
    others = [v for v in out.voted if v not in agreed]
    lines.append(f"Classed {cls} by {', '.join(agreed)}."
                 + (f" Also voted: {', '.join(others)}." if others else ""))
    if s.note_with_comments:
        for v in out.voted:
            if vs[v].comment:
                lines.append(f"- {v}: {vs[v].comment}")
    if s.note_with_score and screening is not None:
        lines.append(f"The screener read the paper at {screening.score:.0%} fit "
                     f"({screening.coverage:.0%} of the criteria answered). That is a "
                     f"machine's reading of the CV, not part of the decision.")
    lines.append("Written from the hiring desk. The class was chosen by the people named "
                 "above; nothing here was decided by the machine.")
    return "\n".join(lines)


@dataclass
class Outgoing:
    application_id: str
    candidate_id: str
    who: str
    body: dict[str, Any]
    #: What the note says the class was, so a changed class writes a new note
    #: and an unchanged one never writes twice.
    said: str


def outgoing(s: Settings, state: dict[str, Any] | None = None) -> list[Outgoing]:
    """Every note that should exist in Ashby and does not yet."""
    import desk
    import screen

    cfg = desk.load_config()
    state = state if state is not None else load_state()
    pipes = desk._pipelines()
    reg = desk.load_registry(desk._registry_path())
    titles = desk._titles()
    out = []
    for aid, m in state.get("applications", {}).items():
        pipe = pipes.get(m["posting"])
        if pipe is None or m["person_id"] not in pipe.candidates:
            continue
        vs = desk.votes(pipe.standing(m["person_id"]))
        o = desk.outcome(vs, cfg)
        if o.status != "agreed":
            continue
        said = f"{o.meaning}:{o.until}"
        if state.get("pushed", {}).get(aid, {}).get("said") == said:
            continue
        f = ROOT / "runs" / "screenings" / f"{m['person_id']}_{m['posting']}.json"
        sc = screen.load(f) if f.exists() else None
        person = reg.people.get(m["person_id"])
        text = note_text(person, m["posting"], o, vs, cfg, s, titles.get(m["posting"], ""), sc)
        out.append(Outgoing(aid, m["candidate_id"], person.name if person else m["person_id"],
                            {"candidateId": m["candidate_id"],
                             "note": {"type": "text/plain", "value": text},
                             "isPrivate": s.note_private, "sendNotifications": s.notify},
                            said))
    return out


def push(client: Client | None, s: Settings, *, send: bool = False, by: str = "",
         state_path: Path | None = None) -> list[str]:
    import desk

    state = load_state(state_path)
    notes = outgoing(s, state)
    if not send:
        return [f"{n.who}: would write\n{n.body['note']['value']}" for n in notes] or \
               ["nothing settled that Ashby does not already have"]
    if not by:
        raise AshbyError("push", "a note is sent by a named person: --as <voter>")
    sender = desk.load_config().voter(by)
    if client is None:
        raise AshbyError("push", "no Ashby client")
    report = []
    for n in notes:
        client.call("candidate.createNote", n.body)
        state.setdefault("pushed", {})[n.application_id] = {
            "said": n.said, "by": sender, "at": datetime.now(timezone.utc).isoformat()}
        save_state(state, state_path)  # after each, so a failure part-way loses nothing sent
        report.append(f"{n.who}: note written, sent by {sender}")
    return report or ["nothing settled that Ashby does not already have"]


# --------------------------------------------------------------------------
# Webhooks
# --------------------------------------------------------------------------

def verify(raw: bytes, header: str, secret: str) -> bool:
    """The request came from Ashby with this secret. False without a secret."""
    if not secret:
        return False
    algo, _, given = header.strip().partition("=")
    if algo != "sha256" or not given:
        return False
    digest = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    # Compared as bytes: compare_digest raises on a non-ASCII str, and a header
    # is whatever the sender typed.
    return hmac.compare_digest(digest.encode(), given.strip().lower().encode("utf-8", "replace"))


def application_id_of(raw: bytes) -> str:
    """The application a verified webhook is about, or "" for a ping or anything else.

    The body is only a pointer: everything the desk keeps is fetched again
    through the API, so a webhook cannot put anything on the desk that Ashby
    would not return to a key.
    """
    try:
        body = json.loads(raw)
    except ValueError:
        return ""
    if body.get("action") != "applicationSubmit":
        return ""
    return str(((body.get("data") or {}).get("application") or {}).get("id", ""))


def on_webhook(raw: bytes, headers: dict[str, str], *, secret: str,
               client: Client | None, s: Settings, state_path: Path | None = None
               ) -> tuple[int, str]:
    """(HTTP status, message). Signature first, then the API, then the desk."""
    sig = next((v for k, v in headers.items() if k.lower() == "ashby-signature"), "")
    if not verify(raw, sig, secret):
        return 401, "signature does not match" if secret else "no webhook secret configured"
    aid = application_id_of(raw)
    if not aid:
        return 200, "nothing to do"
    state = load_state(state_path)
    if aid in state["applications"]:
        return 200, "already on the desk"
    if _erased(aid, state):
        return 200, "erased from the desk on request: not brought back"
    if client is None:
        return 503, "no Ashby key: the application stays in Ashby until the next pull"
    import desk

    inc = fetch(client, aid, s)
    pid = posting_for(inc, desk._titles(), s)
    if not pid:
        return 200, f"job {inc.job_title!r} is not on the desk"
    person = desk.add_now(pid, inc.name, inc.email, link=inc.link, files=inc.files, by="ashby")
    state["applications"][aid] = {"candidate_id": inc.candidate_id, "posting": pid,
                                  "person_id": person.person_id,
                                  "at": datetime.now(timezone.utc).isoformat()}
    save_state(state, state_path)
    return 200, "added"


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(prog="ashby.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pull", help="new Ashby applications onto the desk (reads Ashby)")
    p.add_argument("--dry-run", action="store_true", help="say what would come in; write nothing")
    p.add_argument("--job", default="", help="only this Ashby job title (a first run on one role)")
    sub.add_parser("check", help="step one: does the key work? (reads Ashby)")
    v = sub.add_parser("verify", help="step three: the last people brought in, to compare by eye")
    v.add_argument("--limit", type=int, default=5)
    q = sub.add_parser("push", help="settled classes back as notes (prints unless --send)")
    q.add_argument("--send", action="store_true")
    q.add_argument("--as", dest="by", default="")
    sub.add_parser("assumes", help="what this connector takes on trust from the reference")
    a = ap.parse_args()
    s = load_settings()
    try:
        if a.cmd == "assumes":
            for k, v in ASSUMES.items():
                print(f"{k:22} {v}")
            print(f"\nkey permissions: pull {PERMISSIONS['pull']}, push {PERMISSIONS['push']}")
            return 0
        if a.cmd == "check":
            lines = check(Client.from_env())
        elif a.cmd == "verify":
            lines = review(a.limit)
        elif a.cmd == "pull":
            lines = pull(Client.from_env(), s, dry_run=a.dry_run, job=a.job)
        else:
            lines = push(Client.from_env() if a.send else None, s, send=a.send, by=a.by)
    except AshbyError as e:
        print(f"ashby: {e}", file=sys.stderr)
        return 1
    for line in lines or ["nothing new in Ashby"]:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
