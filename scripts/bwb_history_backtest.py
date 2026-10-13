"""Modelled bwb backtest on daily SPX bars: control, delta and bounce over years of history.

    python scripts/bwb_history_backtest.py validate                 # the model against the paper ledger
    python scripts/bwb_history_backtest.py run --start 2024-10-07 --end 2026-10-01 --validate
    python scripts/bwb_history_backtest.py run --start 2011-01-03 --scale 7000 --cboe-dir .tmp/cboe
    python scripts/bwb_history_backtest.py bars-check --cboe-dir .tmp/cboe
    python scripts/bwb_history_backtest.py fit                      # re-fit the smile (needs scipy)
    python scripts/bwb_history_backtest.py fetch .tmp/cboe          # Cboe VIX9D/VIX/VIX3M/SPX files

Read-only against every store. Only `fetch` writes, to the directory it is given, and `run --out`
writes its rows where it is told.

**The question.** The paper books started on 2026-08-24, and a few weeks of a daily ladder are one
market regime. This replays the module's own rules over the history there is, so a live or sizing
decision rests on more than one rebound (2026-10-09: control -$9.1k, delta -$10.0k, bounce about
flat over 2024-10..2026-10, where the paper window showed delta +$6.6k).

**The structure is the module's.** Expiry from `clock.target_expiration`, strikes from
`engine.select_strikes`, fees from `cherrypick.core.fees` and `engine.settlement_fee`. Nothing about
the trade is re-implemented here, only the prices.

**The prices are a model**, stated so it can be argued with:
- A put smile scaled by the prior close of VIX9D: sigma(K) = v9 * (a + d/days + (b + e/days) * (-m)
  + c * m^2), m = ln(K/S) / (v9 * sqrt(T)), floored at 2 vol points. `FITTED_SMILE` was fitted
  jointly (`fit`) to the paper ledger's recorded leg IVs, entry credits, add-on credits and expected
  moves, 2026-08-24..2026-10-08. Those sessions had VIX9D between about 11 and 15. Every stressed
  reading is an extrapolation of a calm-market fit.
- The expected move is the module's (0.85 x the ATM straddle), the straddle priced by the same smile
  with the call from put-call parity at zero rate.
- Entry is at the day's OPEN with the time to expiry counted from 10:00 ET (the module enters at
  10:00). Settlement is the expiration day's close (PM-settled SPXW).
- Triggers on daily bars, with the trigger level re-solved each day at 12:45 ET time-to-expiry and
  THAT day's VIX9D close (a small look-ahead, kept so `validate` reproduces the 2026-10-09 study):
  delta fires the first day the low reaches L50 (the spot at which the near wing is 50 delta), priced
  at L50 or at the open when the day gaps below it. bounce latches on the same touch and fires when
  spot recovers to L45: on the touch day if it closes at or above L45, else the first later day whose
  high reaches it. The live loop samples every 60 seconds, so a day that touches and recovers within
  the hour is one fire here and may be none there.
- Costs: the module's fee stack, plus slippage at the paper ledger's recorded means (`ENTRY_SLIP`,
  `ADDON_SLIP`: 25% of mid-to-natural). `fills` re-prices the whole run at halfway, 75% and natural.

**Before 2023-08-30 there are no SPX bars in the store.** SPY's raw bars (the technicals history
store) are scaled each day by Cboe's SPX close over SPY's close, which needs `SPX.csv` from `fetch`.
`bars-check` runs the overlap both ways and reports how far the proxy moves the result.

**`--scale`** re-sizes every position's path to one index level (e.g. 7000) at its entry. The
module's $5/$10 wings are fixed in points, so at SPX 1,300 the same rule is a much wider trade
relative to the market. Scaling asks what today's trade would have done in that market. The smile is
in standardised moneyness, so scaling changes the sizes and not the shape.

**`validate`** re-runs every settled paper entry and compares it with the ledger: entry credit,
whether each add-on fired, and net per trade. It refuses, with a non-zero exit, when any figure is
outside `TOLERANCE`. `run --validate` reports nothing unless validation passes.
"""

from __future__ import annotations

import argparse
import bisect
import contextlib
import csv
import io
import json
import math
import sqlite3
import statistics
import sys
import urllib.request
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from cherrypick.bwb import clock, engine
from cherrypick.bwb import db as bwb_db
from cherrypick.core import fees as _fees
from cherrypick.core import home as _home
from cherrypick.core import structures

ARMS = ("control", "delta", "bounce")
ADDON_ARMS = ("delta", "bounce")

# Fitted 2026-10-09 by `fit` on the paper ledger, entries 2026-08-24..2026-10-08: 28 sessions'
# entry legs, 53 add-on fills with a spot from the GEX recorder, and the recorded expected moves.
# Fit quality then: leg IV bias +0.0 points, entry credit MAE about 14 cents, expected move +-6 points.
FITTED_SMILE = {
    "a": 0.907878368548014,
    "b": 0.17505145840038414,
    "c": 0.02300463829256136,
    "d": 0.11028905716435217,
    "e": -0.02393911974053637,
}

# Recorded means on the paper ledger (2026-08-24..10-08): slippage charged at 25% of mid-to-natural,
# and the mid-to-natural gap itself, in dollars per structure.
ENTRY_SLIP = 10.78
ADDON_SLIP = 6.79
ENTRY_GAP = 43.1
ADDON_GAP = 27.1
ENTRY_FEE = _fees.ic_open_fee("SPX", 1, legs=4, sell_legs=2, ndigits=4)
ADDON_FEE = _fees.ic_open_fee("SPX", 1, legs=2, sell_legs=1, ndigits=4)

# What `validate` must reproduce before anything is reported. Declared before the 2026-10-12 run:
# - fire agreement: per add-on arm, the share of positions where the model and the ledger agree on
#   WHETHER the add-on fired (the day may differ; daily bars cannot see a 60-second loop);
# - per-trade bias: |model net - ledger net| summed, over n, per arm, in dollars;
# - credit MAE: entry credit, dollars per share;
# - min positions: fewer settled positions than this and nothing is claimed.
TOLERANCE = {
    "fire_agreement_min": 0.80,
    "per_trade_bias_max": 25.0,
    "credit_mae_max": 0.20,
    "min_positions": 20,
}
VALIDATE_FROM = "2026-08-24"

CBOE_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{name}_History.csv"
CBOE_FILES = ("VIX9D", "VIX", "VIX3M", "SPX")


# ---- inputs --------------------------------------------------------------------------------------


def parse_cboe(text: str) -> dict[str, float]:
    """{iso date: close} from a Cboe daily-history CSV (an OHLC file's CLOSE, else its one value)."""
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    if not rows or rows[0][0].strip().upper() != "DATE":
        return {}
    header = [h.strip().upper() for h in rows[0]]
    col = header.index("CLOSE") if "CLOSE" in header else 1
    out = {}
    for row in rows[1:]:
        try:
            v = float(row[col])
            d = datetime.strptime(row[0].strip(), "%m/%d/%Y").date().isoformat()
        except (ValueError, IndexError):
            continue
        if v > 0:
            out[d] = v
    return out


def cboe_candidates(name: str, cboe_dir: str | None) -> list[Path]:
    data = _home.data_dir()
    found = [Path(cboe_dir) / f"{name}.csv"] if cboe_dir else []
    return found + [data / "market-files" / "cboe" / f"{name}.csv", data / "pmcc" / "replay" / f"{name}.csv"]


def cboe(name: str, cboe_dir: str | None = None, *, required: bool = True) -> dict[str, float]:
    for path in cboe_candidates(name, cboe_dir):
        if path.exists():
            return parse_cboe(path.read_text(encoding="utf-8"))
    if required:
        raise SystemExit(
            f"no {name}.csv (looked in {[str(p) for p in cboe_candidates(name, cboe_dir)]}); run `fetch`"
        )
    return {}


def fetch(directory: str) -> None:
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    for name in CBOE_FILES:
        with urllib.request.urlopen(CBOE_URL.format(name=name), timeout=60) as resp:
            text = resp.read().decode("utf-8")
        if not parse_cboe(text):
            raise SystemExit(f"{name}: the response did not parse as a Cboe history")
        (out / f"{name}.csv").write_text(text, encoding="utf-8")
        print(f"{name}: {len(parse_cboe(text))} rows -> {out / (name + '.csv')}")


def technicals_db(name: str) -> Path:
    """The technicals store's `eod.db` or `history.db`, resolved as `cherrypick.technicals.paths`
    does. Kept local so the bwb test job, which installs only core and bwb, can import this file."""
    return _home.data_dir("technicals", env="TECHNICALS_DATA_DIR") / name


@contextlib.contextmanager
def _ro(path: Path):
    """A read-only connection, closed on exit (sqlite3's own context manager only commits)."""
    conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    try:
        yield conn
    finally:
        conn.close()


def _bars(path: Path, symbol: str) -> dict[str, tuple[float, float, float, float]]:
    if not path.exists():
        return {}
    with _ro(path) as conn:
        q = "SELECT date, open, high, low, close FROM bars WHERE symbol = ? AND close IS NOT NULL"
        return {d: (o, h, lo, c) for d, o, h, lo, c in conn.execute(q, (symbol,)) if None not in (o, h, lo)}


def load_bars(source: str = "auto", cboe_dir: str | None = None) -> dict[str, tuple]:
    """{iso date: (open, high, low, close, source)} for SPX.

    `spx` is the technicals store's SPX bars (the broker's daily candles, 2023-08-30 on). `spy` is
    SPY's raw bars scaled each day by Cboe's SPX close over SPY's close. `auto` takes SPX where the
    store has it and the SPY proxy before."""
    out: dict[str, tuple] = {}
    if source in ("auto", "spy"):
        spx_close = cboe("SPX", cboe_dir)
        spy = _bars(technicals_db("history.db"), "SPY")
        spy.update(_bars(technicals_db("eod.db"), "SPY"))
        for d, (o, h, lo, c) in spy.items():
            if d in spx_close and c > 0:
                k = spx_close[d] / c
                out[d] = (o * k, h * k, lo * k, spx_close[d], "spy")
    if source in ("auto", "spx"):
        for d, bar in _bars(technicals_db("eod.db"), "SPX").items():
            out[d] = (*bar, "spx")
    if not out:
        raise SystemExit(f"no {source} bars")
    return out


class Series:
    """A dated series with a strictly-prior lookup (bisect, not a scan)."""

    def __init__(self, values: dict[str, float]):
        self.values = values
        self.keys = sorted(values)

    def get(self, d: str, default=None):
        return self.values.get(d, default)

    def prior(self, d: str) -> float | None:
        i = bisect.bisect_left(self.keys, d)
        return self.values[self.keys[i - 1]] if i > 0 else None


class Context:
    def __init__(self, bars: dict[str, tuple], vix9d: dict[str, float], smile: dict | None = None):
        self.bars = bars
        self.days = sorted(bars)
        self.v9 = Series({d: v / 100 for d, v in vix9d.items()})
        self.p = smile or FITTED_SMILE


def context(source: str = "auto", cboe_dir: str | None = None, params: str | None = None) -> Context:
    smile = json.loads(Path(params).read_text(encoding="utf-8")) if params else None
    return Context(load_bars(source, cboe_dir), cboe("VIX9D", cboe_dir), smile)


# ---- the price model -----------------------------------------------------------------------------


def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def sigma(p: dict, S: float, K: float, T: float, vol: float) -> float:
    m = math.log(K / S) / (vol * math.sqrt(T))
    inv = 1 / max(T * 365, 0.25)
    return max(vol * (p["a"] + p["d"] * inv + (p["b"] + p["e"] * inv) * (-m) + p["c"] * m * m), 0.02)


def put(p: dict, S: float, K: float, T: float, vol: float) -> float:
    s = sigma(p, S, K, T, vol)
    d1 = (math.log(S / K) + 0.5 * s * s * T) / (s * math.sqrt(T))
    return K * _ncdf(-(d1 - s * math.sqrt(T))) - S * _ncdf(-d1)


def put_delta(p: dict, S: float, K: float, T: float, vol: float) -> float:
    s = sigma(p, S, K, T, vol)
    d1 = (math.log(S / K) + 0.5 * s * s * T) / (s * math.sqrt(T))
    return _ncdf(d1) - 1


def level(p: dict, K: float, T: float, vol: float, target: float) -> float:
    """The spot at which the put at K has |delta| == target (|delta| falls as spot rises)."""
    lo, hi = K * 0.8, K * 1.2
    for _ in range(60):
        mid = (lo + hi) / 2
        if abs(put_delta(p, mid, K, T, vol)) > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def years(d_from: str, hhmm: str, exp: str) -> float:
    t0 = datetime.fromisoformat(d_from + "T" + hhmm)
    t1 = datetime.fromisoformat(exp + "T16:00")
    return max((t1 - t0).total_seconds(), 1800) / (365 * 86400)


# ---- one position ----------------------------------------------------------------------------------


def _payoff_bwb(body: float, near: float, far: float, s: float) -> float:
    return 2 * max(body - s, 0) - max(near - s, 0) - max(far - s, 0)


def simulate(ctx: Context, entry_day: str, scale: float | None = None) -> dict | None:
    """One entry, every arm. None when the expiration has no bar (a holiday the calendar missed) or
    VIX9D has no prior close. `scale` re-sizes the path to that index level at the open."""
    if entry_day not in ctx.bars:
        return None
    plan = clock.target_expiration(date.fromisoformat(entry_day))
    if plan is None or plan["expiration"] not in ctx.bars:
        return None
    exp = plan["expiration"]
    vol0 = ctx.v9.prior(entry_day)
    if vol0 is None:
        return None
    raw_open = ctx.bars[entry_day][0]
    k = scale / raw_open if scale else 1.0

    def bar(d):
        o, h, lo, c, _src = ctx.bars[d]
        return o * k, h * k, lo * k, c * k

    p = ctx.p
    o = bar(entry_day)[0]
    T0 = years(entry_day, "10:00", exp)
    atm = round(o / 5) * 5
    pa = put(p, o, atm, T0, vol0)
    em = structures.expected_move(pa + o - atm, pa)
    listed = [atm + 5 * i for i in range(-400, 401)]
    st = engine.select_strikes(o, em, {}, listed)
    body, near, far = st["body"], st["near"], st["far"]
    credit = 2 * put(p, o, body, T0, vol0) - put(p, o, near, T0, vol0) - put(p, o, far, T0, vol0)
    pos = {
        "entry": entry_day,
        "exp": exp,
        "src": ctx.bars[entry_day][4],
        "scale": round(k, 6),
        "spot": o,
        "em": em,
        "body": body,
        "near": near,
        "far": far,
        "credit": credit,
        "vol": vol0,
        "dte": plan["dte"],
    }
    if credit <= 0:
        pos["refused"] = "no_credit"
        return pos
    i0 = bisect.bisect_left(ctx.days, entry_day)
    i1 = bisect.bisect_right(ctx.days, exp)
    path = ctx.days[i0:i1]
    short_k, long_k = far + 5, far - 5
    for arm in ADDON_ARMS:
        peaked = False
        for d in path:
            do, dh, dl, dc = bar(d)
            vol = ctx.v9.get(d, vol0) if d != entry_day else vol0
            T = years(d, "12:45", exp)
            L50 = level(p, near, T, vol, 0.50)
            if arm == "delta":
                if dl > L50:
                    continue
                s = min(do, L50) if d != entry_day else L50
            else:
                L45 = level(p, near, T, vol, 0.45)
                if not peaked and dl <= L50:
                    peaked = True
                    if dc < L45:
                        continue
                    s = L45
                elif peaked and dh >= L45:
                    s = max(do, L45)
                else:
                    continue
            cr = put(p, s, short_k, T, vol) - put(p, s, long_k, T, vol)
            if cr > 0:
                pos[arm] = {"day": d, "spot": s, "credit": cr}
                break
    settle = bar(exp)[3]
    pos["settle"] = settle
    bwb_intr = _payoff_bwb(body, near, far, settle)
    itm_bwb = sum(1 for x in (body, near, far) if x > settle)
    add_intr = max(short_k - settle, 0) - max(long_k - settle, 0)
    itm_add = sum(1 for x in (short_k, long_k) if x > settle)
    for arm in ARMS:
        fired = arm != "control" and arm in pos
        gross = (credit - bwb_intr) * 100
        cost = ENTRY_FEE + ENTRY_SLIP
        itm = itm_bwb
        if fired:
            gross += (pos[arm]["credit"] - add_intr) * 100
            cost += ADDON_FEE + ADDON_SLIP
            itm += itm_add
        cost += engine.settlement_fee(itm)

        def value(s, fired=fired, arm=arm):
            v = (credit - _payoff_bwb(body, near, far, s)) * 100
            if fired:
                v += (pos[arm]["credit"] - (max(short_k - s, 0) - max(long_k - s, 0))) * 100
            return v

        worst = -min(value(s) for s in (far - 10, long_k, far, short_k, body, near, near + 10))
        pos["net_" + arm] = gross - cost
        pos["risk_" + arm] = max(worst, 0)
        pos["gap_" + arm] = ENTRY_GAP + (ADDON_GAP if fired else 0)
    return pos


def backtest(ctx: Context, start: str, end: str, scale: float | None = None) -> list[dict]:
    out = []
    for d in ctx.days:
        if start <= d <= end:
            pos = simulate(ctx, d, scale)
            if pos:
                out.append(pos)
    return out


# ---- reading a run -------------------------------------------------------------------------------


def drawdown(values: list[float]) -> float:
    cum = peak = dd = 0.0
    for x in values:
        cum += x
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return dd


def session_nets(rows: list[dict], arm: str) -> dict[str, float]:
    """Per settlement session (the expiration date), as the ledger's `closed_session` counts it."""
    out: dict[str, float] = defaultdict(float)
    for r in rows:
        out[r["exp"]] += r["net_" + arm]
    return dict(sorted(out.items()))


def summarize(rows: list[dict], arm: str) -> dict:
    rows = [r for r in rows if not r.get("refused")]
    if not rows:
        return {"n": 0}
    sessions = session_nets(rows, arm)
    w = list(sessions.values())
    net = sum(w)
    gap = sum(r["gap_" + arm] for r in rows)
    days = sorted({r["entry"] for r in rows} | {r["exp"] for r in rows})
    peak_risk = 0.0
    for d in days:
        risk = 0.0
        for r in rows:
            if r["entry"] <= d <= r["exp"]:
                fired = arm != "control" and arm in r and r[arm]["day"] <= d
                risk += r["risk_" + arm] if fired else r["risk_control"]
        peak_risk = max(peak_risk, risk)
    by_year: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        by_year[r["exp"][:4]].append(r["net_" + arm])
    return {
        "n": len(rows),
        "fires": sum(1 for r in rows if arm in r) if arm != "control" else None,
        "net": round(net),
        "per_trade": round(net / len(rows), 1),
        "wins": sum(1 for r in rows if r["net_" + arm] > 0),
        "sessions": len(w),
        "losing_sessions": sum(1 for x in w if x < 0),
        "worst_session": round(min(w)),
        "max_drawdown": round(drawdown(w)),
        "peak_open_risk": round(peak_risk),
        "fills": {
            "halfway": round(net - 0.25 * gap),
            "75pct": round(net - 0.5 * gap),
            "natural": round(net - 0.75 * gap),
        },
        "by_year": {
            y: {"n": len(v), "net": round(sum(v)), "per_trade": round(sum(v) / len(v), 1)}
            for y, v in sorted(by_year.items())
        },
    }


def print_summary(rows: list[dict], arms=ARMS) -> None:
    kept = [r for r in rows if not r.get("refused")]
    if not kept:
        print("no positions")
        return
    srcs = defaultdict(int)
    for r in kept:
        srcs[r["src"]] += 1
    print(
        f"{len(kept)} positions ({len(rows) - len(kept)} refused no_credit),"
        f" entries {kept[0]['entry']}..{kept[-1]['entry']}, bars {dict(srcs)}"
    )
    sums = {a: summarize(rows, a) for a in arms}
    for a, s in sums.items():
        fires = f", fires {s['fires']}" if s["fires"] is not None else ""
        print(
            f"  {a:<8} net ${s['net']:>9,} (${s['per_trade']:>6}/trade{fires}); wins {s['wins']}/{s['n']}; "
            f"max drawdown ${s['max_drawdown']:,}; worst session ${s['worst_session']:,}; "
            f"peak open risk ${s['peak_open_risk']:,}; halfway fills ${s['fills']['halfway']:,}"
        )
    years_ = sorted({y for s in sums.values() for y in s["by_year"]})
    print("  year  " + "".join(f"{a:>18}" for a in arms))
    for y in years_:
        cells = []
        for a in arms:
            v = sums[a]["by_year"].get(y)
            cells.append(f"{v['net']:>10,} ({v['n']:>3})" if v else " " * 18)
        print(f"  {y}  " + "".join(f"{c:>18}" for c in cells))


# ---- validation against the paper ledger -----------------------------------------------------------


def ledger_rows(path: str, start: str = VALIDATE_FROM) -> dict[str, dict[str, dict]]:
    """{entry_session: {arm: row}} for settled control/delta/bounce paper positions."""
    with _ro(Path(path)) as conn:
        conn.row_factory = sqlite3.Row
        cols = {r[1] for r in conn.execute("PRAGMA table_info(bwb_positions)")}
        missed = "addon_missed_at" if "addon_missed_at" in cols else "NULL AS addon_missed_at"
        q = (
            "SELECT entry_session, arm, entry_credit, entry_expected_move, addon_fired_at, addon_credit, "
            f"gross_pnl, fees, {missed} FROM bwb_positions WHERE status = 'closed' AND arm IN "
            "('control', 'delta', 'bounce') AND entry_session >= ?"
        )
        out: dict[str, dict[str, dict]] = defaultdict(dict)
        for r in conn.execute(q, (start,)):
            out[r["entry_session"]][r["arm"]] = dict(r)
    return dict(out)


def validate(
    ctx: Context, ledger: str | None = None, start: str = VALIDATE_FROM, verbose: bool = False
) -> dict:
    """Every settled paper entry re-run through the model and compared with the ledger. A position
    whose add-on was stamped missed (the loop was down) is left out of that arm only."""
    real = ledger_rows(ledger or bwb_db.default_db_path(), start)
    days = ctx.days
    per_arm = {
        a: {"n": 0, "real": 0.0, "model": 0.0, "agree": 0, "both": 0, "same_day": 0, "within_1": 0}
        for a in ARMS
    }
    credit_err, em_err, unmodelled = [], [], []
    lines = []
    for s in sorted(real):
        pos = simulate(ctx, s)
        if pos is None or pos.get("refused"):
            unmodelled.append(s)
            continue
        rows = real[s]
        anyrow = next(iter(rows.values()))
        credit_err.append(pos["credit"] - anyrow["entry_credit"])
        if anyrow["entry_expected_move"]:
            em_err.append(pos["em"] - anyrow["entry_expected_move"])
        line = [s, f"credit {anyrow['entry_credit']:.2f}/{pos['credit']:.2f}"]
        for arm in ARMS:
            r = rows.get(arm)
            if r is None or (arm in ADDON_ARMS and r["addon_missed_at"]):
                continue
            a = per_arm[arm]
            a["n"] += 1
            a["real"] += (r["gross_pnl"] or 0) - (r["fees"] or 0)
            a["model"] += pos["net_" + arm]
            if arm in ADDON_ARMS:
                rf = (r["addon_fired_at"] or "")[:10]
                mf = pos.get(arm, {}).get("day", "")
                a["agree"] += bool(rf) == bool(mf)
                if rf and mf:
                    a["both"] += 1
                    a["same_day"] += rf == mf
                    gap = abs(bisect.bisect_left(days, rf) - bisect.bisect_left(days, mf))
                    a["within_1"] += gap <= 1
                line.append(f"{arm} {rf or '-'}/{mf or '-'}")
        lines.append(" | ".join(line))
    checks = []
    report = {"start": start, "tolerance": TOLERANCE, "unmodelled": unmodelled, "arms": {}, "checks": checks}
    if credit_err:
        mae = statistics.mean(abs(x) for x in credit_err)
        report["credit"] = {
            "n": len(credit_err),
            "bias": round(statistics.mean(credit_err), 3),
            "mae": round(mae, 3),
        }
        checks.append(("credit_mae", round(mae, 3), mae <= TOLERANCE["credit_mae_max"]))
    if em_err:
        report["expected_move"] = {
            "bias": round(statistics.mean(em_err), 1),
            "mae": round(statistics.mean(abs(x) for x in em_err), 1),
        }
    for arm, a in per_arm.items():
        n = a["n"]
        entry = {"n": n, "real_net": round(a["real"]), "model_net": round(a["model"])}
        checks.append((f"{arm}_min_positions", n, n >= TOLERANCE["min_positions"]))
        if n:
            bias = (a["model"] - a["real"]) / n
            entry["per_trade_bias"] = round(bias, 1)
            checks.append(
                (f"{arm}_per_trade_bias", round(bias, 1), abs(bias) <= TOLERANCE["per_trade_bias_max"])
            )
            if arm in ADDON_ARMS:
                share = a["agree"] / n
                entry.update(
                    {
                        "fire_agreement": round(share, 3),
                        "fired_both": a["both"],
                        "same_day": a["same_day"],
                        "within_one_session": a["within_1"],
                    }
                )
                checks.append(
                    (f"{arm}_fire_agreement", round(share, 3), share >= TOLERANCE["fire_agreement_min"])
                )
        report["arms"][arm] = entry
    report["passed"] = all(ok for *_, ok in checks)
    if verbose:
        report["lines"] = lines
    return report


def print_validation(rep: dict) -> None:
    for line in rep.get("lines", []):
        print("  " + line)
    print(f"validation from {rep['start']}: {'PASS' if rep['passed'] else 'FAIL'}")
    if "credit" in rep:
        c = rep["credit"]
        print(f"  entry credit: n {c['n']}, bias {c['bias']:+.3f}, MAE {c['mae']:.3f}")
    if "expected_move" in rep:
        e = rep["expected_move"]
        print(f"  expected move: bias {e['bias']:+.1f}, MAE {e['mae']:.1f} points")
    for arm, a in rep["arms"].items():
        extra = ""
        if "fire_agreement" in a:
            extra = (
                f"; fired-or-not agreement {a['fire_agreement']:.0%}, fired in both {a['fired_both']}, "
                f"same day {a['same_day']}, within one session {a['within_one_session']}"
            )
        print(
            f"  {arm:<8} n {a['n']}: ledger ${a['real_net']:,} vs model ${a['model_net']:,}"
            f" (bias ${a.get('per_trade_bias', 0):+}/trade){extra}"
        )
    for name, value, ok in rep["checks"]:
        if not ok:
            print(f"  OUT OF TOLERANCE: {name} = {value}")
    if rep["unmodelled"]:
        print(f"  not modelled (no bar or no VIX9D yet): {', '.join(rep['unmodelled'])}")


def require_validation(ctx: Context, ledger: str | None = None) -> dict:
    """Validate, print the result, and stop the process if it does not pass."""
    rep = validate(ctx, ledger)
    print_validation(rep)
    if not rep["passed"]:
        print(
            "the model does not reproduce the paper ledger within its declared tolerance: refusing to report"
        )
        raise SystemExit(2)
    return rep


# ---- the proxy check -------------------------------------------------------------------------------


def bars_check(cboe_dir: str | None, start: str, end: str) -> dict:
    """The SPY proxy against real SPX bars over their overlap: bar error, and the backtest both ways."""
    spx = load_bars("spx", cboe_dir)
    spy = load_bars("spy", cboe_dir)
    both = sorted(d for d in spx if d in spy and start <= d <= end)
    err = {k: [] for k in ("open", "high", "low", "close")}
    for d in both:
        for i, k in enumerate(err):
            err[k].append((spy[d][i] / spx[d][i] - 1) * 1e4)
    out = {"sessions": len(both), "bar_error_bps": {}}
    for k, v in err.items():
        a = sorted(abs(x) for x in v)
        out["bar_error_bps"][k] = {
            "mean": round(statistics.mean(v), 1),
            "mae": round(statistics.mean(a), 1),
            "p95": round(a[int(0.95 * (len(a) - 1))], 1),
        }
    v9 = cboe("VIX9D", cboe_dir)
    runs = {}
    for name, bars in (("spx", spx), ("spy", spy)):
        runs[name] = backtest(Context(bars, v9), both[0], both[-1])
    by_entry = {name: {r["entry"]: r for r in rows if not r.get("refused")} for name, rows in runs.items()}
    common = sorted(set(by_entry["spx"]) & set(by_entry["spy"]))
    out["backtest"] = {"positions": len(common)}
    for arm in ARMS:
        a = sum(by_entry["spx"][d]["net_" + arm] for d in common)
        b = sum(by_entry["spy"][d]["net_" + arm] for d in common)
        cell = {"spx": round(a), "spy_proxy": round(b)}
        if arm in ADDON_ARMS:
            cell["fire_agreement"] = round(
                sum((arm in by_entry["spx"][d]) == (arm in by_entry["spy"][d]) for d in common) / len(common),
                3,
            )
        out["backtest"][arm] = cell
    return out


# ---- the smile fit ---------------------------------------------------------------------------------


def _ledger_legs(path: str) -> list[dict]:
    """One row per distinct leg quote the paper ledger recorded at entry or at an add-on fill."""
    q = """
    SELECT p.arm, p.entry_session, p.entry_time, p.entry_spot, p.expiration, p.entry_expected_move,
           p.addon_credit, l.leg_role, l.strike, l.entry_mid, l.entry_iv, l.created_at
    FROM bwb_positions p JOIN bwb_legs l ON l.position_id = p.position_id
    WHERE p.arm IN ('control', 'delta', 'bounce', 'flip') AND p.status != 'cancelled'
    ORDER BY p.entry_session, l.leg_role
    """
    seen, out = set(), []
    with _ro(Path(path)) as conn:
        conn.row_factory = sqlite3.Row
        for r in conn.execute(q):
            key = (r["entry_session"], r["leg_role"], r["strike"], (r["created_at"] or "")[:16])
            if not r["leg_role"].startswith("addon") and key in seen:
                continue
            seen.add(key)
            if r["entry_iv"] is not None:
                out.append(dict(r))
    return out


def fit(ledger: str | None, gex_db: str | None, cboe_dir: str | None) -> dict:
    import numpy as np
    from scipy.optimize import least_squares

    v9 = Series({d: v / 100 for d, v in cboe("VIX9D", cboe_dir).items()})
    gex = Path(gex_db) if gex_db else _home.data_dir("gex") / "gex_history.db"
    with _ro(gex) as g:
        trail = g.execute("SELECT ts, spot FROM gex_spot_history WHERE symbol = 'SPX' ORDER BY ts").fetchall()
    tts = [t for t, _ in trail]

    def spot_at(ts):
        i = bisect.bisect_left(tts, ts)
        j = min((j for j in (i - 1, i) if 0 <= j < len(trail)), key=lambda j: abs(tts[j] - ts))
        return trail[j][1] if abs(tts[j] - ts) < 600 else None

    def T_of(t0, exp):
        t1 = datetime.fromisoformat(exp + "T16:00:00" + t0.isoformat()[-6:])
        return max((t1 - t0).total_seconds(), 1800) / (365 * 86400)

    entry: dict[str, dict] = {}
    addon: dict[tuple, dict] = {}
    ems = {}
    for r in _ledger_legs(ledger or bwb_db.default_db_path()):
        if r["leg_role"].startswith("addon"):
            t0 = datetime.fromisoformat(r["created_at"])
            S = spot_at(t0.timestamp())
            if S is None:
                continue
            day = t0.date().isoformat()
            vol = v9.get(day) or v9.prior(day)
            leg = {
                "S": S,
                "K": r["strike"],
                "T": T_of(t0, r["expiration"]),
                "iv": r["entry_iv"],
                "mid": r["entry_mid"],
                "v9": vol,
            }
            addon.setdefault((r["arm"], r["entry_session"]), {})[r["leg_role"]] = leg
        else:
            t0 = datetime.fromisoformat(r["entry_time"])
            T = T_of(t0, r["expiration"])
            vol = v9.prior(r["entry_session"])
            leg = {
                "S": r["entry_spot"],
                "K": r["strike"],
                "T": T,
                "iv": r["entry_iv"],
                "mid": r["entry_mid"],
                "v9": vol,
            }
            entry.setdefault(r["entry_session"], {})[r["leg_role"]] = leg
            ems[r["entry_session"]] = (r["entry_spot"], T, vol, r["entry_expected_move"])
    addon = {k: v for k, v in addon.items() if "addon_short" in v and "addon_long" in v}
    entry = {k: v for k, v in entry.items() if {"body_short_1", "near_long", "far_long"} <= set(v)}

    def P(x):
        return dict(zip("abcde", x, strict=True))

    def parts(x):
        p = P(x)
        iv, ec, ac, em = [], [], [], []
        for d in entry.values():
            for leg in d.values():
                iv.append(sigma(p, leg["S"], leg["K"], leg["T"], leg["v9"]) - leg["iv"])
            b, n, f = d["body_short_1"], d["near_long"], d["far_long"]
            model = 2 * put(p, b["S"], b["K"], b["T"], b["v9"]) - put(p, n["S"], n["K"], n["T"], n["v9"])
            model -= put(p, f["S"], f["K"], f["T"], f["v9"])
            ec.append(model - (2 * b["mid"] - n["mid"] - f["mid"]))
        for d in addon.values():
            s, lg = d["addon_short"], d["addon_long"]
            for leg in (s, lg):
                iv.append(sigma(p, leg["S"], leg["K"], leg["T"], leg["v9"]) - leg["iv"])
            model = put(p, s["S"], s["K"], s["T"], s["v9"]) - put(p, lg["S"], lg["K"], lg["T"], lg["v9"])
            ac.append(model - (s["mid"] - lg["mid"]))
        for S, T, vol, e in ems.values():
            K = round(S / 5) * 5
            pa = put(p, S, K, T, vol)
            em.append(structures.expected_move(pa + S - K, pa) - e)
        return np.array(iv), np.array(ec), np.array(ac), np.array(em)

    def resid(x):
        iv, ec, ac, em = parts(x)
        # 1 vol point ~ 10 cents of credit ~ 2 points of expected move
        return np.concatenate([iv * 100, ec * 10, ac * 10, em * 0.5])

    x = least_squares(resid, [1.0, 0.5, 0.0, 0.0, 0.0]).x
    iv, ec, ac, em = parts(x)
    return {
        "params": P(float(v) for v in x),
        "entries": len(entry),
        "addon_fills": len(addon),
        "leg_iv": {
            "bias_pts": round(float(iv.mean()) * 100, 2),
            "mae_pts": round(float(abs(iv).mean()) * 100, 2),
        },
        "entry_credit": {"bias": round(float(ec.mean()), 3), "mae": round(float(abs(ec).mean()), 3)},
        "addon_credit": {"bias": round(float(ac.mean()), 3), "mae": round(float(abs(ac).mean()), 3)},
        "expected_move": {"bias": round(float(em.mean()), 2), "mae": round(float(abs(em).mean()), 2)},
    }


# ---- CLI -------------------------------------------------------------------------------------------


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--cboe-dir", help="a directory holding VIX9D.csv (and SPX.csv for the SPY proxy)")
    common.add_argument("--ledger", help="the paper ledger (default: bwb's paper_trades.db)")
    common.add_argument("--params", help="a smile-params JSON in place of FITTED_SMILE")
    common.add_argument("--bars", choices=("auto", "spx", "spy"), default="spx")

    r = sub.add_parser("run", parents=[common])
    r.add_argument("--start", default="2024-10-07")
    r.add_argument("--end", default="9999-12-31")
    r.add_argument("--scale", type=float, help="re-size each position's path to this index level")
    r.add_argument("--validate", action="store_true", help="refuse to report unless validation passes")
    r.add_argument("--out", help="write the position rows here as JSON")

    v = sub.add_parser("validate", parents=[common])
    v.add_argument("--verbose", action="store_true")

    b = sub.add_parser("bars-check", parents=[common])
    b.add_argument("--start", default="2023-09-01")
    b.add_argument("--end", default="9999-12-31")

    f = sub.add_parser("fit", parents=[common])
    f.add_argument("--gex-db")
    f.add_argument("--params-out")

    fe = sub.add_parser("fetch")
    fe.add_argument("dir")

    args = ap.parse_args(argv)
    if args.cmd == "fetch":
        fetch(args.dir)
        return 0
    if args.cmd == "fit":
        res = fit(args.ledger, args.gex_db, args.cboe_dir)
        print(json.dumps(res, indent=1))
        if args.params_out:
            Path(args.params_out).write_text(json.dumps(res["params"]), encoding="utf-8")
        return 0
    if args.cmd == "bars-check":
        print(json.dumps(bars_check(args.cboe_dir, args.start, args.end), indent=1))
        return 0
    ctx = context(args.bars, args.cboe_dir, args.params)
    if args.cmd == "validate":
        if args.bars != "spx":
            ctx = context("spx", args.cboe_dir, args.params)
        rep = validate(ctx, args.ledger, verbose=args.verbose)
        print_validation(rep)
        return 0 if rep["passed"] else 2
    if args.validate:
        vctx = ctx if args.bars == "spx" else context("spx", args.cboe_dir, args.params)
        require_validation(vctx, args.ledger)
    rows = backtest(ctx, args.start, args.end, args.scale)
    if args.out:
        Path(args.out).write_text(json.dumps(rows, indent=1, default=str), encoding="utf-8")
    print_summary(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
