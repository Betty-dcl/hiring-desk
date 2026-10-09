"""install.py: a clean copy outside any synced folder, empty data, a launcher, backups.

Nothing here installs anything: the plan is read, and the file steps are run
into a temporary folder.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

import install

REPO = Path(__file__).resolve().parent.parent


def test_a_synced_folder_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="OneDrive"):
        install.plan(tmp_path / "OneDrive" / "HiringDesk", None)
    p = install.plan(tmp_path / "OneDrive" / "HiringDesk", None, allow_synced=True)
    assert p.steps


def test_the_plan_says_every_step_and_schedules_backups_only_when_asked(tmp_path):
    plain = [s.what for s in install.plan(tmp_path / "desk", None, system="Windows").steps]
    assert any("copy the program" in s for s in plain) and any("launcher" in s for s in plain)
    assert not any("backup" in s for s in plain)
    full = [s.what for s in install.plan(tmp_path / "desk", tmp_path / "drive",
                                         system="Windows").steps]
    assert any("Task Scheduler" in s for s in full) and any("first backup" in s for s in full)
    linux = install.plan(tmp_path / "desk", tmp_path / "drive", system="Linux").steps
    assert any(s.note and "crontab" in s.what for s in linux)


def test_the_copy_leaves_the_demo_behind_and_never_touches_existing_data(tmp_path):
    target = tmp_path / "desk"
    (target / "runs" / "desk").mkdir(parents=True)
    (target / "runs" / "desk" / "people.json").write_text('{"real": 1}', encoding="utf-8")
    (target / "desk.toml").write_text('[team]\nvoters = ["Noa"]\n', encoding="utf-8")
    install.copy_program(REPO, target)
    assert (target / "desk.py").exists() and (target / "check.py").exists()
    assert (target / "runs" / "desk" / "people.json").read_text(encoding="utf-8") == '{"real": 1}'
    assert not list((target / "runs" / "pipeline").glob("*.json"))
    assert 'voters = ["Noa"]' in (target / "desk.toml").read_text(encoding="utf-8")
    assert not (target / "local").exists() and not (target / ".git").exists()


def test_the_backup_folder_is_written_into_the_settings(tmp_path):
    target = tmp_path / "desk"
    install.copy_program(REPO, target)
    install.set_backup_folder(target, Path("G:/My Drive/Hiring desk backups"))
    conf = tomllib.loads((target / "desk.toml").read_text(encoding="utf-8"))
    assert conf["backup"]["to"].replace("\\", "/") == "G:/My Drive/Hiring desk backups"
    assert conf["backup"]["keep"] == 30


def test_the_launcher_backs_up_then_serves_on_this_computer_only(tmp_path):
    name, text = install.launcher_text(tmp_path / "desk")
    assert "daily.py" in text and "desk.py serve --port 8765" in text
    assert "0.0.0.0" not in text and str(tmp_path / "desk") in text
    assert text.index("daily.py") < text.index("desk.py serve")



def test_on_a_mac_the_backup_is_a_launch_agent_and_can_be_left_out(tmp_path):
    mac = [s.what for s in install.plan(tmp_path / "desk", tmp_path / "drive",
                                        system="Darwin").steps]
    assert any("com.hiringdesk.evening.plist" in s for s in mac)
    none = install.plan(tmp_path / "desk", tmp_path / "drive", system="Darwin", schedule=False)
    assert not any("19:00" in s.what for s in none.steps)
    assert any(s.note and "--no-schedule" in s.what for s in none.steps)
    plist, text = install.launch_agent(tmp_path / "desk")
    assert plist.name == "com.hiringdesk.evening.plist"
    assert "<integer>19</integer>" in text and "daily.py" in text
