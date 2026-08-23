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
attribution tag, summarize each group).

Alongside the closed-trade P&L, each module also reports its **open** positions carried past the close
(`_OPEN_READERS`): capital at risk, no realized P&L. Only the multi-day earnings module carries
overnight; the 0DTE modules settle within the session and report an empty overnight view. This is a
separate registry feeding only report/digest — not the four closed-trade adapter registries.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from cherrypick.core import ledgers as _ledgers
from cherrypick.core.profiles import compare_profiles

from . import config as cfgmod

# Untagged sentinels match each module's own schema convention (see cherrypick.core.profiles.attribution_tag).
# The per-schema readers moved to cherrypick.core.ledgers so the review package reads modules
# through the same normalisation this report does, rather than becoming a third implementation of
# rules that had already been written twice and already drifted. These aliases keep the private
# names the rest of this module -- and calibrate.py, which imports report._READERS -- already use.
MEIC_UNTAGGED = _ledgers.MEIC_UNTAGGED
EARNINGS_UNTAGGED = _ledgers.EARNINGS_UNTAGGED
FLIES_UNTAGGED = _ledgers.FLIES_UNTAGGED
_MEIC_UNTAGGED = _ledgers.MEIC_UNTAGGED
_EARNINGS_UNTAGGED = _ledgers.EARNINGS_UNTAGGED
_FLIES_UNTAGGED = _ledgers.FLIES_UNTAGGED
_connect_ro = _ledgers.connect_ro
_session_from_epoch = _ledgers.session_from_epoch
_meic_closed = _ledgers._meic_closed
_earnings_closed = _ledgers._earnings_closed
_flies_closed = _ledgers._flies_closed
_no_overnight = _ledgers._no_overnight
_earnings_open = _ledgers._earnings_open
_READERS = _ledgers.READERS
_OPEN_READERS = _ledgers.OPEN_READERS


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
def run(
    cfg: dict | None = None,
    session: str | None = None,
    session_range: tuple[str | None, str | None] | None = None,
) -> dict:
    """Unified paper P&L across all enabled modules. Read-only; never writes or trades.

    With `session` (an ISO 'YYYY-MM-DD'), restrict to trades whose settlement session matches.
    With `session_range` ((start, end), inclusive, either side None for unbounded), restrict to
    the range AND include a `daily` series — per-session net P&L with a per-module split — the
    feed for a suite equity curve. Bounds are pushed into the per-schema readers' SQL where the
    session date is exact in SQL (MEIC, flies); the tz-sensitive earnings reader stays
    Python-filtered. `session=None, session_range=None` keeps the cumulative behavior.
    """
    if session is not None and session_range is not None:
        raise ValueError("pass session OR session_range, not both")
    lo, hi = (session, session) if session is not None else (session_range or (None, None))

    cfg = cfgmod.load_config() if cfg is None else cfg  # an explicit {} must stay {}, not fall back
    epoch = cfgmod.data_epoch(cfg)
    modules_out: dict[str, dict] = {}
    all_records: list[dict] = []
    all_open: list[dict] = []
    daily: dict[str, dict] = {}

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
            records = reader(conn, start=lo, end=hi)
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

        # Python-side bound: the belt for the reader whose session date can't be bounded in
        # SQL (earnings), and for open records. Exact same semantics as the SQL pushdown.
        if lo is not None or hi is not None:

            def _in_bounds(r):
                s = r.get("session") or ""
                return (lo is None or s >= lo) and (hi is None or s <= hi)

            records = [r for r in records if _in_bounds(r)]
            open_records = [r for r in open_records if _in_bounds(r)]

        if session_range is not None:
            for r in records:
                day = daily.setdefault(r.get("session") or "", {"net_pnl": 0.0, "trades": 0, "by_module": {}})
                day["net_pnl"] += r["net_pnl"]
                day["trades"] += 1
                day["by_module"][name] = round(day["by_module"].get(name, 0.0) + r["net_pnl"], 2)

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
    out = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "session": session,
        "data_epoch": epoch,
        "modules": modules_out,
        "suite": suite,
    }
    if session_range is not None:
        out["session_range"] = list(session_range)
        out["daily"] = [
            {
                "session": s,
                "net_pnl": round(d["net_pnl"], 2),
                "trades": d["trades"],
                "by_module": d["by_module"],
            }
            for s, d in sorted(daily.items())
        ]
    return out


def live_run(cfg: dict | None = None, session: str | None = None) -> dict:
    """LIVE P&L across modules that declare a `live_db` -- the phase-5 isolation seam.

    Deliberately a separate function from `run()`, not a flag on it: `run()` is the promotion
    feed (calibrate reads through it and must only ever see paper), so live stays out of it by
    construction rather than by an argument someone could pass. Same schema readers, same
    net-of-cost summaries, pointed at the live ledgers; every envelope is tagged `live: true`
    and carries no data_epoch (that is a paper-measurement concept). A module without a
    `live_db`, or whose file doesn't exist yet, reports "no live ledger" -- expected, not an
    error, for a suite that hasn't gone live. Read-only, files only, never the broker (the
    broker-truth live view is `reconcile`).
    """
    lo = hi = session
    cfg = cfgmod.load_config() if cfg is None else cfg  # an explicit {} must stay {}, not fall back
    modules_out: dict[str, dict] = {}
    all_records: list[dict] = []
    all_open: list[dict] = []
    for name, mcfg in cfgmod.enabled_modules(cfg).items():
        db_path = cfgmod.live_db_path(mcfg, name)
        if db_path is None:
            modules_out[name] = {"ok": False, "live": True, "reason": "no live_db configured"}
            continue
        schema = (mcfg.get("paper", {}) or {}).get("trade_schema", "meic_ic")
        reader = _READERS.get(schema)
        if reader is None:
            modules_out[name] = {"ok": False, "live": True, "reason": f"unknown schema {schema!r}"}
            continue
        if not db_path.exists():
            modules_out[name] = {"ok": False, "live": True, "reason": "no live ledger yet"}
            continue
        conn = _connect_ro(db_path)
        try:
            records = reader(conn, start=lo, end=hi)
        except sqlite3.Error as exc:
            modules_out[name] = {"ok": False, "live": True, "reason": f"read failed: {exc}"}
            conn.close()
            continue
        try:
            open_reader = _OPEN_READERS.get(schema)
            open_records = open_reader(conn) if open_reader else []
        except sqlite3.Error:
            open_records = []
        finally:
            conn.close()
        if lo is not None:
            records = [r for r in records if (r.get("session") or "") == lo]
        all_records.extend(records)
        all_open.extend(open_records)
        modules_out[name] = {
            "ok": True,
            "live": True,
            "schema": schema,
            **_summarize(records),
            "open": _summarize_open(open_records),
        }
    suite = _summarize(all_records)
    suite["open"] = _summarize_open(all_open)
    return {
        "ok": True,
        "live": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "session": session,
        "modules": modules_out,
        "suite": suite,
    }


# One MAX() per schema — latest_session used to re-run every full reader over every DB
# just to compute a max date (a whole extra DB pass per overnight dashboard render).
# Earnings' MAX is over the raw epoch, converted via the same tz-correct helper the
# reader uses, so the two can't disagree on a session boundary.
_LATEST_SQL = {
    "meic_ic": "SELECT MAX(substr(exit_time, 1, 10)) FROM ic_trades WHERE exit_time IS NOT NULL",
    "earnings": "SELECT MAX(closed_at) FROM trades WHERE closed_at IS NOT NULL",
    "fly_book": "SELECT MAX(trade_date) FROM fly_positions WHERE status = 'settled'",
    "dc_week": "SELECT MAX(closed_session) FROM dc_positions WHERE closed_session IS NOT NULL",
    "pmcc_99": "SELECT MAX(closed_session) FROM pmcc_positions WHERE closed_session IS NOT NULL",
    "curve_vx": "SELECT MAX(closed_session) FROM curve_positions WHERE closed_session IS NOT NULL",
    "bwb_132": "SELECT MAX(closed_session) FROM bwb_positions WHERE closed_session IS NOT NULL",
}


def latest_session(cfg: dict | None = None) -> str | None:
    """Most recent settlement-session date (ISO) with any paper trade across enabled modules, or
    None if there are none. Lets the EOD view fall back off an empty current day (e.g. overnight,
    when the ET date has rolled to a session that hasn't traded yet) to the last real session."""
    cfg = cfgmod.load_config() if cfg is None else cfg  # an explicit {} must stay {}, not fall back
    latest: str | None = None
    for name, mcfg in cfgmod.enabled_modules(cfg).items():
        schema = mcfg.get("paper", {}).get("trade_schema", "meic_ic")
        sql = _LATEST_SQL.get(schema)
        db_path = cfgmod.paper_db_path(mcfg, name)
        if sql is None or not db_path.exists():
            continue
        conn = _connect_ro(db_path)
        try:
            value = conn.execute(sql).fetchone()[0]
        except sqlite3.Error:
            continue
        finally:
            conn.close()
        if value is None:
            continue
        s = _session_from_epoch(value) if schema == "earnings" else str(value)
        if s and (latest is None or s > latest):
            latest = s
    return latest
