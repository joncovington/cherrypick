"""Item 85: item 75's two passing arms re-judged on the three bars.

The declaration is research/edge-hypotheses-plan.md section 85; this script runs it. Results are
derived from licensed data and stay under the backtest results directory, never the repo.

A retest of item 75, said out loud: the same trades, read from item 75's stored result
(`study-all.json`, every trade with its legs, quantity, entry price and net), never replayed again.
Item 75 judged each arm's mean net per session; it never charged an arm for the capital it holds,
judged its tail against a year of its mean, or asked whether it earns more than its own delta. This
judges the two arms that passed, `iron-condor-1345` and `long-put-1030`, through the study kit's
`rule`:

- **capital:** each trade's net (item 75's verdict fill) over its capital, the structure's maximum
  loss at its entry price (`capital`), less the 3-month bill on it over the days held, above zero;
- **tail:** the 99th-percentile one-year drawdown of those returns under one year of their mean;
- **delta:** the return less the structure's entry delta financed on the same capital: the legs'
  deltas at the entry minute from `SPX-60s.db` (the store item 75 replayed), times SPX's move from
  the entry minute's spot to the session's last stored spot (every trade is held to the settlement),
  plus the bill on the capital.

One observation is one session (each arm trades at most once a session). Item 75 read every session
once, so the verdict is provisional by declaration. A trade whose entry delta or spots the store
lacks is skipped and counted.

- `study`: writes `{results}/results.json`, the kit's verdict block per arm.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.core import bs, progress, treasury

from cherrypick.backtest import paths
from cherrypick.backtest import settings as _settings
from cherrypick.backtest.study import checks as ck
from cherrypick.backtest.study import rule as kit_rule

#: The correction log: one entry per fix to this replay, oldest first (orchestrator/studyversion.py).
REPLAY_CORRECTIONS = ()

#: Two arms: at 100 sessions or more a t of 2 is a one-sided p under 0.024, below Holm's strictest
#: bar for two, so no Holm step could bind.
RULE = {
    "t_bar": 2.0,
    "min_n": 100,
    "tail": {"per_year": "sample", "resamples": 10_000, "seed": 85},
    "delta": True,
    "splits": [],
}

CHAIN = ({"step": "study", "kind": "script", "args": ["study"], "output": "{results}/results.json"},)

ARMS = ("iron-condor-1345", "long-put-1030")
ITEM75_RESULT = "study-all.json"
ET = ZoneInfo("America/New_York")
#: The store spells a put side 0 and a call side 1 (its put deltas are negative).
STORE_SIDE = {"P": 0, "C": 1}
RATE_STALE_DAYS = 10


def out_dir() -> Path:
    d = paths.results_dir() / "research" / "item85"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ------------------------------------------------------------------------------------------------
# Pure.


def capital(trade: dict) -> float:
    """The structure's maximum loss at its entry price, whole-position dollars. `entry_mid` is signed
    (a credit positive, a debit negative); at expiry each leg is worth side x intrinsic, so a contract's
    P&L at a settlement S is entry_mid + sum(side x intrinsic(S)). Its minimum over the strikes, zero
    and twice the highest strike is the worst case for any defined-risk structure."""
    legs = trade["legs"]
    strikes = sorted({float(leg["strike"]) for leg in legs})
    points = [0.0, *strikes, 2.0 * strikes[-1]]
    worst = min(
        float(trade["entry_mid"])
        + sum(int(leg["side"]) * bs.intrinsic(s, float(leg["strike"]), leg["right"]) for leg in legs)
        for s in points
    )
    return round(max(-worst, 0.0) * 100.0 * int(trade.get("quantity") or 1), 4)


def position_delta(trade: dict, deltas: dict[tuple[float, str], float]) -> float | None:
    """The position's delta in shares at entry: each leg's side x its delta x 100 x quantity, or None
    when a leg's delta is missing."""
    total = 0.0
    for leg in trade["legs"]:
        d = deltas.get((float(leg["strike"]), leg["right"]))
        if d is None:
            return None
        total += int(leg["side"]) * d * 100.0
    return total * int(trade.get("quantity") or 1)


def minute_et(ts: float) -> int:
    t = datetime.fromtimestamp(ts, ET)
    return t.hour * 60 + t.minute


def days_held(trade: dict) -> int:
    return max((date.fromisoformat(trade["exit_session"]) - date.fromisoformat(trade["session"])).days, 1)


def row(trade: dict, entry, rate: float) -> dict | None:
    """One trade as the kit's row, or None when the store lacks its entry delta or spots. `entry` is
    (spot at the entry minute, spot at the session's last sample, {(strike, right): delta})."""
    f0, f1, deltas = entry
    delta = position_delta(trade, deltas)
    cap = capital(trade)
    if delta is None or f0 is None or f1 is None or cap <= 0:
        return None
    days = days_held(trade)
    return {
        "date": trade["session"],
        "net": float(trade["net"]["verdict"]) / cap,
        "bp": 1.0,
        "days": days,
        "rate": rate,
        "financed": ck.financed_delta(delta, f0, f1, cap, rate, days) / cap,
        "capital": cap,
        "delta": delta,
    }


# ------------------------------------------------------------------------------------------------
# The stores.


class Store:
    """`SPX-60s.db`, read-only: the spot at a minute, a session's last spot, and the deltas of one
    expiry's quotes at a minute."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def _spots(self, session: str) -> list[tuple[int, float]]:
        return [
            (minute_et(ts), s)
            for ts, s in self.conn.execute(
                "SELECT ts, spot FROM gex_spot_history WHERE symbol = 'SPX' AND trade_date = ? ORDER BY ts",
                (session,),
            )
        ]

    def entry(self, trade: dict):
        session, minute = trade["session"], int(trade["entry_minute"])
        spots = self._spots(session)
        before = [s for m, s in spots if m <= minute]
        f0 = before[-1] if before else None
        f1 = self._spots(trade["exit_session"])
        f1 = f1[-1][1] if f1 else None
        sample = None
        for sid, ts in self.conn.execute(
            "SELECT id, ts FROM gex_quote_samples WHERE symbol = 'SPX' AND trade_date = ? AND expiration = ? "
            "ORDER BY ts",
            (session, trade["expiry"]),
        ):
            if minute_et(ts) <= minute:
                sample = sid
            else:
                break
        deltas: dict[tuple[float, str], float] = {}
        if sample is not None:
            for side, strike, d in self.conn.execute(
                "SELECT side, strike, delta FROM gex_quotes WHERE sample_id = ?", (sample,)
            ):
                if d is not None:
                    deltas[(float(strike), "P" if side == STORE_SIDE["P"] else "C")] = float(d)
        return f0, f1, deltas


def rate_reader():
    seen: dict[str, float] = {}

    def rate(session: str) -> float:
        if session not in seen:
            got = treasury.yield_before(session, "3m")
            if got is None or (date.fromisoformat(session) - got[0]).days > RATE_STALE_DAYS:
                raise SystemExit(f"no Treasury 3-month yield within {RATE_STALE_DAYS} days before {session}")
            seen[session] = got[1] / 100.0
        return seen[session]

    return rate


def study(
    trades_by_arm: dict[str, list[dict]], store: Store, rate, reporter: progress.Reporter | None = None
) -> dict:
    rows: dict[str, list[dict]] = {}
    skipped: dict[str, int] = {}
    if reporter is not None:
        reporter.split("trades", ["SPX" for a in ARMS for _ in trades_by_arm.get(a, [])], unit="trades")
    for arm in ARMS:
        out = []
        for t in trades_by_arm.get(arm, []):
            r = row(t, store.entry(t), rate(t["session"]))
            if reporter is not None:
                reporter.advance("trades", key="SPX", current=t["session"])
            if r is None:
                skipped[arm] = skipped.get(arm, 0) + 1
            else:
                out.append(r)
        rows[arm] = out
    block = kit_rule.evaluate(RULE, rows)
    rep = {
        a: {
            "trades": len(trades_by_arm.get(a, [])),
            "judged": len(rows[a]),
            "skipped_no_store_delta_or_spot": skipped.get(a, 0),
            "mean_capital": sum(r["capital"] for r in rows[a]) / len(rows[a]) if rows[a] else None,
            "mean_entry_delta": sum(r["delta"] for r in rows[a]) / len(rows[a]) if rows[a] else None,
        }
        for a in ARMS
    }
    return {**block, "reported": rep}


def cmd_study(args) -> int:
    f = paths.results_dir() / "research" / "item75" / ITEM75_RESULT
    if not f.exists():
        raise SystemExit(f"no item 75 result at {f}: item 75's trades are what this re-reads")
    doc75 = json.loads(f.read_text(encoding="utf-8"))
    trades = {a: doc75["arms"][a]["trades"] for a in ARMS}
    db = paths.vendor_dir(_settings.load()) / "SPX-60s.db"
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        with progress.Reporter("ensemble_bars_item85-study") as rep:
            block = study(trades, Store(conn), rate_reader(), reporter=rep)
    finally:
        conn.close()
    doc = {
        "licence": "derived from licensed vendor data: never commit or publish",
        "item": 85,
        "retest_of": 75,
        "provisional": True,
        **block,
        "corrections": list(REPLAY_CORRECTIONS),
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
