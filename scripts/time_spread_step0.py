"""Step 0 of docs/time-spread-screen-plan.md: did the retired paper calendars lose at low coverage?

    python scripts/time_spread_step0.py [--lookback 12] [--json FILE]

Every `strat_test:atm_calendar` and `strat_test:double_calendar` row in the earnings paper ledger,
scored at entry by `cherrypick.earnings.time_spread` and set against the net P&L it recorded. The
declaration is in the plan, committed before this first ran; this docstring only says how.

**Inputs, all recorded at entry.** The Dolt chains list few expirations a day and miss most of these
trades' own, so the chain is not re-read:

- front ATM IV is the short legs' live IV (`entry_iv`); back ATM IV is front / (1 - term_structure),
  the row's own `entry_context` value ((back - front) / back, scanner's convention);
- spot is the entry tick's mark (`position_marks`), else the day's entry review;
- strikes and expiries are parsed from `legs_json`; days are calendar days from the entry's ET date;
- leg prices are Black-Scholes at those IVs, then the long legs are moved together so the net equals
  the recorded debit (the size of that move is printed as `fit`: a large one means the IVs and the
  debit disagree);
- costs are the row's own entry and exit costs, per unit of the structure.

A double calendar's OTM legs take the ATM IVs (a flat smile). Rows closed within ten minutes of
opening are excluded: the entry-tick bug closed them before the report, so they saw no event.

**Past gaps** are the last `--lookback` reactions before the entry date, signed, pre-report close to
reaction close, from the Dolt calendar and bars via the scanner's own helpers (blank timing is
skipped, as the winrate check does).

Read-only. Nothing is written except the optional JSON.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import statistics
from datetime import date, datetime
from pathlib import Path

from cherrypick.core.clock import ET
from cherrypick.earnings import scanner
from cherrypick.earnings import time_spread as ts

LEDGER = Path.home() / ".cherrypick" / "data" / "earnings" / "paper_trades.db"
ARMS = ("strat_test:atm_calendar", "strat_test:double_calendar")
INSTANT_SECONDS = 600
OCC = re.compile(r"^(?P<root>.{6})(?P<ymd>\d{6})(?P<cp>[CP])(?P<strike>\d{8})$")


def parse_occ(symbol: str) -> tuple[date, str, float]:
    m = OCC.match(symbol)
    if not m:
        raise ValueError(f"not an OCC symbol: {symbol!r}")
    return datetime.strptime(m["ymd"], "%y%m%d").date(), m["cp"], int(m["strike"]) / 1000


def past_gaps(symbol: str, before: date, lookback: int, config: dict) -> list[float]:
    """Signed reaction gaps, most recent first, strictly before `before`."""
    gaps: list[float] = []
    for row in scanner.fetch_historical_earnings_dates(symbol, before.isoformat(), lookback + 6, config):
        if len(gaps) >= lookback:
            break
        closes = scanner.pre_and_reaction_closes(symbol, row["date"], row["timing"], config)
        if closes is None:
            continue
        pre, reaction = (float(c["close"]) for c in closes)
        if pre > 0:
            gaps.append(reaction / pre - 1.0)
    return gaps


def entry_spot(conn: sqlite3.Connection, order_id: str, symbol: str, entry: date) -> float | None:
    row = conn.execute(
        "SELECT spot FROM position_marks WHERE order_id = ? AND spot IS NOT NULL ORDER BY marked_at LIMIT 1",
        (order_id,),
    ).fetchone()
    if row:
        return float(row[0])
    row = conn.execute(
        "SELECT price FROM entry_reviews WHERE symbol = ? AND scan_date = ? AND price IS NOT NULL LIMIT 1",
        (symbol, entry.isoformat()),
    ).fetchone()
    return float(row[0]) if row else None


def score_row(row: sqlite3.Row, conn: sqlite3.Connection, lookback: int, config: dict) -> dict:
    opened = datetime.fromtimestamp(row["opened_at"], ET)
    entry = opened.date()
    net = row["pnl"] - (row["entry_cost"] or 0) - (row["exit_cost"] or 0) - (row["settlement_fees"] or 0)
    out = {
        "order_id": row["order_id"],
        "strategy": row["strategy"],
        "symbol": row["symbol"],
        "entry": entry.isoformat(),
        "exit_reason": row["exit_reason"],
        "net": round(net, 2),
    }
    ctx = json.loads(row["entry_context"] or "{}")
    front_iv, slope = row["entry_iv"], ctx.get("term_structure")
    debit = -(row["entry_credit"] or 0.0)
    spot = entry_spot(conn, row["order_id"], row["symbol"], entry)
    if not front_iv or slope is None or slope >= 1 or debit <= 0 or not spot:
        return {**out, "ok": False, "reason": "missing_entry_inputs"}
    back_iv = front_iv / (1.0 - slope)

    parsed = []
    for leg in json.loads(row["legs_json"]):
        expiry, cp, strike = parse_occ(leg["symbol"])
        sign = 1 if leg["action"].startswith("Buy") else -1
        parsed.append((sign, cp, strike, (expiry - entry).days))
    longs = [p for p in parsed if p[0] > 0]
    shorts = [p for p in parsed if p[0] < 0]
    if not longs or not shorts:
        return {**out, "ok": False, "reason": "not_a_time_spread"}
    front_days = max(p[3] for p in shorts)
    back_days = min(p[3] for p in longs)
    if front_days <= 0 or back_days <= front_days:
        return {**out, "ok": False, "reason": "bad_expiries"}

    legs = []
    for sign, cp, strike, days in parsed:
        vol = back_iv if sign > 0 else front_iv
        price = ts.bs_price(cp, spot, strike, days / ts.YEAR_DAYS, vol)
        legs.append(ts.Leg(sign, cp, strike, days, price, vol))
    model_net = sum(leg.qty * leg.price for leg in legs)
    shift = (debit - model_net) / len(longs)
    legs = [
        ts.Leg(lg.qty, lg.option_type, lg.strike, lg.days, lg.price + (shift if lg.qty > 0 else 0.0), lg.iv)
        for lg in legs
    ]

    units = max(round((row["capital_at_risk"] or debit * 100) / (debit * 100)), 1)
    costs = ((row["entry_cost"] or 0) + (row["exit_cost"] or 0)) / units
    gaps = past_gaps(row["symbol"], entry, lookback, config)
    scores = ts.screen_scores(
        legs,
        spot,
        front_atm_iv=front_iv,
        front_days=front_days,
        back_atm_iv=back_iv,
        back_days=back_days,
        past_gaps=gaps,
        costs=costs,
    )
    out.update(
        spot=spot,
        front_iv=round(front_iv, 4),
        back_iv=round(back_iv, 4),
        front_days=front_days,
        back_days=back_days,
        debit=round(debit, 2),
        fit=round(shift / debit, 3),
        gaps=len(gaps),
    )
    if not scores["ok"]:
        return {**out, "ok": False, "reason": scores["reason"]}
    eg = scores["endurable"]
    return {
        **out,
        "ok": True,
        "coverage": scores["coverage"]["coverage"],
        "cushion": scores["cushion"],
        "richness": scores["richness"],
        "event_move": scores["split"]["event_move"],
        "endurable_down": eg.get("down"),
        "endurable_up": eg.get("up"),
        "loses_unchanged": not eg["ok"],
        "clamped_legs": scores["clamped_legs"],
    }


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None

    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(v):
            j = i
            while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    try:
        return statistics.correlation(rx, ry)
    except statistics.StatisticsError:
        return None


def summarize(rows: list[dict], key: str) -> dict:
    scored = [r for r in rows if r.get("ok") and r.get(key) is not None]
    if len(scored) < 4:
        return {"n": len(scored)}
    scored.sort(key=lambda r: r[key])
    half = len(scored) // 2
    groups = {"low": scored[:half], "high": scored[half:]}
    third = len(scored) // 3
    groups.update(t1=scored[:third], t2=scored[third : len(scored) - third], t3=scored[len(scored) - third :])

    def stats(g):
        nets = [r["net"] for r in g]
        return {
            "n": len(g),
            f"{key}_range": [round(g[0][key], 3), round(g[-1][key], 3)],
            "mean_net": round(statistics.mean(nets), 2),
            "total_net": round(sum(nets), 2),
            "won": round(sum(1 for x in nets if x > 0) / len(nets), 3),
        }

    return {
        "n": len(scored),
        "spearman": spearman([r[key] for r in scored], [r["net"] for r in scored]),
        **{name: stats(g) for name, g in groups.items()},
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lookback", type=int, default=12)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{LEDGER.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    marks = ",".join("?" * len(ARMS))
    rows = conn.execute(
        f"SELECT * FROM trades WHERE arm IN ({marks}) AND closed_at IS NOT NULL ORDER BY opened_at", ARMS
    ).fetchall()
    instant = [r for r in rows if r["closed_at"] - r["opened_at"] < INSTANT_SECONDS]
    kept = [r for r in rows if r["closed_at"] - r["opened_at"] >= INSTANT_SECONDS]
    config: dict = {}
    scored = [score_row(r, conn, args.lookback, config) for r in kept]

    refused: dict[str, int] = {}
    for r in scored:
        if not r["ok"]:
            refused[r["reason"]] = refused.get(r["reason"], 0) + 1
    result = {
        "rows": len(rows),
        "excluded_instant": len(instant),
        "kept": len(kept),
        "scored": sum(1 for r in scored if r["ok"]),
        "refused": refused,
        "refused_net": round(sum(r["net"] for r in scored if not r["ok"]), 2),
        "by": {k: summarize(scored, k) for k in ("coverage", "cushion", "richness")},
        "by_strategy": {
            s: summarize([r for r in scored if r["strategy"] == s], "coverage")
            for s in ("atm_calendar", "double_calendar")
        },
        "loses_unchanged": sum(1 for r in scored if r.get("loses_unchanged")),
        "clamped_rows": sum(1 for r in scored if r.get("clamped_legs")),
        "fit_abs_median": statistics.median(abs(r["fit"]) for r in scored if "fit" in r) if scored else None,
        "rows_detail": scored,
    }
    print(json.dumps({k: v for k, v in result.items() if k != "rows_detail"}, indent=2))
    if args.json:
        args.json.write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
