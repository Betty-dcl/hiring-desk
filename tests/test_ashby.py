"""The Ashby connector, against the shapes Ashby publishes -- never a live account.

The fake below answers with the request and response shapes of Ashby's
OpenAPI reference (updated 2026-05-27). What it cannot tell us is whether a
real account answers the same way; `ashby.ASSUMES` lists what to check first.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from pathlib import Path

import pytest

import ashby
import desk

ROOT = Path(__file__).resolve().parent.parent
CV = b"%PDF-1.4\n% Sylvia Hartmann, invented\n"

APP_ID = "0f5c2f7e-1b6a-4c3d-9e8f-111111111111"
CAND_ID = "7d4e9a10-2c3b-4d5e-8f9a-222222222222"
JOB_ID = "3a2b1c0d-4e5f-4a6b-9c8d-333333333333"


class FakeAshby:
    """Answers like the reference says Ashby answers. Records every call."""

    def __init__(self, *, job_title="Founders' Associate", status="Active", resume=True,
                 pages=1, broken_download=False):
        self.calls: list[tuple[str, dict]] = []
        self.job_title, self.status, self.resume = job_title, status, resume
        self.pages, self.broken_download = pages, broken_download
        self.expire_token = False
        self.notes: list[dict] = []

    def application(self):
        return {"id": APP_ID, "createdAt": "2026-09-24T08:00:00.000Z", "status": self.status,
                "candidate": {"id": CAND_ID, "name": "Sylvia Hartmann",
                              "primaryEmailAddress": {"value": "sylvia@example.org",
                                                      "type": "Personal", "isPrimary": True}},
                "job": {"id": JOB_ID, "title": self.job_title}}

    def __call__(self, method, body):
        self.calls.append((method, body))
        if method == "application.list":
            if self.expire_token and body.get("syncToken"):
                return {"success": False, "errors": ["sync_token_expired"],
                        "errorInfo": {"code": "sync_token_expired", "message": "expired"}}
            page = int(body.get("cursor") or 0)
            more = page + 1 < self.pages
            r = {"success": True, "results": [self.application()] if page == 0 else [],
                 "moreDataAvailable": more}
            if more:
                r["nextCursor"] = str(page + 1)
            else:
                r["syncToken"] = "tok-after"
            return r
        if method == "application.info":
            app = self.application()
            if self.resume:
                app["resumeFileHandle"] = {"id": "f1", "name": "Sylvia CV.pdf", "handle": "h-cv"}
            app["applicationFormSubmissions"] = [{
                "id": "s1",
                "formDefinition": {"sections": [{"title": "Application", "fields": [
                    {"isRequired": True, "field": {"id": "a", "type": "LongText",
                                                   "path": "_systemfield_cover", "title": "Cover letter"}},
                    {"isRequired": False, "field": {"id": "b", "type": "String",
                                                    "path": "_systemfield_pronoun", "title": "Pronouns"}},
                ]}]},
                "submittedValues": {"_systemfield_cover": "I rebuilt a budget nobody asked for.",
                                    "_systemfield_pronoun": "she/her"},
            }]
            return {"success": True, "results": app}
        if method == "candidate.info":
            return {"success": True, "results": {
                "id": CAND_ID, "name": "Sylvia Hartmann",
                "primaryEmailAddress": {"value": "sylvia@example.org", "type": "Personal",
                                        "isPrimary": True},
                "socialLinks": [{"type": "GitHub", "url": "https://github.com/x"},
                                {"type": "LinkedIn", "url": "https://www.linkedin.com/in/sylvia-example"}]}}
        if method == "file.info":
            return {"success": True, "results": {"url": "https://files.example/cv.pdf"}}
        if method == "candidate.createNote":
            self.notes.append(body)
            return {"success": True, "results": {"id": "n1", "content": body["note"]["value"]}}
        return {"success": False, "errors": [f"unknown method {method}"]}

    def download(self, url):
        if self.broken_download:
            raise OSError("link expired")
        return CV


def client(fake):
    return ashby.Client(fake, fake.download)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, "ROOT", tmp_path)
    monkeypatch.setattr(ashby, "ROOT", tmp_path)
    (tmp_path / "mandates" / "generated").mkdir(parents=True)
    (tmp_path / "mandates" / "generated" / "role_founders_associate.provenance.json").write_text(
        json.dumps({"posting_id": "founders_associate", "title": "Founders' Associate"}),
        encoding="utf-8")
    # Invented voters, never the shipped ones: a test log names whoever voted.
    toml = (ROOT / "desk.toml").read_text(encoding="utf-8")
    shipped = re.search(r'^voters = \[.*\]$', toml, re.M)
    assert shipped, "desk.toml [team] voters not found"
    (tmp_path / "desk.toml").write_text(
        toml.replace(shipped.group(0), 'voters = ["Ana", "Ben", "Cy"]'), encoding="utf-8")
    assert desk.load_config().voters == ["Ana", "Ben", "Cy"]
    return tmp_path


S = ashby.Settings()


# -- webhooks ---------------------------------------------------------------

def sign(raw: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


RAW = json.dumps({"action": "applicationSubmit",
                  "data": {"application": {"id": APP_ID}}}).encode()


def test_a_signed_webhook_is_accepted():
    assert ashby.verify(RAW, sign(RAW, "s3cret"), "s3cret")


@pytest.mark.parametrize("header,secret", [
    ("sha256=" + "0" * 64, "s3cret"),          # wrong digest
    (sign(RAW, "other"), "s3cret"),            # another secret
    ("", "s3cret"),                            # no header
    ("md5=abc", "s3cret"),                     # another algorithm
    (sign(RAW, ""), ""),                       # no secret configured: refuse, never accept
])
def test_anything_else_is_refused(header, secret):
    assert not ashby.verify(RAW, header, secret)


def test_a_body_changed_after_signing_is_refused():
    assert not ashby.verify(RAW + b" ", sign(RAW, "s3cret"), "s3cret")


def test_a_ping_or_another_event_is_not_an_application():
    assert ashby.application_id_of(RAW) == APP_ID
    assert ashby.application_id_of(b'{"action": "ping", "data": {}}') == ""
    assert ashby.application_id_of(b'{"action": "candidateHire", "data": {"application": {"id": "x"}}}') == ""
    assert ashby.application_id_of(b"not json") == ""


def test_an_unsigned_webhook_puts_nothing_on_the_desk(home):
    fake = FakeAshby()
    status, _ = ashby.on_webhook(RAW, {"Ashby-Signature": "sha256=bad"}, secret="s3cret",
                                 client=client(fake), s=S)
    assert status == 401 and fake.calls == []


def test_without_a_secret_every_webhook_is_refused(home):
    status, msg = ashby.on_webhook(RAW, {}, secret="", client=client(FakeAshby()), s=S)
    assert status == 401 and "no webhook secret" in msg


def test_a_signed_webhook_is_only_a_pointer_the_api_is_asked_again(home):
    fake = FakeAshby()
    status, msg = ashby.on_webhook(RAW, {"ashby-signature": sign(RAW, "k")}, secret="k",
                                   client=client(fake), s=S)
    assert (status, msg) == (200, "added")
    assert [m for m, _ in fake.calls][:2] == ["application.info", "candidate.info"]
    again = ashby.on_webhook(RAW, {"Ashby-Signature": sign(RAW, "k")}, secret="k",
                             client=client(fake), s=S)
    assert again == (200, "already on the desk")


# -- the API ------------------------------------------------------------------

def test_every_page_is_read_and_the_new_token_kept():
    fake = FakeAshby(pages=3)
    apps, token = client(fake).applications()
    assert len(apps) == 1 and token == "tok-after"
    assert [b.get("cursor") for m, b in fake.calls] == [None, "1", "2"]


def test_an_expired_sync_token_restarts_as_a_full_sync():
    fake = FakeAshby()
    fake.expire_token = True
    apps, token = client(fake).applications("old-token")
    assert len(apps) == 1 and token == "tok-after"
    assert "syncToken" not in fake.calls[-1][1]


def test_an_error_names_its_code():
    c = ashby.Client(lambda m, b: {"success": False, "errors": ["x"],
                                   "errorInfo": {"code": "forbidden"}})
    with pytest.raises(ashby.AshbyError, match="forbidden"):
        c.call("candidate.info", {"id": CAND_ID})


def test_the_note_request_uses_only_fields_the_reference_allows(home):
    # candidate.createNote has additionalProperties: false.
    allowed = {"candidateId", "note", "sendNotifications", "isPrivate", "createdAt"}
    _agree(home)
    (n,) = ashby.outgoing(S, ashby.load_state())
    assert set(n.body) <= allowed and set(n.body["note"]) == {"type", "value"}
    assert n.body["note"]["type"] in ("text/plain", "text/html")


# -- in -----------------------------------------------------------------------

def test_an_application_comes_with_its_cv_its_letter_and_its_link():
    inc = ashby.fetch(client(FakeAshby()), APP_ID, S)
    assert (inc.name, inc.email) == ("Sylvia Hartmann", "sylvia@example.org")
    # LinkedIn first, and the GitHub is kept: it is what the card leads with.
    assert inc.link == "https://www.linkedin.com/in/sylvia-example https://github.com/x"
    kinds = {k: data for k, _, data in inc.files}
    assert kinds["cv"] == CV
    assert b"rebuilt a budget" in kinds["letter"] and b"she/her" not in kinds["letter"]
    assert inc.missing == []


def test_a_cv_that_will_not_download_does_not_lose_the_application():
    inc = ashby.fetch(client(FakeAshby(broken_download=True)), APP_ID, S)
    assert [k for k, _, _ in inc.files] == ["letter"]
    assert "CV not downloaded" in inc.missing[0]


def test_no_cv_is_said_not_guessed():
    inc = ashby.fetch(client(FakeAshby(resume=False)), APP_ID, S)
    assert inc.missing == ["no CV on the application"]


@pytest.mark.parametrize("title,jobs,want", [
    ("Founders' Associate", {}, "founders_associate"),
    ("founders' associate", {}, "founders_associate"),
    ("Founders Associate", {}, ""),                            # near is not the same
    ("Founders Associate", {"Founders Associate": "founders_associate"}, "founders_associate"),
    ("Anything", {JOB_ID: "founders_associate"}, "founders_associate"),
])
def test_a_job_is_matched_exactly_or_by_a_mapping_someone_wrote(title, jobs, want):
    inc = ashby.Incoming(APP_ID, CAND_ID, "S", "", JOB_ID, title, "Active")
    s = ashby.Settings(jobs=jobs)
    assert ashby.posting_for(inc, {"founders_associate": "Founders' Associate"}, s) == want


def test_pull_puts_the_application_on_the_desk_once(home):
    fake = FakeAshby()
    assert ashby.pull(client(fake), S) == ["Sylvia Hartmann: added to Founders' Associate"]
    reg = desk.load_registry(desk._registry_path())
    (p,) = reg.people.values()
    assert p.email == "sylvia@example.org" and p.link.startswith("https://www.linkedin.com/")
    assert p.links == ["https://github.com/x"]
    assert sorted(d.kind for d in p.documents) == ["cv", "letter"]
    assert ashby.load_state()["sync_token"] == "tok-after"
    assert ashby.pull(client(fake), S) == []   # seen: not fetched again


def test_a_job_the_desk_does_not_know_stays_in_ashby(home):
    report = ashby.pull(client(FakeAshby(job_title="Head of Sales")), S)
    assert "not a posting on the desk" in report[0]
    assert not desk._registry_path().exists()


def test_an_archived_application_is_not_brought_in(home):
    assert ashby.pull(client(FakeAshby(status="Archived")), S) == []


def test_a_dry_run_writes_nothing_anywhere(home):
    report = ashby.pull(client(FakeAshby()), S, dry_run=True)
    assert "would be added" in report[0] and "cv, letter" in report[0]
    assert not desk._registry_path().exists() and not ashby._state_path().exists()


def test_pull_only_ever_reads_ashby(home):
    fake = FakeAshby()
    ashby.pull(client(fake), S)
    assert {m for m, _ in fake.calls} <= {"application.list", "application.info",
                                          "candidate.info", "file.info"}


# -- out ----------------------------------------------------------------------

def _agree(home, labels=("contact", "contact", "contact")):
    ashby.pull(client(FakeAshby()), S)
    cfg = desk.load_config()
    pid = next(iter(desk.load_registry(desk._registry_path()).people))
    for voter, label in zip(cfg.voters, labels):
        desk.vote_now("founders_associate", pid, voter, label, cfg)
    return pid


def test_nothing_goes_back_while_people_are_still_voting(home):
    ashby.pull(client(FakeAshby()), S)
    cfg = desk.load_config()
    pid = next(iter(desk.load_registry(desk._registry_path()).people))
    desk.vote_now("founders_associate", pid, cfg.voters[0], "contact", cfg)
    assert ashby.outgoing(S) == []


def test_a_disagreement_never_goes_back(home):
    _agree(home, ("contact", "pass", "contact"))
    assert ashby.outgoing(S) == []


def test_an_agreed_class_is_printed_and_not_sent_by_default(home):
    _agree(home)
    fake = FakeAshby()
    lines = ashby.push(client(fake), S)
    assert "would write" in lines[0] and "Yes, let's talk" in lines[0]
    assert "nothing here was decided by the machine" in lines[0]
    assert fake.notes == []


def test_sending_needs_a_named_voter(home):
    _agree(home)
    with pytest.raises(ashby.AshbyError, match="named person"):
        ashby.push(client(FakeAshby()), S, send=True)
    with pytest.raises(desk.DeskError):
        ashby.push(client(FakeAshby()), S, send=True, by="Somebody Else")


def test_a_note_is_written_once_and_again_only_if_the_class_changes(home):
    pid = _agree(home)
    fake = FakeAshby()
    cfg = desk.load_config()
    assert ashby.push(client(fake), S, send=True, by="Ana")[0].endswith("sent by Ana")
    assert fake.notes[0]["candidateId"] == CAND_ID
    assert ashby.push(client(fake), S, send=True, by=cfg.voters[0]) == [
        "nothing settled that Ashby does not already have"]
    for v in cfg.voters:
        desk.vote_now("founders_associate", pid, v, "pass", cfg)
    ashby.push(client(fake), S, send=True, by=cfg.voters[1])
    assert len(fake.notes) == 2 and "Classed Not for us by" in fake.notes[1]["note"]["value"]


def test_the_note_is_private_and_silent_unless_set_otherwise(home):
    _agree(home)
    (n,) = ashby.outgoing(S)
    assert n.body["isPrivate"] is True and n.body["sendNotifications"] is False
    (n,) = ashby.outgoing(ashby.Settings(note_private=False, notify=True))
    assert n.body["isPrivate"] is False and n.body["sendNotifications"] is True


def test_comments_stay_on_the_desk_unless_asked_for(home):
    ashby.pull(client(FakeAshby()), S)
    cfg = desk.load_config()
    pid = next(iter(desk.load_registry(desk._registry_path()).people))
    for v in cfg.voters:
        desk.vote_now("founders_associate", pid, v, "contact", cfg, comment="strong budget story")
    (n,) = ashby.outgoing(S)
    assert "strong budget story" not in n.body["note"]["value"]
    (n,) = ashby.outgoing(ashby.Settings(note_with_comments=True))
    assert "strong budget story" in n.body["note"]["value"]


def test_the_connector_says_what_it_takes_on_trust():
    assert "NOT DOCUMENTED" in ashby.ASSUMES["form answers"]
    assert ashby.PERMISSIONS == {"pull": ["candidatesRead"], "push": ["candidatesWrite"]}


def test_a_missing_key_is_said_plainly(monkeypatch, tmp_path):
    monkeypatch.delenv("ASHBY_API_KEY", raising=False)
    monkeypatch.setattr(ashby, "KEY_FILE", tmp_path / "none")
    with pytest.raises(ashby.AshbyError, match="ASHBY_API_KEY"):
        ashby.Client.from_env()


# -- through the desk's own server ---------------------------------------------

def _serve(home):
    import threading
    from http.server import ThreadingHTTPServer

    from console import desk_web
    cfg = desk.load_config()
    started, real_init = {}, ThreadingHTTPServer.__init__

    def capture(self, addr, handler):
        real_init(self, ("127.0.0.1", 0), handler)
        started["port"] = self.server_address[1]
        started["srv"] = self

    ThreadingHTTPServer.__init__ = capture
    try:
        threading.Thread(target=desk_web.serve, args=("127.0.0.1", 0, cfg, desk.Access()),
                         daemon=True).start()
        while "port" not in started:
            pass
    finally:
        ThreadingHTTPServer.__init__ = real_init
    return f"http://127.0.0.1:{started['port']}", started["srv"]


def _post(url, raw, headers):
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, data=raw, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_the_desk_refuses_an_unsigned_webhook_and_needs_no_login_for_a_signed_one(
        home, monkeypatch):
    monkeypatch.setenv("ASHBY_WEBHOOK_SECRET", "k")
    monkeypatch.delenv("ASHBY_API_KEY", raising=False)
    base, srv = _serve(home)
    try:
        assert _post(f"{base}/webhook/ashby", RAW, {"Ashby-Signature": "sha256=00"})[0] == 401
        code, msg = _post(f"{base}/webhook/ashby", RAW, {"Ashby-Signature": sign(RAW, "k")})
        # Signed, but no key to ask Ashby with: nothing is taken from the body alone.
        assert code == 503 and "stays in Ashby" in msg
        assert not desk._registry_path().exists()
    finally:
        srv.shutdown()


# -- the first connection ---------------------------------------------------------

def test_step_one_only_asks_whether_the_key_reads(home):
    fake = FakeAshby()
    lines = ashby.check(client(fake))
    assert "the key works" in lines[0] and fake.calls == [("application.list", {"limit": 1})]
    assert not desk._registry_path().exists()


def test_a_first_pull_on_one_role_leaves_the_others_for_later(home):
    other = ashby.pull(client(FakeAshby()), S, job="Marketing Lead")
    assert other == [] and not ashby.load_state().get("sync_token")
    lines = ashby.pull(client(FakeAshby()), S, job="founders' associate")
    assert lines == ["Sylvia Hartmann: added to Founders' Associate"]
    # The sync point did not move: a full pull later still sees every role.
    assert not ashby.load_state().get("sync_token")


def test_step_three_shows_what_is_on_file_to_compare_with_ashby(home):
    ashby.pull(client(FakeAshby()), S)
    (line,) = ashby.review()
    assert line.startswith("Sylvia Hartmann | Founders' Associate | CV: Sylvia CV.pdf (")
    assert "form: application-form.txt" in line and CAND_ID in line
    for f in desk._files_dir().rglob("*"):
        if f.is_file():
            f.unlink()
    assert "MISSING ON DISK" in ashby.review()[0]


# -- by itself (daily.py) ---------------------------------------------------------

ON = ashby.Settings(auto_pull=True)


class Down:
    """Ashby, or the network, not answering."""

    def __call__(self, method, body):
        raise OSError("network unreachable")


def test_the_automatic_pull_is_off_until_someone_switches_it_on(home):
    fake = FakeAshby()
    assert ashby.load_settings(home / "desk.toml").auto_pull is False
    assert ashby.auto(S, client(fake)) == [] and fake.calls == []
    assert not desk._registry_path().exists()


def test_switched_on_it_brings_the_new_ones_and_notes_counts_not_names(home):
    fake = FakeAshby()
    assert ashby.auto(ON, client(fake)) == ["Ashby: 1 new application on the desk"]
    last = ashby.load_state()["last_pull"]
    assert last["added"] == 1 and last["error"] == "" and "Sylvia" not in json.dumps(last)
    assert ashby.auto(ON, client(fake)) == ["Ashby: 0 new applications on the desk"]
    assert {m for m, _ in fake.calls} <= {"application.list", "application.info",
                                          "candidate.info", "file.info"}


def test_a_role_the_desk_does_not_know_is_counted_and_left_in_ashby(home):
    (line,) = ashby.auto(ON, client(FakeAshby(job_title="Head of Sales")))
    assert "1 for a role the desk does not know, left in Ashby" in line


def test_ashby_down_never_stops_the_desk_and_the_error_is_kept(home):
    (line,) = ashby.auto(ON, ashby.Client(Down()))
    assert line.startswith("Ashby: not reached (network unreachable)")
    assert ashby.load_state()["last_pull"]["error"] == "network unreachable"
    assert ashby.load_state()["last_pull"]["added"] == 0


def test_no_key_is_an_error_kept_not_a_crash(home, monkeypatch, tmp_path):
    monkeypatch.delenv("ASHBY_API_KEY", raising=False)
    monkeypatch.setattr(ashby, "KEY_FILE", tmp_path / "none")
    (line,) = ashby.auto(ON)
    assert "not reached" in line and "ASHBY_API_KEY" in line


def test_a_pull_stopped_part_way_keeps_who_came_in_and_does_not_move_the_sync_point(home):
    class Breaks(FakeAshby):
        def download(self, url):
            raise KeyboardInterrupt  # anything that is not a missing CV

    with pytest.raises(KeyboardInterrupt):
        ashby.pull(client(Breaks()), S)
    state = ashby.load_state()
    assert state["last_pull"]["error"] == "stopped part way" and not state.get("sync_token")


def test_the_key_can_live_in_a_file_outside_the_program(monkeypatch, tmp_path):
    # In the home folder: never in the program, so never in a backup or a commit.
    assert not ashby.KEY_FILE.is_relative_to(ROOT) and ashby.KEY_FILE.is_relative_to(Path.home())
    monkeypatch.delenv("ASHBY_API_KEY", raising=False)
    f = tmp_path / ".hiringdesk" / "ashby_key"
    f.parent.mkdir()
    f.write_text("  key-from-file\n", encoding="utf-8")
    monkeypatch.setattr(ashby, "KEY_FILE", f)
    assert ashby.key() == "key-from-file"
    monkeypatch.setenv("ASHBY_API_KEY", "key-from-env")
    assert ashby.key() == "key-from-env"


def test_daily_pulls_then_backs_up_and_ashby_down_does_not_stop_the_backup(home, monkeypatch,
                                                                         tmp_path):
    import backup
    import check
    import daily
    monkeypatch.setattr(backup, "ROOT", home)
    monkeypatch.delenv("ASHBY_API_KEY", raising=False)
    monkeypatch.setattr(ashby, "KEY_FILE", tmp_path / "none")
    import install
    toml = (home / "desk.toml").read_text(encoding="utf-8")
    (home / "desk.toml").write_text(toml.replace("auto_pull = false", "auto_pull = true"),
                                    encoding="utf-8")
    drive = tmp_path / "drive"
    install.set_backup_folder(home, drive)
    ashby.pull(client(FakeAshby()), S)   # someone on the desk, so the backup has data
    lines = daily.run(home)
    assert "not reached" in lines[0] and lines[1].startswith("backup written and checked")
    assert len(backup.listing(drive)) == 1
    got = {r.what: r for r in check.run()}
    assert got["Ashby"].level == "WARN" and "did not finish" in got["Ashby"].detail
