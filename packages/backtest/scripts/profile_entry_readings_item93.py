"""Item 93: Market Profile entry readings, stage A: four forecasts on SPX prices.

The declaration is research/edge-hypotheses-plan.md section 93; this script runs it. Results are
derived from licensed data and stay under the backtest results directory, never the repo.

A forecast study, not a trade, on SPX's one-minute prices in the licensed store. Four declared tests,
the plan's candidates E1 to E4 (research/market-profile-entry-plan.md):

- `balance`: on days that open inside the prior value area, with a narrow first hour and price inside
  it at 10:30, is the rest of the day narrower than item 81's width and the HAR terms already say?
  (a regression coefficient; declared sign negative)
- `acceptance`: after two 30-minute periods trade wholly beyond the prior value area, does price go on
  that way to the close, against non-event days from the same clock time? (declared sign positive)
- `rejection`: after an open outside value and two periods wholly back inside, does price touch the far
  edge before the close more often than non-event days move that far from the same time? (positive)
- `unfinished`: are the prior session's poor highs and lows and single prints touched within five
  sessions more often than levels at the same distance on other days? (positive)

Every baseline is taken from the non-event days of the same part (design or holdout), so the design
reads nothing from the holdout. The pass rule is `FORECAST_RULE`. The pure helpers shared with items 80
and 81 are copied from their scripts (a study does not load another study's script).

- `study`: one run over the whole history, cut at the declared split, written to
  `{results}/results.json` with a top-level `verdict` (`{test: word}`).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sqlite3
import statistics
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.core import marketprofile as mp
from cherrypick.core import progress as core_progress

#: The correction log: one entry per fix to this replay, oldest first (orchestrator/studyversion.py).
REPLAY_CORRECTIONS = ()

#: The pass rule as data, fixed by the declaration before any run.
FORECAST_RULE = {
    "kind": "forecast",
    "symbol": "SPX",
    "through": "2026-11-13",
    "min_samples": 380,
    "max_gap_days": 4,
    "har": [1, 5, 22],
    "row_size": 2.5,
    "value_area_share": 0.70,
    "ib_median_sessions": 20,
    "ib_end": "10:30",
    "tests": {
        "balance": {"sign": -1, "min_effect": 0.10, "narrow_ratio": 0.75},
        "acceptance": {"sign": 1, "min_effect": 0.0010, "first_period": 2, "last_period": 6},
        "rejection": {"sign": 1, "min_effect": 0.10, "first_period": 0, "last_period": 5},
        "unfinished": {"sign": 1, "min_effect": 0.05, "sessions": 5, "max_distance": 0.03},
    },
    "t_bar": 2.5,
    "min_events": 40,
    "min_n": 200,
    "holdout_min_events": 20,
    "bootstrap": {"block": 10, "resamples": 10000, "seed": 93},
    "splits": ["2025-07-01"],
    "halves": "2023-12-01",
}

#: The research-queue chain, queued with `run.py queue-chain --script <this file>`. Nothing is fetched:
#: the study reads the SPX minutes the store already holds.
CHAIN = ({"step": "study", "kind": "script", "args": ["study"], "output": "{results}/results.json"},)

ET = ZoneInfo("America/New_York")
PASS, FAIL, NO_VERDICT, NOT_READ = "pass", "fail", "no verdict", "not read"
OPEN_MIN = 9 * 60 + 30
CLOSE_MIN = 16 * 60
PERIOD = 30
HISTORY = 22
E1_CONTROLS = ("ln_r1", "ln_r5", "ln_r22", "gap", "w")
E1_NAMES = ("const", *E1_CONTROLS, "signal")


# --------------------------------------------------------------------------- pure pieces


def hhmm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def minute_of(ts: float) -> int:
    t = datetime.fromtimestamp(ts, ET)
    return t.hour * 60 + t.minute


def bars(samples: list[tuple[float, float]]) -> list[dict]:
    """Each stamp a one-minute bar from the previous stamp's price to its own (item 80's)."""
    out, prev = [], None
    for ts, px in samples:
        o = px if prev is None else prev
        t = math.floor(ts / 60) * 60
        out.append({"t": t, "o": o, "h": max(o, px), "l": min(o, px), "c": px})
        prev = px
    return out


def profile_of(samples: list[tuple[float, float]], rule: dict):
    day = datetime.fromtimestamp(samples[0][0], ET).date()
    return mp.build_profile(
        bars(samples),
        row_size=rule["row_size"],
        bar_seconds=60,
        session=day,
        value_area_share=rule["value_area_share"],
    )


def periods_of(minutes: list[tuple[int, float]]) -> dict[int, list[int]]:
    """The stamp indices of each 30-minute period of the regular session, A = 0."""
    out: dict[int, list[int]] = {}
    for i, (m, _) in enumerate(minutes):
        if OPEN_MIN <= m < CLOSE_MIN:
            out.setdefault((m - OPEN_MIN) // PERIOD, []).append(i)
    return out


def period_ends(minutes: list[tuple[int, float]], periods: dict[int, list[int]]) -> dict[int, dict]:
    """At each period's last stamp: its price, and from the next stamp to the close the log of the
    highest price over it (`up`), of it over the lowest (`dn`) and of the close over it (`ret`)."""
    px = [p for _, p in minutes]
    n = len(px)
    suf_max, suf_min = [0.0] * n, [0.0] * n
    hi, lo = -math.inf, math.inf
    for i in range(n - 1, -1, -1):
        suf_max[i], suf_min[i] = hi, lo  # strictly after i
        hi, lo = max(hi, px[i]), min(lo, px[i])
    out = {}
    for k, idx in periods.items():
        i = idx[-1]
        if i + 1 >= n:
            continue
        p = px[i]
        out[k] = {
            "p": p,
            "up": math.log(suf_max[i] / p),
            "dn": math.log(p / suf_min[i]),
            "ret": math.log(px[-1] / p),
        }
    return out


def first_acceptance(minutes, periods, band, first: int, last: int) -> tuple[int, int] | None:
    """The first (period, side) where that period and the one before trade wholly beyond the band:
    side 1 every price at or above its top, -1 every price below its bottom. The earlier period is
    no sooner than `first`, the later no later than `last`."""
    val, top = band
    for k in range(first + 1, last + 1):
        a, b = periods.get(k - 1), periods.get(k)
        if not a or not b or k - 1 < first:
            continue
        pa = [minutes[i][1] for i in a + b]
        if min(pa) >= top:
            return k, 1
        if max(pa) < val:
            return k, -1
    return None


def first_rejection(minutes, periods, band, open_px: float, first: int, last: int) -> tuple[int, int] | None:
    """For an open outside the band, the first (period, side) where that period and the one before
    trade wholly inside it; side is the way to the far edge (1 up to the top, -1 down to the bottom)."""
    val, top = band
    if val <= open_px < top:
        return None
    side = -1 if open_px >= top else 1
    for k in range(first + 1, last + 1):
        a, b = periods.get(k - 1), periods.get(k)
        if not a or not b or k - 1 < first:
            continue
        pa = [minutes[i][1] for i in a + b]
        if min(pa) >= val and max(pa) < top:
            return k, side
    return None


def levels_of(prof, row: float) -> list[tuple[str, float]]:
    """The prior session's unfinished business: a poor high or low, and the middle of each run of
    single-print rows."""
    out = []
    if prof.poor_high:
        out.append(("poor_high", prof.high))
    if prof.poor_low:
        out.append(("poor_low", prof.low))
    run: list[float] = []
    for p in sorted(prof.single_prints) + [math.inf]:
        if run and p - run[-1] > row * 1.5:
            out.append(("single_prints", (run[0] + run[-1] + row) / 2))
            run = []
        if math.isfinite(p):
            run.append(p)
    return out


def build_days(sessions: dict[str, list[tuple[float, float]]], rule: dict) -> tuple[list[dict], dict]:
    """Every full session in order with what the tests read. `linked` says the session before it is
    the prior full session, at most `max_gap_days` earlier; a short session or a gap restarts the HAR
    and first-hour histories, so no series runs across a hole."""
    ib_end = hhmm(rule["ib_end"])
    days: list[dict] = []
    dropped = {"short": 0, "gap": 0, "no_profile": 0}
    history: list[float] = []
    ib_history: list[float] = []
    prev: dict | None = None
    for d in sorted(sessions):
        samples = sessions[d]
        if len(samples) < rule["min_samples"]:
            dropped["short"] += 1
            prev, history, ib_history = None, [], []
            continue
        minutes = [(minute_of(ts), px) for ts, px in samples]
        px = [p for _, p in minutes]
        linked = (
            prev is not None
            and (date.fromisoformat(d) - date.fromisoformat(prev["date"])).days <= rule["max_gap_days"]
        )
        if prev is not None and not linked:
            dropped["gap"] += 1
            history, ib_history = [], []
        prof = profile_of(samples, rule)
        if prof is None:
            dropped["no_profile"] += 1
            prev, history, ib_history = None, [], []
            continue
        ib = [p for m, p in minutes if m < ib_end]
        rest = [p for m, p in minutes if m >= ib_end]
        periods = periods_of(minutes)
        day = {
            "date": d,
            "linked": linked,
            "open": px[0],
            "close": px[-1],
            "high": max(px),
            "low": min(px),
            "minutes": minutes,
            "periods": periods,
            "ends": period_ends(minutes, periods),
            "profile": prof,
            "band": (prof.val, prof.vah + rule["row_size"]),
            "ib": (min(ib), max(ib)) if ib else None,
            "px_ib_end": rest[0] if rest else None,
            "rest_range": (max(rest) - min(rest)) if len(rest) >= 2 else None,
        }
        if linked:
            pc = prev["close"]
            day["prior_close"] = pc
            if len(history) >= HISTORY and len(ib_history) >= rule["ib_median_sessions"] and day["ib"]:
                ib_frac = (day["ib"][1] - day["ib"][0]) / pc
                med = statistics.median(ib_history[-rule["ib_median_sessions"] :])
                if ib_frac > 0 and med > 0 and min(history[-HISTORY:]) > 0:
                    day["har"] = {
                        "ln_r1": math.log(history[-1]),
                        "ln_r5": math.log(sum(history[-5:]) / 5),
                        "ln_r22": math.log(sum(history[-HISTORY:]) / HISTORY),
                        "gap": abs(px[0] - pc) / pc,
                        "w": math.log(ib_frac / med),
                    }
            if day["ib"]:
                ib_history.append((day["ib"][1] - day["ib"][0]) / pc)
        history.append((max(px) - min(px)) / px[-1])
        days.append(day)
        prev = day
    return days, dropped


# --------------------------------------------------------------------------- the four tests' rows


def balance_rows(days: list[dict], rule: dict) -> list[dict]:
    """E1: days with the HAR terms and `w`, price inside the first hour at 10:30. `signal` is 1 when
    the open was inside the prior value area and `w` is at most the declared narrow bound."""
    spec = rule["tests"]["balance"]
    out = []
    for i, d in enumerate(days):
        if "har" not in d or not d["linked"] or d["px_ib_end"] is None or not d["rest_range"]:
            continue
        lo, hi = d["ib"]
        if not lo <= d["px_ib_end"] <= hi:
            continue
        val, top = days[i - 1]["band"]
        inside = val <= d["open"] < top
        out.append(
            {
                "date": d["date"],
                **d["har"],
                "signal": 1.0 if inside and d["har"]["w"] <= math.log(spec["narrow_ratio"]) else 0.0,
                "y": math.log(d["rest_range"] / d["prior_close"]),
                "contained": 1.0 if math.floor(lo / 5) * 5 < d["close"] < math.ceil(hi / 5) * 5 else 0.0,
            }
        )
    return out


def acceptance_events(days: list[dict], rule: dict) -> tuple[list[dict], list[dict]]:
    """E2: (events, every eligible day's period ends with whether it had an event)."""
    spec = rule["tests"]["acceptance"]
    events, pool = [], []
    for i, d in enumerate(days):
        if not d["linked"]:
            continue
        hit = first_acceptance(
            d["minutes"], d["periods"], days[i - 1]["band"], spec["first_period"], spec["last_period"]
        )
        pool.append({"date": d["date"], "ends": d["ends"], "event": hit is not None})
        if hit is not None and hit[0] in d["ends"]:
            k, side = hit
            events.append({"date": d["date"], "k": k, "side": side, "outcome": side * d["ends"][k]["ret"]})
    return events, pool


def rejection_events(days: list[dict], rule: dict) -> tuple[list[dict], list[dict]]:
    """E3: (events, the pool) as `acceptance_events`; an event's outcome is 1 when price reaches the
    far edge after the entry, and `d` is that edge's log distance from the entry."""
    spec = rule["tests"]["rejection"]
    events, pool = [], []
    for i, d in enumerate(days):
        if not d["linked"]:
            continue
        band = days[i - 1]["band"]
        hit = first_rejection(
            d["minutes"], d["periods"], band, d["open"], spec["first_period"], spec["last_period"]
        )
        pool.append({"date": d["date"], "ends": d["ends"], "event": hit is not None})
        if hit is not None and hit[0] in d["ends"]:
            k, side = hit
            end = d["ends"][k]
            target = band[1] if side == 1 else band[0]
            dist = math.log(target / end["p"]) * side
            if dist <= 0:
                continue
            reached = end["up"] >= dist if side == 1 else end["dn"] >= dist
            events.append(
                {"date": d["date"], "k": k, "side": side, "d": dist, "outcome": 1.0 if reached else 0.0}
            )
    return events, pool


def unfinished_windows(days: list[dict], rule: dict) -> list[dict]:
    """E4: for each session with a linked prior and `sessions` linked sessions from it, the prior
    session's levels and the window's log high and low against the prior close."""
    spec = rule["tests"]["unfinished"]
    n = spec["sessions"]
    out = []
    for i, d in enumerate(days):
        span = days[i : i + n]
        if len(span) < n or not all(s["linked"] for s in span):
            continue
        prior = days[i - 1]
        pc = prior["close"]
        levels = []
        for kind, lv in levels_of(prior["profile"], rule["row_size"]):
            x = math.log(lv / pc)
            if 0 < abs(x) <= spec["max_distance"]:
                levels.append({"kind": kind, "x": x})
        out.append(
            {
                "date": d["date"],
                "hi": math.log(max(s["high"] for s in span) / pc),
                "lo": math.log(min(s["low"] for s in span) / pc),
                "levels": levels,
            }
        )
    return out


# --------------------------------------------------------------------------- matched differences


def part_of(day: str, rule: dict) -> str:
    return "design" if day < rule["splits"][0] else "holdout"


def acceptance_diffs(events: list[dict], pool: list[dict]) -> list[dict]:
    """Each event's outcome less the mean of the same side's return from the same period end over the
    non-event days of its part."""
    out = []
    for e in events:
        base = [e["side"] * p["ends"][e["k"]]["ret"] for p in pool if not p["event"] and e["k"] in p["ends"]]
        if base:
            out.append({"date": e["date"], "diff": e["outcome"] - sum(base) / len(base)})
    return out


def rejection_diffs(events: list[dict], pool: list[dict]) -> list[dict]:
    """Each event's touch less the share of the part's non-event days that moved as far the same way
    from the same period end."""
    out = []
    for e in events:
        key = "up" if e["side"] == 1 else "dn"
        base = [
            1.0 if p["ends"][e["k"]][key] >= e["d"] else 0.0
            for p in pool
            if not p["event"] and e["k"] in p["ends"]
        ]
        if base:
            out.append({"date": e["date"], "diff": e["outcome"] - sum(base) / len(base)})
    return out


def unfinished_diffs(windows: list[dict], kinds: tuple[str, ...] | None = None) -> list[dict]:
    """Per session with levels: the mean over its levels of touched-within-the-window less the share of
    the part's other windows that reached the same signed distance."""
    out = []
    for w in windows:
        lv = [v for v in w["levels"] if kinds is None or v["kind"] in kinds]
        if not lv:
            continue
        others = [o for o in windows if o is not w]
        if not others:
            continue
        diffs = []
        for v in lv:
            x = v["x"]
            touched = w["hi"] >= x if x > 0 else w["lo"] <= x
            base = sum(1 for o in others if (o["hi"] >= x if x > 0 else o["lo"] <= x)) / len(others)
            diffs.append((1.0 if touched else 0.0) - base)
        out.append({"date": w["date"], "diff": sum(diffs) / len(diffs)})
    return out


def by_part(items: list[dict], rule: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {"design": [], "holdout": []}
    for it in items:
        out[part_of(it["date"], rule)].append(it)
    return out


def matched(kind: str, days: list[dict], rule: dict) -> tuple[dict[str, list[dict]], dict]:
    """A matched test's differences per part, each baseline from its own part, and counts reported."""
    if kind == "unfinished":
        wins = by_part(unfinished_windows(days, rule), rule)
        return {p: unfinished_diffs(ws) for p, ws in wins.items()}, {
            p: {"windows": len(ws), "levels": sum(len(w["levels"]) for w in ws)} for p, ws in wins.items()
        }
    events, pool = (acceptance_events if kind == "acceptance" else rejection_events)(days, rule)
    diff = acceptance_diffs if kind == "acceptance" else rejection_diffs
    ev, pl = by_part(events, rule), by_part(pool, rule)
    return {p: diff(ev[p], pl[p]) for p in ev}, {
        p: {"events": len(ev[p]), "up": sum(1 for e in ev[p] if e["side"] == 1), "pool": len(pl[p])}
        for p in ev
    }


# --------------------------------------------------------------------------- statistics


def stationary_indices(n: int, block: int, rng: random.Random) -> list[int]:
    """One stationary-bootstrap resample (Politis and Romano): runs of mean length `block`, wrapping."""
    out, i = [], rng.randrange(n)
    p = 1.0 / block
    while len(out) < n:
        out.append(i)
        i = rng.randrange(n) if rng.random() < p else (i + 1) % n
    return out


def boot_se_mean(xs: list[float], *, block: int, resamples: int, seed: int) -> float | None:
    if len(xs) < 2:
        return None
    rng = random.Random(seed)
    draws = []
    for _ in range(resamples):
        idx = stationary_indices(len(xs), block, rng)
        draws.append(sum(xs[i] for i in idx) / len(idx))
    m = sum(draws) / len(draws)
    return math.sqrt(sum((d - m) ** 2 for d in draws) / (len(draws) - 1))


def solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting; raises ValueError on a singular system."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[p][c]) < 1e-12:
            raise ValueError("singular design matrix")
        m[c], m[p] = m[p], m[c]
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    return [m[i][n] / m[i][i] for i in range(n)]


def _x(rows: list[dict]) -> list[list[float]]:
    return [[1.0, *(r[c] for c in E1_CONTROLS), r["signal"]] for r in rows]


def _signal_coef(x: list[list[float]], y: list[float], idx) -> float:
    k = len(x[0])
    xtx = [[0.0] * k for _ in range(k)]
    xty = [0.0] * k
    for i in idx:
        xi, yi = x[i], y[i]
        for a in range(k):
            xty[a] += xi[a] * yi
            for b in range(a, k):
                xtx[a][b] += xi[a] * xi[b]
    for a in range(k):
        for b in range(a):
            xtx[a][b] = xtx[b][a]
    return solve(xtx, xty)[-1]


def signal_coef(rows: list[dict]) -> float | None:
    if len(rows) <= len(E1_NAMES) or not any(r["signal"] for r in rows):
        return None
    try:
        return _signal_coef(_x(rows), [r["y"] for r in rows], range(len(rows)))
    except ValueError:
        return None


def boot_se_coef(rows: list[dict], *, block: int, resamples: int, seed: int) -> float | None:
    x, y = _x(rows), [r["y"] for r in rows]
    rng = random.Random(seed)
    draws = []
    for _ in range(resamples):
        try:
            draws.append(_signal_coef(x, y, stationary_indices(len(rows), block, rng)))
        except ValueError:
            continue
    if len(draws) < 2:
        return None
    m = sum(draws) / len(draws)
    return math.sqrt(sum((d - m) ** 2 for d in draws) / (len(draws) - 1))


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def judge_balance(rows: list[dict], rule: dict, *, resamples: int | None = None) -> dict:
    spec = rule["tests"]["balance"]
    sign = spec["sign"]
    parts = by_part(rows, rule)
    design, holdout = parts["design"], parts["holdout"]
    ev = {p: int(sum(r["signal"] for r in rs)) for p, rs in parts.items()}
    out: dict = {
        "design": {"n": len(design), "events": ev["design"]},
        "holdout": {"n": len(holdout), "events": ev["holdout"]},
    }
    if len(design) < rule["min_n"] or ev["design"] < rule["min_events"]:
        out["design"]["verdict"], out["holdout"]["verdict"] = NO_VERDICT, NOT_READ
        return {**out, "verdict": NO_VERDICT}
    coef = signal_coef(design)
    bs = rule["bootstrap"]
    se = boot_se_coef(design, block=bs["block"], resamples=resamples or bs["resamples"], seed=bs["seed"])
    t = coef / se if coef is not None and se else None
    halves = {
        "first": signal_coef([r for r in design if r["date"] < rule["halves"]]),
        "second": signal_coef([r for r in design if r["date"] >= rule["halves"]]),
    }
    checks = {
        "effect": coef is not None and sign * coef >= spec["min_effect"],
        "t": t is not None and sign * t >= rule["t_bar"],
        "halves": all(v is not None and sign * v > 0 for v in halves.values()),
    }
    out["design"].update({"coef": coef, "se": se, "t": t, "halves": halves, "checks": checks})
    return _holdout(out, checks, signal_coef(holdout), sign, ev["holdout"], rule)


def judge_matched(
    diffs: dict[str, list[dict]], rule: dict, name: str, *, resamples: int | None = None
) -> dict:
    spec = rule["tests"][name]
    sign = spec["sign"]
    design, holdout = diffs["design"], diffs["holdout"]
    out: dict = {"design": {"events": len(design)}, "holdout": {"events": len(holdout)}}
    if len(design) < rule["min_events"]:
        out["design"]["verdict"], out["holdout"]["verdict"] = NO_VERDICT, NOT_READ
        return {**out, "verdict": NO_VERDICT}
    xs = [d["diff"] for d in design]
    mean = _mean(xs)
    bs = rule["bootstrap"]
    se = boot_se_mean(xs, block=bs["block"], resamples=resamples or bs["resamples"], seed=bs["seed"])
    t = mean / se if se else None
    halves = {
        "first": _mean([d["diff"] for d in design if d["date"] < rule["halves"]]),
        "second": _mean([d["diff"] for d in design if d["date"] >= rule["halves"]]),
    }
    checks = {
        "effect": sign * mean >= spec["min_effect"],
        "t": t is not None and sign * t >= rule["t_bar"],
        "halves": all(v is not None and sign * v > 0 for v in halves.values()),
    }
    out["design"].update({"mean": mean, "se": se, "t": t, "halves": halves, "checks": checks})
    return _holdout(out, checks, _mean([d["diff"] for d in holdout]), sign, len(holdout), rule)


def _holdout(out: dict, checks: dict, h: float | None, sign: int, events: int, rule: dict) -> dict:
    """The holdout, read only after a design pass: the declared sign, provisional on few events."""
    if not all(checks.values()):
        out["design"]["verdict"], out["holdout"]["verdict"] = FAIL, NOT_READ
        return {**out, "verdict": FAIL}
    out["design"]["verdict"] = PASS
    word = PASS if h is not None and sign * h > 0 else FAIL
    provisional = word == PASS and events < rule["holdout_min_events"]
    out["holdout"].update({"effect": h, "verdict": word, "provisional": provisional})
    return {**out, "verdict": word, "provisional": provisional}


def judge(days: list[dict], rule: dict, *, resamples: int | None = None, reporter=None) -> dict:
    tests, counts = {}, {}
    for name in rule["tests"]:
        if name == "balance":
            tests[name] = judge_balance(balance_rows(days, rule), rule, resamples=resamples)
        else:
            diffs, counts[name] = matched(name, days, rule)
            tests[name] = judge_matched(diffs, rule, name, resamples=resamples)
        if reporter is not None:
            reporter.advance("tests", key=rule["symbol"], current=name)
    return {"tests": tests, "counts": counts, "verdict": {n: t["verdict"] for n, t in tests.items()}}


def fingerprint(sessions: dict[str, list]) -> dict:
    """What the run read, as counts and a hash of them: never a price."""
    lines = "\n".join(f"{d}:{len(s)}" for d, s in sorted(sessions.items()))
    return {
        "sessions": len(sessions),
        "stamps": sum(len(s) for s in sessions.values()),
        "first": min(sessions) if sessions else None,
        "last": max(sessions) if sessions else None,
        "sha256": hashlib.sha256(lines.encode()).hexdigest(),
    }


# --------------------------------------------------------------------------- reported, never judged


def reported(days: list[dict], rule: dict) -> dict:
    """The declared side readings, on the design part only."""
    split = rule["splits"][0]
    rows = [r for r in balance_rows(days, rule) if r["date"] < split]
    sig = [r for r in rows if r["signal"]]
    rest = [r for r in rows if not r["signal"]]
    out: dict = {
        "balance_contained": {
            "signal": _mean([r["contained"] for r in sig]),
            "other": _mean([r["contained"] for r in rest]),
        }
    }
    ev, _ = acceptance_events(days, rule)
    ev = [e for e in ev if e["date"] < split]
    out["acceptance_on_side"] = _mean([1.0 if e["outcome"] > 0 else 0.0 for e in ev])
    rj, _ = rejection_events(days, rule)
    out["rejection_touch_rate"] = _mean([e["outcome"] for e in rj if e["date"] < split])
    wins = by_part(unfinished_windows(days, rule), rule)["design"]
    out["unfinished_by_kind"] = {
        k: _mean([d["diff"] for d in unfinished_diffs(wins, (k,))])
        for k in ("poor_high", "poor_low", "single_prints")
    }
    fomc = fomc_days()
    if fomc is None:
        out["without_fomc"] = "no calendar"
    else:
        # Dropped by date afterwards: removing a day from `days` would make the next one read the
        # wrong session as its prior.
        out["without_fomc"] = {
            "balance": signal_coef([r for r in rows if r["date"] not in fomc]),
            **{
                n: _mean([d["diff"] for d in matched(n, days, rule)[0]["design"] if d["date"] not in fomc])
                for n in ("acceptance", "rejection")
            },
        }
    return out


# --------------------------------------------------------------------------- the store (read-only)


def store_path() -> Path:
    from cherrypick.backtest import paths, settings

    return paths.vendor_dir(settings.load()) / "SPX-60s.db"


def load_sessions(
    conn: sqlite3.Connection, symbol: str, through: str
) -> dict[str, list[tuple[float, float]]]:
    out: dict[str, list[tuple[float, float]]] = {}
    for d, ts, spot in conn.execute(
        "SELECT trade_date, ts, spot FROM gex_spot_history WHERE symbol = ? AND trade_date <= ? AND spot > 0 "
        "ORDER BY trade_date, ts",
        (symbol, through),
    ):
        out.setdefault(d, []).append((float(ts), float(spot)))
    return out


def fomc_days() -> set[str] | None:
    from cherrypick.core import home

    f = home.data_dir() / "public" / "item33" / "calendar.json"
    try:
        return set(json.loads(f.read_text(encoding="utf-8"))["fomc"]["scheduled"])
    except (OSError, ValueError, KeyError):
        return None


def out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item93"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cmd_study(args) -> int:
    rule = FORECAST_RULE
    conn = sqlite3.connect(f"file:{store_path()}?mode=ro", uri=True)
    try:
        sessions = load_sessions(conn, rule["symbol"], rule["through"])
    finally:
        conn.close()
    with core_progress.Reporter("profile_entry_readings_item93-study") as rep:
        rep.split("tests", [rule["symbol"]] * len(rule["tests"]), unit="tests")
        days, dropped = build_days(sessions, rule)
        block = judge(days, rule, reporter=rep)
    doc = {
        "licence": "derived from licensed vendor data: never commit or publish",
        "item": 93,
        "rule": rule,
        "window": {
            "from": days[0]["date"] if days else None,
            "through": rule["through"],
            "splits": rule["splits"],
        },
        "days": len(days),
        "dropped": dropped,
        **block,
        "reported": reported(days, rule),
        "inputs": fingerprint(sessions),
    }
    path = Path(args.out) if args.out else out_dir() / "results.json"
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"written": str(path), "verdict": doc["verdict"]}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="step", required=True)
    p = sub.add_parser("study")
    p.add_argument("--out", default=None)
    p.set_defaults(fn=cmd_study)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
