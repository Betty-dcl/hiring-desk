"""The pool: one page, every role, and a way back in. Every name is invented."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import desk
import stages
from console import pool_web
from console.desk_web import Desk
from desk import Access

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, "ROOT", tmp_path)
    (tmp_path / "mandates" / "generated").mkdir(parents=True)
    for pid, title in (("fa", "Founders' Associate"), ("ops", "Operations")):
        (tmp_path / "mandates" / "generated" / f"role_{pid}.provenance.json").write_text(
            json.dumps({"posting_id": pid, "title": title}), encoding="utf-8")
    toml = (REPO / "desk.toml").read_text(encoding="utf-8")
    (tmp_path / "desk.toml").write_text(toml.replace('voters = ["Recruiter"]', 'voters = ["Ana"]'),
                                        encoding="utf-8")
    return tmp_path


def add(c, posting: str, name: str, decision: str, *steps: str, comment: str = "") -> str:
    p = desk.add_now(posting, name, f"{name.split()[0].lower()}@x.example")
    cid = p.applications[0].candidate_id
    desk.vote_now(posting, cid, "Ana", decision, c, comment=comment)
    for s in steps:
        stages.advance_now(posting, cid, s, "Ana", c)
    return cid


def test_the_pool_gathers_every_role_with_why_and_how_far(home):
    c = desk.load_config()
    add(c, "fa", "Sam Lee", "later", comment="great, wrong timing")
    add(c, "ops", "Kim Ode", "contact", "contacted", "replied", "first_call", "talk_later")
    add(c, "fa", "Lou Pak", "contact")
    now = datetime.now(timezone.utc)
    kept = {k.person.name: k for k in pool_web.kept(c, "Ana", now)}
    assert set(kept) == {"Sam Lee", "Kim Ode"}
    assert kept["Sam Lee"].why == "great, wrong timing" and not kept["Sam Lee"].after
    assert kept["Kim Ode"].after == "first_call"
    assert [k.person.name for k in pool_web.kept(c, "Ana", now, q="timing")] == ["Sam Lee"]
    page = pool_web.page(Desk(c, Access()), "Ana", now)
    assert "after the first interview" in page and "Not told yet" in page
    assert "great, wrong timing" in page
    assert " style=" not in page and " onclick=" not in page
    nav = Desk(c, Access()).page("Ana", "pool", "")
    assert ">Pool</a>" in nav


def test_taking_someone_up_puts_them_back_to_contact(home):
    c = desk.load_config()
    a = add(c, "fa", "Sam Lee", "later")
    b = add(c, "ops", "Kim Ode", "contact", "contacted", "talk_later")
    for posting, cid in (("fa", a), ("ops", b)):
        pool_web.takeup({"posting": posting, "candidate": cid}, "Ana", c)
        assert stages.derive(desk._pipelines()[posting].standing(cid), c).id == "to_contact"
    assert pool_web.kept(c, "Ana", datetime.now(timezone.utc)) == []


def test_another_role_is_only_ever_an_idea(home, monkeypatch):
    import match
    c = desk.load_config()
    sam = add(c, "fa", "Sam Lee", "later")
    guesses = {"fa": 0.55, "ops": 0.71}

    class M:
        def __init__(self, total):
            self.total = total

    monkeypatch.setattr(match, "of", lambda person, pid: M(guesses[pid]))
    now = datetime.now(timezone.utc)
    (k,) = pool_web.kept(c, "Ana", now)
    assert k.idea == ("ops", 0.71)
    page = pool_web.page(Desk(c, Access()), "Ana", now)
    assert "An idea: could also fit <b>Operations</b>" in page and "speculative" in page
    asked = pool_web.page(Desk(c, Access()), "Ana", now, for_role="ops")
    assert "Ideas for <b>Operations</b>" in asked and "AI guess <b>71%</b>" in asked
    assert "Nobody is moved" in asked
    # An idea moves nobody: still in the pool, still for the role applied to.
    st = stages.derive(desk._pipelines()["fa"].standing(sam), c)
    assert st.id == "talk_later" and "ops" not in desk._pipelines() or \
        sam not in desk._pipelines().get("ops", type("P", (), {"candidates": {}})).candidates
    # Below the bar, or well under their own role: no idea is shown.
    guesses.update(fa=0.8, ops=0.6)
    (k,) = pool_web.kept(c, "Ana", now)
    assert k.idea is None
