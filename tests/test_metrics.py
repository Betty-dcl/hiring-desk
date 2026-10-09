"""The promise, the funnel and the agreement, measured on an invented log.

Every figure a partner reads on the Tracking tab comes out of these
functions, so each one is checked against a number worked out by hand -- and
the ways a log goes wrong (a time zone, a missing arrival, a changed vote, a
merged person, a reply dated before the application) each have a case.
Voters are invented (Ana, Ben, Cy): no vote is ever attributed to a real name.
"""

from __future__ import annotations

import json

from datetime import datetime, timezone
from pathlib import Path

import pytest

import desk
import metrics
from desk import Config, Registry
from metrics import (Tracking, agreement, bootstrap, build_case, cases, cohen, fleiss,
                     funnel, instant, kaplan_meier, km_quantile, promise, quantile, share,
                     vote_time, wilson)
from pipeline import Event, Pipeline

REPO = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 6, 9, 0, tzinfo=timezone.utc)  # a Tuesday
VOTERS = ["Ana", "Ben", "Cy"]


def cfg(**kw) -> Config:
    return Config(voters=list(VOTERS), labels={"contact": "Interview", "later": "Keep on file",
                                               "discuss": "Discuss", "pass": "Not interested"},
                  **kw)


def ev(p: Pipeline, cid: str, kind: str, at: str, by: str = "", **kw) -> None:
    if kind in ("closed", "revisit", "flagged", "reopened") and "reason" not in kw:
        kw["reason"] = "invented"
    p.add(Event(candidate_id=cid, kind=kind, at=at, by=by, **kw))


def vote(p: Pipeline, cid: str, by: str, label: str, at: str) -> None:
    detail = {"label": label}
    if label == "later":
        detail["until"] = "2027-01-15"
    ev(p, cid, "voted", at, by, detail=detail)


def journal(naive: bool = True) -> tuple[dict[str, Pipeline], Registry]:
    """An invented desk, one case per way the log can surprise a metric.

    `naive=False` gives Carla a zoned arrival: the desk's own pages (desk.py,
    desk_web.py) compare times assuming every one has a zone.
    """
    fa, ops = Pipeline("fa"), Pipeline("ops")
    reg = Registry()

    # Answered in 3 days, agreed to meet, trial day, then closed.
    ev(fa, "alba", "received", "2026-09-01T10:00:00+00:00", detail={"added_by_hand": True})
    vote(fa, "alba", "Ana", "contact", "2026-09-02T10:00:00+00:00")
    vote(fa, "alba", "Ben", "contact", "2026-09-02T10:03:00+00:00")
    vote(fa, "alba", "Cy", "contact", "2026-09-03T10:00:00+00:00")
    ev(fa, "alba", "contacted", "2026-09-04T10:00:00+00:00", "Ana", detail={"about": "contact"})
    ev(fa, "alba", "met", "2026-09-20T10:00:00+00:00", "Ana", detail={"round": "trial_day"})
    ev(fa, "alba", "closed", "2026-09-25T10:00:00+00:00", "Ana")
    reg.add("alba", "fa", "Alba Ruiz", "alba@x.example")

    # Arrived at 12:00 in Madrid (10:00 UTC); disagreed; nobody wrote: late.
    ev(fa, "bruno", "received", "2026-09-01T12:00:00+02:00")
    vote(fa, "bruno", "Ana", "pass", "2026-09-02T10:06:00+00:00")
    vote(fa, "bruno", "Ben", "later", "2026-09-02T10:09:00+00:00")
    vote(fa, "bruno", "Cy", "pass", "2026-09-03T10:04:00+00:00")
    reg.add("bruno", "fa", "Bruno Sala", "bruno@x.example")

    # A timestamp with no time zone.
    ev(fa, "carla", "received", "2026-09-10T10:00:00" + ("" if naive else "+00:00"))
    reg.add("carla", "fa", "Carla Pons")

    # Acknowledged the same day, answered 20 days later.
    ev(fa, "dario", "received", "2026-09-10T10:00:00+00:00", tag="", detail={"source": "Ashby"})
    ev(fa, "dario", "contacted", "2026-09-10T11:00:00+00:00", "Ben", detail={"about": "received"})
    vote(fa, "dario", "Ana", "pass", "2026-09-11T10:00:00+00:00")
    vote(fa, "dario", "Ana", "later", "2026-09-11T10:02:00+00:00")   # changed her mind
    vote(fa, "dario", "Ben", "later", "2026-09-12T10:00:00+00:00")
    vote(fa, "dario", "Cy", "later", "2026-09-13T10:00:00+00:00")
    ev(fa, "dario", "contacted", "2026-09-30T10:00:00+00:00", "Ben", detail={"about": "later"})
    reg.add("dario", "fa", "Dario Vidal")

    # One person, two records for the same role, merged by a human.
    ev(fa, "elena", "received", "2026-09-20T10:00:00+00:00")
    ev(fa, "elena_2", "received", "2026-09-22T10:00:00+00:00")
    ev(fa, "elena_2", "contacted", "2026-09-25T10:00:00+00:00", "Cy", detail={"about": "pass"})
    reg.add("elena", "fa", "Elena Mora")
    reg.add("elena_2", "fa", "Elena Mora")
    reg.merge("elena", "elena_2", by="Cy")

    # Closed without a word: the silence itself.
    ev(ops, "fabio", "received", "2026-09-05T10:00:00+00:00", tag="")
    ev(ops, "fabio", "tagged", "2026-09-05T10:00:00+00:00", tag="source:referral")
    ev(ops, "fabio", "closed", "2026-09-12T10:00:00+00:00", "Ana")
    reg.add("fabio", "ops", "Fabio Gil")

    # Recent, unanswered: not yet late, and not in the cohort.
    ev(ops, "gala", "received", "2026-10-03T10:00:00+00:00")
    reg.add("gala", "ops", "Gala Soto")

    # A reply recorded before the application arrived.
    ev(ops, "hugo", "received", "2026-09-15T10:00:00+00:00")
    ev(ops, "hugo", "contacted", "2026-09-14T10:00:00+00:00", "Ben", detail={"about": "pass"})
    reg.add("hugo", "ops", "Hugo Rey")
    return {"fa": fa, "ops": ops}, reg


def by_name(cs):
    return {c.name: c for c in cs}


# --------------------------------------------------------------------------
# Time
# --------------------------------------------------------------------------

def test_the_same_instant_in_two_time_zones_is_the_same_instant():
    assert instant("2026-09-01T12:00:00+02:00") == instant("2026-09-01T10:00:00+00:00")
    assert instant("2026-09-01T10:00:00Z") == instant("2026-09-01T10:00:00+00:00")


def test_a_time_with_no_zone_is_unknown_unless_the_setting_says_utc():
    assert instant("2026-09-10T10:00:00") is None
    assert instant("2026-09-10") is None
    assert instant("2026-09-10T10:00:00", "utc") == datetime(2026, 9, 10, 10, tzinfo=timezone.utc)
    assert instant("not a date") is None and instant("") is None


def test_text_order_is_not_time_order_and_the_metric_uses_time():
    # "+02:00" at 11:00 is 09:00 UTC: earlier than 10:00 UTC, later as text.
    p = Pipeline("r")
    ev(p, "x", "received", "2026-09-01T10:00:00+00:00")
    ev(p, "x", "contacted", "2026-09-01T11:00:00+02:00", "Ana", detail={"about": "pass"})
    c = build_case("r", "x", "X", ["x"], p.events, cfg(), Tracking(), "")
    assert "clocks disagree" in " ".join(c.unknown) and c.answer_unknown


# --------------------------------------------------------------------------
# Statistics, against numbers worked out by hand
# --------------------------------------------------------------------------

def test_quantile_interpolates_between_order_statistics():
    assert quantile([1, 2, 3, 4], 0.5) == 2.5
    assert quantile([10], 0.9) == 10
    assert quantile([], 0.5) is None
    assert quantile([0, 10], 0.9) == pytest.approx(9.0)


def test_wilson_interval_matches_the_textbook_value():
    lo, hi = wilson(8, 10)
    assert lo == pytest.approx(0.4902, abs=1e-3) and hi == pytest.approx(0.9433, abs=1e-3)
    assert wilson(0, 0) is None
    assert wilson(0, 5)[0] == pytest.approx(0.0)


def test_kaplan_meier_counts_the_waiting_as_at_least_that_long():
    # Answered at 2, 4; still waiting at 3 and 10; answered at 6.
    curve = kaplan_meier([(2, True), (3, False), (4, True), (6, True), (10, False)])
    assert curve[0] == (0.0, 1.0)
    assert curve[1] == (2, pytest.approx(0.8))
    assert curve[2] == (4, pytest.approx(0.8 * 2 / 3))
    assert curve[3] == (6, pytest.approx(0.8 * 2 / 3 * 1 / 2))
    # S(4) = 0.53 is still above one half: the median is 6. Dropping the two
    # still waiting would have given 4 -- faster than the truth, which is
    # the error this estimator exists to avoid. 90% is never reached.
    assert km_quantile(curve, 0.5) == 6
    assert quantile([2, 4, 6], 0.5) == 4
    assert km_quantile(curve, 0.9) is None


def test_cohen_kappa_on_the_textbook_table():
    # 50 items: yes/yes 20, yes/no 5, no/yes 10, no/no 15 -> kappa 0.4.
    pairs = [("y", "y")] * 20 + [("y", "n")] * 5 + [("n", "y")] * 10 + [("n", "n")] * 15
    assert cohen(pairs, "yn") == pytest.approx(0.4)


def test_kappa_is_undefined_when_both_only_ever_used_one_class():
    assert cohen([("pass", "pass")] * 4) is None
    assert cohen([]) is None


def test_fleiss_kappa_on_the_textbook_table():
    # Fleiss (1971) as reproduced on Wikipedia: 10 subjects, 14 raters, 5 categories.
    rows = [[0, 0, 0, 0, 14], [0, 2, 6, 4, 2], [0, 0, 3, 5, 6], [0, 3, 9, 2, 0], [2, 2, 8, 1, 1],
            [7, 7, 0, 0, 0], [3, 2, 6, 3, 0], [2, 5, 3, 2, 2], [6, 5, 2, 1, 0], [0, 2, 2, 3, 7]]
    items = [[str(j) for j, k in enumerate(r) for _ in range(k)] for r in rows]
    assert fleiss(items, "01234") == pytest.approx(0.210, abs=5e-4)


def test_the_bootstrap_is_reproducible_and_refuses_one_item():
    pairs = [("contact", "contact"), ("pass", "pass"), ("pass", "later"), ("later", "later"),
             ("contact", "discuss"), ("pass", "pass")]
    a = bootstrap(pairs, cohen, samples=500, confidence=0.95)
    assert a == bootstrap(pairs, cohen, samples=500, confidence=0.95)
    assert a[0] <= cohen(pairs) <= a[1]
    assert bootstrap(pairs[:1], cohen, samples=500, confidence=0.95) is None


# --------------------------------------------------------------------------
# One application per person and role
# --------------------------------------------------------------------------

def test_two_merged_records_for_one_role_are_one_application():
    pipes, reg = journal()
    cs = by_name(cases(pipes, reg, cfg(), Tracking()))
    elena = cs["Elena Mora"]
    assert sorted(elena.candidate_ids) == ["elena", "elena_2"]
    # The clock starts at the first arrival; the answer on the other record counts.
    assert elena.received == datetime(2026, 9, 20, 10, tzinfo=timezone.utc)
    assert elena.answered == datetime(2026, 9, 25, 10, tzinfo=timezone.utc)
    assert len([c for c in cs.values() if c.name == "Elena Mora"]) == 1


def test_where_an_application_came_from():
    pipes, reg = journal()
    cs = by_name(cases(pipes, reg, cfg(), Tracking()))
    assert cs["Alba Ruiz"].source == "by hand"
    assert cs["Dario Vidal"].source == "ashby"
    assert cs["Fabio Gil"].source == "referral"
    assert cs["Hugo Rey"].source == "not recorded"


def test_ashbys_own_record_marks_an_application_as_ashbys(tmp_path):
    (tmp_path / "runs" / "desk").mkdir(parents=True)
    (tmp_path / "runs" / "desk" / "ashby.json").write_text(json.dumps(
        {"applications": {"a1": {"posting": "ops", "person_id": "gala"}}}), encoding="utf-8")
    pipes, reg = journal()
    cs = by_name(cases(pipes, reg, cfg(), Tracking(), root=tmp_path))
    assert cs["Gala Soto"].source == "ashby"


# --------------------------------------------------------------------------
# The promise
# --------------------------------------------------------------------------

def report_on_journal(**kw):
    pipes, reg = journal()
    t = Tracking(**kw)
    return promise(cases(pipes, reg, cfg(), t), t, NOW)


def test_first_answers_are_measured_in_calendar_days():
    pr = report_on_journal()
    got = {c.name: round(d, 3) for c, d in pr.answered}
    assert got == {"Alba Ruiz": 3.0, "Dario Vidal": 20.0, "Elena Mora": 5.0}
    assert pr.to_answer.median == 5.0 and pr.to_answer.max == 20.0
    assert pr.to_answer.p90 == pytest.approx(17.0)


def test_the_acknowledgment_is_a_receipt_not_the_answer():
    pr = report_on_journal()
    assert dict((c.name, d) for c, d in pr.answered)["Dario Vidal"] == pytest.approx(20)
    pr = report_on_journal(acknowledgment_counts=True)
    assert dict((c.name, d) for c, d in pr.answered)["Dario Vidal"] == pytest.approx(1 / 24)


def test_unknown_is_said_and_never_guessed():
    pr = report_on_journal()
    why = {c.name: w for c, w in pr.unknown}
    assert "time zone" in why["Carla Pons"]
    assert "clocks disagree" in why["Hugo Rey"]
    assert "Carla Pons" not in {c.name for c, _ in pr.waiting + pr.answered}
    # Told to read a bare time as UTC, Carla becomes an ordinary late application.
    pr = report_on_journal(naive_timestamps="utc")
    assert "Carla Pons" in {c.name for c, _ in pr.late_open}


def test_closed_without_a_word_is_still_unanswered_and_named():
    pr = report_on_journal()
    assert [c.name for c in pr.silent_closes] == ["Fabio Gil"]
    assert "Fabio Gil" in {c.name for c, _ in pr.late_open}


def test_everyone_past_the_promise_is_named_longest_first():
    pr = report_on_journal()
    late = [(c.name, round(d)) for c, d in pr.late_open]
    assert late == [("Bruno Sala", 35), ("Fabio Gil", 31)]
    assert [c.name for c, _ in pr.late_answered] == ["Dario Vidal"]


def test_the_share_on_time_is_over_applications_old_enough_to_tell():
    pr = report_on_journal()
    # Old enough (>= 14 days): Alba, Bruno, Dario, Elena, Fabio. Gala is 3 days old.
    assert pr.cohort == 5 and pr.on_time == 2
    assert "Gala Soto" in {c.name for c, _ in pr.waiting}


def test_the_waiting_move_the_median_the_answered_alone_would_hide():
    pr = report_on_journal()
    # Among the answered, the median is 5 days; with Bruno (35), Fabio (31)
    # and Gala (3) still waiting, half have been answered only by day 20.
    assert pr.to_answer.median == 5.0
    assert pr.km_median == pytest.approx(20.0)
    assert pr.longest == pytest.approx(35.0, abs=0.05)


def test_time_to_a_complete_vote_and_to_agreement():
    pr = report_on_journal()
    # Alba: all voted and agreed 2 days after arriving. Bruno: complete after
    # 2 days, never agreed. Dario: 3 days to both.
    assert sorted(round(x, 3) for x in [pr.to_vote.median, pr.to_vote.max]) == [2.003, 3.0]
    assert pr.to_agree.n == 2 and pr.to_agree.max == pytest.approx(3.0)
    assert pr.not_agreed >= 1


def test_majority_agrees_before_everyone_has_voted():
    p = Pipeline("r")
    ev(p, "x", "received", "2026-09-01T10:00:00+00:00")
    vote(p, "x", "Ana", "contact", "2026-09-02T10:00:00+00:00")
    vote(p, "x", "Ben", "contact", "2026-09-03T10:00:00+00:00")
    c = build_case("r", "x", "X", ["x"], p.events, cfg(agreement="majority"), Tracking(), "")
    assert c.agreed == datetime(2026, 9, 3, 10, tzinfo=timezone.utc) and c.all_voted is None


def test_an_agreement_undone_by_a_changed_vote_keeps_its_date_not_its_meaning():
    p = Pipeline("r")
    ev(p, "x", "received", "2026-09-01T10:00:00+00:00")
    for v in VOTERS:
        vote(p, "x", v, "contact", "2026-09-02T10:00:00+00:00")
    vote(p, "x", "Cy", "pass", "2026-09-04T10:00:00+00:00")
    c = build_case("r", "x", "X", ["x"], p.events, cfg(), Tracking(), "")
    assert c.agreed is not None and c.agreed_meaning is None and c.changed_votes == 1


def test_a_vote_from_someone_no_longer_voting_is_not_counted():
    p = Pipeline("r")
    ev(p, "x", "received", "2026-09-01T10:00:00+00:00")
    vote(p, "x", "Dee", "contact", "2026-09-02T10:00:00+00:00")
    c = build_case("r", "x", "X", ["x"], p.events, cfg(), Tracking(), "")
    assert c.first_votes == {} and c.all_voted is None


# --------------------------------------------------------------------------
# The funnel
# --------------------------------------------------------------------------

def test_the_funnel_counts_each_step_and_the_after():
    pipes, reg = journal()
    t = Tracking()
    one = cfg(interviews=1)
    rows = funnel(cases(pipes, reg, one, t), one)
    assert rows[0].counts == {"received": 8, "voted": 3, "to_contact": 1, "contacted": 1, "replied": 1,
                              "first_call": 1, "trial_day": 1, "offer": 0, "hired": 0}
    # Alba's trial day has no first call recorded before it: reached, and said.
    assert rows[0].inferred == 1
    assert rows[0].after_trial == {"closed": 1, "open": 0}


def test_the_funnel_by_role_and_by_source():
    pipes, reg = journal()
    cs = cases(pipes, reg, cfg(), Tracking())
    by_role = {r.group: r.counts for r in funnel(cs, cfg(), lambda c: c.posting_id)}
    assert by_role["ops"]["received"] == 3 and by_role["fa"]["received"] == 5
    by_src = {r.group: r.counts for r in funnel(cs, cfg(interviews=1), lambda c: c.source)}
    assert by_src["ashby"] == {"received": 1, "voted": 1, "to_contact": 0, "contacted": 0, "replied": 0,
                               "first_call": 0, "trial_day": 0, "offer": 0, "hired": 0}


def test_a_trial_day_with_no_recorded_vote_is_counted_and_said_to_be_inferred():
    p = Pipeline("r")
    ev(p, "x", "received", "2026-09-01T10:00:00+00:00")
    ev(p, "x", "met", "2026-09-10T10:00:00+00:00", "Ana", detail={"round": "trial_day"})
    c = build_case("r", "x", "X", ["x"], p.events, cfg(), Tracking(), "")
    row = funnel([c], cfg(interviews=1))[0]
    assert row.counts == {"received": 1, "voted": 1, "to_contact": 1, "contacted": 1, "replied": 1,
                          "first_call": 1, "trial_day": 1, "offer": 0, "hired": 0}
    assert row.inferred == 1


def test_the_funnel_has_one_column_per_interview_the_team_runs():
    p = Pipeline("r")
    ev(p, "x", "received", "2026-09-01T10:00:00+00:00")
    ev(p, "x", "met", "2026-09-10T10:00:00+00:00", "Ana", detail={"round": "interview_2"})
    three = cfg(interviews=3)
    row = funnel([build_case("r", "x", "X", ["x"], p.events, three, Tracking(), "")], three)[0]
    assert list(row.counts) == ["received", "voted", "to_contact", "contacted", "replied", "first_call",
                                "interview_2", "interview_3", "trial_day", "offer", "hired"]
    assert (row.counts["interview_2"], row.counts["interview_3"]) == (1, 0)
    # An interview recorded beyond the team's number still counts for what came before.
    one = cfg(interviews=1)
    row = funnel([build_case("r", "x", "X", ["x"], p.events, one, Tracking(), "")], one)[0]
    assert "interview_2" not in row.counts and row.counts["first_call"] == 1


def test_no_percentage_under_the_minimum():
    t = Tracking(min_n_percent=5)
    assert share(1, 4, t) == "n too small (1/4)"
    assert share(2, 5, t) == "40%"
    assert share(0, 0, t) == "--"


# --------------------------------------------------------------------------
# Agreement
# --------------------------------------------------------------------------

def test_agreement_is_measured_on_the_blind_first_vote_by_default():
    pipes, reg = journal()
    cs = cases(pipes, reg, cfg(), Tracking())
    first = agreement(cs, cfg(), Tracking())
    ab = next(p for p in first.pairs if (p.a, p.b) == ("Ana", "Ben"))
    # Alba contact/contact, Bruno pass/later, Dario pass (first) / later.
    assert ab.n == 3 and ab.table[("pass", "later")] == 2 and ab.observed == pytest.approx(1 / 3)
    latest = agreement(cs, cfg(), Tracking(agreement_votes="latest"))
    ab2 = next(p for p in latest.pairs if (p.a, p.b) == ("Ana", "Ben"))
    assert ab2.table[("later", "later")] == 1 and ab2.observed == pytest.approx(2 / 3)
    assert first.changed_votes == 1


def test_pairs_come_in_the_teams_order_never_sorted_by_score():
    pipes, reg = journal()
    ag = agreement(cases(pipes, reg, cfg(), Tracking()), cfg(), Tracking())
    assert [(p.a, p.b) for p in ag.pairs] == [("Ana", "Ben"), ("Ana", "Cy"), ("Ben", "Cy")]


def test_the_discuss_rate_and_fleiss_are_over_fully_voted_applications():
    pipes, reg = journal()
    ag = agreement(cases(pipes, reg, cfg(), Tracking()), cfg(), Tracking())
    assert ag.settled == 3 and ag.to_discuss == 1
    assert ag.fleiss_n == 3 and ag.fleiss is not None


def test_small_n_is_flagged_against_the_setting():
    pipes, reg = journal()
    ag = agreement(cases(pipes, reg, cfg(), Tracking()), cfg(), Tracking(min_n_agreement=10))
    assert all(p.n < ag.min_n for p in ag.pairs)
    text = metrics.render(metrics.report(pipes, reg, cfg(), Tracking(), NOW))
    assert "too few to read" in text


# --------------------------------------------------------------------------
# Time to class
# --------------------------------------------------------------------------

def test_too_few_timed_votes_falls_back_to_the_stated_assumption():
    pipes, reg = journal()
    vt = vote_time(pipes, cfg(), Tracking(min_timed_votes=50, minutes_per_vote=2))
    assert vt.basis == "assumed" and vt.minutes == 2
    assert "assuming 2 min a vote" in vt.estimate(3)


def test_the_time_to_class_is_the_median_gap_in_one_sitting():
    pipes, reg = journal()
    vt = vote_time(pipes, cfg(), Tracking(min_timed_votes=2))
    # Gaps within 20 minutes, first votes only: Ana 10:00 -> 10:06, Ben
    # 10:03 -> 10:09, Cy 10:00 -> 10:04. Ana's 10:00 -> 10:02 on 11/09 is a
    # changed vote, and does not count.
    assert vt.basis == "observed" and vt.samples == 3 and vt.minutes == 6.0


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

def test_the_shipped_settings_file_has_valid_tracking_settings():
    t = metrics.load_tracking(REPO / "desk.toml")
    assert t.answer_within_days == 14 and t.agreement_votes == "first"


@pytest.mark.parametrize("bad", [{"answer_within_days": 0}, {"naive_timestamps": "local"},
                                 {"agreement_votes": "mean"}, {"confidence": 1.0},
                                 {"link_ttl_hours": 0}])
def test_a_tracking_setting_that_cannot_run_is_refused(bad):
    with pytest.raises(desk.DeskError):
        Tracking(**bad)


def test_an_unknown_tracking_setting_is_refused_not_ignored(tmp_path):
    f = tmp_path / "desk.toml"
    f.write_text("[tracking]\nanswer_within_dayz = 7\n", encoding="utf-8")
    with pytest.raises(desk.DeskError, match="answer_within_dayz"):
        metrics.load_tracking(f)


def test_the_report_reads_as_text_and_as_json():
    pipes, reg = journal()
    r = metrics.report(pipes, reg, cfg(), Tracking(), NOW, {"fa": "Founders' Associate"})
    text = metrics.render(r)
    assert "Bruno Sala -- fa, 35 days" in text and "Founders' Associate" in text
    d = metrics.to_dict(r)
    json.dumps(d)
    assert d["promise"]["cohort"] == 5 and d["promise"]["late"][0]["name"] == "Bruno Sala"


# --------------------------------------------------------------------------
# A desk on disk, for the recap, the links and the web page
# --------------------------------------------------------------------------

@pytest.fixture
def home(tmp_path, monkeypatch):
    from pipeline import save
    monkeypatch.setattr(desk, "ROOT", tmp_path)
    monkeypatch.delenv("NBH_LINK_SECRET", raising=False)
    text = (REPO / "desk.toml").read_text(encoding="utf-8").replace(
        'voters = ["Recruiter"]', 'voters = ["Ana", "Ben", "Cy"]')
    (tmp_path / "desk.toml").write_text(text, encoding="utf-8")
    pipes, reg = journal(naive=False)
    for pid, p in pipes.items():
        save(p, tmp_path / "runs" / "pipeline" / f"{pid}.json")
    desk.save_registry(reg, tmp_path / "runs" / "desk" / "people.json")
    gen = tmp_path / "mandates" / "generated"
    gen.mkdir(parents=True)
    for pid, title in (("fa", "Founders' Associate"), ("ops", "Operations")):
        (gen / f"role_{pid}.provenance.json").write_text(
            json.dumps({"posting_id": pid, "title": title}), encoding="utf-8")
    return tmp_path


def test_the_desk_on_disk_loads_with_invented_voters(home):
    r = metrics.load_report(NOW)
    assert r.cfg.voters == VOTERS and len(r.cases) == 8




def test_a_pairs_votes_on_an_application_a_third_has_not_classed_stay_out():
    # Ben and Cy class gala; Ana has not. Under the blind rule the Tracking
    # tab, which Ana reads, must not move: a changed Ben/Cy table would tell
    # her how they classed it.
    pipes, reg = journal()
    t = Tracking(bootstrap=200)
    before = agreement(cases(pipes, reg, cfg(), t), cfg(), t)
    vote(pipes["ops"], "gala", "Ben", "pass", "2026-10-04T10:00:00+00:00")
    vote(pipes["ops"], "gala", "Cy", "contact", "2026-10-04T10:05:00+00:00")
    after = agreement(cases(pipes, reg, cfg(), t), cfg(), t)
    assert [(p.n, p.table) for p in after.pairs] == [(p.n, p.table) for p in before.pairs]
    assert after.used == before.used
    # With the blind rule off, nothing is hidden, so it counts.
    off = agreement(cases(pipes, reg, cfg(blind="off"), t), cfg(blind="off"), t)
    assert off.pairs[2].n == before.pairs[2].n + 1


# --------------------------------------------------------------------------
# Vote links
# --------------------------------------------------------------------------

import threading  # noqa: E402
import time  # noqa: E402

import tuesday  # noqa: E402
import votelink  # noqa: E402

KEY = b"k" * 40


def real_now() -> datetime:
    return datetime.now(timezone.utc)


def gala_votes() -> dict:
    return desk.votes(desk._pipelines()["ops"].standing("gala"))


def test_a_link_altered_or_signed_elsewhere_is_refused():
    tok = votelink.mint("Ana", "ops", "gala", "contact", cfg(), ttl_hours=1, key=KEY)
    assert votelink.read(tok, cfg(), key=KEY).voter == "Ana"
    body, _, sig = tok.partition(".")
    forged = votelink._b64(json.dumps({**json.loads(votelink._unb64(body)), "m": "pass"})
                           .encode())
    with pytest.raises(votelink.LinkError, match="altered"):
        votelink.read(f"{forged}.{sig}", cfg(), key=KEY)
    with pytest.raises(votelink.LinkError, match="altered"):
        votelink.read(tok, cfg(), key=b"x" * 40)
    with pytest.raises(votelink.LinkError, match="not a desk link"):
        votelink.read("nodot", cfg(), key=KEY)


def test_a_link_expires():
    tok = votelink.mint("Ana", "ops", "gala", "contact", cfg(), ttl_hours=2, now=NOW, key=KEY)
    votelink.read(tok, cfg(), now=NOW, key=KEY)
    with pytest.raises(votelink.LinkError, match="expired"):
        votelink.read(tok, cfg(), now=datetime(2026, 10, 6, 11, 0, tzinfo=timezone.utc),
                      key=KEY)


def test_a_link_for_someone_who_no_longer_votes_is_refused():
    tok = votelink.mint("Cy", "ops", "gala", "contact", cfg(), ttl_hours=1, key=KEY)
    with pytest.raises(votelink.LinkError, match="no longer votes"):
        votelink.read(tok, Config(voters=["Ana", "Ben"], labels=cfg().labels), key=KEY)


def test_a_short_secret_in_the_environment_is_refused(monkeypatch):
    monkeypatch.setenv("NBH_LINK_SECRET", "short")
    with pytest.raises(votelink.LinkError, match="too short"):
        votelink.secret()


def test_a_link_votes_once(home):
    c = desk.load_config()
    tok = votelink.mint("Ana", "ops", "gala", "contact", c, ttl_hours=1)
    link, _ = votelink.redeem(tok, c)
    assert link.voter == "Ana" and gala_votes()["Ana"].meaning == "contact"
    with pytest.raises(votelink.LinkError, match="already been used"):
        votelink.redeem(tok, c)
    assert votelink.is_used(link)


def test_a_link_casts_its_own_class_and_only_as_its_voter(home):
    c = desk.load_config()
    tok = votelink.mint("Ana", "ops", "gala", "contact", c, ttl_hours=1)
    with pytest.raises(votelink.LinkError, match="another class"):
        votelink.redeem(tok, c, meaning="pass")
    with pytest.raises(votelink.LinkError, match="Ana's"):
        votelink.redeem(tok, c, viewer="Ben")
    anyc = votelink.mint("Ana", "ops", "gala", votelink.ANY, c, ttl_hours=1)
    with pytest.raises(votelink.LinkError, match="choose"):
        votelink.redeem(anyc, c)
    assert "Ana" not in gala_votes()
    votelink.redeem(anyc, c, meaning="discuss")
    assert gala_votes()["Ana"].meaning == "discuss"


def test_a_vote_the_desk_refuses_leaves_the_link_unspent(home):
    c = desk.load_config()
    tok = votelink.mint("Ana", "ops", "gala", "later", c, ttl_hours=1)
    with pytest.raises(desk.DeskError):
        votelink.redeem(tok, c, until="soon")         # not a date
    votelink.redeem(tok, c, until="2027-01-15")
    assert gala_votes()["Ana"].meaning == "later"


def test_an_unreadable_record_of_used_links_reopens_none(home):
    c = desk.load_config()
    tok = votelink.mint("Ana", "ops", "gala", "contact", c, ttl_hours=1)
    votelink._used_path().parent.mkdir(parents=True, exist_ok=True)
    votelink._used_path().write_text("{not json", encoding="utf-8")
    with pytest.raises(votelink.LinkError, match="unreadable"):
        votelink.redeem(tok, c)
    assert "Ana" not in gala_votes()


def test_two_clicks_at_once_cast_one_vote(home, monkeypatch):
    c = desk.load_config()
    tok = votelink.mint("Ana", "ops", "gala", "contact", c, ttl_hours=1)
    real = desk.cast

    def slow_cast(*a, **kw):
        time.sleep(0.2)
        return real(*a, **kw)
    monkeypatch.setattr(desk, "cast", slow_cast)
    got: list[str] = []

    def click():
        try:
            votelink.redeem(tok, c)
            got.append("ok")
        except votelink.LinkError:
            got.append("refused")
    ts = [threading.Thread(target=click) for _ in range(2)]
    for th in ts:
        th.start()
    for th in ts:
        th.join()
    assert sorted(got) == ["ok", "refused"]
    events = [x for x in desk._pipelines()["ops"].standing("gala").events if x.kind == "voted"]
    assert len(events) == 1


# --------------------------------------------------------------------------
# The recap
# --------------------------------------------------------------------------

def test_the_recap_carries_links_for_its_reader_only(home):
    c, t = desk.load_config(), metrics.load_tracking()
    pipes, reg = desk._pipelines(), desk.load_registry(desk._registry_path())
    w = tuesday.build(pipes, reg, c, t, viewer="Ben", now=real_now())
    text = tuesday.render_text(w, c, tuesday.links_for("Ben", c, t, now=real_now()))
    toks = [ln.strip().rsplit("/v/", 1)[1] for ln in text.splitlines() if "/v/" in ln]
    assert toks and {votelink.read(x, c).voter for x in toks} == {"Ben"}
    assert text.splitlines()[2].startswith("Who has what")
    assert "You -- " in text and "LATE" not in text and "promise" not in text.lower()


def test_the_recap_never_shows_a_vote_still_hidden_from_its_reader(home):
    c, t = desk.load_config(), metrics.load_tracking()
    pipes = desk._pipelines()
    desk.cast(pipes["ops"], "gala", "Ben", "pass", c, reason="not this role",
              comment="thin on writing")
    reg = desk.load_registry(desk._registry_path())
    w = tuesday.build(pipes, reg, c, t, viewer="Ana", now=real_now())
    text = tuesday.render_text(w, c)
    assert "Gala Soto" in text
    for leak in ("not this role", "thin on writing", "Ben Not interested"):
        assert leak not in text


def test_the_recap_is_an_unsent_draft(home):
    c, t = desk.load_config(), metrics.load_tracking()
    w = tuesday.build(desk._pipelines(), desk.load_registry(desk._registry_path()), c, t,
                      viewer="Ana", now=real_now())
    m = tuesday.draft(w, c, None, to="ana@x.example")
    assert m["X-Unsent"] == "1" and m["To"] == "ana@x.example"
    assert m["Subject"].startswith("Hiring desk")


# --------------------------------------------------------------------------
# The pages, through the real server
# --------------------------------------------------------------------------

import http.client  # noqa: E402
import re  # noqa: E402
from urllib.parse import urlencode  # noqa: E402


@pytest.fixture
def web(home, monkeypatch):
    from console import desk_web, security
    made: list = []

    class Server(security.Server):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            made.append(self)
    monkeypatch.setattr(security, "Server", Server)

    def start(access: desk.Access | None = None):
        th = threading.Thread(target=desk_web.serve, daemon=True,
                              args=("127.0.0.1", 0, desk.load_config(),
                                    access or desk.Access()))
        th.start()
        for _ in range(500):
            if made:
                break
            time.sleep(0.01)
        port = made[-1].server_address[1]

        def call(method: str, path: str, form: dict | None = None,
                 headers: dict | None = None):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            body = urlencode(form).encode() if form is not None else None
            h = {"Content-Type": "application/x-www-form-urlencoded", **(headers or {})}
            conn.request(method, path, body=body, headers=h)
            r = conn.getresponse()
            out = (r.status, r.read().decode("utf-8", "replace"), dict(r.getheaders()))
            conn.close()
            return out
        return call
    yield start
    for s in made:
        s.shutdown()
        s.server_close()


def form_token(page: str) -> str:
    return re.search(r'name="t" value="([^"]+)"', page).group(1)


def test_the_overview_is_whole_numbers_by_role_and_no_pressure(web):
    call = web()
    code, page, _ = call("GET", "/tracking")
    assert code == 200 and 'href="/tracking' in page
    lead = page[page.index("<h1>Overview</h1>"):].split("<footer")[0]
    # For information: counts by role. Nobody is named as waiting, nothing says hurry.
    assert "Applications, by role" in lead
    assert "still waiting" not in lead and "Bruno Sala" not in lead
    # Whole numbers only: no chart, no percentage, no statistic.
    for gone in ('role="img"', "kappa", "Kaplan", "%", "n too small", "<svg", "<script"):
        assert gone not in lead, gone
    for col in ("Applied", "Voted", "Ready to contact", "Contacted", "First call", "Trial day",
                "In the pool", "Answered no"):
        assert f"<th scope=col>{col}</th>" in lead, col
    assert "<th scope=row>Total</th>" in lead


def test_the_recap_page_and_its_draft(web):
    call = web()
    code, page, _ = call("GET", "/recap?as=Ben")
    assert code == 200 and "/v/" in page
    toks = re.findall(r'href="/v/([^"]+)"', page)
    assert toks and {votelink.read(x, desk.load_config()).voter for x in toks} == {"Ben"}
    code, eml, h = call("GET", "/recap.eml?as=Ben")
    assert code == 200 and h["Content-Type"] == "message/rfc822" and "X-Unsent: 1" in eml


def test_opening_a_link_records_nothing_and_the_button_votes_once(web):
    call = web()
    tok = votelink.mint("Ana", "ops", "gala", votelink.ANY, desk.load_config(), ttl_hours=1)
    code, page, h = call("GET", f"/v/{tok}")
    assert code == 200 and "Nothing was recorded" in page and h["Cache-Control"] == "no-store"
    assert "Ana" not in gala_votes()
    f = {"t": form_token(page), "as": "Ana", "label": "contact"}
    code, _, h = call("POST", f"/v/{tok}", f)
    assert code == 303 and h["Location"].startswith("/person/")
    assert gala_votes()["Ana"].meaning == "contact"
    code, page, _ = call("POST", f"/v/{tok}", f)
    assert code == 400 and "already been used" in page
    code, _, _ = call("GET", f"/v/{tok}")
    assert code == 410


def test_a_link_post_without_the_desks_form_token_is_refused(web):
    call = web()
    tok = votelink.mint("Ana", "ops", "gala", "contact", desk.load_config(), ttl_hours=1)
    code, _, _ = call("POST", f"/v/{tok}", {"label": "contact"})
    assert code == 403 and "Ana" not in gala_votes()


def test_behind_a_proxy_a_forwarded_link_does_not_vote_as_its_owner(web):
    call = web(desk.Access(mode="header", emails={"ana@x.example": "Ana",
                                                  "ben@x.example": "Ben"},
                           trusted_proxies=["127.0.0.1"]))
    tok = votelink.mint("Ana", "ops", "gala", "contact", desk.load_config(), ttl_hours=1)
    ben = {"X-Forwarded-Email": "ben@x.example"}
    code, page, _ = call("GET", f"/v/{tok}", headers=ben)
    assert code == 403 and "Ana&#x27;s" in page
    _, desk_page, _ = call("GET", "/", headers=ben)
    t = form_token(desk_page)
    code, _, _ = call("POST", f"/v/{tok}", {"t": t, "label": "contact"}, headers=ben)
    assert code == 400 and "Ana" not in gala_votes()
    ana = {"X-Forwarded-Email": "ana@x.example"}
    # Ben's form token is bound to Ben: it does not vote as Ana either.
    code, _, _ = call("POST", f"/v/{tok}", {"t": t, "label": "contact"}, headers=ana)
    assert code == 403 and "Ana" not in gala_votes()
    _, ana_page, _ = call("GET", "/", headers=ana)
    t = form_token(ana_page)
    code, _, _ = call("POST", f"/v/{tok}", {"t": t, "label": "contact"}, headers=ana)
    assert code == 303 and gala_votes()["Ana"].meaning == "contact"
