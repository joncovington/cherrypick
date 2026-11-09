"""Item 65, the intraday premium map (research/edge-hypotheses-plan.md): at each time of day, the SPX
0DTE chain's implied probability that the index closes within +/- x% of its price at that time,
against how often it did.

    python packages/backtest/scripts/intraday_premium_map_item65.py [--start D] [--end D] [--json PATH]

Reads only `SPX-60s.db` (SPXW 0DTE at one minute, its spot trail and the SPX daily closes). Writes
under the backtest data dir (results/research/item65/), never into the repo: the inputs are licensed.

**The reading.** For each session, each time T in `TIMES` and each band x in `BANDS`:
- spot S is the trail's value at T; the band is [S(1 - x), S(1 + x)];
- the chain's digital D(K) = P(close > K) at each listed strike K comes from out-of-the-money mids:
  above spot, -(C(K+h) - C(K-h)) / 2h from the calls; below, 1 - (P(K+h) - P(K-h)) / 2h from the puts;
  h is the strike step. D at the band's edges is interpolated between listed strikes and clipped
  to [0, 1], and the implied probability is D(low) - D(high);
- realised is 1 when the session's close is inside the band.

The gap (realised - implied, in percentage points) is the measure. A positive gap means the close
lands inside the band more often than the chain priced: the market overprices the move from T to
the close. One session is one observation per (T, x).
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.core import home

from cherrypick.backtest import paths
from cherrypick.backtest import settings as _settings

ET = ZoneInfo("America/New_York")
SIDE = {0: "P", 1: "C"}
STORE = ("2022-05-16", "2026-11-06")
DESIGN_END = "2024-12-31"
TIMES = ("10:00", "10:30", "11:00", "11:30", "12:00", "12:30", "13:00", "13:30", "14:00", "14:30", "15:00",
         "15:30", "15:55")  # fmt: skip
BANDS = (0.001, 0.002, 0.003, 0.005)


def hhmm(ts: float) -> str:
    return datetime.fromtimestamp(ts, ET).strftime("%H:%M")


def two_sided(q) -> bool:
    return q is not None and q[0] is not None and q[1] is not None and q[1] >= q[0] >= 0 and q[1] > 0


def digitals(chain: dict, spot: float) -> list[tuple[float, float]]:
    """[(K, P(close > K))] at listed strikes with both neighbours priced, from out-of-the-money mids,
    sorted by strike, clipped to [0, 1] and made non-increasing."""
    ks = sorted({k for k, _ in chain})
    out = []
    for i in range(1, len(ks) - 1):
        lo, k, hi = ks[i - 1], ks[i], ks[i + 1]
        right = "C" if k >= spot else "P"
        a, b = chain.get((lo, right)), chain.get((hi, right))
        if not (two_sided(a) and two_sided(b)):
            continue
        slope = ((b[0] + b[1]) / 2 - (a[0] + a[1]) / 2) / (hi - lo)
        # dC/dK = -P(close > K); dP/dK = P(close < K).
        d = -slope if right == "C" else 1.0 - slope
        out.append((k, min(1.0, max(0.0, d))))
    # Monotone: P(close > K) cannot rise with K.
    for i in range(1, len(out)):
        if out[i][1] > out[i - 1][1]:
            out[i] = (out[i][0], out[i - 1][1])
    return out


def digital_at(ds: list[tuple[float, float]], x: float) -> float | None:
    """P(close > x) by linear interpolation; None outside the priced strikes."""
    for (k0, d0), (k1, d1) in zip(ds, ds[1:], strict=False):
        if k0 <= x <= k1:
            return d0 + (d1 - d0) * (x - k0) / (k1 - k0) if k1 > k0 else d0
    return None


def implied_inside(ds: list[tuple[float, float]], spot: float, band: float) -> float | None:
    lo, hi = digital_at(ds, spot * (1 - band)), digital_at(ds, spot * (1 + band))
    if lo is None or hi is None:
        return None
    return max(0.0, lo - hi)


def session(db: sqlite3.Connection, day: str, close: float) -> list[dict]:
    ids = {
        hhmm(ts): sid
        for sid, ts, exp in db.execute(
            "SELECT id, ts, expiration FROM gex_quote_samples WHERE symbol='SPX' AND trade_date=?", (day,)
        )
        if exp == day
    }
    spots = {
        hhmm(ts): s
        for ts, s in db.execute(
            "SELECT ts, spot FROM gex_spot_history WHERE symbol='SPX' AND trade_date=?", (day,)
        )
    }
    rows = []
    for t in TIMES:
        if t not in ids or t not in spots:
            continue
        chain = {
            (k, SIDE[side]): (b, a)
            for side, k, b, a in db.execute(
                "SELECT side, strike, bid, ask FROM gex_quotes WHERE sample_id = ?", (ids[t],)
            )
        }
        s = spots[t]
        ds = digitals(chain, s)
        for x in BANDS:
            imp = implied_inside(ds, s, x)
            if imp is None:
                continue
            rows.append({"day": day, "time": t, "band": x, "implied": imp,
                         "realised": 1.0 if abs(close - s) <= x * s else 0.0})  # fmt: skip
    return rows


def cell(rows: list[dict]) -> dict:
    gaps = [r["realised"] - r["implied"] for r in rows]
    n = len(gaps)
    sd = statistics.stdev(gaps) if n > 1 else None
    mean = statistics.fmean(gaps) if gaps else None
    return {
        "n": n,
        "implied_pct": round(100 * statistics.fmean(r["implied"] for r in rows), 1) if rows else None,
        "realised_pct": round(100 * statistics.fmean(r["realised"] for r in rows), 1) if rows else None,
        "gap_pts": round(100 * mean, 1) if mean is not None else None,
        "t": round(mean / (sd / math.sqrt(n)), 2) if sd else None,
    }


def table(rows: list[dict]) -> dict:
    g: dict = defaultdict(list)
    for r in rows:
        g[(r["time"], r["band"])].append(r)
    return {f"{t} ±{100 * x:g}%": cell(v) for (t, x), v in sorted(g.items())}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--start", default=STORE[0])
    ap.add_argument("--end", default=STORE[1])
    ap.add_argument("--json", type=Path)
    args = ap.parse_args(argv)
    db = sqlite3.connect(f"file:{paths.ensure_vendor_dir(_settings.load()) / 'SPX-60s.db'}?mode=ro", uri=True)
    closes = dict(db.execute("SELECT trade_date, close FROM daily_closes WHERE symbol='SPX'"))
    days = [
        d
        for (d,) in db.execute(
            "SELECT trade_date FROM vendor_sessions WHERE symbol='SPX' AND status='complete' "
            "AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
            (args.start, args.end),
        )
    ]
    rows = [r for d in days if d in closes for r in session(db, d, closes[d])]
    out = {
        "item": 65,
        "verdict": "measurement",
        "window": [args.start, args.end],
        "sessions": len(days),
        "all": table(rows),
        "design": table([r for r in rows if r["day"] <= DESIGN_END]),
        "holdout": table([r for r in rows if r["day"] > DESIGN_END]),
        "by_year": {
            y: table([r for r in rows if r["day"][:4] == y]) for y in sorted({r["day"][:4] for r in rows})
        },
        "licence": "derived from licensed vendor history: stays under ~/.cherrypick, never in the repo",
    }
    target = args.json or home.data_dir("backtest") / "results" / "research" / "item65" / (
        f"map-{args.start}-{args.end}.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps({"verdict": out["verdict"], "written": str(target), "at": datetime.now().isoformat()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
