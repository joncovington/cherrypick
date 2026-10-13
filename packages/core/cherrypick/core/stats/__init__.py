"""cherrypick.core.stats — the suite's one home for comparing an arm against its control.

Every module was already asking the same handful of questions of its arms, and answering them in
its own file: a seeded session bootstrap here (`regimecuts`), an unseeded one there
(`meic.analytics`), a pooled-trade-mean "difference" that was not paired at all
(`meic.experiment`), a one-sided t bound with a hard-coded edge (`flies.intraday_eval`), a sign
test, a Holm step-down (`technicals.study`), and a stationary block bootstrap
(`rangefeatures`). This module is where those answers live now, so two surfaces reading the same
pair cannot disagree about what the interval is.

**Sessions are the unit, never trades.** Entries inside one session share a tape, so every
function here takes per-session values (or a paired per-session difference), and the bootstrap
resamples sessions. A trade-level n reads as power the sample does not have.

**The pairing rule.** `paired_diffs` compares an arm with its control session by session.
`fill=None` keeps only the sessions BOTH traded (the intersection); a number keeps every session
either traded and reads the absent side as that value -- `fill=0.0` is the right choice inside a
window where both arms were running, because an entry filter that blocked a whole session is
informative, not missing (docs/paper-experiments.md), and it makes the diffs sum to exactly the
difference in net over the window. The caller decides which, because only the caller knows the
window.

**Deterministic.** Every bootstrap takes a seed (default `DEFAULT_SEED`) and draws from its own
`random.Random`, so a re-run over last month's rows reproduces the interval exactly.

Pure functions over numbers, standard library only. Nothing here reads a ledger, a clock or a file.
"""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Mapping, Sequence

DEFAULT_SEED = 20261012
DEFAULT_RESAMPLES = 2000
DEFAULT_LEVEL = 0.95
DEFAULT_ALPHA = 0.05
DEFAULT_POWER = 0.80


# --------------------------------------------------------------------------- per-session values
def session_values(
    records: Sequence[Mapping], *, session_key: str = "session", value_key: str = "net_pnl"
) -> dict[str, float]:
    """`{session: summed value}` over normalised records (`core.ledgers` shape). Undated rows are
    dropped: they cannot be paired with a day that is not known."""
    out: dict[str, float] = {}
    for r in records:
        s = r.get(session_key)
        if not s:
            continue
        out[str(s)] = out.get(str(s), 0.0) + float(r.get(value_key) or 0.0)
    return dict(sorted(out.items()))


def paired_diffs(
    arm: Mapping[str, float],
    control: Mapping[str, float],
    *,
    fill: float | None = None,
    sessions: Sequence[str] | None = None,
) -> tuple[list[str], list[float]]:
    """`(sessions, arm - control per session)`, in session order.

    `fill=None`: only sessions both sides carry. `fill=x`: every session either side carries, the
    absent side read as `x`. `sessions`, when given, restricts the comparison to that set first
    (an era window, an experiment's own dates)."""
    keys_a, keys_c = set(arm), set(control)
    if sessions is not None:
        allowed = set(sessions)
        keys_a &= allowed
        keys_c &= allowed
    shared = sorted(keys_a & keys_c) if fill is None else sorted(keys_a | keys_c)

    def side(values: Mapping[str, float], keys: set, s: str) -> float:
        return float(values[s]) if s in keys else float(fill)

    return shared, [side(arm, keys_a, s) - side(control, keys_c, s) for s in shared]


def mean_sd(values: Sequence[float]) -> tuple[float | None, float | None]:
    """Mean and sample standard deviation (n - 1). None where undefined."""
    n = len(values)
    if n == 0:
        return None, None
    mean = statistics.fmean(values)
    if n < 2:
        return mean, None
    return mean, statistics.stdev(values)


# --------------------------------------------------------------------------- bootstrap
def bootstrap_ci(
    values: Sequence[float],
    *,
    statistic: str = "mean",
    level: float = DEFAULT_LEVEL,
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = DEFAULT_SEED,
) -> tuple[float, float] | None:
    """Percentile interval of the mean (or `statistic="sum"`, the total) of `values`, resampling
    the values themselves with replacement -- pass per-session values and this is the session
    bootstrap. Seeded. None on an empty series.

    The index rule is `regimecuts`' original one (lower at `int(tail * R)`, upper at
    `ceil((1 - tail) * R) - 1`), kept byte-for-byte because its stored artifacts were stamped with
    it."""
    k = len(values)
    if k == 0:
        return None
    if statistic not in ("mean", "sum"):
        raise ValueError(f"bootstrap_ci: unknown statistic {statistic!r}")
    rng = random.Random(seed)
    data = list(values)
    if statistic == "sum":
        draws = sorted(sum(rng.choices(data, k=k)) for _ in range(resamples))
    else:
        draws = sorted(sum(rng.choices(data, k=k)) / k for _ in range(resamples))
    tail = (1 - level) / 2
    lo = draws[int(tail * resamples)]
    hi = draws[math.ceil((1 - tail) * resamples) - 1]
    return lo, hi


def stationary_indices(n: int, mean_block: float, rng: random.Random) -> list[int]:
    """One stationary-bootstrap resample (Politis & Romano 1994): blocks of geometric length with
    the given mean, wrapping circularly, so the resample keeps the series' short-range dependence.
    For a series whose neighbours are not independent (overlapping multi-session ranges); a
    session-level comparison of two arms uses `bootstrap_ci`."""
    p = 1.0 / mean_block
    out = []
    j = rng.randrange(n)
    for _ in range(n):
        out.append(j)
        j = rng.randrange(n) if rng.random() < p else (j + 1) % n
    return out


# --------------------------------------------------------------------------- Student's t
def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the regularised incomplete beta (modified Lentz)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    d = tiny if abs(d) < tiny else d
    d = 1.0 / d
    h = d
    for m in range(1, 400):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = tiny if abs(d) < tiny else d
        c = 1.0 + aa / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = tiny if abs(d) < tiny else d
        c = 1.0 + aa / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    return h


def _betainc(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_cdf(t: float, df: float) -> float:
    """Student's t distribution function."""
    if df <= 0:
        raise ValueError("t_cdf: needs positive degrees of freedom")
    x = df / (df + t * t)
    tail = 0.5 * _betainc(df / 2.0, 0.5, x)
    return 1.0 - tail if t > 0 else tail


def t_quantile(p: float, df: float) -> float:
    """Inverse of `t_cdf` (exact to ~1e-10, by bisection on the distribution function)."""
    if not 0.0 < p < 1.0:
        raise ValueError("t_quantile: p must be inside (0, 1)")
    if df <= 0:
        raise ValueError("t_quantile: needs positive degrees of freedom")
    if p == 0.5:
        return 0.0
    if p < 0.5:
        return -t_quantile(1.0 - p, df)
    lo, hi = 0.0, 1.0
    while t_cdf(hi, df) < p:
        hi *= 2.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-12:
            break
    return (lo + hi) / 2.0


def t_lower_bound(
    values: Sequence[float], *, alpha: float = DEFAULT_ALPHA, critical: float | None = None
) -> float | None:
    """One-sided (1 - alpha) lower confidence bound on the mean: mean - t * sd / sqrt(n). None
    below two values. `critical` overrides the exact t quantile for a caller that has declared a
    more conservative table (flies' intraday qualification rounds toward caution past 30 df)."""
    n = len(values)
    if n < 2:
        return None
    mean, sd = mean_sd(values)
    t = critical if critical is not None else t_quantile(1.0 - alpha, n - 1)
    return mean - t * sd / math.sqrt(n)


# --------------------------------------------------------------------------- sign test
def sign_test_p(wins: int, losses: int, *, alternative: str = "two-sided") -> float | None:
    """Exact binomial sign test at p = 1/2; ties are dropped before the call. None with nothing to
    test. `alternative`: "two-sided", "greater" (wins exceed chance) or "less"."""
    n = wins + losses
    if n == 0:
        return None
    denom = 2**n
    if alternative == "greater":
        return sum(math.comb(n, i) for i in range(wins, n + 1)) / denom
    if alternative == "less":
        return sum(math.comb(n, i) for i in range(0, wins + 1)) / denom
    if alternative != "two-sided":
        raise ValueError(f"sign_test_p: unknown alternative {alternative!r}")
    tail = sum(math.comb(n, i) for i in range(min(wins, losses) + 1)) / denom
    return min(1.0, 2 * tail)


# --------------------------------------------------------------------------- power
def _z(p: float) -> float:
    return statistics.NormalDist().inv_cdf(p)


def sessions_needed(
    sd_diff: float | None,
    edge: float | None,
    *,
    alpha: float = DEFAULT_ALPHA,
    power: float = DEFAULT_POWER,
    sided: int = 1,
) -> int | None:
    """Sessions a paired difference with this spread needs to detect a true mean of `edge` at
    `power`, with a `sided`-sided test at `alpha`: ceil(((z_alpha + z_power) * sd / edge)^2).
    None when the spread or the edge is unknown or zero."""
    if not sd_diff or not edge or sd_diff <= 0:
        return None
    z = _z(1 - alpha / sided) + _z(power)
    return math.ceil((z * sd_diff / abs(edge)) ** 2)


def minimum_detectable_effect(
    sd_diff: float | None,
    n: int,
    *,
    alpha: float = DEFAULT_ALPHA,
    power: float = DEFAULT_POWER,
    sided: int = 1,
) -> float | None:
    """The smallest true mean difference `n` sessions detect at `power`: (z_alpha + z_power) * sd /
    sqrt(n). The inverse of `sessions_needed`."""
    if sd_diff is None or n < 1:
        return None
    return (_z(1 - alpha / sided) + _z(power)) * sd_diff / math.sqrt(n)


def sessions_to_exclude_zero(
    mean: float | None, sd: float | None, n: int, *, level: float = DEFAULT_LEVEL
) -> int | None:
    """Further sessions until a two-sided `level` interval on the mean would exclude zero, IF the
    observed mean and spread held: ceil((z * sd / mean)^2) - n, floored at 0. A projection, not a
    promise -- the mean moves as sessions arrive. 0 when the interval already excludes zero at that
    spread; None with no mean, no spread or a zero mean (no finite answer)."""
    if mean is None or sd is None or mean == 0:
        return None
    if sd == 0:
        return 0
    z = _z(1 - (1 - level) / 2)
    return max(0, math.ceil((z * sd / abs(mean)) ** 2) - n)


# --------------------------------------------------------------------------- multiplicity
def holm(pvalues: Mapping[str, float], alpha: float = DEFAULT_ALPHA) -> dict[str, bool]:
    """Holm's step-down: reject the k-th smallest p while p <= alpha / (m - k); stop at the first
    acceptance. Controls the family-wise error rate."""
    order = sorted(pvalues, key=lambda k: pvalues[k])
    m, out, stop = len(order), {}, False
    for k, key in enumerate(order):
        ok = not stop and pvalues[key] <= alpha / (m - k)
        stop = stop or not ok
        out[key] = ok
    return out


def holm_adjusted(pvalues: Mapping[str, float]) -> dict[str, float]:
    """Holm-adjusted p-values: max over the first k of (m - j) * p_(j), capped at 1."""
    order = sorted(pvalues, key=lambda k: pvalues[k])
    m, running, out = len(order), 0.0, {}
    for k, key in enumerate(order):
        running = max(running, min(1.0, (m - k) * pvalues[key]))
        out[key] = running
    return out


def benjamini_hochberg(pvalues: Mapping[str, float], q: float = 0.10) -> dict[str, bool]:
    """Benjamini–Hochberg step-up: reject every p at or below the largest p_(k) <= k * q / m.
    Controls the false discovery rate."""
    order = sorted(pvalues, key=lambda k: pvalues[k])
    m = len(order)
    cut = 0
    for k, key in enumerate(order, start=1):
        if pvalues[key] <= k * q / m:
            cut = k
    return {key: i < cut for i, key in enumerate(order)}


def bh_adjusted(pvalues: Mapping[str, float]) -> dict[str, float]:
    """BH-adjusted p-values (q-values): min over j >= k of m * p_(j) / j, capped at 1."""
    order = sorted(pvalues, key=lambda k: pvalues[k])
    m = len(order)
    out: dict[str, float] = {}
    running = 1.0
    for k in range(m, 0, -1):
        key = order[k - 1]
        running = min(running, m * pvalues[key] / k)
        out[key] = min(1.0, running)
    return out


# --------------------------------------------------------------------------- the paired comparison
def _r(x: float | None, nd: int = 2) -> float | None:
    return None if x is None else round(x, nd)


def paired_comparison(
    arm: Mapping[str, float],
    control: Mapping[str, float],
    *,
    fill: float | None = None,
    sessions: Sequence[str] | None = None,
    level: float = DEFAULT_LEVEL,
    alpha: float = DEFAULT_ALPHA,
    power: float = DEFAULT_POWER,
    edge: float | None = None,
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = DEFAULT_SEED,
) -> dict:
    """An arm against its control, session by session: the one block every surface shows.

    `{sessions, first, last, mean_diff, sd_diff, total_diff, ci: [lo, hi], ci_level,
    ci_excludes_zero, t_lower, alpha, arm_better, control_better, ties, sign_test_p,
    sessions_to_exclude_zero, edge, sessions_needed, mde}` -- money rounded to the cent.
    `t_lower` is the one-sided (1 - alpha) bound; `sign_test_p` is two-sided; `sessions_needed` and
    `mde` are at `power` for `edge` (None without one). Nothing is labelled pass or fail: the
    thresholds belong to whoever reads it."""
    shared, diffs = paired_diffs(arm, control, fill=fill, sessions=sessions)
    n = len(diffs)
    mean, sd = mean_sd(diffs)
    ci = bootstrap_ci(diffs, level=level, resamples=resamples, seed=seed) if n >= 2 else None
    wins = sum(1 for d in diffs if d > 0)
    losses = sum(1 for d in diffs if d < 0)
    p = sign_test_p(wins, losses)
    return {
        "sessions": n,
        "first": shared[0] if shared else None,
        "last": shared[-1] if shared else None,
        "pairing": "intersection" if fill is None else f"union_fill_{fill:g}",
        "mean_diff": _r(mean),
        "sd_diff": _r(sd),
        "total_diff": _r(sum(diffs)) if n else None,
        "ci": [_r(ci[0]), _r(ci[1])] if ci else None,
        "ci_level": level,
        "ci_excludes_zero": (ci[0] > 0 or ci[1] < 0) if ci else None,
        "t_lower": _r(t_lower_bound(diffs, alpha=alpha)),
        "alpha": alpha,
        "arm_better": wins,
        "control_better": losses,
        "ties": n - wins - losses,
        "sign_test_p": _r(p, 4),
        "sessions_to_exclude_zero": (
            0 if ci and (ci[0] > 0 or ci[1] < 0) else sessions_to_exclude_zero(mean, sd, n, level=level)
        ),
        "edge": edge,
        "power": power,
        "sessions_needed": sessions_needed(sd, edge, alpha=alpha, power=power),
        "mde": _r(minimum_detectable_effect(sd, n, alpha=alpha, power=power)) if n else None,
    }


__all__ = [
    "DEFAULT_ALPHA",
    "DEFAULT_LEVEL",
    "DEFAULT_POWER",
    "DEFAULT_RESAMPLES",
    "DEFAULT_SEED",
    "benjamini_hochberg",
    "bh_adjusted",
    "bootstrap_ci",
    "holm",
    "holm_adjusted",
    "mean_sd",
    "minimum_detectable_effect",
    "paired_comparison",
    "paired_diffs",
    "session_values",
    "sessions_needed",
    "sessions_to_exclude_zero",
    "sign_test_p",
    "stationary_indices",
    "t_cdf",
    "t_lower_bound",
    "t_quantile",
]
