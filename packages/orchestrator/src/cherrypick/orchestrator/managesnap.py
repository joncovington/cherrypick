"""The broker snapshot the daily position evaluation is judged on (`run.py manage`).

packages/manage is credential-free and network-free: it evaluates a snapshot and nothing else. This is
where the snapshot comes from -- the one broker-touching step, and it is two READS: the `positions`
verb's priced positions and balances, and the broker's market metrics (last price, the next earnings
date, the ex-dividend date) for every underlying held. Nothing here places, cancels or modifies an
order. Account numbers arrive masked from `positions`.

Market metrics come from the first enabled module whose broker tool answers `get_market_overview`
(meic's does; the others do not). A failure to get them is recorded in the snapshot, never fatal:
the evaluation still runs every rule that needs only prices, and says the dates were unavailable.
"""

from __future__ import annotations

import time
from typing import Any

from cherrypick.core import clock

from . import config as cfgmod
from . import positions as positions_mod
from . import reconcile

SNAPSHOT_VERSION = 1


def _metric(entry: dict[str, Any]) -> dict[str, Any]:
    earnings = entry.get("earnings") if isinstance(entry.get("earnings"), dict) else {}
    return {
        "last": entry.get("last"),
        "earnings_date": earnings.get("expected_report_date"),
        "earnings_time": earnings.get("time_of_day"),
        "ex_dividend_date": entry.get("dividend_ex_date"),
        "dividend_rate_per_share": entry.get("dividend_rate_per_share"),
        "iv_rank": entry.get("implied_volatility_index_rank"),
    }


def market_metrics(
    cfg: dict[str, Any], symbols: list[str], prefer: str | None = None
) -> tuple[dict, str | None]:
    """{symbol: metrics} for `symbols`, and an error string when no broker tool could supply them."""
    if not symbols:
        return {}, None
    modules = cfgmod.enabled_modules(cfg)
    order = [m for m in ([prefer] if prefer else []) + ["meic", *modules] if m in modules]
    last_error = "no enabled module with a broker tool"
    for name in dict.fromkeys(order):
        mcfg = modules[name]
        payload = reconcile._tt(
            cfgmod.module_root(mcfg, name),
            "get_market_overview",
            "--symbols",
            *symbols,
            tool=cfgmod.broker_tool(mcfg, name),
        )
        if payload.get("ok"):
            out = {}
            for entry in payload.get("metrics") or []:
                if isinstance(entry, dict) and entry.get("symbol"):
                    out[str(entry["symbol"]).upper()] = _metric(entry)
            return out, None
        last_error = f"{name}: {payload.get('error') or 'no answer to get_market_overview'}"
    return {}, last_error


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
    metrics, error = (
        market_metrics(cfg, symbols, positions.get("broker_module")) if positions.get("ok") else ({}, None)
    )
    return {
        "snapshot_version": SNAPSHOT_VERSION,
        "session": clock.today_iso(),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "positions": positions,
        "metrics": metrics,
        "metrics_error": error,
    }
