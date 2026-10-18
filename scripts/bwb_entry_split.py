"""Split the modelled bwb backtest's entries by the market's state the day before: is there a filter?

    python scripts/bwb_history_backtest.py run --start 2011-01-03 --bars auto --out ROWS.json
    python scripts/bwb_entry_split.py ROWS.json [--cboe-dir DIR]

Hypothesis 6 in docs/edge-hypotheses-plan.md. Video 11 enters its put broken-wing butterflies on down
days (richer premium, strikes further from spot) and avoids all-time highs (thin premium). The base
structure has no edge from 2011 (docs/history-studies-2026-10.md), so a filter has to create one, not
polish one. This reads the backtest's own position rows (`--out`) and splits them by two things known
before the 10:00 entry: the S&P's previous-session return, and how far its previous close sat below
its running all-time high. Read-only.

**Declared pass test (2026-10-17, before any run).** The `down` subset (previous session at or below
-0.5%) must be net positive for `control` in total, and in at least two-thirds of the calendar years
holding 10 or more of its entries. Otherwise the filter is closed. The all-time-high split is shown as
context, not tested.

The S&P closes are Cboe's SPX.csv (the backtest's own `fetch`), so the split agrees with the index
the backtest prices.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bwb_history_backtest as _bt  # noqa: E402 -- the Cboe file lookup, shared not copied

ARMS = ("control", "delta", "bounce")
DOWN = -0.005
ATH_BANDS = ((0.0, 0.01), (0.01, 0.03), (0.03, 0.10), (0.10, 1.0))


def spx_closes(cboe_dir: str | None) -> tuple[list[str], list[float]]:
    """Cboe's SPX closes through the backtest's own reader, in date order."""
    by_date = _bt.cboe("SPX", cboe_dir)
    dates = sorted(by_date)
    return dates, [by_date[d] for d in dates]


def states(dates: list[str], closes: list[float]) -> dict[str, tuple[float, float]]:
    """{session: (previous session's return, previous close's distance below the running high)}."""
    out, peak = {}, 0.0
    for i in range(1, len(dates)):
        peak = max(peak, closes[i - 1])
        prev_ret = closes[i - 1] / closes[i - 2] - 1.0 if i >= 2 else 0.0
        out[dates[i]] = (prev_ret, 1.0 - closes[i - 1] / peak)
    return out


def state_for(entry: str, st: dict, dates: list[str]) -> tuple[float, float] | None:
    if entry in st:
        return st[entry]
    i = bisect.bisect_left(dates, entry)
    return st.get(dates[i]) if i < len(dates) else None


def tally(rows: list[dict]) -> dict:
    out = {"n": len(rows)}
    for a in ARMS:
        nets = [r[f"net_{a}"] for r in rows if r.get(f"net_{a}") is not None]
        out[a] = {"net": sum(nets), "per_trade": sum(nets) / len(nets) if nets else None}
    return out


def verdict(down_rows: list[dict]) -> dict:
    years: dict[str, list] = defaultdict(list)
    for r in down_rows:
        years[r["entry"][:4]].append(r["net_control"])
    counted = {y: sum(v) for y, v in years.items() if len(v) >= 10}
    pos = sum(1 for v in counted.values() if v > 0)
    total = sum(r["net_control"] for r in down_rows)
    checks = {
        "net_positive": total > 0,
        "two_thirds_of_years": bool(counted) and pos >= math.ceil(2 * len(counted) / 3),
    }
    return {
        "total": total,
        "years_counted": len(counted),
        "years_positive": pos,
        "checks": checks,
        "pass": all(checks.values()),
        "by_year": dict(sorted(counted.items())),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("rows", type=Path, help="the backtest's --out JSON")
    ap.add_argument("--cboe-dir")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args(argv)
    rows = [r for r in json.loads(args.rows.read_text(encoding="utf-8")) if r.get("net_control") is not None]
    dates, closes = spx_closes(args.cboe_dir)
    st = states(dates, closes)
    for r in rows:
        s = state_for(r["entry"], st, dates)
        r["prev_ret"], r["below_high"] = s if s else (None, None)
    rows = [r for r in rows if r["prev_ret"] is not None]
    down = [r for r in rows if r["prev_ret"] <= DOWN]
    groups = {
        "all": rows,
        "down (prev <= -0.5%)": down,
        "flat (-0.5%..+0.5%)": [r for r in rows if DOWN < r["prev_ret"] < -DOWN],
        "up (prev >= +0.5%)": [r for r in rows if r["prev_ret"] >= -DOWN],
    }
    for lo, hi in ATH_BANDS:
        groups[f"below high {lo:.0%}..{hi:.0%}"] = [r for r in rows if lo <= r["below_high"] < hi]
    rep = {"groups": {k: tally(v) for k, v in groups.items()}, "verdict": verdict(down)}
    print(f"\n{'group':<28}{'n':>6}" + "".join(f"{a + ' net':>16}{'/trade':>9}" for a in ARMS))
    for k, t in rep["groups"].items():
        cells = "".join(f"{t[a]['net']:>16,.0f}{(t[a]['per_trade'] or 0):>9.1f}" for a in ARMS)
        print(f"{k:<28}{t['n']:>6}{cells}")
    v = rep["verdict"]
    print("\ndown-day control net by year:", {y: round(x) for y, x in v["by_year"].items()})
    print("Declared pass test:", "PASS" if v["pass"] else "FAIL", json.dumps(v["checks"]))
    if args.json:
        args.json.write_text(json.dumps(rep, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
