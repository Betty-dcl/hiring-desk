"""Where an application stands: one state, apart from the vote.

What matters here is what the desk must refuse: a step out of order, a date
that cannot be true, a step on an application that has ended, a step taken on
a page a colleague has already moved, a step by someone the team did not
name -- and a vote that would move anything but a to-do. Each guard was
checked once by hand to fail with its line removed. Every name is invented.
"""

from __future__ import annotations

import http.client
import json
import shutil
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
from desk import Access, Application, Config, DeskError, Person, cast
from pipeline import Event, Pipeline

REPO = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
TODAY = NOW.date()


def cfg(**kw) -> Config:
    return Config(voters=["Ana", "Ben", "Cy"],
                  labels={"contact": "Yes, let's talk",
                          "discuss": "Not sure, let's discuss as a team",
                          "later": "Not yet", "pass": "Not for us"}, **kw)


def day(n: int) -> str:
    return (TODAY + timedelta(days=n)).isoformat()


def pipe(received: str = "2026-09-20T10:00:00+00:00") -> Pipeline:
    p = Pipeline("fa")
    p.add(Event(candidate_id="x", kind="received", at=received))
    return p


def agreed(meaning: str = "contact", c: Config | None = None, **kw) -> Pipeline:
    p, c = pipe(), c or cfg()
    for v in c.voters:
        cast(p, "x", v, meaning, c, now=NOW - timedelta(hours=3), **kw)
    return p


def state(p: Pipeline, c: Config | None = None, now: datetime = NOW) -> stages.State:
    return stages.derive(p.standing("x"), c or cfg(), now)


def step(p: Pipeline, what: str, by: str = "Ana", c: Config | None = None, **kw):
    return stages.advance(p, "x", what, by, c or cfg(), now=NOW, **kw)


# --------------------------------------------------------------------------
# The votes: they only ever reach a to-do
# --------------------------------------------------------------------------

def test_a_new_application_is_new_then_votes_in():
    p, c = pipe(), cfg()
    assert state(p).id == "new"
    cast(p, "x", "Ana", "contact", c, now=NOW)
    assert state(p).id == "votes_in"


def test_agreeing_on_yes_is_a_to_do_and_writes_nothing_else():
    p = agreed("contact")
    assert state(p).id == "to_contact"
    assert not [e for e in p.events if e.kind in ("contacted", "met", "closed")]


def test_agreeing_on_no_is_to_answer_no_not_an_end():
    p = agreed("pass")
    assert state(p).id == "to_answer_no" and p.standing("x").state != "closed"


def test_agreeing_on_later_parks_until_the_date_then_comes_back():
    p = agreed("later", until=day(30))
    st = state(p)
    assert (st.id, st.until) == ("talk_later", day(30))
    back = state(p, now=NOW + timedelta(days=31))
    assert back.id == "to_contact" and back.back


def test_a_disagreement_is_for_the_team_to_decide():
    p, c = pipe(), cfg()
    for v, m in zip(c.voters, ("contact", "pass", "contact")):
        cast(p, "x", v, m, c, now=NOW)
    st = state(p)
    assert st.id == "team_to_decide" and stages.name(st.id, cfg()) == "Team to decide"


def test_the_old_names_are_read_as_the_same_meanings():
    p, c = pipe(), cfg()
    cast(p, "x", "Ana", "Keep on file", c, until=day(10), now=NOW)
    cast(p, "x", "Ben", "Interview", c, now=NOW)
    cast(p, "x", "Cy", "Not interested", c, now=NOW)
    said = {e.by: e.detail["label"] for e in p.events if e.kind == "voted"}
    assert said == {"Ana": "later", "Ben": "contact", "Cy": "pass"}


def test_an_old_name_never_overrides_a_current_one():
    # A team that calls "later" Interview means "later" by it, not the old "contact".
    c = Config(voters=["Ana"], labels={"contact": "Yes", "discuss": "Talk",
                                       "later": "Interview", "pass": "No"})
    assert c.meaning("Interview") == "later" and c.meaning("Not interested") == "pass"


# --------------------------------------------------------------------------
# The way through
# --------------------------------------------------------------------------

def test_each_step_moves_the_state_with_its_date_and_who():
    p = agreed()
    step(p, "contacted", on=day(-1))
    st = state(p)
    assert (st.id, st.on, st.by) == ("contacted", day(-1), "Ana")
    step(p, "first_call", "Ben", on=day(0))
    step(p, "trial_day", "Cy", on=day(0))
    st = state(p)
    assert (st.id, st.by) == ("trial_day", "Cy") and not st.ended
    # The desk stops at the trial day: an offer is another process, not a step here.
    assert "offer" not in stages.allowed(st, cfg()) and "hired" not in stages.allowed(st, cfg())
    with pytest.raises(DeskError, match="not Offer"):
        step(p, "offer", on=day(0))


def test_the_card_shows_each_state_with_its_day_and_name():
    p = agreed()
    step(p, "contacted", on=day(-2))
    marks, st = stages.journey(p.standing("x"), cfg(), NOW)
    by = {m.id: m for m in marks}
    assert by["contacted"].status == "now" and by["contacted"].by == "Ana"
    assert by["contacted"].on == day(-2)
    assert by["to_contact"].status == "done" and by["first_call"].status == "ahead"
    assert stages.said(st, cfg()).startswith("Contacted ") and st.by == "Ana"


def test_a_planned_trial_day_is_planned_until_it_happens():
    p = agreed()
    step(p, "contacted")
    step(p, "first_call")
    step(p, "trial_day", on=day(5))
    st = state(p)
    assert st.id == "trial_day" and st.planned
    assert stages.allowed(st, cfg())[0] == "trial_day"


# --------------------------------------------------------------------------
# What is refused
# --------------------------------------------------------------------------

def test_a_step_out_of_order_is_refused():
    p = agreed()
    with pytest.raises(DeskError, match="next step is Contacted"):
        step(p, "first_call")


def test_no_step_while_the_votes_are_still_coming():
    p, c = pipe(), cfg()
    cast(p, "x", "Ana", "contact", c, now=NOW)
    with pytest.raises(DeskError, match="next step"):
        step(p, "contacted")


def test_nobody_answers_no_alone_before_the_votes():
    with pytest.raises(DeskError, match="next step"):
        step(pipe(), "answered_no")


def test_an_offer_before_the_trial_day_happened_is_refused():
    p = agreed()
    step(p, "contacted")
    step(p, "first_call")
    step(p, "trial_day", on=day(3))
    with pytest.raises(DeskError, match="not Offer"):
        step(p, "offer")


def test_something_that_happened_cannot_be_dated_in_the_future():
    p = agreed()
    with pytest.raises(DeskError, match="in the future"):
        step(p, "contacted", on=day(5))
    step(p, "contacted", on=day(1))  # tomorrow: a time zone, not a typo


def test_a_meeting_too_far_ahead_is_refused():
    p = agreed()
    step(p, "contacted")
    with pytest.raises(DeskError, match="days ahead"):
        step(p, "first_call", on=day(400))
    c = cfg(plan_ahead_days=10)
    with pytest.raises(DeskError, match="10 days ahead"):
        step(p, "first_call", c=c, on=day(11))


def test_a_date_before_they_applied_is_refused():
    p = agreed()
    with pytest.raises(DeskError, match="before they applied"):
        step(p, "contacted", on="2026-09-01")


def test_a_step_dated_before_the_one_it_follows_is_refused():
    p = agreed()
    step(p, "contacted", on=day(-2))
    with pytest.raises(DeskError, match="before Contacted"):
        step(p, "first_call", on=day(-5))


def test_a_date_that_is_not_one_is_refused():
    with pytest.raises(DeskError, match="not a date"):
        step(agreed(), "contacted", on="yesterday")


def test_talk_later_needs_a_day_after_today_and_not_years_away():
    p = agreed()
    with pytest.raises(DeskError, match="needs the day"):
        step(p, "talk_later")
    with pytest.raises(DeskError, match="after today"):
        step(p, "talk_later", on=day(0))
    with pytest.raises(DeskError, match="years away"):
        step(p, "talk_later", on=day(800))
    step(p, "talk_later", on=day(60), reason="after their exams")
    st = state(p)
    assert (st.id, st.until) == ("talk_later", day(60))


def test_an_ended_application_takes_no_step_until_reopened():
    p = agreed()
    step(p, "withdrew")
    with pytest.raises(DeskError, match="reopen it first"):
        step(p, "contacted")
    step(p, "reopen", reason="they wrote back")
    assert state(p).id == "to_contact"


def test_a_step_on_a_page_a_colleague_already_moved_is_refused():
    p = agreed()
    step(p, "contacted", "Ben")
    n = len(p.events)
    with pytest.raises(DeskError, match="moved while you were looking"):
        step(p, "contacted", expect="to_contact")
    assert len(p.events) == n


def test_only_the_named_people_move_steps_when_the_team_names_some():
    c = cfg(advancers=["Cy"])
    p = agreed(c=c)
    with pytest.raises(DeskError, match="does not move steps"):
        step(p, "contacted", "Ana", c=c)
    step(p, "contacted", "Cy", c=c)


def test_someone_who_does_not_vote_moves_nothing():
    with pytest.raises(DeskError, match="does not vote"):
        step(agreed(), "contacted", "Mallory")


def test_a_reason_on_a_protected_subject_is_not_recorded():
    p = agreed()
    step(p, "contacted")
    with pytest.raises(DeskError, match="protected"):
        step(p, "withdrew", reason="she is pregnant")


def test_the_states_settings_are_checked():
    with pytest.raises(DeskError, match="must be voters"):
        cfg(advancers=["Mallory"])
    with pytest.raises(DeskError, match="two states share a name"):
        cfg(state_names={"contacted": "Hired"})
    with pytest.raises(DeskError, match="no state called"):
        cfg(state_names={"interviewed": "Interviewed"})


# --------------------------------------------------------------------------
# Old logs, and the ways out
# --------------------------------------------------------------------------

def test_an_old_close_after_the_no_mail_reads_as_answered_no():
    p = agreed("pass")
    desk.mark(p, "x", "sent", "Ana", cfg())
    assert state(p).id == "answered_no"


def test_a_close_without_any_record_of_how_is_just_closed():
    p = pipe()
    p.add(Event(candidate_id="x", kind="closed", by="Ana", reason="duplicate", at=NOW.isoformat()))
    assert state(p).id == "closed"


def test_the_later_mail_does_not_end_talk_later():
    p = agreed("later", until=day(30))
    desk.mark(p, "x", "sent", "Ana", cfg())
    assert state(p).id == "talk_later"


def test_answering_no_is_a_message_and_an_end():
    p = agreed("pass")
    step(p, "answered_no", on=day(-1))
    kinds = [(e.kind, e.detail.get("about"), e.detail.get("exit")) for e in p.events[-2:]]
    assert kinds == [("contacted", "pass", None), ("closed", None, "answered_no")]


# --------------------------------------------------------------------------
# Tracking reads the same states
# --------------------------------------------------------------------------

def test_a_planned_trial_day_is_not_counted_in_the_funnel_until_its_day():
    p = agreed()
    step(p, "contacted")
    step(p, "first_call")
    step(p, "trial_day", on=day(5))
    c = metrics.build_case("fa", "x", "X", ["x"], p.events, cfg(), metrics.Tracking(), "",
                           now=NOW)
    row = metrics.funnel([c], cfg())[0]
    assert row.counts["first_call"] == 1 and row.counts["trial_day"] == 0
    c = metrics.build_case("fa", "x", "X", ["x"], p.events, cfg(), metrics.Tracking(), "",
                           now=NOW + timedelta(days=6))
    assert metrics.funnel([c], cfg())[0].counts["trial_day"] == 1


def test_a_candidate_who_withdrew_is_not_left_unanswered():
    p = pipe()
    step(p, "withdrew")
    c = metrics.build_case("fa", "x", "X", ["x"], p.events, cfg(), metrics.Tracking(), "",
                           now=NOW)
    pr = metrics.promise([c], metrics.Tracking(), NOW)
    assert not c.closed_silently and pr.withdrew == [c] and not pr.waiting


def test_contacted_yesterday_counts_from_yesterday_never_earlier():
    p = agreed()
    step(p, "contacted", on=day(-1))
    c = metrics.build_case("fa", "x", "X", ["x"], p.events, cfg(), metrics.Tracking(), "",
                           now=NOW)
    assert c.answered == datetime.combine(TODAY - timedelta(days=1),
                                          datetime.max.time().replace(microsecond=0),
                                          tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# On the page, and through the server
# --------------------------------------------------------------------------

def test_the_row_says_the_state_in_words():
    from console.desk_web import Desk
    p = agreed()
    step(p, "contacted", on=day(-1))
    html = Desk(cfg(), Access()).row(Person("x", "Sam Lee"), Application("fa", "x"),
                                     p.standing("x"), "Ana", {}, NOW)
    assert 'class="standing">Contacted ' in html and "by Ana" in html


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, "ROOT", tmp_path)
    (tmp_path / "mandates" / "generated").mkdir(parents=True)
    (tmp_path / "mandates" / "generated" / "role_fa.provenance.json").write_text(
        json.dumps({"posting_id": "fa", "title": "Founders' Associate"}), encoding="utf-8")
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


def test_a_step_through_the_page_is_recorded_under_the_viewers_own_token(home):
    c = desk.load_config()
    p = desk.add_now("fa", "Sam Lee", "sam@x.example")
    cid = p.applications[0].candidate_id
    for v in c.voters:
        desk.vote_now("fa", cid, v, "contact", c)
    where, srv = start(c)
    try:
        page = call(where, "GET", f"/person/{p.person_id}?as=Ana")[2]
        assert 'action="/step"' in page and 'name="from" value="to_contact"' in page
        token = lambda v: call(where, "GET", f"/?as={v}")[2].split(  # noqa: E731
            'name="t" value="')[1].split('"')[0]
        # add_now stamps the application with the real clock, so the step is dated today.
        form = {"as": "Ana", "posting": "fa", "candidate": cid, "from": "to_contact",
                "step": "contacted", "on": datetime.now(timezone.utc).date().isoformat()}
        # Ben's token, sent as Ana: refused before anything is read.
        code, _, msg = call(where, "POST", "/step", {**form, "t": token("Ben")})
        assert code == 403 and "stale or foreign" in msg
        assert stages.derive(desk._pipelines()["fa"].standing(cid), c).id == "to_contact"
        code, loc, _ = call(where, "POST", "/step", {**form, "t": token("Ana")})
        assert code == 303 and "ok=" in loc
        st = stages.derive(desk._pipelines()["fa"].standing(cid), c)
        assert (st.id, st.by) == ("contacted", "Ana")
        # The same click again, from the same stale page: refused, not doubled.
        code, loc, _ = call(where, "POST", "/step", {**form, "t": token("Ana")})
        assert "error=" in loc and "moved" in urllib.parse.unquote(loc)
        assert sum(e.kind == "contacted" for e in desk._pipelines()["fa"].events) == 1
        # The page with the step form carries no inline style or handler.
        page = call(where, "GET", f"/person/{p.person_id}?as=Ana")[2]
        assert " style=" not in page and " onclick=" not in page
        assert "They replied: OK" in page
    finally:
        srv.shutdown()


def test_steps_from_two_people_at_once_both_land_or_one_is_refused(home):
    c = desk.load_config()
    p = desk.add_now("fa", "Sam Lee", "sam@x.example")
    cid = p.applications[0].candidate_id
    for v in c.voters:
        desk.vote_now("fa", cid, v, "contact", c)
    errors: list[str] = []

    def go(v: str) -> None:
        try:
            stages.advance_now("fa", cid, "contacted", v, c, expect="to_contact")
        except DeskError as err:
            errors.append(str(err))

    threads = [threading.Thread(target=go, args=(v,)) for v in ("Ana", "Ben")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(e.kind == "contacted" for e in desk._pipelines()["fa"].events) == 1
    assert len(errors) == 1 and "moved" in errors[0]


# --------------------------------------------------------------------------
# The Tuesday recap reads the same states
# --------------------------------------------------------------------------

def test_the_recap_names_who_to_contact_this_weeks_meetings_and_who_has_not_replied():
    import tuesday
    from desk import Registry
    c = cfg()
    p, reg = Pipeline("fa"), Registry()
    for cid, name in (("x", "Sam Lee"), ("y", "Kim Ode"), ("z", "Lou Pak"), ("w", "Ida Moe")):
        p.add(Event(candidate_id=cid, kind="received", at="2026-09-20T10:00:00+00:00"))
        reg.add(cid, "fa", name)
        for v in c.voters:
            cast(p, cid, v, "contact", c, now=NOW - timedelta(days=5))
    stages.advance(p, "y", "contacted", "Ana", c, on=day(-9), now=NOW)
    for cid in ("z", "w"):
        stages.advance(p, cid, "contacted", "Ben", c, on=day(-6), now=NOW)
        stages.advance(p, cid, "first_call", "Ben", c, on=day(-1), now=NOW)
    stages.advance(p, "w", "trial_day", "Cy", c, on=day(3), now=NOW)
    w = tuesday.build({"fa": p}, reg, c, metrics.Tracking(), viewer="Ana", now=NOW,
                      titles={"fa": "Founders' Associate"})
    assert [i.name for i in w.to_contact] == ["Sam Lee"]
    assert [(i.name, d) for i, d, _ in w.first_calls] == [("Lou Pak", day(-1)),
                                                          ("Ida Moe", day(-1))]
    assert [(i.name, d, planned) for i, d, planned in w.trial_days] == [
        ("Ida Moe", day(3), True)]
    assert [i.name for i, _ in w.waiting_reply] == ["Kim Ode"]
    text = tuesday.render_text(w, c)
    for line in ("Ready to contact (anyone can", "Interviews this week", "Trial days this week",
                 "Waiting for a reply", "Kim Ode -- written to 9 days ago (past 8 days)"):
        assert line in text, line
    titles = [t for t, _ in tuesday.sections(w, c, None, lambda i: i.name)]
    assert {"Ready to contact", "Interviews this week", "Trial days this week",
            "Waiting for a reply"} <= set(titles)


def test_the_states_names_and_who_moves_them_are_set_on_the_settings_page(home):
    from console.desk_web import Desk, settings_from_form
    form = {"voters": "Ana, Ben, Cy", "state_contacted": "Written to", "advancers": "Cy"}
    desk.save_settings(settings_from_form(form), by="Ana")
    c = desk.load_config()
    assert stages.name("contacted", c) == "Written to" and c.advancers == ["Cy"]
    assert 'name="state_contacted" value="Written to"' in Desk(c, Access()).settings("Ana")
    with pytest.raises(DeskError, match="must be voters"):
        desk.save_settings(settings_from_form({**form, "advancers": "Mallory"}), by="Ana")
    desk.save_settings(settings_from_form({**form, "advancers": ""}), by="Ana")
    assert desk.load_config().advancers == []


def test_enter_in_a_box_of_the_step_form_records_nothing():
    # A browser presses a form's first button on Enter: in Withdrew's reason
    # box, that must not be "Mark: Contacted".
    from console import stages_web
    from console.desk_web import Desk
    p = agreed()
    html = stages_web.section(Desk(cfg(), Access()), Application("fa", "x"), p.standing("x"),
                              "Ana", "/", NOW)
    form = html.split('<form method="post" action="/step"')[1]
    first = form.split("<button", 2)[1]
    assert 'name="step" value=""' in first
    assert 'type="button" data-open="withdrew"' in form
    with pytest.raises(DeskError, match="nothing recorded"):
        stages_web.handle({"step": "", "posting": "fa", "candidate": "x"}, "Ana", cfg())


# --------------------------------------------------------------------------
# One partner goes ahead without waiting
# --------------------------------------------------------------------------

def one_yes(c: Config | None = None) -> Pipeline:
    p, c = pipe(), c or cfg()
    cast(p, "x", "Ana", "contact", c, now=NOW - timedelta(hours=2))
    return p


def test_a_partner_who_said_yes_can_go_ahead_and_it_says_who_and_whose_votes_were_missing():
    p = one_yes()
    ev = stages.go_ahead(p, "x", "Ana", cfg(), now=NOW)
    assert ev.detail == {"missing": ["Ben", "Cy"]}
    st = state(p)
    assert (st.id, st.by) == ("to_contact", "Ana")
    assert st.note == "Ana went ahead without waiting for Ben, Cy"
    # The missing votes can still be cast, and change nothing.
    cast(p, "x", "Ben", "pass", cfg(), now=NOW)
    assert state(p).id == "to_contact"
    step(p, "contacted")
    assert state(p).id == "contacted"


def test_going_ahead_needs_your_own_yes():
    p = one_yes()
    with pytest.raises(DeskError, match="vote .* first"):
        stages.go_ahead(p, "x", "Ben", cfg(), now=NOW)
    cast(p, "x", "Ben", "discuss", cfg(), now=NOW)
    with pytest.raises(DeskError, match="vote .* first"):
        stages.go_ahead(p, "x", "Ben", cfg(), now=NOW)


def test_going_ahead_only_while_the_votes_are_open():
    p = agreed()
    with pytest.raises(DeskError, match="only while the votes"):
        stages.go_ahead(p, "x", "Ana", cfg(), now=NOW)


def test_a_team_that_waits_for_everyone_cannot_go_ahead_early():
    c = cfg(go_ahead_alone="all_voted")
    p = one_yes(c)
    with pytest.raises(DeskError, match="waits until everyone"):
        stages.go_ahead(p, "x", "Ana", c, now=NOW)
    cast(p, "x", "Ben", "pass", c, now=NOW)
    cast(p, "x", "Cy", "discuss", c, now=NOW)
    assert state(p, c).id == "team_to_decide"
    stages.go_ahead(p, "x", "Ana", c, now=NOW)
    assert state(p, c).id == "to_contact"


def test_only_the_process_owner_goes_ahead_when_the_team_says_so():
    c = cfg(go_ahead_alone="owner", advancers=["Cy"])
    p = one_yes(c)
    with pytest.raises(DeskError, match="only Cy may go ahead"):
        stages.go_ahead(p, "x", "Ana", c, now=NOW)
    with pytest.raises(DeskError, match="needs states.advancers"):
        cfg(go_ahead_alone="owner")
    with pytest.raises(DeskError, match="go_ahead_alone must be"):
        cfg(go_ahead_alone="whoever")


def test_going_ahead_on_a_page_already_moved_is_refused():
    p = one_yes()
    stages.go_ahead(p, "x", "Ana", cfg(), now=NOW)
    with pytest.raises(DeskError, match="moved while you"):
        stages.go_ahead(p, "x", "Ana", cfg(), expect="votes_in", now=NOW)


def test_the_go_ahead_button_is_never_a_hint_about_someone_elses_vote():
    from console import stages_web
    from console.desk_web import Desk
    p = one_yes()
    d = Desk(cfg(), Access())
    html_ben = stages_web.section(d, Application("fa", "x"), p.standing("x"), "Ben", "/", NOW)
    html_ana = stages_web.section(d, Application("fa", "x"), p.standing("x"), "Ana", "/", NOW)
    assert "Go ahead without waiting" not in html_ben
    assert 'action="/go-ahead"' in html_ana and "without waiting for Ben, Cy" in html_ana


def test_a_go_ahead_counts_as_ready_to_contact_in_the_funnel_and_the_history():
    p = one_yes()
    stages.go_ahead(p, "x", "Ana", cfg(), now=NOW)
    c = metrics.build_case("fa", "x", "X", ["x"], p.events, cfg(), metrics.Tracking(), "",
                           now=NOW)
    assert metrics.funnel([c], cfg())[0].counts["to_contact"] == 1
    person = Person("x", "Sam Lee", applications=[Application("fa", "x")])
    said = [(m.text, m.detail) for m in desk.timeline(person, {"fa": p}, cfg(), "Ana")]
    assert ("Ana went ahead without waiting", "missing votes: Ben, Cy") in said


def test_each_state_says_its_next_step():
    p = agreed()
    st = state(p)
    assert stages.next_line(st, p.standing("x"), cfg()) == (
        "write the message, then mark as contacted")
    q = pipe()
    cast(q, "x", "Ana", "contact", cfg(), now=NOW)
    assert stages.said(state(q), cfg()) == "Voting (1 of 3 voted)"
    assert stages.next_line(state(q), q.standing("x"), cfg()) == "Ben, Cy to vote"


def test_go_ahead_and_the_message_through_the_page(home):
    c = desk.load_config()
    assert c.labels["contact"] == "Yes, let's talk" and c.go_ahead_alone == "after_one_yes"
    p = desk.add_now("fa", "Sam Lee", "sam@x.example")
    cid = p.applications[0].candidate_id
    desk.vote_now("fa", cid, "Ana", "contact", c)
    where, srv = start(c)
    try:
        page = call(where, "GET", f"/person/{p.person_id}?as=Ana")[2]
        token = page.split('name="t" value="')[1].split('"')[0]
        form = {"t": token, "as": "Ana", "posting": "fa", "candidate": cid, "from": "votes_in"}
        code, loc, _ = call(where, "POST", "/go-ahead", form)
        assert code == 303 and "ok=" in loc, loc
        page = call(where, "GET", f"/person/{p.person_id}?as=Ana")[2]
        assert "Write the message" in page and "Dear Sam," in page and "data-mailto" in page
        assert "Ana went ahead without waiting for Ben, Cy" in page
        assert " style=" not in page
        code, _, eml = call(where, "GET", f"/draft?posting=fa&candidate={cid}&as=Ana")
        assert code == 200 and "X-Unsent: 1" in eml
    finally:
        srv.shutdown()


def test_tracking_counts_each_role_in_whole_numbers_with_a_total(home):
    from console import tracking_web
    from console.desk_web import Desk
    c = desk.load_config()
    ids = []
    for name in ("Sam Lee", "Kim Ode", "Lou Pak"):
        ids.append(desk.add_now("fa", name, f"{name.split()[0].lower()}@x.example")
                   .applications[0].candidate_id)
    for cid, m in zip(ids, ("contact", "later", "pass")):
        for v in c.voters:
            desk.vote_now("fa", cid, v, m, c, until=day(40) if m == "later" else "")
    stages.advance_now("fa", ids[0], "contacted", "Ana", c)
    stages.advance_now("fa", ids[2], "answered_no", "Ben", c)
    rows, late, n = tracking_web.counts(Desk(c, Access()), datetime.now(timezone.utc))
    (role, r), (total, tr) = rows
    assert role == "Founders' Associate" and total == "Total" and r == tr
    assert (r["received"], r["voted"], r["to_contact"], r["contacted"], r["first_call"]) == (
        3, 3, 1, 1, 0)
    assert (r["talk_later"], r["answered_no"]) == (1, 1)
    # Nobody is "late": the page claims no deadline, it lists who still waits.
    assert all(d >= 0 for _, d in late) and late == sorted(late, key=lambda x: -x[1])
