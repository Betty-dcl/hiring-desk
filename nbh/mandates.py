"""Mandates: what each agent knows, as facts.

The load-bearing idea of this project, borrowed from Orbio's engineering
notes (`recherche-orbio.md` §3.4): an agent that says the wrong thing is
almost never an agent with the wrong personality. It is an agent that was
asked a question it had no fact for, and a model with no fact still has to
produce a completion, so it produces the most plausible-sounding one.

The fix is not a longer list of things it may not say. A ban names one
thing and leaves everything else open, and the model fills that space with
whatever sounds right. The fix is to hand it the fact.

So there are no prohibitions in this file. `private` is not "never mention
this" -- it is a statement about what the principal did and did not
authorise, which is itself a fact the agent can act on, and can say out loud
when it declines. Declining on the record is a legal move in this protocol
(`Act.WITHHOLD`); lying is not, and the judge scores for it.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nbh.protocol import Criterion


@dataclass(frozen=True)
class Fact:
    """One thing the candidate's agent is entitled to claim, and its backing.

    `detail` exists so a disclosure can be specific. An agent with only
    headlines produces only headlines, and headlines assess as weak.
    """

    id: str
    claim: str
    detail: str = ""

    def render(self) -> str:
        return f"- {self.claim}" + (f"\n    {self.detail}" if self.detail else "")


@dataclass(frozen=True)
class RoleMandate:
    """The company side's brief: the agenda, and what is actually movable."""

    id: str
    title: str
    company: str
    location: str
    summary: str
    criteria: tuple[Criterion, ...]
    looks_like: dict[str, str] = field(default_factory=dict)
    negotiable: tuple[str, ...] = ()
    not_negotiable: tuple[str, ...] = ()

    def render(self) -> str:
        """The brief as the company's agent reads it.

        Weights are shown. The company's own agent is allowed to know what it
        cares about most -- it is the candidate who is kept in the dark, the
        way a candidate is in life.
        """
        lines = [
            f"# The role you represent",
            f"{self.title} at {self.company}. {self.location}.",
            "",
            self.summary.strip(),
            "",
            "# Your agenda",
            "These are the only things you are here to establish. Each one is a",
            "criterion id you must name when you probe or when you record an answer.",
            "",
        ]
        for c in self.criteria:
            hard = "  [HARD -- no amount of strength elsewhere makes up for it]" if c.hard else ""
            lines.append(f"- {c.id} (weight {c.weight:g}){hard}")
            lines.append(f"    {c.question}")
            if c.id in self.looks_like:
                lines.append(f"    A strong answer contains: {self.looks_like[c.id]}")
        if self.negotiable:
            lines += ["", "# Movable", *(f"- {x}" for x in self.negotiable)]
        if self.not_negotiable:
            lines += ["", "# Not movable", *(f"- {x}" for x in self.not_negotiable)]
        return "\n".join(lines)


@dataclass(frozen=True)
class CandidateMandate:
    """The candidate side's brief: who they are, and what they authorised."""

    id: str
    name: str
    headline: str
    exceptional_at: str
    wants_to_own: str
    facts: tuple[Fact, ...]
    constraints: tuple[str, ...] = ()
    authorised: tuple[str, ...] = ()
    not_authorised: tuple[str, ...] = ()

    def render(self) -> str:
        lines = [
            "# Who you represent",
            f"{self.name} -- {self.headline}",
            "",
            "In their own words, what they are exceptional at:",
            f"  {self.exceptional_at.strip()}",
            "",
            "In their own words, what they want to own:",
            f"  {self.wants_to_own.strip()}",
            "",
            "# What you can draw on",
            "These are the facts you hold. If a question is not covered by one of",
            "them, you do not have the answer, and saying so is a legal move.",
            "",
        ]
        lines += [f.render() for f in self.facts]
        if self.constraints:
            lines += ["", "# Hard constraints", *(f"- {c}" for c in self.constraints)]
        if self.authorised:
            lines += ["", "# They authorised you to share", *(f"- {a}" for a in self.authorised)]
        if self.not_authorised:
            lines += [
                "",
                "# They did not authorise you to share",
                *(f"- {a}" for a in self.not_authorised),
                "",
                "Declining one of these is a move you are entitled to make, on the",
                "record, with the reason. Inventing an answer instead is not.",
            ]
        return "\n".join(lines)


def _read(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"no mandate at {p}")
    with p.open("rb") as fh:
        return tomllib.load(fh)


def load_role(path: str | Path) -> RoleMandate:
    raw = _read(path)
    role = raw["role"]
    criteria: list[Criterion] = []
    looks_like: dict[str, str] = {}
    for c in raw.get("criteria", []):
        criteria.append(
            Criterion(id=c["id"], question=c["question"], weight=float(c["weight"]), hard=c.get("hard", False))
        )
        if c.get("looks_like"):
            looks_like[c["id"]] = c["looks_like"]
    if not criteria:
        raise ValueError(f"{path}: a role mandate with no criteria has no agenda")
    m = raw.get("mandate", {})
    return RoleMandate(
        id=role["id"],
        title=role["title"],
        company=role["company"],
        location=role["location"],
        summary=role["summary"],
        criteria=tuple(criteria),
        looks_like=looks_like,
        negotiable=tuple(m.get("negotiable", [])),
        not_negotiable=tuple(m.get("not_negotiable", [])),
    )


def load_candidate(path: str | Path) -> CandidateMandate:
    raw = _read(path)
    c = raw["candidate"]
    m = raw.get("mandate", {})
    facts = tuple(
        Fact(id=f["id"], claim=f["claim"], detail=f.get("detail", "")) for f in raw.get("facts", [])
    )
    if not facts:
        raise ValueError(f"{path}: a candidate mandate with no facts can only improvise")
    return CandidateMandate(
        id=c["id"],
        name=c["name"],
        headline=c["headline"],
        exceptional_at=c["exceptional_at"],
        wants_to_own=c["wants_to_own"],
        facts=facts,
        constraints=tuple(m.get("constraints", [])),
        authorised=tuple(m.get("authorised", [])),
        not_authorised=tuple(m.get("not_authorised", [])),
    )
