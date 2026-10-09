"""Score extracted facts against an extracted agenda, and rank.

This is the paper screen: the cheap pass that reads what a CV states against
what a posting asks, and says which criteria are answered on paper and which
are not. It does not talk to anyone. The criteria it cannot settle from paper
are the ones worth spending an exchange on, which is what `run.py` does next.

Four properties, each enforced rather than promised:

**The screener never sees the CV.** It is handed `Facts`, which by
construction holds no document text -- see `intake/cv.py`. Names and contact
details are stripped before it is handed anything at all, because a
screener's judgement about a person's phone number is not a judgement anyone
asked for.

**Every score cites fact ids, and the ids are checked.** A criterion scored
against a fact that does not exist is discarded and counted, the same
discipline the posting and CV extractors run under.

**An assertion cannot earn more than `weak`.** A candidate who writes the
posting's own requirements back at it produces quotes that verify perfectly,
because they really did write those words. Verification proves a quote is
real; it never proves a claim is true. So `intake/cv.py` classifies every
fact as an instance or an assertion, and a criterion supported only by
assertions is capped here, in Python, after the model has answered. The model
is not asked to be disciplined about this -- it was, and it was not.

**UNKNOWN is a first-class outcome.** A criterion the CV does not speak to
scores UNKNOWN, contributes nothing, and is reported as an open question. It
is never a low score. The difference matters: a low score says the person is
weak on something, UNKNOWN says the paper does not say, and collapsing the
two is how a screen starts penalising people for what they left off a page.

**The ranking has no reject state.** It is an ordering with the evidence
attached and the open questions listed, routed to a human. Ranking applicants
is the most legally exposed operation in this pipeline -- it is the heart of
Mobley v. Workday -- and a system that cannot express a rejection cannot
quietly become the thing that rejects.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from intake.cv import Fact, Facts
from intake.posting import DraftAgenda, DraftCriterion

#: How much of a criterion's weight a strength earns. UNKNOWN is absent on
#: purpose: it earns nothing and is not a grade.
CREDIT = {"strong": 1.0, "moderate": 0.6, "weak": 0.25}
STRENGTHS = (*CREDIT, "unknown")

#: The most a criterion may earn when nothing but the candidate's own
#: self-description supports it. Measured, not chosen: without this cap a CV
#: built entirely of the posting's own phrases scored 76% and ranked second
#: of five. See `personas/expectations.toml`.
ASSERTION_CAP = "weak"

#: Kinds of fact for which a declaration IS the evidence, and the cap does not
#: apply. A CV has no "instance" form of a language, a degree or a
#: certification -- "English (C1)" is how that fact is stated, by everyone.
#: Capping those punishes the format of a CV rather than the substance of a
#: claim, which is the opposite of what the cap is for. Found by the cap
#: firing on `fluent_english` for three of six candidates at once.
DECLARATIVE_KINDS = ("language", "education", "credential")

#: The most a criterion may earn when the only thing supporting it is the
#: cover letter. Not a judgement about letters: a CV is written once and
#: reused, while a letter is written for this posting by someone who has read
#: the criteria, so it is the document most responsive to what the reader
#: wants to hear. It can answer an `unknown` -- that is the point of reading
#: it -- and it cannot, on its own, establish a criterion outright. When the
#: CV backs the same criterion, this cap does not apply.
LETTER_CAP = "moderate"

#: Facts that serve no criterion and are proxies for things nobody is
#: entitled to score on. Dropped before the screener is handed anything.
CONTACT_PATTERNS = (
    r"^contact_(?!location)", r"^hobb", r"\bphone\b", r"\bemail\b",
    r"\blinkedin\b", r"\baddress\b",
)

#: Facts that look like contact details but answer a criterion a posting may
#: really have. Kept, and the exception is named rather than implicit.
KEPT_DESPITE_CONTACT = (r"^contact_location$", r"\blocation\b")


def anonymise(facts: Facts) -> Facts:
    """Strip the name and the contact details. Keep everything substantive.

    Location is a harder call and is deliberately *kept*: a posting can ask
    for on-site in Madrid, so where someone is can be a real criterion. What
    goes is the identity and the means of contact, which answer nothing.
    """
    def drop(f: Fact) -> bool:
        if any(re.search(p, f.id, re.I) for p in KEPT_DESPITE_CONTACT):
            return False
        return any(re.search(p, f.id, re.I) or re.search(p, f.claim, re.I)
                   for p in CONTACT_PATTERNS)

    keep = [f for f in facts.facts if not drop(f)]
    return Facts(
        candidate_id=facts.candidate_id,
        name="",
        facts=keep,
        rejected=facts.rejected,
        extracted_at=facts.extracted_at,
        model=facts.model,
    )


@dataclass
class Assessment:
    """One criterion, scored against the facts, with what it was scored on."""

    criterion_id: str
    strength: str
    fact_ids: list[str] = field(default_factory=list)
    reasoning: str = ""
    weight: float = 0.0
    hard: bool = False
    #: Set when the cap fired, with the strength the screener first gave.
    #: The credit this strength was actually worth, resolved when the
    #: screening ran. Stored rather than looked up, so a record says which
    #: scale produced it instead of being reinterpreted by whatever the code
    #: happens to ship later.
    credit_value: float | None = None
    capped_from: str = ""
    #: Why it was capped, in the words the report prints. Two caps exist and
    #: a reader has to be able to tell which one fired.
    capped_why: str = ""
    rests_on: str = ""

    @property
    def unknown(self) -> bool:
        return self.strength == "unknown"

    @property
    def credit(self) -> float:
        if self.credit_value is not None:
            return self.credit_value
        return CREDIT.get(self.strength, 0.0)

    @property
    def earned(self) -> float:
        return self.weight * self.credit

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "credit": self.credit, "earned": round(self.earned, 4)}


@dataclass
class Screening:
    """What the paper says about one candidate against one agenda."""

    candidate_id: str
    posting_id: str
    assessments: list[Assessment] = field(default_factory=list)
    dropped: list[dict[str, str]] = field(default_factory=list)
    screened_at: str = ""
    model: str = ""

    @property
    def total_weight(self) -> float:
        return sum(a.weight for a in self.assessments)

    @property
    def evidenced_weight(self) -> float:
        """Weight the paper actually spoke to. The denominator that matters."""
        return sum(a.weight for a in self.assessments if not a.unknown)

    @property
    def score(self) -> float:
        """Earned weight over total weight, 0.0-1.0.

        UNKNOWN sits in the denominator, so a CV that says nothing scores low
        -- which is correct for an ordering, and is why `coverage` is
        reported beside it. A low score with low coverage is a document that
        did not answer, not a person who cannot.
        """
        return round(self.earned / self.total_weight, 4) if self.total_weight else 0.0

    @property
    def earned(self) -> float:
        return sum(a.earned for a in self.assessments)

    @property
    def coverage(self) -> float:
        """Share of weight the paper spoke to at all."""
        return round(self.evidenced_weight / self.total_weight, 4) if self.total_weight else 0.0

    @property
    def capped(self) -> list[str]:
        """Criteria the screener over-rated on self-description alone."""
        return [a.criterion_id for a in self.assessments if a.capped_from]

    @property
    def open_gates(self) -> list[str]:
        """Declared gates the paper leaves unanswered. Never a rejection."""
        return [a.criterion_id for a in self.assessments if a.hard and a.unknown]

    @property
    def open_questions(self) -> list[str]:
        return [a.criterion_id for a in self.assessments if a.unknown]

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "posting_id": self.posting_id,
            "screened_at": self.screened_at,
            "model": self.model,
            "score": self.score,
            "coverage": self.coverage,
            "open_gates": self.open_gates,
            "open_questions": self.open_questions,
            "capped": self.capped,
            "decides": False,
            "for_human_review": True,
            "assessments": [a.to_dict() for a in self.assessments],
            "dropped": self.dropped,
        }


# The four levels below read as vague, and the obvious repair was tried:
# anchoring each one to "how many assumptions does the cited fact need",
# plus a rule to take the lower level when hesitating. Measured on
# 2026-09-19, it was worse on every axis -- resolution 5.0% -> 10.5%,
# settled places 3/6 -> 2/6, `fluent_english` wobbling on four candidates
# instead of one, and every score deflated by the rounding rule. Both
# records are kept (runs/stability/founders_associate_n3_rubric_v1_personas.json and
# _rubric_v2.json); `python -m harness stability --compare` prints the
# difference. The wording that measured better is the one still here, and
# the next attempt should move one thing at a time.
SYSTEM = """\
You are given what a posting asks for, and a list of facts taken from one
candidate's CV. Each fact has an id and quotes the CV. Decide, for each
criterion, what the facts support.

Weigh what happened, not what the candidate says about themselves. A CV that
restates a requirement back at you -- "extreme ownership", "writing that
doesn't need editing" -- has told you nothing that an occasion would have
told you. Look for the occasion. If there is only the claim, the right answer
is weak, or unknown where there is not even a claim.

  strong    a fact directly establishes the criterion
  moderate  a fact clearly bears on it but falls short of establishing it
  weak      a fact is related but thin
  unknown   the facts do not speak to this criterion

`unknown` is the right answer more often than it feels like. It is not a
penalty and not a low score, it is the statement that this document does not
say. Use it whenever answering would require you to assume something the
facts do not state. Do not infer years of experience that no fact gives, do
not infer seniority from a job title, and do not infer a skill from the
presence of a related one.

Cite `fact_ids` for anything that is not unknown. Ids that are not in the
list you were given are discarded, so cite only ids you were actually given.
For unknown, cite nothing and say in one line what the CV would have to state
for this to be answerable.

You are not deciding anything about this person. You are saying what the
paper supports.
"""


def _schema(criterion_ids: list[str], fact_ids: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "assessments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "criterion_id": {"type": "string", "enum": criterion_ids},
                        "strength": {"type": "string", "enum": list(STRENGTHS)},
                        "fact_ids": {"type": "array", "items": {"type": "string", "enum": fact_ids or [""]}},
                        "reasoning": {"type": "string", "description": "one line; for unknown, what the CV would need to state"},
                    },
                    "required": ["criterion_id", "strength", "reasoning"],
                },
            }
        },
        "required": ["assessments"],
    }


def _render_agenda(criteria: list[DraftCriterion]) -> str:
    lines = []
    for c in criteria:
        gate = "  [the posting declares this a hard requirement]" if c.hard else ""
        lines.append(f"- {c.id} (weight {c.weight:g}){gate}")
        lines.append(f"    {c.question}")
        lines.append(f'    the posting says: "{c.source_quote}"')
        if c.looks_like:
            lines.append(f"    a strong answer contains: {c.looks_like}")
    return "\n".join(lines)


def screen(client: Any, agenda: DraftAgenda, facts: Facts, *, model: str,
           anonymous: bool = True, max_tokens: int = 4000,
           credit: dict[str, float] | None = None) -> Screening:
    """Score one candidate's facts against one agenda.

    `credit` is the scale a person chose in `settings.py`. It is passed in
    rather than read from a global so that two postings under two policies can
    be scored in one process without one of them silently inheriting the
    other's.
    """
    credit = credit or CREDIT
    seen = anonymise(facts) if anonymous else facts
    criteria = agenda.scored
    cids = [c.id for c in criteria]
    fids = [f.id for f in seen.facts]

    raw = client.structured(
        model=model,
        system=SYSTEM,
        user=(
            f"# What the posting asks for\n{_render_agenda(criteria)}\n\n"
            # `anonymous=False` used to keep the contact facts and nothing
            # else, which left the name out of the prompt either way and made
            # the flag quietly meaningless. It is shown here so that turning
            # anonymisation off has a cost that can be measured -- see
            # `harness/counterfactual.py`.
            + (f"# Candidate\n{seen.name}\n\n" if seen.name else "")
            + f"# Facts from the CV\n{seen.render()}"
        ),
        schema=_schema(cids, fids),
        tool_name="assess_against_criteria",
        tool_description="Say what the facts support for each criterion.",
        max_tokens=max_tokens,
    )

    out = Screening(
        candidate_id=facts.candidate_id,
        posting_id=agenda.posting_id,
        screened_at=datetime.now(timezone.utc).isoformat(),
        model=model,
    )
    by_id = {c.id: c for c in criteria}
    by_fact = {f.id: f for f in seen.facts}
    got: dict[str, Assessment] = {}

    for item in raw.get("assessments", []):
        cid = str(item.get("criterion_id", "")).strip()
        strength = str(item.get("strength", "")).strip()
        cited = [str(x) for x in (item.get("fact_ids") or [])]

        if cid not in by_id:
            out.dropped.append({"criterion_id": cid, "reason": "not on the agenda"})
            continue
        if cid in got:
            out.dropped.append({"criterion_id": cid, "reason": "duplicate assessment"})
            continue
        if strength not in STRENGTHS:
            out.dropped.append({"criterion_id": cid, "reason": f"unknown strength {strength!r}"})
            continue

        unreal = [f for f in cited if f not in fids]
        if unreal:
            # Evidence that does not exist cannot support a score. The
            # criterion survives as unanswered rather than as a guess.
            out.dropped.append({"criterion_id": cid,
                                "reason": f"cited facts that do not exist: {', '.join(unreal)}"})
            strength, cited = "unknown", []
        if strength != "unknown" and not cited:
            out.dropped.append({"criterion_id": cid, "reason": "scored with no evidence cited"})
            strength = "unknown"

        c = by_id[cid]
        capped_from, capped_why, rests_on = "", "", ""
        if strength != "unknown" and cited:
            backing = [by_fact[f] for f in cited if f in by_fact]
            stands = [f for f in backing
                      if f.is_instance or f.kind in DECLARATIVE_KINDS]
            rests_on = ("instances" if stands else "assertions only" if backing else "")
            if backing and not stands and credit[strength] > credit[ASSERTION_CAP]:
                capped_from, capped_why, strength = strength, "assertions only", ASSERTION_CAP
            # The letter cap is applied after, and only bites if the assertion
            # cap did not already: a criterion resting on the letter alone
            # cannot be established outright, however it was written.
            if backing and all(f.from_letter for f in backing):
                # Said whether or not the cap had to fire. A criterion that
                # came in at `moderate` rests on the letter exactly as much
                # as one knocked down to it, and labelling only the second
                # would make the cap look like the reason instead of the
                # evidence.
                rests_on = "the letter only"
                if credit[strength] > credit[LETTER_CAP]:
                    capped_from, capped_why, strength = strength, "the letter only", LETTER_CAP

        got[cid] = Assessment(
            criterion_id=cid,
            strength=strength,
            fact_ids=cited,
            reasoning=str(item.get("reasoning", "")).strip(),
            weight=c.weight,
            hard=c.hard,
            credit_value=credit.get(strength, 0.0),
            capped_from=capped_from,
            capped_why=capped_why,
            rests_on=rests_on,
        )

    # A criterion the screener simply did not return is unanswered, not absent.
    for c in criteria:
        if c.id not in got:
            got[c.id] = Assessment(c.id, "unknown", [], "the screener returned nothing for this",
                                   weight=c.weight, hard=c.hard,
                                   credit_value=credit.get("unknown", 0.0))
    out.assessments = [got[c.id] for c in criteria]
    return out


@dataclass(frozen=True)
class Lever:
    """A criterion that could still move, and by how much."""

    criterion_id: str
    #: "unanswered" -- the document is silent, so this is a question to ask.
    #: "thin" -- it is answered weakly, so this needs an occasion, not a
    #: question. The distinction is what makes the list actionable.
    kind: str
    weight: float
    gain: float
    hard: bool = False
    asks: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "gain": round(self.gain, 4)}


def leverage(s: Screening, agenda: Any = None) -> list[Lever]:
    """What would move this score the most, in order.

    A percentage tells a reader where somebody stands. It does not tell them
    what to do next, and the next thing is always the same thing: ask about
    the largest gap. This is arithmetic -- weight times the credit still
    available, over the total -- so it costs nothing and cannot be wrong
    about its own maths.

    A declared gate that nobody has answered is listed first whatever its
    weight, because an unanswered condition is a different kind of question
    from a missing strength: one decides whether to keep reading.
    """
    total = s.total_weight
    if not total:
        return []
    questions = {}
    for c in getattr(agenda, "criteria", []) or []:
        questions[c.id] = getattr(c, "question", "")

    out: list[Lever] = []
    for a in s.assessments:
        head = CREDIT["strong"] - CREDIT.get(a.strength, 0.0)
        if head <= 0:
            continue
        out.append(Lever(
            criterion_id=a.criterion_id,
            kind="unanswered" if a.unknown else "thin",
            weight=a.weight,
            gain=a.weight * head / total,
            hard=a.hard,
            asks=questions.get(a.criterion_id, ""),
        ))
    return sorted(out, key=lambda l: (l.hard and l.kind == "unanswered", l.gain),
                  reverse=True)


def next_questions(s: Screening, agenda: Any = None, limit: int = 3) -> str:
    """The shortest useful thing to print under a score."""
    levers = leverage(s, agenda)
    if not levers:
        return "  nothing left to ask: every criterion is answered at full strength"
    lines = ["  what would move this score, most first:"]
    for l in levers[:limit]:
        gate = "  [declared gate]" if l.hard else ""
        what = ("the document is silent -- ask" if l.kind == "unanswered"
                else "answered thinly -- needs an occasion, not a question")
        lines.append(f"    +{l.gain:.1%}  {l.criterion_id}{gate}")
        lines.append(f"           {what}")
        if l.asks:
            lines.append(f'           "{l.asks[:96]}"')
    return "\n".join(lines)


def trace(s: Screening, facts: Facts | None = None, agenda: Any = None,
          *, only: str = "") -> str:
    """Walk one number back to the two documents it came from.

    Linking a judgement to its evidence is the least a system like this owes
    anybody. Two things here go further than a link, and both are the point:

    * the criterion carries the sentence **the employer wrote**, verified
      against their posting, so a candidate can see they were measured
      against a real requirement and not an inferred one;
    * the fact carries the sentence **the candidate wrote**, verified against
      their document, and says whether it was read as something that happened
      or as a self-description -- which is the distinction that decides
      whether it could count for much.

    What was thrown away is printed too. Provenance that only shows the
    evidence that survived is an argument, not a record.
    """
    by_fact = {f.id: f for f in (facts.facts if facts else [])}
    by_crit = {c.id: c for c in (getattr(agenda, "criteria", []) or [])}

    lines = [f"{s.candidate_id} vs {s.posting_id} -- where each judgement came from", ""]
    for a in s.assessments:
        if only and a.criterion_id != only:
            continue
        gate = "  [declared a hard requirement]" if a.hard else ""
        lines.append(f"  {a.criterion_id}: {a.strength} (weight {a.weight:g}){gate}")

        c = by_crit.get(a.criterion_id)
        if c is not None:
            lines.append(f'    the posting says: "{c.source_quote}"')
            if getattr(c, "hard_quote", ""):
                lines.append(f'    and declares it hard: "{c.hard_quote}"')
        if a.capped_from:
            lines.append(f"    capped from {a.capped_from} -- "
                         f"{a.capped_why or 'assertions only'}")
        if a.reasoning:
            lines.append(f"    the screener's one line: {a.reasoning}")

        if a.unknown:
            lines.append("    nothing in the document speaks to this")
        for fid in a.fact_ids:
            f = by_fact.get(fid)
            if f is None:
                lines.append(f"    [{fid}] cited, but not in the fact set")
                continue
            where = "the letter" if f.from_letter else "the CV"
            lines.append(f"    [{fid}] {f.kind}/{f.evidence}, from {where}: {f.claim}")
            lines.append(f'        which {where} says as: "{f.source_quote}"')
        lines.append("")

    if facts and facts.rejected and not only:
        lines.append(f"  {len(facts.rejected)} extracted item(s) were discarded before "
                     f"scoring:")
        for r in facts.rejected[:10]:
            lines.append(f"    {r.id}: {r.reason}")
        lines.append("")

    lines.append("  Every quote above was checked against the document it is attributed")
    lines.append("  to, character for character. A quote that could not be found was")
    lines.append("  discarded rather than repaired -- which proves the document says the")
    lines.append("  words, and never that the words are true.")
    return "\n".join(lines)


def explain(s: Screening, *, spread: float | None = None,
            credit: dict[str, float] | None = None) -> str:
    """The arithmetic, line by line, so a reader can redo it by hand.

    "Purely mathematical" is not a sentence to write next to a number, it is
    a table under it. Someone who can re-add the column stops arguing with
    the score and starts arguing with the criteria -- which is where the
    argument belongs, because that is the part a human owns.

    `spread` is the measured drift from `harness/screener.py` when it exists.
    Printed because a number without its interval invites a comparison it
    cannot support.
    """
    credit = credit or CREDIT
    lines = [f"{s.candidate_id} vs {s.posting_id}",
             "",
             "  score = sum(weight x credit) / sum(weight)",
             ""]
    lines.append(f"  {'criterion':<28}{'weight':>8}{'strength':>11}{'credit':>8}"
                 f"{'earned':>9}")
    total = earned = 0.0
    for a in s.assessments:
        c = credit.get(a.strength, 0.0)
        got = a.weight * c
        total += a.weight
        earned += got
        note = ""
        if a.hard:
            note = "  [gate]"
        if a.capped_from:
            note += f"  [capped from {a.capped_from}: {a.capped_why or 'assertions only'}]"
        lines.append(f"  {a.criterion_id:<28}{a.weight:>8.1f}{a.strength:>11}"
                     f"{c:>8.2f}{got:>9.2f}{note}")
    lines.append("  " + "-" * 64)
    lines.append(f"  {'total':<28}{total:>8.1f}{'':>11}{'':>8}{earned:>9.2f}")
    lines.append("")
    lines.append(f"  score    = {earned:.2f} / {total:.1f} = {s.score:.1%}")
    unknown_weight = total - s.evidenced_weight
    lines.append(f"  coverage = {s.evidenced_weight:.1f} / {total:.1f} = {s.coverage:.0%}"
                 f"   ({unknown_weight:.1f} of weight the paper does not speak to)")
    if spread is not None:
        lines.append(f"  measured drift on identical input: +/-{spread:.1%} -- two "
                     f"candidates closer than that are not being told apart")
    if s.open_gates:
        lines.append(f"  declared gates left unanswered: {', '.join(s.open_gates)}"
                     f"  (a question, never a rejection)")
    lines.append("")
    lines.append("  This is a fit measurement against one posting. It is not a")
    lines.append("  recommendation to hire or to decline, and the system has no")
    lines.append("  state that means either.")
    return "\n".join(lines)


def render(s: Screening, facts: Facts | None = None) -> str:
    """One screening, as a person reads it."""
    lines = [
        f"{s.candidate_id} against {s.posting_id}",
        f"  score {s.score:.0%}   paper covered {s.coverage:.0%} of the weight",
        "",
    ]
    for a in sorted(s.assessments, key=lambda a: (a.strength == "unknown", -a.weight)):
        gate = " [gate]" if a.hard else ""
        cap = (f"  <- capped from {a.capped_from}: {a.capped_why or 'assertions only'}"
               if a.capped_from else "")
        lines.append(f"  {a.strength:<9} {a.criterion_id}{gate}  (weight {a.weight:g}){cap}")
        if a.reasoning:
            lines.append(f"      {a.reasoning}")
        for fid in a.fact_ids:
            f = facts.by_id(fid) if facts else None
            lines.append(f"      <- {fid}" + (f': "{f.source_quote[:90]}"' if f else ""))
    if s.open_gates:
        lines += ["", f"  gates the paper does not answer: {', '.join(s.open_gates)}",
                  "  (an open gate is a question, not a rejection)"]
    if s.dropped:
        lines += ["", f"  {len(s.dropped)} assessments discarded in verification:"]
        lines += [f"      {d['criterion_id']}: {d['reason']}" for d in s.dropped]
    return "\n".join(lines)


@dataclass
class Ranking:
    """An ordering of candidates against one posting. Not a decision."""

    posting_id: str
    screenings: list[Screening] = field(default_factory=list)
    ranked_at: str = ""

    @property
    def ordered(self) -> list[Screening]:
        """Highest score first, and ties broken by coverage.

        Coverage as the tiebreak is deliberate: between two equal scores, the
        one whose paper answered more of the agenda is the one there is more
        reason to believe.
        """
        return sorted(self.screenings, key=lambda s: (s.score, s.coverage), reverse=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "posting_id": self.posting_id,
            "ranked_at": self.ranked_at or datetime.now(timezone.utc).isoformat(),
            "decides": False,
            "for_human_review": True,
            "no_reject_state": True,
            "order": [
                {
                    "rank": i,
                    "candidate_id": s.candidate_id,
                    "score": s.score,
                    "coverage": s.coverage,
                    "open_gates": s.open_gates,
                    "open_questions": s.open_questions,
                    "capped": s.capped,
                }
                for i, s in enumerate(self.ordered, 1)
            ],
            "screenings": [s.to_dict() for s in self.screenings],
        }

    def render(self) -> str:
        lines = [f"{self.posting_id} -- {len(self.screenings)} candidates", ""]
        lines.append(f"  {'#':<3} {'candidate':<24} {'score':>6} {'covered':>8}  open")
        for i, s in enumerate(self.ordered, 1):
            opens = ", ".join(s.open_questions) or "-"
            lines.append(f"  {i:<3} {s.candidate_id:<24} {s.score:>5.0%} {s.coverage:>8.0%}  {opens[:60]}")
        lines += [
            "",
            "  This is an ordering with its evidence attached, for a human to read.",
            "  It contains no rejection and makes no decision.",
        ]
        return "\n".join(lines)


def save(obj: Screening | Ranking, out_dir: str | Path, name: str) -> Path:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.json"
    p.write_text(json.dumps(obj.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return p


def load(path: str | Path) -> Screening:
    """Read back a screening written by `save`."""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    s = Screening(
        candidate_id=d["candidate_id"],
        posting_id=d["posting_id"],
        screened_at=d.get("screened_at", ""),
        model=d.get("model", ""),
        dropped=d.get("dropped", []),
    )
    s.assessments = [
        Assessment(
            criterion_id=a["criterion_id"], strength=a["strength"],
            fact_ids=a.get("fact_ids", []), reasoning=a.get("reasoning", ""),
            weight=a.get("weight", 0.0), hard=a.get("hard", False),
            credit_value=a.get("credit_value"),
            capped_from=a.get("capped_from", ""), capped_why=a.get("capped_why", ""),
            rests_on=a.get("rests_on", ""),
        )
        for a in d.get("assessments", [])
    ]
    return s
