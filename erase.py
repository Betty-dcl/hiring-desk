#!/usr/bin/env python3
"""Erase a person: everything the desk holds about them, for every role.

    python erase.py --due                                  # past the retention period
    python erase.py --person sam_lee --by Recruiter --reason asked --yes

A candidate may ask for their data to be erased (GDPR art. 17), and a desk
must not keep applications for ever (art. 5(1)(e)). Both go through here, and
through the person's page and the Retention page on the desk.

What is removed, wherever it is:

* every event of every application they made, in every role's log;
* their card in the registry (name, email, links, documents);
* their files: CV, "Why us" answer, anything attached;
* what was read from them (facts, screenings, "wants");
* the Ashby link to them, and Ashby is told not to bring them back: the
  Ashby application ids are kept as one-way hashes only;
* Outlook hints and AI-draft records about them.

Then every text file of the desk's data is searched for their name, their
email and their ids, and anything still mentioning them is reported, never
left unsaid. One line is kept in `runs/desk/erasures.jsonl`: when, by whom,
why, how many applications and files. No name, no email, no id.

Backups made before the erasure still hold the person until they roll over
(the 30 newest are kept): the page says so.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import desk
import stages

#: Why someone is erased, as the log records it.
REASONS = {"asked": "they asked for it", "retention": "the retention period is over",
           "mistake": "added by mistake", "other": "another reason"}
#: Retention, in months, when desk.toml [retention] says nothing.
MONTHS, POOL_MONTHS = 12, 24


class EraseError(desk.DeskError):
    pass


def _runs() -> Path:
    return desk.ROOT / "runs"


def h(value: str) -> str:
    """A one-way hash: enough to recognise an Ashby application, not to read it."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass
class Report:
    applications: int = 0
    events: int = 0
    files: int = 0
    roles: list[str] = field(default_factory=list)
    #: Text files that still mention them after the erasure (should be empty).
    remaining: list[str] = field(default_factory=list)


def erase(person_id: str, by: str, reason: str, cfg: desk.Config,
          now: datetime | None = None) -> Report:
    """Remove everything about one person. Irreversible: the page asks first."""
    now = now or datetime.now(timezone.utc)
    if reason not in REASONS:
        raise EraseError(f"say why: {', '.join(REASONS)}")
    by = cfg.voter(by)
    r = Report()
    with desk.locked():
        reg = desk.load_registry(desk._registry_path())
        person = reg.people.get(person_id)
        if person is None:
            raise EraseError("nobody with that id on the desk (already erased?)")
        cids = {a.candidate_id for a in person.applications}
        needles = {person.name, person.email, person_id, *cids} - {""}

        # 1. Every event of every application, in every role.
        for posting, pipe in desk._pipelines().items():
            before = len(pipe.events)
            pipe.events = [e for e in pipe.events if e.candidate_id not in cids]
            if len(pipe.events) != before:
                r.events += before - len(pipe.events)
                r.roles.append(posting)
                desk._save(pipe)
        r.applications = len(person.applications)

        # 2. Their files, then their card.
        files = desk._files_dir()
        for d in person.documents:
            f = files / d.stored
            if f.exists():
                f.unlink()
                r.files += 1
        for folder in {(files / d.stored).parent for d in person.documents}:
            if folder.exists() and folder != files and not any(folder.iterdir()):
                folder.rmdir()
        del reg.people[person_id]
        reg.possible = [x for x in reg.possible if person_id not in (x.get("a"), x.get("b"))]
        reg.merges = [x for x in reg.merges if person_id not in (x.get("kept"), x.get("merged"))]
        desk.save_registry(reg, desk._registry_path())

        # 3. What was read from them.
        runs = _runs()
        for cid in cids:
            for pattern in (f"facts/facts_{cid}.json", f"facts_with_letter/facts_{cid}.json",
                            f"wants/wants_{cid}.json", f"screenings/{cid}_*.json",
                            f"reviews/{cid}_*", f"trial/outcomes/{cid}_*",
                            f"trial/cards/{cid}_*"):
                for f in runs.glob(pattern):
                    if f.is_file():
                        f.unlink()
                        r.files += 1

        # 4. Ashby: forget the link, and do not bring them back.
        a = runs / "desk" / "ashby.json"
        if a.exists():
            state = json.loads(a.read_text(encoding="utf-8"))
            apps = state.get("applications", {})
            gone = [aid for aid, m in apps.items() if m.get("person_id") == person_id
                    or m.get("candidate_id") in cids]
            for aid in gone:
                del apps[aid]
            state.setdefault("erased", [])
            state["erased"] += [h(aid) for aid in gone if h(aid) not in state["erased"]]
            a.write_text(json.dumps(state, indent=2), encoding="utf-8")

        # 5. Outlook hints, AI-draft records.
        o = runs / "desk" / "outlook-replies.json"
        if o.exists():
            d = json.loads(o.read_text(encoding="utf-8"))
            d["replies"] = {k: v for k, v in d.get("replies", {}).items()
                            if k.split("/", 1)[-1] not in cids}
            o.write_text(json.dumps(d, indent=2), encoding="utf-8")
        drafts = runs / "desk" / "ai_drafts.jsonl"
        if drafts.exists():
            kept = [line for line in drafts.read_text(encoding="utf-8").splitlines()
                    if not any(n in line for n in needles)]
            drafts.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")

        # 6. The one trace: what was done, not to whom.
        log = runs / "desk" / "erasures.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": now.isoformat(), "by": by, "reason": reason,
                                 "applications": r.applications, "roles": sorted(r.roles),
                                 "events": r.events, "files": r.files}) + "\n")

        r.remaining = mentions(needles)
    return r


def mentions(needles: set[str]) -> list[str]:
    """Text files of the desk's data that still name the person."""
    out = []
    lowered = {n.lower() for n in needles if len(n) >= 3}
    for f in _runs().rglob("*"):
        if not f.is_file() or f.name == "erasures.jsonl" or f.stat().st_size > 5_000_000:
            continue
        if f.suffix.lower() not in (".json", ".jsonl", ".md", ".txt", ".toml", ".csv", ".eml",
                                    ".html"):
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        if any(n in text for n in lowered):
            out.append(f.relative_to(desk.ROOT).as_posix())
    return out


# --------------------------------------------------------------------------
# Retention: who is past it
# --------------------------------------------------------------------------

def retention() -> tuple[int, int]:
    """(months, months for the pool), from desk.toml [retention]."""
    f = desk.ROOT / "desk.toml"
    try:
        raw = tomllib.loads(f.read_text(encoding="utf-8")).get("retention", {})
    except (OSError, tomllib.TOMLDecodeError):
        raw = {}
    return int(raw.get("months", MONTHS)), int(raw.get("pool_months", POOL_MONTHS))


@dataclass
class Due:
    person: desk.Person
    last: datetime
    where: str


def due(cfg: desk.Config, now: datetime | None = None) -> list[Due]:
    """Everyone past the retention period, oldest first. Never someone in process.

    The clock is the last thing that happened to any of their applications. The
    pool keeps people longer (they agreed to be kept); someone contacted, in an
    interview or ready to contact is never due.
    """
    now = now or datetime.now(timezone.utc)
    months, pool = retention()
    reg = desk.load_registry(desk._registry_path())
    pipes = desk._pipelines()
    out = []
    for person in reg.people.values():
        last, live, kept = None, False, False
        for a in person.applications:
            pipe = pipes.get(a.posting_id)
            if pipe is None or a.candidate_id not in pipe.candidates:
                continue
            s = pipe.standing(a.candidate_id)
            st = stages.derive(s, cfg, now)
            if st.id in stages.TALKING or st.id in ("to_contact", "to_answer_no"):
                live = True
            kept = kept or st.id == "talk_later"
            for e in s.events:
                t = e.when
                last = t if last is None or t > last else last
        if live or last is None:
            continue
        limit = timedelta(days=round(30.44 * (pool if kept else months)))
        if now - last >= limit:
            out.append(Due(person, last, "in the pool" if kept else "decided or closed"))
    return sorted(out, key=lambda d: d.last)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--due", action="store_true", help="list who is past the retention period")
    ap.add_argument("--person", default="", help="the person's id (on their page's address)")
    ap.add_argument("--by", default="")
    ap.add_argument("--reason", default="asked", choices=sorted(REASONS))
    ap.add_argument("--yes", action="store_true", help="confirm: this cannot be undone")
    a = ap.parse_args()
    cfg = desk.load_config()
    if a.due:
        months, pool = retention()
        rows = due(cfg)
        print(f"{len(rows)} past the retention period ({months} months; pool {pool})")
        for d in rows:
            print(f"  {d.person.person_id:30} last activity {d.last.date()}  {d.where}")
        return 0
    if not a.person:
        ap.error("--person or --due")
    if not a.yes:
        print("erasing cannot be undone: add --yes", file=sys.stderr)
        return 2
    try:
        r = erase(a.person, a.by or cfg.voters[0], a.reason, cfg)
    except desk.DeskError as err:
        print(f"erase: {err}", file=sys.stderr)
        return 1
    print(f"erased: {r.applications} application(s) in {len(r.roles)} role(s), "
          f"{r.events} events, {r.files} files")
    for f in r.remaining:
        print(f"  still mentioned in {f}: look at it")
    return 0 if not r.remaining else 1


if __name__ == "__main__":
    sys.exit(main())
