"""cherrypick.core.rangefeatures — the daily range-features study, exactly as declared.

The contract is `docs/range-features.md`, declared 2026-10-03 before any feature here had been
computed against an outcome. This module implements that declaration and decides nothing it did
not. A definition changed here is a new declaration, not a fix.

The question: do four end-of-day SPX features (`nr7`, `close_extremity`, `channel_extremity`,
`round_distance`) forecast the range the next 1, 5 or 7 sessions travel BEYOND a HAR baseline on
ranges? Every test is on the baseline's residual, so a feature counts only for what it adds to
recent range at three speeds.

**Every feature for session t reads bars through t and nothing later.** `feature_series` is written
so that a test can compute it on the full history and again on bars cut off at each t and require
the two to agree; that test, shown to fail when a window reads one bar ahead, runs before any
statistic means anything.

Choices the declaration left open, made once here and stated so they cannot drift:

* **Tercile cut points** come from `statistics.quantiles(n=3, method="inclusive")` over every
  calibration-segment session where the feature is defined. A feature needs no outcome, so the rule
  that drops calibration rows whose outcome crosses into evaluation does not apply to it. Bottom is
  at or below the first cut, top strictly above the second.
* **The bootstrap p-value** is two-sided and centred: (1 + #{|theta* - theta| >= |theta|}) / (B + 1).
  Every test reseeds with `SEED`, so each one reproduces on its own.
* **A conditioner cell** is readable only when both groups it compares hold `MIN_EFFECTIVE_N`
  sessions.
* **Evaluation reads bars through the declared end only**, and the result records the last bar it
  used; `--bars-through` reproduces a past run exactly however many bars have arrived since.
* **The panel cross-check is not run.** The technicals panel starts 2023-08-30, after the
  calibration segment ends, so it has nothing to fit a baseline on, and the declaration does not say
  how to borrow SPX's. Recorded as not run rather than improvised after the fact.

Pure functions over daily bars plus one CLI; standard library only. Derived, never recorded.
Reports only and gates nothing.
"""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Sequence

from cherrypick.core import marketregime as _mr
from cherrypick.core import metrics as _metrics

DECLARATION = "docs/range-features.md"
DECLARED = "2026-10-03"
CALIBRATION_THROUGH = "2022-12-30"
EVALUATION_FROM = "2023-01-03"
EVALUATION_THROUGH = "2026-10-02"
FORWARD_FROM = "2026-10-05"

HORIZONS = (1, 5, 7)
FEATURES = ("nr7", "close_extremity", "channel_extremity", "round_distance")
DECLARED_SIGN = {feature: 1 for feature in FEATURES}

FAMILY_ALPHA = 0.05
THRESHOLD = FAMILY_ALPHA / (len(FEATURES) * len(HORIZONS))
MIN_EFFECT = 0.05
FORWARD_CHECKPOINT = 60
RESAMPLES = 10_000
SEED = 20261003
MIN_BLOCK = 10
MIN_CELL = _metrics.MIN_EFFECTIVE_N

NR_WINDOW = 7
CHANNEL_WINDOW = 20
ATR_WINDOW = 20
ROUND_STEP = 100.0
HAR_LAGS = (1, 5, 22)


# --------------------------------------------------------------------------- features


def feature_series(bars: Sequence[dict]) -> list[dict]:
    """The four declared features for every session, oldest first. A feature that its window cannot
    support yet, or whose denominator is zero, is None rather than a number."""
    trs = _mr.true_ranges(list(bars))
    out = []
    for i, bar in enumerate(bars):
        high, low, close = bar["high"], bar["low"], bar["close"]
        width = high - low
        row: dict = {"session": bar["session"]}

        if i >= NR_WINDOW - 1:
            prior = [b["high"] - b["low"] for b in bars[i - NR_WINDOW + 1 : i]]
            row["nr7"] = 1 if width < min(prior) else 0
        else:
            row["nr7"] = None

        row["close_extremity"] = abs(2 * (close - low) / width - 1) if width > 0 else None

        if i >= CHANNEL_WINDOW - 1:
            window = bars[i - CHANNEL_WINDOW + 1 : i + 1]
            top, bottom = max(b["high"] for b in window), min(b["low"] for b in window)
            span = top - bottom
            row["channel_extremity"] = abs(2 * (close - bottom) / span - 1) if span > 0 else None
        else:
            row["channel_extremity"] = None

        chunk = trs[i - ATR_WINDOW + 1 : i + 1] if i >= ATR_WINDOW - 1 else []
        if len(chunk) == ATR_WINDOW and all(t is not None for t in chunk):
            atr = statistics.fmean(chunk)
            nearest = round(close / ROUND_STEP) * ROUND_STEP
            row["round_distance"] = abs(close - nearest) / atr if atr > 0 else None
        else:
            row["round_distance"] = None
        out.append(row)
    return out


# --------------------------------------------------------------------------- baseline and outcome


def ranges(bars: Sequence[dict]) -> list[float]:
    """r_t = (H_t - L_t) / C_t."""
    return [(b["high"] - b["low"]) / b["close"] for b in bars]


def har_regressors(r: Sequence[float], i: int) -> list[float] | None:
    """[ln r_t, ln mean(r over 5), ln mean(r over 22)], or None before the longest lag is full or
    when a range is zero (its log is undefined)."""
    longest = max(HAR_LAGS)
    if i < longest - 1:
        return None
    out = []
    for lag in HAR_LAGS:
        value = statistics.fmean(r[i - lag + 1 : i + 1])
        if value <= 0:
            return None
        out.append(math.log(value))
    return out


def outcome(bars: Sequence[dict], i: int, h: int) -> float | None:
    """ln y_h(t): ln of (max high - min low over the next h sessions) / C_t. None when the window is
    not complete."""
    if i + h >= len(bars):
        return None
    window = bars[i + 1 : i + h + 1]
    y = (max(b["high"] for b in window) - min(b["low"] for b in window)) / bars[i]["close"]
    return math.log(y) if y > 0 else None


def ols(rows: Sequence[Sequence[float]], ys: Sequence[float]) -> list[float]:
    """Least squares with an intercept, by the normal equations and Gaussian elimination with
    partial pivoting. Returns [intercept, *slopes]."""
    k = len(rows[0]) + 1
    xtx = [[0.0] * k for _ in range(k)]
    xty = [0.0] * k
    for row, y in zip(rows, ys, strict=True):
        x = [1.0, *row]
        for a in range(k):
            xty[a] += x[a] * y
            for b in range(k):
                xtx[a][b] += x[a] * x[b]
    m = [xtx[a] + [xty[a]] for a in range(k)]
    for col in range(k):
        pivot = max(range(col, k), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            raise ValueError("singular design: the baseline cannot be fitted")
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(k):
            if r != col:
                factor = m[r][col] / m[col][col]
                for c in range(col, k + 1):
                    m[r][c] -= factor * m[col][c]
    return [m[a][k] / m[a][a] for a in range(k)]


def fit_baseline(bars: Sequence[dict], h: int) -> dict:
    """The HAR baseline for horizon h, fitted on calibration rows only: t on or before
    `CALIBRATION_THROUGH` and an outcome window that ends there too, so no outcome crosses into
    evaluation."""
    r = ranges(bars)
    xs, ys = [], []
    for i, bar in enumerate(bars):
        if bar["session"] > CALIBRATION_THROUGH or i + h >= len(bars):
            continue
        if bars[i + h]["session"] > CALIBRATION_THROUGH:
            continue
        x, y = har_regressors(r, i), outcome(bars, i, h)
        if x is None or y is None:
            continue
        xs.append(x)
        ys.append(y)
    coefs = ols(xs, ys)
    fitted = [coefs[0] + sum(c * v for c, v in zip(coefs[1:], x, strict=True)) for x in xs]
    mean_y = statistics.fmean(ys)
    ss_res = sum((y - f) ** 2 for y, f in zip(ys, fitted, strict=True))
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    return {
        "horizon": h,
        "coefficients": coefs,
        "rows": len(ys),
        "r_squared": 1 - ss_res / ss_tot if ss_tot > 0 else None,
        "residual_sd": statistics.stdev([y - f for y, f in zip(ys, fitted, strict=True)]),
    }


def excess(bars: Sequence[dict], coefs: Sequence[float], h: int) -> list[float | None]:
    """e_h(t) for every session: the outcome less the frozen baseline's fitted value."""
    r = ranges(bars)
    out: list[float | None] = []
    for i in range(len(bars)):
        x, y = har_regressors(r, i), outcome(bars, i, h)
        if x is None or y is None:
            out.append(None)
            continue
        out.append(y - (coefs[0] + sum(c * v for c, v in zip(coefs[1:], x, strict=True))))
    return out


# --------------------------------------------------------------------------- statistics


def tercile_cuts(values: Sequence[float]) -> tuple[float, float]:
    low, high = statistics.quantiles(values, n=3, method="inclusive")
    return low, high


def group(feature: str, value: float, cuts: tuple[float, float] | None) -> int | None:
    """1 for the side the declared sign expects to travel further (NR7 day, top tercile), 0 for the
    comparison side (other days, bottom tercile), None for the middle tercile."""
    if feature == "nr7":
        return int(value)
    assert cuts is not None
    if value <= cuts[0]:
        return 0
    if value > cuts[1]:
        return 1
    return None


def difference(values: Sequence[float], groups: Sequence[int | None]) -> float | None:
    """mean(e | group 1) - mean(e | group 0), or None when a group is empty."""
    s1 = n1 = s0 = n0 = 0
    for e, g in zip(values, groups, strict=True):
        if g == 1:
            s1 += e
            n1 += 1
        elif g == 0:
            s0 += e
            n0 += 1
    if not n1 or not n0:
        return None
    return s1 / n1 - s0 / n0


def stationary_indices(n: int, mean_block: float, rng: random.Random) -> list[int]:
    """One stationary-bootstrap resample (Politis & Romano 1994): blocks of geometric length with the
    given mean, wrapping circularly, so the resample keeps the series' short-range dependence."""
    p = 1.0 / mean_block
    out = []
    j = rng.randrange(n)
    for _ in range(n):
        out.append(j)
        j = rng.randrange(n) if rng.random() < p else (j + 1) % n
    return out


def bootstrap(values: Sequence[float], groups: Sequence[int | None], h: int) -> dict:
    """The declared inference for one test: the statistic, its centred two-sided p-value and the
    95% percentile interval, over `RESAMPLES` stationary resamples with mean block max(10, 2h)."""
    theta = difference(values, groups)
    if theta is None:
        return {"statistic": None, "p": None, "interval_95": None, "resamples_used": 0}
    rng = random.Random(SEED)
    mean_block = max(MIN_BLOCK, 2 * h)
    n = len(values)
    draws = []
    for _ in range(RESAMPLES):
        idx = stationary_indices(n, mean_block, rng)
        star = difference([values[i] for i in idx], [groups[i] for i in idx])
        if star is not None:
            draws.append(star)
    extreme = sum(1 for d in draws if abs(d - theta) >= abs(theta))
    draws.sort()
    return {
        "statistic": theta,
        "p": (1 + extreme) / (len(draws) + 1),
        "interval_95": [draws[int(0.025 * len(draws))], draws[int(0.975 * len(draws)) - 1]],
        "resamples_used": len(draws),
        "mean_block": mean_block,
    }


# --------------------------------------------------------------------------- the study


def _rows(feats, ex, feature, cuts, sessions_ok):
    values, groups, sessions = [], [], []
    for row, e in zip(feats, ex, strict=True):
        value = row[feature]
        if e is None or value is None or not sessions_ok(row["session"]):
            continue
        values.append(e)
        groups.append(group(feature, value, cuts))
        sessions.append(row["session"])
    return values, groups, sessions


def _counts(groups):
    return {"group_1": sum(1 for g in groups if g == 1), "group_0": sum(1 for g in groups if g == 0)}


def study(bars: Sequence[dict], *, bars_through: str = EVALUATION_THROUGH) -> dict:
    """Run the declared study once. `bars` may run past the declared end; evaluation reads only bars
    on or before `bars_through` (never later than `EVALUATION_THROUGH`), and the forward check reads
    every bar given."""
    bars_through = min(bars_through, EVALUATION_THROUGH)
    ev_bars = [b for b in bars if b["session"] <= bars_through]
    feats = feature_series(ev_bars)
    vol = {row["session"]: row["vol"] for row in _mr.series(list(ev_bars), "swing")}
    cuts = {
        f: tercile_cuts([r[f] for r in feats if r["session"] <= CALIBRATION_THROUGH and r[f] is not None])
        for f in FEATURES
        if f != "nr7"
    }
    all_feats = feature_series(bars)

    def evaluation(session):
        return EVALUATION_FROM <= session <= EVALUATION_THROUGH

    def forward(session):
        return session >= FORWARD_FROM

    baselines, tests = {}, []
    for h in HORIZONS:
        base = fit_baseline(ev_bars, h)
        baselines[h] = base
        ex = excess(ev_bars, base["coefficients"], h)
        ex_all = excess(bars, base["coefficients"], h)
        for f in FEATURES:
            values, groups, sessions = _rows(feats, ex, f, cuts.get(f), evaluation)
            result = bootstrap(values, groups, h)
            theta = result["statistic"]
            sign = DECLARED_SIGN[f]
            significant = result["p"] is not None and result["p"] < THRESHOLD
            sign_matches = theta is not None and theta * sign > 0
            clears = theta is not None and theta * sign >= MIN_EFFECT

            by_vol = {}
            for bucket in _mr.VOLS:
                rows = zip(values, groups, sessions, strict=True)
                picked = [(v, g) for v, g, s in rows if vol.get(s) == bucket]
                cell_groups = [g for _, g in picked]
                counts = _counts(cell_groups)
                if min(counts.values()) < MIN_CELL:
                    by_vol[bucket] = {**counts, "not_yet_readable": True}
                else:
                    by_vol[bucket] = {**counts, "statistic": difference([v for v, _ in picked], cell_groups)}

            fv, fg, _ = _rows(all_feats, ex_all, f, cuts.get(f), forward)
            fwd = {"sessions": len(fv), "checkpoint": FORWARD_CHECKPOINT}
            if len(fv) >= FORWARD_CHECKPOINT:
                fstat = difference(fv[:FORWARD_CHECKPOINT], fg[:FORWARD_CHECKPOINT])
                fwd.update(
                    statistic=fstat,
                    same_sign=fstat is not None and theta is not None and fstat * theta > 0,
                )
            else:
                fwd["status"] = "pending"

            passes = significant and sign_matches and clears
            tests.append(
                {
                    "feature": f,
                    "horizon": h,
                    "declared_sign": "+" if sign > 0 else "-",
                    "sessions": len(values),
                    **_counts(groups),
                    **result,
                    "significant": significant,
                    "sign_matches": sign_matches,
                    "effect_clears": clears,
                    "passes_evaluation": passes,
                    "against_mechanism": significant and not sign_matches,
                    "forward": fwd,
                    "finding": (passes and fwd.get("same_sign")) if "same_sign" in fwd else None,
                    "by_vol": by_vol,
                }
            )

    passing = [t for t in tests if t["passes_evaluation"]]
    if not passing:
        verdict = "closed: no feature x horizon passed the evaluation segment"
    elif any(t["finding"] is None for t in passing):
        verdict = "pending: a test passed evaluation and awaits the forward checkpoint"
    elif any(t["finding"] for t in passing):
        verdict = "finding: confirmed on the forward checkpoint"
    else:
        verdict = "closed: every test that passed evaluation failed the forward check"

    return {
        "declaration": DECLARATION,
        "declared": DECLARED,
        "bars": {
            "first": ev_bars[0]["session"] if ev_bars else None,
            "last_used_for_evaluation": ev_bars[-1]["session"] if ev_bars else None,
            "count_for_evaluation": len(ev_bars),
            "last_available": bars[-1]["session"] if bars else None,
        },
        "threshold": THRESHOLD,
        "min_effect": MIN_EFFECT,
        "tercile_cuts": cuts,
        "baselines": baselines,
        "tests": tests,
        "against_mechanism": [f"{t['feature']}@{t['horizon']}" for t in tests if t["against_mechanism"]],
        "panel_cross_check": "not run: the panel starts 2023-08-30, after calibration ends",
        "verdict": verdict,
    }
