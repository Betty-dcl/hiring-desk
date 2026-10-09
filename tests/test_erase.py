"""Erasing a person: everything, everywhere, and only a trace that it was done.

Every name is invented.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import ashby
import desk
import erase
import stages
from console import erase_web
from console.desk_web import Desk
from desk import Access, DeskError

REPO = Path(__file__).resolve().parent.parent
NOW = datetime.now(timezone.utc)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, "ROOT", tmp_path)
    monkeypatch.setattr(ashby, "ROOT", tmp_path)
    (tmp_path / "mandates" / "generated").mkdir(parents=True)
    for pid in ("fa", "ops"):
        (tmp_path / "mandates" / "generated" / f"role_{pid}.provenance.json").write_text(
            json.dumps({"posting_id": pid, "title": pid.upper()}), encoding="utf-8")
    toml = (REPO / "desk.toml").read_text(encoding="utf-8")
    (tmp_path / "desk.toml").write_text(toml.replace('voters = ["Recruiter"]', 'voters = ["Ana"]'),
                                        encoding="utf-8")
    return tmp_path


def sam(home) -> tuple[desk.Person, list[str]]:
    c = desk.load_config()
    p = desk.add_now("fa", "Sam Lee", "sam@x.example",
                     files=[("cv", "cv.txt", b"Sam Lee built a ledger.")])
    desk.add_now("ops", "Sam Lee", "sam@x.example",
                 files=[("letter", "why.txt", b"I want to own payments.")])
    p = desk.load_registry(desk._registry_path()).people[p.person_id]
    cids = [a.candidate_id for a in p.applications]
    desk.vote_now("fa", cids[0], "Ana", "contact", c)
    desk.note_now("fa", cids[0], "Ana", "Strong on ledgers", c)
    (home / "runs" / "facts").mkdir(parents=True, exist_ok=True)
    (home / "runs" / "facts" / f"facts_{cids[0]}.json").write_text('{"name": "Sam Lee"}',
                                                                   encoding="utf-8")
    (home / "runs" / "desk" / "ashby.json").write_text(json.dumps({
        "sync_token": "t", "applications": {"app-1": {"candidate_id": "ash-9", "posting": "fa",
                                                      "person_id": p.person_id, "at": "x"}},
        "pushed": {}}), encoding="utf-8")
    return p, cids


def test_everything_about_a_person_is_erased_in_every_role(home):
    other = desk.add_now("fa", "Kim Ode", "kim@x.example")
    p, cids = sam(home)
    r = erase.erase(p.person_id, "Ana", "asked", desk.load_config())
    assert r.applications == 2 and sorted(r.roles) == ["fa", "ops"] and r.files == 3
    assert r.remaining == []
    reg = desk.load_registry(desk._registry_path())
    assert p.person_id not in reg.people and other.person_id in reg.people
    for pipe in desk._pipelines().values():
        assert not set(cids) & set(pipe.candidates)
    assert not list(desk._files_dir().rglob("*.txt"))
    assert not (home / "runs" / "facts" / f"facts_{cids[0]}.json").exists()
    # Kim is untouched.
    assert other.applications[0].candidate_id in desk._pipelines()["fa"].candidates


def test_only_a_nameless_trace_is_kept(home):
    p, _ = sam(home)
    erase.erase(p.person_id, "Ana", "asked", desk.load_config(), now=NOW)
    (line,) = (home / "runs" / "desk" / "erasures.jsonl").read_text(encoding="utf-8").splitlines()
    rec = json.loads(line)
    assert rec["reason"] == "asked" and rec["by"] == "Ana" and rec["applications"] == 2
    assert "Sam" not in line and "sam@" not in line and p.person_id not in line


def test_ashby_never_brings_an_erased_person_back(home):
    p, _ = sam(home)
    erase.erase(p.person_id, "Ana", "asked", desk.load_config())
    state = ashby.load_state()
    assert "app-1" not in state["applications"] and "app-1" not in json.dumps(state)
    assert ashby._erased("app-1", state) and not ashby._erased("app-2", state)


def test_erasing_asks_why_and_cannot_be_done_twice(home):
    p, _ = sam(home)
    with pytest.raises(DeskError, match="say why"):
        erase.erase(p.person_id, "Ana", "because", desk.load_config())
    erase.erase(p.person_id, "Ana", "mistake", desk.load_config())
    with pytest.raises(DeskError, match="already erased"):
        erase.erase(p.person_id, "Ana", "mistake", desk.load_config())


def test_the_page_asks_for_the_box_before_erasing(home):
    p, _ = sam(home)
    c = desk.load_config()
    page = Desk(c, Access()).person("Ana", p.person_id, NOW)
    assert "Erase this person" in page and 'name="sure"' in page
    with pytest.raises(DeskError, match="tick the box"):
        erase_web.handle("/erase", {"person": p.person_id, "reason": "asked"}, [], "Ana", c)
    said = erase_web.handle("/erase", {"person": p.person_id, "reason": "asked", "sure": "1"},
                            [], "Ana", c)
    assert said == "1 person erased"


def _age(home, posting: str, cid: str, days: float) -> None:
    pipe = desk._pipelines()[posting]
    for e in pipe.events:
        if e.candidate_id == cid:
            e.at = (NOW - timedelta(days=days)).isoformat()
    desk._save(pipe)


def test_retention_lists_the_old_never_someone_in_process(home):
    c = desk.load_config()
    old_no = desk.add_now("fa", "Lou Pak", "lou@x.example").applications[0].candidate_id
    desk.vote_now("fa", old_no, "Ana", "pass", c)
    stages.advance_now("fa", old_no, "answered_no", "Ana", c)
    talking = desk.add_now("fa", "Ida Moe", "ida@x.example").applications[0].candidate_id
    desk.vote_now("fa", talking, "Ana", "contact", c)
    stages.advance_now("fa", talking, "contacted", "Ana", c)
    pooled = desk.add_now("fa", "Noa Rey", "noa@x.example").applications[0].candidate_id
    desk.vote_now("fa", pooled, "Ana", "later", c)
    recent = desk.add_now("fa", "Tom Aye", "tom@x.example").applications[0].candidate_id
    for cid in (old_no, talking, pooled):
        _age(home, "fa", cid, 400)
    _age(home, "fa", recent, 10)
    names = [d.person.name for d in erase.due(c, NOW)]
    # A year and more since the no: due. In process: never. The pool: two years.
    assert names == ["Lou Pak"]
    _age(home, "fa", pooled, 800)
    assert sorted(d.person.name for d in erase.due(c, NOW)) == ["Lou Pak", "Noa Rey"]
    page = erase_web.page(Desk(c, Access()), "Ana", NOW)
    assert "Lou Pak" in page and "Ida Moe" not in page and "Tom Aye" not in page
