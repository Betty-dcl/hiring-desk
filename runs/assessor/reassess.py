"""Re-grade Ines's two owns_ambiguity answers with the assessor prompt as it
is (`before`) and with a prompt that also reads the question and the history
(`after`). Three calls each, on the smaller model, on a subscription.

The `after` prompt was rejected: it marked both answers down, the thin one
and the concrete one alike. See docs/DETAILS.md, "A judge, and how often it is wrong".

    python runs/assessor/reassess.py
"""
import json, sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, ".")
from nbh.llm import default_client, JUDGE_MODEL
from nbh.mandates import load_role
from nbh.protocol import Act, Exchange, Ledger, Message, Side, Strength

ROOT = Path(".")
ROLE = load_role(ROOT / "mandates" / "role_causa_prima_open_door.toml")


def _replayed(upto: int):
    """The recorded Ines exchange, replayed up to (not including) turn `upto`."""
    trace = json.loads((ROOT / "runs" / "exchange_ines_abadi.json").read_text(encoding="utf-8"))
    ex = Exchange(ledger=Ledger.from_criteria(ROLE.criteria), max_turns=trace["exchange"]["max_turns"])
    msgs = [Message(turn=m["turn"], speaker=Side(m["speaker"]), act=Act(m["act"]),
                    body=m["body"], criterion_id=m.get("criterion_id"))
            for m in trace["exchange"]["transcript"]]
    for m in msgs[: upto - 1]:
        ex.append(m, evidence_strength=Strength.PARTIAL if m.act is Act.DISCLOSE else None)
    return ex, msgs[upto - 1]


# The rejected prompt, kept here so the record can be reproduced. It is not in nbh/agent.py.
def assessment_prompt(exchange: Exchange, msg: Message, role: RoleMandate) -> str:
    """What the assessor reads: the criterion, the question, the history, the answer."""
    criterion = exchange.ledger.require(msg.criterion_id)  # type: ignore[arg-type]
    looks_like = role.looks_like.get(criterion.id, "concrete, specific, first-hand detail")
    asked = next((m for m in reversed(exchange.transcript) if m.speaker is not msg.speaker), None)
    before = [m for m in exchange.transcript
              if m.speaker is msg.speaker and m.criterion_id == criterion.id]
    parts = [
        f"# The criterion\n{criterion.id}: {criterion.question}\n"
        f"A strong answer contains: {looks_like}\n",
    ]
    if asked is not None:
        parts.append(f"# The question this answers (turn {asked.turn})\n{asked.body}\n")
    if before:
        parts.append("# Already said on this criterion\n" + "\n".join(
            f"- turn {m.turn} ({m.act.value}): {m.body}" for m in before) + "\n")
    parts.append(f"# What was said (turn {msg.turn})\n{msg.body}\n")
    parts.append(
        "# Record it\n"
        "strong  -- concrete, specific, first-hand, actually answers this: what they "
        "did, decided or found, in enough detail that it could be checked\n"
        "partial -- relevant but thin, second-hand, or adjacent to the question\n"
        "none    -- did not address this criterion\n\n"
        "An answer that asserts the quality in the criterion's own words, without "
        "saying what was done, is partial at most -- however well it fits the "
        "description above. Repeating what was already said on this criterion "
        "adds nothing."
    )
    return "\n".join(parts)



def old_prompt(ex, msg, role):
    c = ex.ledger.require(msg.criterion_id)
    ll = role.looks_like.get(c.id, "concrete, specific, first-hand detail")
    return (f"# The criterion\n{c.id}: {c.question}\nA strong answer contains: {ll}\n\n"
            f"# What was said\n{msg.body}\n\n# Record it\n"
            "strong  -- concrete, specific, first-hand, actually answers this\n"
            "partial -- relevant but thin, second-hand, or adjacent to the question\n"
            "none    -- did not address this criterion")

SYSTEM = ("You record how well an answer covered the question that was asked. "
          "You are not deciding whether to hire anyone, and you are not being "
          "polite. Warmth, confidence and fluency are not evidence.")
SCHEMA = {"type": "object", "properties": {"reasoning": {"type": "string"},
          "strength": {"type": "string", "enum": ["strong", "partial", "none"]}},
          "required": ["reasoning", "strength"]}
client = default_client()
out = Path("runs/assessor/reassess_ines_owns_ambiguity.json")
rec = {"trace": "exchange_ines_abadi", "criterion": "owns_ambiguity", "model": JUDGE_MODEL,
       "at": datetime.now(timezone.utc).isoformat(), "calls": []}
for turn in (10, 20):
    ex, msg = _replayed(turn)
    for name, fn in (("before", old_prompt), ("after", assessment_prompt)):
        for i in range(3):
            try:
                raw = client.structured(model=JUDGE_MODEL, system=SYSTEM, user=fn(ex, msg, ROLE),
                                        schema=SCHEMA, tool_name="record_evidence",
                                        tool_description="Record how well the answer covered the criterion.")
                r = {"turn": turn, "prompt": name, "pass": i + 1, "strength": raw["strength"], "reasoning": raw["reasoning"]}
            except Exception as e:
                r = {"turn": turn, "prompt": name, "pass": i + 1, "error": f"{type(e).__name__}: {e}"[:300]}
            rec["calls"].append(r); print(turn, name, i + 1, r.get("strength", r.get("error")), flush=True)
            out.write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
