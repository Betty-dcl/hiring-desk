#!/usr/bin/env python3
"""Is everything in order? A health check of the desk's data, read only.

    python check.py            # one line per check: OK, WARN or FAIL
    (and the Check page on the desk, under House rules)

It reads, it never writes. What it looks at:

* the settings load (desk.toml, and what House rules saved);
* the data folder exists, can be written to, and is not inside a synced folder
  (OneDrive, Google Drive, Dropbox, iCloud), where a file left "online only"
  can stop the desk reading its own data;
* every application log reads, and every application has its person;
* every CV and "Why us" answer listed on a person is on disk, and readable;
* when the last backup was made;
* whether Ashby is connected, when it last brought applications in, and
  whether that happens by itself (and did, these last days).

Exit code 0 when nothing FAILs, 1 otherwise.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import desk

#: Folder names that mean "a sync tool watches this".
SYNCED = ("onedrive", "google drive", "googledrive", "my drive", "dropbox", "icloud",
          "icloud drive", "mobile documents")
#: A backup older than this is worth a warning; so is an automatic Ashby pull.
BACKUP_DAYS = 2
#: Ashby's sync point expires after this many days without a pull.
ASHBY_TOKEN_DAYS = 14


@dataclass
class Result:
    level: str  # "OK", "WARN" or "FAIL"
    what: str
    detail: str = ""


def synced(path: Path) -> str:
    """The sync tool a path sits under, or ""."""
    parts = [p.lower() for p in path.resolve().parts]
    for p in parts:
        for s in SYNCED:
            if p == s or p.startswith(s + " -") or p.startswith(s + "-"):
                return s
    return ""


def run(now: datetime | None = None) -> list[Result]:
    now = now or datetime.now(timezone.utc)
    out: list[Result] = []
    root = desk.ROOT
    runs = root / "runs"

    # Settings.
    try:
        cfg = desk.load_config()
        out.append(Result("OK", "Settings load",
                          f"{len(cfg.voters)} deciding, {cfg.interviews} interviews"))
    except (desk.DeskError, ValueError, OSError) as err:
        out.append(Result("FAIL", "Settings load", str(err)))
        cfg = None

    # The data folder.
    if not runs.exists():
        out.append(Result("FAIL", "Data folder", f"{runs} does not exist"))
        return out
    probe = runs / ".write-check.tmp"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        out.append(Result("OK", "Data folder can be written", str(runs)))
    except OSError as err:
        out.append(Result("FAIL", "Data folder can be written", f"{runs}: {err}"))
    where = synced(runs)
    if where:
        out.append(Result("WARN", "Data folder is inside a synced folder",
                          f"{where}: a file left online only can stop the desk reading its "
                          f"own data. Move the desk out of it (install.py), and let the "
                          f"backups go to the synced folder instead."))
    unreadable = []
    for f in runs.rglob("*"):
        if f.is_file():
            try:
                with open(f, "rb") as h:
                    h.read(1)
            except OSError:
                unreadable.append(f.relative_to(root).as_posix())
    if unreadable:
        out.append(Result("FAIL", "Every data file can be read",
                          f"{len(unreadable)} cannot, e.g. {', '.join(unreadable[:3])}"))
    else:
        out.append(Result("OK", "Every data file can be read"))

    # Applications and people.
    try:
        pipes = desk._pipelines()
        n = sum(len(p.candidates) for p in pipes.values())
        out.append(Result("OK", "Application logs read", f"{n} applications, {len(pipes)} roles"))
    except Exception as err:  # noqa: BLE001 -- a broken log must be reported, whatever it raises
        out.append(Result("FAIL", "Application logs read", str(err)))
        pipes = {}
    try:
        reg = desk.load_registry(desk._registry_path())
    except Exception as err:  # noqa: BLE001
        out.append(Result("FAIL", "People read", str(err)))
        reg = None
    if reg is not None:
        orphans = [f"{pid}/{cid}" for pid, p in pipes.items() for cid in p.candidates
                   if reg.person_of(cid) is None]
        out.append(Result("WARN" if orphans else "OK", "Every application has its person",
                          f"{len(orphans)} without, e.g. {', '.join(orphans[:3])}"
                          if orphans else f"{len(reg.people)} people"))
        missing, empty, total = [], [], 0
        files = desk._files_dir()
        for person in reg.people.values():
            for d in person.documents:
                total += 1
                f = files / d.stored
                if not f.exists():
                    missing.append(f"{person.name}: {d.original}")
                elif f.stat().st_size == 0:
                    empty.append(f"{person.name}: {d.original}")
        if missing or empty:
            out.append(Result("FAIL", "Every CV and answer is on disk",
                              f"{len(missing)} missing, {len(empty)} empty, e.g. "
                              f"{', '.join((missing + empty)[:3])}"))
        else:
            out.append(Result("OK", "Every CV and answer is on disk", f"{total} files"))

    # Backups.
    import backup
    b = backup.last(root)
    if not b:
        out.append(Result("WARN", "Backups", "no backup made yet: python backup.py --to <folder>"))
    else:
        try:
            age = now - datetime.fromisoformat(b["at"])
        except (KeyError, ValueError):
            age = timedelta(days=999)
        level = "WARN" if age > timedelta(days=BACKUP_DAYS) else "OK"
        out.append(Result(level, "Backups", f"last one {age.days} day(s) ago, "
                                            f"{b.get('files', '?')} files, {b.get('path', '')}"))

    # Retention.
    if cfg is not None:
        import erase
        months, pool = erase.retention()
        late = erase.due(cfg, now)
        out.append(Result("WARN" if late else "OK", "Retention",
                          f"{len(late)} past {months} months ({pool} in the pool): House rules > "
                          f"Retention" if late else f"nobody past {months} months "
                                                     f"({pool} in the pool)"))

    # Ashby.
    out.append(_ashby(root, now))
    return out


def _ashby(root: Path, now: datetime) -> Result:
    """Connected or not; when it last brought applications in; whether that runs by itself."""
    import ashby
    try:
        auto = ashby.load_settings(root / "desk.toml").auto_pull
    except (OSError, ValueError):
        auto = False
    key = bool(ashby.key())
    f = root / "runs" / "desk" / "ashby.json"
    if not f.exists():
        if auto and not key:
            return Result("WARN", "Ashby", "auto_pull is on but no key is set: see CLAUDE.md, "
                                           "Connect Ashby")
        return Result("WARN" if key or auto else "OK", "Ashby",
                      "key set, nothing brought in yet: python ashby.py check" if key or auto
                      else "not connected (applications come in by hand or CSV)")
    try:
        state = json.loads(f.read_text(encoding="utf-8"))
    except (ValueError, OSError) as err:
        return Result("FAIL", "Ashby", f"ashby.json does not read: {err}")
    apps = state.get("applications", {})
    last = state.get("last_pull") or {}
    when = last.get("at") or max((v.get("at", "") for v in apps.values()), default="")
    try:
        age = now - datetime.fromisoformat(when) if when else None
    except ValueError:
        age = None
    said = (f"{len(apps)} applications brought in"
            + (f", last pull {when[:10]} ({last.get('added', 0)} new)" if last.get("at")
               else f", the last on {when[:10]}" if when else "")
            + ("; by itself at each start and every evening" if auto else "; by hand"))
    if last.get("error"):
        return Result("WARN", "Ashby", f"the last pull did not finish ({last['error']}): "
                                       f"python ashby.py pull, to see why. {said}")
    if not key:
        return Result("WARN" if auto else "OK", "Ashby",
                      said + ("; no key found now" if auto else "; ASHBY_API_KEY not set now"))
    if auto and (age is None or age > timedelta(days=BACKUP_DAYS)):
        return Result("WARN", "Ashby", f"{said}, but not in the last {BACKUP_DAYS} days: is "
                                       f"the evening task there? (CLAUDE.md, Keep the data safe)")
    if not auto and age is not None and age > timedelta(days=ASHBY_TOKEN_DAYS):
        return Result("WARN", "Ashby", f"{said}; more than {ASHBY_TOKEN_DAYS} days ago, Ashby "
                                       f"has forgotten where it was (a full pull, nobody twice)")
    return Result("OK", "Ashby", said)


def main() -> int:
    results = run()
    width = max(len(r.what) for r in results)
    for r in results:
        print(f"{r.level:4}  {r.what:{width}}  {r.detail}".rstrip())
    return 1 if any(r.level == "FAIL" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
