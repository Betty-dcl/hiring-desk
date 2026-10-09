#!/usr/bin/env python3
"""Back up the desk's data, and put a backup back. Standard library only.

    python backup.py --to "G:/My Drive/Hiring desk backups"     # one dated zip
    python backup.py --list --to "G:/My Drive/Hiring desk backups"
    python backup.py --restore "G:/.../hiring-desk_2026-10-06_1900.zip" --yes

Everything the desk knows lives in `runs/` (applications, decisions, steps,
notes, CVs, settings) and in `desk.toml`. A backup is one zip of both, named
with its date, checked after it is written, in a folder of your choice: a
synced company drive is the natural place, so the copy leaves the laptop.
The oldest are removed beyond `--keep` (30 by default).

A restore never overwrites: the current `runs/` is first moved aside to
`runs-before-restore-<date>/`, then the zip is unpacked. It asks for `--yes`.

The destination can also be set once in desk.toml, [backup] to = "...".
`install.py` schedules this every evening.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tomllib
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PREFIX = "hiring-desk_"
#: Never in a backup: a lock, a half-written file, a cache.
SKIP_NAMES = {".lock", "desk.locked"}
SKIP_SUFFIXES = (".tmp", ".pyc")


class BackupError(RuntimeError):
    pass


def settings(root: Path = ROOT) -> dict:
    f = root / "desk.toml"
    if not f.exists():
        return {}
    return tomllib.loads(f.read_text(encoding="utf-8")).get("backup", {})


def _files(root: Path) -> list[Path]:
    out = []
    runs = root / "runs"
    if runs.exists():
        for p in sorted(runs.rglob("*")):
            if p.is_file() and p.name not in SKIP_NAMES and not p.name.endswith(SKIP_SUFFIXES):
                out.append(p)
    if (root / "desk.toml").exists():
        out.append(root / "desk.toml")
    return out


def make(dest: Path, root: Path = ROOT, keep: int = 30,
         now: datetime | None = None) -> Path:
    """Write one zip of runs/ and desk.toml into `dest`, check it, prune the oldest."""
    now = now or datetime.now(timezone.utc)
    dest.mkdir(parents=True, exist_ok=True)
    name = dest / f"{PREFIX}{now.astimezone():%Y-%m-%d_%H%M%S}.zip"
    files = _files(root)
    if not files:
        raise BackupError(f"nothing to back up under {root}")
    tmp = name.with_suffix(".zip.tmp")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for f in files:
                try:
                    z.write(f, f.relative_to(root).as_posix())
                except OSError as err:
                    # A file a sync tool has left "online only" cannot be read.
                    raise BackupError(f"could not read {f.relative_to(root)}: {err}") from None
        with zipfile.ZipFile(tmp) as z:
            bad = z.testzip()
            if bad:
                raise BackupError(f"the backup did not read back correctly ({bad})")
        tmp.replace(name)
    finally:
        tmp.unlink(missing_ok=True)
    for old in listing(dest)[keep:]:
        old.unlink(missing_ok=True)
    record = root / "runs" / "desk" / "last_backup.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps({"at": now.isoformat(), "path": str(name),
                                  "files": len(files)}, indent=2) + "\n", encoding="utf-8")
    return name


def listing(dest: Path) -> list[Path]:
    """The backups in `dest`, newest first."""
    if not dest.exists():
        return []
    return sorted(dest.glob(f"{PREFIX}*.zip"), reverse=True)


def last(root: Path = ROOT) -> dict:
    f = root / "runs" / "desk" / "last_backup.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except (ValueError, OSError):
        return {}


def restore(zip_path: Path, root: Path = ROOT, now: datetime | None = None) -> Path:
    """Unpack a backup; the current runs/ is moved aside first. Returns where it went."""
    now = now or datetime.now(timezone.utc)
    if not zipfile.is_zipfile(zip_path):
        raise BackupError(f"{zip_path} is not a backup zip")
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        if any(n.startswith("/") or ".." in Path(n).parts for n in names):
            raise BackupError("the zip holds a path outside the desk: refused")
        if not any(n.startswith("runs/") for n in names):
            raise BackupError("the zip holds no runs/ folder: not a desk backup")
        aside = root / f"runs-before-restore-{now.astimezone():%Y-%m-%d_%H%M%S}"
        if (root / "runs").exists():
            shutil.move(str(root / "runs"), str(aside))
        aside.mkdir(parents=True, exist_ok=True)
        # The zip's desk.toml replaces the current one: keep the current one aside too.
        if (root / "desk.toml").exists():
            shutil.copy2(root / "desk.toml", aside / "desk.toml")
        z.extractall(root)
    return aside


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--to", default="", help="the folder backups go to (or desk.toml [backup] to)")
    ap.add_argument("--keep", type=int, default=0, help="how many to keep (default 30)")
    ap.add_argument("--list", action="store_true", help="list the backups, newest first")
    ap.add_argument("--restore", default="", help="a backup zip to put back")
    ap.add_argument("--yes", action="store_true", help="confirm a restore")
    ap.add_argument("--if-configured", action="store_true",
                    help="do nothing, quietly, when no destination is set (the launcher)")
    a = ap.parse_args()
    conf = settings()
    dest = Path(a.to or conf.get("to", "")).expanduser() if (a.to or conf.get("to")) else None
    keep = a.keep or int(conf.get("keep", 30))
    try:
        if a.restore:
            if not a.yes:
                print("restore moves the current data aside and unpacks the backup: "
                      "add --yes to do it", file=sys.stderr)
                return 2
            aside = restore(Path(a.restore).expanduser())
            print(f"restored {a.restore}; the previous data is in {aside}")
            return 0
        if dest is None:
            if a.if_configured:
                return 0
            print("say where backups go: --to <folder>, or desk.toml [backup] to = \"...\"",
                  file=sys.stderr)
            return 2
        if a.list:
            for z in listing(dest):
                print(z.name)
            return 0
        made = make(dest, keep=keep)
        print(f"backup written and checked: {made}")
        return 0
    except BackupError as err:
        print(f"backup: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
