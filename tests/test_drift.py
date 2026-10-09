"""The drift pool and the desk's "any order" band, on hand-built records.

The band decides which neighbours the desk refuses to put in order, so an
error here is silent and flattering: a pool that double-counts a record
looks more precise than it is, and a pool that lets in a reverted prompt
measures a screener nobody runs.
"""

from __future__ import annotations

import json
import math

import pytest

from harness import drift, stats


def stability(root, name, *groups, at="2026-09-19T10:00:00+00:00", posting="p"):
    d = {"posting_id": posting, "n": len(groups[0][1]), "at": at,
         "candidates": [{"candidate_id": c, "scores": s} for c, s in groups]}
    f = root / "runs" / "stability" / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(d), encoding="utf-8")
    return f


def bias(root, name, candidate, blind, at="2026-09-20T10:00:00+00:00"):
    d = {"candidate_id": candidate, "at": at, "n": len(blind), "names": [],
         "blind": {"scores": blind}}
    f = root / "runs" / "bias" / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(d), encoding="utf-8")
    return f


def test_pool_counts_a_measurement_once_however_many_files_carry_it(tmp_path):
    stability(tmp_path, "p_n3_personas.json", ("a", [0.5, 0.52, 0.54]), ("b", [0.3, 0.3, 0.33]))
    # A derived record (someone dropped) repeating its parent's scores.
    stability(tmp_path, "p_n3_rubric_v1_personas.json", ("a", [0.5, 0.52, 0.54]))
    p = drift.pool("p", tmp_path)
    assert [g.label for g in p.groups] == ["a", "b"]
    assert p.df == 4


def test_pool_leaves_out_the_reverted_prompt(tmp_path):
    stability(tmp_path, "p_n3_personas.json", ("a", [0.5, 0.52, 0.54]))
    stability(tmp_path, "p_n3_rubric_v2_personas.json", ("a", [0.1, 0.9, 0.5]))
    p = drift.pool("p", tmp_path)
    assert p.sources == ["runs/stability/p_n3_personas.json"]
    assert p.sd == pytest.approx(0.02)


def test_pool_takes_the_anonymised_baseline_of_a_name_test(tmp_path):
    stability(tmp_path, "p_n3_personas.json", ("a", [0.5, 0.52, 0.54]))
    bias(tmp_path, "a_p_n7.json", "a", [0.5, 0.51, 0.49, 0.5, 0.52, 0.48, 0.5])
    p = drift.pool("p", tmp_path)
    assert [g.label for g in p.groups] == ["a", "a (no name)"]
    assert p.df == 2 + 6
    assert drift.pool("p", tmp_path, stability_only=True).df == 2


def test_nothing_measured_means_no_band(tmp_path):
    assert drift.pool("p", tmp_path).gap(0.05) is None
    stability(tmp_path, "p_n2_personas.json", ("a", [0.5, 0.52]))
    # One degree of freedom: t's tails are too heavy to give a number anyone
    # should draw a band with.
    assert drift.pool("p", tmp_path).gap(0.05) is None


def test_the_gap_is_the_t_quantile_of_the_pooled_sd(tmp_path):
    stability(tmp_path, "p_n3_personas.json", ("a", [0.5, 0.52, 0.54]), ("b", [0.3, 0.32, 0.34]))
    p = drift.pool("p", tmp_path)
    assert p.gap(0.05) == pytest.approx(stats.t_ppf(0.95, 4) * 0.02 * math.sqrt(2))


def test_measured_gap_sees_a_new_record(tmp_path):
    f = stability(tmp_path, "p_n3_personas.json", ("a", [0.5, 0.52, 0.54]), ("b", [0.3, 0.3, 0.3]))
    first = drift.measured_gap("p", root=tmp_path)
    stability(tmp_path, "p_n3_more_personas.json", ("c", [0.2, 0.3, 0.4]),
              at="2026-09-21T10:00:00+00:00")
    assert drift.measured_gap("p", root=tmp_path) > first
    assert f.exists()


# --------------------------------------------------------------------------
# [any_order] in desk.toml
# --------------------------------------------------------------------------

def setting(tmp_path, body):
    f = tmp_path / "desk.toml"
    f.write_text(body, encoding="utf-8")
    return drift.any_order_setting(f)


def test_absent_section_is_the_measured_default(tmp_path):
    assert setting(tmp_path, "[team]\nvoters = []\n") == drift.AnyOrder("measured", 0.05)
    assert drift.any_order_setting(tmp_path / "missing.toml").rule == "measured"


def test_a_number_is_points(tmp_path):
    s = setting(tmp_path, "[any_order]\ngap = 5\n")
    assert s.rule == "fixed" and s.fixed == pytest.approx(0.05)


@pytest.mark.parametrize("body", [
    '[any_order]\ngap = "loose"\n', "[any_order]\ngap = -1\n", "[any_order]\nswap = 0.5\n",
    "[any_order]\nswap = 0\n", "[any_order]\ngap = true\n",
])
def test_a_setting_that_means_nothing_is_refused(tmp_path, body):
    with pytest.raises(ValueError):
        setting(tmp_path, body)


def test_the_shipped_desk_toml_uses_the_measured_rule():
    assert drift.any_order_setting() == drift.AnyOrder("measured", 0.05)


def test_triage_follows_the_setting(monkeypatch):
    import triage

    monkeypatch.setattr(triage, "_spreads", lambda posting: {"a": 0.03})
    monkeypatch.setattr(drift, "measured_gap", lambda posting, swap: 0.066)
    assert triage.any_order_noise("p", "a", drift.AnyOrder("spread")) == 0.03
    assert triage.any_order_noise("p", "b", drift.AnyOrder("spread")) is None
    assert triage.any_order_noise("p", "b", drift.AnyOrder("fixed", fixed=0.05)) == 0.05
    # Measured: the same for everyone, re-screened or not.
    assert triage.any_order_noise("p", "b", drift.AnyOrder("measured")) == 0.066
