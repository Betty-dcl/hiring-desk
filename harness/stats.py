"""The statistics behind every number the README prints, in the standard library.

Everything else in `harness/` measures; this module says how far a
measurement can be read. It exists because the first versions of those
answers were rules of thumb -- "a gap counts when it beats the widest range",
"the floor is z times a standard error" -- and a rule of thumb is exactly what
an auditor asks to see derived. So each one is here in its textbook form,
tested against the textbook's own worked values, and the report
(`python -m harness report`) says which rule produced which number.

Four families, each chosen for the shape of the data this repository has,
which is small, discrete and clustered:

* **Proportions.** Wilson's score interval, because at n = 32 the Wald
  interval (p +/- 1.96 sd) undercovers and can leave [0, 1]; Clopper-Pearson
  alongside it, because it is the one an auditor already trusts, and it is
  conservative by construction.
* **Noise between repeats.** A pooled standard deviation with its degrees of
  freedom, a chi-square interval on it, and Student's t rather than the normal
  wherever that standard deviation is *estimated* -- with ten or thirty
  degrees of freedom the difference is the difference between the README's
  number and the honest one.
* **Differences between names.** An exact permutation test, because the
  scores are a handful of repeated values (0.29 seven times out of ten) and
  nothing about them is normal; a stratified bootstrap for the interval; and
  the smallest detectable difference computed both from the formula and by
  simulating the permutation test itself on the observed residuals.
* **Agreement.** Cohen's and Fleiss' kappa for "did the screener call the same
  criterion the same way", and Kendall's tau-b for "did the order hold".

No numpy: the heaviest computation here is a few thousand subset sums, and a
dependency is a thing an auditor has to trust too. Every random draw goes
through a `random.Random(seed)` passed in, never the global generator, so a
report run twice prints the same digits.
"""

from __future__ import annotations

import itertools
import math
import random
import statistics
from dataclasses import dataclass
from typing import Callable, Sequence

_NORMAL = statistics.NormalDist()

#: The seed every simulation uses unless told otherwise. Fixed, and printed in
#: the report, so the Monte Carlo digits are reproducible bit for bit.
SEED = 20260930


# ---------------------------------------------------------------------------
# Special functions. Numerical Recipes' continued fractions, bisection for the
# inverses: slow by library standards, exact to 1e-10, and readable.
# ---------------------------------------------------------------------------

def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (modified Lentz)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 400):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Regularised incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
          + a * math.log(x) + b * math.log1p(-x))
    front = math.exp(ln)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def gammainc(a: float, x: float) -> float:
    """Regularised lower incomplete gamma P(a, x)."""
    if x <= 0.0:
        return 0.0
    gln = math.lgamma(a)
    if x < a + 1.0:
        term = total = 1.0 / a
        ap = a
        for _ in range(1000):
            ap += 1.0
            term *= x / ap
            total += term
            if abs(term) < abs(total) * 1e-16:
                break
        return total * math.exp(-x + a * math.log(x) - gln)
    tiny = 1e-300
    b = x + 1.0 - a
    c, d = 1.0 / tiny, 1.0 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = b + an / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-16:
            break
    return 1.0 - math.exp(-x + a * math.log(x) - gln) * h


def _invert(cdf: Callable[[float], float], p: float, lo: float, hi: float) -> float:
    """The x with cdf(x) = p, for a monotone cdf, by bisection."""
    while cdf(hi) < p:
        hi *= 2.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if cdf(mid) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-12 * max(1.0, abs(mid)):
            break
    return (lo + hi) / 2.0


def t_cdf(t: float, df: float) -> float:
    """Student's t distribution function."""
    if math.isinf(df):
        return _NORMAL.cdf(t)
    tail = 0.5 * betainc(df / 2.0, 0.5, df / (df + t * t))
    return 1.0 - tail if t > 0 else tail


def t_ppf(p: float, df: float) -> float:
    """Student's t quantile. `df=inf` is the normal."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    if math.isinf(df):
        return _NORMAL.inv_cdf(p)
    if p == 0.5:
        return 0.0
    if p < 0.5:
        return -t_ppf(1.0 - p, df)
    return _invert(lambda x: t_cdf(x, df), p, 0.0, 10.0)


def chi2_cdf(x: float, df: float) -> float:
    return gammainc(df / 2.0, x / 2.0)


def chi2_ppf(p: float, df: float) -> float:
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    return _invert(lambda x: chi2_cdf(x, df), p, 0.0, max(1.0, 2.0 * df))


def beta_ppf(p: float, a: float, b: float) -> float:
    return _invert(lambda x: betainc(a, b, x), p, 0.0, 1.0)


# ---------------------------------------------------------------------------
# Proportions
# ---------------------------------------------------------------------------

def wilson(k: int, n: int, conf: float = 0.95) -> tuple[float, float]:
    """Wilson's score interval for k successes in n trials.

    Chosen over the Wald interval because at the sizes here (8, 32 trials)
    Wald's coverage falls well below its label and it can print an interval
    reaching past 100%. Newcombe (1998) is the reference comparison.
    """
    if n <= 0:
        raise ValueError("n must be positive")
    if not 0 <= k <= n:
        raise ValueError("k must be in [0, n]")
    z = _NORMAL.inv_cdf(1.0 - (1.0 - conf) / 2.0)
    p = k / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1.0 - p) / n + z * z / (4 * n * n)) / denom
    lo = 0.0 if k == 0 else max(0.0, centre - half)
    hi = 1.0 if k == n else min(1.0, centre + half)
    return lo, hi


def clopper_pearson(k: int, n: int, conf: float = 0.95) -> tuple[float, float]:
    """The exact binomial interval: never undercovers, usually overcovers."""
    if n <= 0 or not 0 <= k <= n:
        raise ValueError("need 0 <= k <= n, n > 0")
    a = (1.0 - conf) / 2.0
    lo = 0.0 if k == 0 else beta_ppf(a, k, n - k + 1)
    hi = 1.0 if k == n else beta_ppf(1.0 - a, k + 1, n - k)
    return lo, hi


def rule_of_three_upper(n: int, conf: float = 0.95) -> float:
    """The most an event can happen per trial after n trials without it.

    Exact form (1 - (1-conf)^(1/n)), not the 3/n approximation: at n = 3 the
    approximation says 100% and the exact bound says 63%.
    """
    return 1.0 - (1.0 - conf) ** (1.0 / n)


def cluster_bootstrap_proportion(clusters: Sequence[tuple[int, int]], *, B: int = 4000,
                                 conf: float = 0.95, seed: int = SEED
                                 ) -> tuple[float, float]:
    """Percentile interval for a pooled proportion, resampling whole clusters.

    `clusters` is (successes, trials) per cluster. The judge's 32 trials are
    eight planted defects judged four times each: the four judgements of one
    defect share its wording, so they are not 32 independent coin flips.
    Resampling defects rather than judgements keeps that dependence in the
    interval.
    """
    rng = random.Random(seed)
    k = len(clusters)
    stats = []
    for _ in range(B):
        pick = [clusters[rng.randrange(k)] for _ in range(k)]
        n = sum(t for _, t in pick)
        stats.append(sum(s for s, _ in pick) / n if n else 0.0)
    return percentile(stats, (1 - conf) / 2), percentile(stats, 1 - (1 - conf) / 2)


def design_effect(clusters: Sequence[tuple[int, int]]) -> tuple[float, float]:
    """(ICC, design effect) for a binary outcome in equal-sized clusters.

    The one-way ANOVA estimator of the intraclass correlation, truncated at
    zero. The design effect 1 + (m - 1) * ICC is how many trials one
    independent trial is worth; n / deff is the effective sample size.
    """
    sizes = {t for _, t in clusters}
    if len(sizes) != 1:
        raise ValueError("clusters must be the same size")
    m = sizes.pop()
    k = len(clusters)
    if k < 2 or m < 2:
        return 0.0, 1.0
    p = [s / m for s, _ in clusters]
    grand = sum(s for s, _ in clusters) / (k * m)
    msb = m * sum((pi - grand) ** 2 for pi in p) / (k - 1)
    msw = sum(s * (1 - pi) ** 2 + (m - s) * pi ** 2 for (s, _), pi in zip(clusters, p)) / (k * (m - 1))
    icc = (msb - msw) / (msb + (m - 1) * msw) if (msb + (m - 1) * msw) else 0.0
    icc = max(0.0, icc)
    return icc, 1.0 + (m - 1) * icc


# ---------------------------------------------------------------------------
# Noise between repeats
# ---------------------------------------------------------------------------

def pooled_sd(groups: Sequence[Sequence[float]]) -> tuple[float, int]:
    """(pooled standard deviation, degrees of freedom) over groups of repeats.

    Weighted by each group's n - 1, which is what makes it the maximum
    likelihood estimate under a common variance. The harness's first version
    averaged the variances unweighted; with equal group sizes that is the
    same number, and the report checks it reproduces it.
    """
    live = [g for g in groups if len(g) > 1]
    df = sum(len(g) - 1 for g in live)
    if not df:
        return 0.0, 0
    ss = sum(sum((x - statistics.fmean(g)) ** 2 for x in g) for g in live)
    return math.sqrt(ss / df), df


def sd_interval(sd: float, df: int, conf: float = 0.95) -> tuple[float, float]:
    """Chi-square interval for a standard deviation estimated on `df`."""
    a = (1.0 - conf) / 2.0
    return (sd * math.sqrt(df / chi2_ppf(1.0 - a, df)),
            sd * math.sqrt(df / chi2_ppf(a, df)))


def detectable(sd: float, n: int, *, df: float = math.inf, alpha: float = 0.05,
               power: float = 0.80, comparisons: int = 1) -> float:
    """Smallest true difference between two means of n runs caught with `power`.

    Two-sided, Bonferroni-corrected for `comparisons`. `df=inf` is the
    normal approximation the name test first shipped with; a finite df uses
    Student's t for both the critical value and the power term, which is the
    honest version when the sd itself came from those same runs.
    """
    crit = t_ppf(1.0 - alpha / (2 * comparisons), df)
    return (crit + t_ppf(power, df)) * sd * math.sqrt(2.0 / n)


def swap_probability(gap: float, sd: float, df: float = math.inf) -> float:
    """How often two single screenings come out in the wrong order.

    Two people whose true fits differ by `gap`, each screened once, each
    score moving with standard deviation `sd` around their own mean: the
    difference moves with sd * sqrt(2), and it is wrong-signed with this
    probability. Student's t when the sd is estimated.
    """
    if sd <= 0:
        return 0.0 if gap > 0 else 0.5
    return t_cdf(-gap / (sd * math.sqrt(2.0)), df)


def gap_for_swap(swap: float, sd: float, df: float = math.inf) -> float:
    """The gap below which single screenings swap more often than `swap`.

    The inverse of `swap_probability`, and the rule the desk's "any order"
    band is drawn by.
    """
    if not 0.0 < swap < 0.5:
        raise ValueError("swap must be in (0, 0.5)")
    return t_ppf(1.0 - swap, df) * sd * math.sqrt(2.0)


# ---------------------------------------------------------------------------
# Differences between groups
# ---------------------------------------------------------------------------

def percentile(xs: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile (the 'type 7' of R and numpy)."""
    s = sorted(xs)
    if not s:
        raise ValueError("empty")
    h = (len(s) - 1) * q
    lo = math.floor(h)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (h - lo) * (s[hi] - s[lo])


#: Up to this many relabellings the permutation test is exact; beyond, it is
#: Monte Carlo with the seed given. C(14, 7) = 3432, so every name pair here
#: is exact.
EXACT_LIMIT = 200_000


@dataclass(frozen=True)
class Permutation:
    diff: float
    p: float
    exact: bool
    relabellings: int


def permutation_test(x: Sequence[float], y: Sequence[float], *, seed: int = SEED,
                     resamples: int = 20_000) -> Permutation:
    """Two-sided test of mean(x) = mean(y) by relabelling the pooled scores.

    Chosen because the scores are not normal -- they are a few repeated values
    with the odd outlier -- and a permutation test assumes only that, under
    no effect, the labels are exchangeable. That is exactly the name test's
    null: the name changed nothing, so which runs carried it is arbitrary.

    p counts relabellings at least as extreme as the one observed, the
    observed one included, so it is never zero.
    """
    x, y = list(x), list(y)
    pooled = x + y
    nx, n = len(x), len(x) + len(y)
    total = sum(pooled)
    observed = abs(statistics.fmean(x) - statistics.fmean(y))
    eps = 1e-12

    def stat(sum_x: float) -> float:
        return abs(sum_x / nx - (total - sum_x) / (n - nx))

    count = math.comb(n, nx)
    if count <= EXACT_LIMIT:
        hits = sum(1 for idx in itertools.combinations(range(n), nx)
                   if stat(sum(pooled[i] for i in idx)) >= observed - eps)
        return Permutation(round(statistics.fmean(x) - statistics.fmean(y), 10),
                           hits / count, True, count)
    rng = random.Random(seed)
    hits = 1
    for _ in range(resamples):
        rng.shuffle(pooled)
        if stat(sum(pooled[:nx])) >= observed - eps:
            hits += 1
    return Permutation(round(statistics.fmean(x) - statistics.fmean(y), 10),
                       hits / (resamples + 1), False, resamples)


def bootstrap_diff(x: Sequence[float], y: Sequence[float], *, B: int = 4000,
                   conf: float = 0.95, seed: int = SEED) -> tuple[float, float]:
    """Percentile interval for mean(x) - mean(y), resampling within each group.

    Stratified: each resample keeps n_x runs of x and n_y runs of y, because
    how many runs each name got is fixed by the design, not drawn.
    """
    rng = random.Random(seed)
    x, y = list(x), list(y)
    diffs = []
    for _ in range(B):
        bx = [x[rng.randrange(len(x))] for _ in x]
        by = [y[rng.randrange(len(y))] for _ in y]
        diffs.append(statistics.fmean(bx) - statistics.fmean(by))
    return percentile(diffs, (1 - conf) / 2), percentile(diffs, 1 - (1 - conf) / 2)


class _SubsetSums:
    """Every way of splitting 2n pooled runs into two groups, precomputed.

    A permutation test on two groups of n needs the sum of every n-subset of
    the pooled values. The subsets never change between simulations -- only
    the values do -- so the index sets are built once.
    """

    def __init__(self, n: int) -> None:
        self.n = n
        self.combos = list(itertools.combinations(range(2 * n), n))

    def rejects(self, x: list[float], y: list[float], alpha: float) -> bool:
        """Whether the exact two-sided test rejects at `alpha`.

        Stops counting as soon as more than alpha of the relabellings are at
        least as extreme: a simulation only needs the verdict, and most
        simulated tests lose it within the first few hundred splits.
        """
        pooled = x + y
        n = self.n
        total = sum(pooled)
        obs = abs(sum(x) - sum(y)) - 1e-12
        allowed = alpha * len(self.combos)
        hits = 0
        for idx in self.combos:
            s = 0.0
            for i in idx:
                s += pooled[i]
            if abs(2 * s - total) >= obs:
                hits += 1
                if hits > allowed:
                    return False
        return True


def simulated_power(residuals: Sequence[float], n: int, delta: float, *, alpha: float,
                    sims: int, rng: random.Random, sums: _SubsetSums | None = None) -> float:
    """Share of simulated name tests that catch a true difference `delta`.

    Each simulated test draws two groups of n runs from the observed
    residuals (the scores with each name's own mean taken out, so they carry
    the screener's real, lumpy noise and no name effect), adds `delta` to
    one group, and runs the exact permutation test at `alpha`.
    """
    sums = sums or _SubsetSums(n)
    r = list(residuals)
    hit = 0
    for _ in range(sims):
        x = [r[rng.randrange(len(r))] + delta for _ in range(n)]
        y = [r[rng.randrange(len(r))] for _ in range(n)]
        if sums.rejects(x, y, alpha):
            hit += 1
    return hit / sims


def detectable_by_simulation(groups: Sequence[Sequence[float]], *, alpha: float = 0.05,
                             power: float = 0.80, comparisons: int = 1, sims: int = 300,
                             seed: int = SEED, hi: float = 0.2) -> float:
    """The smallest difference the permutation test catches with `power`.

    Found by bisection on `simulated_power`, with common random numbers (the
    same seed at every step) so the power curve is monotone in delta and the
    bisection converges. This is the name test's floor without any normal
    assumption: it uses the residuals actually observed.
    """
    n = min(len(g) for g in groups)
    residuals = [x - statistics.fmean(g) for g in groups for x in g]
    a = alpha / comparisons
    sums = _SubsetSums(n)

    def pw(d: float) -> float:
        return simulated_power(residuals, n, d, alpha=a, sims=sims,
                               rng=random.Random(seed), sums=sums)

    lo = 0.0
    if pw(hi) < power:
        return math.inf
    for _ in range(10):
        mid = (lo + hi) / 2
        if pw(mid) >= power:
            hi = mid
        else:
            lo = mid
    return hi


# ---------------------------------------------------------------------------
# Agreement
# ---------------------------------------------------------------------------

def cohen_kappa(a: Sequence[str], b: Sequence[str]) -> float:
    """Agreement between two raters beyond what their marginals give by chance."""
    if len(a) != len(b) or not a:
        raise ValueError("two equal, non-empty sequences")
    n = len(a)
    cats = set(a) | set(b)
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pe = sum((sum(1 for x in a if x == c) / n) * (sum(1 for y in b if y == c) / n)
             for c in cats)
    return 1.0 if pe == 1.0 else (po - pe) / (1.0 - pe)


def fleiss_kappa(table: Sequence[Sequence[int]]) -> float:
    """Fleiss' kappa: rows are items, columns are how many raters chose each category.

    Here an item is one criterion for one candidate and the raters are the
    repeated runs, so it answers "beyond chance, how consistently does the
    screener call a criterion?" in one number that does not reward a
    criterion for being `unknown` for everyone.
    """
    rows = [list(r) for r in table if sum(r)]
    if not rows:
        raise ValueError("empty table")
    m = sum(rows[0])
    if any(sum(r) != m for r in rows):
        raise ValueError("every item needs the same number of ratings")
    if any(len(r) != len(rows[0]) for r in rows):
        raise ValueError("every item needs the same categories")
    n = len(rows)
    p_j = [sum(r[j] for r in rows) / (n * m) for j in range(len(rows[0]))]
    p_i = [(sum(c * c for c in r) - m) / (m * (m - 1)) for r in rows]
    pbar = statistics.fmean(p_i)
    pe = sum(p * p for p in p_j)
    return 1.0 if pe == 1.0 else (pbar - pe) / (1.0 - pe)


def kendall_tau_b(x: Sequence[float], y: Sequence[float]) -> float:
    """Kendall's tau-b between two rankings (as scores), ties corrected."""
    if len(x) != len(y) or len(x) < 2:
        raise ValueError("two equal sequences of at least two")
    conc = disc = tx = ty = 0
    for i in range(len(x)):
        for j in range(i + 1, len(x)):
            dx, dy = x[i] - x[j], y[i] - y[j]
            if dx == 0 and dy == 0:
                continue
            if dx == 0:
                tx += 1
            elif dy == 0:
                ty += 1
            elif (dx > 0) == (dy > 0):
                conc += 1
            else:
                disc += 1
    denom = math.sqrt((conc + disc + tx) * (conc + disc + ty))
    return (conc - disc) / denom if denom else 0.0
