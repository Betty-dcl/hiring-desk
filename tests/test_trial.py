"""The trial day: its design, the brief, the card, and the day against the paper.

Three things here can do damage that a later correction does not reach: a day
that tests different candidates differently, a card that lets a verdict onto
the record without an observation under it, and a calibration that prints a
rate off three data points. Most of these tests hold one of those shut.
"""

from __future__ import annotations

import tomllib

import pytest

from intake.cv import Fact, Facts
from intake.posting import DraftAgenda, DraftCriterion
from screen import Assessment, Screening
from trial import (
    MIN_EXERCISE_MINUTES,
    MIN_PAIRS,
    CardError,
    Design,
    Entry,
    Exercise,
    Observation,
    answer_of,
    build,
    calibrate,
    card,
    design,
    outcome,
    probes,
    read_card,
    verify,
    wilson,
)


def agenda():
    return DraftAgenda(posting_id="p", title="T", criteria=[
        DraftCriterion(id="gate", question="Show me something you built with an agent",
                       source_quote="Genuinely AI-native.", section="requirement",
                       looks_like="Concrete examples of using AI agents to do real work",
                       hard=True, hard_quote="hard requirement", weight=2.0),
        DraftCriterion(id="writing", question="Share a piece of writing",
                       source_quote="Writing that doesn't need editing.", section="requirement",
                       looks_like="Writing samples that went out without edits", weight=2.0),
        DraftCriterion(id="own", question="Describe a gap you filled",
                       source_quote="Extreme ownership.", section="requirement", weight=2.0),
        DraftCriterion(id="years", question="How long were you there?",
                       source_quote="3-4 years", section="requirement", weight=2.0),
        DraftCriterion(id="spanish", question="Do you speak Spanish?",
                       source_quote="Spanish", section="nice_to_have", weight=1.0),
        DraftCriterion(id="resp_ops", question="q", source_quote="Run financial operations.",
                       section="responsibility"),
        DraftCriterion(id="resp_build", question="q", source_quote="Build with AI agents.",
                       section="responsibility"),
    ])


def ex(eid="e1", rid="resp_build", minutes=60, observes=("gate",), **kw):
    return {"id": eid, "title": kw.get("title", "Build it"), "responsibility_id": rid,
            "minutes": minutes, "material": kw.get("material", "A folder of invented receipts."),
            "observes": [{"criterion_id": c, "strong": kw.get("strong", "has working output"),
                          "thin": kw.get("thin", "describes it in theory")} for c in observes]}


def answer(*exercises, not_observable=()):
    return {"exercises": list(exercises),
            "not_observable": [{"criterion_id": c, "why": "a fact about the past"}
                               for c in not_observable]}


# --------------------------------------------------------------------------
# The design: what verification keeps and what it throws out
# --------------------------------------------------------------------------

def test_an_exercise_must_be_a_piece_of_a_listed_responsibility():
    d = verify(agenda(), answer(ex(rid="resp_invented")))
    assert not d.exercises
    assert "not a responsibility this posting lists" in d.rejected[0]["reason"]


def test_the_posting_sentence_travels_with_the_exercise():
    d = verify(agenda(), answer(ex()))
    assert d.exercises[0].from_posting == "Build with AI agents."


def test_an_exercise_that_observes_nothing_scored_is_dropped():
    d = verify(agenda(), answer(ex(observes=("resp_ops", "invented"))))
    assert not d.exercises
    assert any("not criteria of this posting" in r["reason"] for r in d.rejected)


@pytest.mark.parametrize("text", [
    "Ask them how many children they have",
    "Note the candidate's age",
    "Find out their nationality",
    "Ask about their health",
    "Discuss family plans over lunch",
])
def test_an_exercise_touching_a_protected_subject_is_dropped_whole(text):
    d = verify(agenda(), answer(ex(material=text)))
    assert not d.exercises
    assert "protected subject" in d.rejected[0]["reason"]


@pytest.mark.parametrize("text", [
    "Reconcile the invoice age report",
    "Run payroll including health insurance for three entities",
    "Invite two top-tier partners to the dinner",
    "Assess political risk in the market analysis",
])
def test_ordinary_finance_material_is_not_mistaken_for_a_protected_subject(text):
    """A filter that discards sound exercises gets switched off."""
    assert verify(agenda(), answer(ex(material=text))).exercises


def test_what_an_observer_sees_cannot_be_written_as_a_verdict():
    d = verify(agenda(), answer(ex(strong="hire them if the script runs")))
    assert not d.exercises
    assert "verdict" in d.rejected[0]["reason"]


def test_an_exercise_under_the_floor_is_dropped():
    d = verify(agenda(), answer(ex(minutes=MIN_EXERCISE_MINUTES - 15)))
    assert not d.exercises
    assert "floor" in d.rejected[0]["reason"]


def test_exercises_past_the_budget_are_named_not_squeezed():
    d = verify(agenda(), answer(ex("a", minutes=60), ex("b", minutes=60)),
               day_minutes=120, slot_minutes=30)
    assert [e.id for e in d.exercises] == ["a"]
    assert d.rejected == [{"id": "b", "reason": "over the day's budget (60 + 60 > 90 minutes)"}]


def test_a_duplicate_exercise_id_is_dropped():
    d = verify(agenda(), answer(ex("a"), ex("a", observes=("writing",))))
    assert [e.id for e in d.exercises] == ["a"]
    assert d.rejected[0]["reason"] == "duplicate id"


def test_a_criterion_nobody_observes_or_excuses_is_named_as_uncovered():
    d = verify(agenda(), answer(ex(), not_observable=("years",)))
    assert set(d.uncovered) == {"writing", "own", "spanish"}
    assert "years" in d.not_observable


def test_unobservable_needs_a_reason():
    d = verify(agenda(), {"exercises": [ex()],
                          "not_observable": [{"criterion_id": "years", "why": ""}]})
    assert "years" not in d.not_observable
    assert "years" in d.uncovered


def test_an_exercise_that_shows_it_beats_a_sentence_saying_nothing_can():
    d = verify(agenda(), answer(ex(observes=("gate", "years")), not_observable=("years",)))
    assert "years" not in d.not_observable


def test_a_declared_condition_read_once_is_flagged():
    d = verify(agenda(), answer(ex()))
    assert d.under_read_gates == ["gate"]
    assert "WARNING: gate" in d.render()


def test_a_declared_condition_read_twice_is_not_flagged():
    d = verify(agenda(), answer(ex("a"), ex("b", rid="resp_ops")))
    assert d.under_read_gates == []


def test_verification_can_be_replayed_from_the_stored_answer():
    first = verify(agenda(), answer(ex("a", minutes=15), ex("b")))
    again = verify(agenda(), answer_of(first))
    assert [e.id for e in again.exercises] == [e.id for e in first.exercises] == ["b"]


class Capture:
    def __init__(self, reply):
        self.reply, self.seen = reply, {}

    def structured(self, **kw):
        self.seen = kw
        return self.reply


def test_the_design_call_is_handed_the_posting_and_no_candidate():
    """The one model call in this module never sees a person."""
    c = Capture(answer(ex()))
    design(c, agenda(), model="m")
    prompt = c.seen["system"] + c.seen["user"]
    assert "Genuinely AI-native." in prompt
    for absent in ("Facts from the CV", "Candidate", "candidate_id"):
        assert absent not in c.seen["user"]


def test_a_posting_with_no_responsibilities_cannot_be_designed():
    a = agenda()
    a.criteria = [c for c in a.criteria if c.section != "responsibility"]
    with pytest.raises(ValueError, match="no responsibilities"):
        design(Capture({}), a, model="m")


# --------------------------------------------------------------------------
# The brief
# --------------------------------------------------------------------------

def facts():
    return Facts(candidate_id="c", name="C", facts=[
        Fact("f_built", "project", "Built a tool", "Built a tool with a coding agent",
             evidence="instance"),
        Fact("f_claim", "trait", "Says they own things", "I take extreme ownership",
             evidence="assertion"),
        Fact("f_letter", "project", "Wrote updates", "I wrote every investor update",
             evidence="instance", source="letter"),
        Fact("f_es", "language", "Spanish", "Spanish (fluent)", evidence="assertion"),
    ])


def screening(**over):
    rows = {
        "gate": Assessment("gate", "unknown", [], "", weight=2.0, hard=True),
        "writing": Assessment("writing", "moderate", ["f_letter"], "", weight=2.0,
                              rests_on="the letter only"),
        "own": Assessment("own", "weak", ["f_claim"], "", weight=2.0, capped_from="strong",
                          capped_why="assertions only", rests_on="assertions only"),
        "years": Assessment("years", "strong", ["f_built"], "", weight=2.0, rests_on="instances"),
        "spanish": Assessment("spanish", "strong", ["f_es"], "", weight=1.0,
                              rests_on="instances"),
    }
    rows.update(over)
    s = Screening(candidate_id="c", posting_id="p")
    s.assessments = list(rows.values())
    return s


def test_every_criterion_is_in_the_brief_including_the_settled_ones():
    ps = probes(screening(), agenda(), facts())
    assert {p.criterion_id for p in ps} == {"gate", "writing", "own", "years", "spanish"}


def test_an_open_declared_condition_comes_first():
    assert probes(screening(), agenda(), facts())[0].criterion_id == "gate"
    assert probes(screening(), agenda(), facts())[0].reasons == ["open gate"]


def test_why_each_criterion_is_open_is_read_from_the_record():
    by = {p.criterion_id: p for p in probes(screening(), agenda(), facts())}
    assert by["own"].reasons == ["self-description only"]
    assert by["writing"].reasons == ["the letter only"]
    assert by["years"].reasons == ["on paper"] and not by["years"].settle


def test_a_criterion_the_screener_wavered_on_is_open_even_if_strong():
    ps = probes(screening(), agenda(), facts(), wavered={"years": {"strong": 2, "moderate": 1}})
    years = next(p for p in ps if p.criterion_id == "years")
    assert years.settle and "moved between reads" in years.reasons


def test_settled_criteria_come_after_every_open_one():
    ps = probes(screening(), agenda(), facts())
    first_settled = next(i for i, p in enumerate(ps) if not p.settle)
    assert all(not p.settle for p in ps[first_settled:])


def design_for(*observes_per_exercise):
    return Design(posting_id="p", exercises=[
        Exercise(id=f"e{i}", title=f"Exercise {i}", responsibility_id="resp_build", minutes=60,
                 material="m", observes=[Observation(c, "s", "t") for c in obs])
        for i, obs in enumerate(observes_per_exercise)])


def test_the_conversation_asks_what_no_exercise_shows_before_confirming_anything():
    b = build(screening(), agenda(), facts(), design=design_for(("writing", "own")),
              slot_minutes=60)
    asked, _ = b.conversation()
    ids = [p.criterion_id for p in asked]
    assert ids.index("gate") < ids.index("years")
    assert "writing" not in ids and "own" not in ids


def test_a_claim_an_exercise_already_tests_is_asked_last():
    b = build(screening(), agenda(), facts(), design=design_for(("years",)), slot_minutes=60)
    ids = [p.criterion_id for p in b.conversation()[0]]
    assert ids[-1] == "years"


def test_what_does_not_fit_is_returned_not_dropped():
    b = build(screening(), agenda(), facts(), slot_minutes=10)
    asked, overflow = b.conversation()
    assert len(asked) == 2
    assert {p.criterion_id for p in asked + overflow} == {p.criterion_id for p in b.probes}
    assert "did not fit in 10 minutes" in b.render()


def test_a_language_is_confirmed_by_speaking_it_not_by_asking_about_it():
    b = build(screening(), agenda(), facts(), slot_minutes=60)
    assert "hold part of this conversation in it" in b.render()


def test_two_candidates_get_the_same_day():
    """Structured means the exercises do not move; only where to look does."""
    d = design_for(("gate", "writing"), ("own",))
    one = build(screening(), agenda(), facts(), design=d)
    two = build(screening(gate=Assessment("gate", "strong", ["f_built"], "", weight=2.0,
                                          hard=True, rests_on="instances")),
                agenda(), facts(), design=d)
    titles = lambda b: [l for l in b.render().splitlines() if "min  Exercise" in l]
    assert titles(one) == titles(two) and titles(one)


@pytest.mark.parametrize("word", ["reject", "hire", "shortlist", "not a fit", "unfortunately",
                                  "recommend "])
def test_the_brief_contains_no_decision(word):
    b = build(screening(), agenda(), facts(), design=design_for(("gate",)))
    assert word not in b.render().lower()


def test_without_a_design_the_brief_says_so_rather_than_improvising():
    assert "not designed yet" in build(screening(), agenda(), facts()).render()


def test_what_an_exchange_left_open_is_carried_into_the_brief():
    x = {"exchange": {"ledger": {"status": {"conviction": "withheld", "ships": "resolved"}}},
         "verdicts": {"criteria": [{"id": "conviction", "question": "Do they care?"}]}}
    b = build(screening(), agenda(), facts(), exchange=x)
    assert b.exchange_open == [{"id": "conviction", "status": "withheld",
                                "question": "Do they care?"}]


# --------------------------------------------------------------------------
# The card
# --------------------------------------------------------------------------

def blank():
    return card(build(screening(), agenda(), facts(), design=design_for(("gate",))))


def fill(text, cid, observed="", strength=""):
    out, cur = [], None
    for line in text.splitlines():
        if line.startswith("[criteria."):
            cur = line[len("[criteria."):-1]
        if cur == cid and line == 'observed = ""':
            line = f'observed = "{observed}"'
        if cur == cid and line == 'strength = ""':
            line = f'strength = "{strength}"'
        out.append(line)
    return "\n".join(out)


def test_the_blank_card_is_valid_toml_with_every_criterion():
    raw = tomllib.loads(blank())
    assert set(raw["criteria"]) == {"gate", "writing", "own", "years", "spanish"}
    assert raw["criteria"]["gate"]["seen_in"] == ["e0"]


def test_the_reviewer_is_not_a_field_on_the_card():
    """It is given at recording time, so a card cannot be filed under
    somebody else's name by editing it."""
    assert "reviewer" not in tomllib.loads(blank())


def test_a_blank_card_is_refused():
    with pytest.raises(CardError, match="nothing was observed"):
        read_card(blank(), agenda())


def test_a_strength_with_nothing_observed_is_refused():
    with pytest.raises(CardError, match="a strength with nothing observed"):
        read_card(fill(blank(), "gate", "", "strong"), agenda())


def test_an_observation_that_repeats_the_rubric_is_refused():
    text = fill(blank(), "gate", "Concrete examples of using AI agents to do real work", "strong")
    with pytest.raises(CardError, match="repeats the rubric"):
        read_card(text, agenda())


def test_an_observation_that_repeats_the_exercise_anchor_is_refused():
    d = Design(posting_id="p", exercises=[Exercise(
        id="e0", title="t", responsibility_id="resp_build", minutes=60, material="m",
        observes=[Observation("gate", "Directs the agent iteratively and fixes a wrong field",
                              "only theory")])])
    text = fill(blank(), "gate", "Directs the agent iteratively and fixes a wrong field", "strong")
    with pytest.raises(CardError, match="repeats the rubric"):
        read_card(text, agenda(), d)


def test_an_observation_touching_a_protected_subject_is_refused():
    text = fill(blank(), "own", "Mentioned her children were at home, so rushed", "weak")
    with pytest.raises(CardError, match="protected subject"):
        read_card(text, agenda())


def test_a_decision_in_the_note_is_refused():
    text = fill(blank(), "gate", "Script ran on 9 of 12 receipts", "strong")
    text = text.replace('note = ""', 'note = "We should hire her"')
    with pytest.raises(CardError, match="records a decision"):
        read_card(text, agenda())


def test_every_problem_is_reported_at_once():
    text = fill(fill(blank(), "gate", "", "strong"), "own", "", "bogus")
    with pytest.raises(CardError) as e:
        read_card(text, agenda())
    assert len(e.value.problems) >= 3


def test_an_observation_without_a_strength_is_kept_and_changes_nothing():
    head, entries = read_card(fill(blank(), "gate", "Spent the hour on setup"), agenda())
    e = next(x for x in entries if x.criterion_id == "gate")
    assert e.observed and not e.strength


def test_an_impression_outside_0_100_is_refused():
    text = fill(blank(), "gate", "Script ran on 9 of 12 receipts", "strong")
    with pytest.raises(CardError, match="outside 0-100"):
        read_card(text.replace('impression = ""', "impression = 140"), agenda())


# --------------------------------------------------------------------------
# The day against the paper
# --------------------------------------------------------------------------

def test_the_paper_side_comes_from_the_screening_not_from_the_card():
    """The card is a text file somebody edited. The comparison is only worth
    anything if the half it is compared against cannot have been."""
    tampered = [Entry("years", paper="unknown", why_open="", observed="x", strength="weak")]
    row = next(r for r in outcome(screening(), tampered, by="A")["rows"]
               if r["criterion_id"] == "years")
    assert row["paper"] == "strong"


def rows(*triples, capped=None):
    capped = capped or {}
    return {"candidate_id": "c", "rows": [
        {"criterion_id": f"k{i}", "paper": p, "day": d, "rests_on": r,
         "capped_from": capped.get(i, "")}
        for i, (p, d, r) in enumerate(triples)]}


def test_no_trial_day_says_what_it_cannot_measure():
    assert "cannot make on its own" in calibrate("p", []).render()


def test_a_day_that_did_not_show_it_agrees_with_nothing():
    c = calibrate("p", [rows(("strong", "unknown", "instances"))])
    assert c.rates[0].n == 0
    assert c.table["strong"]["unknown"] == 1


def test_an_overclaim_is_counted_only_on_criteria_established_by_an_occasion():
    c = calibrate("p", [rows(("strong", "moderate", "instances"),
                             ("moderate", "weak", "the letter only"))])
    over = next(r for r in c.rates if "overclaimed" in r.label)
    assert (over.k, over.n) == (1, 1)


def test_the_cap_is_charged_when_the_day_sees_what_the_screener_first_said():
    c = calibrate("p", [rows(("weak", "strong", "assertions only"),
                             ("weak", "weak", "assertions only"),
                             capped={0: "strong", 1: "moderate"})])
    cap = next(r for r in c.rates if "cap" in r.label)
    assert (cap.k, cap.n) == (1, 2)


def test_below_the_floor_a_count_is_printed_and_a_rate_is_not():
    c = calibrate("p", [rows(("strong", "strong", "instances"))])
    line = c.rates[0].render()
    assert "a count, not a rate" in line and "%" not in line


def test_at_the_floor_the_rate_comes_with_its_interval():
    c = calibrate("p", [rows(*[("strong", "strong", "instances")] * MIN_PAIRS)])
    line = c.rates[0].render()
    assert "100%" in line and "[" in line


def test_the_interval_is_honest_at_the_extremes():
    lo, hi = wilson(0, 10)
    assert lo == 0.0 and 0.25 < hi < 0.35
    lo, hi = wilson(10, 10)
    assert hi == 1.0 and 0.65 < lo < 0.75
