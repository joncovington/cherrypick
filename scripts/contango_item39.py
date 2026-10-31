"""Item 39, the contango module's own rule on real daily prices (docs/edge-hypotheses-plan.md): holding
SVXY while VIX/VIX3M is below 0.97 and SHV otherwise, and two declared changes to the exit, against
holding SVXY.

    python scripts/contango_item39.py refresh            # VIX9D and SPX from Cboe's public files (network)
    python scripts/contango_item39.py plan [--json PATH] # coverage counts and skipped dates; no returns
    python scripts/contango_item39.py study [--json PATH]
    python scripts/contango_item39.py report [--json PATH]  # results.md from results.json (local)

`refresh` replaces `VIX9D.csv` and `SPX.csv` in the market-files folder the routine fetcher keeps
(`scripts/fetch_market_files.py` keeps VIX and VIX3M but not these two), refusing a download that
parses to less than the file on disk, as the fetcher does. `plan` counts sessions and lists the dates
the study will skip; it exits 1 while VIX9D or SPX end before the last SVXY session. `study` writes
`~/.cherrypick/data/research/item39/results.json` and `results.md` beside it. Nothing here is
licensed; the folder is still outside the repo, as the declaration says.

**The arms** (one variable each). A decision is a state, SVXY or SHV, taken at the close with the
module's own rule (`cherrypick.contango.regime.target_state`) and earned by the module's own replay
(`cherrypick.contango.replay.run`). The two gates are expressed as a signal for that same replay: the
close's VIX/VIX3M when the gate clears, a value far above any threshold (`OFF`) when it does not, so
the replay and the arms cannot disagree about the rule or the costs.

- `control`: SVXY below 0.97. `flipexit`: in below 0.97, out only at 1.0, holding through the band.
- `vix9d-exit`: `control`'s test and VIX9D/VIX below 1.0 at the same close.
- `rv-gate`: `control`'s test and VIX above SPX's realised volatility, the sample standard deviation of
  10 daily log returns ending on the decision day, times the square root of 252, times 100.

**Baselines.** `hold-svxy` (bought on the first session, one side of cost) is the judged comparison.
`hold-shv` is reported; excess return is measured against SHV's own total return.

**Sessions and gaps.** The candidates are SVXY's stored sessions. A date in none of the index files is
a non-session (a US holiday the fund store carries) and is counted, not scored. A session missing any
input for an arm is skipped for that arm and counted, never filled with a zero return; more than 1% of
a window's sessions missing is no verdict for the arm. A return across a skipped session is the
held fund's close to its previous usable close, as the module's replay computes it.

**The pass rule** (design window, per arm, 10 bps a side, against `hold-svxy`): the Sharpe ratio above
it with a one-sided 95% lower bound of the difference above zero (stationary bootstrap of paired daily
returns, mean block 20, 20,000 resamples, seed 39) and Holm at 0.05 across the four arms; a smaller
maximum drawdown; CAGR no more than 3 points a year lower; in each half a positive Sharpe difference
and a drawdown no deeper; a positive Sharpe difference with 2020-02-19 to 2020-04-30 removed (the
days' returns dropped, the rest joined); a positive Sharpe difference with every signal lagged one
session (the fill still at the same close). Then the holdout, read once and only for the arms that pass
the design: Sharpe above `hold-svxy`'s and the return floor met; a holdout in which the arm sits out of
SVXY under 5% of sessions is uninformative about the gate and gives no verdict. A design fail stays a
fail. The 0.97 and 1.0 thresholds were chosen having seen the exploratory replay
(`scripts/contango_replay.py`); the report says so.

**Guards**, each shown to fail in the tests before an arm is read: reproduction of the module's own
replay, accounting (NAV equals the held fund's returns less costs, the cost count equals switches
times two sides plus the opening side), no look-ahead, SVXY across its 2024-04-11 split, coverage.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from datetime import date, timedelta
from pathlib import Path

from cherrypick.contango import regime, replay
from cherrypick.core import stats as core_stats

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

ARMS = ("control", "flipexit", "vix9d-exit", "rv-gate")
BASELINES = ("hold-svxy", "hold-shv")
CONTROL_PARAMS = {"enter_below": 0.97, "exit_at_or_above": 0.97}
PARAMS = {
    "control": CONTROL_PARAMS,
    "flipexit": {"enter_below": 0.97, "exit_at_or_above": 1.0},
    "vix9d-exit": CONTROL_PARAMS,
    "rv-gate": CONTROL_PARAMS,
}
#: What a gate that has not cleared writes into the signal: above every threshold, so the replay
#: holds cash. A hold-svxy baseline writes `ON` (below every threshold).
OFF = 99.0
ON = 0.0

WINDOWS = {"design": ("2018-03-01", "2022-12-30"), "holdout": ("2023-01-03", None)}
SPLIT = "2020-07-01"
CRASH = ("2020-02-19", "2020-04-30")
SPLIT_DAY = "2024-04-11"

COST_JUDGED = 10.0
COST_REPORTED = 2.0
RESAMPLES = 20_000
BLOCK = 20
SEED = 39
ALPHA = 0.05
RETURN_FLOOR = 0.03
MISSING_MAX = 0.01
WHIPSAW_SESSIONS = 5
UNINFORMATIVE = 0.05
RV_SESSIONS = 10
VIX9D_MAX = 1.0
TRADING_DAYS = 252
#: The detectable size the declaration assumes (B12); the report sets the run's own beside it.
MDE_DECLARED = 0.5

INDEX_FILES = ("VIX", "VIX3M", "VIX9D", "SPX")
#: The inputs each arm reads, by name in the data.
NEEDS = {
    "control": ("vix", "vix3m"),
    "flipexit": ("vix", "vix3m"),
    "vix9d-exit": ("vix", "vix3m", "vix9d"),
    "rv-gate": ("vix", "vix3m", "spx"),
}


# ------------------------------------------------------------------------------------------------
# Signals. Pure; each value on day t reads closes through day t only.


def ratio_series(vix: dict[str, float], vix3m: dict[str, float]) -> dict[str, float]:
    """VIX/VIX3M at each close, unrounded, as `scripts/contango_replay.py` reads it."""
    return {d: vix[d] / vix3m[d] for d in vix if d in vix3m and vix3m[d] > 0}


def realised_vol(closes: list[float]) -> float | None:
    """Sample standard deviation of the log returns of `closes` (11 closes: 10 returns), annualised
    by the square root of 252, in volatility points (times 100). None on a non-positive close."""
    if len(closes) < 3 or any(c <= 0 for c in closes):
        return None
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:], strict=False)]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(TRADING_DAYS) * 100.0


def realised_series(
    spx: dict[str, float], calendar: list[str], sessions: int = RV_SESSIONS
) -> dict[str, float]:
    """Realised volatility at each calendar day whose `sessions` returns all exist: the day and the
    `sessions` calendar days before it each have an SPX close. A missing close leaves the day out."""
    out: dict[str, float] = {}
    for i in range(sessions, len(calendar)):
        window = calendar[i - sessions : i + 1]
        if all(d in spx for d in window):
            v = realised_vol([spx[d] for d in window])
            if v is not None:
                out[calendar[i]] = v
    return out


def signals(arm: str, inputs: dict[str, dict[str, float]], calendar: list[str]) -> dict[str, float]:
    """The arm's signal per day that has every input the arm reads: the ratio when the arm's gate (if
    any) clears, `OFF` when it does not. Stateless gates; `flipexit`'s memory is in the replay's
    thresholds. `calendar` is the trading calendar the realised volatility counts sessions on."""
    ratio = ratio_series(inputs["vix"], inputs["vix3m"])
    out: dict[str, float] = {}
    if arm in ("control", "flipexit"):
        return dict(ratio)
    if arm == "vix9d-exit":
        for d, r in ratio.items():
            if d in inputs["vix9d"] and inputs["vix"][d] > 0:
                out[d] = r if inputs["vix9d"][d] / inputs["vix"][d] < VIX9D_MAX else OFF
        return out
    if arm == "rv-gate":
        rv = realised_series(inputs["spx"], calendar)
        for d, r in ratio.items():
            if d in rv:
                out[d] = r if inputs["vix"][d] > rv[d] else OFF
        return out
    raise ValueError(f"unknown arm {arm!r}")


def lagged(signal: dict[str, float], calendar: list[str]) -> dict[str, float]:
    """Each day's signal replaced by the previous calendar day's: the decision uses inputs one
    session old, the fill stays at today's close."""
    return {
        calendar[i]: signal[calendar[i - 1]] for i in range(1, len(calendar)) if calendar[i - 1] in signal
    }


def shifted_inputs(inputs: dict[str, dict[str, float]], calendar: list[str]) -> dict[str, dict[str, float]]:
    """Every input series moved one calendar day later (the guard's deliberate look-behind)."""
    out = {}
    for name, series in inputs.items():
        out[name] = {
            calendar[i]: series[calendar[i - 1]] for i in range(1, len(calendar)) if calendar[i - 1] in series
        }
    return out


# ------------------------------------------------------------------------------------------------
# Sessions, coverage and the runs.


def window_days(candidates: list[str], non_sessions: set[str], lo: str, hi: str | None) -> list[str]:
    return [d for d in candidates if d >= lo and (hi is None or d <= hi) and d not in non_sessions]


def non_session_days(candidates: list[str], inputs: dict[str, dict[str, float]]) -> set[str]:
    """Candidate dates in none of the index files: not sessions of the market the signals read."""
    seen = set().union(*(set(inputs[k]) for k in ("vix", "vix3m", "vix9d", "spx")))
    return {d for d in candidates if d not in seen}


def arm_days(
    signal: dict[str, float], risk: dict[str, float], cash: dict[str, float], days: list[str]
) -> tuple[list[str], list[str]]:
    """(usable, missing): the days with the signal and both funds' closes, and those without. A day
    missing an input is out of the run, so no return is ever earned or invented across it by a zero."""
    usable = [d for d in days if d in signal and d in risk and d in cash]
    have = set(usable)
    return usable, [d for d in days if d not in have]


def missing_inputs(
    arm: str, inputs: dict[str, dict[str, float]], risk, cash, missing: list[str]
) -> dict[str, int]:
    """Which input each skipped day lacked in the raw files. A day with every raw input that is still
    out (an rv-gate day without ten prior SPX closes) is counted as `spx history`."""
    named = {**{k: inputs[k] for k in NEEDS[arm]}, "svxy": risk, "shv": cash}
    out: dict[str, int] = {}
    for d in missing:
        lacking = [name for name, series in named.items() if d not in series]
        for name in lacking or ["spx history"]:
            out[name] = out.get(name, 0) + 1
    return out


def run_arm(
    usable: list[str],
    signal: dict[str, float],
    risk: dict[str, float],
    cash: dict[str, float],
    params: dict,
    cost_bps: float,
) -> dict:
    """One arm over `usable` through the module's replay, plus what the report reads: the state taken
    each day, the state held through each day, daily returns (the first is the opening side of cost),
    the cash fund's daily returns and the excess. Switches exclude the opening trade."""
    out = replay.run(usable, signal, risk, cash, params, cost_bps=cost_bps)
    navs = out["navs"]
    states: list[str] = []
    state = None
    for d in usable:
        state = regime.target_state(signal[d], state, params)
        states.append(state)
    rets = [navs[0][1] - 1.0] + [navs[i][1] / navs[i - 1][1] - 1.0 for i in range(1, len(navs))]
    cash_rets = [0.0] + [cash[usable[i]] / cash[usable[i - 1]] - 1.0 for i in range(1, len(usable))]
    changes = [i for i in range(1, len(states)) if states[i] != states[i - 1]]
    held = [None] + states[:-1]
    return {
        "days": list(usable),
        "states": states,
        "held": held,
        "navs": navs,
        "rets": rets,
        "cash_rets": cash_rets,
        "excess": [r - c for r, c in zip(rets, cash_rets, strict=True)],
        "switches": len(changes),
        "switch_at": changes,
        "sides": 2 * len(changes) + (1 if states else 0),
        "replay_switches": out["switches"],
        "replay_in_risk": out["days_in_risk"],
        "cost_bps": cost_bps,
    }


def hold_run(kind: str, usable: list[str], risk, cash, cost_bps: float) -> dict:
    sig = dict.fromkeys(usable, ON if kind == "hold-svxy" else OFF)
    return run_arm(usable, sig, risk, cash, CONTROL_PARAMS, cost_bps)


def whipsaws(switch_at: list[int], within: int = WHIPSAW_SESSIONS) -> int:
    """Switches followed by the opposite switch within `within` sessions. With two states every next
    switch is the opposite one."""
    return sum(1 for a, b in zip(switch_at, switch_at[1:], strict=False) if b - a <= within)


def slice_run(run: dict, lo: str | None, hi: str | None) -> dict:
    """The part of a run on `lo <= day <= hi`, series cut together."""
    keep = [i for i, d in enumerate(run["days"]) if (lo is None or d >= lo) and (hi is None or d <= hi)]
    states = [run["states"][i] for i in keep]
    return {
        "days": [run["days"][i] for i in keep],
        "rets": [run["rets"][i] for i in keep],
        "excess": [run["excess"][i] for i in keep],
        "held": [run["held"][i] for i in keep],
        "states": states,
    }


def drop_days(series: dict, lo: str, hi: str) -> dict:
    """The series with the sessions from `lo` to `hi` removed and the rest joined."""
    keep = [i for i, d in enumerate(series["days"]) if not (lo <= d <= hi)]
    return {k: [series[k][i] for i in keep] for k in ("days", "rets", "excess")}


# ------------------------------------------------------------------------------------------------
# Measures.


def sharpe(excess: list[float]) -> float | None:
    """Mean excess daily return over its sample standard deviation, times the square root of 252."""
    n = len(excess)
    if n < 2:
        return None
    mean = sum(excess) / n
    var = sum((x - mean) ** 2 for x in excess) / (n - 1)
    sd = math.sqrt(var)
    if sd < 1e-12:
        return None
    return mean / sd * math.sqrt(TRADING_DAYS)


def _compound(rets: list[float]) -> tuple[list[float], float]:
    nav, path = 1.0, [1.0]
    for r in rets:
        nav *= 1.0 + r
        path.append(nav)
    return path, nav


def max_drawdown(rets: list[float]) -> float:
    path, _ = _compound(rets)
    peak, worst = path[0], 0.0
    for v in path:
        peak = max(peak, v)
        worst = min(worst, v / peak - 1.0)
    return worst


def cagr(rets: list[float], days: list[str]) -> float | None:
    _, nav = _compound(rets)
    if len(days) < 2 or nav <= 0:
        return None
    years = (date.fromisoformat(days[-1]) - date.fromisoformat(days[0])).days / 365.25
    return nav ** (1 / years) - 1 if years > 0 else None


def worst_week(rets: list[float], span: int = 5) -> float | None:
    if len(rets) < span:
        return None
    worst = 0.0
    for i in range(len(rets) - span + 1):
        p = 1.0
        for r in rets[i : i + span]:
            p *= 1.0 + r
        worst = min(worst, p - 1.0)
    return worst


def years_between(days: list[str]) -> float:
    return (
        (date.fromisoformat(days[-1]) - date.fromisoformat(days[0])).days / 365.25 if len(days) > 1 else 0.0
    )


def metrics(
    part: dict, *, switches: int | None = None, sides: int | None = None, whips: int | None = None
) -> dict:
    """The declared measures for one return series (`part`: days, rets, excess, held)."""
    rets, days = part["rets"], part["days"]
    g = cagr(rets, days)
    dd = max_drawdown(rets)
    held = [h for h in part["held"] if h is not None]
    years = years_between(days)
    return {
        "n": len(rets),
        "from": days[0] if days else None,
        "to": days[-1] if days else None,
        "sharpe": sharpe(part["excess"]),
        "cagr": g,
        "max_drawdown": dd,
        "calmar": (g / abs(dd)) if g is not None and dd < 0 else None,
        "worst_day": min(rets) if rets else None,
        "worst_week": worst_week(rets),
        "in_svxy": (sum(h == regime.RISK for h in held) / len(held)) if held else None,
        "switches": switches,
        "whipsaws": whips,
        "sides": sides,
        "cost_pts_year": (sides * part.get("cost_bps", 0.0) / 100.0 / years)
        if sides is not None and years
        else None,
        "total_return": _compound(rets)[1] - 1.0 if rets else None,
    }


def run_metrics(run: dict, part: dict | None = None) -> dict:
    part = part or run
    m = metrics(
        {**part, "cost_bps": run["cost_bps"]},
        switches=run["switches"],
        sides=run["sides"],
        whips=whipsaws(run["switch_at"]),
    )
    return m


def sharpe_diff(arm_excess: list[float], base_excess: list[float]) -> float | None:
    a, b = sharpe(arm_excess), sharpe(base_excess)
    return None if a is None or b is None else a - b


def bootstrap_sharpe_diff(
    arm_excess: list[float],
    base_excess: list[float],
    *,
    resamples: int = RESAMPLES,
    block: float = BLOCK,
    seed: int = SEED,
) -> dict:
    """Stationary bootstrap of paired daily excess returns: the Sharpe difference recomputed on each
    resample. `lower` is the one-sided 95% lower bound (the 5th percentile), `p` the share of
    resamples at or below zero, `se` the spread of the resampled differences."""
    n = len(arm_excess)
    if n != len(base_excess) or n < 3:
        return {"diff": None, "lower": None, "p": 1.0, "se": None, "resamples": 0}
    rng = random.Random(seed)
    a2 = [x * x for x in arm_excess]
    b2 = [x * x for x in base_excess]
    root = math.sqrt(TRADING_DAYS)
    diffs: list[float] = []
    for _ in range(resamples):
        idx = core_stats.stationary_indices(n, block, rng)
        sa = sum(arm_excess[i] for i in idx)
        sb = sum(base_excess[i] for i in idx)
        qa = sum(a2[i] for i in idx)
        qb = sum(b2[i] for i in idx)
        va = (qa - sa * sa / n) / (n - 1)
        vb = (qb - sb * sb / n) / (n - 1)
        if va <= 1e-24 or vb <= 1e-24:
            continue
        diffs.append((sa / n) / math.sqrt(va) * root - (sb / n) / math.sqrt(vb) * root)
    if not diffs:
        return {
            "diff": sharpe_diff(arm_excess, base_excess),
            "lower": None,
            "p": 1.0,
            "se": None,
            "resamples": 0,
        }
    diffs.sort()
    mean = sum(diffs) / len(diffs)
    se = math.sqrt(sum((x - mean) ** 2 for x in diffs) / max(len(diffs) - 1, 1))
    return {
        "diff": sharpe_diff(arm_excess, base_excess),
        "lower": diffs[int(ALPHA * (len(diffs) - 1))],
        "p": sum(1 for x in diffs if x <= 0) / len(diffs),
        "se": se,
        "resamples": len(diffs),
    }


def by_year(part: dict) -> dict[str, float]:
    """Compounded return per calendar year of the series' own days."""
    out: dict[str, float] = {}
    for d, r in zip(part["days"], part["rets"], strict=True):
        out[d[:4]] = (1.0 + out.get(d[:4], 0.0)) * (1.0 + r) - 1.0
    return out


# ------------------------------------------------------------------------------------------------
# The judging, as functions of figures.


def design_checks(fig: dict, holm_ok: bool) -> dict[str, bool]:
    """The six design clauses from one arm's figures (see `evaluate_arm`)."""

    def above(x):
        return x is not None and x > 0

    def no_deeper(arm_dd, base_dd):
        return arm_dd >= base_dd  # drawdowns are negative: -0.2 is not deeper than -0.3

    return {
        "sharpe": above(fig["sharpe_diff"]) and above(fig["lower"]) and holm_ok,
        "drawdown": fig["arm_dd"] > fig["base_dd"],
        "floor": fig["arm_cagr"] is not None
        and fig["base_cagr"] is not None
        and fig["arm_cagr"] >= fig["base_cagr"] - RETURN_FLOOR,
        "halves": all(
            above(h["sharpe_diff"]) and no_deeper(h["arm_dd"], h["base_dd"]) for h in fig["halves"]
        ),
        "crash": above(fig["crash_diff"]),
        "lag": above(fig["lag_diff"]),
    }


def holdout_checks(fig: dict) -> dict[str, bool]:
    return {
        "sharpe": fig["sharpe_diff"] is not None and fig["sharpe_diff"] > 0,
        "floor": fig["arm_cagr"] is not None
        and fig["base_cagr"] is not None
        and fig["arm_cagr"] >= fig["base_cagr"] - RETURN_FLOOR,
    }


def missing_share(missing: int, sessions: int) -> float:
    return missing / sessions if sessions else 1.0


def judge_arm(design: dict | None, holdout: dict | None, why: str | None = None) -> dict:
    """The arm's verdict. `design` is `{checks, coverage_ok}`; `holdout` is None while unread, else
    `{checks, coverage_ok, informative}`. `why` overrides (a failed guard)."""
    if why:
        return {"verdict": "no verdict", "why": why}
    if not design["coverage_ok"]:
        return {"verdict": "no verdict", "why": "more than 1% of the design window's sessions miss an input"}
    failed = [k for k, v in design["checks"].items() if not v]
    if failed:
        return {"verdict": "fail", "why": "design: " + ", ".join(failed)}
    if holdout is None:
        return {"verdict": "no verdict", "why": "passed the design; the holdout is not read yet"}
    if not holdout["coverage_ok"]:
        return {"verdict": "no verdict", "why": "more than 1% of the holdout's sessions miss an input"}
    if not holdout["informative"]:
        return {
            "verdict": "no verdict",
            "why": "the holdout is uninformative: the arm sat out of SVXY under 5% of it",
        }
    failed = [k for k, v in holdout["checks"].items() if not v]
    if failed:
        return {"verdict": "fail", "why": "holdout: " + ", ".join(failed)}
    return {"verdict": "pass", "why": "design and holdout hold"}


def base_candidate(verdicts: dict[str, dict], design_diffs: dict[str, float | None]) -> str | None:
    passing = [a for a, v in verdicts.items() if v["verdict"] == "pass"]
    if not passing:
        return None
    return max(passing, key=lambda a: design_diffs.get(a) or -math.inf)


# ------------------------------------------------------------------------------------------------
# One arm in one window.


def window_arm(arm: str, data: dict, days: list[str], cost_bps: float, *, lag: bool = False) -> dict:
    """The arm's run and its paired hold-svxy over the days both have, for one window and cost."""
    signal = signals(arm, data["inputs"], data["calendar"])
    if lag:
        signal = lagged(signal, data["calendar"])
    usable, missing = arm_days(signal, data["risk"], data["cash"], days)
    run = run_arm(usable, signal, data["risk"], data["cash"], PARAMS[arm], cost_bps) if usable else None
    base = hold_run("hold-svxy", usable, data["risk"], data["cash"], cost_bps) if usable else None
    return {"usable": usable, "missing": missing, "run": run, "base": base, "signal": signal}


def _fig_between(arm_part: dict, base_part: dict) -> dict:
    return {
        "sharpe_diff": sharpe_diff(arm_part["excess"], base_part["excess"]),
        "arm_dd": max_drawdown(arm_part["rets"]),
        "base_dd": max_drawdown(base_part["rets"]),
        "arm_cagr": cagr(arm_part["rets"], arm_part["days"]),
        "base_cagr": cagr(base_part["rets"], base_part["days"]),
    }


def evaluate_arm(
    arm: str,
    data: dict,
    days: list[str],
    *,
    split: str,
    crash: tuple[str, str],
    resamples: int,
    with_clauses: bool,
) -> dict:
    """Everything a window reports for one arm: metrics at both costs against the paired baseline,
    per-year returns, and (design only) the bootstrap, halves, crash-removed and lagged figures."""
    out: dict = {"coverage": None}
    judged = window_arm(arm, data, days, COST_JUDGED)
    sessions = len(days)
    out["coverage"] = {
        "sessions": sessions,
        "usable": len(judged["usable"]),
        "missing": len(judged["missing"]),
        "missing_share": missing_share(len(judged["missing"]), sessions),
        "missing_dates": judged["missing"],
        "missing_by_input": missing_inputs(
            arm, data["inputs"], data["risk"], data["cash"], judged["missing"]
        ),
    }
    if judged["run"] is None:
        return out
    run, base = judged["run"], judged["base"]
    out["10"] = {
        "arm": run_metrics(run),
        "base": run_metrics(base),
        "diff_sharpe": sharpe_diff(run["excess"], base["excess"]),
    }
    out["by_year"] = {"arm": by_year(run), "base": by_year(base)}
    reported = window_arm(arm, data, days, COST_REPORTED)
    if reported["run"] is not None:
        out["2"] = {
            "arm": run_metrics(reported["run"]),
            "base": run_metrics(reported["base"]),
            "diff_sharpe": sharpe_diff(reported["run"]["excess"], reported["base"]["excess"]),
        }
    if not with_clauses:
        return out
    boot = bootstrap_sharpe_diff(run["excess"], base["excess"], resamples=resamples)
    out["bootstrap"] = boot
    halves = []
    for lo, hi in ((None, _day_before(split)), (split, None)):
        a, b = slice_run(run, lo, hi), slice_run(base, lo, hi)
        h = (
            _fig_between(a, b)
            if a["rets"]
            else {"sharpe_diff": None, "arm_dd": 0.0, "base_dd": 0.0, "arm_cagr": None, "base_cagr": None}
        )
        h["from"], h["to"] = (a["days"][0], a["days"][-1]) if a["days"] else (None, None)
        h["sessions"] = len(a["days"])
        halves.append(h)
    out["halves"] = halves
    a, b = drop_days(run, *crash), drop_days(base, *crash)
    out["crash"] = {
        "sharpe_diff": sharpe_diff(a["excess"], b["excess"]),
        "removed": len(run["days"]) - len(a["days"]),
        "arm_worst_day": min(a["rets"]) if a["rets"] else None,
        "base_worst_day": min(b["rets"]) if b["rets"] else None,
        "arm_worst_week": worst_week(a["rets"]),
        "base_worst_week": worst_week(b["rets"]),
        "with_arm_worst_day": min(run["rets"]),
        "with_base_worst_day": min(base["rets"]),
        "with_arm_worst_week": worst_week(run["rets"]),
        "with_base_worst_week": worst_week(base["rets"]),
    }
    lg = window_arm(arm, data, days, COST_JUDGED, lag=True)
    out["lag"] = {
        "sharpe_diff": sharpe_diff(lg["run"]["excess"], lg["base"]["excess"]) if lg["run"] else None,
        "usable": len(lg["usable"]),
        "switches": lg["run"]["switches"] if lg["run"] else None,
    }
    return out


def _day_before(day: str) -> str:
    return (date.fromisoformat(day) - timedelta(days=1)).isoformat()


def baseline_rows(data: dict, days: list[str]) -> dict:
    """hold-svxy and hold-shv over the days both funds have a close, at both costs."""
    usable = [d for d in days if d in data["risk"] and d in data["cash"]]
    out: dict = {"sessions": len(days), "usable": len(usable)}
    if not usable:
        return out
    for cost, label in ((COST_JUDGED, "10"), (COST_REPORTED, "2")):
        for kind in BASELINES:
            run = hold_run(kind, usable, data["risk"], data["cash"], cost)
            m = run_metrics(run)
            if kind == "hold-shv":
                m["sharpe"] = None  # excess over SHV's own return is zero by construction
            out.setdefault(kind, {})[label] = m
            if label == "10":
                out.setdefault("by_year", {})[kind] = by_year(run)
    return out


# ------------------------------------------------------------------------------------------------
# The guards. Each takes what it checks as arguments, so a test can break the invariant.


def check_reproduction(
    mine: list[tuple[str, float]], reference: list[tuple[str, float]] | None, tol: float = 1e-12
) -> dict:
    if reference is None:
        return {"ok": False, "detail": "no reference run to compare with"}
    if [d for d, _ in mine] != [d for d, _ in reference]:
        return {"ok": False, "detail": f"{len(mine)} days against the reference's {len(reference)}"}
    worst = max((abs(a - b) for (_, a), (_, b) in zip(mine, reference, strict=True)), default=0.0)
    return {"ok": worst <= tol, "detail": f"{len(mine)} NAVs, largest gap {worst:.2e}"}


def check_accounting(run: dict, risk: dict[str, float], cash: dict[str, float], tol: float = 1e-9) -> dict:
    """Recompute the NAV from the states alone: the held fund's return each day, then the cost of any
    change of holding. The sides paid must be switches times two plus the opening side."""
    c = run["cost_bps"] / 10_000
    nav, got = 1.0, []
    days, states = run["days"], run["states"]
    for i, d in enumerate(days):
        if i:
            series = risk if states[i - 1] == regime.RISK else cash
            nav *= series[d] / series[days[i - 1]]
        if i == 0:
            nav *= 1 - c
        elif states[i] != states[i - 1]:
            nav *= 1 - 2 * c
        got.append(nav)
    worst = max((abs(a - b) for a, (_, b) in zip(got, run["navs"], strict=True)), default=0.0)
    changes = sum(1 for i in range(1, len(states)) if states[i] != states[i - 1])
    sides_ok = run["sides"] == 2 * changes + 1 and changes == run["replay_switches"]
    return {
        "ok": worst <= tol and sides_ok,
        "detail": f"largest NAV gap {worst:.2e}; {run['sides']} sides for {changes} switches",
    }


def check_causal(signals_fn, arm: str, inputs: dict, calendar: list[str], probe: str) -> dict:
    """The signal on every day up to `probe` is unchanged when everything after `probe` is removed
    (it read nothing later), and moving the inputs one day changes some signal (the check can fire)."""
    full = signals_fn(arm, inputs, calendar)
    cut = {k: {d: v for d, v in s.items() if d <= probe} for k, s in inputs.items()}
    trunc = signals_fn(arm, cut, [d for d in calendar if d <= probe])
    same = all(full.get(d) == trunc.get(d) for d in calendar if d <= probe)
    moved = signals_fn(arm, shifted_inputs(inputs, calendar), calendar)
    sensitive = any(full.get(d) != moved.get(d) for d in calendar)
    return {"ok": same and sensitive, "detail": f"causal {same}, moves with the inputs {sensitive}"}


def check_split(
    closes: dict[str, float], day: str = SPLIT_DAY, band: tuple[float, float] = (0.7, 1.3)
) -> dict:
    """The fund's close-to-close ratio across `day` is an ordinary day's, not a halving."""
    ds = sorted(closes)
    if day not in closes or ds.index(day) == 0:
        return {"ok": False, "detail": f"{day} is not in the series"}
    prev = ds[ds.index(day) - 1]
    r = closes[day] / closes[prev]
    return {"ok": band[0] < r < band[1], "detail": f"{prev} to {day}: {r - 1:+.1%}"}


def check_skip(arm: str, data: dict, days: list[str], delete: str, days_fn=arm_days) -> dict:
    """Delete the VIX3M row on `delete`: the arm drops exactly that day from its run, counts it as
    missing, and earns one return per day it kept (none is a zero for the gap)."""
    sig = signals(arm, data["inputs"], data["calendar"])
    before, _ = days_fn(sig, data["risk"], data["cash"], days)
    cut = {**data["inputs"], "vix3m": {d: v for d, v in data["inputs"]["vix3m"].items() if d != delete}}
    after_sig = signals(arm, cut, data["calendar"])
    after, missing = days_fn(after_sig, data["risk"], data["cash"], days)
    ok = delete in before and delete not in after and delete in missing and len(after) == len(before) - 1
    if ok:
        run = run_arm(after, after_sig, data["risk"], data["cash"], PARAMS[arm], COST_JUDGED)
        ok = len(run["rets"]) == len(after)
    return {"ok": ok, "detail": f"{len(before)} usable days, {len(after)} after deleting {delete}"}


def run_guards(data: dict, days: list[str], reference, split_day: str = SPLIT_DAY) -> dict[str, dict]:
    """All five guards on the study's data. `reference(arm, days, cost_bps)` returns the module's
    own replay NAVs for `control` and `flipexit`, or None."""
    cal = data["calendar"]
    inputs = data["inputs"]
    guards: dict[str, dict] = {}
    repro = []
    for arm in ("control", "flipexit"):
        sig = signals(arm, inputs, cal)
        usable, _ = arm_days(sig, data["risk"], data["cash"], days)
        mine = replay.run(usable, sig, data["risk"], data["cash"], PARAMS[arm], cost_bps=COST_JUDGED)["navs"]
        r = check_reproduction(mine, reference(arm, days, COST_JUDGED))
        repro.append(r)
        guards[f"reproduction {arm}"] = r
    acct = {}
    for arm in ARMS:
        w = window_arm(arm, data, days, COST_JUDGED)
        if w["run"] is not None:
            acct[arm] = check_accounting(w["run"], data["risk"], data["cash"])
    guards["accounting"] = {
        "ok": bool(acct) and all(v["ok"] for v in acct.values()),
        "detail": "; ".join(f"{a}: {v['detail']}" for a, v in acct.items()),
    }
    probe = cal[len(cal) // 2]
    causal = {a: check_causal(signals, a, inputs, cal, probe) for a in ARMS}
    guards["no look-ahead"] = {
        "ok": all(v["ok"] for v in causal.values()),
        "detail": "; ".join(f"{a}: {v['detail']}" for a, v in causal.items()),
    }
    guards["split"] = check_split(data["risk"], split_day)
    mid = [d for d in days if d in data["inputs"]["vix3m"]]
    guards["coverage"] = (
        check_skip("control", data, days, mid[len(mid) // 2])
        if mid
        else {"ok": False, "detail": "no day to delete"}
    )
    return guards


# ------------------------------------------------------------------------------------------------
# The study over invented or real data.


def run_study(
    data: dict,
    *,
    windows: dict | None = None,
    split: str = SPLIT,
    split_day: str = SPLIT_DAY,
    crash: tuple[str, str] = CRASH,
    resamples: int = RESAMPLES,
    reference=None,
) -> dict:
    """The whole study over `data`: `inputs` (vix, vix3m, vix9d, spx), `risk` and `cash` total-return
    closes, `calendar` (the index sessions) and `candidates` (the fund's stored sessions). The
    holdout is read only for arms that pass the design."""
    w = windows or WINDOWS
    cand = data["candidates"]
    non = non_session_days(cand, data["inputs"])
    d0, d1 = w["design"]
    h0, h1 = w["holdout"]
    ddays = window_days(cand, non, d0, d1)
    hdays = window_days(cand, non, h0, h1)
    result: dict = {
        "window": f"{d0}..{d1} design, {h0}..{h1 or (hdays[-1] if hdays else '')} holdout",
        "span": [d0, h1 or (hdays[-1] if hdays else d1)],
        "settings": {
            "cost_judged_bps": COST_JUDGED,
            "cost_reported_bps": COST_REPORTED,
            "resamples": resamples,
            "block": BLOCK,
            "seed": SEED,
            "split": split,
            "crash": list(crash),
            "return_floor": RETURN_FLOOR,
        },
        "non_sessions": sorted(non),
    }
    guards = run_guards(data, ddays, reference or (lambda arm, days, cost: None), split_day)
    result["guards"] = guards
    broken = [k for k, g in guards.items() if not g["ok"]]
    if broken:
        result["verdict"] = {
            a: judge_arm(None, None, why="a guard failed: " + ", ".join(broken)) for a in ARMS
        }
        result["base_candidate"] = None
        return result

    design = {
        "from": d0,
        "to": d1,
        "sessions": len(ddays),
        "baselines": baseline_rows(data, ddays),
        "arms": {},
    }
    for arm in ARMS:
        design["arms"][arm] = evaluate_arm(
            arm, data, ddays, split=split, crash=crash, resamples=resamples, with_clauses=True
        )
    result["design"] = design

    pvals = {a: (design["arms"][a].get("bootstrap") or {}).get("p", 1.0) for a in ARMS}
    holm = core_stats.holm(pvals, ALPHA)
    adj = core_stats.holm_adjusted(pvals)
    checks: dict[str, dict] = {}
    for arm in ARMS:
        r = design["arms"][arm]
        r["holm_p"] = adj[arm]
        if "10" not in r or "bootstrap" not in r:
            checks[arm] = None
            continue
        fig = {
            "sharpe_diff": r["bootstrap"]["diff"],
            "lower": r["bootstrap"]["lower"],
            "arm_dd": r["10"]["arm"]["max_drawdown"],
            "base_dd": r["10"]["base"]["max_drawdown"],
            "arm_cagr": r["10"]["arm"]["cagr"],
            "base_cagr": r["10"]["base"]["cagr"],
            "halves": r["halves"],
            "crash_diff": r["crash"]["sharpe_diff"],
            "lag_diff": r["lag"]["sharpe_diff"],
        }
        checks[arm] = design_checks(fig, holm[arm])
        r["checks"] = checks[arm]
    cover = {
        "design": {a: design["arms"][a]["coverage"] for a in ARMS},
    }

    holdout: dict = {
        "from": h0,
        "to": h1 or (hdays[-1] if hdays else None),
        "sessions": len(hdays),
        "baselines": baseline_rows(data, hdays),
        "arms": {},
    }
    held: dict[str, dict] = {}
    for arm in ARMS:
        ok_cov = design["arms"][arm]["coverage"]["missing_share"] <= MISSING_MAX
        if checks[arm] is not None and ok_cov and all(checks[arm].values()):
            r = evaluate_arm(
                arm, data, hdays, split=split, crash=crash, resamples=resamples, with_clauses=False
            )
            holdout["arms"][arm] = r
            if "10" in r:
                m = r["10"]
                held[arm] = {
                    "checks": holdout_checks(
                        {
                            "sharpe_diff": m["diff_sharpe"],
                            "arm_cagr": m["arm"]["cagr"],
                            "base_cagr": m["base"]["cagr"],
                        }
                    ),
                    "coverage_ok": r["coverage"]["missing_share"] <= MISSING_MAX,
                    "informative": (1 - (m["arm"]["in_svxy"] or 0.0)) >= UNINFORMATIVE,
                    "out_share": 1 - (m["arm"]["in_svxy"] or 0.0),
                }
            else:
                held[arm] = {
                    "checks": {"sharpe": False, "floor": False},
                    "coverage_ok": False,
                    "informative": False,
                    "out_share": None,
                }
    result["holdout"] = holdout
    cover["holdout"] = {a: holdout["arms"][a]["coverage"] for a in holdout["arms"]}
    result["coverage"] = cover

    verdict = {}
    for arm in ARMS:
        cov_ok = design["arms"][arm]["coverage"]["missing_share"] <= MISSING_MAX
        if checks[arm] is None:
            verdict[arm] = {"verdict": "no verdict", "why": "no usable session"}
            continue
        verdict[arm] = judge_arm({"checks": checks[arm], "coverage_ok": cov_ok}, held.get(arm))
        verdict[arm]["checks"] = checks[arm]
        if arm in held:
            verdict[arm]["holdout_checks"] = held[arm]["checks"]
            verdict[arm]["holdout_out_share"] = held[arm]["out_share"]
    result["verdict"] = verdict
    diffs = {a: (design["arms"][a].get("bootstrap") or {}).get("diff") for a in ARMS}
    result["base_candidate"] = base_candidate(verdict, diffs)
    return result


# ------------------------------------------------------------------------------------------------
# Data: the stores and the files.


def _out_dir() -> Path:
    from cherrypick.core import home as _home

    d = _home.data_dir("research") / "item39"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_data(start: str = WINDOWS["design"][0]) -> dict:
    """SVXY and SHV total-return closes from the technicals store, the four index files from the
    market-files folder, and the candidate sessions (SVXY's stored dates from `start`)."""
    import contango_replay as cr

    conn = cr.history_conn()
    try:
        risk = cr._shield.closes(conn, "SVXY", total_return=True)[0]
        cash = cr._shield.closes(conn, "SHV", total_return=True)[0]
    finally:
        conn.close()
    inputs = {name.lower(): cr.cboe(name) for name in INDEX_FILES}
    candidates = [d for d in sorted(risk) if d >= start]
    return {
        "risk": risk,
        "cash": cash,
        "inputs": inputs,
        # The trading calendar is SPX's own dates: the VIX files also carry rows on exchange holidays.
        "calendar": sorted(inputs["spx"]),
        "candidates": candidates,
    }


def reference_navs(data: dict):
    """The module's own replay through `scripts/contango_replay.py` on the packaged example config:
    `reference(arm, days, cost_bps)` for the guard."""
    import contango_replay as cr
    from cherrypick.contango import cli as _cli

    example = Path(__file__).resolve().parents[1] / "packages" / "contango" / "config.example.json"
    config = _cli.load_config(str(example))
    ratio = ratio_series(data["inputs"]["vix"], data["inputs"]["vix3m"])

    def ref(arm: str, days: list[str], cost_bps: float):
        conn = cr.history_conn()
        try:
            rows = cr.run_arms(config, conn, ratio, start=days[0], end=days[-1], cost_bps=cost_bps)
        finally:
            conn.close()
        for r in rows:
            if r["arm"] == arm:
                return r["navs"]
        return None

    return ref


def paper_agreement() -> dict:
    """The paper days' decision agreement from `contango_replay.validate`, reported, not judged."""
    try:
        import contango_replay as cr
        from cherrypick.contango import cli as _cli
        from cherrypick.contango import db as _db

        ledger = Path(_db.default_db_path())
        if not ledger.exists():
            return {"unavailable": f"no ledger at {ledger.name}"}
        return cr.validate(_cli.load_config(None), ledger, out=lambda *a, **k: None)
    except Exception as exc:  # reported, never fatal: the paper check has no say in the verdict
        return {"unavailable": f"{type(exc).__name__}: {exc}"}


def cmd_refresh(_args) -> int:
    import urllib.request

    from cherrypick.overview import files

    bad = 0
    for name in ("VIX9D", "SPX"):
        req = urllib.request.Request(
            files.CBOE_URL.format(symbol=name), headers={"User-Agent": "cherrypick-item39/1.0"}
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                text = resp.read().decode("utf-8", errors="replace")
        except OSError as exc:
            print(f"{name}: {exc}")
            bad = 1
            continue
        path = files.cboe_path(name)
        old = files.parse_cboe(path.read_text(encoding="utf-8")) if path.exists() else []
        new = files.parse_cboe(text)
        problems = files.history_problems(old, new)
        if problems:
            print(f"{name} not replaced: {'; '.join(problems)}")
            bad = 1
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8", newline="")
        tmp.replace(path)
        was = f"was {len(old)}, ending {old[-1][0]}" if old else "no file before"
        print(f"{name}: {len(new)} rows, {new[0][0]} to {new[-1][0]} ({was})")
    return bad


def plan_facts(data: dict, windows: dict | None = None) -> dict:
    """Coverage counts and listed dates; no return is computed."""
    w = windows or WINDOWS
    cand = data["candidates"]
    non = non_session_days(cand, data["inputs"])
    out: dict = {"files": {}, "windows": {}}
    last = cand[-1] if cand else None
    for name, series in data["inputs"].items():
        ds = sorted(series)
        out["files"][name.upper()] = {
            "first": ds[0],
            "last": ds[-1],
            "rows": len(ds),
            "current": bool(last) and ds[-1] >= last,
        }
    out["last_session"] = last
    out["stale"] = [n for n, f in out["files"].items() if not f["current"]]
    out["non_sessions"] = sorted(non)
    out["fund_store_differences"] = sorted(
        d for d in set(data["risk"]) ^ set(data["cash"]) if w["design"][0] <= d <= (last or w["design"][0])
    )
    for wname, (lo, hi) in w.items():
        days = window_days(cand, non, lo, hi)
        arms = {}
        for arm in ARMS:
            sig = signals(arm, data["inputs"], data["calendar"])
            usable, missing = arm_days(sig, data["risk"], data["cash"], days)
            arms[arm] = {
                "usable": len(usable),
                "missing": len(missing),
                "missing_share": missing_share(len(missing), len(days)),
                "missing_by_input": missing_inputs(arm, data["inputs"], data["risk"], data["cash"], missing),
                "missing_dates": missing,
            }
        out["windows"][wname] = {
            "from": lo,
            "to": hi or (days[-1] if days else None),
            "sessions": len(days),
            "arms": arms,
        }
    return out


def cmd_plan(args) -> int:
    data = load_data()
    facts = plan_facts(data)
    path = Path(args.json) if args.json else _out_dir() / "plan.json"
    path.write_text(json.dumps(facts, indent=1), encoding="utf-8")
    print(f"candidate sessions {len(data['candidates'])}, last {facts['last_session']}")
    for name, f in facts["files"].items():
        print(f"  {name:6} {f['first']} to {f['last']} ({f['rows']} rows){'' if f['current'] else '  STALE'}")
    print(f"non-sessions skipped: {facts['non_sessions']}")
    print(f"dates the two fund stores do not share: {facts['fund_store_differences']}")
    for wname, wi in facts["windows"].items():
        print(f"{wname}: {wi['from']} to {wi['to']}, {wi['sessions']} sessions")
        for arm, a in wi["arms"].items():
            print(
                f"  {arm:11} usable {a['usable']}, missing {a['missing']} ({a['missing_share']:.2%})"
                f" {a['missing_by_input'] or ''}"
            )
    if facts["stale"]:
        print(f"STALE: {', '.join(facts['stale'])} end before the last session; run `refresh`")
        return 1
    return 0


def cmd_study(args) -> int:
    data = load_data()
    result = run_study(data, reference=reference_navs(data))
    result["paper_agreement"] = paper_agreement()
    result["coverage_files"] = plan_facts(data)["files"]
    out = Path(args.json) if args.json else _out_dir() / "results.json"
    out.write_text(json.dumps(result, default=str), encoding="utf-8")
    out.with_suffix(".md").write_text(report(result), encoding="utf-8")
    for arm, v in result["verdict"].items():
        print(arm, v["verdict"], v.get("why", ""))
    return 0


def cmd_report(args) -> int:
    src = Path(args.json) if args.json else _out_dir() / "results.json"
    src.with_suffix(".md").write_text(report(json.loads(src.read_text(encoding="utf-8"))), encoding="utf-8")
    return 0


# ------------------------------------------------------------------------------------------------
# The report.

CLAUSES = {
    "sharpe": ("Sharpe", "above hold-svxy's, lower bound above zero, Holm 0.05 across four"),
    "drawdown": ("Drawdown", "smaller than hold-svxy's"),
    "floor": ("Return floor", "CAGR no more than 3 points below hold-svxy's"),
    "halves": ("Both halves", "Sharpe difference above zero and drawdown no deeper, each half"),
    "crash": ("Not one crash", "Sharpe difference above zero without 2020-02-19 to 2020-04-30"),
    "lag": ("Not the close", "Sharpe difference above zero with every signal lagged one session"),
}


def _pct(x: float | None, digits: int = 1) -> str:
    if x is None:
        return "n/a"
    return f"{'−' if x < 0 and round(abs(x) * 100, digits) else ''}{abs(x) * 100:.{digits}f}%"


def _num(x: float | None, digits: int = 2) -> str:
    if x is None:
        return "n/a"
    return f"{'−' if x < 0 and round(abs(x), digits) else ''}{abs(x):.{digits}f}"


def _pts(x: float | None) -> str:
    return "n/a" if x is None else f"{_num(x * 100, 1)} pts"


def findings(result: dict) -> list[str]:
    """The reading in words, worked out from the figures each time the study runs."""
    out: list[str] = []
    if any(not g["ok"] for g in result.get("guards", {}).values()):
        broken = [k for k, g in result["guards"].items() if not g["ok"]]
        return [f"**A guard failed** ({', '.join(broken)}), so no arm was read and every arm has no verdict."]
    d = result["design"]
    base = d["baselines"].get("hold-svxy", {}).get("10")
    if base:
        out.append(
            f"**Holding SVXY** over the design window ({d['from']} to {d['to']}, {d['sessions']}"
            f" sessions) earned {_pct(base['cagr'])} a year with a {_pct(base['max_drawdown'])} maximum"
            f" drawdown and a Sharpe ratio of {_num(base['sharpe'])}, at 10 bps."
        )
    for arm in ARMS:
        r = d["arms"][arm]
        v = result["verdict"][arm]
        if "10" not in r:
            out.append(f"**{arm}:** no usable session.")
            continue
        m, b = r["10"]["arm"], r["10"]["base"]
        boot = r["bootstrap"]
        failed = [CLAUSES[k][0].lower() for k, ok in (r.get("checks") or {}).items() if not ok]
        line = (
            f"**{arm}** ({v['verdict']}): Sharpe {_num(m['sharpe'])} against {_num(b['sharpe'])},"
            f" a difference of {_num(boot['diff'])} with a one-sided lower bound of"
            f" {_num(boot['lower'])}; drawdown {_pct(m['max_drawdown'])} against"
            f" {_pct(b['max_drawdown'])}; CAGR {_pct(m['cagr'])} against"
            f" {_pct(b['cagr'])} ({_pts((m['cagr'] or 0) - (b['cagr'] or 0))}); {m['switches']} switches,"
            f" {m['whipsaws']} whipsaws, costs {_num(m['cost_pts_year'])} points a year."
        )
        line += f" It misses on: {', '.join(failed)}." if failed else " Every design clause holds."
        if r["coverage"]["missing_share"] > MISSING_MAX:
            line += f" {r['coverage']['missing']} of {r['coverage']['sessions']} sessions miss an input."
        cl = r.get("checks") or {}
        if boot["se"] is not None and not cl.get("sharpe") and cl.get("drawdown") and cl.get("floor"):
            line += (
                " The drawdown and the return floor hold but the Sharpe clause does not: the"
                f" bootstrap's standard error of the difference is {_num(boot['se'])}, so a difference"
                f" under about {_num(2 * boot['se'])} cannot be told from zero in this window (the"
                f" declaration assumed about {MDE_DECLARED}). Read it as no edge of detectable size,"
                " not no edge."
            )
        out.append(line)
    cand = result.get("base_candidate")
    passing = [a for a, v in result["verdict"].items() if v["verdict"] == "pass"]
    if passing:
        out.append(
            f"**Passing:** {', '.join(passing)}. The base candidate, with the higher design Sharpe"
            f" difference, is {cand}."
        )
    else:
        out.append(
            "**No arm passes.** The declaration reads a fail as the evidence the retirement rule"
            " needs; paper keeps running unchanged."
        )
    ho = result["holdout"]
    if ho["arms"]:
        for arm in ho["arms"]:
            hv = result["verdict"][arm]
            out.append(
                f"**Holdout, {arm}** ({ho['from']} to {ho['to']}): {hv['why']}. It sat out of SVXY for"
                f" {_pct(hv.get('holdout_out_share'))} of sessions."
            )
    else:
        out.append(
            "**Holdout:** not read for any arm, because none passed the design; a holdout cannot"
            " overturn a design fail."
        )
    gap = [a for a in ARMS if result["coverage"]["design"][a]["missing"]]
    if gap:
        out.append(
            "**Skipped sessions** (design): "
            + "; ".join(f"{a} {result['coverage']['design'][a]['missing']}" for a in gap)
            + f". Non-sessions in no index file: {len(result.get('non_sessions', []))} (all windows)."
        )
    pa = result.get("paper_agreement")
    if pa and "unavailable" not in pa:
        out.append(
            "**Paper days** (reported, no say in the verdict): the replay's decision agrees with the"
            f" ledger on {pa.get('decision_ok', 0)} of"
            f" {pa.get('decision_ok', 0) + pa.get('decision_bad', 0)} sessions;"
            f" {pa.get('rule_bad', 0)} rule breaks."
        )
    elif pa:
        out.append(f"**Paper days:** not compared ({pa['unavailable']}).")
    out.append(
        "**Seen before declaring:** the 0.97 and 1.0 thresholds were chosen with the exploratory"
        " replay in sight and that replay's span covers both windows, so control, flipexit and the"
        " SVXY baseline have no clean holdout; the two gates use fixed values (1.0 and 10 sessions),"
        " no grid."
    )
    return out


def _row(label: str, verdict: str, m: dict) -> str:
    switches = "" if m["switches"] is None else m["switches"]
    whips = "" if m["whipsaws"] is None else m["whipsaws"]
    return (
        f"| {label} | {verdict} | {_num(m['sharpe'])} | {_pct(m['cagr'])} | {_pct(m['max_drawdown'])}"
        f" | {_num(m['calmar'])} | {_pct(m['worst_day'])} | {_pct(m['worst_week'])}"
        f" | {_pct(m['in_svxy'], 0)} | {switches} | {whips} | {_num(m['cost_pts_year'])} |"
    )


TABLE_HEAD = (
    "| arm | verdict | Sharpe | CAGR | max drawdown | Calmar | worst day | worst week | in SVXY"
    " | switches | whipsaws | costs, pts/yr |"
)
TABLE_RULE = "|---|---|---|---|---|---|---|---|---|---|---|---|"


def _window_tables(win: dict, name: str, verdicts: dict, cost: str) -> list[str]:
    lines = [TABLE_HEAD, TABLE_RULE]
    for kind in BASELINES:
        m = win["baselines"].get(kind, {}).get(cost)
        if m:
            lines.append(_row(kind, "judged baseline" if kind == "hold-svxy" else "reported", m))
    for arm in ARMS:
        r = win["arms"].get(arm)
        if r and cost in r:
            lines.append(_row(arm, verdicts[arm]["verdict"], r[cost]["arm"]))
    return lines


def report(result: dict) -> str:
    verdicts = result["verdict"]
    words = [v["verdict"] for v in verdicts.values()]
    counts = {w: words.count(w) for w in ("pass", "fail", "no verdict")}
    overall = "pass" if counts["pass"] else "fail" if counts["fail"] else "no verdict"
    lines = [
        "# Item 39: the contango module's own rule on real daily prices",
        "",
        f"**Verdict: {overall}.** Of the four judged arms, {counts['pass']} pass, {counts['fail']} fail and"
        f" {counts['no verdict']} have no verdict. Window {result['window']}.",
        "",
        "Each arm holds SVXY or SHV by the module's own rule on the close's inputs and is judged"
        " against holding SVXY, on daily returns in excess of SHV's, at 10 bps of NAV a side."
        " Hold-SHV is reported, not judged.",
        "",
        "## What it says",
        "",
        *[f"- {x}" for x in findings(result)],
        "",
        "## How to read the tables",
        "",
        "- **Sharpe**: mean daily return in excess of SHV's over its sample standard deviation, times"
        " the square root of 252. Hold-SHV has none (its excess is zero by construction).",
        "- **vs hold-svxy**: the arm's Sharpe less hold-svxy's over the arm's own sessions. **lower"
        " bound**: the one-sided 95% lower bound from a stationary bootstrap of paired daily returns"
        f" (mean block {BLOCK}, {result['settings']['resamples']:,} resamples, seed {SEED})."
        " **Holm p**: the share of resamples at or below zero, Holm-adjusted across the four arms;"
        f" it must be {ALPHA} or below.",
        "- **CAGR** and **max drawdown** are from compounding the daily returns, the first of which"
        " is the opening side of cost. **Calmar** is CAGR over the absolute drawdown. **worst"
        " week**: five consecutive sessions compounded.",
        "- **in SVXY**: the share of return days the fund was held. **switches** exclude the opening"
        " trade. **whipsaws**: a switch followed by the opposite switch within"
        f" {WHIPSAW_SESSIONS} sessions. **costs, pts/yr**: sides paid times the cost, over the years.",
        "- The **clause** table is the pass rule; a clause reads pass or fail. A Sharpe fail with a"
        " good drawdown reads as a want of power when the standard error is large (see the minimum"
        " detectable effect in the declaration).",
        "- **Crash-removed**: the returns of 2020-02-19 to 2020-04-30 dropped from every series and"
        " the rest joined. **Lagged**: every signal one session old, filled at the same-day close.",
        "- Per-year tables are compounded returns; **2 bps** is the module's own slippage floor and"
        " is reported, not judged.",
    ]
    if any(not g["ok"] for g in result.get("guards", {}).values()):
        lines += ["", "## Guards", ""]
        lines += [
            f"- {k}: {'ok' if g['ok'] else 'FAILED'} ({g['detail']})" for k, g in result["guards"].items()
        ]
        return "\n".join(lines) + "\n"

    d = result["design"]
    lines += ["", f"## Design: {d['from']} to {d['to']}, {d['sessions']} sessions, 10 bps a side", ""]
    lines += _window_tables(d, "design", verdicts, "10")
    lines += [
        "",
        "### The pass rule, clause by clause",
        "",
        "| arm | "
        + " | ".join(CLAUSES[k][0] for k in CLAUSES)
        + " | Sharpe vs hold-svxy | lower bound | Holm p |",
        "|---|" + "---|" * (len(CLAUSES) + 3),
    ]
    for arm in ARMS:
        r = d["arms"][arm]
        cl = r.get("checks")
        if not cl:
            lines.append(f"| {arm} | " + " | ".join("n/a" for _ in CLAUSES) + " | n/a | n/a | n/a |")
            continue
        b = r["bootstrap"]
        lines.append(
            f"| {arm} | "
            + " | ".join("pass" if cl[k] else "fail" for k in CLAUSES)
            + f" | {_num(b['diff'])} | {_num(b['lower'])} | {_num(r['holm_p'], 3)} |"
        )
    lines += ["", "Needed: " + "; ".join(f"**{CLAUSES[k][0]}** {CLAUSES[k][1]}" for k in CLAUSES) + ".", ""]

    lines += [
        "### Halves, crash-removed and lagged",
        "",
        "| arm | half | sessions | Sharpe vs hold-svxy | arm drawdown | hold-svxy drawdown |",
        "|---|---|---|---|---|---|",
    ]
    for arm in ARMS:
        for i, h in enumerate(d["arms"][arm].get("halves", []), start=1):
            lines.append(
                f"| {arm} | {i}: {h['from']} to {h['to']} | {h['sessions']} | {_num(h['sharpe_diff'])}"
                f" | {_pct(h['arm_dd'])} | {_pct(h['base_dd'])} |"
            )
    lines += [
        "",
        "| arm | Sharpe diff, crash removed | sessions removed | worst day with / without"
        " | worst week with / without | Sharpe diff, lagged | switches, lagged |",
        "|---|---|---|---|---|---|---|",
    ]
    for arm in ARMS:
        r = d["arms"][arm]
        if "crash" not in r:
            continue
        c, lg = r["crash"], r["lag"]
        lines.append(
            f"| {arm} | {_num(c['sharpe_diff'])} | {c['removed']}"
            f" | {_pct(c['with_arm_worst_day'])} / {_pct(c['arm_worst_day'])}"
            f" (hold-svxy {_pct(c['with_base_worst_day'])} / {_pct(c['base_worst_day'])})"
            f" | {_pct(c['with_arm_worst_week'])} / {_pct(c['arm_worst_week'])}"
            f" (hold-svxy {_pct(c['with_base_worst_week'])} / {_pct(c['base_worst_week'])})"
            f" | {_num(lg['sharpe_diff'])} | {lg['switches']} |"
        )
    lines += ["", f"### Reported at {COST_REPORTED:g} bps a side (not judged)", ""]
    lines += _window_tables(d, "design", {a: {"verdict": "reported"} for a in ARMS}, "2")
    lines += _year_table(d, "Design")

    ho = result["holdout"]
    lines += ["", f"## Holdout: {ho['from']} to {ho['to']}, {ho['sessions']} sessions, 10 bps a side", ""]
    if ho["arms"]:
        lines += _window_tables(ho, "holdout", verdicts, "10")
        lines += [
            "",
            "| arm | Sharpe vs hold-svxy | CAGR vs hold-svxy | out of SVXY | holdout verdict |",
            "|---|---|---|---|---|",
        ]
        for arm, r in ho["arms"].items():
            m = r["10"]
            lines.append(
                f"| {arm} | {_num(m['diff_sharpe'])} | {_pct(m['arm']['cagr'])} vs {_pct(m['base']['cagr'])}"
                f" | {_pct(verdicts[arm].get('holdout_out_share'))} | {verdicts[arm]['verdict']} |"
            )
    else:
        lines += [
            "No arm passed the design, so no arm was read here. The baselines, reported:",
            "",
            TABLE_HEAD,
            TABLE_RULE,
        ]
        for kind in BASELINES:
            m = ho["baselines"].get(kind, {}).get("10")
            if m:
                lines.append(_row(kind, "reported", m))
    lines += _year_table(ho, "Holdout")

    lines += [
        "",
        "## Sessions and coverage",
        "",
        "| window | arm | sessions | skipped | share | inputs missing |",
        "|---|---|---|---|---|---|",
    ]
    for wname, cov in result["coverage"].items():
        for arm, c in cov.items():
            lines.append(
                f"| {wname} | {arm} | {c['sessions']} | {c['missing']} | {_pct(c['missing_share'], 2)}"
                f" | {c['missing_by_input'] or ''} |"
            )
    lines += [
        "",
        "Non-sessions (in no index file, skipped and counted): "
        f"{', '.join(result.get('non_sessions', [])) or 'none'}.",
        "",
    ]
    files = result.get("coverage_files")
    if files:
        lines += [
            "Index files used: "
            + "; ".join(f"{n} {f['first']} to {f['last']}" for n, f in files.items())
            + ".",
            "",
        ]
    lines += ["## Guards", ""]
    lines += [f"- {k}: {'ok' if g['ok'] else 'FAILED'} ({g['detail']})" for k, g in result["guards"].items()]
    return "\n".join(lines) + "\n"


def _year_table(win: dict, title: str) -> list[str]:
    series = {}
    for kind, y in (win["baselines"].get("by_year") or {}).items():
        series[kind] = y
    for arm, r in win["arms"].items():
        if "by_year" in r:
            series[arm] = r["by_year"]["arm"]
    if not series:
        return []
    years = sorted({y for s in series.values() for y in s})
    lines = [
        "",
        f"### {title}: return by calendar year, 10 bps",
        "",
        "| series | " + " | ".join(years) + " |",
        "|---|" + "---|" * len(years),
    ]
    for name, s in series.items():
        lines.append(f"| {name} | " + " | ".join(_pct(s.get(y)) for y in years) + " |")
    return lines


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("refresh", help="VIX9D and SPX from Cboe (network)").set_defaults(fn=cmd_refresh)
    for name, fn, help_ in (
        ("plan", cmd_plan, "coverage counts and skipped dates (no returns)"),
        ("study", cmd_study, "run the study and write results.json and results.md"),
        ("report", cmd_report, "results.md from an existing results.json"),
    ):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--json")
        sp.set_defaults(fn=fn)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
