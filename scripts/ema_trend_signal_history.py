"""Does the daily 8-vs-21 EMA state predict the next week's return? (The dynamic PMCC's signal.)

    python scripts/ema_trend_signal_history.py [--symbols SPY,QQQ,GLD,IWM,SLV,SMH] [--start 2011-01-03]

Hypothesis 5 in docs/edge-hypotheses-plan.md. Video 15's dynamic poor man's covered call picks the
weekly short call's delta from the daily 8 and 21 EMAs: out of the money in an uptrend, in the money
in a downtrend. Selling in the money in a downtrend is mostly being less long when the trend turns, so
the rule can only beat pmcc's fixed 0.70-delta short if the EMA state predicts the next week. This
measures that first, before any variant enters `scripts/pmcc_shield_replay.py`. Read-only.

**Data:** adjusted daily closes from the technicals history store (Dolt's bars), for SPY (standing in
for XSP), QQQ, GLD, IWM, SLV and SMH. **Samples:** one per week, at each Friday's close (or the last
session of the week), so samples do not overlap; the outcome is the return to the next week's last
close. **State:** 8-session EMA above the 21-session EMA is `up`, below is `down`.

**Declared pass test (2026-10-17, before any run).** For at least 4 of the 6 symbols, the mean
next-week return in `up` weeks minus that in `down` weeks must be positive with a t of at least 2.0
(Welch), and positive in both halves (before 2019, and from 2019). Otherwise the dynamic delta is not
added to the shield replay.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
import sys
from datetime import date
from pathlib import Path

from cherrypick.technicals import store as _store

HISTORY_DB = Path.home() / ".cherrypick" / "data" / "technicals" / "history.db"
SYMBOLS = ("SPY", "QQQ", "GLD", "IWM", "SLV", "SMH")
SPLIT = date(2019, 1, 1)


def ema(xs: list[float], n: int) -> list[float]:
    a, out, e = 2.0 / (n + 1), [], None
    for x in xs:
        e = x if e is None else a * x + (1 - a) * e
        out.append(e)
    return out


def weekly_samples(dates: list[date], closes: list[float]) -> list[tuple[date, str, float]]:
    """(week-end date, state, next week's return) at each week's last session, after a 21-session
    warm-up so the slow EMA means something."""
    e8, e21 = ema(closes, 8), ema(closes, 21)
    ends = [i for i in range(len(dates) - 1) if dates[i + 1].isocalendar()[:2] != dates[i].isocalendar()[:2]]
    out = []
    for a, b in zip(ends, ends[1:], strict=False):
        if a < 21 or e8[a] == e21[a]:
            continue
        out.append((dates[a], "up" if e8[a] > e21[a] else "down", closes[b] / closes[a] - 1.0))
    return out


def welch(a: list[float], b: list[float]) -> dict:
    if len(a) < 3 or len(b) < 3:
        return {"diff": None}
    va, vb = statistics.variance(a), statistics.variance(b)
    se = math.sqrt(va / len(a) + vb / len(b))
    diff = statistics.fmean(a) - statistics.fmean(b)
    return {"diff": diff, "t": diff / se if se > 0 else None, "n_up": len(a), "n_down": len(b)}


def symbol_report(samples: list[tuple[date, str, float]]) -> dict:
    def split(rows):
        return [r for _, s, r in rows if s == "up"], [r for _, s, r in rows if s == "down"]

    up, down = split(samples)
    early = welch(*split([x for x in samples if x[0] < SPLIT]))
    late = welch(*split([x for x in samples if x[0] >= SPLIT]))
    whole = welch(up, down)
    whole["hit_rate"] = (sum(1 for r in up if r > 0) + sum(1 for r in down if r < 0)) / max(
        1, len(up) + len(down)
    )
    ok = (
        whole.get("diff") is not None
        and whole["diff"] > 0
        and (whole.get("t") or 0) >= 2.0
        and (early.get("diff") or 0) > 0
        and (late.get("diff") or 0) > 0
    )
    return {"whole": whole, "early": early, "late": late, "pass": ok}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--symbols", default=",".join(SYMBOLS))
    ap.add_argument("--start", default="2011-01-03")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args(argv)
    conn = sqlite3.connect(f"file:{HISTORY_DB.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    start = date.fromisoformat(args.start)
    rep = {}
    for sym in args.symbols.split(","):
        bars = [b for b in _store.adjusted_bars(conn, sym) if b.close and date.fromisoformat(b.date) >= start]
        if len(bars) < 100:
            print(f"{sym}: too few bars", file=sys.stderr)
            continue
        rep[sym] = symbol_report(
            weekly_samples([date.fromisoformat(b.date) for b in bars], [b.close for b in bars])
        )
    conn.close()
    print(f"\n{'symbol':<8}{'up-down /wk':>12}{'t':>7}{'hit':>8}{'pre-2019':>11}{'2019+':>9}  pass")
    for sym, r in rep.items():
        w, e, lt = r["whole"], r["early"], r["late"]

        def pct(x):
            return "   -" if x is None else f"{x * 100:+.2f}%"

        print(
            f"{sym:<8}{pct(w['diff']):>12}{(w.get('t') or 0):>7.1f}{w['hit_rate'] * 100:>7.1f}%"
            f"{pct(e.get('diff')):>11}{pct(lt.get('diff')):>9}  {r['pass']}"
        )
    passed = sum(1 for r in rep.values() if r["pass"])
    print(f"\nDeclared pass test: {'PASS' if passed >= 4 else 'FAIL'} ({passed} of {len(rep)} symbols)")
    if args.json:
        args.json.write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
