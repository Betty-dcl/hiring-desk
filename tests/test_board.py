"""The page, whose only job is to not overstate what was measured.

A screen is where a careful measurement usually dies: the interval gets
dropped because it does not fit the column, and a band of indistinguishable
people is drawn as 3rd, 4th and 5th because a table has rows.
"""

from __future__ import annotations

from console.render import _bands, _score_cell, render


def st(cid, score=None, spread=None, state="screened", **kw):
    return {"candidate_id": cid, "score": score, "spread": spread, "state": state,
            "tags": kw.get("tags", []), "read_by": kw.get("read_by", []),
            "idle_days": kw.get("idle_days", 1.0)}


# --------------------------------------------------------------------------
# Bands, not ranks
# --------------------------------------------------------------------------

def test_people_inside_the_resolution_are_one_band():
    bands = _bands([st("a", 0.35), st("b", 0.33), st("c", 0.32)], 0.05)
    assert len(bands) == 1 and len(bands[0]) == 3


def test_a_gap_wider_than_the_resolution_starts_a_new_band():
    bands = _bands([st("high", 0.90), st("low", 0.30)], 0.05)
    assert [len(b) for b in bands] == [1, 1]


def test_a_zero_resolution_separates_everybody():
    """Nothing measured yet is not a licence to band people together."""
    bands = _bands([st("a", 0.35), st("b", 0.34)], 0.0)
    assert [len(b) for b in bands] == [1, 1]


def test_an_unscored_candidate_is_not_placed_last_but_apart():
    bands = _bands([st("scored", 0.40), st("unscored")], 0.05)
    assert bands[-1][0]["candidate_id"] == "unscored"
    assert len(bands[-1]) == 1


def test_the_page_says_a_band_is_not_an_order():
    page = render({"posting_id": "p", "standings": [st("a", 0.35), st("b", 0.33)]},
                  {}, 0.05, stale=[], due=[])
    assert "does not separate them" in page


# --------------------------------------------------------------------------
# The interval, which is the thing a table drops first
# --------------------------------------------------------------------------

def test_a_measured_interval_is_shown_with_the_score():
    assert "± 5%" in _score_cell(st("a", 0.42, 0.05))


def test_an_unmeasured_interval_says_so_rather_than_showing_nothing():
    """Silence here reads as precision, which is the lie to avoid."""
    assert "unmeasured" in _score_cell(st("a", 0.42))


def test_an_unscored_candidate_is_not_drawn_as_zero():
    assert "not scored" in _score_cell(st("a"))


# --------------------------------------------------------------------------
# What the page puts first
# --------------------------------------------------------------------------

def test_silence_outranks_the_highest_score():
    page = render({"posting_id": "p", "standings": [st("top", 0.99), st("forgotten", 0.10)]},
                  {}, 0.05, stale=["forgotten"], due=[])
    assert page.index("Waiting on you") < page.index("Everyone")
    assert "forgotten" in page


def test_a_quiet_board_shows_no_alarm_section():
    page = render({"posting_id": "p", "standings": [st("a", 0.5)]}, {}, 0.05,
                  stale=[], due=[])
    assert "Waiting on you" not in page


def test_the_page_refuses_to_read_as_a_hiring_recommendation():
    page = render({"posting_id": "p", "standings": [st("a", 0.9)]}, {}, 0.05,
                  stale=[], due=[])
    assert "not a recommendation to" in page


# --------------------------------------------------------------------------
# Self-contained
# --------------------------------------------------------------------------

def test_the_page_makes_no_network_calls():
    """It will be opened from a file:// URL on someone else's laptop."""
    page = render({"posting_id": "p", "standings": [st("a", 0.5)]},
                  {"a": {"explain": "x"}}, 0.05, stale=[], due=[])
    assert "http://" not in page and "https://" not in page
    assert "<script" in page and "src=" not in page


def test_a_candidate_name_cannot_inject_markup():
    page = render({"posting_id": "p", "standings": [st("<img src=x onerror=alert(1)>", 0.5)]},
                  {}, 0.05, stale=[], due=[])
    assert "<img src=x" not in page
    assert "&lt;img" in page


# --------------------------------------------------------------------------
# The grid, and the cells a scorecard would flatten
# --------------------------------------------------------------------------

GRID = {"criteria": ["ai_native", "ownership"], "rows": [
    {"candidate_id": "a", "cells": {
        "ai_native": {"strength": "strong", "capped": "", "moved": False},
        "ownership": {"strength": "weak", "capped": "strong", "moved": False}}},
    {"candidate_id": "b", "cells": {
        "ai_native": {"strength": "moderate", "capped": "", "moved": True},
        "ownership": {"strength": "unknown", "capped": "", "moved": False}}},
]}


def page_with_grid(grid=GRID):
    return render({"posting_id": "p", "standings": [st("a", 0.6), st("b", 0.4)]},
                  {}, 0.05, stale=[], due=[], grid=grid)


def test_a_cell_that_moved_between_runs_is_marked_not_coloured_like_the_rest():
    page = page_with_grid()
    assert "moved" in page
    assert "did not hold between runs" in page


def test_a_capped_cell_says_it_was_capped_rather_than_earned():
    assert "capped from strong" in page_with_grid()


def test_unknown_is_drawn_as_a_non_answer_not_as_a_low_score():
    page = page_with_grid()
    assert "unknown — the document does not say" in page


def test_the_grid_is_omitted_when_there_is_nothing_to_draw():
    assert "Criterion by criterion" not in page_with_grid(grid={})
    assert "Criterion by criterion" not in page_with_grid(
        grid={"criteria": [], "rows": []})


def test_a_criterion_name_cannot_inject_markup_into_the_grid():
    bad = {"criteria": ["<b>x</b>"], "rows": [
        {"candidate_id": "a", "cells": {"<b>x</b>": {"strength": "weak"}}}]}
    assert "<b>x</b>" not in page_with_grid(grid=bad)
