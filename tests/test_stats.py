"""harness/stats.py against the textbooks' own worked values.

Every number the report prints goes through one of these functions, and a
statistics bug fails in the quiet direction: an interval a little too narrow
reads exactly like a precise measurement. So each function is pinned to a
value someone else published, and each guard it carries has a test that
breaks when the guard is taken out.
"""

from __future__ import annotations

import itertools
import math
import random
import statistics

import pytest

from harness import stats


# --------------------------------------------------------------------------
# Distributions: the tables at the back of the book
# --------------------------------------------------------------------------

@pytest.mark.parametrize("p,df,want", [
    (0.975, 10, 2.228139), (0.975, 30, 2.042272), (0.95, 5, 2.015048),
    (0.975, math.inf, 1.959964), (0.80, 30, 0.853767),
])
def test_student_t_quantiles_match_the_table(p, df, want):
    assert stats.t_ppf(p, df) == pytest.approx(want, abs=1e-5)
    assert stats.t_cdf(stats.t_ppf(p, df), df) == pytest.approx(p, abs=1e-9)


@pytest.mark.parametrize("p,df,want", [
    (0.975, 10, 20.483177), (0.025, 10, 3.246973), (0.975, 20, 34.169607),
    (0.025, 20, 9.590777),
])
def test_chi_square_quantiles_match_the_table(p, df, want):
    assert stats.chi2_ppf(p, df) == pytest.approx(want, abs=1e-4)


# --------------------------------------------------------------------------
# Proportions
# --------------------------------------------------------------------------

def test_wilson_reproduces_newcombe():
    # Newcombe (1998), example 81/263: Wilson score interval 0.2553-0.3662.
    lo, hi = stats.wilson(81, 263)
    assert (lo, hi) == (pytest.approx(0.2553, abs=1e-4), pytest.approx(0.3662, abs=1e-4))


def test_wilson_ends_exactly_at_zero_and_one_when_it_should():
    # With no successes the lower end is 0 by definition. Computed as
    # centre - half it comes out as a rounding residue a hair above zero,
    # which the report would print as "0.0%" while the JSON said otherwise.
    assert stats.wilson(0, 10)[0] == 0.0
    assert stats.wilson(10, 10)[1] == 1.0
    assert stats.wilson(0, 10)[1] == pytest.approx(0.2775, abs=1e-4)
    assert stats.wilson(10, 10)[0] == pytest.approx(0.7225, abs=1e-4)


def test_clopper_pearson_is_the_exact_binomial_interval():
    # Zero in ten: the upper end is 1 - 0.025^(1/10), in closed form.
    assert stats.clopper_pearson(0, 10) == (0.0, pytest.approx(1 - 0.025 ** 0.1, abs=1e-8))
    lo, hi = stats.clopper_pearson(25, 32)
    # It is never narrower than Wilson's on the same counts.
    wlo, whi = stats.wilson(25, 32)
    assert lo <= wlo and hi >= whi
    assert (lo, hi) == (pytest.approx(0.6003, abs=1e-3), pytest.approx(0.9072, abs=1e-3))


def test_rule_of_three_is_exact_not_three_over_n():
    assert stats.rule_of_three_upper(3) == pytest.approx(0.6316, abs=1e-4)
    assert stats.rule_of_three_upper(300) == pytest.approx(3 / 300, rel=0.01)


def test_design_effect_bounds():
    # Every cluster the same: nothing shared, one trial is worth one trial.
    assert stats.design_effect([(2, 4)] * 8) == (0.0, 1.0)
    # All-or-nothing clusters: the four judgements of a defect are one.
    icc, deff = stats.design_effect([(4, 4), (0, 4)] * 4)
    assert icc == pytest.approx(1.0) and deff == pytest.approx(4.0)


def test_cluster_bootstrap_is_reproducible_and_brackets_the_rate():
    clusters = [(4, 4), (3, 4), (2, 4), (4, 4), (3, 4), (4, 4), (1, 4), (4, 4)]
    a = stats.cluster_bootstrap_proportion(clusters)
    assert a == stats.cluster_bootstrap_proportion(clusters)
    assert a[0] < 25 / 32 < a[1]


# --------------------------------------------------------------------------
# Noise between repeats
# --------------------------------------------------------------------------

def test_pooled_sd_weights_by_degrees_of_freedom():
    # Variances 1 (df 2) and 2 (df 1): pooled 4/3, not the unweighted 1.5.
    sd, df = stats.pooled_sd([[1, 2, 3], [10, 12]])
    assert df == 3 and sd == pytest.approx(math.sqrt(4 / 3))
    # A single run carries no information about noise.
    assert stats.pooled_sd([[1, 2, 3], [7]]) == stats.pooled_sd([[1, 2, 3]])


def test_sd_interval_contains_the_estimate_and_narrows_with_df():
    lo10, hi10 = stats.sd_interval(1.0, 10)
    lo30, hi30 = stats.sd_interval(1.0, 30)
    assert lo10 < lo30 < 1.0 < hi30 < hi10
    assert lo10 == pytest.approx(math.sqrt(10 / 20.483177), abs=1e-5)


def test_detectable_with_t_is_wider_than_the_normal():
    normal = stats.detectable(0.03, 7, comparisons=10)
    t = stats.detectable(0.03, 7, df=30, comparisons=10)
    assert t > normal
    # The normal version is the textbook (z_{1-a/2m} + z_power) sd sqrt(2/n).
    z = statistics.NormalDist()
    want = (z.inv_cdf(1 - 0.05 / 20) + z.inv_cdf(0.8)) * 0.03 * math.sqrt(2 / 7)
    assert normal == pytest.approx(want)


def test_swap_probability_and_gap_are_inverses():
    for df in (math.inf, 20, 5):
        gap = stats.gap_for_swap(0.05, 0.027, df)
        assert stats.swap_probability(gap, 0.027, df) == pytest.approx(0.05, abs=1e-9)
    assert stats.swap_probability(0.0, 0.027) == pytest.approx(0.5)
    assert stats.swap_probability(0.01, 0.0) == 0.0
    with pytest.raises(ValueError):
        stats.gap_for_swap(0.5, 0.027)


# --------------------------------------------------------------------------
# Differences between groups
# --------------------------------------------------------------------------

def test_percentile_is_type_7():
    assert stats.percentile([1, 2, 3, 4], 0.5) == 2.5
    assert stats.percentile([1, 2, 3, 4], 0.25) == 1.75


def test_exact_permutation_on_a_complete_separation():
    # Three against three, completely separated: 2 of the C(6,3) = 20 splits
    # are as extreme (it and its mirror). Never zero.
    p = stats.permutation_test([1, 2, 3], [4, 5, 6])
    assert p.exact and p.relabellings == 20 and p.p == pytest.approx(0.1)
    assert p.diff == -3.0


def test_the_fast_verdict_agrees_with_the_full_test():
    # `_SubsetSums.rejects` stops early; it must give the full test's verdict
    # at every alpha, including when p lands exactly on alpha.
    rng = random.Random(1)
    sums = stats._SubsetSums(4)
    for _ in range(200):
        x = [round(rng.gauss(0.5, 0.03), 2) for _ in range(4)]
        y = [round(rng.gauss(0.53, 0.03), 2) for _ in range(4)]
        p = stats.permutation_test(x, y).p
        for alpha in (0.0286, 2 / 70, 0.05, 0.1, p):
            assert sums.rejects(x, y, alpha) == (p <= alpha + 1e-12), (x, y, alpha)


def test_permutation_power_floor_is_reproducible_and_sane():
    rng = random.Random(7)
    groups = [[0.5 + rng.gauss(0, 0.02) for _ in range(5)] for _ in range(3)]
    a = stats.detectable_by_simulation(groups, sims=100)
    assert a == stats.detectable_by_simulation(groups, sims=100)
    sd, df = stats.pooled_sd(groups)
    # Same order of size as the t formula; a permutation test on five runs
    # has less power than the t test, never much more.
    assert 0.8 * stats.detectable(sd, 5, df=df) < a < 3 * stats.detectable(sd, 5, df=df)


def test_power_floor_is_infinite_when_the_test_cannot_reject():
    # Two against two: the smallest possible p is 2/6, above any alpha used.
    assert stats.detectable_by_simulation([[0.5, 0.51], [0.52, 0.5]], sims=20) == math.inf


def test_bootstrap_interval_is_stratified():
    x, y = [0.5] * 7, [0.4] * 3
    # With no spread inside either group, every stratified resample has the
    # same difference; a pooled resample would not.
    lo, hi = stats.bootstrap_diff(x, y)
    assert lo == pytest.approx(0.1) and hi == pytest.approx(0.1)


# --------------------------------------------------------------------------
# Agreement
# --------------------------------------------------------------------------

def test_cohen_kappa_textbook():
    # 50 items: 20 yes/yes, 5 yes/no, 10 no/yes, 15 no/no -> kappa 0.4.
    pairs = [("y", "y")] * 20 + [("y", "n")] * 5 + [("n", "y")] * 10 + [("n", "n")] * 15
    a, b = zip(*pairs)
    assert stats.cohen_kappa(a, b) == pytest.approx(0.4)


def test_fleiss_kappa_textbook():
    # Fleiss (1971) as reproduced on Wikipedia: 10 items, 14 raters -> 0.210.
    table = [[0, 0, 0, 0, 14], [0, 2, 6, 4, 2], [0, 0, 3, 5, 6], [0, 3, 9, 2, 0],
             [2, 2, 8, 1, 1], [7, 7, 0, 0, 0], [3, 2, 6, 3, 0], [2, 5, 3, 2, 2],
             [6, 5, 2, 1, 0], [0, 2, 2, 3, 7]]
    assert stats.fleiss_kappa(table) == pytest.approx(0.210, abs=5e-4)
    with pytest.raises(ValueError):
        stats.fleiss_kappa([[1, 2], [3]])
    with pytest.raises(ValueError):
        stats.fleiss_kappa([[1, 2], [2, 2]])


def test_kendall_tau_b_with_ties():
    # scipy.stats.kendalltau's documented example.
    assert stats.kendall_tau_b([12, 2, 1, 12, 2], [1, 4, 7, 1, 0]) == pytest.approx(
        -0.47140452079103173)
    assert stats.kendall_tau_b([1, 2, 3], [1, 2, 3]) == 1.0
    assert stats.kendall_tau_b([1, 2, 3], [3, 2, 1]) == -1.0


def test_no_simulation_touches_the_global_generator():
    random.seed(12345)
    before = random.random()
    random.seed(12345)
    stats.bootstrap_diff([1, 2, 3], [2, 3, 4])
    stats.cluster_bootstrap_proportion([(1, 2), (2, 2)])
    stats.detectable_by_simulation([[1, 2, 3, 4], [2, 3, 4, 5]], sims=5, hi=5)
    assert random.random() == before
