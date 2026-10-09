#!/usr/bin/env python3
"""What runs by itself: at each start of the desk, and every evening at 19:00.

    python daily.py

1. New applications from Ashby, only when `[ashby] auto_pull = true` in
   desk.toml and a key is set. It reads Ashby, never writes to it.
2. A backup, only when a backup folder is set (`[backup] to`).

In that order, so the evening backup holds the day's applications. Neither
part can stop the other, nor the desk from starting: an Ashby error is kept
in runs/desk/ashby.json and shown by check.py (and House rules > Check).
The launcher and the evening task that install.py sets up both run this.
"""

from __future__ import annotations

import sys
from pathlib import Path

import ashby
import backup


def run(root: Path | None = None) -> list[str]:
    lines = ashby.auto(ashby.load_settings(root / "desk.toml" if root else None))
    conf = backup.settings(root or backup.ROOT)
    if conf.get("to"):
        try:
            made = backup.make(Path(conf["to"]).expanduser(), root=root or backup.ROOT,
                               keep=int(conf.get("keep", 30)))
            lines.append(f"backup written and checked: {made}")
        except (backup.BackupError, OSError) as err:
            lines.append(f"backup: {err}")
    return lines


def main() -> int:
    for line in run():
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
