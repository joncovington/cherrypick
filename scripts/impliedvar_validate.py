"""`cherrypick.core.impliedvar` against Cboe's VIX, on the local Dolt SPY option history.

    python scripts/impliedvar_validate.py --cboe-dir .tmp/cboe              # every Wednesday from 2019-02
    python scripts/impliedvar_validate.py --cboe-dir .tmp/cboe --json .tmp/iv.json

Read-only: point SELECTs against the local `dolt sql-server` (the `options` clone), one chain date at
a time, because `option_chain` leads its key with `date` and anything else walks the table (a
`SELECT DISTINCT date` ran past ten minutes on 2026-10-12). Needs VIX.csv (`--cboe-dir`, else the
copy `scripts/fetch_market_files.py` keeps).

**What it checks.** SPY's 30-day model-free implied vol, from the end-of-day bids and asks Dolt
holds, interpolated between the two expirations that bracket 30 days (`constant_maturity`), against
the published VIX close the same day. SPY is not SPX and Dolt's quotes are not Cboe's, so this is a
check of the arithmetic and of how much the thin historical strips cut off, not a reproduction.

**The strips are thin** (about 11 to 27 strikes an expiration). No Dolt wing runs dry, so every
reading is incomplete and a LOWER bound, worst on stress days when the tails are fattest.

**Tolerance**, declared from the first run (2026-10-09: corr 0.9956, MAE 0.51, 96% within 2 points,
337 of 345 dates computed) with room for new dates: the script prints the result and exits 2 when
any figure is outside `TOLERANCE`, so a caller cannot read a number from an arithmetic that has
drifted.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bwb_history_backtest as _bt  # noqa: E402  (the Cboe file reader)
from cherrypick.core import impliedvar as iv  # noqa: E402

TOLERANCE = {"corr_min": 0.99, "mae_max": 0.75, "within_2pt_min": 0.90, "computed_min": 0.90}
DOLT = {"host": "127.0.0.1", "port": 3306, "user": "root", "database": "options"}


def connect(host: str = DOLT["host"], port: int = DOLT["port"]):
    import mysql.connector

    return mysql.connector.connect(
        host=host, port=int(port), user=DOLT["user"], database=DOLT["database"], connection_timeout=15
    )


def chain(cur, day: date, symbol: str = "SPY") -> dict[date, list[dict]]:
    """{expiration: quote rows} for one chain date; rows without both bid and ask are dropped."""
    cur.execute(
        "SELECT expiration, strike, call_put, bid, ask FROM option_chain WHERE date = %s AND act_symbol = %s",
        (day, symbol),
    )
    out: dict[date, list[dict]] = defaultdict(list)
    for exp, strike, cp, bid, ask in cur.fetchall():
        if bid is None or ask is None:
            continue
        out[exp].append(
            {
                "strike": float(strike),
                "option_type": "call" if str(cp).lower().startswith("c") else "put",
                "bid": float(bid),
                "ask": float(ask),
            }
        )
    return dict(out)


def term(quotes: list[dict], day: date, exp: date, rate: float) -> dict:
    """One expiration's single-term reading, close to close (calendar days / 365)."""
    return iv.single_term(quotes, years=(exp - day).days / 365.0, rate=rate)


def thirty_day(by_exp: dict[date, list[dict]], day: date, rate: float) -> dict:
    exps = sorted(e for e in by_exp if (e - day).days >= 7)
    near = max((e for e in exps if (e - day).days <= 30), default=None)
    nxt = min((e for e in exps if (e - day).days > 30), default=None)
    if near is None or nxt is None:
        return {"ok": False, "reason": "no_bracket"}
    terms = [term(by_exp[e], day, e, rate) for e in (near, nxt)]
    cm = iv.constant_maturity(terms[0], terms[1], days=30)
    cm.update(
        {"near": near.isoformat(), "next": nxt.isoformat(), "strikes": [t.get("strikes") for t in terms]}
    )
    return cm


def trading_days(start: str, end: str) -> list[date]:
    """Sessions from the technicals store's SPY bars: the dates worth asking Dolt about."""
    bars = _bt._bars(_bt.technicals_db("history.db"), "SPY")
    bars.update(_bt._bars(_bt.technicals_db("eod.db"), "SPY"))
    return [date.fromisoformat(d) for d in sorted(bars) if start <= d <= end]


def run(cur, vix: dict[str, float], days: list[date], rate: float) -> list[dict]:
    results = []
    for d in days:
        if d.isoformat() not in vix:
            continue
        by_exp = chain(cur, d)
        if not by_exp:
            continue
        rec = {"date": d.isoformat(), "vix": vix[d.isoformat()]}
        cm = thirty_day(by_exp, d, rate)
        if cm.get("ok"):
            rec.update(
                {
                    "ok": True,
                    "iv30": round(cm["vol"], 2),
                    "complete": cm["complete"],
                    "diff": round(cm["vol"] - rec["vix"], 2),
                    "strikes": cm["strikes"],
                }
            )
        else:
            rec.update({"ok": False, "refused": cm.get("reason")})
        results.append(rec)
    return results


def summarize(results: list[dict]) -> dict:
    ok = [r for r in results if r.get("ok")]
    if len(ok) < 3:
        return {"dates": len(results), "computed": len(ok), "passed": False}
    diffs = [r["diff"] for r in ok]
    s = {
        "dates": len(results),
        "computed": len(ok),
        "refused": len(results) - len(ok),
        "complete_share": round(sum(r["complete"] for r in ok) / len(ok), 3),
        "mean_diff": round(statistics.mean(diffs), 2),
        "median_diff": round(statistics.median(diffs), 2),
        "mean_abs_diff": round(statistics.mean(abs(x) for x in diffs), 2),
        "within_1pt": round(sum(abs(x) <= 1 for x in diffs) / len(diffs), 3),
        "within_2pt": round(sum(abs(x) <= 2 for x in diffs) / len(diffs), 3),
        "corr": round(statistics.correlation([r["iv30"] for r in ok], [r["vix"] for r in ok]), 4),
        "worst": sorted(
            ({"date": r["date"], "diff": r["diff"], "vix": r["vix"]} for r in ok), key=lambda x: x["diff"]
        )[:5],
    }
    by_year = defaultdict(list)
    for r in ok:
        by_year[r["date"][:4]].append(r["diff"])
    s["by_year_mean_diff"] = {y: round(statistics.mean(v), 2) for y, v in sorted(by_year.items())}
    s["by_year_mean_abs"] = {
        y: round(statistics.mean(abs(x) for x in v), 2) for y, v in sorted(by_year.items())
    }
    s["checks"] = [
        ("corr", s["corr"], s["corr"] >= TOLERANCE["corr_min"]),
        ("mean_abs_diff", s["mean_abs_diff"], s["mean_abs_diff"] <= TOLERANCE["mae_max"]),
        ("within_2pt", s["within_2pt"], s["within_2pt"] >= TOLERANCE["within_2pt_min"]),
        (
            "computed_share",
            round(len(ok) / len(results), 3),
            len(ok) / len(results) >= TOLERANCE["computed_min"],
        ),
    ]
    s["passed"] = all(ok_ for *_, ok_ in s["checks"])
    return s


def require(summary: dict) -> None:
    for name, value, ok in summary.get("checks", []):
        if not ok:
            print(f"OUT OF TOLERANCE: {name} = {value} (tolerance {TOLERANCE})")
    if not summary.get("passed"):
        print(
            "core.impliedvar does not reproduce VIX on Dolt's history within its declared tolerance:"
            " refusing to report"
        )
        raise SystemExit(2)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cboe-dir")
    ap.add_argument("--start", default="2019-02-01")
    ap.add_argument("--end", default="9999-12-31")
    ap.add_argument("--weekday", type=int, default=2, help="0=Mon..4=Fri; the 2026-10-09 run used Wednesdays")
    ap.add_argument("--rate", type=float, default=0.0)
    ap.add_argument("--host", default=DOLT["host"])
    ap.add_argument("--port", type=int, default=DOLT["port"])
    ap.add_argument("--json")
    args = ap.parse_args(argv)

    vix = _bt.cboe("VIX", args.cboe_dir)
    days = [d for d in trading_days(args.start, args.end) if d.weekday() == args.weekday]
    conn = connect(args.host, args.port)
    try:
        results = run(conn.cursor(), vix, days, args.rate)
    finally:
        conn.close()
    summary = summarize(results)
    summary["rate"] = args.rate
    if args.json:
        Path(args.json).write_text(
            json.dumps({"summary": summary, "results": results}, indent=1), encoding="utf-8"
        )
    require(summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "checks"}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
