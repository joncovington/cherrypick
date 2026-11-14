"""The three verdict bars every new study is judged on (the user's choice, 2026-11-14), and the
verdict block that holds them.

The user trades for capital efficiency, a small delta for the portfolio's size, omnidirectional
structures and no tail risk. Holding the same delta in shares is not a rival they would choose, so
these three bars decide a verdict; same-capital rivals (a long call or put at the same max loss, one
side of a structure alone, the plain condor or strangle at the same buying power) are reported beside
it and never judged:

1. **Capital** (`capital_check`): each trade's net less the T-bill rate earned on its buying power
   over the days it was held. Passes on a mean above zero with a one-sided t at or above the bar.
2. **Tail** (`tail_check`): a bad year is earned back within a year. The 99th-percentile drawdown
   over one year of trades, a stationary bootstrap of the study's own trades in time order
   (`core.killcriteria.derive`), is smaller than one year's mean net.
3. **Delta** (`delta_check`, for a structure with net delta): each trade's net less its entry delta
   financed on the same capital (`financed_delta`: the forward's move on that delta, plus the bill on
   the capital), the edge beyond direction. Passes as the capital bar does. A neutral structure
   declares it not applicable.

Every bar, sample floor, resample count and seed is a required argument: a declaration fixes them,
so the kit never chooses one. Costs are already in each net (`core.fees`, core's slippage).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from cherrypick.core import killcriteria
from cherrypick.core.stats import mean_sd, t_cdf

PASS, FAIL, NO_VERDICT = "pass", "fail", "no verdict"
NOT_APPLICABLE = "not applicable"


def bill_on(buying_power: float, rate: float, days: float) -> float:
    """What `buying_power` earns at the annual T-bill `rate` over `days` calendar days."""
    return buying_power * rate * days / 365.0


def financed_delta(delta: float, fwd0: float, fwd1: float, capital: float, rate: float, days: float) -> float:
    """The entry delta held on `capital` with the rest borrowed at the bill: the forward's change
    carries the borrowing and the dividend, the capital earns the bill. Copied from item 74."""
    return round(delta * (fwd1 - fwd0) + bill_on(capital, rate, days), 2)


def _mean_t(values: Sequence[float]) -> dict:
    n = len(values)
    mean, sd = mean_sd(values)
    t = mean / (sd / math.sqrt(n)) if mean is not None and sd else None
    p = 1.0 - t_cdf(t, n - 1) if t is not None and n > 1 else None
    return {"n": n, "mean": mean, "t": t, "p": p}


def _judged(stats: dict, *, t_bar: float, min_n: int) -> dict:
    if stats["n"] < min_n:
        return {**stats, "verdict": NO_VERDICT, "why": f"{stats['n']} trades, under {min_n}"}
    ok = stats["mean"] is not None and stats["mean"] > 0 and stats["t"] is not None and stats["t"] >= t_bar
    return {**stats, "verdict": PASS if ok else FAIL}


def capital_check(
    nets: Sequence[float],
    buying_power: Sequence[float],
    days: Sequence[float],
    rates: Sequence[float],
    *,
    t_bar: float,
    min_n: int,
) -> dict:
    """Bar 1: the net of each trade less the bill on its buying power over its days."""
    if not (len(nets) == len(buying_power) == len(days) == len(rates)):
        raise ValueError("capital_check: one buying power, day count and rate per trade")
    excess = [n - bill_on(b, r, d) for n, b, d, r in zip(nets, buying_power, days, rates, strict=True)]
    return _judged(_mean_t(excess), t_bar=t_bar, min_n=min_n)


def tail_check(
    nets: Sequence[float],
    *,
    per_year: int,
    min_n: int,
    resamples: int,
    seed: int,
    sessions: int | None = None,
) -> dict:
    """Bar 2: the 99th-percentile drawdown over `per_year` trades (the bootstrap of `nets` in time
    order) is smaller than `per_year` trades of mean net. `sessions` (how many sessions the trades
    span) sets the bootstrap's mean block, as `killcriteria.derive` reads it."""
    n = len(nets)
    if n < min_n:
        return {"n": n, "verdict": NO_VERDICT, "why": f"{n} trades, under {min_n}"}
    mean, _ = mean_sd(nets)
    dd = killcriteria.derive(list(nets), sessions=sessions, horizon=per_year, resamples=resamples, seed=seed)
    if dd.get("insufficient"):
        return {"n": n, "verdict": NO_VERDICT, "why": f"under {dd['min_evidence']} trades for the bootstrap"}
    year = mean * per_year
    p99 = dd["drawdown_block"]
    ok = mean > 0 and p99 < year
    return {
        "n": n,
        "per_year": per_year,
        "mean": mean,
        "year_net": year,
        "drawdown_p99_year": p99,
        "mean_block": dd["mean_block"],
        "verdict": PASS if ok else FAIL,
    }


def delta_check(
    nets: Sequence[float],
    financed: Sequence[float],
    *,
    t_bar: float,
    min_n: int,
) -> dict:
    """Bar 3: each trade's net less its `financed_delta` on the same capital."""
    if len(nets) != len(financed):
        raise ValueError("delta_check: one financed delta per trade")
    excess = [n - f for n, f in zip(nets, financed, strict=True)]
    return _judged(_mean_t(excess), t_bar=t_bar, min_n=min_n)


def verdict(capital: dict, tail: dict, delta: dict | None) -> dict:
    """The verdict block: each bar's result and the arm's verdict. A pass needs every applicable bar
    to pass; any no-verdict bar (and no failed one) makes the arm no verdict. `delta` None is a
    neutral structure, recorded as not applicable."""
    bars = {"capital": capital, "tail": tail, "delta": delta or {"verdict": NOT_APPLICABLE}}
    judged = [b["verdict"] for b in bars.values() if b["verdict"] != NOT_APPLICABLE]
    if FAIL in judged:
        out = FAIL
    elif NO_VERDICT in judged:
        out = NO_VERDICT
    else:
        out = PASS
    failed = [k for k, b in bars.items() if b["verdict"] == FAIL]
    return {"verdict": out, "failed": failed, "bars": bars}
