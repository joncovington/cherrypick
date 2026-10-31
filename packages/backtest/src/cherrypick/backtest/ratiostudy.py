"""Item 34's declared test of the option-to-stock premium ratio: the rules, pure.

`docs/edge-hypotheses-plan.md` item 34 declares the test. This module is the arithmetic only: plain
rows in, plain numbers out, so every rule can be shown firing and failing on invented data.
`scripts/option_stock_ratio_item34.py` reads the stores, builds a `Scan` and calls `evaluate`.

**The signal** of a name on a grid session is the premium of the day's single-leg option orders over
the stock's dollar volume (raw close times volume). The premium is read from the store's premium
histogram (`flow.histogram`, every single-leg order of the name-day, whatever its size or side), each
bucket at its geometric midpoint (`premium_of`). Two buckets have no midpoint: the underflow bucket
(under $100) is counted at half its ceiling and the overflow bucket (over $10M) at its floor.

**The spread** of a session is the mean outcome of the lowest decile of the ratio less that of the
highest (long the lowest, short the highest), less `COST_SIDE` a side on each of the two legs. The
outcome is item 32's: D+1 close to D+6 close, name minus SPY, so SPY cancels in the spread. Deciles
are by rank within the session (ties broken by symbol), `n // 10` names at each end. Sessions are
weighted equally; the interval is the stationary bootstrap over sessions.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from cherrypick.core import robustness

from . import flow, stats

#: A side's cost on one leg, as a return: 0.10%.
COST_SIDE = 0.001
#: Two legs, each entered and exited: four sides.
SIDES = 4
COST_STRESS = 1.5
MIN_SESSIONS = 100  # grid sessions with both deciles filled, in the design window
MIN_HOLDOUT_SESSIONS = 30  # the plan states no holdout floor; item 32's is used
MIN_NAMES = 10  # a session needs this many names for a decile to hold one
ALPHA = 0.05
RESAMPLES = 20_000
SEED = 34
TERCILES = 3


# ------------------------------------------------------------------------------------------------
# The signal.


def bucket_value(i: int) -> float:
    """The premium one order in histogram bucket `i` is counted at: the geometric midpoint of
    [10**(LOG_LO + (i-1)B), 10**(LOG_LO + iB)); the underflow bucket at half $100, the overflow
    bucket at its floor."""
    if i == 0:
        return 10.0**flow.LOG_LO / 2.0
    if i == flow.N_BUCKETS - 1:
        return flow.bucket_floor(i)
    return 10.0 ** (flow.LOG_LO + (i - 0.5) * flow.BUCKET)


def premium_of(histogram: Sequence[int]) -> float:
    """The name-day's single-leg premium, summed from its histogram at bucket midpoints."""
    return sum(n * bucket_value(i) for i, n in enumerate(histogram) if n)


def ratio_of(histogram: Sequence[int], close: float | None, volume: float | None) -> float | None:
    """Premium over dollar volume (raw close times volume); None without a positive dollar volume."""
    if not close or not volume or close <= 0 or volume <= 0:
        return None
    premium = premium_of(histogram)
    if premium <= 0:  # no single-leg orders: not "light" trading, no signal
        return None
    return premium / (close * volume)


# ------------------------------------------------------------------------------------------------
# Scan: what the study reads.


@dataclass
class Scan:
    """One window's readings. ``grid``: the grid sessions in order. ``rows``: session -> name-rows,
    each {sym, ratio, ex (unsigned 5-session excess over SPY), dvol (dollar volume), prior (the
    prior five sessions' return, or None), reported (a report's reaction day falls in the outcome
    window)}."""

    grid: list[str]
    rows: dict[str, list[dict]]


def deciles(rows: Sequence[dict]) -> tuple[list[dict], list[dict]] | None:
    """(lowest, highest) tenth of `rows` by ratio, `n // 10` each, ties by symbol; None under
    `MIN_NAMES` names."""
    n = len(rows)
    if n < MIN_NAMES:
        return None
    k = n // 10
    ordered = sorted(rows, key=lambda r: (r["ratio"], r["sym"]))
    return ordered[:k], ordered[n - k :]


def cost(multiple: float = 1.0) -> float:
    return SIDES * COST_SIDE * multiple


def session_gross(rows: Sequence[dict]) -> float | None:
    """Lowest-decile mean outcome less highest-decile mean outcome, before costs."""
    d = deciles(rows)
    if d is None:
        return None
    low, high = d
    return statistics.fmean(r["ex"] for r in low) - statistics.fmean(r["ex"] for r in high)


def session_long_only(rows: Sequence[dict]) -> float | None:
    """The lowest decile's mean outcome less the session's mean (gross of costs)."""
    d = deciles(rows)
    if d is None:
        return None
    return statistics.fmean(r["ex"] for r in d[0]) - statistics.fmean(r["ex"] for r in rows)


def series(
    scan: Scan,
    sessions: Sequence[str],
    keep: Callable[[dict], bool] = lambda r: True,
    reader: Callable[[Sequence[dict]], float | None] = session_gross,
) -> dict:
    """Per session, the gross spread (or `reader`'s reading) over the rows `keep` leaves in, for the
    sessions where both deciles fill."""
    out_s, out_g = [], []
    for s in sessions:
        g = reader([r for r in scan.rows.get(s, ()) if keep(r)])
        if g is not None:
            out_s.append(s)
            out_g.append(g)
    return {"sessions": out_s, "gross": out_g}


def mean_of(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


def net(gross: Sequence[float], multiple: float = 1.0) -> list[float]:
    c = cost(multiple)
    return [g - c for g in gross]


def lower_bound(values: Sequence[float], *, resamples: int = RESAMPLES) -> float | None:
    """The one-sided 95% lower bound of the mean: the lower end of a two-sided 90% stationary
    bootstrap interval (mean block 5, seeded)."""
    ci = stats.block_bootstrap_ci(values, level=1.0 - 2.0 * ALPHA, resamples=resamples, seed=SEED)
    return None if ci is None else ci[0]


def halves(values: Sequence[float]) -> tuple[list[float], list[float]]:
    """The first and second half of a series in time order (the middle session to the second)."""
    h = len(values) // 2
    return list(values[:h]), list(values[h:])


def name_half_of(r: dict) -> int:
    return robustness.name_half(r["sym"])


def tercile_rows(rows: Sequence[dict], key: str) -> list[list[dict]]:
    """`rows` cut into three equal-count groups by `key`, lowest first; rows lacking the key are left
    out. Fewer than three rows per group leaves the groups short, not padded."""
    have = sorted((r for r in rows if r.get(key) is not None), key=lambda r: (r[key], r["sym"]))
    n = len(have)
    return [have[n * t // TERCILES : n * (t + 1) // TERCILES] for t in range(TERCILES)]


def tercile_readings(scan: Scan, sessions: Sequence[str], key: str) -> list[dict]:
    """Reported, not judged: the net spread read inside each tercile of `key`, deciles of the ratio
    taken within the tercile, sessions weighted equally."""
    out = []
    for t in range(TERCILES):
        gross = []
        for s in sessions:
            g = session_gross(tercile_rows(scan.rows.get(s, ()), key)[t])
            if g is not None:
                gross.append(g)
        m = mean_of(net(gross))
        out.append({"tercile": t + 1, "sessions": len(gross), "mean": m})
    return out


# ------------------------------------------------------------------------------------------------
# The study.


def verdict_word(sessions: int, passed: bool, minimum: int = MIN_SESSIONS) -> str:
    return "no verdict" if sessions < minimum else "pass" if passed else "fail"


def _positive(values: Sequence[float]) -> bool:
    m = mean_of(values)
    return m is not None and m > 0


def evaluate(scan: Scan, *, resamples: int = RESAMPLES) -> dict:
    """The design window: the headline spread, the five pass conditions and the reported readings."""
    sessions = list(scan.grid)
    main = series(scan, sessions)
    gross = main["gross"]
    netted = net(gross)
    lo = lower_bound(netted, resamples=resamples) if len(netted) >= 2 else None
    first, second = halves(netted)
    name_halves = {}
    for h in (0, 1):
        nh = series(scan, sessions, keep=lambda r, h=h: name_half_of(r) == h)
        name_halves[f"name_half_{h}"] = {"sessions": len(nh["gross"]), "mean": mean_of(net(nh["gross"]))}
    clean = series(scan, sessions, keep=lambda r: not r["reported"])
    stressed = net(gross, COST_STRESS)
    checks = {
        "mean_positive": _positive(netted),
        "lower_bound_positive": lo is not None and lo > 0,
        "first_half_positive": _positive(first),
        "second_half_positive": _positive(second),
        "name_halves_positive": all(v["mean"] is not None and v["mean"] > 0 for v in name_halves.values()),
        "stressed_costs_positive": _positive(stressed),
        "no_reports_positive": _positive(net(clean["gross"])),
    }
    only = series(scan, sessions, reader=session_long_only)
    return {
        "sessions": len(gross),
        "mean_gross": mean_of(gross),
        "mean_net": mean_of(netted),
        "lower_bound": lo,
        "cost": cost(),
        "halves": {"first": mean_of(first), "second": mean_of(second)},
        "name_halves": name_halves,
        "stressed": {"multiple": COST_STRESS, "mean": mean_of(stressed)},
        "no_reports": {"sessions": len(clean["gross"]), "mean": mean_of(net(clean["gross"]))},
        "dollar_volume_terciles": tercile_readings(scan, sessions, "dvol"),
        "prior_return_terciles": tercile_readings(scan, sessions, "prior"),
        "long_only": {
            "sessions": len(only["gross"]),
            "mean_gross": mean_of(only["gross"]),
            "mean_net": mean_of([g - cost(0.5) for g in only["gross"]]),
        },
        "checks": checks,
        "verdict": verdict_word(len(gross), all(checks.values())),
    }


def evaluate_holdout(scan: Scan) -> dict:
    """The holdout, read once: the mean net spread above zero."""
    main = series(scan, list(scan.grid))
    netted = net(main["gross"])
    return {
        "sessions": len(netted),
        "mean_gross": mean_of(main["gross"]),
        "mean_net": mean_of(netted),
        "cost": cost(),
        "checks": {"mean_positive": _positive(netted)},
        "verdict": verdict_word(len(netted), _positive(netted), MIN_HOLDOUT_SESSIONS),
    }


def passed(res: dict) -> list[str]:
    return ["ratio"] if res.get("verdict") == "pass" else []
