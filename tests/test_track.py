"""The Interested tab: a yes followed on its own row, step by step.

One person deciding sees the people they want first, each with the way
through (to contact, contacted, replied OK, the interviews, the trial day)
and the one thing to do now. "Stop here" sends them to the no's to write,
and they leave once it is sent. Every name is invented.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import stages
from console import stages_web, track_web
from desk import Access, Application, Config, cast
from pipeline import Event, Pipeline

NOW = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
TODAY = NOW.date()
LABELS = {"contact": "Yes, let's talk", "discuss": "Not sure, to think about",
          "later": "Keep in the pool", "pass": "Not for us"}


def solo(**kw) -> Config:
    kw.setdefault("later_needs_date", False)
    return Config(voters=["Ana"], labels=dict(LABELS), **kw)


def team() -> Config:
    return Config(voters=["Ana", "Ben", "Cy"], labels=dict(LABELS))


def day(n: int) -> str:
    return (TODAY + timedelta(days=n)).isoformat()


def yes(c: Config | None = None) -> Pipeline:
    p = Pipeline("fa")
    p.add(Event(candidate_id="x", kind="received", at="2026-09-20T10:00:00+00:00"))
    cast(p, "x", "Ana", "contact", c or solo(), now=NOW - timedelta(days=12))
    return p


def state(p: Pipeline, c: Config | None = None) -> stages.State:
    return stages.derive(p.standing("x"), c or solo(), NOW)


def step(p: Pipeline, what: str, c: Config | None = None, **kw):
    return stages.advance(p, "x", what, "Ana", c or solo(), now=NOW, **kw)


def test_their_ok_is_a_step_between_the_message_and_the_first_interview():
    p = yes()
    step(p, "contacted", on=day(-10))
    assert stages.allowed(state(p), solo())[0] == "replied"
    step(p, "replied", on=day(-8))
    st = state(p)
    assert (st.id, st.on) == ("replied", day(-8))
    assert stages.allowed(st, solo())[0] == "first_call"
    assert stages.next_step(p.standing("x"), solo(), NOW) == "first interview to plan"
    marks, _ = stages.journey(p.standing("x"), solo(), NOW)
    assert [m.status for m in marks if m.id in ("contacted", "replied")] == ["done", "now"]


def test_a_reply_after_an_interview_is_not_a_step_back():
    p = yes()
    step(p, "contacted", on=day(-10))
    step(p, "first_call", on=day(-5))
    p.add(Event(candidate_id="x", kind="replied", by="Ana",
                at=(NOW - timedelta(days=1)).isoformat()))
    assert state(p).id == "first_call"


def test_stop_here_moves_them_to_the_nos_to_send_then_out():
    p = yes()
    step(p, "contacted", on=day(-10))
    step(p, "first_call", on=day(3))  # planned: stopping does not wait for its day
    assert "stop" in stages.allowed(state(p), solo())
    step(p, "stop")
    st = state(p)
    assert (st.id, st.by) == ("to_answer_no", "Ana")
    assert stages.meaning_of(st, solo()) == "pass"
    assert stages_web.tab_of(st, p.standing("x"), "Ana", solo()) == "no"
    step(p, "answered_no")
    assert state(p).id == "answered_no"


def test_nothing_to_stop_before_they_were_written_to():
    st = state(yes())
    assert st.id == "to_contact" and "stop" not in stages.allowed(st, solo())


def test_one_persons_tabs_are_the_phases_in_order():
    assert stages_web.tabs_for(solo()) == ("todo", "interested", "discuss", "no", "all")
    assert stages_web.first_tab(solo()) == "todo"
    assert stages_web.first_tab(team()) == "todo"
    p = yes()
    assert stages_web.tab_of(state(p), p.standing("x"), "Ana", solo()) == "interested"
    step(p, "contacted", on=day(-10))
    step(p, "first_call", on=day(-5))
    step(p, "trial_day", on=day(-1))
    # The trial day has happened: the desk's part is over.
    assert stages_web.tab_of(state(p), p.standing("x"), "Ana", solo()) == "closed"
    # A team keeps its own tabs.
    t = yes(team())
    stages.go_ahead(t, "x", "Ana", team(), now=NOW)
    assert stages_web.tab_of(state(t, team()), t.standing("x"), "Ana", team()) == "write"


def _row(p: Pipeline, c: Config) -> str:
    from console.desk_web import Desk
    d = Desk(c, Access())
    return track_web.tracker(d, Application("fa", "x"), p.standing("x"), state(p, c), "Ana",
                             "/?tab=interested", NOW)


def test_the_row_shows_the_way_and_the_one_thing_to_do():
    c = solo()
    p = yes()
    step(p, "contacted", on=day(-10))
    step(p, "replied", on=day(-8))
    row = _row(p, c)
    assert '<li class="done"><b>Contacted</b>' in row and '<li class="now"><b>Replied, OK' in row
    assert 'value="first_call">Plan it</button>' in row
    assert 'name="list" value="1"' in row
    # The ways out are there, small: stop, the pool (one click, no date), withdrew.
    assert 'value="stop"' in row and 'value="talk_later">Keep in the pool' in row
    assert " style=" not in row and " onclick=" not in row

    step(p, "first_call", on=day(4))
    row = _row(p, c)
    assert "Change the date" in row and 'name="about" value="First interview"' in row

    p2 = yes()
    step(p2, "contacted", on=day(-10))
    step(p2, "first_call", on=day(-2))
    row = _row(p2, c)
    # After the interview: the arrow to the next one, or straight to the trial day.
    assert 'value="interview_2">Continue</button>' in row
    assert 'value="trial_day">Straight to it</button>' in row


def test_a_pool_that_needs_a_date_is_not_offered_in_one_click():
    c = solo(later_needs_date=True)
    p = yes(c)
    step(p, "contacted", c, on=day(-10))
    step(p, "replied", c, on=day(-8))
    assert 'value="talk_later"' not in _row(p, c)


# --------------------------------------------------------------------------
# One message per step, with the meeting's day and time
# --------------------------------------------------------------------------

def test_each_meeting_has_its_invitation_with_its_day_and_time():
    import desk
    from desk import Person
    c = desk.load_config(overrides=False)  # the examples desk.toml ships
    sam = Person("p1", "Sam Lee", "sam@x.example")
    planned = stages.State("interview_2", on="2026-10-14", planned=True, time="10:30")
    m = desk.draft(sam, "Founders' Associate", "interview_2", c,
                   when={**stages.when(planned), "interview": "second interview"})
    body = m.get_content()
    assert "second interview" in body and "Wednesday 14 October at 10:30" in body
    assert "10:30" in str(m["Subject"]) and "[day]" not in body
    # Not planned yet: blanks to fill in by hand, never an invented date.
    m = desk.draft(sam, "Founders' Associate", "first_call", c)
    assert "[day] at [time]" in m.get_content()
    # Every interview shares the same words, unless one is given its own.
    assert c.template("interview_5") == c.template("first_call") == c.template("interview") != ""
    assert c.template("trial_day") and "{day}" in c.template("trial_day")


def test_the_house_rules_show_a_single_invitation_for_every_interview():
    from console.desk_web import Desk
    page = Desk(solo(), Access()).settings("Ana")
    assert 'name="template_interview"' in page and 'name="template_first_call"' not in page
    assert 'name="template_trial_day"' in page and 'data-f="interview"' in page


def test_a_mail_not_on_the_page_keeps_its_words(tmp_path, monkeypatch):
    import desk
    from console.desk_web import settings_from_form
    monkeypatch.setattr(desk, "ROOT", tmp_path)
    (tmp_path / "desk.toml").write_text('[team]\nvoters = ["Ana"]\n', encoding="utf-8")
    desk.save_settings({"templates": {"interview_4": "Our words for the fourth."}}, by="Ana")
    # A later save from a page showing two interviews only.
    desk.save_settings(settings_from_form({"template_interview": "Hello {first_name}"}),
                       by="Ana")
    c = desk.load_config()
    assert c.templates["interview_4"] == "Our words for the fourth."
    assert c.templates["interview"] == "Hello {first_name}"
    assert c.template("interview_4") == "Our words for the fourth."
    assert c.template("interview_2") == "Hello {first_name}"


def test_a_mail_switched_off_is_never_offered():
    from pathlib import Path
    import desk
    from console.desk_web import Desk, settings_from_form
    shipped = desk._from_toml(Path(__file__).resolve().parent.parent / "desk.toml")
    # No reminder by default: someone who does not reply is not chased.
    assert shipped.mails_off == ["follow_up"]
    p = yes()
    step(p, "contacted", on=day(-10))
    assert stages.meaning_of(state(p, shipped), shipped) == ""
    assert stages.meaning_of(state(p), solo()) == "follow_up"
    # Switching off the interview mail switches off every invitation to one.
    step(p, "replied", on=day(-8))
    off = solo(mails_off=["interview"])
    assert stages.meaning_of(state(p, off), off) == ""
    # The House rules box: unticked is off, ticked is on.
    page = Desk(off, Access()).settings("Ana")
    assert 'name="use_interview" value="1">' in page and 'name="use_pass" value="1" checked' in page
    form = {"template_contact": "", "use_contact": "1", "use_pass": "1", "use_later": "1",
            "use_trial_day": "1"}
    assert settings_from_form(form)["mails_off"] == ["follow_up", "interview"]


def test_interested_can_be_narrowed_to_the_step_they_are_at(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    import desk
    from datetime import datetime, timezone
    from console.desk_web import Desk
    repo = Path(__file__).resolve().parent.parent
    monkeypatch.setattr(desk, "ROOT", tmp_path)
    (tmp_path / "mandates" / "generated").mkdir(parents=True)
    (tmp_path / "mandates" / "generated" / "role_fa.provenance.json").write_text(
        json.dumps({"posting_id": "fa", "title": "FA"}), encoding="utf-8")
    toml = (repo / "desk.toml").read_text(encoding="utf-8")
    (tmp_path / "desk.toml").write_text(toml.replace('voters = ["Recruiter"]', 'voters = ["Ana"]'),
                                        encoding="utf-8")
    c = desk.load_config()
    for name, steps in (("Sam Lee", ["contacted"]), ("Kim Ode", ["contacted", "replied"]),
                        ("Lou Pak", [])):
        p = desk.add_now("fa", name, f"{name.split()[0].lower()}@x.example")
        cid = p.applications[0].candidate_id
        desk.vote_now("fa", cid, "Ana", "contact", c)
        for s in steps:
            stages.advance_now("fa", cid, s, "Ana", c)
    d, now = Desk(c, Access()), datetime.now(timezone.utc)
    page = d.applications("Ana", "interested", now)
    assert ">Contacted<em>1</em>" in page and ">Replied OK<em>1</em>" in page
    assert ">All<em>3</em>" in page
    only = d.applications("Ana", "interested", now, step="contacted")
    assert "Sam Lee" in only and "Kim Ode" not in only and "Lou Pak" not in only
