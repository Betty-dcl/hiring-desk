"""The two free paths: re-reading a record, and comparing two of them.

Neither calls a model, and both exist to answer a question after the fact --
so their failure mode is a reassuring answer about a measurement that says
something else. That is worth testing even though it is only printing.
"""

from __future__ import annotations

import json

from harness.__main__ import _stability_compare, _stability_report


def record(path, *candidates, posting="p", n=3, unstable=()):
    """Write a stability record shaped like the real one.

    `candidates` are (id, mean, spread, runs) and `unstable` are
    (candidate_id, criterion_id) pairs.
    """
    d = {
        "posting_id": posting,
        "n": n,
        "at": "2026-09-19T10:00:00+00:00",
        "unstable_criteria": [
            {"candidate_id": c, "criterion_id": k, "agreement": 0.667}
            for c, k in unstable
        ],
        "candidates": [
            {
                "candidate_id": cid,
                "runs": runs,
                "scores": [mean] * runs,
                "mean": mean,
                "spread": spread,
                "per_criterion": {"a": {}, "b": {}, "c": {}},
                "errors": [],
            }
            for cid, mean, spread, runs in candidates
        ],
    }
    path.write_text(json.dumps(d), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# --report
# --------------------------------------------------------------------------

def test_report_says_where_the_ranking_stops(tmp_path, capsys):
    f = record(tmp_path / "r.json",
               ("a", 0.90, 0.02, 3), ("b", 0.31, 0.04, 3), ("c", 0.30, 0.04, 3))
    assert _stability_report(str(f)) == 0
    out = capsys.readouterr().out
    assert "settled places: 1 of 3" in out
    assert "NOT SEP." in out


def test_report_prints_the_band_the_desk_and_readme_use(tmp_path, capsys):
    """The record's own resolution is a range; the desk's any-order band is the
    pooled drift. A reader of DETAILS sees both, so the command prints both."""
    from harness.__main__ import ROOT
    from harness.drift import DEFAULT_SWAP, pool

    f = record(tmp_path / "r.json", ("a", 0.90, 0.02, 3), ("b", 0.31, 0.04, 3),
               posting="founders_associate")
    assert _stability_report(str(f)) == 0
    gap = pool("founders_associate", ROOT).gap(DEFAULT_SWAP)
    assert f"any order below: {gap:.1%}" in capsys.readouterr().out


def test_report_on_a_missing_file_is_an_error_not_an_empty_table(tmp_path, capsys):
    assert _stability_report(str(tmp_path / "nope.json")) == 2


def test_report_refuses_a_record_where_nothing_ran(tmp_path, capsys):
    """Zero completed runs is not a stable cohort, it is no cohort."""
    f = record(tmp_path / "r.json", ("a", 0.0, 0.0, 0))
    assert _stability_report(str(f)) == 2


# --------------------------------------------------------------------------
# --compare
# --------------------------------------------------------------------------

def test_compare_names_a_criterion_that_became_steady(tmp_path, capsys):
    before = record(tmp_path / "b.json", ("a", 0.5, 0.08, 3), unstable=[("a", "wobbly")])
    after = record(tmp_path / "a.json", ("a", 0.5, 0.01, 3))
    assert _stability_compare(str(before), str(after)) == 0
    out = capsys.readouterr().out
    assert "steady now:" in out and "wobbly" in out
    assert "steadier" in out


def test_compare_reports_a_newly_unsteady_criterion_separately(tmp_path, capsys):
    """Two fixed and two broken is a move, not a fix, and must read as one."""
    before = record(tmp_path / "b.json", ("a", 0.5, 0.05, 3), unstable=[("a", "old")])
    after = record(tmp_path / "a.json", ("a", 0.5, 0.05, 3), unstable=[("a", "new")])
    _stability_compare(str(before), str(after))
    out = capsys.readouterr().out
    assert "steady now:" in out and "old" in out
    assert "newly unsteady:" in out and "new" in out
    #: the counts are not netted off against each other
    assert "criterion calls that moved: 1/3 -> 1/3" in out


def test_compare_shows_places_gained(tmp_path, capsys):
    before = record(tmp_path / "b.json", ("a", 0.60, 0.10, 3), ("b", 0.55, 0.10, 3))
    after = record(tmp_path / "a.json", ("a", 0.60, 0.01, 3), ("b", 0.55, 0.01, 3))
    _stability_compare(str(before), str(after))
    assert "settled places:             0/2 -> 2/2" in capsys.readouterr().out


def test_compare_survives_a_candidate_who_only_ran_the_first_time(tmp_path, capsys):
    before = record(tmp_path / "b.json", ("a", 0.5, 0.01, 3), ("gone", 0.4, 0.01, 3))
    after = record(tmp_path / "a.json", ("a", 0.5, 0.01, 3))
    assert _stability_compare(str(before), str(after)) == 0
    assert "gone" in capsys.readouterr().out


def test_compare_on_a_missing_record_is_an_error(tmp_path):
    f = record(tmp_path / "b.json", ("a", 0.5, 0.01, 3))
    assert _stability_compare(str(f), str(tmp_path / "nope.json")) == 2


def test_compare_notices_a_criterion_that_spread_to_more_people(tmp_path, capsys):
    """Set membership hides the loudest change: same name, four times as many."""
    before = record(tmp_path / "b.json", ("a", 0.5, 0.01, 3), unstable=[("x", "wobbly")])
    after = record(tmp_path / "a.json", ("a", 0.5, 0.01, 3),
                   unstable=[("x", "wobbly"), ("y", "wobbly"), ("z", "wobbly")])
    _stability_compare(str(before), str(after))
    out = capsys.readouterr().out
    assert "wobbly" in out and "1 -> 3 candidates" in out
    #: it is in neither of the membership lines, which is the point
    assert "steady now:" not in out and "newly unsteady:" not in out
