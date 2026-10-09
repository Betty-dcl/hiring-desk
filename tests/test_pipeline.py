"""The record of where each application stands.

Two things are being protected here. One: the machine must never be able to
claim a human acted -- read it, met them, closed it. Two: an application that
nobody has touched must surface, because that silence is the failure the
whole project is named after.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from pipeline import Event, Pipeline, PipelineError, Standing


NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


def days_ago(n: float) -> str:
    return (NOW - timedelta(days=n)).isoformat()


def pipe(*events: Event, stale: float = 7.0) -> Pipeline:
    p = Pipeline(posting_id="p", stale_after_days=stale)
    for e in events:
        p.add(e)
    return p


def ev(cid, kind, when=0.0, **kw) -> Event:
    return Event(candidate_id=cid, kind=kind, at=days_ago(when), **kw)


# --------------------------------------------------------------------------
# What the machine is not allowed to say
# --------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["read", "flagged", "contacted", "met", "closed", "reopened"])
def test_a_human_only_event_without_a_person_is_refused(kind):
    with pytest.raises(PipelineError, match="needs `by`|does not get to claim"):
        Event(candidate_id="c", kind=kind, reason="r")


@pytest.mark.parametrize("kind", ["flagged", "closed", "reopened"])
def test_ending_or_promoting_without_a_reason_is_refused(kind):
    with pytest.raises(PipelineError, match="not a record, it is a shrug"):
        Event(candidate_id="c", kind=kind, by="iris_vogel")


def test_the_machine_may_record_that_it_scored_something():
    """`screened` is the machine's own act, so it needs no person."""
    e = Event(candidate_id="c", kind="screened", detail={"score": 0.42})
    assert e.by == ""


def test_an_invented_event_kind_is_refused():
    with pytest.raises(PipelineError, match="unknown event"):
        Event(candidate_id="c", kind="rejected")


# --------------------------------------------------------------------------
# State, derived rather than stored
# --------------------------------------------------------------------------

def test_an_application_nobody_has_opened_is_new():
    assert Standing("c").state == "new"


def test_state_is_the_furthest_thing_that_happened():
    s = pipe(ev("c", "received", 9), ev("c", "screened", 9),
             ev("c", "read", 5, by="iris_vogel")).standing("c")
    assert s.state == "read" and s.seen and s.read_by == ["iris_vogel"]


def test_closing_wins_over_everything_before_it():
    s = pipe(ev("c", "contacted", 8, by="a"), ev("c", "met", 6, by="a"),
             ev("c", "closed", 2, by="a", reason="took another offer")).standing("c")
    assert s.state == "closed"


def test_reopening_restores_what_had_been_reached():
    s = pipe(ev("c", "contacted", 9, by="a"),
             ev("c", "closed", 5, by="a", reason="paused the role"),
             ev("c", "reopened", 2, by="a", reason="role is funded again"),
             ev("c", "met", 1, by="a")).standing("c")
    assert s.state == "met"


def test_a_closed_application_that_is_reopened_and_left_alone_is_not_still_closed():
    s = pipe(ev("c", "closed", 9, by="a", reason="no budget"),
             ev("c", "reopened", 1, by="a", reason="budget")).standing("c")
    assert s.state == "new"


# --------------------------------------------------------------------------
# Tags
# --------------------------------------------------------------------------

def test_tags_accumulate_and_can_be_removed():
    s = pipe(ev("c", "tagged", 5, tag="madrid"), ev("c", "tagged", 4, tag="senior"),
             ev("c", "untagged", 1, tag="madrid")).standing("c")
    assert s.tags == ["senior"]


def test_a_tag_event_with_no_tag_is_refused():
    with pytest.raises(PipelineError, match="needs a tag"):
        Event(candidate_id="c", kind="tagged")


def test_the_board_groups_by_tag():
    p = pipe(ev("a", "tagged", 1, tag="madrid"), ev("b", "tagged", 1, tag="madrid"),
             ev("c", "tagged", 1, tag="remote"))
    assert p.by_tag() == {"madrid": ["a", "b"], "remote": ["c"]}


# --------------------------------------------------------------------------
# Silence, which is the point
# --------------------------------------------------------------------------

def test_an_untouched_application_surfaces_as_stale():
    p = pipe(ev("forgotten", "received", 21), ev("fresh", "received", 1))
    assert [s.candidate_id for s in p.stale(NOW)] == ["forgotten"]


def test_a_closed_application_is_not_nagging_anybody():
    p = pipe(ev("done", "received", 30),
             ev("done", "closed", 29, by="a", reason="withdrew"))
    assert p.stale(NOW) == []


def test_the_longest_silence_comes_first():
    p = pipe(ev("a", "received", 10), ev("b", "received", 30), ev("c", "received", 20))
    assert [s.candidate_id for s in p.stale(NOW)] == ["b", "c", "a"]


def test_the_report_says_plainly_when_nothing_has_been_left():
    p = pipe(ev("a", "received", 1))
    assert "nothing has been sitting untouched" in p.render(NOW)


def test_idle_days_counts_from_the_last_event_not_the_first():
    s = pipe(ev("c", "received", 30), ev("c", "read", 2, by="a")).standing("c")
    assert s.idle_days(NOW) == pytest.approx(2, abs=0.1)


# --------------------------------------------------------------------------
# The reading order
# --------------------------------------------------------------------------

def test_unread_applications_come_before_read_ones():
    """The promise is about the unread ones, so they sort first."""
    p = pipe(ev("read_high", "screened", 1, detail={"score": 0.9}),
             ev("read_high", "read", 1, by="a"),
             ev("unread_low", "screened", 1, detail={"score": 0.3}))
    assert [s.candidate_id for s in p.board()][0] == "unread_low"


def test_closed_applications_sink_to_the_bottom():
    p = pipe(ev("open", "screened", 1, detail={"score": 0.1}),
             ev("shut", "screened", 1, detail={"score": 0.99}),
             ev("shut", "closed", 1, by="a", reason="done"))
    assert [s.candidate_id for s in p.board()][-1] == "shut"


def test_an_unmeasured_interval_is_unknown_rather_than_zero():
    s = pipe(ev("c", "screened", 1, detail={"score": 0.5})).standing("c")
    assert s.score == 0.5 and s.spread is None
    assert "+/-" not in pipe(ev("c", "screened", 1, detail={"score": 0.5})).render(NOW)


def test_a_measured_interval_is_shown_next_to_the_score():
    p = pipe(ev("c", "screened", 1, detail={"score": 0.5, "spread": 0.08}))
    assert "+/-8%" in p.render(NOW)


# --------------------------------------------------------------------------
# Round trip
# --------------------------------------------------------------------------

def test_the_log_survives_a_save_and_a_load(tmp_path):
    from pipeline import load, save
    p = pipe(ev("c", "received", 3), ev("c", "read", 1, by="noam_castel"),
             ev("c", "tagged", 1, tag="madrid"))
    back = load(save(p, tmp_path / "pipe.json"))
    assert back.standing("c").state == "read"
    assert back.standing("c").tags == ["madrid"]
    assert len(back.events) == 3


# --------------------------------------------------------------------------
# Parked, not forgotten
# --------------------------------------------------------------------------

def test_parking_someone_without_a_date_is_refused():
    """"Interesting in six months" with no date is how people get lost."""
    with pytest.raises(PipelineError, match="needs detail"):
        Event(candidate_id="c", kind="revisit", by="a", reason="good, wrong role")


def test_a_bare_date_is_read_as_utc_midnight():
    s = pipe(ev("c", "revisit", 1, by="a", reason="later",
               detail={"on": "2027-03-01"})).standing("c")
    assert s.revisit_on.tzinfo is not None
    assert not s.revisit_due(NOW)


def test_a_parked_application_does_not_nag_before_its_date():
    p = pipe(ev("c", "received", 40),
             ev("c", "revisit", 30, by="a", reason="revisit when the platform role opens",
                detail={"on": "2027-03-01"}))
    assert p.stale(NOW) == []
    assert p.standing("c").state == "revisit"


def test_a_parked_application_surfaces_on_its_date():
    p = pipe(ev("c", "received", 200),
             ev("c", "revisit", 190, by="a", reason="later",
                detail={"on": "2026-09-01"}))
    assert [s.candidate_id for s in p.due(NOW)] == ["c"]
    assert "now due" in p.render(NOW)


def test_acting_on_someone_cancels_their_parking():
    """Parked and then contacted is not parked any more."""
    s = pipe(ev("c", "revisit", 30, by="a", reason="later", detail={"on": "2026-09-01"}),
             ev("c", "contacted", 2, by="a")).standing("c")
    assert s.revisit_on is None and s.state == "contacted"


def test_the_guess_is_a_range_when_the_paper_is_silent_on_some_criteria():
    at = "2026-09-18T10:00:00+00:00"
    p = Pipeline(posting_id="fa")
    p.add(Event(candidate_id="x", kind="received", at=at))
    assert p.standing("x").guess is None
    p.add(Event(candidate_id="x", kind="screened", at=at,
                detail={"score": 0.5, "coverage": 0.8}))
    low, mid, high = p.standing("x").guess
    # Silence is neither absence nor proof: floor, ceiling, and the middle between them.
    assert (low, round(mid, 4), round(high, 4)) == (0.5, 0.6, 0.7)
    p.add(Event(candidate_id="x", kind="screened", at=at, detail={"score": 0.88, "coverage": 1.0}))
    assert p.standing("x").guess == (0.88, 0.88, 0.88)
    # An older log without coverage: the score alone, no invented range.
    p.add(Event(candidate_id="x", kind="screened", at=at, detail={"score": 0.4}))
    assert p.standing("x").guess == (0.4, 0.4, 0.4)
