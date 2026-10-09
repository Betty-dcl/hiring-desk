# CLAUDE.md: operating manual for the hiring desk

You are helping the person who runs hiring use this desk: install it, open it,
keep its data safe, connect Ashby, change its settings, and fix what breaks.
Read this file first; it is written for you. Everything in this repository is
in English, and stays in English.

## What this is

A hiring desk for one person who reads every application (the settings call
them `Recruiter`). It runs on their own computer, as a small Python program
that serves pages to the browser at `http://127.0.0.1:8765`. It calls no AI
model, sends no mail, and costs nothing. Each application gets a decision
(Yes, Not sure, Keep in the pool, Not for us), then steps recorded by a person
(Contacted, Replied OK, each interview, Trial day), and an answer.

Pages: **At the door** (sort and follow: To sort, Interested, To think about,
Not for us, All), **Progress** (how far each yes went), **Pool** (people kept
for later, across roles, with ideas for other roles), **Calendar**,
**Overview** (counts by role, for information), **AI guess** (how the
speculative word count works), **House rules** (every setting; also *Guided
questions* and *Check*).

## Rules you never break

1. **Never send anything to a candidate.** The desk prepares drafts; a person
   sends them from their own mail. Do not build or enable automatic sending.
2. **Never write to Ashby without an explicit yes** from the person, in this
   conversation, for that action (`ashby.py push --send`). Reading Ashby is
   fine once they gave you the key.
3. **Never call a model or a paid API without an explicit yes**: that includes
   `python -m intake extract`, `triage.py score` and `NBH_BACKEND=api`. They
   send candidates' documents to a provider.
4. **Candidate data never leaves the computer except to the backup folder the
   person chose.** Never commit `runs/`, `local/`, `deploy/.env`, a CV, or an
   API key. Never paste a candidate's documents into anything outside this
   machine.
5. **Ask before anything that changes the system**: running `install.py`,
   scheduling tasks, `backup.py --restore`, deleting files, moving the data.
   Show the plan first (`install.py --dry-run`).
6. **The person decides.** The desk never rejects, ranks for them, or moves
   someone on its own; keep it that way in any change you make.
7. **Real names stay out of the code.** Names of people at the company go in
   the installed `desk.toml` only, never in a file of this repository.

## Where everything is

| What | Where |
|---|---|
| Applications: every decision, step, note, with who and when | `runs/pipeline/<role>.json` (append-only logs) |
| People: name, email, links, their documents | `runs/desk/people.json` |
| CVs and "Why us" answers | `runs/desk/files/` |
| What House rules saved | `runs/desk/settings.json` |
| The settings file (defaults, mail texts, Ashby, backup) | `desk.toml` |
| The roles (postings) and what the AI guess reads in them | `mandates/generated/role_<id>.provenance.json` |
| Ashby's sync state | `runs/desk/ashby.json` |
| Last backup, install record | `runs/desk/last_backup.json`, `runs/desk/install.json` |

Nothing is ever edited in place: every click adds a dated event to a log.
Closing the browser loses nothing. The one real risk is the computer itself
(lost, broken) and sync tools: hence the backups.

**This repository holds invented demo data in `runs/`.** A real installation
is a separate copy with empty data, made by `install.py`. Run the tests in
the repository, never in an installed copy (it has no demo data to test on).

## Install it on a computer (once)

Needs Python 3.11 or later (`python --version`; on Windows, from python.org,
ticking "Add python.exe to PATH"). **On a Mac**, the `python3` that comes with
the system is older: install Python from python.org (or `brew install
python@3.13`), then use `python3` in the commands below. On a Mac the launcher
is `Hiring desk.command` on the Desktop (if macOS refuses to open it the first
time: right-click, Open), and the evening run is a LaunchAgent
(`~/Library/LaunchAgents/com.hiringdesk.evening.plist`).

1. Pick where backups go: a folder a sync tool copies off the computer. With
   Google Drive for desktop, typically `G:\My Drive\Hiring desk backups` on
   Windows, or `~/Library/CloudStorage/GoogleDrive-<email>/My Drive/Hiring
   desk backups` on macOS. Ask the person; create the folder if needed.
2. Show the plan, then do it after a yes:

       python install.py --dry-run --backup-to "<backup folder>"
       python install.py --backup-to "<backup folder>"

   It copies the program to `~/HiringDesk` (`--to` to change), never inside
   OneDrive / Google Drive / Dropbox / iCloud, with empty data; creates
   `.venv`; installs the requirements; puts a **Hiring desk** launcher on the
   Desktop; writes the backup folder into `desk.toml`; schedules `daily.py`
   every evening at 19:00 (new applications from Ashby once that is switched
   on, then a backup); makes a first backup; runs the check.
3. In the installed copy, set the person's name: `desk.toml`, `[team] voters =
   ["<their first name>"]`, and `[mail] sender` / `company`. Or on House rules.
4. Double-click the launcher. It runs `daily.py` (Ashby when switched on, a
   backup), starts the desk and opens the browser. Closing its window stops
   the desk.

From then on, work in the installed folder (`~/HiringDesk`).

## Keep the data safe

    python backup.py                       # a backup now, to the folder in desk.toml
    python backup.py --list                # the backups, newest first
    python backup.py --restore <zip> --yes # put one back (the current data is moved aside first)
    python check.py                        # the health check; also House rules > Check

What `check.py` says, and what to do:

- **Data folder is inside a synced folder**: move the installed folder out of
  it (`install.py --to`), point the backups at the synced folder instead.
- **Every data file can be read: FAIL**: a sync tool made files "online only".
  Start the sync tool, or mark the folder "Always keep on this device", then
  check again.
- **Every CV and answer is on disk: FAIL**: restore the latest backup that has
  them (`backup.py --list`), or attach the file again on the person's page.
- **Backups: WARN**: no backup yet, or the last is more than two days old: run
  `python backup.py`, and check the scheduled task exists (Windows: Task
  Scheduler, "Hiring desk evening"; macOS: `launchctl list | grep hiringdesk`).

## Erase a person, and the retention period

A candidate may ask to be erased (GDPR art. 17), and applications are not
kept for ever. On a person's page, **Erase this person** (a reason, a box to
tick); on **House rules > Retention**, everyone past the retention period
(12 months after the last activity, 24 in the pool, never someone in
process; `desk.toml [retention]`). Or:

    python erase.py --due
    python erase.py --person <id> --by <name> --reason asked --yes

It removes every application, step, note and file of the person in every
role, what was read from them, and their Ashby link (Ashby will not bring
them back). Only a line without their name is kept in
`runs/desk/erasures.jsonl`. Backups made before still hold them until they
roll over. **Never erase without the person's explicit yes, for that person.**
If `erase.py` reports a file that still mentions them, look at it with the
person before deleting anything by hand.

## Connect Ashby (optional, never run live yet)

Causa Prima's roles are on Ashby (`jobs.ashbyhq.com/causaprima`). The
connector `ashby.py` was built from Ashby's published API reference and has
never run against a real account: go step by step, with the person.

1. **A key.** An Ashby admin creates an API key in Ashby's admin settings,
   with the permission `candidatesRead` only (add `candidatesWrite` only if
   they later want notes written back). API access comes with Ashby's paid
   plans; if the admin cannot create a key, ask Ashby.
2. **Where the key goes.** Never in the program's folder or the repository.
   Put it in the file `.hiringdesk/ashby_key` in the person's home folder
   (the evening task reads it there; on a Mac it cannot see `~/.zshrc`):
   Windows, in PowerShell: `New-Item -ItemType Directory -Force
   "$HOME\.hiringdesk"; Set-Content "$HOME\.hiringdesk\ashby_key" "<key>"`;
   macOS: `mkdir -p ~/.hiringdesk && printf %s '<key>' > ~/.hiringdesk/ashby_key
   && chmod 600 ~/.hiringdesk/ashby_key`. The variable `ASHBY_API_KEY`, when
   set, wins over the file.
3. **The three steps**, in the installed folder:

       python ashby.py check                      # 1. the key works (reads one application)
       python ashby.py pull --dry-run             # 2. what would come in; writes nothing
       python ashby.py pull --job "Founders' Associate"   # 3. one role for real
       python ashby.py verify                     #    the last people brought in, to compare

   `verify` prints each person with their CV's name and size and their Ashby
   address: open three or four in Ashby and compare. Only then run
   `python ashby.py pull` for every role.
4. **Let it come in by itself** (ask first): in the installed `desk.toml`,
   `[ashby] auto_pull = true`. From then on `daily.py` pulls at each start of
   the desk and every evening at 19:00, then makes the backup; it only reads
   Ashby. Nobody needs to run anything. If Ashby or the network is down, the
   desk starts anyway and the error shows on House rules > Check (and
   `check.py`), which also warns when no pull happened for two days. Left
   off, run `python ashby.py pull` at least weekly: Ashby's sync tokens
   expire after 14 days (a full sync then restarts, and nobody is added
   twice).
5. **Roles the desk does not know** are reported and left in Ashby. Either
   map an Ashby title to a role under `[ashby.jobs]` in `desk.toml`, or add
   the role (below).

The desk keeps its own copy of each CV (in `runs/desk/files/`): it needs the
text for the AI guess and works without a connection. Ashby keeps the
original.

## Add a role

A role is a posting the desk knows: `mandates/generated/role_<id>.provenance.json`
(the title Ashby uses, and the criteria the AI guess counts words against).

1. Save the posting's text as `intake/postings/<id-with-dashes>.md`, with the
   same header as the others (title, then the source line).
2. Ask before this step, it calls a model:
   `python -m intake extract intake/postings/<file>.md --out mandates/generated`.
   It quotes every criterion from the posting and drops any it cannot find.
3. Restart the desk. The role appears in the filters, and Ashby's pull can
   place applications in it.

## Change the settings

Everything a person would change is on **House rules**: the names of the
choices (and switching "Not sure" or the pool off, or adding choices of their
own), the number of interviews, the names of the steps, every mail text (and
switching a mail off), the Outlook option. It is saved in
`runs/desk/settings.json` and applies at once. `desk.toml` holds the defaults
and what is not on the page (access, Ashby, backup, mail mode).

## Options that are off

- **Outlook** (`outlook.py`, `docs/OUTLOOK.md`): marks "they wrote back" from
  a Microsoft 365 mailbox. Causa Prima's mail is on Google Workspace, so this
  does not apply to them as built; a Gmail version is not written.
- **A server** (`deploy/README.md`): the desk on a small server behind the
  company's Google sign-in, for access from anywhere. Never deployed yet; the
  person decides whether one day they want it.
- **AI-drafted mails** (`[mail] ai_drafts`): off; sends documents to a model.

## When something goes wrong

- *The page does not open*: the launcher window was closed, or the desk is
  not running. Double-click the launcher. If the port is taken:
  `python desk.py serve --port 8766` and open that port.
- *"'X' does not vote on this desk"*: an old browser tab from before a name
  change. Close the tab, open `http://127.0.0.1:8765` again.
- *An error page or a traceback*: run `python check.py`, read the last lines
  of the launcher window, fix, restart.
- *Something looks lost*: the desk deletes nothing on its own (only an
  erasure a person asked for does, and it is logged in
  `runs/desk/erasures.jsonl`); look under **All**, then in the latest backup
  (`backup.py --list`).

## For a developer

    python -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
    .venv/bin/python -m pytest -q        # in this repository; no model is called

README.md describes the product, DEPLOY.md and deploy/README.md the server,
docs/SECURITY.md the threat model, docs/DETAILS.md the measurements behind the
optional model screener. Keep the code's style: short modules under
`console/` for pages, the rules in `stages.py`, every change with its tests.
