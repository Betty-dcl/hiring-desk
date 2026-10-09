"""The loop, driven by a scripted model. No network, no key.

The point is to prove the mechanics before letting a real model near them:
that turns alternate, that the ledger only moves through legal acts, that an
illegal act is retried once and recorded, and that a fatal one ends the run
instead of silently continuing.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from nbh.agent import run_exchange, visible_criteria
from nbh.mandates import load_candidate, load_role
from nbh.protocol import Act, Side, Status

ROOT = Path(__file__).resolve().parents[1]
ROLE = load_role(ROOT / "mandates" / "role_causa_prima_open_door.toml")
CANDIDATE = load_candidate(ROOT / "mandates" / "candidate_ines.toml")


class ScriptedClient:
    """Replays a fixed list of answers, and remembers what it was asked."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.i = 0
        self.prompts = []

    def structured(self, **kw):
        self.prompts.append(kw["user"])
        if self.i >= len(self.answers):
            raise AssertionError(f"script exhausted after {self.i}")
        a = self.answers[self.i]
        self.i += 1
        return a


def act(a, body, criterion=None, reasoning="because"):
    return {"act": a, "body": body, "criterion_id": criterion, "reasoning": reasoning}


def strength(s):
    return {"strength": s, "reasoning": "recorded"}


def test_a_clean_exchange_runs_to_a_settled_close():
    script = [
        act("open", "Open door. Six things to establish."),
        act("open", "Representing Ines Abadi."),
        act("probe", "What have you shipped alone?", "ships_real_things"),
        act("disclose", "A nine-agent lead-intelligence system in production.", "ships_real_things"),
        strength("strong"),
        act("probe", "Madrid, on-site?", "madrid_on_site"),
        act("disclose", "Already there.", "madrid_on_site"),
        strength("strong"),
        act("close", "Enough for a recommendation."),
        act("close", "Agreed."),
    ]
    client = ScriptedClient(script)
    run = run_exchange(client, ROLE, CANDIDATE, max_turns=24)

    assert run.exchange.ended_reason == "both sides closed"
    assert run.exchange.ledger.status["ships_real_things"] is Status.RESOLVED
    assert run.exchange.ledger.status["madrid_on_site"] is Status.RESOLVED
    assert not run.violations
    assert run.exchange.ledger.fit_score() > 0


def test_the_candidate_never_sees_the_agenda_until_it_is_probed():
    ex_seen = []

    class Spy(ScriptedClient):
        def structured(self, **kw):
            ex_seen.append(kw["user"])
            return super().structured(**kw)

    client = Spy([
        act("open", "Open."),
        act("open", "Open."),
        act("probe", "Shipped what?", "ships_real_things"),
        act("disclose", "This.", "ships_real_things"),
        strength("partial"),
        act("close", "Done."),
        act("close", "Done."),
    ])
    run_exchange(client, ROLE, CANDIDATE, max_turns=24)

    candidate_opening = ex_seen[1]
    assert "goes_deep" not in candidate_opening
    assert "weight" not in candidate_opening.lower().split("# the exchange")[0].split("your move")[-1]

    candidate_answering = ex_seen[3]
    assert "ships_real_things" in candidate_answering
    assert "goes_deep" not in candidate_answering  # still hidden, never probed


def test_an_illegal_act_is_retried_once_and_recorded():
    client = ScriptedClient([
        act("open", "Open."),
        act("open", "Open."),
        act("probe", "About what?", "not_a_real_criterion"),   # illegal
        act("probe", "Shipped what?", "ships_real_things"),    # the retry
        act("disclose", "This.", "ships_real_things"),
        strength("strong"),
        act("close", "Done."),
        act("close", "Done."),
    ])
    run = run_exchange(client, ROLE, CANDIDATE, max_turns=24)

    assert len(run.violations) == 1
    v = run.violations[0]
    assert v["fatal"] is False and v["side"] == "company"
    assert "unknown criterion" in v["error"]
    assert run.exchange.ended_reason == "both sides closed"


def test_two_illegal_acts_in_a_row_end_the_run_rather_than_limping_on():
    client = ScriptedClient([
        act("open", "Open."),
        act("open", "Open."),
        act("probe", "?", "nope"),
        act("probe", "?", "still_nope"),
    ])
    run = run_exchange(client, ROLE, CANDIDATE, max_turns=24)

    assert run.violations[-1]["fatal"] is True
    assert "could not play a legal act" in run.exchange.ended_reason


def test_a_hard_blocker_collapses_the_score_even_with_strength_elsewhere():
    client = ScriptedClient([
        act("open", "Open."),
        act("open", "Open."),
        act("probe", "Shipped what?", "ships_real_things"),
        act("disclose", "A production multi-agent system.", "ships_real_things"),
        strength("strong"),
        act("probe", "Madrid?", "madrid_on_site"),
        act("withhold", "Not authorised to discuss location.", "madrid_on_site"),
        act("flag", "Location is unresolved and it is the one thing I cannot move on."),
    ])
    # budget stops it at the flag; we are testing the score, not the ending
    run = run_exchange(client, ROLE, CANDIDATE, max_turns=7)
    led = run.exchange.ledger

    assert led.status["madrid_on_site"] is Status.WITHHELD
    assert led.fit_score() > 0          # withheld is not blocked
    led.mark_blocked("madrid_on_site")
    assert led.fit_score() == 0.0       # blocked is


def test_the_turn_budget_is_enforced_by_the_exchange_not_the_agents():
    script = [act("open", "Open."), act("open", "Open.")]
    script += [act("flag", f"still going {i}") for i in range(20)]
    run = run_exchange(ScriptedClient(script), ROLE, CANDIDATE, max_turns=6)
    assert run.exchange.ended_reason == "turn budget exhausted"
    assert run.exchange.turn == 6


def test_every_turn_is_replayable_from_the_recorded_decisions():
    script = [
        act("open", "Open."),
        act("open", "Open."),
        act("probe", "Shipped?", "ships_real_things"),
        act("disclose", "Yes, this.", "ships_real_things"),
        strength("strong"),
        act("close", "Done."),
        act("close", "Done."),
    ]
    run = run_exchange(ScriptedClient(script), ROLE, CANDIDATE)
    # one recorded decision per committed message, assessor calls excluded
    assert len(run.decisions) == run.exchange.turn
    assert [d.raw["act"] for d in run.decisions] == [m.act.value for m in run.exchange.transcript]


def test_state_diff_is_captured_from_before_to_after():
    run = run_exchange(
        ScriptedClient([
            act("open", "Open."),
            act("open", "Open."),
            act("probe", "Shipped?", "ships_real_things"),
            act("disclose", "Yes.", "ships_real_things"),
            strength("strong"),
            act("close", "Done."),
            act("close", "Done."),
        ]),
        ROLE,
        CANDIDATE,
    )
    d = run.to_dict()["state_diff"]
    assert d["before"]["fit_score"] == 0.0
    assert d["after"]["fit_score"] > 0.0
    assert d["before"]["status"]["ships_real_things"] == "open"
    assert d["after"]["status"]["ships_real_things"] == "resolved"


def test_the_published_trace_replays_to_itself_with_no_model():
    """`run.py --replay` is the free demo: it must reach the same record.

    The trace keeps one decision per act and not the assessor's or the note
    writer's answers, so a replay fed the decisions alone crashed on the
    first disclosure.
    """
    import json

    from nbh import verdict
    from nbh.llm import ReplayClient

    path = ROOT / "runs" / "exchange_ines_abadi.json"
    rec = json.loads(path.read_text(encoding="utf-8"))
    client = ReplayClient.from_run(str(path))
    run = run_exchange(client, ROLE, CANDIDATE, max_turns=rec["exchange"]["max_turns"],
                       model="replay", assessor_model="replay")
    got = run.to_dict()
    assert got["exchange"] == rec["exchange"] and not run.violations
    assert verdict.render(client, run.exchange, ROLE, CANDIDATE, model="replay") == rec["verdicts"]
    assert client.usage.calls == 0
