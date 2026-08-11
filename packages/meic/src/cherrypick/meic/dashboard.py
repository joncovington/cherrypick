"""MEICAgent Dashboard — local HTTP server serving a trading dashboard."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import threading
import urllib.parse
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

from cherrypick.core import viz

from cherrypick.meic import analytics as _an
from cherrypick.meic import paths as _paths
from cherrypick.meic import stop_policies as _sp

# ── Timezone helpers ─────────────────────────────────────────────────────────

try:  # stdlib zoneinfo first (tzdata supplies the db on Windows); pytz only as fallback
    from zoneinfo import ZoneInfo

    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - only where zoneinfo has no tz database
    import pytz

    _ET = pytz.timezone("America/New_York")


def _today() -> str:
    return datetime.now(_ET).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(_ET).isoformat()


def _week_start() -> str:
    now = datetime.now(_ET)
    return (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")


def _month_start() -> str:
    return datetime.now(_ET).strftime("%Y-%m-01")


def _year_start() -> str:
    return datetime.now(_ET).strftime("%Y-01-01")


# ── DB helpers ────────────────────────────────────────────────────────────────

_DB_PATH = str(_paths.live_db_path())
_PAPER_DB_PATH = str(_paths.paper_db_path())
_CACHE_DB_PATH = str(_paths.stream_cache_path())
# "live" (default, meic_trades.db) or "paper" (paper_trades.db) in the data home — set from --mode
# in main(). Drives the PAPER MODE banner; _DB_PATH itself is the only thing that changes
# which data actually gets served. _CACHE_DB_PATH (the streamer cache) is never mode-dependent
# — paper trading marks positions from the same real streamer quotes live trading uses.
_MODE = "live"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _one(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> dict | None:
    r = conn.execute(sql, params).fetchone()
    return dict(r) if r else None


# ── Stats helpers ─────────────────────────────────────────────────────────────


def _wl_ratio(wins: int, losses: int) -> float | None:
    total = (wins or 0) + (losses or 0)
    return round((wins or 0) / total * 100, 1) if total > 0 else None


def _fetch_spread_legs(conn: sqlite3.Connection, ic_order_ids: list[str]) -> dict[str, dict[str, dict]]:
    """Return {ic_order_id: {'put': leg_row, 'call': leg_row}} for the given IC ids.
    Sides with no recorded leg (legacy trades, or a side still open) are simply absent."""
    if not ic_order_ids:
        return {}
    placeholders = ", ".join(["?"] * len(ic_order_ids))
    rows = _rows(conn, f"SELECT * FROM ic_spread_legs WHERE ic_order_id IN ({placeholders})", ic_order_ids)
    legs: dict[str, dict[str, dict]] = {}
    for r in rows:
        legs.setdefault(r["ic_order_id"], {})[r["side"]] = r
    return legs


def _leg_outcome(leg_status: str | None, leg_pnl: float | None) -> tuple[int, int]:
    """Return (win, loss) for a single resolved spread leg; (0, 0) if still open/unresolved."""
    status = (leg_status or "").lower()
    if status == "expired":
        return (1, 0)
    if status in ("force_closed", "closed_profit_target"):
        return (1, 0) if (leg_pnl is None or leg_pnl >= 0) else (0, 1)
    if status == "stopped":
        return (0, 1)
    return (0, 0)


def _spread_wins_losses(
    trade_status: str, trade_pnl: float | None, put_leg: dict | None, call_leg: dict | None
) -> tuple[int, int]:
    """Return (spread_wins, spread_losses) for one IC, counting each leg separately.
    Prefers real per-leg records. A side with no leg row is either (a) part of a
    legacy trade recorded before per-leg tracking existed — both sides then share
    the whole-trade guess — or (b) a side that closed together with its sibling in
    the same event (expired/force-closed together) and so was never given its own
    row — it inherits that shared outcome. A side still genuinely open (trade status
    'partial'/'open') is left uncounted until it resolves."""
    status = (trade_status or "").lower()

    if put_leg is None and call_leg is None:
        if status == "expired":
            return (2, 0)
        if status in ("force_closed", "closed_profit_target"):
            return (2, 0) if (trade_pnl or 0) >= 0 else (0, 2)
        if status in ("stopped", "partial"):
            return (0, 2)
        return (0, 0)

    def side_outcome(leg: dict | None) -> tuple[int, int]:
        if leg is not None:
            return _leg_outcome(leg.get("status"), leg.get("pnl"))
        if status == "expired":
            return (1, 0)
        if status in ("force_closed", "closed_profit_target"):
            return (1, 0) if (trade_pnl or 0) >= 0 else (0, 1)
        return (0, 0)

    pw, pl_ = side_outcome(put_leg)
    cw, cl_ = side_outcome(call_leg)
    return (pw + cw, pl_ + cl_)


def _stats_for_period(
    conn: sqlite3.Connection,
    start: str | None = None,
    end: str | None = None,
    symbol: str | None = None,
    profile: str | None = None,
) -> dict:
    """Compute stats for a date range, querying ic_trades directly for accuracy.
    start/end are inclusive YYYY-MM-DD strings; omit to mean unbounded. symbol filters to
    one traded symbol; omit (or "ALL") for the account-wide total across every symbol —
    this is what the global risk caps (max_concurrent_ics, max_entries_per_day) are checked
    against, so "ALL" is the economically meaningful default, not just a UI convenience.
    profile filters to one risk_profile (paper-trading DB only, behind a column-exists check,
    so it's a no-op on the live DB); omit (or "ALL") for the profile-blended total."""
    where = ["status NOT IN ('cancelled', 'pending', 'partial_entry')"]
    params: list = []
    if start:
        where.append("trade_date >= ?")
        params.append(start)
    if end:
        where.append("trade_date <= ?")
        params.append(end)
    if symbol and symbol.upper() != "ALL":
        where.append("symbol = ?")
        params.append(symbol.upper())
    if profile and profile.upper() != "ALL" and _has_column(conn, "ic_trades", "risk_profile"):
        where.append("risk_profile = ?")
        params.append(profile)
    rows = _rows(
        conn, f"SELECT ic_order_id, pnl, fees, status FROM ic_trades WHERE {' AND '.join(where)}", params
    )
    net_pnl = 0.0
    total_trades = 0
    wins = 0
    losses = 0
    # One win definition module-wide (matches db._range_stats_for_rows and the orchestrator's
    # calibrate reading): a resolved trade whose net P&L (pnl - fees) is positive. The per-side
    # spread lens (_spread_wins_losses) is a per-row display, never a headline win rate.
    for r in rows:
        net_pnl += float(r.get("pnl") or 0)
        total_trades += 1
        if r.get("pnl") is not None:
            if float(r.get("pnl") or 0) - float(r.get("fees") or 0) > 0:
                wins += 1
            else:
                losses += 1
    result = {
        "net_pnl": round(net_pnl, 2),
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
    }
    result["wl_ratio"] = _wl_ratio(wins, losses)
    return result


# ── Multi-timeframe performance series ─────────────────────────────────────────
# Virtual bankroll each profile's (and the live series') equity/drawdown curve is
# anchored on — matches the paper-trading plan's $100k-per-profile convention so
# figures read identically here and in the weekly paper report.
_BANKROLL_BASE = 100000

# The forced-sampling study arms (config.risk.json), in the fixed display order the study chart
# draws them. Replaced the ladder + GEX-pair design 2026-08-07: `open` runs every study gate off
# with full path recording, so it is a superset every other arm (including the retired GEX pair)
# derives from read-side — see analytics.py and stop_policies.py. `control` is the deployed
# reference book (validates the derivation); `width-5`/`width-10` are the one genuinely
# non-derivable structural variant (wing width), paired against each other on the same ticks.
STUDY_ARMS = ["control", "open", "width-5", "width-10"]


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def _period_bucket_key(granularity: str, trade_date: str) -> str:
    """Bucket key for one trade_date: daily=itself, weekly=Monday of that ISO week
    (YYYY-MM-DD), monthly=YYYY-MM. Computed in Python rather than SQL strftime so week
    boundaries are unambiguous (SQLite's %W starts Sunday; _week_start() below is Monday)."""
    if granularity == "monthly":
        return trade_date[:7]
    if granularity == "weekly":
        d = datetime.strptime(trade_date, "%Y-%m-%d")
        monday = d - timedelta(days=d.weekday())
        return monday.strftime("%Y-%m-%d")
    return trade_date


def _pnl_series(
    conn: sqlite3.Connection, granularity: str, symbol: str | None = None, profile: str | None = None
) -> list[dict]:
    """Time-bucketed net P&L / win-rate / profit-factor series for the Performance view.

    Reuses _stats_for_period's exact WHERE clause and net_pnl convention (sum of the `pnl`
    column, not fee-subtracted — matches how the existing stats grid already computes
    net_pnl) so a 'daily' series summed over a range always equals _stats_for_period's
    net_pnl for that same range — the API's consistency guarantee, exercised in tests.

    profile filters to one risk_profile (paper-trading DB only) behind a column-exists
    check, so this is a no-op on the live DB until that column exists there too — the
    "profile-ready" seam the paper-trading plan's risk_profile column plugs into.
    """
    where = ["status NOT IN ('cancelled', 'pending', 'partial_entry')"]
    params: list = []
    if symbol and symbol.upper() != "ALL":
        where.append("symbol = ?")
        params.append(symbol.upper())
    if profile and _has_column(conn, "ic_trades", "risk_profile"):
        where.append("risk_profile = ?")
        params.append(profile)
    rows = _rows(
        conn,
        f"SELECT ic_order_id, trade_date, pnl, fees, net_credit, status FROM ic_trades "
        f"WHERE {' AND '.join(where)} ORDER BY trade_date",
        params,
    )

    buckets: dict[str, dict] = {}
    for r in rows:
        key = _period_bucket_key(granularity, r["trade_date"])
        b = buckets.setdefault(
            key,
            {
                "period": key,
                "net_pnl": 0.0,
                "gross_credit": 0.0,
                "fees": 0.0,
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "trade_pnls": [],
            },
        )
        pnl = float(r.get("pnl") or 0)
        b["net_pnl"] += pnl
        b["gross_credit"] += float(r.get("net_credit") or 0)
        b["fees"] += float(r.get("fees") or 0)
        b["trades"] += 1
        if r.get("pnl") is not None:
            b["trade_pnls"].append(pnl)
            # Same net-P&L win definition as _stats_for_period / db._range_stats_for_rows.
            if pnl - float(r.get("fees") or 0) > 0:
                b["wins"] += 1
            else:
                b["losses"] += 1

    series = sorted(buckets.values(), key=lambda b: b["period"])
    running = 0.0
    peak = 0.0
    for b in series:
        pnls = b.pop("trade_pnls")
        wins_dollar = [p for p in pnls if p > 0]
        losses_dollar = [p for p in pnls if p <= 0]
        gross_win = sum(wins_dollar)
        gross_loss = abs(sum(losses_dollar))
        b["net_pnl"] = round(b["net_pnl"], 2)
        b["fees"] = round(b["fees"], 2)
        b["profit_factor"] = round(gross_win / gross_loss, 3) if gross_loss > 0 else None
        b["avg_win"] = round(gross_win / len(wins_dollar), 2) if wins_dollar else None
        b["avg_loss"] = round(sum(losses_dollar) / len(losses_dollar), 2) if losses_dollar else None
        b["expectancy_per_trade"] = round(sum(pnls) / len(pnls), 2) if pnls else None
        running += b["net_pnl"]
        peak = max(peak, running)
        b["cumulative_pnl"] = round(running, 2)
        b["equity"] = round(_BANKROLL_BASE + running, 2)
        b["drawdown"] = round(peak - running, 2)
        resolved = b["wins"] + b["losses"]
        b["win_rate_pct"] = round(b["wins"] / resolved * 100, 1) if resolved else None

    return series


def _risk_metrics(daily_series: list[dict], bankroll: float = _BANKROLL_BASE) -> dict:
    """Sharpe/Sortino/Calmar/recovery-factor for the current window, derived from the
    daily net_pnl series (ratio metrics need daily granularity regardless of which
    granularity the UI is displaying). Returns None fields (not zeros) when the sample is
    too small to be meaningful (Sharpe/Sortino need >=2 return periods) — the caller
    (client-side) renders these as an explicit '—', not a misleadingly precise 0.00.
    """
    if not daily_series:
        return {"sharpe": None, "sortino": None, "calmar": None, "recovery_factor": None, "sample_size": 0}

    returns = [b["net_pnl"] / bankroll for b in daily_series]
    n = len(returns)
    mean_r = sum(returns) / n

    def _stdev(values: list[float]) -> float | None:
        if len(values) < 2:
            return None
        m = sum(values) / len(values)
        var = sum((v - m) ** 2 for v in values) / (len(values) - 1)
        return var**0.5

    sd = _stdev(returns)
    sharpe = round(mean_r / sd * (252**0.5), 3) if sd else None

    downside = [r for r in returns if r < 0]
    dd_sd = _stdev(downside) if len(downside) >= 2 else None
    sortino = round(mean_r / dd_sd * (252**0.5), 3) if dd_sd else None

    total_return = sum(returns)
    max_dd = max((b["drawdown"] for b in daily_series), default=0.0)
    max_dd_pct = max_dd / bankroll if bankroll else 0.0
    annualized_return = total_return * (252 / n) if n else 0.0
    calmar = round(annualized_return / max_dd_pct, 3) if max_dd_pct > 0 else None

    net_pnl_total = sum(b["net_pnl"] for b in daily_series)
    recovery_factor = round(net_pnl_total / max_dd, 3) if max_dd > 0 else None

    return {
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "recovery_factor": recovery_factor,
        "sample_size": n,
        # Overfit flags per docs/paper-trading.md's graduation-gate notes — Sharpe > 3 or
        # profit_factor > 4.0 is a curve-fit warning, not a stronger pass.
        "sharpe_overfit_flag": sharpe is not None and sharpe > 3,
    }


# ── Per-spread status ─────────────────────────────────────────────────────────


def _badge(label: str, btype: str) -> dict:
    return {"label": label, "type": btype}


def _leg_badge(leg: dict | None) -> dict | None:
    """Badge for a single resolved leg, or None if there's no per-leg record for it."""
    if leg is None:
        return None
    status = (leg.get("status") or "").lower()
    exit_time = leg.get("exit_time") or ""
    time_str = ""
    if exit_time:
        s = str(exit_time).replace("T", " ")
        time_str = s[11:16] if len(s) >= 16 else ""
    if status == "stopped":
        return _badge(f"STOPPED {time_str}".strip(), "stopped")
    if status == "expired":
        return _badge("expired", "expired")
    if status in ("force_closed", "closed_profit_target"):
        return _badge("force closed", "force_closed")
    if status == "open":
        return _badge("monitoring", "monitoring")
    return _badge(status or "unknown", "unknown")


def _spread_statuses(
    trade: dict, put_leg: dict | None = None, call_leg: dict | None = None
) -> tuple[dict, dict]:
    """Per-spread status badges. Uses real ic_spread_legs rows when available. A side
    with no leg row is either (a) part of a legacy trade recorded before per-leg
    tracking existed — both sides then show the same whole-trade-derived badge — or
    (b) a side that closed together with its sibling (expired/force-closed together,
    never given its own row) and so inherits that shared badge, or is genuinely
    still open and shows 'monitoring'."""
    status = (trade.get("status") or "").lower()
    exit_time = trade.get("exit_time") or ""
    time_str = ""
    if exit_time:
        s = str(exit_time).replace("T", " ")
        time_str = s[11:16] if len(s) >= 16 else ""

    monitoring = _badge("monitoring", "monitoring")
    expired = _badge("expired", "expired")
    pending = _badge("pending", "pending")
    cancelled = _badge("cancelled", "cancelled")
    force = _badge("force closed", "force_closed")
    stopped = _badge(f"STOPPED {time_str}".strip(), "stopped")

    if put_leg is None and call_leg is None:
        if status in ("pending", "partial_entry"):
            return pending, pending
        if status == "open":
            return monitoring, monitoring
        if status == "expired":
            return expired, expired
        if status == "cancelled":
            return cancelled, cancelled
        if status == "force_closed":
            return force, force
        if status in ("stopped", "partial"):
            return stopped, stopped
        return _badge(status, "unknown"), _badge(status, "unknown")

    def side_badge(leg: dict | None) -> dict:
        if leg is not None:
            return _leg_badge(leg)
        if status == "expired":
            return expired
        if status == "force_closed":
            return force
        if status == "cancelled":
            return cancelled
        if status in ("pending", "partial_entry"):
            return pending
        return monitoring

    return side_badge(put_leg), side_badge(call_leg)


# ── History / Performance analytics ────────────────────────────────────────────
# net convention here is explicit: gross = SUM(pnl) (spread P&L before costs), fees =
# SUM(fees), net = gross - fees. This matches the gross-vs-net split the report/EOD digest
# emit — and is deliberately labelled so it isn't confused with the stats-grid "net_pnl",
# which is SUM(pnl) (gross-of-fees) for backward-compat.

_HIST_TRADE_COLS = (
    "ic_order_id, trade_date, symbol, risk_profile, entry_time, exit_time, "
    "put_strike, call_strike, wing_width, net_credit, quantity, "
    "put_credit, call_credit, status, session_quality, "
    # iv_skew_signal / price_action_signal deliberately not selected: the paper engine
    # leaves both NULL (they were the retired agent loop's fields), so shipping them in
    # every payload invites analysis over columns that are all NULL by construction.
    "iv_rank_at_entry, "
    "put_delta_at_entry, call_delta_at_entry, "
    "exit_reason, pnl, fees, ai_entry_reasoning"
)


def _history_trades(conn, sym_filter, prof_filter, limit=1000):
    """Full Today's-Trades-shape rows across every date (newest first), for the filterable
    trade log. Client filters/sorts within this window; the server just scopes by the two
    global selectors (symbol, profile) and caps the set."""
    sql = (
        f"SELECT {_HIST_TRADE_COLS} FROM ic_trades "
        "WHERE status NOT IN ('cancelled','pending','partial_entry')"
    )
    params: list = []
    if sym_filter:
        sql += " AND symbol = ?"
        params.append(sym_filter)
    if prof_filter:
        sql += " AND risk_profile = ?"
        params.append(prof_filter)
    sql += " ORDER BY trade_date DESC, entry_time DESC LIMIT ?"
    params.append(limit)
    raw = _rows(conn, sql, params)
    legs = _fetch_spread_legs(conn, [t["ic_order_id"] for t in raw])
    out = []
    for t in raw:
        tl = legs.get(t["ic_order_id"], {})
        put_s, call_s = _spread_statuses(t, tl.get("put"), tl.get("call"))
        row = dict(t)
        row["put_status"] = put_s
        row["call_status"] = call_s
        out.append(row)
    return out


def _by_profile_compare(conn, sym_filter):
    """Side-by-side per-profile scorecard (all profiles, ranked by net) — the variance-testing
    payoff. Symbol filter is honoured; the profile selector is intentionally NOT (the whole
    point is to compare every profile at once). Returns [] on a DB with no risk_profile column."""
    if not _has_column(conn, "ic_trades", "risk_profile"):
        return []
    where = ["status NOT IN ('cancelled','pending','partial_entry')", "risk_profile IS NOT NULL"]
    params: list = []
    if sym_filter:
        where.append("symbol = ?")
        params.append(sym_filter)
    rows = _rows(
        conn,
        f"SELECT risk_profile, trade_date, entry_time, pnl, fees FROM ic_trades "
        f"WHERE {' AND '.join(where)} ORDER BY risk_profile, trade_date, entry_time",
        params,
    )
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(r["risk_profile"], []).append(r)
    out = []
    for prof, rs in groups.items():
        nets = [float(r.get("pnl") or 0) - float(r.get("fees") or 0) for r in rs]
        trades = len(rs)
        gross = sum(float(r.get("pnl") or 0) for r in rs)
        fees = sum(float(r.get("fees") or 0) for r in rs)
        net = gross - fees
        # Win rate over RESOLVED trades only (pnl recorded), same denominator as
        # db._range_stats_for_rows -- an open trade is not a loss yet.
        resolved_nets = [
            float(r.get("pnl") or 0) - float(r.get("fees") or 0) for r in rs if r.get("pnl") is not None
        ]
        wins = sum(1 for x in resolved_nets if x > 0)
        gw = sum(x for x in nets if x > 0)
        gl = abs(sum(x for x in nets if x <= 0))
        running = peak = maxdd = 0.0
        for x in nets:  # rs is date/time-ordered, so this is the real equity path
            running += x
            peak = max(peak, running)
            maxdd = max(maxdd, peak - running)
        out.append(
            {
                "profile": prof,
                "trades": trades,
                # Under overlap_scope 'none' a single session can carry 100+ entries from one
                # stream (see analytics.py's module docstring) — trades alone overstates the
                # independent evidence behind a row, so the session count rides beside it.
                "sessions": len({r["trade_date"] for r in rs if r["trade_date"]}),
                "gross_pnl": round(gross, 2),
                "fees": round(fees, 2),
                "net_pnl": round(net, 2),
                "win_rate_pct": round(wins / len(resolved_nets) * 100, 1) if resolved_nets else None,
                "expectancy": round(net / trades, 2) if trades else None,
                "profit_factor": round(gw / gl, 2) if gl > 0 else None,
                "max_drawdown": round(maxdd, 2),
            }
        )
    out.sort(key=lambda d: d["net_pnl"], reverse=True)
    return out


def _arm_scorecard(conn: sqlite3.Connection, sym_filter: str | None) -> list[dict]:
    """Per-stream breakeven identity (analytics.by_arm + breakeven_scorecard), the read
    surface the forward-test plan is designed around. Paper-DB-only (behind an `era`
    column-exists check, same pattern as `_by_profile_compare`'s `risk_profile` check) — an
    inert [] on the live DB, which has neither arms nor the identity's inputs."""
    if not _has_column(conn, "ic_trades", "era"):
        return []
    out = []
    for row in _an.by_arm(conn, symbol=sym_filter):
        bc = _an.breakeven_scorecard(conn, symbol=sym_filter, arm=row["arm"])
        out.append({**row, **bc, "arm": row["arm"]})
    return out


def _stop_policy_card(conn: sqlite3.Connection, sym_filter: str | None) -> list[dict]:
    """What each derived stop policy (stop_policies.POLICIES, minus `control` which IS the
    real mechanism) would have paid across `open`'s recorded paths — see
    analytics.stop_counterfactual. Paper-DB-only, same guard as `_arm_scorecard`."""
    if not _has_column(conn, "ic_trades", "era"):
        return []
    return [
        _an.stop_counterfactual(conn, name, symbol=sym_filter, arm="open")
        for name in _sp.POLICIES
        if name != "control"
    ]


def _regime_coverage_panel(conn: sqlite3.Connection, sym_filter: str | None) -> dict:
    """Regime-tag coverage per dimension, plus a by-bucket P&L cut for every dimension that
    is both tagged and non-degenerate (see analytics.regime_coverage's docstring on why a
    degenerate dimension's P&L table is withheld rather than shown as a false contrast).
    Paper-DB-only, same guard as `_arm_scorecard`."""
    if not _has_column(conn, "ic_trades", "era"):
        return {"coverage": {}, "by_dimension": {}}
    coverage = _an.regime_coverage(conn, symbol=sym_filter)
    by_dimension = {}
    for dim, d in coverage["dimensions"].items():
        if d["tagged"] and not d["degenerate"]:
            by_dimension[dim] = _an.by_regime(conn, dim, symbol=sym_filter)
    return {"coverage": coverage, "by_dimension": by_dimension}


_DELTA_BANDS = ("<0.10", "0.10-0.15", "0.15-0.20", ">=0.20")
_DOW_ORDER = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _delta_band(d):
    if d is None:
        return None
    d = abs(float(d))
    if d < 0.10:
        return "<0.10"
    if d < 0.15:
        return "0.10-0.15"
    if d < 0.20:
        return "0.15-0.20"
    return ">=0.20"


def _by_signal(conn, sym_clause, sym_params):
    """'Does this pre-trade attribute predict outcome?' breakdowns — avg NET P&L, trades, and
    win-rate by short-call delta band, wing width, symbol, and weekday. (IV-skew and
    price-action signals are captured columns but the paper engine leaves them NULL, and stored
    entry_time mixes timezones, so neither is surfaced.) Honours both global filters via
    sym_clause."""
    rows = _rows(
        conn,
        f"""
        SELECT trade_date, symbol, call_delta_at_entry, wing_width, pnl, fees
        FROM ic_trades
        WHERE pnl IS NOT NULL
          AND status NOT IN ('cancelled','pending','partial_entry'){sym_clause}
    """,
        sym_params,
    )

    def agg(keyfn, order=None):
        buckets: dict[str, dict] = {}
        for r in rows:
            k = keyfn(r)
            if k is None:
                continue
            net = float(r.get("pnl") or 0) - float(r.get("fees") or 0)
            b = buckets.setdefault(
                k, {"bucket": k, "trades": 0, "net_sum": 0.0, "wins": 0, "sessions": set()}
            )
            b["trades"] += 1
            b["net_sum"] += net
            if net > 0:
                b["wins"] += 1
            if r.get("trade_date"):
                b["sessions"].add(r["trade_date"])
        result = []
        for k, b in buckets.items():
            result.append(
                {
                    "bucket": k,
                    "trades": b["trades"],
                    "sessions": len(b["sessions"]),
                    "avg_pnl": round(b["net_sum"] / b["trades"], 2) if b["trades"] else None,
                    "win_rate_pct": round(b["wins"] / b["trades"] * 100, 1) if b["trades"] else None,
                }
            )
        if order:
            idx = {v: i for i, v in enumerate(order)}
            result.sort(key=lambda d: idx.get(d["bucket"], len(order)))
        else:
            result.sort(key=lambda d: -d["trades"])
        return result

    def _dow(r):
        try:
            return datetime.strptime(r["trade_date"], "%Y-%m-%d").strftime("%a")
        except Exception:
            return None

    def _wing(r):
        w = r.get("wing_width")
        if w is None:
            return None
        return (str(int(w)) if float(w).is_integer() else str(w)) + "-wide"

    return {
        "by_delta": agg(lambda r: _delta_band(r.get("call_delta_at_entry")), _DELTA_BANDS),
        "by_wing": agg(_wing),
        "by_symbol": agg(lambda r: r.get("symbol")),
        "by_dow": agg(_dow, _DOW_ORDER),
    }


def _daily_pnl(conn, sym_clause, sym_params):
    """Per-date gross/fees/net + trade count for the calendar heatmap. Honours both filters."""
    rows = _rows(
        conn,
        f"""
        SELECT trade_date,
               COALESCE(SUM(pnl), 0)  AS gross,
               COALESCE(SUM(fees), 0) AS fees,
               COUNT(*)               AS trades
        FROM ic_trades
        WHERE status NOT IN ('cancelled','pending','partial_entry'){sym_clause}
        GROUP BY trade_date
        ORDER BY trade_date ASC
    """,
        sym_params,
    )
    out = []
    for r in rows:
        gross = float(r.get("gross") or 0)
        fees = float(r.get("fees") or 0)
        out.append(
            {
                "date": r["trade_date"],
                "trades": r["trades"],
                "gross_pnl": round(gross, 2),
                "fees": round(fees, 2),
                "net_pnl": round(gross - fees, 2),
            }
        )
    return out


# ── API data builder ──────────────────────────────────────────────────────────


def _build_api_data(symbol: str | None = None, profile: str | None = None) -> dict:
    """symbol filters trades/stats/analytics to one traded symbol; omit (or "ALL") for the
    account-wide view across every symbol — the economically meaningful default, since the
    account's actual risk caps (max_concurrent_ics, max_entries_per_day, buying power) are
    checked against the combined total, not any one symbol in isolation.

    profile filters to one risk_profile (paper-trading DB only, behind a column-exists check,
    so it's inert on the live DB) across trades/stats/analytics/performance alike — a peer of
    `symbol`; omit (or "ALL") for the profile-blended view. nlv_series stays blended (closing
    NLV is an account-level daily figure, not attributable to a single profile)."""
    if not os.path.exists(_DB_PATH):
        return {"ok": False, "error": "Database not found — run: python src/db.py init_db"}

    sym_filter = symbol.upper() if symbol and symbol.upper() != "ALL" else None

    conn = _connect()
    today = _today()

    prof_filter = (
        profile
        if (profile and profile.upper() != "ALL" and _has_column(conn, "ic_trades", "risk_profile"))
        else None
    )

    stats = {
        "today": _stats_for_period(conn, start=today, end=today, symbol=sym_filter, profile=prof_filter),
        "week": _stats_for_period(
            conn, start=_week_start(), end=today, symbol=sym_filter, profile=prof_filter
        ),
        "month": _stats_for_period(
            conn, start=_month_start(), end=today, symbol=sym_filter, profile=prof_filter
        ),
        "year": _stats_for_period(
            conn, start=_year_start(), end=today, symbol=sym_filter, profile=prof_filter
        ),
        "all_time": _stats_for_period(conn, end=today, symbol=sym_filter, profile=prof_filter),
    }

    trades_sql = """
        SELECT ic_order_id, symbol, risk_profile, entry_time, fill_confirmed_at,
               put_strike, call_strike, wing_width, net_credit, quantity,
               put_credit, call_credit, status, session_quality,
               iv_rank_at_entry,
               stop_trigger_current, stop_limit_current, stop_adjustment_count,
               exit_time, exit_price, exit_reason, pnl, fees,
               ai_entry_reasoning
        FROM ic_trades
        WHERE trade_date = ?
    """
    trades_params: list = [today]
    if sym_filter:
        trades_sql += " AND symbol = ?"
        trades_params.append(sym_filter)
    if prof_filter:
        trades_sql += " AND risk_profile = ?"
        trades_params.append(prof_filter)
    trades_sql += " ORDER BY entry_time"
    raw_trades = _rows(conn, trades_sql, trades_params)

    today_legs = _fetch_spread_legs(conn, [t["ic_order_id"] for t in raw_trades])
    trades = []
    for t in raw_trades:
        trade_legs = today_legs.get(t["ic_order_id"], {})
        put_s, call_s = _spread_statuses(t, trade_legs.get("put"), trade_legs.get("call"))
        row = dict(t)
        row["put_status"] = put_s
        row["call_status"] = call_s
        trades.append(row)

    last_loop = _one(
        conn,
        """
        SELECT loop_time, action, open_trades_n, today_pnl,
               iv_rank, underlying_price, session_quality
        FROM loop_log
        WHERE loop_date = ?
        ORDER BY loop_time DESC LIMIT 1
    """,
        (today,),
    )

    nlv_series = _rows(
        conn,
        """
        SELECT summary_date AS date, closing_nlv, net_pnl
        FROM daily_summary
        WHERE closing_nlv IS NOT NULL
        ORDER BY summary_date ASC
    """,
    )

    # Combined symbol+profile predicate reused by every analytics/history query below, so both
    # filters scope them identically (the name stays `sym_*` to leave those f-strings untouched).
    sym_clause = " AND symbol = ?" if sym_filter else ""
    sym_params = [sym_filter] if sym_filter else []
    if prof_filter:
        sym_clause += " AND risk_profile = ?"
        sym_params.append(prof_filter)

    by_session = _rows(
        conn,
        f"""
        SELECT session_quality,
               COUNT(*) AS total,
               SUM(CASE WHEN pnl IS NOT NULL AND pnl - COALESCE(fees, 0) > 0
                        THEN 1 ELSE 0 END) AS wins,
               ROUND(AVG(pnl), 2) AS avg_pnl
        FROM ic_trades
        WHERE status NOT IN ('cancelled','pending','partial_entry')
          AND session_quality IS NOT NULL{sym_clause}
        GROUP BY session_quality
        ORDER BY total DESC
    """,
        sym_params,
    )

    by_exit = _rows(
        conn,
        f"""
        SELECT COALESCE(exit_reason, 'open') AS exit_reason, COUNT(*) AS count
        FROM ic_trades
        WHERE status NOT IN ('cancelled','pending','partial_entry'){sym_clause}
        GROUP BY exit_reason
        ORDER BY count DESC
    """,
        sym_params,
    )

    by_iv = _rows(
        conn,
        f"""
        SELECT
            CASE
                WHEN iv_rank_at_entry < 0.25 THEN '<25%'
                WHEN iv_rank_at_entry < 0.50 THEN '25-50%'
                WHEN iv_rank_at_entry < 0.75 THEN '50-75%'
                ELSE '>75%'
            END AS iv_bucket,
            COUNT(*) AS trades,
            ROUND(AVG(pnl), 2) AS avg_pnl,
            SUM(CASE WHEN pnl - COALESCE(fees, 0) > 0 THEN 1 ELSE 0 END) AS wins
        FROM ic_trades
        WHERE pnl IS NOT NULL AND iv_rank_at_entry IS NOT NULL
          AND status NOT IN ('cancelled','pending','partial_entry'){sym_clause}
        GROUP BY iv_bucket
        ORDER BY MIN(iv_rank_at_entry)
    """,
        sym_params,
    )

    fee_row = (
        _one(
            conn,
            f"""
        SELECT COALESCE(SUM(net_credit * quantity), 0) AS gross_credit,
               COALESCE(SUM(fees), 0)                  AS total_fees,
               COALESCE(SUM(pnl), 0)                   AS net_pnl
        FROM ic_trades
        WHERE status NOT IN ('cancelled','pending','partial_entry'){sym_clause}
    """,
            sym_params,
        )
        or {}
    )
    gross = float(fee_row.get("gross_credit") or 0)
    fees = float(fee_row.get("total_fees") or 0)
    net = float(fee_row.get("net_pnl") or 0)
    fee_summary = {
        "gross_credit": round(gross, 2),
        "total_fees": round(fees, 2),
        "net_pnl": round(net, 2),
        "fee_drag_pct": round(fees / gross * 100, 1) if gross > 0 else None,
    }

    raw_recent = _rows(
        conn,
        f"""
        SELECT trade_date, ic_order_id, symbol, entry_time, exit_time,
               put_strike, call_strike, wing_width,
               net_credit, put_credit, call_credit,
               status, exit_reason, pnl, fees, session_quality
        FROM ic_trades
        WHERE status NOT IN ('cancelled','pending','partial_entry'){sym_clause}
        ORDER BY trade_date DESC, entry_time DESC
        LIMIT 60
    """,
        sym_params,
    )

    recent_legs = _fetch_spread_legs(conn, [t["ic_order_id"] for t in raw_recent])
    recent_trades = []
    for t in raw_recent:
        trade_legs = recent_legs.get(t["ic_order_id"], {})
        put_s, call_s = _spread_statuses(t, trade_legs.get("put"), trade_legs.get("call"))
        w, loss = _spread_wins_losses(
            t.get("status"), t.get("pnl"), trade_legs.get("put"), trade_legs.get("call")
        )
        recent_trades.append(
            {
                "trade_date": t.get("trade_date"),
                "ic_order_id": t.get("ic_order_id"),
                "symbol": t.get("symbol"),
                "entry_time": t.get("entry_time"),
                "exit_time": t.get("exit_time"),
                "put_strike": t.get("put_strike"),
                "call_strike": t.get("call_strike"),
                "wing_width": t.get("wing_width"),
                "net_credit": t.get("net_credit"),
                "put_credit": t.get("put_credit"),
                "call_credit": t.get("call_credit"),
                "status": t.get("status"),
                "exit_reason": t.get("exit_reason"),
                "pnl": t.get("pnl"),
                "fees": t.get("fees"),
                "session_quality": t.get("session_quality"),
                "put_status": put_s,
                "call_status": call_s,
                "spread_wins": w,
                "spread_losses": loss,
            }
        )

    profile_rows = _rows(
        conn,
        "SELECT DISTINCT risk_profile FROM ic_trades WHERE risk_profile IS NOT NULL ORDER BY risk_profile",
    )
    # A DB with no profile-tagged trades (today's live DB, or a fresh paper DB) falls back to
    # a single "live" entry — the profile selector then stays inert, exactly like before this
    # feature existed. A paper DB with real trades naturally returns the four risk profiles as
    # they accrue — no _MODE branching needed, this is purely data-driven.
    profiles = [r["risk_profile"] for r in profile_rows] or ["live"]

    daily_series = _pnl_series(conn, "daily", symbol=sym_filter, profile=profile)
    performance = {
        "daily": daily_series,
        "weekly": _pnl_series(conn, "weekly", symbol=sym_filter, profile=profile),
        "monthly": _pnl_series(conn, "monthly", symbol=sym_filter, profile=profile),
        "bankroll_base": _BANKROLL_BASE,
        "risk_metrics": _risk_metrics(daily_series),
        "profiles": profiles,
        "selected_profile": profile or "ALL",
        # Ranked all-profiles scorecard — ignores the profile selector by design (symbol only).
        "by_profile": _by_profile_compare(conn, sym_filter),
        # The forward-test streams' breakeven identity and derived-stop-policy read surfaces
        # (analytics.py) — also ignore the profile selector, same reasoning as by_profile above.
        "arm_scorecard": _arm_scorecard(conn, sym_filter),
        "stop_policy_card": _stop_policy_card(conn, sym_filter),
    }

    history_trades = _history_trades(conn, sym_filter, prof_filter)
    signals = _by_signal(conn, sym_clause, sym_params)
    daily_pnl = _daily_pnl(conn, sym_clause, sym_params)
    regime = _regime_coverage_panel(conn, sym_filter)

    # Width-study comparison: one daily cumulative_pnl series per (symbol x arm) cell — each
    # already its own paper portfolio via the (profile x symbol) grain, so this is the same
    # _pnl_series() the Performance view uses, just called once per cell rather than once for the
    # page's current symbol/profile selection. Ignores the page's symbol/profile filters by design
    # (like by_profile above) — the comparison view always shows every cell side by side.
    study_arms = {
        "arms": STUDY_ARMS,
        "symbols": {
            sym: {arm: _pnl_series(conn, "daily", symbol=sym, profile=arm) for arm in STUDY_ARMS}
            for sym in _load_symbols()
        },
    }

    conn.close()

    return {
        "ok": True,
        "as_of": _now_iso(),
        "today": today,
        "symbols": _load_symbols(),  # every configured symbol, for the selector
        "selected_symbol": sym_filter or "ALL",
        "stats": stats,
        "trades": trades,
        "last_loop": last_loop,
        "nlv_series": nlv_series,
        "performance": performance,
        "study_arms": study_arms,
        "analytics": {
            "by_session": by_session,
            "by_exit": by_exit,
            "by_iv": by_iv,
            "fee_summary": fee_summary,
            "recent_trades": recent_trades,
            "history": history_trades,
            "signals": signals,
            "daily_pnl": daily_pnl,
            "regime": regime,
        },
    }


# ── symbols ────────────────────────────────────────────────────────────────


def _load_symbols() -> list[str]:
    """Every traded symbol, in config order. Falls back to the deprecated
    single-symbol 'symbol' key, then to ["XSP"], if 'symbols' is absent."""
    try:
        with open(_paths.config_path()) as f:
            cfg = json.load(f)
    except Exception:
        return ["XSP"]
    if cfg.get("symbols"):
        return [str(s).strip().upper() for s in cfg["symbols"] if str(s).strip()]
    if cfg.get("symbol"):
        return [str(cfg["symbol"]).strip().upper()]
    return ["XSP"]


# ── HTML ──────────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MEICAgent</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
     background:#0a0d12;color:#e6edf3;height:100vh;overflow:hidden}
.app{display:flex;height:100vh}

/* Sidebar */
.sidebar{width:210px;flex-shrink:0;background:#0d1117;border-right:1px solid #1e2430;
          display:flex;flex-direction:column}
.brand{padding:22px 18px 18px;border-bottom:1px solid #1e2430}
.brand h1{font-size:15px;font-weight:700;letter-spacing:.3px;color:#fff}
.brand p{font-size:10px;color:#6b7280;margin-top:3px;letter-spacing:1px;text-transform:uppercase}
.mode-badge{display:inline-block;margin-top:9px;padding:4px 9px;font-size:10px;font-weight:700;
  letter-spacing:.8px;text-transform:uppercase;color:#f5a623;background:#3d2a0d;
  border:1px solid #f5a623;border-radius:4px}
nav{flex:1;padding:10px 0}
.nav-item{display:flex;align-items:center;gap:9px;padding:9px 18px;cursor:pointer;
           font-size:13px;color:#6b7280;transition:all .15s;
           border-left:3px solid transparent}
.nav-item:hover{color:#e6edf3;background:#111519}
.nav-item.active{color:#e6edf3;background:#111519;border-left-color:#00c896}
.nav-icon{font-size:14px;width:16px;text-align:center}
.sidebar-footer{padding:14px 18px;border-top:1px solid #1e2430;font-size:11px}
.status-pill{display:flex;align-items:center;gap:6px;font-weight:600;margin-bottom:5px}
.dot{width:7px;height:7px;border-radius:50%}
.dot.live{background:#00c896;animation:pulse 2s infinite}
.dot.idle{background:#6b7280}
.dot.err{background:#e8423a}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.status-meta{color:#6b7280;line-height:1.7}

/* Content */
.content{flex:1;display:flex;flex-direction:column;overflow:hidden;min-width:0}
.disc-banner{display:none;background:#3d1a1a;color:#e8423a;padding:7px 18px;
              font-size:11px;font-weight:600;border-bottom:1px solid #e8423a}
.view{display:none;flex-direction:column;height:100%}
.view.active{display:flex}

/* Frames */
.frame{overflow-y:auto;border-bottom:1px solid #1e2430}
.frame:last-child{flex:1;min-height:0;border-bottom:none}
.frame-hdr{padding:10px 18px 0;display:flex;align-items:center;justify-content:space-between}
.frame-title{font-size:10px;font-weight:600;color:#6b7280;letter-spacing:1.5px;text-transform:uppercase}
.frame-sub{font-size:10px;color:#6b7280}

/* Stats grid */
.stats-wrap{padding:10px 18px 14px;overflow-x:auto}
.sgrid{border-collapse:collapse;width:100%;font-size:12px}
.sgrid th,.sgrid td{padding:7px 14px;text-align:right;white-space:nowrap}
.sgrid th{font-size:9px;font-weight:700;color:#6b7280;letter-spacing:1px;
           text-transform:uppercase;border-bottom:1px solid #1e2430}
.sgrid td{border-bottom:1px solid #111519;color:#e6edf3}
.sgrid tr:last-child td{border-bottom:none}
.sgrid .lbl{text-align:left;color:#6b7280;font-size:11px;padding-left:0;font-weight:500}
.sgrid .today-col{background:#111519}
.sgrid .today-col th{color:#e6edf3}
.pos{color:#00c896}.neg{color:#e8423a}.neu{color:#e6edf3}
.wh{color:#00c896}.wm{color:#f5a623}.wl{color:#e8423a}
.dash{color:#2d3441}

/* Trades table */
.tbl-wrap{overflow-x:auto;height:100%}
.ttbl{border-collapse:collapse;width:100%;font-size:12px;min-width:780px}
.ttbl th{padding:8px 11px;text-align:left;font-size:9px;font-weight:700;
          color:#e6edf3;letter-spacing:1px;text-transform:uppercase;
          background:#0d1117;border-bottom:2px solid #1e2430;
          position:sticky;top:0;z-index:1}
.ttbl td{padding:9px 11px;border-bottom:1px solid #111519;vertical-align:middle}
.ttbl tr:hover td{background:#0f1318}
.tr{text-align:right}
td.num,th.num{text-align:right}
.tcredit{font-weight:600;color:#00c896}
.tppos{font-weight:700;color:#00c896;text-align:right}
.tpneg{font-weight:700;color:#e8423a;text-align:right}
.tpnil{color:#2d3441;text-align:right}
.empty{padding:36px 18px;text-align:center;color:#2d3441;font-size:13px}

/* Badges */
.bdg{display:inline-block;padding:2px 7px;border-radius:3px;
      font-size:9px;font-weight:700;letter-spacing:.5px;text-transform:uppercase;white-space:nowrap}
.bdg-monitoring{background:#131920;color:#3d4451}
.bdg-expired{background:#1a1f2a;color:#8b949e}
.bdg-stopped{background:#3d1a1a;color:#e8423a}
.bdg-pending{background:#2a2510;color:#f5a623}
.bdg-cancelled{background:#1a1f2a;color:#3d4451;text-decoration:line-through}
.bdg-force_closed{background:#2a1a08;color:#f97316}
.bdg-unknown{background:#1a1f2a;color:#6b7280}

/* History charts */
.chart-wrap{padding:14px 18px;position:relative}
.chart-wrap canvas{max-height:175px}
.ana-grid{display:grid;grid-template-columns:1fr 1fr;gap:1px;
           background:#1e2430;flex:1;min-height:0;height:100%}
.apanel{background:#0a0d12;padding:14px 18px;overflow-y:auto}
.ptitle{font-size:9px;font-weight:700;color:#6b7280;letter-spacing:1.5px;
         text-transform:uppercase;margin-bottom:10px}
.atable{width:100%;border-collapse:collapse;font-size:11px}
.atable th{font-size:9px;color:#6b7280;font-weight:600;text-transform:uppercase;
            letter-spacing:.5px;padding:3px 8px;text-align:right;
            border-bottom:1px solid #1e2430}
.atable th:first-child{text-align:left}
.atable td{padding:5px 8px;border-bottom:1px solid #111519;text-align:right;color:#e6edf3}
.atable td:first-child{text-align:left;color:#8b949e}
.atable tr:last-child td{border-bottom:none}
/* Card grid: auto-fit so cards reflow to as many columns as the viewer's width allows
   (4 across when wide, down to 1 when narrow) with no fixed breakpoints. */
.fee-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}
.fee-card{padding:10px 12px;background:#111519;border-radius:5px}
.fee-lbl{font-size:9px;color:#6b7280;text-transform:uppercase;letter-spacing:.5px;margin-bottom:3px}
.fee-val{font-size:17px;font-weight:700;color:#e6edf3}
.fee-val.neg{color:#e8423a}.fee-val.warn{color:#f5a623}

/* Two-panel analytics grid: side-by-side when there's room, stacked on narrow viewports. */
@media (max-width:820px){.ana-grid{grid-template-columns:1fr}}

/* Drag-to-reorder: cards/panels within a grid can be dragged by their handle to
   reorder; order persists per-browser in localStorage. Column reflow (auto-fit /
   the 820px breakpoint above) still runs on top of the manual order. */
.apanel,.fee-card{position:relative}
.reorder-handle{position:absolute;top:4px;right:5px;z-index:3;cursor:grab;
                color:#6b7280;font-size:12px;line-height:1;letter-spacing:-1px;
                padding:2px 4px;border-radius:3px;user-select:none;opacity:.5;
                border:1px solid #1e2430;background:#0d1117;
                transition:opacity .12s,color .12s,border-color .12s}
.apanel:hover>.reorder-handle,.fee-card:hover>.reorder-handle{opacity:.9}
.reorder-handle:hover{opacity:1;color:#e6edf3;border-color:#2f81f7}
.reorder-handle:active{cursor:grabbing}
.reorder-drag{opacity:.35}
.reorder-over{outline:1px dashed #2f81f7;outline-offset:-2px}
.reset-layout{margin-top:8px;font-size:9px;letter-spacing:.5px;text-transform:uppercase;
              color:#4a5568;background:none;border:none;cursor:pointer;padding:2px 0;
              text-align:left;display:none}
.reset-layout:hover{color:#8b949e;text-decoration:underline}
.reset-layout.show{display:block}

/* History analytics stack: let the analytics frame flow and scroll rather than force a
   single full-height grid, so the heatmap, breakdowns, and trade log can stack below. */
.ana-grid.flow{height:auto;flex:none;min-height:0}
.ana-grid.flow>.apanel{overflow:visible}

/* Sortable table headers (trade log + profile comparison) */
.atable th.sortable{cursor:pointer;user-select:none;white-space:nowrap}
.atable th.sortable:hover{color:#8b949e}
.atable th.sortable .ar{opacity:.45;font-size:8px;margin-left:2px}
.atable th.sortable.sorted{color:#e6edf3}
.atable th.sortable.sorted .ar{opacity:1;color:#2f81f7}

/* Trade-log filter bar */
.log-filters{display:flex;flex-wrap:wrap;gap:8px 10px;margin:8px 0 10px;align-items:center}
.log-filters input,.log-filters select{background:#0d1117;color:#e6edf3;border:1px solid #1e2430;
  border-radius:4px;padding:4px 7px;font-size:11px;outline:none}
.log-filters input[type=date]{color-scheme:dark;min-width:120px}
.log-filters input[type=search]{min-width:150px}
.lf-lbl{font-size:9px;color:#6b7280;text-transform:uppercase;letter-spacing:.5px}
.lf-clear{font-size:9px;letter-spacing:.5px;text-transform:uppercase;color:#4a5568;
  background:none;border:none;cursor:pointer;padding:2px 0}
.lf-clear:hover{color:#8b949e;text-decoration:underline}

/* Daily Net P&L calendar heatmap: shared renderer (cherrypick.core.viz) */
%%CP_CAL_HEAT_STYLE%%

</style>
</head>
<body data-cp-reorder-store='meic-dash-layout-v1'>
<div class="app">

<aside class="sidebar">
  <div class="brand">
    <h1>MEICAgent</h1>
    <p>0DTE MEIC Strategy</p>
    <!--MODE_BADGE-->
  </div>
  <nav>
    <div class="nav-item active" data-view="today">
      <span class="nav-icon">&#9670;</span> Today
    </div>
    <div class="nav-item" data-view="history">
      <span class="nav-icon">&#9711;</span> History
    </div>
    <div class="nav-item" data-view="performance">
      <span class="nav-icon">&#128200;</span> Performance
    </div>
  </nav>
  <div class="sidebar-footer">
    <div class="status-pill">
      <div class="dot idle" id="sdot"></div>
      <span id="slabel">Loading&hellip;</span>
    </div>
    <div class="status-meta">
      <div id="smeta">&mdash;</div>
      <div id="scountdown" style="color:#4a5568"></div>
    </div>
    <button type="button" class="reset-layout" id="reset-layout"
            title="Restore the original card order">&#8635; Reset layout</button>
  </div>
</aside>

<main class="content">
  <div class="disc-banner" id="disc-banner">&#9679; DISCONNECTED &mdash; unable to reach dashboard server</div>

  <!-- TODAY VIEW -->
  <div class="view active" id="view-today">
    <div class="frame" style="flex:0 0 auto">
      <div class="frame-hdr">
        <span class="frame-title">Performance</span>
        <select id="main-symbol-select" style="background:#0d1117;color:#e6edf3;border:1px solid #1e2430;
                border-radius:4px;padding:3px 8px;font-size:11px;cursor:pointer;outline:none;margin-left:10px">
          <option value="ALL" selected>All symbols</option>
        </select>
        <select id="main-profile-select" disabled title="Enabled once paper-trading risk_profile data exists"
                style="background:#0d1117;color:#e6edf3;border:1px solid #1e2430;
                border-radius:4px;padding:3px 8px;font-size:11px;cursor:pointer;outline:none;margin-left:6px">
          <option value="ALL" selected>All profiles</option>
        </select>
        <span class="frame-sub" id="as-of"></span>
      </div>
      <div class="stats-wrap">
        <table class="sgrid">
          <thead>
            <tr>
              <th class="lbl" style="padding-left:0"></th>
              <th class="today-col">TODAY</th>
              <th>THIS WEEK</th>
              <th>THIS MONTH</th>
              <th>THIS YEAR</th>
              <th>ALL-TIME</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td class="lbl">Net P&amp;L</td>
              <td class="today-col" id="pnl-today"></td>
              <td id="pnl-week"></td><td id="pnl-month"></td>
              <td id="pnl-year"></td><td id="pnl-all"></td>
            </tr>
            <tr>
              <td class="lbl">Total Trades</td>
              <td class="today-col" id="tr-today"></td>
              <td id="tr-week"></td><td id="tr-month"></td>
              <td id="tr-year"></td><td id="tr-all"></td>
            </tr>
            <tr>
              <td class="lbl">Wins (spreads)</td>
              <td class="today-col" id="w-today"></td>
              <td id="w-week"></td><td id="w-month"></td>
              <td id="w-year"></td><td id="w-all"></td>
            </tr>
            <tr>
              <td class="lbl">Losses (spreads)</td>
              <td class="today-col" id="l-today"></td>
              <td id="l-week"></td><td id="l-month"></td>
              <td id="l-year"></td><td id="l-all"></td>
            </tr>
            <tr>
              <td class="lbl">W/L Ratio</td>
              <td class="today-col" id="wl-today"></td>
              <td id="wl-week"></td><td id="wl-month"></td>
              <td id="wl-year"></td><td id="wl-all"></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
    <div class="frame" style="flex:1;min-height:0">
      <div class="frame-hdr" style="padding-bottom:7px">
        <span class="frame-title">Today&rsquo;s Trades</span>
        <span class="frame-sub" id="trade-count"></span>
      </div>
      <div class="tbl-wrap" style="height:calc(100% - 34px)">
        <table class="ttbl">
          <thead>
            <tr>
              <th>TIME</th><th>SYMBOL</th><th>PROFILE</th><th>WIDTH</th><th>PUT STRIKE</th><th>CALL STRIKE</th>
              <th>PUT $</th><th>CALL $</th><th>NET CREDIT</th>
              <th>PUT STATUS</th><th>CALL STATUS</th>
              <th style="text-align:right">P&amp;L</th>
            </tr>
          </thead>
          <tbody id="tbody">
            <tr><td colspan="12" class="empty">Loading&hellip;</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- HISTORY VIEW -->
  <div class="view" id="view-history">
    <div class="frame" style="flex:0 0 230px">
      <div class="frame-hdr" style="padding-bottom:4px">
        <span class="frame-title">Account Value (NLV) Over Time</span>
      </div>
      <div class="chart-wrap">
        <canvas id="nlv-canvas"></canvas>
        <div class="empty" id="nlv-empty" style="display:none;padding:18px 0">
          No closing NLV data yet &mdash; appears after the first EOD sequence runs.
        </div>
      </div>
    </div>
    <div class="frame" style="flex:1;min-height:0">
      <!-- Daily net P&L calendar heatmap -->
      <div class="apanel" style="overflow:visible">
        <div class="ptitle">Daily Net P&amp;L (after fees)</div>
        <div id="cal-heat" class="cal-heat"></div>
        <div class="empty" id="cal-heat-empty" style="display:none;padding:8px 0">No closed sessions yet.</div>
      </div>

      <div class="ana-grid flow" data-cp-reorder="history-ana" data-cp-reorder-items=".apanel" data-cp-reorder-label=".ptitle" style="margin-top:10px">
        <div class="apanel">
          <div class="ptitle">Win Rate by Session</div>
          <table class="atable" id="sess-tbl">
            <thead><tr><th>Session</th><th>Trades</th><th>Wins</th><th>Win %</th><th>Avg P&amp;L</th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
        <div class="apanel">
          <div class="ptitle">Exit Reasons</div>
          <table class="atable" id="exit-tbl">
            <thead><tr><th>Reason</th><th>Count</th><th>%</th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
        <div class="apanel">
          <div class="ptitle">Avg P&amp;L by IV Rank</div>
          <table class="atable" id="iv-tbl">
            <thead><tr><th>IV Rank</th><th>Trades</th><th>Wins</th><th>Avg P&amp;L</th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
        <div class="apanel">
          <div class="ptitle">Fee Drag (All-Time)</div>
          <div class="fee-grid" data-cp-reorder="fee-drag" data-cp-reorder-items=".fee-card" data-cp-reorder-label=".fee-lbl">
            <div class="fee-card"><div class="fee-lbl">Gross Credit</div><div class="fee-val" id="f-gross">&mdash;</div></div>
            <div class="fee-card"><div class="fee-lbl">Total Fees</div><div class="fee-val neg" id="f-fees">&mdash;</div></div>
            <div class="fee-card"><div class="fee-lbl">Net P&amp;L</div><div class="fee-val" id="f-net">&mdash;</div></div>
            <div class="fee-card"><div class="fee-lbl">Fee Drag</div><div class="fee-val warn" id="f-drag">&mdash;</div></div>
          </div>
        </div>
      </div>

      <!-- Signal-outcome breakdowns (avg NET P&L per pre-trade attribute) -->
      <div class="ana-grid flow" data-cp-reorder="signals-ana" data-cp-reorder-items=".apanel" data-cp-reorder-label=".ptitle" style="margin-top:10px">
        <div class="apanel">
          <div class="ptitle">Net P&amp;L by Short-Call Delta</div>
          <table class="atable" id="sig-delta-tbl">
            <thead><tr><th>Delta band</th><th>Trades</th><th>Sessions</th><th>Win %</th><th>Avg Net</th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
        <div class="apanel">
          <div class="ptitle">Net P&amp;L by Wing Width</div>
          <table class="atable" id="sig-wing-tbl">
            <thead><tr><th>Width</th><th>Trades</th><th>Sessions</th><th>Win %</th><th>Avg Net</th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
        <div class="apanel">
          <div class="ptitle">Net P&amp;L by Symbol</div>
          <table class="atable" id="sig-symbol-tbl">
            <thead><tr><th>Symbol</th><th>Trades</th><th>Sessions</th><th>Win %</th><th>Avg Net</th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
        <div class="apanel">
          <div class="ptitle">Net P&amp;L by Weekday</div>
          <table class="atable" id="sig-dow-tbl">
            <thead><tr><th>Weekday</th><th>Trades</th><th>Sessions</th><th>Win %</th><th>Avg Net</th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>

      <!-- Regime coverage: tagged/untagged/degenerate per dimension, plus a by-bucket P&L cut
           for whichever dimensions are both tagged and non-degenerate (paper era only). -->
      <div class="apanel" style="margin-top:10px;overflow:visible">
        <div class="ptitle">Regime Coverage</div>
        <table class="atable" id="regime-cov-tbl">
          <thead><tr><th>Dimension</th><th>Tagged</th><th>Untagged</th><th>Coverage %</th><th>Sessions</th><th>Effective n</th><th>Degenerate</th></tr></thead>
          <tbody></tbody>
        </table>
        <div id="regime-by-dim" style="margin-top:10px"></div>
      </div>

      <!-- Filterable trade log (full Today's-Trades shape across all dates) -->
      <div class="apanel" style="margin-top:10px;overflow:visible">
        <div class="ptitle">Trade Log <span id="log-count" style="color:#4a5568;font-weight:400"></span></div>
        <div class="log-filters">
          <span class="lf-lbl">From</span>
          <input type="date" id="lf-from">
          <span class="lf-lbl">To</span>
          <input type="date" id="lf-to">
          <select id="lf-outcome">
            <option value="ALL">All outcomes</option>
            <option value="win">Net wins</option>
            <option value="loss">Net losses</option>
            <option value="open">Open</option>
          </select>
          <select id="lf-exit"><option value="ALL">All exits</option></select>
          <select id="lf-session"><option value="ALL">All sessions</option></select>
          <input type="search" id="lf-search" placeholder="search symbol / profile / strike">
          <button type="button" class="lf-clear" id="lf-clear">clear</button>
        </div>
        <div style="overflow-x:auto">
          <table class="atable" id="log-tbl" style="width:100%;min-width:1080px"><tbody></tbody></table>
        </div>
      </div>
    </div>
  </div>

  <!-- PERFORMANCE VIEW -->
  <div class="view" id="view-performance">
    <div class="frame" style="flex:0 0 auto">
      <div class="frame-hdr" style="padding-bottom:4px">
        <span class="frame-title">Performance</span>
      </div>
      <div style="padding:0 18px 12px;display:flex;flex-wrap:wrap;align-items:center;gap:12px">
        <div class="radio-group" id="perf-granularity-group">
          <label><input type="radio" name="perf_granularity" value="daily" checked><span>Daily</span></label>
          <label><input type="radio" name="perf_granularity" value="weekly"><span>Weekly</span></label>
          <label><input type="radio" name="perf_granularity" value="monthly"><span>Monthly</span></label>
          <label><input type="radio" name="perf_granularity" value="cumulative"><span>Cumulative</span></label>
        </div>
        <span style="font-size:10px;color:#6b7280;text-transform:uppercase;letter-spacing:.8px">Profile</span>
        <select id="perf-profile-select" disabled style="background:#0d1117;color:#4a5568;border:1px solid #1e2430;
                border-radius:4px;padding:4px 8px;font-size:12px;outline:none" title="Enabled once paper-trading risk_profile data exists">
          <option value="live">Live</option>
        </select>
      </div>
    </div>

    <div class="frame" style="flex:0 0 auto">
      <div class="frame-hdr"><span class="frame-title">Profile Comparison</span>
        <span class="frame-sub">every profile &middot; ranked by net &middot; gross vs. net after fees (ignores the profile filter)</span></div>
      <div style="overflow-x:auto;padding:6px 18px 16px">
        <table class="atable" id="prof-cmp-tbl" style="width:100%;min-width:760px">
          <thead><tr>
            <th class="sortable" data-k="profile">Profile<span class="ar"></span></th>
            <th class="sortable" data-k="trades">Trades<span class="ar"></span></th>
            <th class="sortable" data-k="sessions">Sessions<span class="ar"></span></th>
            <th class="sortable" data-k="gross_pnl">Gross<span class="ar"></span></th>
            <th class="sortable" data-k="fees">Fees<span class="ar"></span></th>
            <th class="sortable" data-k="net_pnl">Net<span class="ar"></span></th>
            <th class="sortable" data-k="win_rate_pct">Win %<span class="ar"></span></th>
            <th class="sortable" data-k="expectancy">Expectancy<span class="ar"></span></th>
            <th class="sortable" data-k="profit_factor">Profit Factor<span class="ar"></span></th>
            <th class="sortable" data-k="max_drawdown">Max DD<span class="ar"></span></th>
          </tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>

    <div class="frame" style="flex:0 0 auto" id="arm-scorecard-frame">
      <div class="frame-hdr"><span class="frame-title">Arm Scorecard (Breakeven Identity)</span>
        <span class="frame-sub">P(clean OTM) - P(double stop) vs. the fees/credit bar, per forward-test stream</span></div>
      <div style="overflow-x:auto;padding:6px 18px 16px">
        <table class="atable" id="arm-scorecard-tbl" style="width:100%;min-width:820px">
          <thead><tr>
            <th>Arm</th><th>Trades</th><th>Sessions</th><th>Net P&amp;L</th><th>Win %</th>
            <th>Clean %</th><th>Double-Stop %</th><th>Breakeven Bar %</th><th>Margin %</th>
          </tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>

    <div class="frame" style="flex:0 0 auto" id="stop-policy-frame">
      <div class="frame-hdr"><span class="frame-title">Stop Policies (derived from <code>open</code>)</span>
        <span class="frame-sub">read-side computations over `open`'s recorded per-side paths, not separate entry streams</span></div>
      <div class="fee-grid" id="stop-policy-grid" style="padding:6px 18px 16px"></div>
      <div class="empty" id="stop-policy-empty" style="display:none;padding:0 18px 16px">No `open`-arm trades yet.</div>
    </div>

    <div class="frame" style="flex:0 0 auto">
      <div class="frame-hdr"><span class="frame-title">Risk-Adjusted Metrics</span>
        <span class="frame-sub" id="perf-overfit-note"></span></div>
      <div class="fee-grid" data-cp-reorder="fee-risk" data-cp-reorder-items=".fee-card" data-cp-reorder-label=".fee-lbl" style="padding:14px 18px 18px">
        <div class="fee-card"><div class="fee-lbl">Sharpe</div><div class="fee-val" id="rm-sharpe">&mdash;</div></div>
        <div class="fee-card"><div class="fee-lbl">Sortino</div><div class="fee-val" id="rm-sortino">&mdash;</div></div>
        <div class="fee-card"><div class="fee-lbl">Calmar</div><div class="fee-val" id="rm-calmar">&mdash;</div></div>
        <div class="fee-card"><div class="fee-lbl">Recovery Factor</div><div class="fee-val" id="rm-recovery">&mdash;</div></div>
      </div>
    </div>

    <div class="frame" style="flex:0 0 210px">
      <div class="frame-hdr" style="padding-bottom:4px"><span class="frame-title">Cumulative Equity ($100k base)</span></div>
      <div class="chart-wrap">
        <canvas id="perf-equity-canvas"></canvas>
        <div class="empty" id="perf-equity-empty" style="display:none;padding:18px 0">Insufficient history yet.</div>
      </div>
    </div>
    <div class="frame" style="flex:0 0 150px">
      <div class="frame-hdr" style="padding-bottom:4px"><span class="frame-title">Drawdown (Underwater)</span></div>
      <div class="chart-wrap">
        <canvas id="perf-drawdown-canvas"></canvas>
        <div class="empty" id="perf-drawdown-empty" style="display:none;padding:18px 0">Insufficient history yet.</div>
      </div>
    </div>

    <div class="frame" style="flex:0 0 210px" id="study-arms-frame">
      <div class="frame-hdr" style="padding-bottom:4px"><span class="frame-title">Study Arms</span>
        <span class="frame-sub">cumulative net P&amp;L &middot; the four forward-test streams (control/open/width-5/width-10) &middot; ladder tiers excluded (reference curves only)</span></div>
      <div class="ana-grid" id="study-arms-grid"></div>
      <div class="empty" id="study-arms-empty" style="display:none;padding:18px 0">No study-arm trades yet.</div>
    </div>

    <div class="frame" style="flex:1;min-height:0;overflow:hidden">
      <div class="ana-grid" data-cp-reorder="perf-ana" data-cp-reorder-items=".apanel" data-cp-reorder-label=".ptitle">
        <div class="apanel">
          <div class="ptitle">Per-Period Net P&amp;L</div>
          <div class="chart-wrap" style="padding:6px 0"><canvas id="perf-pnlbar-canvas"></canvas>
            <div class="empty" id="perf-pnlbar-empty" style="display:none">Insufficient history yet.</div></div>
        </div>
        <div class="apanel">
          <div class="ptitle">Win Rate Trend</div>
          <div class="chart-wrap" style="padding:6px 0"><canvas id="perf-winrate-canvas"></canvas>
            <div class="empty" id="perf-winrate-empty" style="display:none">Insufficient history yet.</div></div>
        </div>
        <div class="apanel">
          <div class="ptitle">Profit Factor Trend</div>
          <div class="chart-wrap" style="padding:6px 0"><canvas id="perf-pf-canvas"></canvas>
            <div class="empty" id="perf-pf-empty" style="display:none">Insufficient history yet.</div></div>
        </div>
        <div class="apanel">
          <div class="ptitle">Expectancy per Trade</div>
          <div class="chart-wrap" style="padding:6px 0"><canvas id="perf-expectancy-canvas"></canvas>
            <div class="empty" id="perf-expectancy-empty" style="display:none">Insufficient history yet.</div></div>
        </div>
        <div class="apanel">
          <div class="ptitle">Trade Count</div>
          <div class="chart-wrap" style="padding:6px 0"><canvas id="perf-count-canvas"></canvas>
            <div class="empty" id="perf-count-empty" style="display:none">Insufficient history yet.</div></div>
        </div>
        <div class="apanel">
          <div class="ptitle">Avg Win vs Avg Loss</div>
          <div class="chart-wrap" style="padding:6px 0"><canvas id="perf-winloss-canvas"></canvas>
            <div class="empty" id="perf-winloss-empty" style="display:none">Insufficient history yet.</div></div>
        </div>
      </div>
      <div class="apanel" style="margin-top:10px">
        <div class="ptitle">Per-Period Values</div>
        <div style="overflow-x:auto">
          <table class="atable" id="perf-values-tbl" style="width:100%;min-width:640px">
            <thead><tr>
              <th>Period</th><th>Trades</th><th>Net P&amp;L</th><th>Cumulative</th>
              <th>Win %</th><th>Profit Factor</th><th>Avg Win</th><th>Avg Loss</th>
            </tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
    </div>
  </div>

</main>
</div>

<script>
// ── state ────────────────────────────────────────────────────────────────────
let cache = null;
let nlvChart = null;
let cd = 30;

// ── nav ───────────────────────────────────────────────────────────────────────
document.querySelectorAll('.nav-item').forEach(el => {
  el.addEventListener('click', () => {
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    el.classList.add('active');
    document.getElementById('view-' + el.dataset.view).classList.add('active');
    if (el.dataset.view === 'history' && cache) renderHistory(cache);
    if (el.dataset.view === 'performance' && cache) renderPerformance(cache);
  });
});

// ── formatters ────────────────────────────────────────────────────────────────
function fPnl(v, cls) {
  if (v == null) return '<span class="dash">—</span>';
  const c = cls || (v > 0 ? 'pos' : v < 0 ? 'neg' : 'neu');
  const s = v > 0 ? '+' : v < 0 ? '-' : '';
  return '<span class="' + c + '">' + s + '$' + Math.abs(v).toFixed(2) + '</span>';
}
function fNum(v) {
  if (v == null) return '<span class="dash">—</span>';
  return v.toLocaleString();
}
function fWl(v) {
  if (v == null) return '<span class="dash">—</span>';
  const c = v >= 70 ? 'wh' : v >= 50 ? 'wm' : 'wl';
  return '<span class="' + c + '">' + v.toFixed(1) + '%</span>';
}
// Parse a stored timestamp (ISO, usually with a -04:00/-05:00 ET offset; space or 'T'
// separator; microsecond precision) into a Date. Trims sub-millisecond digits so Date
// can parse it, and preserves the offset so the instant is unambiguous.
function parseTs(ts) {
  if (!ts) return null;
  let s = String(ts).trim().replace(' ', 'T').replace(/(\\.\\d{3})\\d+/, '$1');
  const d = new Date(s);
  return isNaN(d.getTime()) ? null : d;
}
// Render an instant as Eastern wall-clock HH:MM regardless of the stored offset or the
// viewer's own timezone — so a value that ever arrives in UTC still reads correctly in ET.
function fTime(ts) {
  const d = parseTs(ts);
  if (!d) return '—';
  return d.toLocaleTimeString('en-US',
    { timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', hour12: false });
}
function fDateTimeET(ts) {
  const d = parseTs(ts);
  if (!d) return '';
  const date = d.toLocaleDateString('en-CA', { timeZone: 'America/New_York' });   // YYYY-MM-DD
  const time = d.toLocaleTimeString('en-US',
    { timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
  return date + ' ' + time;
}
function fMoney(v) {
  if (v == null) return '—';
  return (v >= 0 ? '+$' : '-$') + Math.abs(v).toFixed(2);
}
function bdg(b) {
  return '<span class="bdg bdg-' + (b.type || 'unknown') + '">' + b.label + '</span>';
}

// ── stats grid ────────────────────────────────────────────────────────────────
function renderStats(s) {
  const map = [
    ['today','today'], ['week','week'], ['month','month'], ['year','year'], ['all_time','all']
  ];
  map.forEach(([key, sfx]) => {
    const d = s[key] || {};
    document.getElementById('pnl-' + sfx).innerHTML = fPnl(d.net_pnl);
    document.getElementById('tr-'  + sfx).innerHTML = fNum(d.total_trades);
    document.getElementById('w-'   + sfx).innerHTML = d.wins  != null ? '<span class="pos">' + d.wins  + '</span>' : '<span class="dash">—</span>';
    document.getElementById('l-'   + sfx).innerHTML = d.losses != null ? '<span class="neg">' + d.losses + '</span>' : '<span class="dash">—</span>';
    document.getElementById('wl-'  + sfx).innerHTML = fWl(d.wl_ratio);
  });
}

// ── trades table ──────────────────────────────────────────────────────────────
function renderTrades(trades) {
  const tbody = document.getElementById('tbody');
  const lbl   = document.getElementById('trade-count');
  if (!trades || !trades.length) {
    tbody.innerHTML = '<tr><td colspan="12" class="empty">No trades today — agent is monitoring</td></tr>';
    lbl.textContent = '';
    return;
  }
  lbl.textContent = trades.length + ' trade' + (trades.length !== 1 ? 's' : '');
  tbody.innerHTML = trades.map(t => {
    const pc = t.put_credit  != null ? '$' + Number(t.put_credit).toFixed(2)  : '—';
    const cc = t.call_credit != null ? '$' + Number(t.call_credit).toFixed(2) : '—';
    const pnlCell = t.pnl != null
      ? '<td class="' + (t.pnl > 0 ? 'tppos' : 'tpneg') + '">' + fMoney(t.pnl) + '</td>'
      : '<td class="tpnil">—</td>';
    const tip = (t.ai_entry_reasoning || '')
      .replace(/&/g, '&amp;').replace(/"/g, '&quot;')
      .replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return '<tr title="' + tip + '">' +
      '<td>' + fTime(t.entry_time) + '</td>' +
      '<td style="color:#6b7280;font-size:10px">' + (t.symbol || '—') + '</td>' +
      '<td style="color:#8b5cf6;font-size:10px">' + (t.risk_profile || '—') + '</td>' +
      '<td class="tr">' + (t.wing_width != null ? t.wing_width : '—') + '</td>' +
      '<td class="tr">' + (t.put_strike  != null ? t.put_strike  : '—') + '</td>' +
      '<td class="tr">' + (t.call_strike != null ? t.call_strike : '—') + '</td>' +
      '<td class="tr">' + pc + '</td>' +
      '<td class="tr">' + cc + '</td>' +
      '<td class="tr tcredit">$' + Number(t.net_credit || 0).toFixed(2) + '</td>' +
      '<td>' + bdg(t.put_status)  + '</td>' +
      '<td>' + bdg(t.call_status) + '</td>' +
      pnlCell + '</tr>';
  }).join('');
}

// ── sidebar status ────────────────────────────────────────────────────────────
function renderStatus(d) {
  const dot   = document.getElementById('sdot');
  const label = document.getElementById('slabel');
  const meta  = document.getElementById('smeta');
  const loop  = d.last_loop;
  if (!loop) {
    dot.className = 'dot idle'; label.textContent = 'NO DATA';
    meta.textContent = 'No loop activity today'; return;
  }
  const t = new Date(String(loop.loop_time).replace(' ', 'T'));
  const ageMin = (Date.now() - t) / 60000;
  if (ageMin < 10) { dot.className = 'dot live'; label.textContent = 'LIVE'; }
  else             { dot.className = 'dot idle'; label.textContent = 'IDLE'; }
  const iv = loop.iv_rank != null ? ' · IV ' + Math.round(loop.iv_rank * 100) + '%' : '';
  const px = loop.underlying_price != null ? ' · $' + Number(loop.underlying_price).toFixed(2) : '';
  meta.textContent = 'Last: ' + fTime(loop.loop_time) + ' ET' + iv + px;
}

// ── NLV chart ─────────────────────────────────────────────────────────────────
function renderNlv(series) {
  const canvas = document.getElementById('nlv-canvas');
  const empty  = document.getElementById('nlv-empty');
  if (!series || !series.length) {
    canvas.style.display = 'none'; empty.style.display = 'block'; return;
  }
  canvas.style.display = 'block'; empty.style.display = 'none';
  const labels = series.map(r => r.date);
  const vals   = series.map(r => r.closing_nlv);
  const color  = vals[vals.length - 1] >= vals[0] ? '#00c896' : '#e8423a';
  if (nlvChart) {
    nlvChart.data.labels = labels;
    nlvChart.data.datasets[0].data = vals;
    nlvChart.data.datasets[0].borderColor = color;
    nlvChart.data.datasets[0].backgroundColor = color + '22';
    nlvChart.update(); return;
  }
  nlvChart = new Chart(canvas, {
    type: 'line',
    data: { labels, datasets: [{ label: 'NLV', data: vals, borderColor: color,
      backgroundColor: color + '22', borderWidth: 2, pointRadius: 3,
      pointHoverRadius: 5, fill: true, tension: 0.3 }] },
    options: {
      responsive: true, maintainAspectRatio: true,
      plugins: { legend: { display: false },
        tooltip: { callbacks: { label: ctx => {
          const r = series[ctx.dataIndex];
          return '$' + ctx.parsed.y.toLocaleString() + (r.net_pnl != null ? '  (day P&L: ' + fMoney(r.net_pnl) + ')' : '');
        }}}},
      scales: {
        x: { grid: { color: '#1e2430' }, ticks: { color: '#6b7280', font: { size: 10 } } },
        y: { grid: { color: '#1e2430' }, ticks: { color: '#6b7280', font: { size: 10 },
               callback: v => '$' + v.toLocaleString() } }
      }
    }
  });
}

// ── history panels ────────────────────────────────────────────────────────────
function renderHistory(d) {
  renderNlv(d.nlv_series);
  const a = d.analytics || {};

  // Session
  const sb = document.querySelector('#sess-tbl tbody');
  const sess = a.by_session || [];
  sb.innerHTML = !sess.length ? '<tr><td colspan="5" class="empty">No data</td></tr>'
    : sess.map(s => {
        const wr = s.total > 0 ? (s.wins / s.total * 100).toFixed(1) + '%' : '—';
        const wc = s.total > 0 ? (s.wins/s.total >= 0.7 ? 'wh' : s.wins/s.total >= 0.5 ? 'wm' : 'wl') : '';
        return '<tr><td>' + (s.session_quality || '—') + '</td><td>' + s.total + '</td>' +
          '<td class="pos">' + s.wins + '</td><td class="' + wc + '">' + wr + '</td>' +
          '<td>' + (s.avg_pnl != null ? fMoney(s.avg_pnl) : '—') + '</td></tr>';
      }).join('');

  // Exit
  const eb = document.querySelector('#exit-tbl tbody');
  const exits = a.by_exit || [];
  const etotal = exits.reduce((s, e) => s + (e.count || 0), 0);
  eb.innerHTML = !exits.length ? '<tr><td colspan="3" class="empty">No data</td></tr>'
    : exits.map(e => {
        const pct = etotal > 0 ? (e.count / etotal * 100).toFixed(1) + '%' : '—';
        return '<tr><td>' + (e.exit_reason || '—').replace(/_/g, ' ') +
          '</td><td>' + e.count + '</td><td>' + pct + '</td></tr>';
      }).join('');

  // IV
  const ib = document.querySelector('#iv-tbl tbody');
  const ivs = a.by_iv || [];
  ib.innerHTML = !ivs.length ? '<tr><td colspan="4" class="empty">No data</td></tr>'
    : ivs.map(iv => {
        const ac = iv.avg_pnl > 0 ? 'pos' : iv.avg_pnl < 0 ? 'neg' : '';
        return '<tr><td>' + iv.iv_bucket + '</td><td>' + iv.trades + '</td>' +
          '<td class="pos">' + iv.wins + '</td>' +
          '<td class="' + ac + '">' + (iv.avg_pnl != null ? fMoney(iv.avg_pnl) : '—') + '</td></tr>';
      }).join('');

  // Fees
  const f = a.fee_summary || {};
  document.getElementById('f-gross').textContent = f.gross_credit != null ? '$' + f.gross_credit.toFixed(2) : '—';
  document.getElementById('f-fees').textContent  = f.total_fees   != null ? '$' + f.total_fees.toFixed(2)   : '—';
  const fnet = document.getElementById('f-net');
  fnet.textContent  = f.net_pnl != null ? fMoney(f.net_pnl) : '—';
  fnet.className    = 'fee-val ' + (f.net_pnl >= 0 ? 'pos' : 'neg');
  document.getElementById('f-drag').textContent = f.fee_drag_pct != null ? f.fee_drag_pct.toFixed(1) + '%' : '—';

  // Signal-outcome breakdowns (avg NET P&L per pre-trade attribute)
  const sig = a.signals || {};
  renderSignalTable('sig-delta-tbl',  sig.by_delta);
  renderSignalTable('sig-wing-tbl',   sig.by_wing);
  renderSignalTable('sig-symbol-tbl', sig.by_symbol);
  renderSignalTable('sig-dow-tbl',    sig.by_dow);

  // Regime coverage (paper era only -- see _regime_coverage_panel's era-column guard)
  renderRegimeCoverage(a.regime || {});

  // Daily net P&L calendar heatmap
  renderCalHeat(a.daily_pnl || []);

  // Filterable trade log
  logRows = a.history || [];
  populateLogFilters(logRows);
  renderTradeLog();
}

// One signal breakdown table: Bucket | Trades | Sessions | Win % | Avg Net.
function renderSignalTable(tblId, rows) {
  const tb = document.querySelector('#' + tblId + ' tbody');
  if (!tb) return;
  rows = rows || [];
  tb.innerHTML = !rows.length ? '<tr><td colspan="5" class="empty">No data</td></tr>'
    : rows.map(r => {
        const ac = r.avg_pnl > 0 ? 'pos' : r.avg_pnl < 0 ? 'neg' : '';
        const wc = r.win_rate_pct == null ? '' : r.win_rate_pct >= 70 ? 'wh' : r.win_rate_pct >= 50 ? 'wm' : 'wl';
        return '<tr><td>' + (r.bucket || '—') + '</td><td>' + r.trades + '</td>' +
          '<td>' + (r.sessions != null ? r.sessions : '—') + '</td>' +
          '<td class="' + wc + '">' + (r.win_rate_pct != null ? r.win_rate_pct.toFixed(1) + '%' : '—') + '</td>' +
          '<td class="' + ac + '">' + (r.avg_pnl != null ? fMoney(r.avg_pnl) : '—') + '</td></tr>';
      }).join('');
}

// Regime coverage: tagged/untagged/degenerate per dimension, plus a by-bucket P&L table for
// every dimension that's both tagged and non-degenerate (server withholds degenerate ones).
function renderRegimeCoverage(reg) {
  const cov = (reg && reg.coverage) || {};
  const dims = cov.dimensions || {};
  const tb = document.querySelector('#regime-cov-tbl tbody');
  if (tb) {
    const keys = Object.keys(dims);
    tb.innerHTML = !keys.length ? '<tr><td colspan="7" class="empty">No paper-era data yet</td></tr>'
      : keys.map(k => {
          const d = dims[k];
          // Sessions, not rows, is what bounds a threshold re-cut: rows inside one session share
          // that session's market. An underpowered dimension is flagged on the row itself rather
          // than left to be inferred from a trade count that can read as large while resting on
          // two days. Distinct from degenerate — that one says re-cut, this one says wait.
          const eff = (d.effective_n != null ? d.effective_n : '—') + (d.daily_scale ? ' (daily)' : '');
          const warn = d.underpowered ? ' class="neg" title="too few sessions to cut a threshold against"' : '';
          return '<tr><td>' + k + '</td><td>' + d.tagged + '</td><td>' + d.untagged + '</td>' +
            '<td>' + (d.coverage_pct != null ? d.coverage_pct.toFixed(0) + '%' : '—') + '</td>' +
            '<td' + warn + '>' + (d.sessions != null ? d.sessions : '—') + '</td>' +
            '<td>' + eff + '</td>' +
            '<td>' + (d.degenerate ? 'yes' : 'no') + '</td></tr>';
        }).join('');
  }
  const host = document.getElementById('regime-by-dim');
  if (!host) return;
  const byDim = (reg && reg.by_dimension) || {};
  const dimKeys = Object.keys(byDim).filter(k => (byDim[k] || []).length);
  host.innerHTML = !dimKeys.length ? '' : dimKeys.map(dim => {
    const rows = byDim[dim];
    return '<div style="margin-top:8px"><div class="ptitle">' + dim + ' by bucket</div>' +
      '<table class="atable"><thead><tr><th>Bucket</th><th>Trades</th><th>Sessions</th>' +
      '<th>Win %</th><th>Net P&amp;L</th></tr></thead><tbody>' +
      rows.map(r => {
        const nc = (r.net_pnl || 0) >= 0 ? 'pos' : 'neg';
        return '<tr><td>' + r.bucket + '</td><td>' + r.trades + '</td><td>' + (r.sessions || 0) + '</td>' +
          '<td>' + (r.win_rate != null ? (r.win_rate * 100).toFixed(1) + '%' : '—') + '</td>' +
          '<td class="' + nc + '">' + fMoney(r.net_pnl) + '</td></tr>';
      }).join('') + '</tbody></table></div>';
  }).join('');
}

// ── calendar heatmap ──────────────────────────────────────────────────────────
// The shared week-column calendar (cherrypick.core.viz.cpCalHeat): Monday-anchored week
// columns, Mon–Fri only, and an empty weekday cell means 'no settled session' — a different
// thing from a flat day. Replaces this page's month-grid version (the audit counted two).
function renderCalHeat(days) {
  const host  = document.getElementById('cal-heat');
  const empty = document.getElementById('cal-heat-empty');
  if (!host) return;
  const drew = window.cpCalHeat(host, days, fMoney);
  if (empty) empty.style.display = drew ? 'none' : 'block';
}

// ── filterable trade log ──────────────────────────────────────────────────────
let logRows = [];
let logSort = { i: 0, dir: -1 };   // default: newest first (Date desc; per-column defaults below)

function populateLogFilters(rows) {
  const fill = (id, values, label) => {
    const sel = document.getElementById(id);
    if (!sel) return;
    const keep = sel.value;
    const uniq = [...new Set(values.filter(v => v != null && v !== ''))].sort();
    sel.innerHTML = '<option value="ALL">' + label + '</option>' +
      uniq.map(v => '<option value="' + v + '">' + String(v).replace(/_/g, ' ') + '</option>').join('');
    if ([...sel.options].some(o => o.value === keep)) sel.value = keep;
  };
  fill('lf-exit',    rows.map(r => r.exit_reason),     'All exits');
  fill('lf-session', rows.map(r => r.session_quality), 'All sessions');
}

function logNet(t) { return (t.pnl != null && t.fees != null) ? t.pnl - t.fees : t.pnl; }

function logFiltered() {
  const from = document.getElementById('lf-from').value;
  const to   = document.getElementById('lf-to').value;
  const out  = document.getElementById('lf-outcome').value;
  const ex   = document.getElementById('lf-exit').value;
  const ss   = document.getElementById('lf-session').value;
  const q    = document.getElementById('lf-search').value.trim().toLowerCase();

  let rows = logRows.filter(t => {
    if (from && (t.trade_date || '') < from) return false;
    if (to   && (t.trade_date || '') > to)   return false;
    if (ex !== 'ALL' && (t.exit_reason || '') !== ex) return false;
    if (ss !== 'ALL' && (t.session_quality || '') !== ss) return false;
    if (out !== 'ALL') {
      const n = logNet(t);
      const open = (t.status || '').toLowerCase() === 'open' || (t.status || '').toLowerCase() === 'partial' || n == null;
      if (out === 'open' && !open) return false;
      if (out === 'win'  && !(n != null && n > 0)) return false;
      if (out === 'loss' && !(n != null && n <= 0)) return false;
    }
    if (q) {
      const hay = [t.symbol, t.risk_profile, t.put_strike, t.call_strike, t.exit_reason]
        .map(v => String(v == null ? '' : v).toLowerCase()).join(' ');
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  return window.cpTableSort(rows, LOG_COLS, logSort);
}

// The trade log is the shared filterable table (cherrypick.core.viz.cpTable) with the sorting
// this page always had: opt-in per column, caller-owned {i, dir} state, cpTableSort's nulls-last
// ordering. Display markup stays here (badges, muted spans); raw sort values ride each column's
// `s` accessor. The pre-table filter bar above is unchanged -- logFiltered() feeds the table.
const LOG_COLS = [
  {h:'Date', f:t => (t.trade_date || '—').substring(5), s:t => t.trade_date, defDir:-1},
  {h:'Time', f:t => fTime(t.entry_time), s:t => t.entry_time, defDir:-1},
  {h:'Symbol', f:t => '<span style="color:#6b7280;font-size:10px">' + (t.symbol || '—') + '</span>',
   s:t => t.symbol},
  {h:'Profile', f:t => '<span style="color:#8b5cf6;font-size:10px">' + (t.risk_profile || '—') + '</span>',
   s:t => t.risk_profile},
  {h:'Width', num:true, f:t => t.wing_width != null ? t.wing_width : '—', s:t => t.wing_width},
  {h:'Put K', num:true, f:t => t.put_strike != null ? t.put_strike : '—', s:t => t.put_strike},
  {h:'Call K', num:true, f:t => t.call_strike != null ? t.call_strike : '—', s:t => t.call_strike},
  {h:'Put $', num:true, sortable:false,
   f:t => '<span style="color:#6b7280">$' + Number(t.put_credit || 0).toFixed(2) + '</span>'},
  {h:'Call $', num:true, sortable:false,
   f:t => '<span style="color:#6b7280">$' + Number(t.call_credit || 0).toFixed(2) + '</span>'},
  {h:'Net Cr', num:true, tone:() => 'tcredit',
   f:t => '$' + Number(t.net_credit || 0).toFixed(2), s:t => t.net_credit},
  {h:'Put', sortable:false, f:t => bdg(t.put_status)},
  {h:'Call', sortable:false, f:t => bdg(t.call_status)},
  {h:'Gross', num:true, f:t => t.pnl != null ? fMoney(t.pnl) : '—', s:t => t.pnl,
   tone:t => t.pnl != null ? (t.pnl >= 0 ? 'pos' : 'neg') : ''},
  {h:'Net', num:true, f:t => { const n = logNet(t); return n != null ? fMoney(n) : '—'; },
   s:t => logNet(t), tone:t => { const n = logNet(t); return n != null ? (n >= 0 ? 'pos' : 'neg') : ''; }},
  {h:'Exit', f:t => '<span style="color:#6b7280;font-size:10px">'
   + (t.exit_reason || '—').replace(/_/g, ' ') + '</span>', s:t => t.exit_reason},
];

function renderTradeLog() {
  const tbl = document.getElementById('log-tbl');
  const cnt = document.getElementById('log-count');
  if (!tbl) return;
  const rows = logFiltered();
  cnt.textContent = rows.length + ' of ' + logRows.length + ' trade' + (logRows.length !== 1 ? 's' : '');
  window.cpTable(tbl, LOG_COLS, rows, 'No trades match the filters', {
    sort: logSort,
    onSort: i => {
      logSort = { i, dir: logSort.i === i ? -logSort.dir : (LOG_COLS[i].defDir || 1) };
      renderTradeLog();
    },
    rowTitle: t => t.ai_entry_reasoning || '',
  });
}

// Reflect the active sort key/direction in a table's header arrows.
function syncSortIndicators(tblId, sort) {
  document.querySelectorAll('#' + tblId + ' th.sortable').forEach(th => {
    const ar = th.querySelector('.ar');
    if (th.dataset.k === sort.k) { th.classList.add('sorted'); if (ar) ar.textContent = sort.dir < 0 ? '▼' : '▲'; }
    else { th.classList.remove('sorted'); if (ar) ar.textContent = ''; }
  });
}

// ── symbol selectors ─────────────────────────────────────────────────────────
// Populated once from the traded-symbols list the server reports (config.json's
// `symbols`) so the main-view symbol filter matches what the suite trades.
let symbolsPopulated = false;
function populateSymbolSelectors(symbols) {
  if (symbolsPopulated || !symbols || !symbols.length) return;
  symbolsPopulated = true;
  const mainSel = document.getElementById('main-symbol-select');
  symbols.forEach(sym => {
    const o1 = document.createElement('option'); o1.value = sym; o1.textContent = sym;
    mainSel.appendChild(o1);
  });
}

// ── profile selector (paper-trading dashboard) ──────────────────────────────────
// Unlike symbols (fixed from config.json, populated once), profiles can appear one at a
// time as each accrues its first trade — so this re-populates whenever the server's
// profile list actually changes, rather than a single-shot guard, while preserving the
// current selection if it's still valid.
// Two synced profile selectors — the global one in the Today header (peer of the symbol
// filter) and the one in the Performance view — are populated identically so a choice in
// either scopes the whole dashboard. An "All profiles" option leads (the blended default).
const PROFILE_SELECT_IDS = ['main-profile-select', 'perf-profile-select'];
let lastProfilesKey = '';
function populateProfileSelector(profiles) {
  if (!profiles || !profiles.length) return;
  const key = profiles.join(',');
  if (key === lastProfilesKey) return;
  lastProfilesKey = key;
  // Both selects are kept in sync, so either one carries the current selection.
  const current = document.getElementById('main-profile-select').value;
  PROFILE_SELECT_IDS.forEach(id => {
    const sel = document.getElementById(id);
    if (!sel) return;
    sel.innerHTML = '';
    const all = document.createElement('option');
    all.value = 'ALL'; all.textContent = 'All profiles';
    sel.appendChild(all);
    profiles.forEach(p => {
      const o = document.createElement('option');
      o.value = p;
      o.textContent = p === 'live' ? 'Live' : p.charAt(0).toUpperCase() + p.slice(1);
      sel.appendChild(o);
    });
    sel.value = (current === 'ALL' || profiles.includes(current)) ? current : 'ALL';
    // Only real multi-profile data (paper trades tagged with a risk_profile) makes the
    // selector meaningful — a live dashboard or an empty paper DB stays on the inert
    // single "All profiles" placeholder, disabled, as before this feature existed.
    sel.disabled = profiles.length <= 1;
    sel.title = sel.disabled ? 'Enabled once paper-trading risk_profile data exists' : '';
  });
}

// ── performance view ─────────────────────────────────────────────────────────
let perfEquityChart=null, perfDrawdownChart=null, perfPnlChart=null, perfWinRateChart=null,
    perfPfChart=null, perfExpectancyChart=null, perfCountChart=null, perfWinLossChart=null;

function _perfGuard(series, canvasId, emptyId, minLen) {
  const canvas = document.getElementById(canvasId);
  const empty = document.getElementById(emptyId);
  const ok = series && series.length >= (minLen || 2);
  if (canvas) canvas.style.display = ok ? 'block' : 'none';
  if (empty) empty.style.display = ok ? 'none' : 'block';
  return ok;
}

function _perfSeries(d) {
  const g = document.querySelector('input[name="perf_granularity"]:checked')?.value || 'daily';
  const perf = d.performance || {};
  // "Cumulative" reuses the daily series (finest granularity) — its own charts read
  // cumulative_pnl/equity/drawdown rather than switching the underlying bucketing.
  return g === 'cumulative' ? (perf.daily || []) : (perf[g] || []);
}

function renderPerformance(d) {
  const perf = d.performance || {};
  const g = document.querySelector('input[name="perf_granularity"]:checked')?.value || 'daily';
  const series = _perfSeries(d);
  const isCumulative = g === 'cumulative';

  renderProfileCompare(perf.by_profile || []);
  renderArmScorecard(perf.arm_scorecard || []);
  renderStopPolicyCard(perf.stop_policy_card || []);
  renderRiskMetrics(perf.risk_metrics || {});
  renderPerfEquity(series);
  renderPerfDrawdown(series);
  // Per-period bar/trend charts are granularity-specific and not meaningful in the
  // cumulative view (which is about the running curve, not discrete periods) — hide
  // them there rather than showing per-day bars mislabeled as "cumulative."
  ['perf-pnlbar', 'perf-winrate', 'perf-pf', 'perf-expectancy', 'perf-count', 'perf-winloss'].forEach(id => {
    const frame = document.getElementById(id + '-canvas')?.closest('.apanel');
    if (frame) frame.style.display = isCumulative ? 'none' : '';
  });
  if (!isCumulative) {
    renderPerfPnlBar(series);
    renderPerfWinRate(series);
    renderPerfProfitFactor(series);
    renderPerfExpectancy(series);
    renderPerfCount(series);
    renderPerfWinLoss(series);
  }
  renderPerfTable(series);
  renderStudyArms(d.study_arms || {});
}

// ── study arms (forced-sampling experiment arms) ───────────────────────────────
let studyArmCharts = {};   // symbol -> Chart instance, kept across renders (update in place)
// Colours are per arm NAME, so an arm family that is retired takes its colour with it and a new
// one only needs an entry here. The width-* keys went when the wing-width study was retired.
const STUDY_ARM_COLORS = {
  control: '#2f81f7', open: '#00c896', 'width-5': '#f5a623', 'width-10': '#8b5cf6',
};

function renderStudyArms(ws) {
  const arms = ws.arms || [];
  const symbols = Object.keys(ws.symbols || {});
  const grid = document.getElementById('study-arms-grid');
  const empty = document.getElementById('study-arms-empty');
  const hasAnyData = symbols.some(sym => arms.some(arm => (ws.symbols[sym][arm] || []).length > 0));
  if (!grid) return;
  grid.style.display = hasAnyData ? '' : 'none';
  if (empty) empty.style.display = hasAnyData ? 'none' : 'block';
  if (!hasAnyData) return;

  symbols.forEach(sym => {
    let canvas = document.getElementById('study-arms-canvas-' + sym);
    if (!canvas) {
      const panel = document.createElement('div');
      panel.className = 'apanel';
      panel.innerHTML = '<div class="ptitle">' + sym + '</div>' +
        '<div class="chart-wrap" style="padding:6px 0"><canvas id="study-arms-canvas-' + sym + '"></canvas></div>';
      grid.appendChild(panel);
      canvas = document.getElementById('study-arms-canvas-' + sym);
    }
    const bySym = ws.symbols[sym] || {};
    // Union of periods across this symbol's arms (an arm can be missing a day another arm has,
    // e.g. its floor refused every entry that day) — spanGaps lets each line skip its own nulls.
    const periods = [...new Set(arms.flatMap(arm => (bySym[arm] || []).map(b => b.period)))].sort();
    const datasets = arms.map(arm => {
      const byPeriod = Object.fromEntries((bySym[arm] || []).map(b => [b.period, b.cumulative_pnl]));
      return {
        label: arm, data: periods.map(p => byPeriod[p] ?? null), spanGaps: true,
        borderColor: STUDY_ARM_COLORS[arm] || '#6b7280',
        backgroundColor: (STUDY_ARM_COLORS[arm] || '#6b7280') + '22',
        borderWidth: 2, pointRadius: periods.length > 60 ? 0 : 2, pointHoverRadius: 5, tension: 0.2,
      };
    });
    const opts = _baseOpts();
    opts.plugins.legend = { display: true, labels: { color: '#8b949e', boxWidth: 10, font: { size: 10 } } };
    opts.plugins.tooltip.callbacks = { label: ctx => ctx.dataset.label + ': ' +
      (ctx.parsed.y == null ? '—' : '$' + ctx.parsed.y.toFixed(2)) };
    opts.scales.y.ticks.callback = v => '$' + v.toLocaleString();
    if (studyArmCharts[sym]) {
      studyArmCharts[sym].data.labels = periods;
      studyArmCharts[sym].data.datasets = datasets;
      studyArmCharts[sym].update();
    } else {
      studyArmCharts[sym] = new Chart(canvas, { type: 'line', data: { labels: periods, datasets }, options: opts });
    }
  });
}

// ── profile comparison ────────────────────────────────────────────────────────
let profCmpRows = [];
let profCmpSort = { k: 'net_pnl', dir: -1 };   // default: best net first

function renderProfileCompare(rows) {
  profCmpRows = rows || [];
  const tb = document.querySelector('#prof-cmp-tbl tbody');
  if (!tb) return;
  if (!profCmpRows.length) {
    tb.innerHTML = '<tr><td colspan="10" class="empty">No profile-tagged trades yet</td></tr>';
    return;
  }
  const k = profCmpSort.k, dir = profCmpSort.dir;
  const sorted = [...profCmpRows].sort((a, b) => {
    const av = a[k], bv = b[k];
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * dir;
    return String(av).localeCompare(String(bv)) * dir;
  });
  tb.innerHTML = sorted.map(r => {
    const gc = r.gross_pnl >= 0 ? 'pos' : 'neg';
    const nc = r.net_pnl   >= 0 ? 'pos' : 'neg';
    const ec = r.expectancy == null ? '' : r.expectancy >= 0 ? 'pos' : 'neg';
    const wc = r.win_rate_pct == null ? '' : r.win_rate_pct >= 70 ? 'wh' : r.win_rate_pct >= 50 ? 'wm' : 'wl';
    return '<tr>' +
      '<td style="color:#8b5cf6">' + (r.profile || '—') + '</td>' +
      '<td class="tr">' + r.trades + '</td>' +
      '<td class="tr">' + (r.sessions != null ? r.sessions : '—') + '</td>' +
      '<td class="tr ' + gc + '">' + fMoney(r.gross_pnl) + '</td>' +
      '<td class="tr" style="color:#6b7280">$' + Number(r.fees || 0).toFixed(2) + '</td>' +
      '<td class="tr ' + nc + '">' + fMoney(r.net_pnl) + '</td>' +
      '<td class="tr ' + wc + '">' + (r.win_rate_pct != null ? r.win_rate_pct.toFixed(1) + '%' : '—') + '</td>' +
      '<td class="tr ' + ec + '">' + (r.expectancy != null ? fMoney(r.expectancy) : '—') + '</td>' +
      '<td class="tr">' + (r.profit_factor != null ? r.profit_factor.toFixed(2) : '—') + '</td>' +
      '<td class="tr neg">' + (r.max_drawdown ? '-$' + Number(r.max_drawdown).toFixed(2) : '$0.00') + '</td>' +
      '</tr>';
  }).join('');
  syncSortIndicators('prof-cmp-tbl', profCmpSort);
}

// ── arm scorecard (breakeven identity, per forward-test stream) ────────────────
function renderArmScorecard(rows) {
  const tb = document.querySelector('#arm-scorecard-tbl tbody');
  if (!tb) return;
  if (!rows.length) {
    tb.innerHTML = '<tr><td colspan="9" class="empty">No paper-era resolved trades yet</td></tr>';
    return;
  }
  tb.innerHTML = rows.map(r => {
    const nc = (r.net_pnl || 0) >= 0 ? 'pos' : 'neg';
    const mc = r.margin_pct == null ? '' : r.margin_pct >= 0 ? 'pos' : 'neg';
    const pct = v => v == null ? '—' : v.toFixed(1) + '%';
    return '<tr>' +
      '<td style="color:#8b5cf6">' + (r.arm || '—') + '</td>' +
      '<td class="tr">' + r.trades + '</td>' +
      '<td class="tr">' + (r.sessions != null ? r.sessions : '—') + '</td>' +
      '<td class="tr ' + nc + '">' + fMoney(r.net_pnl) + '</td>' +
      '<td class="tr">' + (r.win_rate != null ? (r.win_rate * 100).toFixed(1) + '%' : '—') + '</td>' +
      '<td class="tr">' + pct(r.clean_pct) + '</td>' +
      '<td class="tr">' + pct(r.double_stop_pct) + '</td>' +
      '<td class="tr">' + pct(r.breakeven_bar_pct) + '</td>' +
      '<td class="tr ' + mc + '">' + (r.margin_pct == null ? '—' : (r.margin_pct >= 0 ? '+' : '') + r.margin_pct.toFixed(1) + '%') + '</td>' +
      '</tr>';
  }).join('');
}

// ── stop-policy card (derived from `open`'s recorded paths) ────────────────────
function renderStopPolicyCard(rows) {
  const grid = document.getElementById('stop-policy-grid');
  const empty = document.getElementById('stop-policy-empty');
  if (!grid) return;
  const hasData = rows.some(r => r.derivable > 0);
  grid.style.display = hasData ? '' : 'none';
  if (empty) empty.style.display = hasData ? 'none' : 'block';
  if (!hasData) { grid.innerHTML = ''; return; }
  grid.innerHTML = rows.map(r => {
    const dc = r.delta == null ? '' : r.delta >= 0 ? 'pos' : 'neg';
    return '<div class="fee-card">' +
      '<div class="fee-lbl">' + r.policy + '</div>' +
      '<div class="fee-val ' + dc + '">' + (r.delta == null ? '—' : (r.delta >= 0 ? '+' : '') + fMoney(r.delta)) + '</div>' +
      '<div style="font-size:9px;color:#6b7280;margin-top:3px">' +
        (r.derivable || 0) + '/' + (r.trades || 0) + ' derivable &middot; derived ' +
        (r.derived_net_pnl != null ? fMoney(r.derived_net_pnl) : '—') + ' vs actual ' +
        (r.actual_net_pnl != null ? fMoney(r.actual_net_pnl) : '—') +
      '</div></div>';
  }).join('');
}

function renderRiskMetrics(rm) {
  const fmt = v => v == null ? '—' : v.toFixed(2);
  document.getElementById('rm-sharpe').textContent = fmt(rm.sharpe);
  document.getElementById('rm-sortino').textContent = fmt(rm.sortino);
  document.getElementById('rm-calmar').textContent = fmt(rm.calmar);
  document.getElementById('rm-recovery').textContent = fmt(rm.recovery_factor);
  document.getElementById('rm-sharpe').className = 'fee-val' + (rm.sharpe != null && rm.sharpe < 0 ? ' neg' : '');
  const note = document.getElementById('perf-overfit-note');
  if (rm.sharpe_overfit_flag) {
    note.textContent = 'Sharpe > 3 — likely overfit on a thin sample, not a stronger result';
  } else if (rm.sample_size != null && rm.sample_size < 30) {
    note.textContent = 'Sample size ' + rm.sample_size + ' day(s) — below the significance floor used for graduation';
  } else {
    note.textContent = '';
  }
}

// Fresh Chart.js base options for the Performance charts — a factory (new object each
// call) so per-chart tweaks (tooltip/tick callbacks, y-min/max, legend) never bleed across
// charts. Dark theme matches the NLV chart above (#1e2430 grid, #6b7280 ticks).
function _baseOpts() {
  return {
    responsive: true, maintainAspectRatio: true,
    plugins: {
      legend: { display: false },
      tooltip: {},
    },
    scales: {
      x: { grid: { color: '#1e2430' }, ticks: { color: '#6b7280', font: { size: 9 }, maxRotation: 0, autoSkip: true } },
      y: { grid: { color: '#1e2430' }, ticks: { color: '#6b7280', font: { size: 9 } } },
    },
  };
}

function renderPerfEquity(series) {
  if (!_perfGuard(series, 'perf-equity-canvas', 'perf-equity-empty')) return;
  const labels = series.map(b => b.period);
  const vals = series.map(b => b.equity);
  const color = vals[vals.length - 1] >= vals[0] ? '#00c896' : '#e8423a';
  if (perfEquityChart) {
    perfEquityChart.data.labels = labels;
    perfEquityChart.data.datasets[0].data = vals;
    perfEquityChart.data.datasets[0].borderColor = color;
    perfEquityChart.data.datasets[0].backgroundColor = color + '22';
    perfEquityChart.update(); return;
  }
  const opts = _baseOpts();
  opts.plugins.tooltip.callbacks = { label: ctx => '$' + ctx.parsed.y.toLocaleString() };
  opts.scales.y.ticks.callback = v => '$' + v.toLocaleString();
  perfEquityChart = new Chart(document.getElementById('perf-equity-canvas'), {
    type: 'line',
    data: { labels, datasets: [{ data: vals, borderColor: color, backgroundColor: color + '22',
      borderWidth: 2, pointRadius: series.length > 60 ? 0 : 3, pointHoverRadius: 5, fill: true, tension: 0.25 }] },
    options: opts,
  });
}

function renderPerfDrawdown(series) {
  if (!_perfGuard(series, 'perf-drawdown-canvas', 'perf-drawdown-empty')) return;
  const labels = series.map(b => b.period);
  const vals = series.map(b => -b.drawdown); // negative so the fill visually sits "underwater"
  if (perfDrawdownChart) {
    perfDrawdownChart.data.labels = labels;
    perfDrawdownChart.data.datasets[0].data = vals;
    perfDrawdownChart.update(); return;
  }
  const opts = _baseOpts();
  opts.plugins.tooltip.callbacks = { label: ctx => '-$' + Math.abs(ctx.parsed.y).toLocaleString() };
  opts.scales.y.ticks.callback = v => '$' + v.toLocaleString();
  perfDrawdownChart = new Chart(document.getElementById('perf-drawdown-canvas'), {
    type: 'line',
    data: { labels, datasets: [{ data: vals, borderColor: '#e8423a', backgroundColor: '#e8423a1a',
      borderWidth: 2, pointRadius: 0, fill: true, tension: 0 }] },
    options: opts,
  });
}

function renderPerfPnlBar(series) {
  if (!_perfGuard(series, 'perf-pnlbar-canvas', 'perf-pnlbar-empty')) return;
  const labels = series.map(b => b.period);
  const vals = series.map(b => b.net_pnl);
  const colors = vals.map(v => v >= 0 ? '#00c896' : '#e8423a');
  if (perfPnlChart) {
    perfPnlChart.data.labels = labels;
    perfPnlChart.data.datasets[0].data = vals;
    perfPnlChart.data.datasets[0].backgroundColor = colors;
    perfPnlChart.update(); return;
  }
  const opts = _baseOpts();
  opts.plugins.tooltip.callbacks = { label: ctx => '$' + ctx.parsed.y.toFixed(2) };
  opts.scales.y.ticks.callback = v => '$' + v.toLocaleString();
  perfPnlChart = new Chart(document.getElementById('perf-pnlbar-canvas'), {
    type: 'bar',
    data: { labels, datasets: [{ data: vals, backgroundColor: colors, borderRadius: 4, maxBarThickness: 24 }] },
    options: opts,
  });
}

function renderPerfWinRate(series) {
  if (!_perfGuard(series, 'perf-winrate-canvas', 'perf-winrate-empty')) return;
  const labels = series.map(b => b.period);
  const vals = series.map(b => b.win_rate_pct);
  if (perfWinRateChart) {
    perfWinRateChart.data.labels = labels;
    perfWinRateChart.data.datasets[0].data = vals;
    perfWinRateChart.update(); return;
  }
  const opts = _baseOpts();
  opts.plugins.tooltip.callbacks = { label: ctx => ctx.parsed.y == null ? '—' : ctx.parsed.y.toFixed(1) + '%' };
  opts.scales.y.min = 0; opts.scales.y.max = 100;
  opts.scales.y.ticks.callback = v => v + '%';
  perfWinRateChart = new Chart(document.getElementById('perf-winrate-canvas'), {
    type: 'line',
    data: { labels, datasets: [{ data: vals, borderColor: '#00c896', backgroundColor: '#00c89622',
      borderWidth: 2, pointRadius: 3, spanGaps: true, tension: 0.25 }] },
    options: opts,
  });
}

function renderPerfProfitFactor(series) {
  if (!_perfGuard(series, 'perf-pf-canvas', 'perf-pf-empty')) return;
  const labels = series.map(b => b.period);
  const vals = series.map(b => b.profit_factor);
  if (perfPfChart) {
    perfPfChart.data.labels = labels;
    perfPfChart.data.datasets[0].data = vals;
    perfPfChart.update(); return;
  }
  const opts = _baseOpts();
  opts.plugins.tooltip.callbacks = { label: ctx => ctx.parsed.y == null ? '—' : ctx.parsed.y.toFixed(2) };
  perfPfChart = new Chart(document.getElementById('perf-pf-canvas'), {
    type: 'line',
    data: { labels, datasets: [{ data: vals, borderColor: '#00c896', backgroundColor: '#00c89622',
      borderWidth: 2, pointRadius: 3, spanGaps: true, tension: 0.25 }] },
    options: opts,
  });
}

function renderPerfExpectancy(series) {
  if (!_perfGuard(series, 'perf-expectancy-canvas', 'perf-expectancy-empty')) return;
  const labels = series.map(b => b.period);
  const vals = series.map(b => b.expectancy_per_trade);
  const colors = vals.map(v => v == null ? '#4a5568' : v >= 0 ? '#00c896' : '#e8423a');
  if (perfExpectancyChart) {
    perfExpectancyChart.data.labels = labels;
    perfExpectancyChart.data.datasets[0].data = vals;
    perfExpectancyChart.data.datasets[0].backgroundColor = colors;
    perfExpectancyChart.update(); return;
  }
  const opts = _baseOpts();
  opts.plugins.tooltip.callbacks = { label: ctx => ctx.parsed.y == null ? '—' : '$' + ctx.parsed.y.toFixed(2) };
  perfExpectancyChart = new Chart(document.getElementById('perf-expectancy-canvas'), {
    type: 'bar',
    data: { labels, datasets: [{ data: vals, backgroundColor: colors, borderRadius: 4, maxBarThickness: 24 }] },
    options: opts,
  });
}

function renderPerfCount(series) {
  if (!_perfGuard(series, 'perf-count-canvas', 'perf-count-empty')) return;
  const labels = series.map(b => b.period);
  const vals = series.map(b => b.trades);
  if (perfCountChart) {
    perfCountChart.data.labels = labels;
    perfCountChart.data.datasets[0].data = vals;
    perfCountChart.update(); return;
  }
  const opts = _baseOpts();
  perfCountChart = new Chart(document.getElementById('perf-count-canvas'), {
    type: 'bar',
    data: { labels, datasets: [{ data: vals, backgroundColor: '#4a5568', borderRadius: 4, maxBarThickness: 24 }] },
    options: opts,
  });
}

function renderPerfWinLoss(series) {
  if (!_perfGuard(series, 'perf-winloss-canvas', 'perf-winloss-empty')) return;
  const labels = series.map(b => b.period);
  const wins = series.map(b => b.avg_win);
  const losses = series.map(b => b.avg_loss);
  if (perfWinLossChart) {
    perfWinLossChart.data.labels = labels;
    perfWinLossChart.data.datasets[0].data = wins;
    perfWinLossChart.data.datasets[1].data = losses;
    perfWinLossChart.update(); return;
  }
  const opts = _baseOpts();
  // Two series (avg win / avg loss) — a legend is required per the dashboard's
  // dataviz-skill guidance for >=2 series; a single-series chart elsewhere skips it.
  opts.plugins.legend = { display: true, labels: { color: '#8b949e', boxWidth: 10, font: { size: 10 } } };
  opts.plugins.tooltip.callbacks = { label: ctx => ctx.dataset.label + ': ' + (ctx.parsed.y == null ? '—' : '$' + ctx.parsed.y.toFixed(2)) };
  perfWinLossChart = new Chart(document.getElementById('perf-winloss-canvas'), {
    type: 'bar',
    data: { labels, datasets: [
      { label: 'Avg Win', data: wins, backgroundColor: '#00c896', borderRadius: 4, maxBarThickness: 18 },
      { label: 'Avg Loss', data: losses, backgroundColor: '#e8423a', borderRadius: 4, maxBarThickness: 18 },
    ] },
    options: opts,
  });
}

function renderPerfTable(series) {
  const tbody = document.querySelector('#perf-values-tbl tbody');
  if (!series || !series.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty">No data</td></tr>';
    return;
  }
  tbody.innerHTML = series.slice().reverse().map(b => {
    return '<tr><td>' + b.period + '</td><td>' + b.trades + '</td>' +
      '<td>' + fPnl(b.net_pnl) + '</td><td>' + fPnl(b.cumulative_pnl) + '</td>' +
      '<td>' + (b.win_rate_pct == null ? '—' : b.win_rate_pct.toFixed(1) + '%') + '</td>' +
      '<td>' + (b.profit_factor == null ? '—' : b.profit_factor.toFixed(2)) + '</td>' +
      '<td>' + (b.avg_win == null ? '—' : '$' + b.avg_win.toFixed(2)) + '</td>' +
      '<td>' + (b.avg_loss == null ? '—' : '$' + b.avg_loss.toFixed(2)) + '</td></tr>';
  }).join('');
}

document.querySelectorAll('input[name="perf_granularity"]').forEach(el =>
  el.addEventListener('change', () => { if (cache) renderPerformance(cache); }));

// ── trade-log filters + sortable headers (wired once) ────────────────────────
['lf-from','lf-to','lf-outcome','lf-exit','lf-session','lf-search'].forEach(id => {
  const el = document.getElementById(id);
  if (el) el.addEventListener('input', renderTradeLog);
});
const lfClear = document.getElementById('lf-clear');
if (lfClear) lfClear.addEventListener('click', () => {
  ['lf-from','lf-to','lf-search'].forEach(id => { const e = document.getElementById(id); if (e) e.value = ''; });
  ['lf-outcome','lf-exit','lf-session'].forEach(id => { const e = document.getElementById(id); if (e) e.value = 'ALL'; });
  renderTradeLog();
});
document.querySelectorAll('#prof-cmp-tbl th.sortable').forEach(th => th.addEventListener('click', () => {
  const k = th.dataset.k;
  profCmpSort = { k, dir: profCmpSort.k === k ? -profCmpSort.dir : (k === 'profile' ? 1 : -1) };
  renderProfileCompare(profCmpRows);
}));

// ── render all ────────────────────────────────────────────────────────────────
function renderAll(d) {
  cache = d;
  document.getElementById('disc-banner').style.display = 'none';
  document.getElementById('as-of').textContent = d.as_of
    ? fDateTimeET(d.as_of) + ' ET' : '';
  populateSymbolSelectors(d.symbols);
  populateProfileSelector((d.performance && d.performance.profiles) || []);
  renderStats(d.stats || {});
  renderTrades(d.trades || []);
  renderStatus(d);
  if (document.getElementById('view-history').classList.contains('active')) renderHistory(d);
  if (document.getElementById('view-performance').classList.contains('active')) renderPerformance(d);
}

// ── fetch ─────────────────────────────────────────────────────────────────────
async function fetchData() {
  try {
    const sym = document.getElementById('main-symbol-select').value || 'ALL';
    const prof = document.getElementById('main-profile-select').value || 'ALL';
    const r = await fetch('/api/data?symbol=' + encodeURIComponent(sym) + '&profile=' + encodeURIComponent(prof));
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    if (d.ok === false) {
      document.getElementById('tbody').innerHTML =
        '<tr><td colspan="11" class="empty">' + (d.error || 'Error loading data') + '</td></tr>';
      return;
    }
    renderAll(d);
  } catch(e) {
    // Distinguish an unreachable server (fetch/network failure) from a client-side render
    // bug (data arrived but rendering threw) — reporting the latter as "DISCONNECTED" is
    // misleading and hides real dashboard errors.
    const reach = (e instanceof TypeError) || /^HTTP /.test(e && e.message || '');
    const banner = document.getElementById('disc-banner');
    banner.style.display = 'block';
    banner.textContent = reach
      ? '● DISCONNECTED — unable to reach dashboard server'
      : '● DASHBOARD ERROR — ' + (e && e.message || e);
    document.getElementById('sdot').className = 'dot err';
    document.getElementById('slabel').textContent = reach ? 'DISCONNECTED' : 'RENDER ERROR';
    console.error('fetchData/render error:', e);
    // A brief server blip (e.g. a restart) shouldn't strand the page on the banner for a
    // full refresh cycle — retry soon so it reconnects within a few seconds, not up to 30.
    if (reach) cd = 4;
  }
}

document.getElementById('main-symbol-select').addEventListener('change', fetchData);
// A profile choice in either select mirrors to the other, then reloads — one filter, two entry points.
function onProfileChange(e) {
  const v = e.target.value;
  PROFILE_SELECT_IDS.forEach(id => {
    const s = document.getElementById(id);
    if (s && s.value !== v) s.value = v;
  });
  fetchData();
}
PROFILE_SELECT_IDS.forEach(id =>
  document.getElementById(id).addEventListener('change', onProfileChange));

// ── auto-refresh ──────────────────────────────────────────────────────────────
fetchData();
setInterval(() => {
  cd--;
  document.getElementById('scountdown').textContent = 'Refresh in ' + cd + 's';
  if (cd <= 0) { cd = 30; fetchData(); }
}, 1000);

// drag-to-reorder lives in cherrypick.core.viz.REORDER_JS now (the suite's one copy);
// groups are declared with data-cp-reorder attributes on the grids above.
%%CP_REORDER_JS%%
</script>
</body>
</html>"""

# The shared drag-to-reorder implementation is baked in at import time (the template stays one
# literal; the token keeps the JS out of an f-string's brace-escaping).
HTML = HTML.replace("%%CP_REORDER_JS%%", viz.REORDER_JS)
HTML = HTML.replace("%%CP_CAL_HEAT_STYLE%%", viz.CAL_HEAT_STYLE + viz.TABLE_STYLE)
HTML = HTML.replace("</script>\n</body>", viz.CAL_HEAT_JS + viz.TABLE_JS + "</script>\n</body>", 1)


# ── HTTP handler ──────────────────────────────────────────────────────────────


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            page = HTML
            if _MODE == "paper":
                page = page.replace(
                    "<!--MODE_BADGE-->", '<span class="mode-badge">Paper Mode — Simulated</span>'
                )
                page = page.replace("<title>MEICAgent</title>", "<title>MEICAgent — Paper</title>")
            else:
                page = page.replace("<!--MODE_BADGE-->", "")
            body = page.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            # Never cache: the page template is baked into this process, so a browser holding a cached
            # copy shows a stale layout after a restart until a hard refresh.
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/api/data"):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            sym = (qs.get("symbol") or [None])[0]
            prof = (qs.get("profile") or [None])[0]
            if prof and prof.upper() == "ALL":
                prof = None
            try:
                result = _build_api_data(sym, prof)
            except Exception as exc:
                result = {"ok": False, "error": str(exc)}
            body = json.dumps(result, default=str).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *_):
        pass  # suppress request logs


class _ThreadingServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


# ── Entry point ───────────────────────────────────────────────────────────────


def _resolve_mode_defaults(
    mode: str, db_arg: str | None, port_arg: int | None, default_db_path: str = _DB_PATH
) -> tuple[str, int]:
    """Pure resolution of (db_path, port) from --mode/--db/--port. Extracted out of main()
    so the default-resolution logic (the only part of --mode/--db/--port with real branching)
    is unit-testable without spinning up a real HTTP server or parsing sys.argv.

    `default_db_path` is a parameter (not a direct read of the module-level _DB_PATH) so
    tests can pin it explicitly rather than depending on import-time global state.
    """
    if db_arg:
        db_path = db_arg
    elif mode == "paper":
        db_path = _PAPER_DB_PATH
    else:
        db_path = default_db_path
    port = port_arg or (5051 if mode == "paper" else 5050)
    return db_path, port


def main():
    global _DB_PATH, _MODE
    parser = argparse.ArgumentParser(description="MEICAgent Dashboard")
    parser.add_argument(
        "--mode",
        choices=["live", "paper"],
        default="live",
        help="'paper' points the dashboard at the data home's paper_trades.db "
        "and defaults the port to 5051, so it can run alongside the live "
        "dashboard (port 5050) without conflict.",
    )
    parser.add_argument(
        "--port", type=int, default=None, help="Overrides the mode-based default (5050 live / 5051 paper)."
    )
    parser.add_argument("--db", default=None, help="Overrides the mode-based default DB path.")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Don't open a browser tab on start (for headless/background launches, "
        "e.g. the suite's `/serve-dashboard all`).",
    )
    args = parser.parse_args()

    _MODE = args.mode
    _DB_PATH, port = _resolve_mode_defaults(args.mode, args.db, args.port)
    # `python dashboard.py` with no args resolves to (today's default meic_trades.db path, 5050)
    # — byte-identical to pre-paper-mode behavior.
    # Check if already running (the suite's one bind-probe, cherrypick.core.viz)
    if viz.port_in_use(port):
        if args.no_browser:
            print(f"Dashboard already running at http://localhost:{port}.")
        else:
            print(f"Dashboard already running at http://localhost:{port} — opening browser.")
            webbrowser.open(f"http://localhost:{port}")
        sys.exit(0)

    try:
        # Loopback-only: this API has no authentication, and serves trade P&L, credit
        # amounts, strikes, and AI reasoning text — binding to all interfaces would expose
        # it to the whole LAN (or the internet, if the port is forwarded).
        server = _ThreadingServer(("127.0.0.1", port), _Handler)
    except OSError as exc:
        print(f"ERROR: Cannot bind to port {port}: {exc}")
        print(f"Check what is using it: netstat -ano | findstr :{port}")
        sys.exit(1)

    url = f"http://localhost:{port}"
    print(f"MEICAgent Dashboard  ->  {url}")
    print("Press Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=[url]).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
        server.shutdown()


if __name__ == "__main__":
    main()
