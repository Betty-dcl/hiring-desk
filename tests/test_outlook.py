"""Outlook: a hint that someone wrote back, from the smallest look at the mailbox.

Microsoft is never called here: `Fake` answers in the shapes outlook.ASSUMES
lists, and records every question asked, so the tests can say what the desk
asked for and, above all, what it did not. Every name is invented.
"""

from __future__ import annotations

import json
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import desk
import outlook
import stages
from console import outlook_web, track_web
from desk import Access, Application

REPO = Path(__file__).resolve().parent.parent
NOW = datetime.now(timezone.utc).replace(microsecond=0)


class Fake:
    """Microsoft, as the references describe it. `inbox` maps an address to mails."""

    def __init__(self, inbox: dict[str, list[str]] | None = None, signed_in: bool = True):
        self.inbox = inbox or {}
        self.signed_in = signed_in
        self.calls: list[tuple[str, str, dict | None, dict]] = []

    def __call__(self, method, url, form, headers):
        self.calls.append((method, url, form, headers))
        if url.endswith("/devicecode"):
            return {"device_code": "DC", "user_code": "ABCD-1234", "expires_in": 900,
                    "verification_uri": "https://microsoft.com/devicelogin", "interval": 5}
        if url.endswith("/token"):
            if form["grant_type"].endswith("device_code") and not self.signed_in:
                return {"error": "authorization_pending", "_status": 400}
            return {"access_token": f"AT{len(self.calls)}", "refresh_token": "RT",
                    "expires_in": 3600}
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        addr = q["$filter"][0].split("address eq '")[1].rstrip("'").replace("''", "'")
        since = q["$filter"][0].split("receivedDateTime ge ")[1].split(" ")[0]
        got = sorted((m for m in self.inbox.get(addr, []) if m >= since), reverse=True)
        return {"value": [{"receivedDateTime": got[0],
                           "webLink": "https://outlook.office365.com/owa/?ItemID=x"}]
                if got else []}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, "ROOT", tmp_path)
    (tmp_path / "mandates" / "generated").mkdir(parents=True)
    (tmp_path / "mandates" / "generated" / "role_fa.provenance.json").write_text(
        json.dumps({"posting_id": "fa", "title": "Founders' Associate"}), encoding="utf-8")
    toml = (REPO / "desk.toml").read_text(encoding="utf-8")
    toml = toml.replace('voters = ["Recruiter"]', 'voters = ["Ana"]')
    assert "[outlook]\n" in toml and 'client_id = ""' in toml
    toml = toml.replace('client_id = ""', 'client_id = "app-1"', 1)
    (tmp_path / "desk.toml").write_text(toml, encoding="utf-8")
    return tmp_path


def on() -> desk.Config:
    desk.save_settings({"outlook": True}, by="Ana")
    return desk.load_config()


def settings() -> outlook.Settings:
    return outlook.load_settings(desk.ROOT / "desk.toml")


def applicant(c, name: str, *, contacted: bool = True) -> str:
    p = desk.add_now("fa", name, f"{name.split()[0].lower()}@x.example")
    cid = p.applications[0].candidate_id
    desk.vote_now("fa", cid, "Ana", "contact", c)
    if contacted:
        stages.advance_now("fa", cid, "contacted", "Ana", c)  # today
    return cid


def signed_in(fake: Fake) -> None:
    outlook.connect(settings(), fake, NOW)
    outlook.finish(settings(), fake, NOW)


def test_it_is_off_and_unregistered_by_default():
    c = desk.load_config(REPO / "desk.toml", overrides=False)
    assert c.outlook is False
    assert not outlook.load_settings(REPO / "desk.toml").ready


def test_signing_in_asks_for_the_smallest_permission_and_keeps_no_password(home):
    fake = Fake(signed_in=False)
    p = outlook.connect(settings(), fake, NOW)
    assert p["user_code"] == "ABCD-1234" and outlook.pending()
    assert fake.calls[0][2]["scope"] == "https://graph.microsoft.com/Mail.ReadBasic offline_access"
    assert "Mail.Read " not in fake.calls[0][2]["scope"] + " "
    with pytest.raises(outlook.OutlookError, match="not signed in yet"):
        outlook.finish(settings(), fake, NOW)
    fake.signed_in = True
    outlook.finish(settings(), fake, NOW)
    assert outlook.connected() and not outlook.pending()
    kept = json.loads((home / "runs" / "desk" / "outlook-token.json").read_text())
    assert set(kept) == {"access_token", "refresh_token", "expires_at", "since"}


def test_only_the_people_waited_on_are_asked_about(home):
    c = on()
    waited = applicant(c, "Sam Lee")
    applicant(c, "Kim Ode", contacted=False)  # a yes not written to yet: not asked
    fake = Fake({"sam@x.example": [NOW.strftime("%Y-%m-%dT%H:%M:%SZ")]})
    signed_in(fake)
    n, asked = outlook.sync(c, settings(), fake, NOW)
    assert (n, asked) == (1, 1)
    graph = [u for m, u, f, h in fake.calls if "graph.microsoft.com/v1.0" in u]
    assert len(graph) == 1 and "/me/messages?" in graph[0]
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(graph[0]).query)
    # Two fields asked for, never the body; one mail at most.
    assert q["$select"] == ["receivedDateTime,webLink"] and q["$top"] == ["1"]
    assert "from/emailAddress/address eq 'sam@x.example'" in q["$filter"][0]
    r = outlook.reply("fa", waited)
    assert set(r) == {"at", "link"}
    # A hint is not a step: the application is still "Contacted" until a person ticks.
    assert stages.derive(desk._pipelines()["fa"].standing(waited), c).id == "contacted"
    assert not any(e.kind == "replied" for e in desk._pipelines()["fa"].events)


def test_a_mail_from_before_the_message_is_not_a_reply(home):
    c = on()
    cid = applicant(c, "Sam Lee")
    fake = Fake({"sam@x.example": [(NOW - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")]})
    signed_in(fake)
    assert outlook.sync(c, settings(), fake, NOW) == (0, 1)
    assert outlook.reply("fa", cid) is None


def test_a_hint_no_longer_waited_on_is_dropped_and_disconnect_forgets_all(home):
    c = on()
    cid = applicant(c, "Sam Lee")
    fake = Fake({"sam@x.example": [NOW.strftime("%Y-%m-%dT%H:%M:%SZ")]})
    signed_in(fake)
    outlook.sync(c, settings(), fake, NOW)
    assert outlook.reply("fa", cid)
    stages.advance_now("fa", cid, "replied", "Ana", c)
    outlook.sync(c, settings(), fake, NOW)
    assert outlook.reply("fa", cid) is None
    outlook.disconnect()
    assert not outlook.connected() and not list((home / "runs" / "desk").glob("outlook-*"))


def test_nothing_is_asked_while_it_is_off(home):
    c = desk.load_config()
    applicant(c, "Sam Lee")
    fake = Fake()
    signed_in(fake)
    with pytest.raises(outlook.OutlookError, match="off"):
        outlook.sync(c, settings(), fake, NOW)
    assert not [u for _, u, _, _ in fake.calls if "graph.microsoft.com" in u]


def test_an_expired_access_is_renewed_without_signing_in_again(home):
    c = on()
    applicant(c, "Sam Lee")
    fake = Fake()
    signed_in(fake)
    outlook.sync(c, settings(), fake, NOW + timedelta(hours=2))
    grants = [f["grant_type"] for _, u, f, _ in fake.calls if u.endswith("/token")]
    assert grants[-1] == "refresh_token"


def test_a_quote_in_an_address_cannot_change_the_question():
    w = outlook.Waiting("fa", "x", "o'neil@x.example", NOW)
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(outlook._query(w)).query)["$filter"][0]
    assert q.endswith("address eq 'o''neil@x.example'")


def test_the_row_says_they_wrote_back_and_the_tick_stays_a_persons(home):
    from console.desk_web import Desk
    c = on()
    cid = applicant(c, "Sam Lee")
    fake = Fake({"sam@x.example": [NOW.strftime("%Y-%m-%dT%H:%M:%SZ")]})
    signed_in(fake)
    outlook.sync(c, settings(), fake, NOW)
    d = Desk(c, Access())
    s = desk._pipelines()["fa"].standing(cid)
    row = track_web.tracker(d, Application("fa", cid), s, stages.derive(s, c), "Ana",
                            "/?tab=interested", datetime.now(timezone.utc))
    assert "They wrote back on" in row and "Open in Outlook" in row
    assert 'value="replied"' in row  # still the person's tick
    assert "Check Outlook for replies" in outlook_web.bar(d, "Ana", "/?tab=interested")
    page = d.settings("Ana")
    assert 'name="outlook"' in page and "Disconnect and forget" in page
    assert " style=" not in page + row and " onclick=" not in page + row
