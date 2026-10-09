"""The Calendar: a week of interviews, and the people to fit in.

No calendar is read or written: the page draws what the desk holds, plans a
meeting exactly as a person's row would, and hands Google Calendar a filled-in
"new event" page that nothing saves but a person. Every name is invented.
"""

from __future__ import annotations

import json
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import desk
import stages
from console import calendar_web
from desk import Access, Config, DeskError, Person

REPO = Path(__file__).resolve().parent.parent
NOW = datetime.now(timezone.utc).replace(microsecond=0)
TODAY = NOW.date()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, "ROOT", tmp_path)
    (tmp_path / "mandates" / "generated").mkdir(parents=True)
    (tmp_path / "mandates" / "generated" / "role_fa.provenance.json").write_text(
        json.dumps({"posting_id": "fa", "title": "Founders' Associate"}), encoding="utf-8")
    toml = (REPO / "desk.toml").read_text(encoding="utf-8")
    (tmp_path / "desk.toml").write_text(toml.replace('voters = ["Recruiter"]', 'voters = ["Ana"]'),
                                        encoding="utf-8")
    return tmp_path


def yes(c: Config, name: str, *steps: tuple[str, str, str]) -> str:
    p = desk.add_now("fa", name, f"{name.split()[0].lower()}@x.example")
    cid = p.applications[0].candidate_id
    desk.vote_now("fa", cid, "Ana", "contact", c)
    for step, on, hour in steps:
        stages.advance_now("fa", cid, step, "Ana", c, on=on, hour=hour)
    return cid


def test_google_gets_a_filled_in_event_and_nothing_more():
    c = Config(voters=["Ana"], labels={m: m for m in desk.MEANINGS}, company="Acme")
    sam = Person("p1", "Sam Lee", "sam@x.example")
    url = calendar_web.gcal(sam, "Founders' Associate", "interview_2", "2026-10-14", "10:30", c)
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert url.startswith("https://calendar.google.com/calendar/render?")
    assert q["action"] == ["TEMPLATE"] and q["ctz"] == ["Europe/Madrid"]
    assert q["dates"] == ["20261014T103000/20261014T111500"]  # 45 minutes by default
    assert q["add"] == ["sam@x.example"] and "Sam Lee" in q["text"][0]
    # No time: the whole day. A trial day with a time: the working day.
    whole = calendar_web.gcal(sam, "FA", "first_call", "2026-10-14", "", c)
    assert "dates=20261014%2F20261015" in whole
    trial = calendar_web.gcal(sam, "FA", "trial_day", "2026-10-14", "09:00", c)
    assert "20261014T090000%2F20261014T170000" in trial


def test_the_week_shows_the_meetings_and_the_side_the_people_to_fit_in(home):
    c = desk.load_config()
    soon = (TODAY + timedelta(days=1)).isoformat()
    planned = yes(c, "Sam Lee", ("contacted", "", ""), ("first_call", soon, "10:00"))
    ready = yes(c, "Kim Ode", ("contacted", "", ""), ("replied", "", ""))
    yes(c, "Lou Pak", ("contacted", "", ""))
    meetings, fit = calendar_web.collect(c, NOW)
    assert [(m.person.name, m.step, m.time) for m in meetings] == [
        ("Sam Lee", "first_call", "10:00")]
    assert [(t.person.name, t.why) for t in fit] == [("Kim Ode", "replied OK"),
                                                    ("Lou Pak", "no reply yet")]
    from console.desk_web import Desk
    d = Desk(c, Access())
    week = (TODAY + timedelta(days=1)).isoformat()
    page = calendar_web.page(d, "Ana", week, NOW)
    month = calendar_web.page(d, "Ana", week[:7], NOW, "month")
    assert "Sam Lee" in month and 'class="cal month"' in month and ">Month<" in month
    assert "Sam Lee" in page and "To fit in" in page and f'value="fa/{ready}"' in page
    # Planned already: in the list for a meeting of your own, with no "next" to plan.
    assert f'value="fa/{planned}">Sam Lee · Founders&#x27; Associate</option>' in page
    assert "next: first interview" in page and 'data-filter="who"' in page
    assert " style=" not in page and " onclick=" not in page
    # A slot before today is not offered to plan in.
    assert f'data-day="{(TODAY - timedelta(days=1)).isoformat()}"' not in page


def test_putting_someone_in_plans_their_row_as_the_row_would(home):
    c = desk.load_config()
    cid = yes(c, "Kim Ode", ("contacted", "", ""), ("replied", "", ""))
    soon = (TODAY + timedelta(days=2)).isoformat()
    msg = calendar_web.plan({"who": f"fa/{cid}", "on": soon, "time": "9:30"}, "Ana", c)
    assert "first interview" in msg
    st = stages.derive(desk._pipelines()["fa"].standing(cid), c)
    assert (st.id, st.on, st.time, st.planned) == ("first_call", soon, "09:30", True)
    # Planned now: nothing more to put in for them, and a day is always asked for.
    with pytest.raises(DeskError, match="nothing to plan"):
        calendar_web.plan({"who": f"fa/{cid}", "on": soon}, "Ana", c)
    other = yes(c, "Lou Pak", ("contacted", "", ""))
    with pytest.raises(DeskError, match="choose a day"):
        calendar_web.plan({"who": f"fa/{other}", "on": ""}, "Ana", c)


def test_the_row_of_a_planned_interview_offers_google_calendar(home):
    from console import track_web
    from console.desk_web import Desk
    from desk import Application
    c = desk.load_config()
    soon = (TODAY + timedelta(days=1)).isoformat()
    cid = yes(c, "Sam Lee", ("contacted", "", ""), ("first_call", soon, "10:00"))
    s = desk._pipelines()["fa"].standing(cid)
    row = track_web.tracker(Desk(c, Access()), Application("fa", cid), s,
                            stages.derive(s, c), "Ana", "/", NOW)
    assert "Add to Google Calendar" in row and "calendar.google.com" in row
    assert "Calendar" in Desk(c, Access()).page("Ana", "calendar", "")


def test_one_persons_page_is_the_row_in_full_and_nothing_twice(home):
    from console.desk_web import Desk
    c = desk.load_config()
    cid = yes(c, "Sam Lee", ("contacted", "", ""),
              ("first_call", TODAY.isoformat(), ""))  # today: it has happened
    p = desk.load_registry(desk._registry_path()).person_of(cid)
    page = Desk(c, Access()).person("Ana", p.person_id, datetime.now(timezone.utc))
    # The way through, the next step and its arrow: once.
    assert page.count('class="trk"') == 1 and 'value="interview_2">Continue</button>' in page
    # No table of a single decision, no "Answered no" beside "Stop here".
    assert "<h2>Your decision</h2>" not in page and ">Answered no<" not in page
    assert "Change it here" in page and 'name="about"' in page
    assert 'name="comment"' in page and 'value="pass"' in page  # decide from here
    assert " style=" not in page and " onclick=" not in page


def test_the_calendar_styles_touch_nothing_else_on_the_desk():
    # A side panel once borrowed the class of the AI guess and boxed it on every row.
    import re
    names = set(re.findall(r"^\.([a-z][\w-]*)", calendar_web.CSS, flags=re.M))
    assert names and all(n.startswith("cal") or n == "gcal" for n in names), names


def test_the_form_plans_any_later_interview_a_meeting_of_your_own_and_a_note(home):
    c = desk.load_config()
    soon = (TODAY + timedelta(days=3)).isoformat()
    cid = yes(c, "Kim Ode", ("contacted", "", ""), ("first_call", TODAY.isoformat(), ""))
    # Straight to the third interview, with who will be there.
    calendar_web.plan({"who": f"fa/{cid}", "what": "interview_3", "on": soon, "time": "11:00",
                       "note": "with Ana or Ben"}, "Ana", c)
    st = stages.derive(desk._pipelines()["fa"].standing(cid), c)
    assert (st.id, st.time, st.memo) == ("interview_3", "11:00", "with Ana or Ben")
    # Something else: on the calendar, and the steps do not move.
    calendar_web.plan({"who": f"fa/{cid}", "what": "other", "other": "Coffee with the CTO",
                       "on": soon, "time": "16:00"}, "Ana", c)
    assert stages.derive(desk._pipelines()["fa"].standing(cid), c).id == "interview_3"
    meetings, _ = calendar_web.collect(c, NOW)
    assert {(m.step, m.label or m.note) for m in meetings} >= {
        ("interview_3", "with Ana or Ben"), ("other", "Coffee with the CTO")}
    with pytest.raises(DeskError, match="write what the meeting is"):
        calendar_web.plan({"who": f"fa/{cid}", "what": "other", "on": soon}, "Ana", c)


def test_the_calendar_opens_on_the_month_and_today_is_found(home):
    from console.desk_web import Desk
    d = Desk(desk.load_config(), Access())
    page = calendar_web.page(d, "Ana", "", NOW)
    assert 'class="cal month"' in page and 'id="today"' in page and "#today\">Today<" in page
    week = calendar_web.page(d, "Ana", "", NOW, "week")
    assert 'id="today"' in week and 'class="tcol"' in week
