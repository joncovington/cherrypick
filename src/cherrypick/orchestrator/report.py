"""Unified cross-module paper P&L report (read-only).

Reads each enabled module's paper DB — files only, no broker, no network, no trading — and produces one
unified P&L summary across MEICAgent (`ic_trades`) and EarningsAgent (`trades`), broken down by module
and, within each module, by risk profile. A read-mostly reporting surface for the walk-away user: the
first slice of the reporting/alerting hub (later: the Part-14 status dashboard and drift/stall alerts).

Two paper-DB schemas are wired, dispatched by `paper.trade_schema` (same registry idea as
trade_notifier), each yielding a normalized closed-trade record `{profile, symbol, strategy, net_pnl}`:
  - "meic_ic"  : MEICAgent's `ic_trades`; closed = exit_time set; net = pnl - fees; tag = risk_profile.
  - "earnings" : EarningsAgent's `trades`; closed = closed_at set; net = pnl - entry_cost - exit_cost;
                 tag = profile.

The per-profile grouping uses cherrypick.core.profiles.compare_profiles (group closed trades by their
attribution tag, summarize each group) via the src/_core submodule — bootstrapped onto sys.path in
this package's __init__.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from cherrypick.core.profiles import compare_profiles

from . import config as cfgmod

# Untagged sentinels match each module's own schema convention (see cherrypick.core.profiles.attribution_tag).
_MEIC_UNTAGGED = "unassigned"
_EARNINGS_UNTAGGED = "default"


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# --------------------------------------------------------------------------- per-schema readers
def _session_from_epoch(closed_at) -> str:
    """Trading-session date (ISO) from an epoch-seconds close time; '' if unparseable."""
    try:
        return date.fromtimestamp(float(closed_at)).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _meic_closed(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT symbol, risk_profile, pnl, fees, exit_time FROM ic_trades WHERE exit_time IS NOT NULL"
    ).fetchall()
    return [
        {
            "profile": r["risk_profile"] or _MEIC_UNTAGGED,
            "symbol": r["symbol"],
            "strategy": None,
            "net_pnl": (r["pnl"] or 0.0) - (r["fees"] or 0.0),
            # Session date for calibration (distinct-days count); ISO date prefix of exit_time.
            "session": (r["exit_time"] or "")[:10],
        }
        for r in rows
    ]


def _earnings_closed(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT symbol, profile, strategy, pnl, entry_cost, exit_cost, closed_at "
        "FROM trades WHERE closed_at IS NOT NULL"
    ).fetchall()
    return [
        {
            "profile": r["profile"] or _EARNINGS_UNTAGGED,
            "symbol": r["symbol"],
            "strategy": r["strategy"],
            "net_pnl": (r["pnl"] or 0.0) - (r["entry_cost"] or 0.0) - (r["exit_cost"] or 0.0),
            "session": _session_from_epoch(r["closed_at"]),
        }
        for r in rows
    ]


_READERS = {"meic_ic": _meic_closed, "earnings": _earnings_closed}


# --------------------------------------------------------------------------- summarization
def _summarize(records: list[dict]) -> dict:
    """P&L stats over a set of normalized closed-trade records (net_pnl already cost-adjusted)."""
    pnls = [r["net_pnl"] for r in records]
    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    return {
        "trades": n,
        "net_pnl": round(sum(pnls), 2),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / n, 4) if n else None,
        "avg_pnl": round(sum(pnls) / n, 2) if n else None,
    }


# --------------------------------------------------------------------------- entrypoint
def run(cfg: dict | None = None) -> dict:
    """Unified paper P&L across all enabled modules. Read-only; never writes or trades."""
    cfg = cfg or cfgmod.load_config()
    modules_out: dict[str, dict] = {}
    all_records: list[dict] = []

    for name, mcfg in cfgmod.enabled_modules(cfg).items():
        paper = mcfg.get("paper", {})
        schema = paper.get("trade_schema", "meic_ic")
        reader = _READERS.get(schema)
        db_path = cfgmod.module_root(mcfg) / paper.get("paper_db", "data/paper_trades.db")

        if reader is None:
            modules_out[name] = {"ok": False, "reason": f"unknown schema {schema!r}"}
            continue
        if not db_path.exists():
            modules_out[name] = {"ok": False, "reason": "paper DB not found", "db": str(db_path)}
            continue

        conn = _connect_ro(db_path)
        try:
            records = reader(conn)
        except sqlite3.Error as exc:  # empty/uninitialized DB, missing table, etc. — never crash the report
            modules_out[name] = {"ok": False, "reason": f"read failed: {exc}"}
            continue
        finally:
            conn.close()

        all_records.extend(records)
        modules_out[name] = {
            "ok": True,
            "schema": schema,
            **_summarize(records),
            "by_profile": compare_profiles(records, tag_key="profile", summarize=_summarize),
        }

    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "modules": modules_out,
        "suite": _summarize(all_records),
    }
