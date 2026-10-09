#!/usr/bin/env python3
"""Install the hiring desk on this computer, for real use. Standard library only.

    python install.py --dry-run                       # say every step, do nothing
    python install.py --backup-to "G:/My Drive/Hiring desk backups"
    python install.py --to "D:/HiringDesk" --backup-to "..."

What it does, in order, each step printed before it is done:

1. Checks Python (3.11 or later).
2. Copies the program to its own folder (default: HiringDesk in your home
   folder), **outside** OneDrive, Google Drive, Dropbox or iCloud: a sync tool
   can leave a file "online only", and the desk then cannot read its own data.
   It refuses a synced folder unless `--allow-synced`.
3. Starts with empty data: the invented demo applications of this repository
   are not copied. The postings, the settings (desk.toml) and the logo are.
4. Creates a Python environment there and installs its single requirement.
5. Puts a "Hiring desk" launcher on the Desktop: a double-click starts the
   desk and opens it in the browser, on this computer only. Each start first
   runs daily.py: new applications from Ashby (when switched on), a backup
   (when a backup folder is set).
6. With `--backup-to`: writes it in desk.toml and schedules daily.py every
   evening at 19:00 (Windows Task Scheduler, or a macOS LaunchAgent; on Linux
   the cron line is printed, not installed).
7. Runs the health check (check.py) in the new folder.

Nothing is sent anywhere, no account is created, nothing is paid for. It can
be run again: the program files are refreshed, the data is never touched.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent
PORT = 8765
#: Never copied: the demo's data, local and personal files, caches, history.
SKIP_DIRS = {"runs", "local", ".git", ".venv", "venv", "__pycache__", ".pytest_cache",
             ".mypy_cache", ".ruff_cache", ".claude"}
SKIP_FILES = {".DS_Store", "Thumbs.db", "desktop.ini"}


@dataclass
class Step:
    what: str
    do: Callable[[], None] | None = None
    #: A step that is only said (a line to add by hand), never done.
    note: bool = False


@dataclass
class Plan:
    target: Path
    steps: list[Step] = field(default_factory=list)


def default_target() -> Path:
    return Path.home() / "HiringDesk"


def synced(path: Path) -> str:
    import check
    return check.synced(path)


def venv_python(target: Path) -> Path:
    return (target / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))


def desktop() -> Path:
    """The real Desktop folder (OneDrive may have moved it on Windows)."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            # CSIDL_DESKTOPDIRECTORY = 0x10
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) == 0:
                return Path(buf.value)
        except (AttributeError, OSError):
            pass
    return Path.home() / "Desktop"


def copy_program(source: Path, target: Path) -> None:
    """The program files only. Existing data under target/runs is never touched."""
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        if item.name in SKIP_DIRS or item.name in SKIP_FILES:
            continue
        dest = target / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(*SKIP_DIRS, *SKIP_FILES, "*.pyc"))
        else:
            # desk.toml holds the team's settings once installed: kept if there.
            if item.name == "desk.toml" and dest.exists():
                continue
            shutil.copy2(item, dest)
    for d in ("runs/desk", "runs/pipeline"):
        (target / d).mkdir(parents=True, exist_ok=True)


def launcher_text(target: Path) -> tuple[str, str]:
    """(file name, content) of the Desktop launcher for this system."""
    if os.name == "nt":
        return "Hiring desk.cmd", (
            "@echo off\r\n"
            "rem The hiring desk, on this computer only. Closing this window stops it.\r\n"
            f'cd /d "{target}"\r\n'
            "set PYTHONUTF8=1\r\n"
            '".venv\\Scripts\\python.exe" daily.py\r\n'
            f'start "" cmd /c "timeout /t 2 >nul & start http://127.0.0.1:{PORT}"\r\n'
            f'".venv\\Scripts\\python.exe" desk.py serve --port {PORT}\r\n'
            "pause\r\n")
    return "Hiring desk.command", (
        "#!/bin/bash\n"
        "# The hiring desk, on this computer only. Closing this window stops it.\n"
        f'cd "{target}" || exit 1\n'
        "export PYTHONUTF8=1\n"
        "./.venv/bin/python daily.py\n"
        f"(sleep 2; open http://127.0.0.1:{PORT} 2>/dev/null || "
        f"xdg-open http://127.0.0.1:{PORT}) &\n"
        f"./.venv/bin/python desk.py serve --port {PORT}\n")


def write_launcher(target: Path) -> Path:
    name, text = launcher_text(target)
    for where in (target / name, desktop() / name):
        where.parent.mkdir(parents=True, exist_ok=True)
        where.write_text(text, encoding="utf-8", newline="")
        if os.name != "nt":
            where.chmod(0o755)
    return desktop() / name


def set_backup_folder(target: Path, dest: Path) -> None:
    """Write [backup] to = "<dest>" in the installed desk.toml."""
    f = target / "desk.toml"
    text = f.read_text(encoding="utf-8")
    line = f'to = {json.dumps(str(dest))}'
    if "\n[backup]\n" not in text:
        text += f"\n[backup]\n{line}\nkeep = 30\n"
    else:
        head, _, rest = text.partition("\n[backup]\n")
        lines = rest.split("\n")
        for i, l in enumerate(lines):
            if l.startswith("["):
                lines.insert(i, line)
                break
            if l.startswith("to ="):
                lines[i] = line
                break
        else:
            lines.append(line)
        text = head + "\n[backup]\n" + "\n".join(lines)
    f.write_text(text, encoding="utf-8")


#: The evening task's name, in Task Scheduler and as a macOS LaunchAgent.
TASK = "Hiring desk evening"
AGENT = "com.hiringdesk.evening"


def schedule_command(target: Path) -> list[str] | None:
    """The command that schedules the evening run, or None where it is printed."""
    # pythonw: the evening run opens no window.
    py = str(venv_python(target)).replace("python.exe", "pythonw.exe")
    if os.name == "nt":
        return ["schtasks", "/Create", "/F", "/SC", "DAILY", "/ST", "19:00",
                "/TN", TASK, "/TR", f'"{py}" "{target / "daily.py"}"']
    return None


def launch_agent(target: Path) -> tuple[Path, str]:
    """macOS: the LaunchAgent that runs daily.py at 19:00."""
    py = venv_python(target)
    plist = Path.home() / "Library" / "LaunchAgents" / f"{AGENT}.plist"
    return plist, (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>\n'
        f"<key>Label</key><string>{AGENT}</string>\n"
        f"<key>ProgramArguments</key><array><string>{py}</string>"
        f"<string>{target / 'daily.py'}</string></array>\n"
        f"<key>WorkingDirectory</key><string>{target}</string>\n"
        "<key>StartCalendarInterval</key><dict><key>Hour</key><integer>19</integer>"
        "<key>Minute</key><integer>0</integer></dict>\n"
        "</dict></plist>\n")


def plan(target: Path, backup_to: Path | None, *, allow_synced: bool = False,
         schedule: bool = True,
         source: Path = ROOT, system: str | None = None) -> Plan:
    """Every step, in order; nothing is done until `run`."""
    system = system or platform.system()
    p = Plan(target)
    if sys.version_info < (3, 11):
        raise SystemExit(f"Python 3.11 or later is needed; this is {platform.python_version()}")
    where = synced(target)
    if where and not allow_synced:
        raise SystemExit(f"{target} is inside {where}: choose a folder outside it (--to), and "
                         f"send the backups there instead (--backup-to)")
    if target.resolve() == source.resolve():
        raise SystemExit("install into another folder than the repository itself (--to)")
    p.steps.append(Step(f"copy the program to {target} (the demo data is not copied; data "
                        f"already there is kept)", lambda: copy_program(source, target)))
    p.steps.append(Step(f"create a Python environment in {target / '.venv'}",
                        lambda: _run([sys.executable, "-m", "venv", str(target / ".venv")])))
    p.steps.append(Step("install the requirements (pdfminer.six)",
                        lambda: _run([str(venv_python(target)), "-m", "pip", "install", "-q",
                                      "-r", str(target / "requirements.txt")])))
    name, _ = launcher_text(target)
    p.steps.append(Step(f"put the launcher “{name}” on the Desktop ({desktop()})",
                        lambda: write_launcher(target)))
    if backup_to is not None:
        p.steps.append(Step(f"set the backup folder: {backup_to}",
                            lambda: set_backup_folder(target, backup_to)))
        if not schedule:
            p.steps.append(Step("nothing scheduled for the evening (--no-schedule): each start "
                                "of the desk still makes a backup", note=True))
        elif system == "Windows":
            cmd = schedule_command(target)
            p.steps.append(Step(f"every evening at 19:00, new applications from Ashby (once "
                                f"switched on) then a backup (Task Scheduler, “{TASK}”)",
                                lambda: _run(cmd)))
        elif system == "Darwin":
            plist, text = launch_agent(target)

            def agent() -> None:
                plist.parent.mkdir(parents=True, exist_ok=True)
                plist.write_text(text, encoding="utf-8")
                # macOS 11 and later: bootstrap into the user's session; older: load.
                try:
                    _run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)])
                except subprocess.CalledProcessError:
                    _run(["launchctl", "load", "-w", str(plist)])
            p.steps.append(Step(f"every evening at 19:00, new applications from Ashby (once "
                                f"switched on) then a backup ({plist.name})",
                                agent))
        else:
            p.steps.append(Step(
                "add this line to your crontab (crontab -e) for the evening run: "
                f"0 19 * * * cd '{target}' && '{venv_python(target)}' daily.py",
                note=True))
        p.steps.append(Step("make a first backup now",
                            lambda: _run([str(venv_python(target)), str(target / "backup.py")],
                                         cwd=target)))
    p.steps.append(Step("run the health check", lambda: _run(
        [str(venv_python(target)), str(target / "check.py")], cwd=target, check=False)))

    def record() -> None:
        f = target / "runs" / "desk" / "install.json"
        f.write_text(json.dumps({"installed_at": datetime.now(timezone.utc).isoformat(),
                                 "from": str(source), "to": str(target),
                                 "backup_to": str(backup_to or ""), "port": PORT,
                                 "launcher": str(desktop() / name)}, indent=2) + "\n",
                     encoding="utf-8")
    p.steps.append(Step("note where everything is (runs/desk/install.json)", record))
    return p


def _run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> None:
    print("   $ " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
    subprocess.run(cmd, cwd=cwd, check=check)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--to", default="", help=f"where to install (default {default_target()})")
    ap.add_argument("--backup-to", default="", help="a folder for the evening backups")
    ap.add_argument("--dry-run", action="store_true", help="say every step, do nothing")
    ap.add_argument("--no-schedule", action="store_true",
                    help="no evening task (each start of the desk still makes a backup)")
    ap.add_argument("--allow-synced", action="store_true",
                    help="install inside a synced folder anyway (not advised)")
    a = ap.parse_args()
    target = Path(a.to).expanduser() if a.to else default_target()
    backup_to = Path(a.backup_to).expanduser() if a.backup_to else None
    p = plan(target, backup_to, allow_synced=a.allow_synced, schedule=not a.no_schedule)
    for i, s in enumerate(p.steps, 1):
        print(f"{i}. {s.what}")
        if a.dry_run or s.note or s.do is None:
            continue
        s.do()
    if a.dry_run:
        print("\n(dry run: nothing was done)")
    else:
        print(f"\nInstalled in {target}. Double-click “{launcher_text(target)[0]}” on the "
              f"Desktop to start the desk.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
