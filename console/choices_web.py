"""Choices of your own, and choices switched off.

Yes and No are always there: the desk acts on them (the follow-up, the answer
to send). "Not sure" and "Keep in the pool" can be switched off on House rules.
And a team can add its own choices ("Another role", "Next year"...): each keeps
people aside in a tab of its own, as the pool does, with no date, until
someone decides again. Under the hood such a choice is the pool's meaning
("later") with a label naming the choice, so every rule about the pool holds.

One person deciding only: on a team's desk the four meanings are what the
votes are counted on.
"""

from __future__ import annotations

from typing import Any

import desk
from desk import MEANINGS, Config, DeskError
from pipeline import Event, Standing

#: The label that says which of the team's own choices was made.
TAG = "choice:"
#: The choices that can be switched off. Yes and No cannot.
SWITCHABLE = ("discuss", "later")
#: At most this many of your own, each this long at most: buttons, not a form.
MAX_OWN, MAX_LEN = 6, 40


def shown(cfg: Config) -> list[str]:
    """The four meanings still offered, in their order."""
    return [m for m in MEANINGS if not (cfg.solo and m in SWITCHABLE and m in cfg.choices_off)]


def own(cfg: Config) -> list[str]:
    return list(cfg.extra_choices) if cfg.solo else []


def own_of(s: Standing, viewer: str, cfg: Config) -> str:
    """The team's own choice this application was put in, if it still is."""
    v = desk.votes(s).get(viewer)
    if v is None or v.meaning != "later":
        return ""
    names = own(cfg)
    for tag in reversed(s.tags):
        if tag.startswith(TAG) and tag[len(TAG):] in names:
            return tag[len(TAG):]
    return ""


def tab_id(cfg: Config, name: str) -> str:
    names = own(cfg)
    return f"x{names.index(name)}" if name in names else ""


def record(posting: str, cid: str, viewer: str, label: str, cfg: Config, **kw: Any) -> str:
    """One click: the decision, and the label of an own choice. Returns what to say."""
    name = ""
    if label.startswith("x:"):
        try:
            name = own(cfg)[int(label[2:])]
        except (ValueError, IndexError):
            raise DeskError("that choice is not on the desk any more: look again") from None
    import stages
    with desk.locked():
        pipe = desk._pipelines().get(posting)
        if pipe is None:
            raise DeskError(f"nothing on the desk for {posting!r}")
        before = stages.derive(pipe.standing(cid), cfg) if cid in pipe.candidates else None
        desk.cast(pipe, cid, viewer, "later" if name else label, cfg,
                  until="" if name else kw.get("until", ""),
                  comment=kw.get("comment", ""), reason=kw.get("reason", ""))
        # A new decision ends the old own choice; an own choice is labelled.
        for tag in pipe.standing(cid).tags:
            if tag.startswith(TAG) and tag != TAG + name:
                pipe.add(Event(candidate_id=cid, kind="untagged", tag=tag, by=viewer))
        if name and TAG + name not in pipe.standing(cid).tags:
            pipe.add(Event(candidate_id=cid, kind="tagged", tag=TAG + name, by=viewer))
        # Once people were written to, the steps decide where they stand: a new
        # "no" stops them there, a new "keep aside" puts them in the pool.
        meaning = "later" if name else cfg.meaning(label)
        if cfg.solo and before is not None and before.id in stages.TALKING:
            ok = stages.allowed(stages.derive(pipe.standing(cid), cfg), cfg)
            step = {"pass": "stop", "later": "talk_later"}.get(meaning, "")
            if step in ok:
                try:
                    stages.advance(pipe, cid, step, viewer, cfg)
                except DeskError:
                    pass  # a pool that needs a date: set on the row, with its date
        desk._save(pipe)
    return name or cfg.label(cfg.meaning(label))


def clean(lines: list[str]) -> list[str]:
    """The names typed on House rules: one line each, no doubles, not too many."""
    out: list[str] = []
    for x in lines:
        x = " ".join(x.split())[:MAX_LEN]
        if x and x.lower() not in {o.lower() for o in out}:
            out.append(x)
    return out[:MAX_OWN]
