"""Statistics for a backtest's per-session results. Pure; every Sharpe ratio here is PER SESSION.

Annualising one side of a comparison and not the other is the error this module exists to avoid (a
reviewed open-source tool compared an annualised Sharpe against a per-period hurdle in its deflated
Sharpe). Nothing here annualises.

- `psr`: the probabilistic Sharpe ratio (Bailey & López de Prado, "The Sharpe ratio efficient
  frontier"): the probability the true per-session Sharpe exceeds a benchmark, allowing for skew and
  fat tails.
- `dsr`: the deflated Sharpe ratio ("The Deflated Sharpe Ratio", 2014): PSR against the Sharpe the
  best of `trials` independent tries would show by luck alone. `trials` is the module's count from
  the results store, every spec ever run, so the deflation cannot be argued down after the fact.
- `min_track_record`: sessions needed before the observed Sharpe could exclude the benchmark.
- `block_bootstrap_ci`: a stationary-bootstrap interval on the mean, so serially correlated sessions
  are not treated as independent; `block_bootstrap_diff_ci` the same for the change between two
  stretches of one series (the run cards' edge trend).
"""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Sequence

from cherrypick.core import stats as core_stats

EULER_GAMMA = 0.5772156649015329
_N = statistics.NormalDist()


def moments(values: Sequence[float]) -> dict | None:
    """Mean, sd, per-session Sharpe, skew and (non-excess) kurtosis, or None under two values or no spread."""
    n = len(values)
    if n < 2:
        return None
    mean = statistics.fmean(values)
    sd = statistics.stdev(values)
    if sd == 0:
        return None
    m2 = sum((v - mean) ** 2 for v in values) / n
    m3 = sum((v - mean) ** 3 for v in values) / n
    m4 = sum((v - mean) ** 4 for v in values) / n
    return {
        "n": n,
        "mean": mean,
        "sd": sd,
        "sharpe": mean / sd,
        "skew": m3 / m2**1.5,
        "kurtosis": m4 / m2**2,
    }


def _psr_from(m: dict, benchmark: float) -> float | None:
    sr = m["sharpe"]
    denom = 1.0 - m["skew"] * sr + (m["kurtosis"] - 1.0) / 4.0 * sr * sr
    if denom <= 0:
        return None
    return _N.cdf((sr - benchmark) * math.sqrt(m["n"] - 1) / math.sqrt(denom))


def psr(values: Sequence[float], benchmark: float = 0.0) -> float | None:
    m = moments(values)
    return None if m is None else _psr_from(m, benchmark)


def expected_max_sharpe(trials: int, sharpe_variance: float) -> float:
    """The Sharpe the best of `trials` tries reaches by luck, given the variance of Sharpe across them."""
    if trials <= 1 or sharpe_variance <= 0:
        return 0.0
    sd = math.sqrt(sharpe_variance)
    return sd * (
        (1 - EULER_GAMMA) * _N.inv_cdf(1 - 1.0 / trials)
        + EULER_GAMMA * _N.inv_cdf(1 - 1.0 / (trials * math.e))
    )


def dsr(values: Sequence[float], trials: int, trial_sharpes: Sequence[float] | None = None) -> float | None:
    """PSR against the luck hurdle for `trials`. The variance of Sharpe across trials comes from the
    trials' own per-session Sharpes when two or more are known, otherwise from this series' own
    sampling variance, (1 + SR^2/2) / (n - 1), the null's spread."""
    m = moments(values)
    if m is None:
        return None
    known = [s for s in (trial_sharpes or []) if s is not None]
    if len(known) >= 2:
        var = statistics.variance(known)
    else:
        var = (1 + m["sharpe"] ** 2 / 2) / (m["n"] - 1)
    return _psr_from(m, expected_max_sharpe(max(int(trials), 1), var))


def min_track_record(
    values: Sequence[float], benchmark: float = 0.0, confidence: float = 0.95
) -> float | None:
    """Sessions needed for the observed Sharpe to exclude `benchmark` at `confidence`; None when the
    observed Sharpe does not exceed it (no length of record would)."""
    m = moments(values)
    if m is None or m["sharpe"] <= benchmark:
        return None
    sr = m["sharpe"]
    z = _N.inv_cdf(confidence)
    return 1 + (1 - m["skew"] * sr + (m["kurtosis"] - 1) / 4 * sr * sr) * (z / (sr - benchmark)) ** 2


def block_bootstrap_ci(
    values: Sequence[float],
    *,
    mean_block: float = 5.0,
    resamples: int = 2000,
    level: float = 0.95,
    seed: int = 7,
) -> tuple[float, float] | None:
    """A stationary-bootstrap interval for the mean (blocks of mean length `mean_block` sessions)."""
    n = len(values)
    if n < 2:
        return None
    rng = random.Random(seed)
    means = sorted(
        statistics.fmean(values[i] for i in core_stats.stationary_indices(n, mean_block, rng))
        for _ in range(resamples)
    )
    lo = means[int((1 - level) / 2 * (resamples - 1))]
    hi = means[int((1 + level) / 2 * (resamples - 1))]
    return lo, hi


def block_bootstrap_diff_ci(
    first: Sequence[float],
    second: Sequence[float],
    *,
    mean_block: float = 5.0,
    resamples: int = 2000,
    level: float = 0.95,
    seed: int = 7,
) -> tuple[float, float] | None:
    """A stationary-bootstrap interval for mean(second) - mean(first), each half resampled on its own
    (the halves are consecutive stretches of one series, not paired sessions)."""
    if len(first) < 2 or len(second) < 2:
        return None
    rng = random.Random(seed)

    def one(values: Sequence[float]) -> float:
        picks = core_stats.stationary_indices(len(values), mean_block, rng)
        return statistics.fmean(values[i] for i in picks)

    diffs = sorted(one(second) - one(first) for _ in range(resamples))
    return diffs[int((1 - level) / 2 * (resamples - 1))], diffs[int((1 + level) / 2 * (resamples - 1))]


def summary(values: Sequence[float], *, trials: int, trial_sharpes: Sequence[float] | None = None) -> dict:
    """Everything a result is reported with: per-session moments, PSR, DSR at the trial count, the
    record length it would need, and the block-bootstrap interval on the mean."""
    m = moments(values)
    ci = block_bootstrap_ci(values)
    return {
        "sessions": len(values),
        "total": round(sum(values), 2),
        "mean": None if m is None else round(m["mean"], 2),
        "sd": None if m is None else round(m["sd"], 2),
        "sharpe_per_session": None if m is None else round(m["sharpe"], 4),
        "psr": None if m is None else _round(psr(values)),
        "trials": trials,
        "dsr": _round(dsr(values, trials, trial_sharpes)),
        "min_track_record": _round(min_track_record(values), 1),
        "mean_ci95_block_bootstrap": None if ci is None else [round(ci[0], 2), round(ci[1], 2)],
    }


def _round(v: float | None, nd: int = 4) -> float | None:
    return None if v is None else round(v, nd)
