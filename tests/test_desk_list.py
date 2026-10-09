"""The Applications list and the "More details" page, as a partner in a hurry reads them.

A row carries what is needed to vote and nothing else; the rest is one click
away. The role filter lives in the address. On "More details", each partner's
vote and comment are shown -- once the reader has voted. Each test was checked
once by hand to fail with the line it guards removed. Every name is invented.
"""

from __future__ import annotations

import http.client
import re
import json
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import desk
from console.desk_web import AI_HINT, Desk
from desk import Access, Application, Config, Document, Person, cast
from pipeline import Event, Pipeline

REPO = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
TITLES = {"fa": "Founders' Associate"}


def cfg() -> Config:
    return Config(voters=["Ana", "Ben", "Cy"],
                  labels={"contact": "Yes, let's talk",
                          "discuss": "Not sure, let's discuss as a team",
                          "later": "Not yet", "pass": "Not for us"})


def pipe() -> Pipeline:
    p = Pipeline("fa")
    p.add(Event(candidate_id="sam", kind="received", at="2026-09-20T10:00:00+00:00"))
    p.add(Event(candidate_id="sam", kind="screened", at="2026-09-20T10:00:00+00:00",
                detail={"score": 0.62}))
    return p


def sam(**kw) -> Person:
    p = Person("sam", "Sam Lee", applications=[Application("fa", "sam")], **kw)
    p.documents.append(Document("fa", "cv", "sam/fa_cv_1.pdf", "Sam CV.pdf"))
    return p


def row(person: Person, p: Pipeline, viewer: str = "Ana", c: Config | None = None) -> str:
    return Desk(c or cfg(), Access()).row(person, Application("fa", "sam"), p.standing("sam"),
                                          viewer, TITLES, NOW)


# -- the row ------------------------------------------------------------------------

def test_a_row_carries_name_role_state_documents_fit_votes_and_more_details():
    html = row(sam(), pipe())
    assert ">Sam Lee</a>" in html
    assert '<span class="role">Founders&#x27; Associate</span>' in html
    assert '<span class="standing">New</span>' in html
    assert ">CV</a>" in html and "no “Why us” answer" in html
    # The guess is counted from the documents (match.py): with none readable, it says so.
    assert ">no AI guess</span>" in html
    for word in ("Yes, let&#x27;s talk", "Not sure, let&#x27;s discuss as a team",
                 ">Not yet</button>", ">Not for us</button>"):
        assert word in html
    assert 'name="comment"' in html and "Comment (optional, one line)" in html
    assert ">More details →</a>" in html and "/person/sam?as=Ana#app-fa" in html


def test_what_they_built_and_want_is_not_on_the_row():
    html = row(sam(), pipe())
    for gone in ("Built", "Wants to own", "AI in their work", 'class="sig"', "<q"):
        assert gone not in html


def test_a_repository_and_a_profile_are_one_click_each():
    html = row(sam(link="https://github.com/sam-lee/ledger-replay",
                   links=["https://www.linkedin.com/in/sam-lee-example"]), pipe())
    assert 'href="https://github.com/sam-lee/ledger-replay"' in html and ">GitHub</a>" in html
    assert ">LinkedIn</a>" in html
    # A site is not a repository: it waits on "More details".
    html = row(sam(link="https://sam-lee.example/demo"), pipe())
    assert ">GitHub</a>" not in html and "sam-lee.example" not in html


def test_the_row_counts_votes_without_saying_whose_or_what():
    p, c = pipe(), cfg()
    cast(p, "sam", "Ben", "contact", c, comment="thin on writing")
    cast(p, "sam", "Cy", "contact", c)
    html = row(sam(), p, c=c)
    assert '<span class="standing">Voting · 2 of 3</span>' in html
    # The form token is random: "Cy" can turn up in it by chance.
    html = re.sub(r'name="t" value="[^"]*"', "", html)
    for leak in ("Ben", "Cy", "thin on writing", "Your vote"):
        assert leak not in html


def test_two_different_votes_or_one_not_sure_go_to_the_team_without_waiting():
    p, c = pipe(), cfg()
    cast(p, "sam", "Ben", "discuss", c)
    assert '<span class="standing">Team to decide</span>' in row(sam(), p, c=c)
    p = pipe()
    cast(p, "sam", "Ben", "pass", c, reason="not this role", comment="thin on writing")
    cast(p, "sam", "Cy", "contact", c)
    html = row(sam(), p, c=c)
    assert '<span class="standing">Team to decide</span>' in html
    # It says the votes differ, never whose or what.
    html = re.sub(r'name="t" value="[^"]*"', "", html)
    for leak in ("Ben", "Cy", "not this role", "thin on writing", "Your vote"):
        assert leak not in html


def test_once_you_voted_the_row_says_your_vote_and_folds_the_buttons():
    p, c = pipe(), cfg()
    cast(p, "sam", "Ana", "contact", c, comment="the replay notes")
    html = row(sam(), p, c=c)
    assert "Your vote: <b>Yes, let&#x27;s talk</b>, “the replay notes”" in html
    assert "Change my vote" in html


# -- "More details" ---------------------------------------------------------------

def card(p: Pipeline, viewer: str, c: Config | None = None) -> str:
    return Desk(c or cfg(), Access()).application_card(sam(), Application("fa", "sam"),
                                                       p.standing("sam"), viewer, TITLES, NOW)


def test_each_partners_vote_and_comment_show_once_the_reader_has_voted():
    p, c = pipe(), cfg()
    cast(p, "sam", "Ben", "pass", c, reason="not this role", comment="thin on writing")
    before = card(p, "Ana", c)
    assert "voted, hidden until you vote" in before and "votes and comments show once" in before
    assert "thin on writing" not in before and "not this role" not in before
    cast(p, "sam", "Ana", "contact", c, comment="built the replay tool")
    after = card(p, "Ana", c)
    assert "<th>Ben</th>" in after and "not this role, thin on writing" in after
    assert "built the replay tool" in after and "<th>Cy</th>" in after
    assert "not voted yet" in after


def test_the_page_reads_top_down_state_documents_votes_words_trial_day():
    html = card(pipe(), "Ana")
    order = [html.index(x) for x in ('class="nxt"', "Next step:", 'action="/vote"',
                                     "<h2>Documents and links</h2>",
                                     "<h2>Votes and comments</h2>",
                                     "<h2>In their own words</h2>")]
    assert order == sorted(order)
    # The trial-day proposals live on their own tab, not on a person's page.
    assert 'class="tt"' not in html
    assert 'class="sig"' in html and "Built" in html


# -- through the server: the role filter, and a comment with a yes ------------------

@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, "ROOT", tmp_path)
    gen = tmp_path / "mandates" / "generated"
    gen.mkdir(parents=True)
    for pid, title in (("fa", "Founders' Associate"), ("be", "Backend Engineer")):
        (gen / f"role_{pid}.provenance.json").write_text(
            json.dumps({"posting_id": pid, "title": title}), encoding="utf-8")
    toml = (REPO / "desk.toml").read_text(encoding="utf-8")
    (tmp_path / "desk.toml").write_text(
        toml.replace('voters = ["Recruiter"]', 'voters = ["Ana", "Ben", "Cy"]'),
        encoding="utf-8")
    return tmp_path


def start(c: Config) -> tuple[str, ThreadingHTTPServer]:
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


def call(where: str, method: str, path: str, form: dict[str, str] | None = None
         ) -> tuple[int, str, str]:
    host, port = where.split(":")
    con = http.client.HTTPConnection(host, int(port), timeout=10)
    body = urllib.parse.urlencode(form).encode() if form is not None else None
    con.request(method, path, body=body, headers={
        "Host": where, "Content-Type": "application/x-www-form-urlencoded"})
    r = con.getresponse()
    out = r.status, r.getheader("Location") or "", r.read().decode("utf-8", "replace")
    con.close()
    return out


def test_the_role_filter_keeps_one_role_and_survives_the_tabs(home):
    c = desk.load_config()
    desk.add_now("fa", "Sam Lee", "sam@x.example")
    desk.add_now("be", "Kim Ito", "kim@x.example")
    where, srv = start(c)
    try:
        every = call(where, "GET", "/?as=Ana&tab=all")[2]
        assert ">Sam Lee</a>" in every and ">Kim Ito</a>" in every
        assert '<option value="be">Backend Engineer (1)</option>' in every
        one = call(where, "GET", "/?as=Ana&tab=all&role=be")[2]
        assert ">Kim Ito</a>" in one and ">Sam Lee</a>" not in one
        assert '<option value="be" selected>' in one
        assert 'href="/?tab=todo&role=be&as=Ana"' in one
        # After a vote, back to the same filtered list.
        assert 'name="back" value="/?tab=all&amp;role=be"' in one
        # An unknown role is no filter, not an empty page.
        assert ">Sam Lee</a>" in call(where, "GET", "/?as=Ana&tab=all&role=zz")[2]
    finally:
        srv.shutdown()


def test_a_comment_goes_with_any_vote_not_only_later_and_no(home):
    c = desk.load_config()
    p = desk.add_now("fa", "Sam Lee", "sam@x.example")
    cid = p.applications[0].candidate_id
    where, srv = start(c)
    try:
        page = call(where, "GET", "/?as=Ana&tab=all")[2]
        token = page.split('name="t" value="')[1].split('"')[0]
        code, loc, _ = call(where, "POST", "/vote", {
            "t": token, "as": "Ana", "posting": "fa", "candidate": cid, "label": "contact",
            "comment": "the replay notes are the best thing in the pile", "back": "/"})
        assert code == 303 and "error" not in loc
        v = desk.votes(desk._pipelines()["fa"].standing(cid))["Ana"]
        assert v.meaning == "contact" and v.comment.startswith("the replay notes")
        # Enter in the comment box presses the hidden first button: nothing is recorded.
        code, loc, _ = call(where, "POST", "/vote", {
            "t": token, "as": "Ana", "posting": "fa", "candidate": cid, "label": "",
            "comment": "x", "back": "/"})
        assert "error=" in loc and "nothing+recorded" in loc.replace("%20", "+")
    finally:
        srv.shutdown()


# -- Trial days: optional, so last -------------------------------------------

def test_there_is_no_trial_days_tab(home):
    # The desk stops at the invitation to a trial day: what the day is about is not its business.
    c = desk.load_config()
    where, srv = start(c)
    try:
        page = call(where, "GET", "/?as=Ana")[2]
        nav = page.split("<nav>")[1].split("</nav>")[0]
        assert "Trial days" not in nav and "/blockers" not in nav
        # House rules is the last entry; its guided questions are a part of it.
        assert nav.rstrip().endswith(">House rules</a>") and "Your call" not in nav
        assert call(where, "GET", "/blockers?as=Ana")[0] == 404
    finally:
        srv.shutdown()


# -- mails: one per outcome, no acknowledgment ------------------------------------

def test_no_acknowledgment_mail_is_offered_anywhere(home):
    import tomllib
    raw = tomllib.loads((REPO / "desk.toml").read_text(encoding="utf-8"))
    mails = {"contact", "follow_up", "interview", "trial_day", "later", "pass"}
    assert set(raw["mail"]["templates"]) == mails
    assert set(raw["mail"]["subjects"]) == mails
    c = desk.load_config()
    page = Desk(c, Access()).settings("Ana")
    assert "When an application arrives" not in page and 'name="template_received"' not in page
    p = desk.add_now("fa", "Sam Lee", "sam@x.example")
    with pytest.raises(desk.DeskError, match="Ashby already sends"):
        desk.draft_for(p.applications[0].candidate_id, "fa", "received", c)


def test_an_old_acknowledgment_in_the_log_still_reads(home):
    # Logs written while the desk drafted acknowledgments stay readable: the
    # "sent" mark is in the history, and it is not taken for a contact.
    import stages
    c = desk.load_config()
    p = desk.add_now("fa", "Sam Lee", "sam@x.example")
    cid = p.applications[0].candidate_id
    with desk.locked():
        pipe = desk._pipelines()["fa"]
        pipe.add(Event(candidate_id=cid, kind="contacted", by="Ana",
                       detail={"about": "received"}))
        desk._save(pipe)
    s = desk._pipelines()["fa"].standing(cid)
    assert stages.derive(s, c).id == "new"
    reg = desk.load_registry(desk._registry_path())
    said = [m.text for m in desk.timeline(reg.people[p.person_id], desk._pipelines(), c,
                                          "Ana", {})]
    assert "Ana wrote to them" in said
