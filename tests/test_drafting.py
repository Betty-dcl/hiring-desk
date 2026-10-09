"""Drafts written by AI: off by default, a draft only, and checked against its sources.

Only a fake client is used here: no model is called by these tests. Each
guard was checked once by hand to fail with its line removed. Every name is
invented.
"""

from __future__ import annotations

import http.client
import json
import threading
import time
import urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import desk
import drafting
from console import drafting_web
from desk import Access, DeskError

REPO = Path(__file__).resolve().parent.parent
LETTER = (b"Hello,\n\nI'd want to own the month-end close and the supplier side end to end. "
          b"I built a reconciliation agent with Claude Code last year.\n")


class Fake:
    """Stands in for nbh.llm's clients: records the call, returns what it is told."""

    def __init__(self, subject="Founders' Associate at Example Co: next step", body=None):
        self.calls: list[dict] = []
        self.subject = subject
        self.body = body or ("Hi Sam,\n\nThank you for applying for Founders' Associate. We would "
                             "like to talk with you about owning the month-end close.\n\nAna")

    def structured(self, **kw):
        self.calls.append(kw)
        return {"subject": self.subject, "body": self.body}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, "ROOT", tmp_path)
    drafting_web.DRAFTS.clear()
    gen = tmp_path / "mandates" / "generated"
    gen.mkdir(parents=True)
    (gen / "role_fa.provenance.json").write_text(
        json.dumps({"posting_id": "fa", "title": "Founders' Associate"}), encoding="utf-8")
    toml = (REPO / "desk.toml").read_text(encoding="utf-8")
    toml = toml.replace('voters = ["Recruiter"]', 'voters = ["Ana", "Ben", "Cy"]')
    toml = toml.replace('company = "Causa Prima"', 'company = "Example Co"')
    toml = toml.replace('sender = ""', 'sender = "Ana"')
    (tmp_path / "desk.toml").write_text(toml, encoding="utf-8")
    return tmp_path


def turn_on(home: Path) -> None:
    f = home / "desk.toml"
    f.write_text(f.read_text(encoding="utf-8").replace("ai_drafts = false", "ai_drafts = true"),
                 encoding="utf-8")


def decided(meaning="contact", comment="the reconciliation agent is what we need"):
    c = desk.load_config()
    p = desk.add_now("fa", "Sam Lee", "sam@x.example",
                     files=[("letter", "letter.md", LETTER)])
    cid = p.applications[0].candidate_id
    for v in c.voters:
        desk.vote_now("fa", cid, v, meaning, c, comment=comment if v == "Ben" else "",
                      until="2027-03-01" if meaning == "later" else "")
    return c, p, cid


def test_off_by_default_and_said_so(home):
    shipped = (REPO / "desk.toml").read_text(encoding="utf-8")
    assert "\nai_drafts = false\n" in shipped
    assert not drafting.enabled()
    c, p, cid = decided()
    fake = Fake()
    with pytest.raises(DeskError, match="AI drafts are off"):
        drafting.draft(cid, "fa", c, by="Ana", client=fake)
    assert fake.calls == []


def test_the_prompt_carries_the_sources_and_only_them(home):
    turn_on(home)
    c, p, cid = decided()
    fake = Fake()
    drafting.draft(cid, "fa", c, by="Ana", client=fake)
    (call,) = fake.calls
    user = call["user"]
    assert "We would like to talk with them" in user
    assert "Role applied for: Founders' Associate" in user and "first name: Sam" in user
    assert "- Ben: the reconciliation agent is what we need" in user
    assert "I'd want to own the month-end close" in user
    assert "three slots that suit you" in user  # the team's template, for the tone
    assert "sam@x.example" not in user  # nothing the draft does not need
    assert "Use only the facts in the SOURCES" in call["system"]


def test_a_number_or_a_name_not_in_the_sources_is_flagged(home):
    turn_on(home)
    c, p, cid = decided()
    fake = Fake(body="Hi Sam,\n\nWith your 15 years in finance, we would love to meet you in "
                     "Lisbon about the month-end close.\n\nAna")
    dr = drafting.draft(cid, "fa", c, by="Ana", client=fake)
    assert "15" in dr.warnings and "Lisbon" in dr.warnings
    assert "Sam" not in dr.warnings and "Ana" not in dr.warnings


def test_a_draft_from_the_sources_carries_no_warning(home):
    turn_on(home)
    c, p, cid = decided()
    dr = drafting.draft(cid, "fa", c, by="Ana", client=Fake())
    assert dr.warnings == [] and dr.meaning == "contact"


def test_a_draft_touching_a_protected_subject_is_refused_whole(home):
    turn_on(home)
    c, p, cid = decided("pass", comment="not this role")
    fake = Fake(subject="Your application",
                body="Hi Sam,\n\nGiven your family plans, we will not go further.\n\nAna")
    with pytest.raises(DeskError, match="protected subject"):
        drafting.draft(cid, "fa", c, by="Ana", client=fake)
    log = (home / "runs" / "desk" / "ai_drafts.jsonl").read_text(encoding="utf-8")
    assert "refused: protected subject" in log


def test_who_asked_is_recorded_not_the_text(home):
    turn_on(home)
    c, p, cid = decided("later")
    drafting.draft(cid, "fa", c, by="Cy", client=Fake())
    (rec,) = [json.loads(x) for x in
              (home / "runs" / "desk" / "ai_drafts.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rec["by"] == "Cy" and rec["meaning"] == "later" and rec["candidate"] == cid
    assert "body" not in rec and "month-end" not in json.dumps(rec)


def test_no_draft_before_the_team_has_decided(home):
    turn_on(home)
    c = desk.load_config()
    p = desk.add_now("fa", "Sam Lee", "sam@x.example")
    fake = Fake()
    with pytest.raises(DeskError, match="decided outcome"):
        drafting.draft(p.applications[0].candidate_id, "fa", c, by="Ana", client=fake)
    assert fake.calls == []


def test_a_stranger_cannot_ask(home):
    turn_on(home)
    c, p, cid = decided()
    with pytest.raises(DeskError):
        drafting.draft(cid, "fa", c, by="Mallory", client=Fake())


# -- through the server ---------------------------------------------------------------

def start(c) -> tuple[str, ThreadingHTTPServer]:
    from console import desk_web
    started, real_init = {}, ThreadingHTTPServer.__init__

    def capture(self, addr, handler):
        real_init(self, ("127.0.0.1", 0), handler)
        started["port"], started["srv"] = self.server_address[1], self

    ThreadingHTTPServer.__init__ = capture
    try:
        threading.Thread(target=desk_web.serve, args=("127.0.0.1", 0, c, Access()),
                         daemon=True).start()
        while "port" not in started:
            time.sleep(0.01)
    finally:
        ThreadingHTTPServer.__init__ = real_init
    return f"127.0.0.1:{started['port']}", started["srv"]


def call(where, method, path, form=None):
    host, port = where.split(":")
    con = http.client.HTTPConnection(host, int(port), timeout=10)
    body = urllib.parse.urlencode(form).encode() if form is not None else None
    con.request(method, path, body=body, headers={
        "Host": where, "Content-Type": "application/x-www-form-urlencoded"})
    r = con.getresponse()
    out = r.status, r.getheader("Location") or "", r.read().decode("utf-8", "replace")
    con.close()
    return out


def test_the_button_shows_only_when_on_and_the_draft_lands_unsent(home, monkeypatch):
    c, p, cid = decided()
    where, srv = start(c)
    try:
        page = call(where, "GET", f"/person/{p.person_id}?as=Ana")[2]
        assert "Write the message" in page and "Draft with AI" not in page
        turn_on(home)
        fake = Fake()
        import nbh.llm
        monkeypatch.setattr(nbh.llm, "default_client", lambda **kw: fake)
        page = call(where, "GET", f"/person/{p.person_id}?as=Ana")[2]
        assert 'action="/draft-ai"' in page
        token = page.split('name="t" value="')[1].split('"')[0]
        code, loc, _ = call(where, "POST", "/draft-ai", {
            "t": token, "as": "Ana", "posting": "fa", "candidate": cid, "back": "/"})
        assert code == 303 and "AI+draft+ready" in loc.replace("%20", "+")
        assert len(fake.calls) == 1
        mine = call(where, "GET", f"/person/{p.person_id}?as=Ana")[2]
        assert "Drafted with AI for Ana" in mine and "owning the month-end close" in mine
        assert '<details class="writer" open>' in mine
        # Shown to whoever asked; recorded nowhere in the application's history.
        other = call(where, "GET", f"/person/{p.person_id}?as=Ben")[2]
        assert "Drafted with AI" not in other
        assert not any(e.kind == "contacted" for e in
                       desk._pipelines()["fa"].standing(cid).events)
    finally:
        srv.shutdown()


def test_a_protected_comment_in_an_old_log_never_reaches_the_model(home):
    # Comments are refused at the vote; a log written before that guard, or
    # edited by hand, is filtered again on the way to the model.
    from pipeline import Event
    turn_on(home)
    c, p, cid = decided()
    with desk.locked():
        pipe = desk._pipelines()["fa"]
        pipe.add(Event(candidate_id=cid, kind="voted", by="Cy",
                       detail={"label": "contact", "comment": "how old is she, though"}))
        desk._save(pipe)
    fake = Fake()
    drafting.draft(cid, "fa", c, by="Ana", client=fake)
    assert "how old" not in fake.calls[0]["user"]
