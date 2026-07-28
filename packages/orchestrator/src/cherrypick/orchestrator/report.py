"""Unified cross-module paper P&L report (read-only).

Reads each enabled module's paper DB — files only, no broker, no network, no trading — and produces one
unified P&L summary across MEICAgent (`ic_trades`) and EarningsAgent (`trades`), broken down by module
and, within each module, by risk profile. A read-mostly reporting surface for the walk-away user: the
first slice of the reporting/alerting hub (later: the Part-14 status dashboard and drift/stall alerts).

Three paper-DB schemas are wired, dispatched by `paper.trade_schema` (same registry idea as
trade_notifier), each yielding a normalized closed-trade record `{profile, symbol, strategy, net_pnl}`:
  - "meic_ic"  : MEICAgent's `ic_trades`; closed = exit_time set; net = pnl - fees; tag = risk_profile.
  - "earnings" : EarningsAgent's `trades`; closed = closed_at set; net = pnl - entry_cost - exit_cost;
                 tag = profile.
  - "fly_book" : cherrypick-flies' `fly_positions`; closed = status 'settled'; net = gross_pnl - fees;
                 tag = arm. Note this reader is P&L only — the module's book-level floor and the price
                 band it holds over live in `fly_books` and are NOT summarizable as a per-trade number,
                 so `run.py status` remains the place to read them.

The per-profile grouping uses cherrypick.core.profiles.compare_profiles (group closed trades by their
attribution tag, summarize each group) via the src/_core submodule — bootstrapped onto sys.path in
this package's __init__.

Alongside the closed-trade P&L, each module also reports its **open** positions carried past the close
(`_OPEN_READERS`): capital at risk, no realized P&L. Only the multi-day earnings module carries
overnight; the 0DTE modules settle within the session and report an empty overnight view. This is a
separate registry feeding only report/digest — not the four closed-trade adapter registries.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone

from cherrypick.core import db as core_db
from cherrypick.core.profiles import compare_profiles

from . import config as cfgmod

# Untagged sentinels match each module's own schema convention (see cherrypick.core.profiles.attribution_tag).
_MEIC_UNTAGGED = "unassigned"
_EARNINGS_UNTAGGED = "default"
_FLIES_UNTAGGED = "unassigned"


# Read-only connection, shared suite-wide (calibrate/reconcile import it from here).
# core.db.connect_ro percent-escapes the path, so '?'/'#'/'%' in a directory name can't
# silently change the URI's meaning — the old f"file:{path}?mode=ro" copy could.
_connect_ro = core_db.connect_ro


# --------------------------------------------------------------------------- per-schema readers
def _session_from_epoch(closed_at) -> str:
    """Trading-session date (ISO) from an epoch-seconds close time; '' if unparseable."""
    try:
        return date.fromtimestamp(float(closed_at)).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _table_cols(conn, table: str) -> set:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _meic_closed(conn) -> list[dict]:
    # slippage_dollars (cumulative modeled slippage on the trade's fills) rides along when
    # the column exists; older DBs degrade to slippage=None rather than failing the reader.
    # Slippage is linear in the modeled fraction, so net at a stressed 2x fraction is
    # exactly net - slippage — the cost-sensitivity column every reading carries.
    cols = _table_cols(conn, "ic_trades")
    has_slip = "slippage_dollars" in cols
    slip_col = ", slippage_dollars" if has_slip else ""
    has_capital = {"wing_width", "net_credit", "quantity"} <= cols
    cap_cols = ", wing_width, net_credit, quantity" if has_capital else ""
    mult_col = ", dollar_multiplier" if "dollar_multiplier" in cols else ""
    rows = conn.execute(
        f"SELECT symbol, risk_profile, pnl, fees, exit_time{slip_col}{cap_cols}{mult_col} "
        "FROM ic_trades WHERE exit_time IS NOT NULL"
    ).fetchall()

    def _capital(r):
        # An IC's capital at risk = (wing width - credit received) x multiplier x quantity.
        # This is what makes return-on-capital comparable: a 2-wide and a 10-wide IC stop
        # weighing equally. None (not zero) when the row can't support the computation.
        if not has_capital or r["wing_width"] is None:
            return None
        mult = (r["dollar_multiplier"] if mult_col and r["dollar_multiplier"] else 100.0)
        cap = (float(r["wing_width"]) - float(r["net_credit"] or 0.0)) * mult * (r["quantity"] or 1)
        return round(cap, 2) if cap > 0 else None

    return [
        {
            "profile": r["risk_profile"] or _MEIC_UNTAGGED,
            "symbol": r["symbol"],
            "strategy": None,
            # gross = spread P&L (already at the modeled fill prices); cost = exchange fees.
            "gross_pnl": (r["pnl"] or 0.0),
            "cost": (r["fees"] or 0.0),
            "net_pnl": (r["pnl"] or 0.0) - (r["fees"] or 0.0),
            "slippage": (r["slippage_dollars"] if has_slip else None),
            "capital": _capital(r),
            # Session date for calibration (distinct-days count); ISO date prefix of exit_time.
            "session": (r["exit_time"] or "")[:10],
        }
        for r in rows
    ]


def _earnings_closed(conn) -> list[dict]:
    cols = _table_cols(conn, "trades")
    has_slip = "entry_slippage" in cols and "exit_slippage" in cols
    slip_cols = ", entry_slippage, exit_slippage" if has_slip else ""
    has_capital = "capital_at_risk" in cols
    cap_col = ", capital_at_risk" if has_capital else ""
    rows = conn.execute(
        f"SELECT symbol, profile, strategy, pnl, entry_cost, exit_cost, closed_at{slip_cols}{cap_col} "
        "FROM trades WHERE closed_at IS NOT NULL"
    ).fetchall()

    def _slip(r):
        if not has_slip or (r["entry_slippage"] is None and r["exit_slippage"] is None):
            return None  # pre-instrumentation row — unknown, not zero
        return (r["entry_slippage"] or 0.0) + (r["exit_slippage"] or 0.0)

    return [
        {
            "profile": r["profile"] or _EARNINGS_UNTAGGED,
            "symbol": r["symbol"],
            "strategy": r["strategy"],
            # gross = mid-priced spread P&L; cost = commission + pass-through + slippage.
            "gross_pnl": (r["pnl"] or 0.0),
            "cost": (r["entry_cost"] or 0.0) + (r["exit_cost"] or 0.0),
            "net_pnl": (r["pnl"] or 0.0) - (r["entry_cost"] or 0.0) - (r["exit_cost"] or 0.0),
            "slippage": _slip(r),
            # sizing.compute_position_size's defined max loss, stored at entry.
            "capital": (r["capital_at_risk"] if has_capital else None),
            "session": _session_from_epoch(r["closed_at"]),
        }
        for r in rows
    ]


def _flies_closed(conn) -> list[dict]:
    """cherrypick-flies' `fly_positions`; closed = settled. The attribution tag is the ARM, because
    comparing the arms is the entire point of the module — a per-symbol view would hide the one
    contrast the experiment exists to draw. Read straight off the row rather than against a known list,
    so an arm added on the module side (wide_wing joined gex / time_window / control on 2026-07-27)
    appears here without a change on this side."""
    rows = conn.execute(
        "SELECT symbol, arm, entry_mode, gross_pnl, fees, trade_date "
        "FROM fly_positions WHERE status = 'settled'"
    ).fetchall()
    return [
        {
            "profile": r["arm"] or _FLIES_UNTAGGED,
            "symbol": r["symbol"],
            # legged vs outright: the two entry mechanisms perform differently enough that
            # aggregating them would average away the finding.
            "strategy": r["entry_mode"],
            "gross_pnl": (r["gross_pnl"] or 0.0),
            "cost": (r["fees"] or 0.0),
            "net_pnl": (r["gross_pnl"] or 0.0) - (r["fees"] or 0.0),
            # Slippage capture deferred for flies: its decisive metrics are floor/completion
            # based, and its fills already price the haircut into gross_pnl. Unknown != zero,
            # and likewise capital: a legged book's risk depends on completion state.
            "slippage": None,
            "capital": None,
            "session": r["trade_date"] or "",
        }
        for r in rows
    ]


_READERS = {"meic_ic": _meic_closed, "earnings": _earnings_closed, "fly_book": _flies_closed}


# --------------------------------------------------------------------------- per-schema open readers
# The closed readers above answer "what settled" (realized P&L). These answer "what is still on the
# book, carried past the close" (capital at risk, no P&L yet). Only a multi-day strategy carries
# overnight; the two 0DTE modules open and settle inside one session, so their overnight view is
# structurally empty — see _no_overnight. Kept as a SEPARATE registry from _READERS on purpose: it
# feeds only the report/digest, not calibrate/reconcile/notifier, so it is not one of the "four
# registries extended together" the module CLAUDE.md warns about.
def _no_overnight(conn) -> list[dict]:
    """MEIC and flies are 0DTE: every position opens and cash-settles within the same session, so
    nothing is deliberately carried overnight. A position still open at digest time is a settlement
    lag for the watchdog to flag, not overnight risk for this roll-up — so the carried-overnight
    view is empty here by construction rather than by a query that could accidentally surface an
    unsettled 0DTE position as though it were an earnings hold."""
    return []


def _earnings_open(conn) -> list[dict]:
    """EarningsAgent's `trades` still open (closed_at NULL): defined-risk earnings structures entered
    one afternoon and carried over the earnings event to the next morning. `capital_at_risk` is the
    known max loss set at entry — the honest overnight-exposure number, since these have no realized
    P&L until they settle. `session` is the OPEN session (opened_at), so the digest for a day shows
    what that day put on, matching the earnings module's own 'Opened this session' EOD section."""
    rows = conn.execute(
        "SELECT symbol, profile, strategy, capital_at_risk, opened_at "
        "FROM trades WHERE closed_at IS NULL"
    ).fetchall()
    return [
        {
            "profile": r["profile"] or _EARNINGS_UNTAGGED,
            "symbol": r["symbol"],
            "strategy": r["strategy"],
            "capital_at_risk": (r["capital_at_risk"] or 0.0),
            "session": _session_from_epoch(r["opened_at"]),
        }
        for r in rows
    ]


_OPEN_READERS = {"meic_ic": _no_overnight, "earnings": _earnings_open, "fly_book": _no_overnight}


def _summarize_open(records: list[dict]) -> dict:
    """Overnight-exposure stats over normalized open-position records: how many positions are carried
    and the summed defined max loss, plus a per-symbol count for the digest's Names column. No P&L —
    an open position has none yet, which is the whole reason it needs a separate surface from the
    closed-trade P&L summary."""
    by_symbol: dict[str, int] = {}
    for r in records:
        by_symbol[r["symbol"]] = by_symbol.get(r["symbol"], 0) + 1
    return {
        "positions": len(records),
        "capital_at_risk": round(sum(r.get("capital_at_risk") or 0.0 for r in records), 2),
        "by_symbol": dict(sorted(by_symbol.items())),
    }


# --------------------------------------------------------------------------- summarization
def _summarize(records: list[dict]) -> dict:
    """P&L stats over normalized closed-trade records. net_pnl is cost-adjusted; gross_pnl is
    before costs and cost is the total modeled cost (MEIC fees; earnings commission+slippage).
    win_rate is on net P&L; gross_win_rate is on gross -- the gap shows how many trades have edge
    before costs but not after (the signal at 1-contract sizing, where cost dominates)."""
    net = [r["net_pnl"] for r in records]
    gross = [r.get("gross_pnl", r["net_pnl"]) for r in records]
    cost = [r.get("cost", 0.0) for r in records]
    n = len(net)
    wins = [p for p in net if p > 0]
    gross_wins = [p for p in gross if p > 0]
    # Cost sensitivity: slippage is linear in the modeled fraction, so doubling it costs
    # exactly the recorded slippage again. Only rows carrying the datum contribute —
    # slippage_coverage says how much of the sample that is (pre-instrumentation rows
    # are unknown, not zero, and must not silently pass the stress unscathed).
    slips = [r.get("slippage") for r in records]
    known_slips = [s for s in slips if s is not None]
    return {
        "trades": n,
        "gross_pnl": round(sum(gross), 2),
        "cost": round(sum(cost), 2),
        "net_pnl": round(sum(net), 2),
        "wins": len(wins),
        "losses": n - len(wins),
        "win_rate": round(len(wins) / n, 4) if n else None,
        "gross_win_rate": round(len(gross_wins) / n, 4) if n else None,
        "avg_pnl": round(sum(net) / n, 2) if n else None,
        "slippage": round(sum(known_slips), 2),
        "slippage_coverage": len(known_slips),
        "net_pnl_2x_slippage": round(sum(net) - sum(known_slips), 2),
    }


# --------------------------------------------------------------------------- entrypoint
def run(cfg: dict | None = None, session: str | None = None) -> dict:
    """Unified paper P&L across all enabled modules. Read-only; never writes or trades.

    With `session` (an ISO 'YYYY-MM-DD'), restrict to trades whose settlement session matches — the
    per-schema readers already emit a `session` per record, so a daily/EOD view is just a filter over
    the same normalized records the all-time view uses. `session=None` keeps the cumulative behavior.
    """
    cfg = cfg or cfgmod.load_config()
    epoch = cfgmod.data_epoch(cfg)
    modules_out: dict[str, dict] = {}
    all_records: list[dict] = []
    all_open: list[dict] = []

    for name, mcfg in cfgmod.enabled_modules(cfg).items():
        paper = mcfg.get("paper", {})
        schema = paper.get("trade_schema", "meic_ic")
        reader = _READERS.get(schema)
        db_path = cfgmod.paper_db_path(mcfg, name)

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
            conn.close()
            continue
        # Open positions are a secondary, best-effort read: an older DB missing the open-position
        # columns must degrade to "no overnight view" without failing the module's realized P&L.
        try:
            open_reader = _OPEN_READERS.get(schema)
            open_records = open_reader(conn) if open_reader else []
        except sqlite3.Error:
            open_records = []
        finally:
            conn.close()

        if session is not None:
            records = [r for r in records if r.get("session") == session]
            open_records = [r for r in open_records if r.get("session") == session]

        all_records.extend(records)
        all_open.extend(open_records)
        modules_out[name] = {
            "ok": True,
            "schema": schema,
            **_summarize(records),
            "by_profile": compare_profiles(records, tag_key="profile", summarize=_summarize),
            # Positions opened this session and carried past the close (empty for the 0DTE modules).
            "open": _summarize_open(open_records),
        }
        # Descriptive only: the report never rewrites history, but when an epoch is declared it
        # says how much of this module's history predates it (rows a promotion reading excludes).
        if epoch is not None:
            modules_out[name]["pre_epoch_trades"] = sum(
                1 for r in records if not r.get("session") or r["session"] < epoch["date"]
            )

    suite = _summarize(all_records)
    # Nested inside suite (not a sibling) so the digest's suite.get("open") finds it next to the
    # suite P&L it belongs with.
    suite["open"] = _summarize_open(all_open)
    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "session": session,
        "data_epoch": epoch,
        "modules": modules_out,
        "suite": suite,
    }


def latest_session(cfg: dict | None = None) -> str | None:
    """Most recent settlement-session date (ISO) with any paper trade across enabled modules, or
    None if there are none. Lets the EOD view fall back off an empty current day (e.g. overnight,
    when the ET date has rolled to a session that hasn't traded yet) to the last real session."""
    cfg = cfg or cfgmod.load_config()
    latest: str | None = None
    for name, mcfg in cfgmod.enabled_modules(cfg).items():
        schema = mcfg.get("paper", {}).get("trade_schema", "meic_ic")
        reader = _READERS.get(schema)
        db_path = cfgmod.paper_db_path(mcfg, name)
        if reader is None or not db_path.exists():
            continue
        conn = _connect_ro(db_path)
        try:
            records = reader(conn)
        except sqlite3.Error:
            continue
        finally:
            conn.close()
        for r in records:
            s = r.get("session")
            if s and (latest is None or s > latest):
                latest = s
    return latest
