"""The judge, without a model: what it is allowed to keep, and how it is measured."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness import contracts
from judge import agenda, seeds
from judge.judge import Judgement, judge, load_principles, locations, render, verify
from nbh.llm import ReplayClient

ROOT = Path(__file__).resolve().parent.parent
TRACE = json.loads((ROOT / "runs" / "exchange_ines_abadi.json").read_text(encoding="utf-8"))
P = load_principles()


def _f(pid, at, quotes, why="because"):
    return {"principle_id": pid, "at": at, "quotes": quotes, "why": why}


# -- the principles --------------------------------------------------------


def test_eight_principles_on_four_axes_three_of_them_cross_turn_or_more():
    assert len(P) == 8
    assert {p.axis for p in P} == {"state", "grounding", "pressure", "decision"}
    assert sum(p.cross_turn for p in P) >= 3


def test_every_principle_says_what_it_is_not():
    assert all(p.not_this for p in P)


def test_the_judge_is_not_asked_what_a_contract_already_decides():
    names = {p.id for p in P}
    assert not names & set(contracts.names())


# -- what the judge reads --------------------------------------------------


def test_every_turn_evidence_row_and_verdict_is_a_location():
    where = locations(TRACE)
    assert "turn:1" in where and "turn:23" in where
    assert "evidence:goes_deep" in where
    assert {"verdict:company", "verdict:candidate"} <= set(where)


def test_the_rendered_trace_labels_every_location_it_offers():
    text = render(TRACE)
    for loc in locations(TRACE):
        assert f"[{loc}]" in text


# -- what is kept ----------------------------------------------------------


def test_a_finding_that_quotes_the_trace_is_kept():
    kept, dropped = verify(TRACE, {"findings": [
        _f("verdict_unsupported", ["verdict:company"], ["Madrid and trial confirmed"])]}, P)
    assert len(kept) == 1 and dropped == []


def test_a_quote_that_is_not_there_is_dropped():
    kept, dropped = verify(TRACE, {"findings": [
        _f("verdict_unsupported", ["verdict:company"], ["she managed a team of six"])]}, P)
    assert kept == [] and "quote not found" in dropped[0]["reason"]


def test_a_quote_must_be_at_the_location_it_names_not_anywhere():
    # Real words from turn 2, attributed to turn 3.
    kept, dropped = verify(TRACE, {"findings": [
        _f("evidence_overread", ["turn:3"], ["Four people use it now"])]}, P)
    assert kept == [] and dropped


def test_quotes_survive_curly_quotes_and_dashes():
    kept, _ = verify(TRACE, {"findings": [
        _f("verdict_unsupported", ["verdict:company"], ["a concrete ambiguity case-she designed"])]}, P)
    assert len(kept) == 1


def test_an_invented_location_is_dropped():
    _, dropped = verify(TRACE, {"findings": [
        _f("misremembered", ["turn:2", "turn:99"], ["Inès Abadi"])]}, P)
    assert "no such location" in dropped[0]["reason"]


def test_a_cross_turn_finding_needs_two_places():
    _, dropped = verify(TRACE, {"findings": [
        _f("self_contradiction", ["turn:4"], ["Nobody asked."])]}, P)
    assert "single location" in dropped[0]["reason"]


def test_a_principle_that_is_not_in_the_file_is_dropped():
    _, dropped = verify(TRACE, {"findings": [
        _f("brand_voice", ["turn:1"], ["I'm here for Causa Prima"])]}, P)
    assert "not a principle" in dropped[0]["reason"]


def test_the_same_finding_twice_is_kept_once():
    item = _f("verdict_unsupported", ["verdict:company"], ["Madrid and trial confirmed"])
    kept, _ = verify(TRACE, {"findings": [item, item]}, P)
    assert len(kept) == 1


def test_nothing_found_is_a_complete_answer():
    j = judge(ReplayClient([{"findings": []}]), TRACE, model="replay", trace_name="t")
    assert j.findings == [] and j.dropped == []
    d = j.to_dict()
    assert d["decides"] is False and d["is_a_quality_score"] is False


def test_the_raw_answer_is_kept_so_the_checks_can_be_rerun():
    ans = {"findings": [_f("verdict_unsupported", ["verdict:company"], ["not in there"])]}
    j = judge(ReplayClient([ans]), TRACE, model="replay", trace_name="t")
    assert j.answer == ans and len(j.dropped) == 1


# -- the seeds -------------------------------------------------------------


@pytest.mark.parametrize("seed", seeds.SEEDS, ids=lambda s: s.id)
def test_every_seed_passes_every_contract(seed):
    # The point of a seed: a defect the Python layer cannot see.
    report = contracts.check(seed.apply(TRACE))
    assert report.passed, [r.detail for r in report.failures]


@pytest.mark.parametrize("seed", seeds.SEEDS, ids=lambda s: s.id)
def test_every_seed_changes_the_place_it_names_and_nothing_else(seed):
    before, after = locations(TRACE), locations(seed.apply(TRACE))
    changed = {k for k in before if before[k] != after.get(k)}
    assert changed == set(seed.at)


def test_a_seed_never_touches_the_recorded_trace():
    snapshot = json.dumps(TRACE, sort_keys=True)
    for s in seeds.SEEDS:
        s.apply(TRACE)
    assert json.dumps(TRACE, sort_keys=True) == snapshot


def test_one_seed_per_principle():
    assert sorted(s.principle for s in seeds.SEEDS) == sorted(p.id for p in P)


def _judged(seed_id, findings):
    return {"seed": seed_id, "trace": "t", "judged_at": "", "model": "m", "principles": "x",
            "findings": [], "dropped": [], "answer": {"findings": findings}}


def test_caught_another_name_and_missed_are_kept_apart():
    planted = seeds.SEEDS[0]  # misremembered at turn 7
    quote = "You said twelve people rely on that tool"
    rec = {"runs": [
        _judged("", []),
        _judged(planted.id, [_f("misremembered", ["turn:2", "turn:7"], [quote])]),
        _judged(planted.id, [_f("verdict_unsupported", ["turn:7"], [quote])]),
        _judged(planted.id, []),
    ]}
    rep = seeds.report(rec, TRACE, seeds=(planted,))
    assert rep["seeds"][planted.id] == {"caught": 1, "another name": 1, "dropped": 0, "missed": 1}


def test_a_catch_with_a_misplaced_quote_is_dropped_not_missed():
    planted = seeds.SEEDS[0]
    # Real words, from turn 2, said to be at turn 7: the check throws it out,
    # and the report says it was seen rather than calling it a miss.
    rec = {"runs": [_judged(planted.id, [_f("misremembered", ["turn:2", "turn:7"],
                                             ["Four people use it now", "It pulls invoice status"])])]}
    rec["runs"][0]["answer"]["findings"][0]["quotes"].append("Who asked you to build that")
    rep = seeds.report(rec, TRACE, seeds=(planted,))
    assert rep["seeds"][planted.id]["dropped"] == 1 and rep["caught"] == 0


def test_a_catch_is_scored_on_todays_checks_not_the_stored_findings():
    planted = seeds.SEEDS[0]
    # The answer quotes something that is not in the seeded trace: however it
    # was stored at the time, the replayed check drops it.
    rec = {"runs": [_judged(planted.id, [_f("misremembered", ["turn:2", "turn:7"],
                                             ["You said thirty people"])])]}
    rec["runs"][0]["findings"] = [{"principle_id": "misremembered", "at": ["turn:2", "turn:7"],
                                   "quotes": ["You said thirty people"], "why": ""}]
    rep = seeds.report(rec, TRACE, seeds=(planted,))
    assert rep["caught"] == 0 and rep["seeds"][planted.id]["dropped"] == 1


def test_a_handful_of_trials_is_printed_as_a_count():
    rec = {"runs": [_judged(s.id, []) for s in seeds.SEEDS]}
    out = seeds.render(seeds.report(rec, TRACE))
    assert "caught 0 of 8 planted defects -- a count, not a rate" in out


def test_silence_on_the_clean_trace_is_not_called_a_certificate():
    rec = {"runs": [_judged("", [])]}
    assert "not a certificate" in seeds.render(seeds.report(rec, TRACE))


# -- the agenda ------------------------------------------------------------


def _j(trace, *pids, seed=""):
    from judge.judge import Finding
    return Judgement(trace=trace, judged_at="", model="m", principles="x", seed=seed,
                     findings=[Finding(p, ["turn:1"], ["q"], f"{p} on {trace}") for p in pids])


def test_the_agenda_orders_by_how_many_exchanges_not_how_many_findings():
    a = agenda.build([_j("a", "resettled", "resettled", "resettled"),
                      _j("b", "misremembered"), _j("c", "misremembered"), _j("d")])
    assert [p["principle_id"] for p in a["principles"]] == ["misremembered", "resettled"]
    assert a["with_nothing_found"] == 1


def test_planted_defects_never_reach_a_review_agenda():
    a = agenda.build([_j("a", "misremembered", seed="misremembered"), _j("b")])
    assert a["principles"] == [] and a["left_out_seeded"] == 1


def test_an_empty_agenda_still_says_to_read_a_sample():
    out = agenda.render(agenda.build([_j("a")]))
    assert "Read a sample anyway" in out and "different from cleared" in out


# -- a batch survives an incident ------------------------------------------------------

class Flaky:
    """Answers nothing found, except on the calls told to fail."""

    def __init__(self, fail_on=()):
        self.calls, self.fail_on = 0, set(fail_on)

    def structured(self, **kw):
        self.calls += 1
        if self.calls in self.fail_on:
            from nbh.llm import LLMError
            raise LLMError("CLI timed out after 180s")
        return {"findings": []}


def test_one_failed_call_does_not_end_the_batch(tmp_path):
    out = tmp_path / "rec.json"
    rec = seeds.measure(Flaky(fail_on={3}), TRACE, model="m", trace_name="t", n=1, out=out)
    assert len(rec["runs"]) == 8 and len(rec["errors"]) == 1
    assert json.loads(out.read_text())["runs"] == rec["runs"]  # written, not just returned


def test_a_rerun_retries_only_what_is_missing(tmp_path):
    out = tmp_path / "rec.json"
    seeds.measure(Flaky(fail_on={3}), TRACE, model="m", trace_name="t", n=1, out=out)
    again = Flaky()
    rec = seeds.measure(again, TRACE, model="m", trace_name="t", n=1, out=out)
    assert again.calls == 1 and len(rec["runs"]) == 9 and rec["errors"] == []


def test_more_passes_extend_a_record_rather_than_redo_it(tmp_path):
    out = tmp_path / "rec.json"
    seeds.measure(Flaky(), TRACE, model="m", trace_name="t", n=1, out=out)
    more = Flaky()
    rec = seeds.measure(more, TRACE, model="m", trace_name="t", n=2, out=out)
    assert more.calls == 9 and len(rec["runs"]) == 18


def test_a_failed_call_is_not_counted_as_a_miss():
    rec = {"runs": [_judged(s.id, []) for s in seeds.SEEDS[1:]],
           "errors": [{"pass": 1, "seed": seeds.SEEDS[0].id, "error": "timeout"}]}
    rep = seeds.report(rec, TRACE)
    assert rep["trials"] == 7 and rep["errors"] == 1
    assert "not counted" in seeds.render(rep)
