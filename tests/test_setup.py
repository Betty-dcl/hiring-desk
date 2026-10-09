"""The Set up page: every rule as a question, answers kept per partner.

What matters here is what the page must not do: change a setting nobody
asked to change, show a partner the others' answers before they gave their
own, or record an answer the question does not allow. Each guard was checked
once by hand to fail with its line removed.
"""

from __future__ import annotations

import http.client
import json
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import desk
from console import setup_web as su
from console.desk_web import Desk
from desk import Access, Config, DeskError, Vote, outcome

REPO = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)


def cfg(**kw):
    return Config(voters=["Ana", "Ben", "Cy"],
                  labels={"contact": "Interview", "later": "Keep on file",
                          "discuss": "Discuss", "pass": "Not interested"}, **kw)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, "ROOT", tmp_path)
    # The shipped file, with invented voters: no test acts in a real person's name.
    toml = (REPO / "desk.toml").read_text(encoding="utf-8")
    shipped = 'voters = ["Recruiter"]'
    assert shipped in toml
    (tmp_path / "desk.toml").write_text(toml.replace(shipped, 'voters = ["Ana", "Ben", "Cy"]'),
                                        encoding="utf-8")
    return tmp_path


def form(**answers: str) -> dict[str, str]:
    return dict(answers)


# --------------------------------------------------------------------------
# The one rule built for this page: which date "later" wakes up on
# --------------------------------------------------------------------------

def later(vs: dict[str, str]) -> dict[str, Vote]:
    return {v: Vote(v, "later", "2026-10-01T10:00:00+00:00", until=d) for v, d in vs.items()}


def test_different_dates_wake_up_on_the_earliest_by_default():
    vs = later({"Ana": "2027-03-01", "Ben": "2027-01-15", "Cy": "2027-06-01"})
    assert outcome(vs, cfg()).until == "2027-01-15"


def test_the_team_can_choose_the_latest_date():
    vs = later({"Ana": "2027-03-01", "Ben": "2027-01-15", "Cy": "2027-06-01"})
    assert outcome(vs, cfg(later_date="latest")).until == "2027-06-01"


def test_a_date_rule_that_does_not_exist_is_refused():
    with pytest.raises(DeskError, match="later_date"):
        cfg(later_date="median")


def test_desk_toml_carries_the_date_rule():
    assert desk.load_config(REPO / "desk.toml", overrides=False).later_date == "earliest"


# --------------------------------------------------------------------------
# The questions say what the code does
# --------------------------------------------------------------------------

def test_every_default_and_current_value_is_one_of_the_options(home):
    for q in su.questions(cfg()):
        assert q.theme in su.THEMES, q.id
        if q.kind == "choice":
            values = {o.value for o in q.options}
            assert q.default in values and q.current in values, q.id


def test_options_offered_as_built_are_the_ones_the_code_accepts(home):
    qs = {q.id: q for q in su.questions(cfg())}
    built = lambda qid: {o.value for o in qs[qid].options if o.built}  # noqa: E731
    assert built("blind") == set(desk.BLIND)
    assert built("agreement") == set(desk.AGREEMENT)
    assert built("later_date") == set(desk.LATER_DATE)
    assert built("recap_weekday") == set(desk.WEEKDAYS)
    assert "auto" not in built("mail_mode")


def test_only_rules_settings_can_change_may_be_applied(home):
    for q in su.questions(cfg()):
        if q.apply:
            assert q.apply.split(".")[0] in desk.EDITABLE, q.id


def test_the_page_never_calls_the_desk_compliant(home):
    d = Desk(cfg(), Access())
    texts = [su.page(d, "Ana"), su.answers(d, "Ana"),
             su.markdown(d, "Ana", NOW)[0].decode()]
    for t in texts:
        assert "compliant" not in t.lower()
    assert "not legal advice" in texts[2]


def test_good_to_know_names_the_rules_it_rests_on(home):
    # No longer on the questions page: in the export a team can keep.
    page = su.markdown(Desk(cfg(), Access()), "Ana", NOW)[0].decode()
    for said in ("Annex III", "Article 50", "Article 22", "consent", "retention period",
                 "The partners' vote decides"):
        assert said in page, said  # Markdown: no HTML escaping


# --------------------------------------------------------------------------
# What a form may record
# --------------------------------------------------------------------------

@pytest.mark.parametrize("field,value,why", [
    ("q_blind", "sometimes", "not one of the options"),
    ("q_answer_within_days", "0", "between"),
    ("q_answer_within_days", "soon", "not a number"),
    ("q_sender", "x" * 300, "characters at most"),
    ("note_blind", "x" * 400, "characters at most"),
])
def test_an_answer_the_question_does_not_allow_is_refused(home, field, value, why):
    with pytest.raises(DeskError, match=why):
        su.save({field: value}, "Ana", cfg())
    assert not (home / "runs" / "desk" / "setup.json").exists()


# --------------------------------------------------------------------------
# Untouched is not answered
# --------------------------------------------------------------------------

def test_a_question_left_alone_is_not_answered(home):
    msg = su.save(form(q_blind="off", q_sender="", q_answer_within_days=" "), "Ana", cfg())
    assert set(su.load()["answers"]["Ana"]) == {"blind"}
    n = len(su.questions(cfg()))
    assert f"1 answer saved under your name; {n - 1} not answered yet" == msg


def test_an_empty_name_is_not_applied(home):
    su.save(form(q_label_contact="", apply_label_contact="1"), "Ana", desk.load_config())
    assert desk.load_config().labels["contact"] == "Yes, let's talk"
    assert "label_contact" not in su.load()["answers"]["Ana"]


def test_nothing_is_ticked_for_a_partner_who_has_not_answered(home):
    d = Desk(cfg(), Access())
    page = su.page(d, "Ana")
    assert " checked" not in page.split('name="read_')[0]
    assert 'class="tag def">default</span>' in page and "not answered" in page
    su.save(form(q_blind="off"), "Ana", cfg())
    page = su.page(d, "Ana")
    # Her own answer comes back ticked; nothing else does.
    assert page.count('type="radio"') > 0
    assert page.split('name="read_')[0].count(" checked") == 1
    assert 'name="q_blind" value="off" checked' in page


def test_accept_all_defaults_fills_only_what_was_left_alone_in_that_section(home):
    theme = "When it is settled"
    su.save(form(q_agreement="majority", q_blind="off", accept=theme), "Ana", cfg())
    mine = su.load()["answers"]["Ana"]
    assert mine["agreement"]["answer"] == "majority" and not mine["agreement"].get("default")
    assert mine["later_date"] == {**mine["later_date"], "answer": "earliest", "default": True}
    assert mine["blind"]["answer"] == "off"
    # Another section is left as it was: not answered.
    assert "recap_weekday" not in mine and "vote_moves" not in mine
    text = su.answers(Desk(cfg(), Access()), "Ana")
    assert "the default, accepted for the section" in text


def test_every_section_has_its_accept_button(home):
    page = su.page(Desk(cfg(), Access()), "Ana")
    # Enter in a box presses the first button: it must not be an accept.
    assert page.split('action="/setup"')[1].split("<button", 2)[1].startswith(" hidden")
    for theme in su.THEMES:
        assert f'name="accept" value="{theme}"' in page


def test_the_states_have_their_questions(home):
    ids = {q.id for q in su.questions(cfg())}
    assert {"vote_moves", "state_names", "who_advances", "plan_ahead_days"} <= ids
    q = next(q for q in su.questions(cfg()) if q.id == "who_advances")
    assert q.default == "any" and q.current == "any"
    q = next(q for q in su.questions(cfg(advancers=["Cy"])) if q.id == "who_advances")
    assert q.current == "named"


def test_answers_are_kept_per_partner_with_the_date(home):
    su.save(form(q_blind="off", note_blind="we trust each other"), "Ana", cfg(), now=NOW)
    su.save(form(q_blind="until_you_vote"), "Ben", cfg(), now=NOW)
    rec = su.load()
    assert rec["answers"]["Ana"]["blind"] == {"answer": "off", "keep": False,
                                              "note": "we trust each other",
                                              "at": NOW.isoformat()}
    assert rec["answers"]["Ben"]["blind"]["answer"] == "until_you_vote"
    assert [h["by"] for h in rec["history"]] == ["Ana", "Ben"]


def test_an_answer_saved_again_unchanged_keeps_its_first_date(home):
    later_on = datetime(2026, 10, 3, tzinfo=timezone.utc)
    su.save(form(q_blind="off"), "Ana", cfg(), now=NOW)
    su.save(form(q_blind="off", q_agreement="majority"), "Ana", cfg(), now=later_on)
    mine = su.load()["answers"]["Ana"]
    assert mine["blind"]["at"] == NOW.isoformat()
    assert mine["agreement"]["at"] == later_on.isoformat()


def test_keep_as_is_records_what_runs_today(home):
    su.save(form(q_agreement="majority", keep_agreement="1"), "Ana", cfg(), now=NOW)
    a = su.load()["answers"]["Ana"]["agreement"]
    assert a["keep"] and a["answer"] == "unanimous"


def test_someone_who_does_not_vote_cannot_answer(home):
    with pytest.raises(DeskError, match="does not vote"):
        su.save(form(q_blind="off"), "Mallory", cfg())


def test_the_record_is_written_under_the_desks_lock(home, monkeypatch):
    held = {"now": False, "wrote_inside": None}
    real_write = Path.write_text

    class Spy:
        def __enter__(self):
            held["now"] = True

        def __exit__(self, *_):
            held["now"] = False

    def write(self, *a, **k):
        if self.name == "setup.json":
            held["wrote_inside"] = held["now"]
        return real_write(self, *a, **k)

    monkeypatch.setattr(desk, "locked", Spy)
    monkeypatch.setattr(Path, "write_text", write)
    su.save(form(q_blind="off"), "Ana", cfg())
    assert held["wrote_inside"] is True


# --------------------------------------------------------------------------
# Apply now: only what Settings already changes, only when ticked
# --------------------------------------------------------------------------

def test_an_answer_alone_changes_nothing_on_the_desk(home):
    su.save(form(q_agreement="majority", q_blind="off"), "Ana", cfg())
    assert desk.load_config().agreement == "unanimous"
    assert not (home / "runs" / "desk" / "settings.json").exists()


def test_apply_now_changes_the_setting_under_the_partners_name(home):
    su.save(form(q_agreement="majority", apply_agreement="1"), "Ana", cfg())
    assert desk.load_config().agreement == "majority"
    rec = json.loads((home / "runs" / "desk" / "settings.json").read_text(encoding="utf-8"))
    assert rec["history"][-1]["by"] == "Ana" and rec["history"][-1]["changed"] == ["agreement"]
    assert su.load()["answers"]["Ana"]["agreement"]["applied"]


def test_apply_now_does_nothing_for_a_rule_settings_does_not_edit(home):
    su.save(form(q_later_date="latest", apply_later_date="1"), "Ana", cfg())
    assert not (home / "runs" / "desk" / "settings.json").exists()


def test_keep_as_is_wins_over_apply_now(home):
    su.save(form(q_agreement="majority", keep_agreement="1", apply_agreement="1"), "Ana", cfg())
    assert desk.load_config().agreement == "unanimous"


def test_applying_one_name_keeps_a_name_changed_before(home):
    desk.save_settings({"labels": {"later": "Later"}}, by="Ben")
    c = desk.load_config()
    su.save(form(q_label_contact="Meet", apply_label_contact="1"), "Ana", c)
    labels = desk.load_config().labels
    assert labels["contact"] == "Meet" and labels["later"] == "Later"


def test_a_setting_that_cannot_run_records_nothing(home):
    # Two meanings with one name: Settings refuses it, so the answer is not kept either.
    with pytest.raises(DeskError, match="share a name"):
        su.save(form(q_label_contact="Not sure, to think about", apply_label_contact="1"), "Ana", desk.load_config())
    assert not (home / "runs" / "desk" / "setup.json").exists()


# --------------------------------------------------------------------------
# Side by side, under the blind rule
# --------------------------------------------------------------------------

def test_the_others_answers_are_hidden_until_you_answer(home):
    d = Desk(cfg(), Access())
    su.save(form(q_agreement="majority", note_agreement="zebra-note"), "Ben", cfg())
    before = su.answers(d, "Ana")
    md = su.markdown(d, "Ana", NOW)[0].decode()
    assert "zebra-note" not in before and "zebra-note" not in md
    assert "answered — hidden until you answer" in before
    assert "- Ben: answered (hidden until you answer)" in md
    assert "state differ" not in before and "To discuss" not in before
    su.save(form(q_agreement="unanimous"), "Ana", cfg())
    after = su.answers(d, "Ana")
    assert "A majority agrees" in after and "zebra-note" in after


def test_with_the_blind_rule_off_answers_show_straight_away(home):
    d = Desk(cfg(blind="off"), Access())
    su.save(form(q_agreement="majority"), "Ben", cfg())
    assert "A majority agrees" in su.answers(d, "Ana")


def test_where_the_answers_differ_is_named_first(home):
    c = cfg()
    su.save(form(q_agreement="majority", q_blind="until_you_vote"), "Ana", c)
    su.save(form(q_agreement="unanimous", q_blind="until_you_vote"), "Ben", c)
    su.save(form(q_agreement="unanimous", q_blind="until_you_vote"), "Cy", c)
    qs = {q.id: q for q in su.questions(c)}
    rec = su.load()
    assert su.compare(qs["agreement"], rec, c) == "differ"
    assert su.compare(qs["blind"], rec, c) == "agree"
    assert su.compare(qs["recap_weekday"], rec, c) == "open"
    page = su.answers(Desk(c, Access()), "Ana")
    to_discuss = page.split("<h2>To discuss</h2>")[1].split("</ul>")[0]
    assert 'href="#a-agreement"' in to_discuss and "#a-blind" not in to_discuss


def test_all_agreeing_on_something_new_is_a_change(home):
    c = cfg()
    for v in c.voters:
        su.save(form(q_agreement="majority"), v, c)
    q = next(q for q in su.questions(c) if q.id == "agreement")
    assert su.compare(q, su.load(), c) == "change"


def test_the_markdown_carries_every_question_and_the_answers(home):
    c = cfg(blind="off")
    su.save(form(q_agreement="majority", note_agreement="faster"), "Ben", c)
    data, name = su.markdown(Desk(c, Access()), "Ana", NOW)
    text = data.decode()
    assert name == "hiring-desk-setup-2026-10-01.md"
    for q in su.questions(c):
        assert f"### {q.title}" in text
    assert '- Ben: A majority agrees (2 of 3) -- "faster"' in text
    assert "- Ana: not answered" in text
    assert "\r" not in text


# --------------------------------------------------------------------------
# Through the server
# --------------------------------------------------------------------------

def start(c: Config) -> tuple[str, ThreadingHTTPServer]:
    from console import desk_web
    started, real_init = {}, ThreadingHTTPServer.__init__

    def capture(self, addr, handler):
        real_init(self, ("127.0.0.1", 0), handler)
        started["port"] = self.server_address[1]
        started["srv"] = self

    ThreadingHTTPServer.__init__ = capture
    try:
        threading.Thread(target=desk_web.serve, args=("127.0.0.1", 0, c, Access()),
                         daemon=True).start()
        while "port" not in started:
            time.sleep(0.01)
    finally:
        ThreadingHTTPServer.__init__ = real_init
    return f"127.0.0.1:{started['port']}", started["srv"]


def raw(where: str, method: str, path: str, body: bytes = b"") -> tuple[int, dict, str]:
    host, port = where.split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=10)
    h = {"Host": where}
    if body:
        h["Content-Type"] = "application/x-www-form-urlencoded"
    conn.request(method, path, body or None, h)
    r = conn.getresponse()
    out = r.status, dict(r.getheaders()), r.read().decode("utf-8", "replace")
    conn.close()
    return out


def token(where: str, viewer: str) -> str:
    page = raw(where, "GET", f"/setup?as={viewer}")[2]
    return page.split('name="t" value="')[1].split('"')[0]


def test_saving_through_the_page_records_and_shows_the_answers(home):
    where, srv = start(cfg())
    try:
        body = urllib.parse.urlencode({"t": token(where, "Ana"), "as": "Ana",
                                       "back": "/setup", "q_agreement": "majority",
                                       "read_ai_act": "1"}).encode()
        code, h, _ = raw(where, "POST", "/setup", body)
        assert code == 303 and h["Location"].startswith("/setup/answers")
        rec = su.load()
        assert rec["answers"]["Ana"]["agreement"]["answer"] == "majority"
        assert "ai_act" in rec["read"]["Ana"]
    finally:
        srv.shutdown()


def test_one_partners_page_cannot_answer_for_another(home):
    where, srv = start(cfg())
    try:
        body = urllib.parse.urlencode({"t": token(where, "Ben"), "as": "Ana",
                                       "q_agreement": "majority"}).encode()
        code, _, msg = raw(where, "POST", "/setup", body)
        assert code == 403 and "stale or foreign" in msg
        assert not (home / "runs" / "desk" / "setup.json").exists()
    finally:
        srv.shutdown()


def test_the_markdown_is_a_download_not_a_page(home):
    where, srv = start(cfg())
    try:
        code, h, text = raw(where, "GET", "/setup.md?as=Ana")
        assert code == 200 and h["Content-Type"].startswith("text/markdown")
        assert h["Content-Disposition"].startswith("attachment;")
        assert "sandbox" in h["Content-Security-Policy"]
        assert text.startswith("# Hiring desk: set up")
    finally:
        srv.shutdown()


def test_go_ahead_alone_is_asked_and_can_be_applied(home):
    q = next(q for q in su.questions(desk.load_config()) if q.id == "go_ahead_alone")
    assert q.default == "after_one_yes" and q.apply == "go_ahead_alone"
    su.save(form(q_go_ahead_alone="all_voted", apply_go_ahead_alone="1"), "Ana",
            desk.load_config())
    assert desk.load_config().go_ahead_alone == "all_voted"
    # A process owner nobody has named cannot run: refused, and not recorded.
    with pytest.raises(DeskError, match="needs states.advancers"):
        su.save(form(q_go_ahead_alone="owner", apply_go_ahead_alone="1"), "Ben",
                desk.load_config())
    assert "Ben" not in su.load()["answers"]
