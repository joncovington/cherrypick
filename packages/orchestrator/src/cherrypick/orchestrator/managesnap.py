"""The broker snapshot the daily position evaluation is judged on (`run.py manage`).

packages/manage is credential-free and network-free: it evaluates a snapshot and nothing else. This is
where the snapshot comes from -- the one broker-touching step, and it is three READS: the `positions`
verb's priced positions and balances, the broker's market metrics (last price, the next earnings date,
the ex-dividend date) for every underlying held, and each underlying's trade history. Nothing here
places, cancels or modifies an order. Account numbers arrive masked from `positions`, and the history
rows carry none.

The history is what lets manage see an ORDER CHAIN -- the opening order and every roll since -- rather
than the one average open price per leg the positions endpoint keeps. Without it a rolled trade's
basis is wrong (GLD on 2026-10-13: the open legs said $1,104 in, the chain $193, because two rolls had
taken in $640).

Metrics and history come from the first enabled module whose broker tool answers (meic's does). A
failure to get either is recorded in the snapshot, never fatal: the evaluation still runs every rule
that needs only prices, and says what was unavailable.

Version 3 (2026-10-18) adds what a portfolio reading needs (docs/portfolio-guidance-plan.md): each
underlying's beta and three-month correlation to SPY as the broker states them -- the beta the broker's
own beta-weighted delta uses, so the two can agree -- and the benchmark's metrics whether or not it is
held, since a beta-weighted delta is priced in its units. The account's kind (margin or cash, IRA or
not) rides on `positions`, from the broker's account record.

Version 4 adds each underlying's implied volatility index and 30-day historical volatility, kept in
the broker's own units and named for them (`iv_index` is a decimal, 0.196; `hv_30_day_pct` a
percentage, 10.45). SPY's are what a day's expected move is read against.

Version 5 adds the hedge chain: one expiry of XSP, quoted, for manage to size a beta-weighted-delta
hedge from. XSP because it is European and cash-settled (no early assignment, no shares delivered,
allowed in an IRA as a defined-risk spread) and a tenth of SPX, so a small account can size it. The
expiry is the standard monthly 30-60 days out, preferring 45-60 -- the suite's premium-selling cycle --
and the window is HEDGE_STRIKES strikes either side of spot, wide enough to reach a 15-delta strike.
Its metrics are quoted every day with the benchmark's.

Version 6 quotes VIX every day too: the buying-power band rises with it.
"""

from __future__ import annotations

import subprocess
import time
from datetime import date, timedelta
from typing import Any

from cherrypick.core import clock

from . import config as cfgmod
from . import doctor
from . import positions as positions_mod
from .util import first_json

SNAPSHOT_VERSION = 6
# What a beta-weighted delta is stated in. The broker's betas are against SPY, so this is not a free
# choice: a different benchmark would need betas the broker does not supply.
BENCHMARK = "SPY"
# What the buying-power band is scaled by.
VOLATILITY_INDEX = "VIX"
# The instrument a delta hedge is sized in, and the slice of its chain the snapshot carries.
HEDGE_ROOT = "XSP"
HEDGE_DTE = (30, 60)
HEDGE_PREFER_MIN_DTE = 45
# XSP lists 1-point strikes near the money; 60 a side reaches past the 15-delta strike at 45 days.
HEDGE_STRIKES = 60
# How far back a chain is rebuilt. A LEAPS-backed position can roll for a year; anything older than
# this is reported as an incomplete chain rather than guessed.
HISTORY_DAYS = 400


def _metric(entry: dict[str, Any]) -> dict[str, Any]:
    earnings = entry.get("earnings") if isinstance(entry.get("earnings"), dict) else {}
    return {
        "last": entry.get("last"),
        "earnings_date": earnings.get("expected_report_date"),
        "earnings_time": earnings.get("time_of_day"),
        "ex_dividend_date": entry.get("dividend_ex_date"),
        "dividend_rate_per_share": entry.get("dividend_rate_per_share"),
        "iv_rank": entry.get("implied_volatility_index_rank"),
        "beta": entry.get("beta"),
        "beta_updated_at": entry.get("beta_updated_at"),
        "corr_spy_3month": entry.get("corr_spy_3month"),
        "iv_index": entry.get("implied_volatility_index"),
        "hv_30_day_pct": entry.get("historical_volatility_30_day"),
    }


def _first_answer(
    cfg: dict[str, Any], prefer: str | None, argv: list[str], timeout: int = 35
) -> tuple[dict, str | None]:
    """The first enabled module whose broker tool answers `argv` ok, or the last error."""
    modules = cfgmod.enabled_modules(cfg)
    order = [m for m in ([prefer] if prefer else []) + ["meic", *modules] if m in modules]
    last_error = "no enabled module with a broker tool"
    for name in dict.fromkeys(order):
        mcfg = modules[name]
        try:
            run = doctor._run(
                cfgmod.module_root(mcfg, name), [*cfgmod.broker_tool(mcfg, name), *argv], timeout=timeout
            )
        except subprocess.TimeoutExpired:
            # A slow broker read is a missing piece of the snapshot, never the end of it: until
            # 2026-10-18 one timed-out metrics call took the whole evaluation down with it.
            last_error = f"{name}: {argv[0]} timed out after {timeout}s"
            continue
        payload = first_json(run.stdout)
        if payload.get("ok"):
            return payload, None
        last_error = f"{name}: {payload.get('error') or f'no answer to {argv[0]}'}"
    return {}, last_error


def market_metrics(
    cfg: dict[str, Any], symbols: list[str], prefer: str | None = None
) -> tuple[dict, str | None]:
    """{symbol: metrics} for `symbols`, and an error string when no broker tool could supply them."""
    if not symbols:
        return {}, None
    payload, error = _first_answer(cfg, prefer, ["get_market_overview", "--symbols", *symbols])
    out = {}
    for entry in payload.get("metrics") or []:
        if isinstance(entry, dict) and entry.get("symbol"):
            out[str(entry["symbol"]).upper()] = _metric(entry)
    return out, error


def transactions(
    cfg: dict[str, Any], symbols: list[str], prefer: str | None = None
) -> tuple[dict, str | None]:
    """{underlying: [trade history rows]} reaching back HISTORY_DAYS, so manage can rebuild each
    position's order chain (its opening order and every roll); an error string when unavailable."""
    if not symbols:
        return {}, None
    since = (date.today() - timedelta(days=HISTORY_DAYS)).isoformat()
    payload, error = _first_answer(
        cfg, prefer, ["get_transactions", "--underlyings", *symbols, "--since", since], timeout=180
    )
    return payload.get("transactions") or {}, error


def pick_hedge_expiry(listing: dict[str, list[dict]], session: date) -> str | None:
    """The standard monthly 30-60 days out, preferring 45-60 and then the nearest to 45; a weekly
    only when no monthly falls in the window."""
    lo, hi = HEDGE_DTE
    found = []
    for exp, rows in listing.items():
        try:
            dte = (date.fromisoformat(exp) - session).days
        except ValueError:
            continue
        if lo <= dte <= hi:
            monthly = any(
                str(r.get("expiration_type")) in ("Regular", "ExpirationType.REGULAR") for r in rows
            )
            found.append((not monthly, dte < HEDGE_PREFER_MIN_DTE, abs(dte - HEDGE_PREFER_MIN_DTE), exp))
    return min(found)[-1] if found else None


def _option_row(row: dict[str, Any]) -> dict[str, Any] | None:
    right = str(row.get("option_type") or "")
    right = (
        "C" if right.endswith(("C", "CALL", "Call")) else "P" if right.endswith(("P", "PUT", "Put")) else None
    )
    if right is None or row.get("strike_price") is None:
        return None
    return {
        "symbol": row.get("symbol"),
        "strike": float(row["strike_price"]),
        "right": right,
        "bid": row.get("bid"),
        "ask": row.get("ask"),
        "mid": row.get("mid"),
        # The broker's own greeks, kept for comparison; manage prices from the mid.
        "broker_delta": row.get("delta"),
        "broker_iv": row.get("iv"),
    }


def hedge_chain(cfg: dict[str, Any], spot: float | None, session: date, prefer: str | None) -> dict[str, Any]:
    """One quoted expiry of HEDGE_ROOT around `spot`, or a record of why there is none. Two reads:
    the expiry listing, then the slice with quotes and greeks."""
    out: dict[str, Any] = {"root": HEDGE_ROOT, "expiration": None, "options": [], "error": None}
    if not spot:
        out["error"] = f"no {HEDGE_ROOT} price to centre the chain on"
        return out
    listing, error = _first_answer(
        cfg, prefer, ["get_option_chain", "--symbol", HEDGE_ROOT, "--strike_count", "0"]
    )
    expiry = pick_hedge_expiry(listing.get("chain") or {}, session)
    if expiry is None:
        out["error"] = error or f"no {HEDGE_ROOT} expiry {HEDGE_DTE[0]}-{HEDGE_DTE[1]} days out"
        return out
    argv = [
        "get_option_chain",
        "--symbol",
        HEDGE_ROOT,
        "--expiration",
        expiry,
        "--include_quotes",
        "--include_greeks",
        "--strike_count",
        str(HEDGE_STRIKES),
        "--around_price",
        str(spot),
    ]
    payload, error = _first_answer(cfg, prefer, argv, timeout=60)
    rows = (payload.get("chain") or {}).get(expiry) or []
    first = rows[0] if rows else {}
    out.update(
        {
            "expiration": expiry,
            "dte": (date.fromisoformat(expiry) - session).days,
            "spot": spot,
            "settlement_type": first.get("settlement_type"),
            "exercise_style": first.get("exercise_style"),
            "quotes_complete": payload.get("quotes_complete"),
            "options": [o for o in (_option_row(r) for r in rows) if o is not None],
            "error": error,
        }
    )
    return out


def build(cfg: dict[str, Any]) -> dict[str, Any]:
    positions = positions_mod.run(cfg)
    symbols = sorted(
        {
            str(u.get("underlying")).upper()
            for account in positions.get("accounts") or []
            for u in account.get("underlyings") or []
            if u.get("underlying")
        }
    )
    ok, prefer = positions.get("ok"), positions.get("broker_module")
    # The benchmark, the hedge root and VIX are quoted whether or not they are held; their history is not
    # wanted unless they are.
    quoted = sorted({*symbols, BENCHMARK, HEDGE_ROOT, VOLATILITY_INDEX})
    metrics, error = market_metrics(cfg, quoted, prefer) if ok else ({}, None)
    history, history_error = transactions(cfg, symbols, prefer) if ok else ({}, None)
    session = clock.today_iso()
    hedge_spot = (metrics.get(HEDGE_ROOT) or {}).get("last")
    hedge = hedge_chain(cfg, hedge_spot, date.fromisoformat(session), prefer) if ok else None
    return {
        "snapshot_version": SNAPSHOT_VERSION,
        "session": session,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "benchmark": BENCHMARK,
        "positions": positions,
        "metrics": metrics,
        "metrics_error": error,
        "transactions": history,
        "transactions_error": history_error,
        "hedge_chain": hedge,
    }
