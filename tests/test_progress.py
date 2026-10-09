"""Progress: everyone said yes to, how far each went, and where it stopped.

Every name is invented.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import desk
import stages
from console import choices_web, progress_web
from desk import Access

REPO = Path(__file__).resolve().parent.parent
TODAY = datetime.now(timezone.utc).date()


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


def person(c, name: str, decision: str, *steps: str) -> str:
    p = desk.add_now("fa", name, f"{name.split()[0].lower()}@x.example")
    cid = p.applications[0].candidate_id
    desk.vote_now("fa", cid, "Ana", decision, c)
    for s in steps:
        stages.advance_now("fa", cid, s, "Ana", c)
    return cid


def test_the_funnel_counts_who_reached_each_step_and_who_left_where(home):
    c = desk.load_config()
    person(c, "Sam Lee", "contact", "contacted", "replied", "first_call")
    person(c, "Kim Ode", "contact", "contacted", "replied", "first_call", "stop")
    person(c, "Lou Pak", "contact")
    person(c, "Ida Moe", "pass")  # never a yes: not on the page
    now = datetime.now(timezone.utc)
    got = {x.person.name: x for x in progress_web.lines(c, now)}
    assert set(got) == {"Sam Lee", "Kim Ode", "Lou Pak"}
    way = progress_web.steps(c)
    assert way[:4] == ["to_contact", "contacted", "replied", "first_call"]
    assert way[got["Sam Lee"].furthest] == "first_call" and not got["Sam Lee"].out
    assert got["Kim Ode"].out == "Not for us" and way[got["Kim Ode"].furthest] == "first_call"
    assert got["Lou Pak"].furthest == 0
    from console.desk_web import Desk
    page = progress_web.page(Desk(c, Access()), "Ana", now)
    assert "<b>3</b><span>Said yes</span>" in page and "1 left here" in page
    assert "after first interview" in page and "Ida Moe" not in page
    assert " style=" not in page and " onclick=" not in page
    left = progress_web.page(Desk(c, Access()), "Ana", now, show="out")
    assert "Kim Ode" in left and "Sam Lee" not in left


def test_saying_no_on_the_person_page_after_an_interview_stops_them(home):
    c = desk.load_config()
    cid = person(c, "Sam Lee", "contact", "contacted", "replied", "first_call")
    choices_web.record("fa", cid, "Ana", "pass", c)
    st = stages.derive(desk._pipelines()["fa"].standing(cid), c)
    assert st.id == "to_answer_no"
    cid = person(c, "Kim Ode", "contact", "contacted")
    choices_web.record("fa", cid, "Ana", "later", c)
    assert stages.derive(desk._pipelines()["fa"].standing(cid), c).id == "talk_later"
