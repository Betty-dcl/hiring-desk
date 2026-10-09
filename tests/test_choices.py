"""Choices switched off, and choices of one's own.

Yes and No are always there. "Not sure" and the pool can be switched off; a
choice of the team's own keeps people aside in a tab of its own, as the pool
does. Every name is invented.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import desk
import stages
from console import choices_web, stages_web
from console.desk_web import Desk, settings_from_form
from desk import Access, Application, DeskError

REPO = Path(__file__).resolve().parent.parent


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


def test_a_choice_switched_off_has_no_button_and_no_tab(home):
    desk.save_settings({"choices_off": ["discuss"]}, by="Ana")
    c = desk.load_config()
    form = Desk(c, Access()).vote_form(Application("fa", "x"), {}, "Ana")
    assert 'value="discuss"' not in form and 'value="contact"' in form
    assert "discuss" not in stages_web.tabs_for(c)
    with pytest.raises(DeskError, match="yes and no are always there"):
        desk.save_settings({"choices_off": ["pass"]}, by="Ana")


def test_an_own_choice_keeps_people_aside_in_its_tab_and_a_new_decision_ends_it(home):
    desk.save_settings({"extra_choices": ["Another role"]}, by="Ana")
    c = desk.load_config()
    assert stages_web.tabs_for(c) == ("todo", "interested", "discuss", "x0", "no", "all")
    p = desk.add_now("fa", "Sam Lee", "sam@x.example")
    cid = p.applications[0].candidate_id
    assert choices_web.record("fa", cid, "Ana", "x:0", c) == "Another role"
    s = desk._pipelines()["fa"].standing(cid)
    st = stages.derive(s, c)
    assert st.id == "talk_later" and stages_web.tab_of(st, s, "Ana", c) == "x0"
    assert choices_web.own_of(s, "Ana", c) == "Another role"
    page = Desk(c, Access()).applications("Ana", "x0", desk.datetime.now(desk.timezone.utc))
    assert "Sam Lee" in page and ">Another role<em>1</em>" in page
    # A new decision ends the own choice.
    choices_web.record("fa", cid, "Ana", "contact", c)
    s = desk._pipelines()["fa"].standing(cid)
    assert choices_web.own_of(s, "Ana", c) == "" and not [t for t in s.tags if "choice:" in t]


def test_house_rules_read_the_choices_back():
    form = {"label_contact": "", "usechoice_later": "1", "extra_choices": "Another role\n\n"
            "another role\nNext year"}
    out = settings_from_form(form)
    assert out["choices_off"] == ["discuss"]
    assert out["extra_choices"] == ["Another role", "Next year"]


def test_one_persons_guided_questions_ask_nothing_about_voters_signature_or_recap(home):
    from console import setup_web as su
    c = desk.load_config()
    ids = {q.id for q in su.questions(c)}
    assert not ids & {"voters", "blind", "sender", "recap_weekday", "answer_within_days"}
    page = su.page(Desk(c, Access()), "Ana")
    assert "Who votes and how" not in page and "a note for the others" not in page
    assert ">The rules</a>" in page and "Everyone's answers" not in page
    assert "for everyone" not in page
