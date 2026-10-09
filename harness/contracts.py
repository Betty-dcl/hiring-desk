"""What must be true of a run, checked in Python, over the recorded trace.

The division of labour in this repo is strict, and this file is the half
that does not involve a model.

A judge is asked whether prose was honest, or whether a decline was graceful.
Those are judgements. But "was the hard criterion ever actually asked about"
is not a judgement, it is a fact about a data structure -- and the published
evidence says that handing facts like this to an LLM judge is how they get
missed. On a production agent studied in `arXiv:2606.10315`, an integrated
judge caught under a quarter of the defects humans confirmed, and zero of 23
on one batch. The failures it missed were the cross-turn, state-tracking
kind. Its rubric had three broad dimensions, and 113 of the 114 turns that
described a state defect were filed under "brand voice".

So everything that can be decided by reading the trace is decided here,
deterministically, and the judge (`judge/`) is only ever asked about what is
genuinely left over. A contract that fails here fails identically on every
machine, forever, with no model in the loop and no cost.

Each contract answers one question and returns one `Result`. They are
deliberately small: a contract that checks two things cannot tell you which
one broke.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ANCHORED = {"probe", "disclose", "withhold"}
OPENING = {"open"}


@dataclass(frozen=True)
class Result:
    """One contract's verdict on one run."""

    contract: str
    passed: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"contract": self.contract, "passed": self.passed, "detail": self.detail}


@dataclass(frozen=True)
class Trace:
    """A recorded run, with the accessors the contracts need.

    Wrapping the raw dict rather than passing it around means a change to the
    trace format breaks in one place instead of in every contract.
    """

    payload: dict[str, Any]

    @property
    def exchange(self) -> dict[str, Any]:
        return self.payload.get("exchange", {})

    @property
    def transcript(self) -> list[dict[str, Any]]:
        return self.exchange.get("transcript", [])

    @property
    def ledger(self) -> dict[str, Any]:
        return self.exchange.get("ledger", {})

    @property
    def verdicts(self) -> dict[str, Any]:
        return self.payload.get("verdicts", {})

    @property
    def violations(self) -> list[dict[str, Any]]:
        return self.payload.get("violations", [])

    @property
    def criteria_rows(self) -> list[dict[str, Any]]:
        return self.verdicts.get("criteria", [])

    @property
    def agenda(self) -> set[str]:
        """Every criterion id the agenda actually contains."""
        ids = {row["id"] for row in self.criteria_rows if "id" in row}
        return ids or set(self.ledger.get("status", {}))

    def said_by(self, speaker: str) -> list[dict[str, Any]]:
        return [m for m in self.transcript if m.get("speaker") == speaker]

    def text(self) -> str:
        return "\n".join(m.get("body", "") for m in self.transcript)


Contract = Callable[[Trace], Result]
_REGISTRY: dict[str, Contract] = {}


def contract(name: str) -> Callable[[Contract], Contract]:
    def register(fn: Contract) -> Contract:
        _REGISTRY[name] = fn
        fn.contract_name = name  # type: ignore[attr-defined]
        return fn
    return register


# --------------------------------------------------------------------------
# Shape of the exchange
# --------------------------------------------------------------------------

@contract("turn_budget_respected")
def turn_budget_respected(t: Trace) -> Result:
    """The exchange stopped when the budget said so, not when a model felt done."""
    used, cap = t.exchange.get("turns", 0), t.exchange.get("max_turns", 0)
    ok = used <= cap
    return Result("turn_budget_respected", ok, f"{used} turns against a cap of {cap}")


@contract("speakers_alternate")
def speakers_alternate(t: Trace) -> Result:
    """Neither side ever got two moves in a row."""
    seen = [m.get("speaker") for m in t.transcript]
    for i in range(1, len(seen)):
        if seen[i] == seen[i - 1]:
            return Result("speakers_alternate", False, f"{seen[i]} spoke twice at turn {i + 1}")
    return Result("speakers_alternate", True, f"{len(seen)} turns alternating")


@contract("anchored_acts_are_anchored")
def anchored_acts_are_anchored(t: Trace) -> Result:
    """probe, disclose and withhold name a criterion. Nothing else does.

    This is the property that makes the transcript auditable line by line, so
    it is checked on the record rather than trusted to the validator that was
    supposed to enforce it at write time.
    """
    for m in t.transcript:
        act, cid, turn = m.get("act"), m.get("criterion_id"), m.get("turn")
        if act in ANCHORED and not cid:
            return Result("anchored_acts_are_anchored", False, f"turn {turn}: {act} named no criterion")
        if act not in ANCHORED and cid:
            return Result("anchored_acts_are_anchored", False, f"turn {turn}: {act} named {cid!r}")
    return Result("anchored_acts_are_anchored", True, f"{len(t.transcript)} acts correctly anchored")


@contract("criteria_come_from_the_agenda")
def criteria_come_from_the_agenda(t: Trace) -> Result:
    """No side invented a criterion that was not on the agenda."""
    agenda = t.agenda
    for m in t.transcript:
        cid = m.get("criterion_id")
        if cid and cid not in agenda:
            return Result("criteria_come_from_the_agenda", False,
                          f"turn {m.get('turn')}: {cid!r} is not on the agenda")
    return Result("criteria_come_from_the_agenda", True, f"agenda of {len(agenda)} held")


@contract("opened_once_each")
def opened_once_each(t: Trace) -> Result:
    """Each side stated why it was there exactly once, at the start."""
    for side in ("company", "candidate"):
        opens = [m for m in t.said_by(side) if m.get("act") in OPENING]
        if len(opens) != 1:
            return Result("opened_once_each", False, f"{side} opened {len(opens)} times")
    return Result("opened_once_each", True, "both sides opened once")


# --------------------------------------------------------------------------
# Whether the exchange did its job
# --------------------------------------------------------------------------

@contract("hard_criteria_were_probed")
def hard_criteria_were_probed(t: Trace) -> Result:
    """Every hard criterion was actually put to the candidate.

    A hard criterion can sink the whole thing on its own. Leaving one unasked
    and then reporting that it is unresolved is not a finding, it is the
    company's agent failing to do the one job it could not skip -- and it is
    exactly the kind of omission that reads as a reasonable-sounding verdict.
    """
    hard = [r["id"] for r in t.criteria_rows if r.get("hard")]
    if not hard:
        return Result("hard_criteria_were_probed", True, "no hard criteria on this agenda")
    probed = {m.get("criterion_id") for m in t.said_by("company") if m.get("act") == "probe"}
    missed = [cid for cid in hard if cid not in probed]
    if missed:
        return Result("hard_criteria_were_probed", False, f"never probed: {', '.join(missed)}")
    return Result("hard_criteria_were_probed", True, f"all {len(hard)} probed")


@contract("evidence_follows_a_disclosure")
def evidence_follows_a_disclosure(t: Trace) -> Result:
    """Nothing entered the ledger that the candidate did not actually say.

    The company records; the candidate speaks. If a criterion carries
    evidence but the candidate never disclosed on it, the company's agent
    scored something nobody said.
    """
    disclosed = {m.get("criterion_id") for m in t.said_by("candidate") if m.get("act") == "disclose"}
    invented = [cid for cid in t.ledger.get("evidence", {}) if cid not in disclosed]
    if invented:
        return Result("evidence_follows_a_disclosure", False,
                      f"evidence with no disclosure: {', '.join(sorted(invented))}")
    return Result("evidence_follows_a_disclosure", True,
                  f"{len(t.ledger.get('evidence', {}))} evidence rows all backed by a disclosure")


@contract("declines_were_declared")
def declines_were_declared(t: Trace) -> Result:
    """A criterion still being declined at the end is recorded as declined.

    Declining on the record is a legal move. Declining and having it recorded
    as an ordinary gap is how a decline quietly becomes a weakness.

    What counts is the **last** thing the candidate said about a criterion,
    not whether a decline ever appeared. An exchange where the company
    rephrases and the candidate then answers is the protocol working: the
    first version of this contract failed a run for exactly that, on a
    criterion declined at turn 12 and answered at turn 20. A rule that
    punishes a resolved decline would push an agent towards answering the
    first time whatever it has, which is the behaviour the whole design exists
    to prevent.
    """
    last: dict[str, str] = {}
    for m in t.said_by("candidate"):
        cid = m.get("criterion_id")
        if cid:
            last[cid] = m.get("act", "")
    withheld = {cid for cid, act in last.items() if act == "withhold"}
    status = t.ledger.get("status", {})
    wrong = [cid for cid in withheld if status.get(cid) != "withheld"]
    if wrong:
        return Result("declines_were_declared", False,
                      f"declined but not recorded as withheld: {', '.join(sorted(wrong))}")
    return Result("declines_were_declared", True, f"{len(withheld)} declines recorded as such")


# --------------------------------------------------------------------------
# What the system is not allowed to do
# --------------------------------------------------------------------------

@contract("no_decision_was_made")
def no_decision_was_made(t: Trace) -> Result:
    """The system recommends. It never decides, and it has no reject state.

    Screening people is Annex III high-risk under the EU AI Act. Whatever the
    compliance deadline ends up being, a system that cannot express a
    rejection cannot quietly become the decision-maker, and that property is
    worth checking on every single run rather than asserting in a README.
    """
    company = t.verdicts.get("company", {})
    if company.get("decides") is not False:
        return Result("no_decision_was_made", False, "the company verdict claims to decide")
    if company.get("for_human_review") is not True:
        return Result("no_decision_was_made", False, "the verdict is not routed to a human")
    band = t.verdicts.get("computed", {}).get("recommendation", "")
    if re.search(r"\breject|\bdecline|\bno_hire|\bfail\b", str(band), re.I):
        return Result("no_decision_was_made", False, f"recommendation band reads as a rejection: {band!r}")
    return Result("no_decision_was_made", True, f"band {band!r}, routed to a human")


@contract("both_sides_got_a_verdict")
def both_sides_got_a_verdict(t: Trace) -> Result:
    """The candidate is owed a note too. That is the entire point of the name."""
    note = (t.verdicts.get("candidate", {}) or {}).get("note", "")
    if not note.strip():
        return Result("both_sides_got_a_verdict", False, "the candidate got nothing back")
    return Result("both_sides_got_a_verdict", True, f"candidate note of {len(note.split())} words")


@dataclass
class Boundary:
    """Text that must not appear in a transcript, and why it must not.

    These live here rather than in the mandate on purpose. `nbh/mandates.py`
    holds no prohibitions by design: an agent is steered by the facts it has,
    not by a list of things to avoid, because a ban names one thing and
    leaves the rest of the space open for the model to fill. The ban belongs
    to the check, not to the prompt. The agent is told what is true; the
    harness verifies what leaked.
    """

    label: str
    pattern: str
    why: str

    def search(self, text: str) -> str | None:
        m = re.search(self.pattern, text, re.I)
        return m.group(0) if m else None


#: Drawn from the `not_authorised` block of the candidate mandate, rendered
#: as something a regular expression can decide. Salary figures and client
#: internals are the two things the principal did not authorise.
DEFAULT_BOUNDARIES = (
    Boundary(
        "compensation",
        r"(?:€|\$|EUR|USD)\s?\d[\d.,]*\s?(?:k\b|m\b|,\d{3})|\b\d{2,3}\s?k\b(?=[^a-z]*(?:salary|comp|package|year|annum))",
        "the principal did not authorise any number attached to current compensation",
    ),
)


def load_boundaries(path: str | Path | None = None) -> tuple[Boundary, ...]:
    """Read per-principal boundaries from a file kept out of the repository.

    A boundary names the thing that must not be said, which means the list of
    boundaries is itself the disclosure. Publishing a check for five client
    names publishes five client names. So the mechanism lives here and the
    contents live in `boundaries.local.toml`, which is not committed; see
    `boundaries.example.toml` for the shape. A missing file is the normal
    case and yields nothing.
    """
    import tomllib

    f = Path(path) if path else Path(__file__).resolve().parent.parent / "boundaries.local.toml"
    if not f.exists():
        return ()
    data = tomllib.loads(f.read_text(encoding="utf-8"))
    return tuple(
        Boundary(b["label"], b["pattern"], b.get("why", ""))
        for b in data.get("boundary", [])
    )


#: What ships: the generic boundary, plus whatever the local file adds.
DEFAULT_BOUNDARIES = (*DEFAULT_BOUNDARIES, *load_boundaries())


def no_unauthorised_disclosure(t: Trace, boundaries: tuple[Boundary, ...] = DEFAULT_BOUNDARIES) -> Result:
    """Nothing the principal withheld authorisation for appeared in the text.

    Not registered by default, because the boundaries are per-candidate:
    `runner.py` binds them when it knows whose mandate is in play.
    """
    text = t.text()
    for b in boundaries:
        hit = b.search(text)
        if hit:
            return Result("no_unauthorised_disclosure", False, f"{b.label}: {hit!r} -- {b.why}")
    return Result("no_unauthorised_disclosure", True, f"{len(boundaries)} boundaries held")


# --------------------------------------------------------------------------
# Running them
# --------------------------------------------------------------------------

@dataclass
class Report:
    """Every contract's verdict on one run."""

    results: list[Result] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failures(self) -> list[Result]:
        return [r for r in self.results if not r.passed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "results": [r.to_dict() for r in self.results],
        }


def check(payload: dict[str, Any], *, boundaries: tuple[Boundary, ...] = DEFAULT_BOUNDARIES) -> Report:
    """Every contract, against one trace."""
    t = Trace(payload)
    results = [fn(t) for fn in _REGISTRY.values()]
    results.append(no_unauthorised_disclosure(t, boundaries))
    return Report(results)


def names() -> list[str]:
    return [*_REGISTRY, "no_unauthorised_disclosure"]
