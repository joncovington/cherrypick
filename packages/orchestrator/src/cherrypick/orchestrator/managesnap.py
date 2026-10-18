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
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any

from cherrypick.core import clock

from . import config as cfgmod
from . import doctor
from . import positions as positions_mod
from .util import first_json

SNAPSHOT_VERSION = 4
# What a beta-weighted delta is stated in. The broker's betas are against SPY, so this is not a free
# choice: a different benchmark would need betas the broker does not supply.
BENCHMARK = "SPY"
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
        run = doctor._run(
            cfgmod.module_root(mcfg, name), [*cfgmod.broker_tool(mcfg, name), *argv], timeout=timeout
        )
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
    # The benchmark is quoted whether or not it is held; its history is not wanted unless it is.
    quoted = sorted({*symbols, BENCHMARK})
    metrics, error = market_metrics(cfg, quoted, prefer) if ok else ({}, None)
    history, history_error = transactions(cfg, symbols, prefer) if ok else ({}, None)
    return {
        "snapshot_version": SNAPSHOT_VERSION,
        "session": clock.today_iso(),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "benchmark": BENCHMARK,
        "positions": positions,
        "metrics": metrics,
        "metrics_error": error,
        "transactions": history,
        "transactions_error": history_error,
    }
