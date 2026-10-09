"""Backups and the health check: nothing is lost, and anything wrong is said."""

from __future__ import annotations

import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import backup
import check
import desk

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, "ROOT", tmp_path)
    (tmp_path / "mandates" / "generated").mkdir(parents=True)
    (tmp_path / "mandates" / "generated" / "role_fa.provenance.json").write_text(
        json.dumps({"posting_id": "fa", "title": "FA"}), encoding="utf-8")
    toml = (REPO / "desk.toml").read_text(encoding="utf-8")
    (tmp_path / "desk.toml").write_text(toml.replace('voters = ["Recruiter"]', 'voters = ["Ana"]'),
                                        encoding="utf-8")
    p = desk.add_now("fa", "Sam Lee", "sam@x.example",
                     files=[("cv", "cv.txt", b"Built a ledger in Python.")])
    desk.vote_now("fa", p.applications[0].candidate_id, "Ana", "contact", desk.load_config())
    return tmp_path


def test_a_backup_holds_everything_reads_back_and_keeps_the_newest(home, tmp_path):
    dest = tmp_path / "drive"
    t = datetime(2026, 10, 6, 19, tzinfo=timezone.utc)
    for i in range(4):
        backup.make(dest, home, keep=3, now=t + timedelta(days=i))
    zips = backup.listing(dest)
    assert len(zips) == 3 and "2026-10-09" in zips[0].name
    with zipfile.ZipFile(zips[0]) as z:
        names = z.namelist()
    assert "desk.toml" in names and "runs/desk/people.json" in names
    assert any(n.startswith("runs/pipeline/") for n in names)
    assert any(n.startswith("runs/desk/files/") for n in names)
    assert not any(n.endswith(".lock") for n in names)
    assert backup.last(home)["files"] == len(names)


def test_a_restore_moves_the_current_data_aside_first(home, tmp_path):
    made = backup.make(tmp_path / "drive", home)
    (home / "runs" / "desk" / "people.json").write_text("{}", encoding="utf-8")
    aside = backup.restore(made, home)
    assert json.loads((aside / "desk" / "people.json").read_text(encoding="utf-8")) == {}
    assert "Sam Lee" in (home / "runs" / "desk" / "people.json").read_text(encoding="utf-8")
    assert (aside / "desk.toml").exists()


def test_a_zip_reaching_outside_the_desk_is_refused(home, tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("../evil.txt", "x")
        z.writestr("runs/desk/people.json", "{}")
    with pytest.raises(backup.BackupError, match="outside the desk"):
        backup.restore(bad, home)


def test_the_check_says_ok_then_finds_a_missing_cv_and_an_old_backup(home, tmp_path):
    levels = {r.what: r.level for r in check.run()}
    assert levels["Every CV and answer is on disk"] == "OK"
    assert levels["Backups"] == "WARN"  # none yet
    backup.make(tmp_path / "drive", home, now=datetime.now(timezone.utc) - timedelta(days=5))
    for f in desk._files_dir().rglob("*"):
        if f.is_file():
            f.unlink()
    got = {r.what: r for r in check.run()}
    assert got["Every CV and answer is on disk"].level == "FAIL"
    assert got["Backups"].level == "WARN" and "5 day" in got["Backups"].detail


def test_a_synced_folder_is_recognised():
    assert check.synced(Path("C:/Users/x/OneDrive - Acme/HiringDesk/runs")) == "onedrive"
    assert check.synced(Path("/Users/x/Library/Mobile Documents/HiringDesk")) == "mobile documents"
    assert check.synced(Path("C:/HiringDesk/runs")) == ""
