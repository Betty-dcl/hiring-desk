"""A message drafted by a model, for a person to read, change and send. Off by default.

`desk.toml`:

    [mail]
    ai_drafts = false

When a team turns it on, an application that is Ready to contact, Ready to
answer no or Talk later shows "Draft with AI" beside the draft built from the
team's template. Pressed, it asks the model configured for this repository
(`nbh.llm.default_client`: `NBH_BACKEND`, the Claude Code subscription by
default) for one draft, written from these sources and nothing else:

  * the outcome the votes reached (contact, later, no) and the role;
  * the candidate's first name;
  * the comments of the partners who voted -- only theirs, only once cast;
  * the candidate's own sentences, as the card quotes them (`signals.py`);
  * the team's template for that outcome, for the tone and the signature.

What the desk does around that one call, because a model is not a reliable
narrator of its own facts:

  * the answer is a draft in an editable box, never sent and never stored as
    the message: a person reads it, changes it, and sends it from their own
    mail app, exactly as with the template;
  * **no invented facts**: every number, web address, e-mail address and name
    in the draft must appear in the sources; anything that does not is
    listed above the box as "not in the sources -- check it". The check is
    textual: it catches a "15 years" or a "Lisbon" the sources never said,
    not a sentence that misreads a true one;
  * **no protected subject**: a draft touching age, family, health, religion,
    origin and the rest (`trial.PROTECTED`, the guard every note and vote
    comment already passes) is refused whole, not shown with a warning: a
    "no" that gave such a reason is the one draft nobody should edit from;
  * **who asked** is recorded (`runs/desk/ai_drafts.jsonl`: who, when, which
    application, which outcome, how many warnings) -- not the text.

Limits, said plainly: the draft can still be wrong in tone or emphasis, or
leave out what matters; the fact check is a filter on tokens, not a reading.
A model sees the candidate's documents' sentences and the partners'
comments: with the default backend that is the team's own Claude account.
The tests use a fake client only; nothing here is called by the test suite.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import desk
from desk import Config, DeskError
from trial import PROTECTED, _hits

#: The meanings a draft can be asked for, and how the prompt names each.
OUTCOME = {
    "contact": "We would like to talk with them: invite them to a first conversation.",
    "later": "Not now: we would like to talk with them later, and keep their application.",
    "pass": "No, not for this role: a clear, kind answer.",
}

SCHEMA = {
    "type": "object",
    "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
    "required": ["subject", "body"],
    "additionalProperties": False,
}

SYSTEM = """You draft one e-mail for a small hiring team. A person on the team will read
your draft, change it, and send it themselves. You never send anything.

Rules, in order of importance:
1. Use only the facts in the SOURCES. Do not add any number, date, name, place,
   company, title, link, promise or detail that is not written there. If something
   would be useful but is not in the sources, leave it out.
2. Never mention or allude to age, family, children, marriage, pregnancy, health,
   disability, religion, ethnicity, nationality, origin, sexual orientation, union
   membership or political views -- not as a reason, not as small talk.
3. For a "no": do not invent a reason. You may say the team decided not to go
   further for this role, and, if the partners' comments give a reason about the
   work itself, say it briefly and kindly.
4. Keep the team's tone, length and signature from their TEMPLATE. Write in the
   template's language. Address the candidate by the first name given.
5. You may quote the candidate's own sentences, word for word, if it helps.
Reply with a subject and a body."""


@dataclass
class Sources:
    meaning: str
    role: str
    first_name: str
    company: str
    sender: str
    #: (voter, what they wrote) -- only voters who voted, only what they wrote.
    comments: list[tuple[str, str]]
    #: (what the card calls it, their sentence).
    quotes: list[tuple[str, str]]
    template_subject: str
    template_body: str

    def texts(self) -> list[str]:
        return ([self.role, self.first_name, self.company, self.sender,
                 self.template_subject, self.template_body]
                + [f"{v} {c}" for v, c in self.comments] + [q for _, q in self.quotes])


@dataclass
class Draft:
    subject: str
    body: str
    meaning: str
    #: What the check found that is not in the sources. Shown above the box.
    warnings: list[str] = field(default_factory=list)
    by: str = ""
    at: str = ""


# --------------------------------------------------------------------------
# The setting
# --------------------------------------------------------------------------

def enabled(root: Path | None = None) -> bool:
    """`[mail] ai_drafts` in desk.toml. False when absent: nobody turns a model on by default."""
    try:
        raw = tomllib.loads(((root or desk.ROOT) / "desk.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False
    return raw.get("mail", {}).get("ai_drafts", False) is True


# --------------------------------------------------------------------------
# What the model is given
# --------------------------------------------------------------------------

def outcome_of(candidate_id: str, posting: str, cfg: Config,
               now: datetime | None = None) -> str:
    """contact, later or pass -- the outcome a person is to write about -- or ""."""
    import stages
    pipe = desk._pipelines().get(posting)
    if pipe is None or candidate_id not in pipe.candidates:
        raise DeskError(f"{candidate_id} is not on the desk for {posting}")
    s = pipe.standing(candidate_id)
    st = stages.derive(s, cfg, now)
    if st.id == "talk_later":
        return "later"
    meaning = stages.meaning_of(st, cfg)
    if meaning:
        return meaning
    o = desk.outcome(desk.votes(s), cfg)
    if o.status == "agreed" and o.meaning == "later" and not st.ended:
        return "later"
    return ""


def sources(candidate_id: str, posting: str, cfg: Config,
            now: datetime | None = None) -> Sources:
    meaning = outcome_of(candidate_id, posting, cfg, now)
    # A reminder or an invitation is the team's template, filled in: the
    # model only drafts the three outcomes it has words for.
    if meaning not in OUTCOME:
        raise DeskError("a draft follows a decided outcome: Ready to contact, Ready to "
                        "answer no, or Talk later")
    reg = desk.load_registry(desk._registry_path())
    person = reg.person_of(candidate_id)
    if person is None:
        raise DeskError(f"{candidate_id} is not on the desk")
    role = desk._titles().get(posting, posting)
    s = desk._pipelines()[posting].standing(candidate_id)
    comments = []
    for voter, v in desk.votes(s).items():
        said = " — ".join(x for x in (v.reason, v.comment) if x)
        # Already refused at the vote; checked again, the model must never see one.
        if said and not _hits(said, PROTECTED):
            comments.append((voter, said))
    from signals import signals
    sg = signals(person, candidate_id, posting)
    quotes = ([("built", q) for q in sg.built[:1]] + [("wants to own", q) for q in sg.wants[:2]]
              + ([("AI in their work", sg.ai_rests_on)] if sg.ai_rests_on else []))
    quotes = [(k, q) for k, q in quotes if not _hits(q, PROTECTED)]
    fill = desk.mail_fields(person, role, cfg)
    return Sources(meaning=meaning, role=role, first_name=person.first_name,
                   company=cfg.company, sender=fill["sender"], comments=comments,
                   quotes=quotes,
                   template_subject=desk.fill_in(cfg.subject(meaning), fill),
                   template_body=desk.fill_in(cfg.template(meaning), fill).strip())


def prompt(src: Sources) -> str:
    """The user turn: the sources, labelled, and nothing the desk inferred."""
    comments = "\n".join(f"- {v}: {c}" for v, c in src.comments) or "- (none)"
    quotes = "\n".join(f"- {k}: \"{q}\"" for k, q in src.quotes) or "- (none)"
    return (f"SOURCES\n\nOutcome decided by the team: {OUTCOME[src.meaning]}\n"
            f"Role applied for: {src.role}\nCandidate's first name: {src.first_name}\n"
            f"Company: {src.company}\nSignature: {src.sender}\n\n"
            f"What the partners who voted wrote:\n{comments}\n\n"
            f"The candidate's own sentences:\n{quotes}\n\n"
            f"TEMPLATE (the team's words for this outcome: keep its tone, length and "
            f"signature)\nSubject: {src.template_subject}\n\n{src.template_body}\n")


# --------------------------------------------------------------------------
# The check on what comes back
# --------------------------------------------------------------------------

NUMBER = re.compile(r"\d+(?:[.,:]\d+)*")
LINK = re.compile(r"https?://\S+|\bwww\.\S+|[\w.+-]+@[\w-]+\.[\w.]+")
#: A capitalised word that does not start a sentence or a line: a name, a
#: place, a company, a product -- the facts a model invents most easily.
NAME = re.compile(r"(?<![.!?:\n]\s)(?<!^)(?<=\s)([A-ZÀ-Þ][\w'’-]+)", re.M)
#: Capitalised words every mail may carry.
COMMON = {"I", "I'm", "I'd", "I've", "I'll", "Hi", "Hello", "Dear", "Thank", "Thanks",
          "Best", "Kind", "Regards", "We", "AI"}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).casefold()


def unsupported(text: str, src: Sources) -> list[str]:
    """Numbers, links and names in `text` that no source contains. Textual, on purpose."""
    hay = _norm(" ".join(src.texts()))
    hay_num = re.sub(r"\D+", " ", hay)
    out: list[str] = []
    for n in NUMBER.findall(text):
        if re.sub(r"\D+", " ", n).strip() not in hay_num and n not in out:
            out.append(n)
    for link in LINK.findall(text):
        link = link.rstrip(".,;:!?)")
        if link.casefold() not in hay and link not in out:
            out.append(link)
    for w in NAME.findall(text):
        w = w.strip("'’-")
        if w in COMMON or len(w) < 2 or w in out:
            continue
        if w.casefold() not in hay:
            out.append(w)
    return out


def check(subject: str, body: str, src: Sources) -> list[str]:
    """Refuse a draft on a protected subject; list what is not in the sources."""
    hits = _hits(f"{subject}\n{body}", PROTECTED)
    if hits:
        raise DeskError("the AI draft touched a protected subject and was not kept; write "
                        "this one from the template")
    return unsupported(f"{subject}\n{body}", src)


# --------------------------------------------------------------------------
# The one call
# --------------------------------------------------------------------------

def _log(rec: dict[str, Any]) -> None:
    f = desk.ROOT / "runs" / "desk" / "ai_drafts.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    with f.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def draft(candidate_id: str, posting: str, cfg: Config, *, by: str, client: Any = None,
          now: datetime | None = None, root: Path | None = None) -> Draft:
    """Ask the model once for a draft, check it, record who asked. Never sends anything."""
    if not enabled(root):
        raise DeskError("AI drafts are off on this desk ([mail] ai_drafts in desk.toml)")
    by = cfg.voter(by)
    src = sources(candidate_id, posting, cfg, now)
    now = now or datetime.now(timezone.utc)
    rec = {"at": now.isoformat(), "by": by, "posting": posting, "candidate": candidate_id,
           "meaning": src.meaning}
    if client is None:
        from nbh.llm import default_client
        client = default_client()
    from nbh.llm import AGENT_MODEL, LLMError
    try:
        out = client.structured(model=AGENT_MODEL, system=SYSTEM, user=prompt(src),
                                schema=SCHEMA, tool_name="draft",
                                tool_description="One e-mail draft: a subject and a body.",
                                max_tokens=900)
    except LLMError as err:
        _log({**rec, "result": "failed"})
        raise DeskError(f"the AI draft could not be made: {err}") from err
    subject = " ".join(str(out.get("subject", "")).split())
    body = str(out.get("body", "")).strip() + "\n"
    try:
        warnings = check(subject, body, src)
    except DeskError:
        _log({**rec, "result": "refused: protected subject"})
        raise
    _log({**rec, "result": "drafted", "warnings": len(warnings)})
    return Draft(subject=subject, body=body, meaning=src.meaning, warnings=warnings,
                 by=by, at=now.isoformat())
