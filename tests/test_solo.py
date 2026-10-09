"""One person, hundreds of applications a month: the desk as a sorting table.

Three things are held here. With a single name under [team] voters, a click is
the decision and nothing waits on anybody. Between the first message and the
trial day there are as many interviews as the team runs, each with its own
name, and the trial day is invited to after the last one, never before. And
the answers owed to everyone who got a no can be prepared and recorded
together, for exactly the people ticked. Every name is invented.
"""

from __future__ import annotations

import csv
import html
import http.client
import io
import json
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import desk
import metrics
import stages
import tuesday
from console import batch_web, stages_web, tracking_web
from desk import Access, Application, Config, DeskError, Person, cast
from pipeline import Event, Pipeline

REPO = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
TODAY = NOW.date()
LABELS = {"contact": "Yes, let's talk", "discuss": "Not sure, to think about",
          "later": "Keep in the pool", "pass": "Not for us"}


def solo(**kw) -> Config:
    kw.setdefault("later_needs_date", False)
    return Config(voters=["Ana"], labels=dict(LABELS), **kw)


def team(**kw) -> Config:
    return Config(voters=["Ana", "Ben", "Cy"], labels=dict(LABELS), **kw)


def day(n: int) -> str:
    return (TODAY + timedelta(days=n)).isoformat()


def pipe() -> Pipeline:
    p = Pipeline("fa")
    p.add(Event(candidate_id="x", kind="received", at="2026-09-20T10:00:00+00:00"))
    return p


def decided(meaning: str, c: Config | None = None, **kw) -> Pipeline:
    p = pipe()
    cast(p, "x", "Ana", meaning, c or solo(), now=NOW - timedelta(hours=3), **kw)
    return p


def state(p: Pipeline, c: Config | None = None) -> stages.State:
    return stages.derive(p.standing("x"), c or solo(), NOW)


def step(p: Pipeline, what: str, c: Config | None = None, **kw):
    return stages.advance(p, "x", what, "Ana", c or solo(), now=NOW, **kw)


# --------------------------------------------------------------------------
# One person deciding
# --------------------------------------------------------------------------

def test_one_persons_click_is_the_decision():
    assert state(decided("contact")).id == "to_contact"
    assert state(decided("pass")).id == "to_answer_no"
    st = state(decided("later", until=day(30)))
    assert (st.id, st.until) == ("talk_later", day(30))
    # The same click from one of three voters settles nothing yet.
    assert state(decided("contact", team()), team()).id == "votes_in"


def test_not_sure_is_something_to_think_about_not_a_team_matter():
    st = state(decided("discuss"))
    assert st.id == "team_to_decide" and stages.name(st.id, solo()) == "To think about"
    assert stages.next_line(st, decided("discuss").standing("x"), solo()).startswith("look again")
    # A team keeps its own words, and a name the person chose wins over both.
    assert stages.name("team_to_decide", team()) == "Team to decide"
    assert stages.name("team_to_decide", solo(state_names={"team_to_decide": "Maybe"})) == "Maybe"


# --------------------------------------------------------------------------
# The pool: liked, not for now, with or without a date
# --------------------------------------------------------------------------

def test_someone_can_be_kept_in_the_pool_with_no_date():
    p = decided("later")
    st = state(p)
    assert (st.id, st.until) == ("talk_later", "")
    # Nothing is put on the board to wake: there is no day to wake it on.
    assert not [e for e in p.events if e.kind == "revisit"]
    assert stages.next_line(st, p.standing("x"), solo()).startswith("no date")
    # It does not come back by itself, however long it stays.
    later = stages.derive(p.standing("x"), solo(), NOW + timedelta(days=400))
    assert later.id == "talk_later"
    # From the pool: taken up again first (ready to contact), or written to straight away.
    assert stages.allowed(st, solo())[:2] == ["takeup", "contacted"]


def test_a_date_on_the_pool_still_brings_the_person_back():
    p = decided("later", until=day(30))
    assert state(p).until == day(30)
    back = stages.derive(p.standing("x"), solo(), NOW + timedelta(days=31))
    assert back.id == "to_contact" and back.back
    # Keeping them with no date after all: the older date is no longer theirs.
    cast(p, "x", "Ana", "later", solo(), now=NOW)
    still = stages.derive(p.standing("x"), solo(), NOW + timedelta(days=31))
    assert (still.id, still.until) == ("talk_later", "")


def test_a_desk_that_wants_a_date_every_time_still_gets_one():
    strict = solo(later_needs_date=True)
    with pytest.raises(DeskError, match="needs a date"):
        cast(pipe(), "x", "Ana", "later", strict, now=NOW)
    p = decided("contact", strict)
    step(p, "contacted", strict, on=day(-2))
    with pytest.raises(DeskError, match="needs the day to come back"):
        step(p, "talk_later", strict)
    with pytest.raises(DeskError, match="is not a date"):
        cast(pipe(), "x", "Ana", "later", solo(), until="soon", now=NOW)


def test_someone_who_did_not_get_through_an_interview_goes_to_the_pool():
    c, p = solo(), decided("contact")
    step(p, "contacted", on=day(-6))
    step(p, "first_call", on=day(-4))
    written = step(p, "talk_later", reason="strong, not this role")
    assert [(e.kind, e.tag, e.by) for e in written] == [("tagged", "pool", "Ana")]
    st = state(p)
    assert (st.id, st.until, st.by) == ("talk_later", "", "Ana")
    assert stages.said(st, solo(state_names={"talk_later": "In the pool"})) == "In the pool"
    # Taken up again later: written to, and the pool no longer holds them.
    step(p, "contacted", on=day(0))
    assert state(p).id == "contacted"


def test_each_decision_has_its_colour_on_the_row_and_only_ones_own():
    from console.desk_web import CSS, Desk
    for m in ("contact", "discuss", "later", "pass"):
        p = decided(m)
        row = Desk(solo(), Access()).row(Person("x", "Sam Lee"), Application("fa", "x"),
                                         p.standing("x"), "Ana", {}, NOW)
        assert f'<span class="dec d-{m}">{html.escape(LABELS[m])}</span>' in row
        assert f".dec.d-{m}" in CSS and " style=" not in row
    # Not decided yet: no colour. And a colleague's vote never colours a row.
    blank = Desk(solo(), Access()).row(Person("x", "Sam Lee"), Application("fa", "x"),
                                       pipe().standing("x"), "Ana", {}, NOW)
    assert 'class="dec' not in blank
    p = decided("contact", team())
    theirs = Desk(team(), Access()).row(Person("x", "Sam Lee"), Application("fa", "x"),
                                        p.standing("x"), "Ben", {}, NOW)
    assert 'class="dec' not in theirs


def test_nothing_on_the_page_waits_for_anybody_else():
    from console.desk_web import Desk
    p = pipe()
    s, st = p.standing("x"), state(p)
    assert stages.next_line(st, s, solo()) == "your decision"
    assert stages_web.tab_names(solo())["todo"] == "To sort"
    app = Application("fa", "x")
    assert stages_web._go_ahead(Desk(solo(), Access()), app, s, st, "Ana", "/", NOW) == ""
    # With colleagues, going ahead without them is still offered.
    ahead = stages_web._go_ahead(Desk(team(), Access()), app, s, stages.derive(s, team(), NOW),
                                 "Ana", "/", NOW)
    assert "without waiting for the others" in ahead


def test_the_shipped_desk_is_one_persons_desk_with_four_interviews():
    c = desk._from_toml(REPO / "desk.toml")
    assert c.solo and c.interviews == 4
    assert stages.rounds(c) == ("first_call", "interview_2", "interview_3", "interview_4")
    for mail in stages.STEP_MAILS:
        m = desk.draft(Person("x", "Sam Lee", email="sam@x.example"), "Role", mail, c)
        assert "Dear Sam," in m.get_content() and str(m["Subject"])


def test_the_recap_and_the_table_speak_to_one_person():
    assert tuesday.headline(tuesday.Due("Ana", to_vote=[object(), object()]), solo(),
                            "Ana") == "You -- 2 to sort"
    assert tuesday.headline(tuesday.Due("Ana", to_vote=[object()]), team(),
                            "Ana") == "You -- 1 to vote on"


def test_a_no_is_one_click_for_one_person_and_two_where_a_reason_is_asked():
    from console.desk_web import Desk
    app = Application("fa", "x")
    one = Desk(solo(), Access()).vote_form(app, {}, "Ana")
    assert '<button class="pass" name="label" value="pass">Not for us</button>' in one
    assert 'data-for="pass"' not in one
    # The pool takes no date unless the team wants one: one click too.
    assert '<button class="later" name="label" value="later">' in one
    assert 'data-for="later"' not in one
    dated = Desk(solo(later_needs_date=True), Access()).vote_form(app, {}, "Ana")
    assert 'data-open="later"' in dated and 'data-for="later"' in dated
    for c in (team(), solo(pass_reason_required=True, pass_reasons=["not this role"])):
        two = Desk(c, Access()).vote_form(app, {}, "Ana")
        assert 'data-open="pass"' in two and 'data-for="pass"' in two


# --------------------------------------------------------------------------
# The interviews before the trial day
# --------------------------------------------------------------------------

def test_every_interview_comes_before_the_trial_day():
    c, p = solo(), decided("contact")
    step(p, "contacted", on=day(-9))
    # Their OK comes first; an interview straight away is allowed too.
    assert stages.allowed(state(p), c)[:2] == ["replied", "first_call"]
    step(p, "replied", on=day(-9))
    for i, r in enumerate(stages.rounds(c)):
        assert stages.allowed(state(p), c)[0] == r
        step(p, r, on=day(-8 + i))
        st = state(p)
        last = i == c.interviews - 1
        # After an interview: the invitation to the next one, by its name. After
        # the last: the trial day, never before.
        assert stages.meaning_of(st, c) == ("trial_day" if last else stages.ROUNDS[i + 1])
    assert stages.allowed(st, c)[0] == "trial_day"
    assert stages.next_line(st, p.standing("x"), c) == "plan the trial day"
    step(p, "trial_day", on=day(0))
    st = state(p)
    assert st.id == "trial_day" and stages.meaning_of(st, c) == ""
    assert not {"offer", "hired"} & set(stages.allowed(st, c))


def test_the_trial_day_is_not_invited_to_after_the_first_call():
    p = decided("contact")
    step(p, "contacted", on=day(-3))
    step(p, "first_call", on=day(-1))
    assert stages.meaning_of(state(p), solo()) == "interview_2"
    assert stages.next_line(state(p), p.standing("x"), solo()) == "plan the second interview"
    # A team that runs a single interview keeps the path it had.
    one = solo(interviews=1)
    assert stages.meaning_of(state(p, one), one) == "trial_day"
    assert stages.allowed(state(p, one), one)[0] == "trial_day"


def test_an_interview_still_to_come_asks_for_its_own_invitation_and_no_shortcut():
    c, p = solo(), decided("contact")
    step(p, "contacted", on=day(-3))
    step(p, "first_call", on=day(3), hour="9:30")
    st = state(p)
    # Planned: its invitation, with its day and time filled in.
    assert st.planned and stages.meaning_of(st, c) == "first_call" and st.time == "09:30"
    assert stages.when(st)["time"] == "09:30" and stages.when(st)["day"].endswith("October")
    ok = stages.allowed(st, c)
    assert ok[0] == "first_call" and "trial_day" not in ok


def test_a_role_that_needs_fewer_interviews_goes_straight_to_the_trial_day():
    c, p = solo(), decided("contact")
    step(p, "contacted", on=day(-3))
    step(p, "first_call", on=day(-2))
    assert stages.allowed(state(p), c)[:4] == ["interview_2", "interview_3", "interview_4",
                                              "trial_day"]
    # Any later interview may come next: the person who runs hiring knows the role.
    step(p, "interview_3", on=day(-1))
    assert state(p).id == "interview_3"
    # Never one the team does not run, never back.
    with pytest.raises(DeskError, match="next step"):
        step(p, "interview_5", on=day(0))
    with pytest.raises(DeskError, match="next step"):
        step(p, "interview_2", on=day(0))
    step(p, "trial_day", on=day(0))
    assert state(p).id == "trial_day"


def test_the_number_of_interviews_is_a_number_from_one_to_six():
    for bad in (0, 7, -1, "many", None):
        with pytest.raises(DeskError, match="states.interviews"):
            solo(interviews=bad)
    assert solo(interviews="3").interviews == 3
    assert stages.rounds(solo(interviews=6)) == stages.ROUNDS


def test_lowering_the_number_leaves_nobody_stuck_in_an_interview():
    c, p = solo(), decided("contact")
    step(p, "contacted", on=day(-5))
    for i, r in enumerate(("first_call", "interview_2", "interview_3")):
        step(p, r, on=day(-4 + i))
    two = solo(interviews=2)
    st = state(p, two)
    assert st.id == "interview_3" and stages.allowed(st, two)[0] == "trial_day"
    # The interview that took place is still on the journey, with its day.
    marks, _ = stages.journey(p.standing("x"), two, NOW)
    by = {m.id: m for m in marks}
    assert by["interview_3"].status == "now" and "interview_4" not in by


def test_a_meeting_recorded_before_interviews_had_names_is_a_first_call():
    assert stages.step_of(Event(candidate_id="x", kind="met", by="Ana")) == "first_call"
    assert stages.step_of(Event(candidate_id="x", kind="met", by="Ana",
                                detail={"round": "interview_4"})) == "interview_4"


def test_each_interview_is_called_what_the_team_calls_it():
    c = solo(state_names={"interview_2": "Technical interview"})
    p = decided("contact", c)
    step(p, "contacted", c, on=day(-3))
    step(p, "first_call", c, on=day(-2))
    assert stages.action("interview_2", c) == "Record the technical interview"
    assert stages.next_line(state(p, c), p.standing("x"), c) == "plan the technical interview"
    assert stages.next_step(p.standing("x"), c, NOW) == "technical interview to plan"


def test_the_table_has_a_column_per_interview():
    assert tracking_web.columns(solo(interviews=1)) == tracking_web.COLUMNS
    cols = tracking_web.columns(solo())
    assert cols[4] == "replied" and cols[5:9] == stages.ROUNDS[:4] and cols[9] == "trial_day"
    assert metrics.stages_for(solo(interviews=2))[4:8] == ("replied", "first_call",
                                                         "interview_2", "trial_day")


# --------------------------------------------------------------------------
# Answering many at once
# --------------------------------------------------------------------------

@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, "ROOT", tmp_path)
    (tmp_path / "mandates" / "generated").mkdir(parents=True)
    (tmp_path / "mandates" / "generated" / "role_fa.provenance.json").write_text(
        json.dumps({"posting_id": "fa", "title": "Founders' Associate"}), encoding="utf-8")
    # The shipped file, with an invented name: no test acts in a real person's name.
    toml = (REPO / "desk.toml").read_text(encoding="utf-8")
    assert 'voters = ["Recruiter"]' in toml
    (tmp_path / "desk.toml").write_text(toml.replace('voters = ["Recruiter"]', 'voters = ["Ana"]'),
                                        encoding="utf-8")
    return tmp_path


def people(c: Config, *decisions: str) -> list[str]:
    """One invented applicant per decision, decided by Ana. Returns their ids."""
    names = ("Sam Lee", "Kim Ode", "Lou Pak", "Ida Moe", "Noa Rey", "Tom Aye")
    ids = []
    for name, m in zip(names, decisions):
        p = desk.add_now("fa", name, f"{name.split()[0].lower()}@x.example")
        cid = p.applications[0].candidate_id
        if m:
            desk.vote_now("fa", cid, "Ana", m, c)
        ids.append(cid)
    return ids


def where_is(cid: str, c: Config) -> str:
    return stages.derive(desk._pipelines()["fa"].standing(cid), c).id


def test_the_ticked_ones_are_marked_and_the_others_left_alone(home):
    c = desk.load_config()
    a, b, yes = people(c, "pass", "pass", "contact")
    done, refused = stages.answer_many([("fa", a), ("fa", b), ("fa", yes)], "Ana", c)
    assert done == 2 and len(refused) == 1 and yes in refused[0]
    assert [where_is(x, c) for x in (a, b, yes)] == ["answered_no", "answered_no",
                                                     "to_contact"]
    # Each no is on the record as written by a named person, like the single step.
    evs = [e for e in desk._pipelines()["fa"].events if e.candidate_id == a]
    assert [(e.kind, e.by) for e in evs[-2:]] == [("contacted", "Ana"), ("closed", "Ana")]
    # The same batch again writes nothing twice.
    done, refused = stages.answer_many([("fa", a), ("fa", b)], "Ana", c)
    assert done == 0 and len(refused) == 2
    assert sum(e.kind == "closed" for e in desk._pipelines()["fa"].events) == 2


def test_a_tick_from_a_stale_page_does_not_close_someone_now_in_process(home):
    # Ticked while the answer was no; since then the decision changed and they
    # were written to. "Answered no" is a way out of Contacted too, so only the
    # state the page showed keeps the batch from closing them.
    c = desk.load_config()
    (a,) = people(c, "pass")
    desk.vote_now("fa", a, "Ana", "contact", c)
    stages.advance_now("fa", a, "contacted", "Ana", c)
    done, refused = stages.answer_many([("fa", a)], "Ana", c)
    assert done == 0 and "moved while you were looking" in refused[0]
    assert where_is(a, c) == "contacted"


def test_nobody_gets_a_no_the_desk_holds_no_decision_for(home):
    c = desk.load_config()
    a, undecided = people(c, "pass", "")
    done, refused = stages.answer_many([("fa", undecided), ("nowhere", a)], "Ana", c)
    assert done == 0 and len(refused) == 2 and where_is(undecided, c) == "new"
    with pytest.raises(DeskError, match="not Ready to answer no"):
        batch_web.answers_csv([("fa", a), ("fa", undecided)], c)
    with pytest.raises(DeskError, match="more than a batch holds"):
        stages.answer_many([("fa", str(i)) for i in range(stages.BATCH_MAX + 1)], "Ana", c)


def test_the_download_has_one_filled_mail_per_ticked_person(home):
    c = desk.load_config()
    a, b, _ = people(c, "pass", "pass", "pass")
    data, name = batch_web.answers_csv([("fa", a), ("fa", b)], c, NOW)
    assert name == "answers_2026-10-05_2.csv"
    rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
    assert tuple(rows[0]) == batch_web.COLUMNS and len(rows) == 3
    assert [r[:4] for r in rows[1:]] == [
        ["sam@x.example", "Sam", "Lee", "Founders' Associate"],
        ["kim@x.example", "Kim", "Ode", "Founders' Associate"]]
    assert rows[1][5].startswith("Dear Sam,") and "{" not in rows[1][4] + rows[1][5]
    # Downloading records nothing: they are still to be answered.
    assert where_is(a, c) == "to_answer_no"


def test_a_cell_is_never_a_formula():
    assert batch_web._cell("=HYPERLINK(1)") == "'=HYPERLINK(1)"
    assert batch_web._cell("+33 6") == "'+33 6" and batch_web._cell("Sam") == "Sam"


def test_the_ticks_are_applications_and_nothing_else():
    assert batch_web.picked({"pick": "fa/a\nfa/b\nfa/a"}) == [("fa", "a"), ("fa", "b")]
    for bad, why in (({}, "nobody is ticked"), ({"pick": "nonsense"}, "not an application"),
                     ({"pick": "/a"}, "not an application")):
        with pytest.raises(DeskError, match=why):
            batch_web.picked(bad)


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


def call(where: str, method: str, path: str, form: list[tuple[str, str]] | None = None
         ) -> tuple[int, dict[str, str], str]:
    host, port = where.split(":")
    con = http.client.HTTPConnection(host, int(port), timeout=10)
    body = urllib.parse.urlencode(form).encode() if form is not None else None
    con.request(method, path, body=body, headers={
        "Host": where, "Content-Type": "application/x-www-form-urlencoded"})
    r = con.getresponse()
    out = r.status, {k.lower(): v for k, v in r.getheaders()}, r.read().decode("utf-8-sig")
    con.close()
    return out


def test_a_batch_through_the_page(home):
    c = desk.load_config()
    a, b, yes, undecided = people(c, "pass", "pass", "contact", "")
    where, srv = start(c)
    try:
        page = call(where, "GET", "/?tab=no&as=Ana")[2]
        # The tools and one tick box per person to answer no to, and only them.
        assert 'id="batch"' in page and page.count('name="pick"') == 2
        assert f'value="fa/{a}"' in page and f'value="fa/{yes}"' not in page
        assert " style=" not in page and " onclick=" not in page
        # One person on the desk: no menu of names, no tab that waits on others.
        assert "Looking as" not in page and "Waiting for the others" not in page
        assert ">To sort<em>1</em>" in page
        token = page.split('name="t" value="')[1].split('"')[0]
        picks = [("as", "Ana"), ("pick", f"fa/{a}"), ("pick", f"fa/{b}"), ("back", "/?tab=no")]
        # Without the page's own token nothing is read, and nothing is recorded.
        code, _, msg = call(where, "POST", "/batch", picks + [("do", "answered"), ("t", "x")])
        assert code == 403 and where_is(a, c) == "to_answer_no"
        code, head, body = call(where, "POST", "/batch", picks + [("do", "csv"), ("t", token)])
        assert code == 200 and head["content-type"].startswith("text/csv")
        assert "attachment" in head["content-disposition"] and body.count("Dear ") == 2
        assert where_is(a, c) == "to_answer_no"
        code, head, _ = call(where, "POST", "/batch", picks + [("do", "answered"), ("t", token)])
        assert code == 303 and "2%20marked%20as%20answered" in head["location"]
        assert [where_is(x, c) for x in (a, b, yes, undecided)] == [
            "answered_no", "answered_no", "to_contact", "new"]
        # Nobody ticked: said, and nothing recorded.
        code, head, _ = call(where, "POST", "/batch", [("as", "Ana"), ("do", "answered"),
                                                      ("t", token)])
        assert "error=" in head["location"] and "nobody%20is%20ticked" in head["location"]
    finally:
        srv.shutdown()


def test_all_shows_everyone_on_a_role_decided_or_not_the_nos_too(home):
    from console import desk_web
    c = desk.load_config()
    yes, no, pool, think, new = people(c, "contact", "pass", "later", "discuss", "")
    stages.answer_many([("fa", no)], "Ana", c)
    d = desk_web.Desk(c, Access())
    page = d.applications("Ana", "all", datetime.now(timezone.utc), role="fa")
    assert ">All<em>5</em>" in page and page.count('<div class="row') == 5
    for m in ("contact", "pass", "later", "discuss"):
        assert page.count(f'<span class="dec d-{m}">') == 2      # the row, and the key
    assert "Answered no" in page and ">In the pool<" in page
    # The shipped words for the four choices, and the pool asks for no date.
    assert [c.label(m) for m in ("contact", "discuss", "later", "pass")] == [
        "Yes, let's talk", "Not sure, to think about", "Keep in the pool", "Not for us"]
    assert not c.later_needs_date and where_is(pool, c) == "talk_later"
    # A team's last tab is the same: everyone, the closed ones too.
    assert stages_web.tab_names(team())["all"] == "All"


def test_one_person_has_no_catch_up_page_in_the_menu_a_team_does():
    from console.desk_web import Desk
    one = Desk(solo(), Access()).page("Ana", "desk", "")
    assert 'href="/recap' not in one and "Catch-up" not in one and "At the door" in one
    assert 'href="/recap?as=Ana">Catch-up</a>' in Desk(team(), Access()).page("Ana", "desk", "")


def test_the_list_can_be_narrowed_to_a_period_by_the_day_they_applied(home):
    from console import desk_web
    c = desk.load_config()
    recent, old, older = people(c, "", "contact", "pass")
    with desk.locked():
        fa = desk._pipelines()["fa"]
        for ev in fa.events:
            if ev.kind == "received" and ev.candidate_id in (old, older):
                days = 40 if ev.candidate_id == old else 200
                ev.at = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        desk._save(fa)
    d = desk_web.Desk(c, Access())
    now = datetime.now(timezone.utc)
    count = lambda page: page.count('<div class="row')  # noqa: E731
    assert count(d.applications("Ana", "all", now)) == 3
    month = d.applications("Ana", "all", now, since=30)
    assert count(month) == 1 and "Sam Lee" in month and ">All<em>1</em>" in month
    assert '<option value="30" selected>The last 30 days</option>' in month
    assert count(d.applications("Ana", "all", now, since=90)) == 2
    assert count(d.applications("Ana", "all", now, since=365)) == 3
    # The period stays in the address: the tabs, the role filter and a vote keep it.
    assert 'href="/?tab=todo&since=30&as=Ana"' in month
    assert '<input type="hidden" name="since" value="30">' in month
    assert 'name="back" value="/?tab=all&amp;since=30"' in month
    # A period nobody offers is "any time", not an error.
    assert count(d.applications("Ana", "all", now, since=12345)) == 3


def test_a_long_list_is_drawn_a_page_at_a_time(home, monkeypatch):
    from console import desk_web
    monkeypatch.setattr(desk_web, "PAGE", 2)
    c = desk.load_config()
    people(c, "", "", "", "", "")
    d = desk_web.Desk(c, Access())
    now = datetime.now(timezone.utc)
    first = d.applications("Ana", "todo", now)
    assert first.count('<div class="row') == 2 and "1 to 2 of 5" in first
    assert "Next 2" in first and "Previous" not in first and ">To sort<em>5</em>" in first
    last = d.applications("Ana", "todo", now, page=3)
    assert last.count('<div class="row') == 1 and "5 to 5 of 5" in last and "Next" not in last
    # A vote on the second page comes back to the second page.
    assert 'name="back" value="/?tab=todo&amp;page=2"' in d.applications("Ana", "todo", now,
                                                                         page=2)
    # A page that does not exist is the nearest one that does.
    assert "5 to 5 of 5" in d.applications("Ana", "todo", now, page=99)
    assert "1 to 2 of 5" in d.applications("Ana", "todo", now, page=0)
