"""Notify paper-trade entries and exits (e.g. to Discord) as they happen.

Reads each module's paper DB — files only, no broker — finds trades that opened or closed since the last
check, and pushes one concise line per event through the notifier. Distinct from the watchdog's health
alerts: each trade is a one-shot event tracked by an id watermark (not deduped or re-notified). On first
activation the watermark is seeded to the current DB state, so pre-existing paper trades aren't
backfilled as a burst.

Called both by the dedicated fast trade-notify task (low latency, ~2 min) and as a fallback on each
watchdog tick; the per-schema id watermark keeps either path from re-sending the same event, and the
state file is written atomically so overlapping runs can't corrupt it.

Two paper-DB schemas are wired, dispatched by `paper.trade_schema`:
  - "meic_ic"  : MEICAgent's `ic_trades` table (integer id, exit_time watermark).
  - "earnings" : EarningsAgent's `trades` table (text order_id key, opened_at/closed_at timestamps).

Trade lines go to the `notify.trade_channels` set (default log + discord) rather than every channel, so
frequent paper fills don't spam desktop toasts.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from cherrypick.notify import Notifier

from . import config as cfgmod

_STATE = cfgmod.STATE_DIR / "trade_notify.json"
_ID_CAP = 4000  # bound the remembered-id lists (per schema, per direction)


def _load_state() -> dict:
    try:
        return json.loads(_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    # Atomic replace: the trade-notify task and the watchdog tick can both call run() at once, so a
    # plain truncate-then-write could leave a half-written state file if they overlap.
    cfgmod.ensure_dirs()
    tmp = _STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, _STATE)


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# --------------------------------------------------------------------------- MEIC ic_trades schema
def _meic_seed(conn) -> dict:
    max_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM ic_trades").fetchone()[0]
    exited = [r[0] for r in conn.execute("SELECT id FROM ic_trades WHERE exit_time IS NOT NULL")]
    return {"last_entry_id": max_id, "notified_exit_ids": exited}


def _meic_new_entries(conn, last_entry_id: int) -> list:
    return conn.execute(
        "SELECT id, symbol, risk_profile, put_strike, call_strike, wing_width, net_credit, quantity "
        "FROM ic_trades WHERE id > ? AND status NOT IN ('pending', 'cancelled', 'partial_entry') "
        "ORDER BY id",
        (last_entry_id,),
    ).fetchall()


def _meic_new_exits(conn, notified_ids: set) -> list:
    rows = conn.execute(
        "SELECT id, symbol, risk_profile, exit_reason, pnl FROM ic_trades WHERE exit_time IS NOT NULL"
    ).fetchall()
    return [r for r in rows if r["id"] not in notified_ids]


def _fmt_meic_entry(r) -> str:
    return (
        f"\U0001f7e2 MEIC paper ENTRY — {r['symbol']} "
        f"{r['put_strike']:.0f}P/{r['call_strike']:.0f}C w{r['wing_width']:.0f} "
        f"x{r['quantity']} credit ${r['net_credit']:.2f} [{r['risk_profile']}]"
    )


def _fmt_meic_exit(r) -> str:
    pnl = r["pnl"]
    pnl_str = f"${pnl:+.2f}" if pnl is not None else "n/a"
    return (
        f"\U0001f534 MEIC paper EXIT — {r['symbol']} [{r['risk_profile']}] "
        f"{r['exit_reason'] or 'closed'}, P&L {pnl_str}"
    )


def _meic_process(conn, st: dict, notifier: Notifier, name: str) -> dict:
    entries = _meic_new_entries(conn, st["last_entry_id"])
    for r in entries:
        notifier.notify("INFO", f"trade.{name}.entry.{r['id']}", "Paper entry", _fmt_meic_entry(r))
        st["last_entry_id"] = max(st["last_entry_id"], r["id"])

    notified = set(st.get("notified_exit_ids", []))
    exits = _meic_new_exits(conn, notified)
    for r in exits:
        notifier.notify("INFO", f"trade.{name}.exit.{r['id']}", "Paper exit", _fmt_meic_exit(r))
        notified.add(r["id"])
    st["notified_exit_ids"] = sorted(notified)[-_ID_CAP:]

    return {"entries_notified": len(entries), "exits_notified": len(exits)}


# --------------------------------------------------------------------------- Earnings trades schema
# EarningsAgent's `trades` table keys on a text `order_id` (no integer id) and timestamps opens/closes
# with `opened_at`/`closed_at` (epoch seconds). We watermark by remembering notified order_ids per
# direction — earnings paper volume is a handful of trades a day, so the capped id lists never grow big.
def _earnings_seed(conn) -> dict:
    rows = conn.execute("SELECT order_id, closed_at FROM trades").fetchall()
    return {
        "notified_entry_ids": [r["order_id"] for r in rows],
        "notified_exit_ids": [r["order_id"] for r in rows if r["closed_at"] is not None],
    }


def _earnings_new_entries(conn, notified_ids: set) -> list:
    rows = conn.execute(
        "SELECT order_id, strategy, symbol, short_strike, long_call_strike, long_put_strike, "
        "entry_credit, quantity, profile FROM trades ORDER BY opened_at"
    ).fetchall()
    return [r for r in rows if r["order_id"] not in notified_ids]


def _earnings_new_exits(conn, notified_ids: set) -> list:
    rows = conn.execute(
        "SELECT order_id, strategy, symbol, pnl, profile FROM trades "
        "WHERE closed_at IS NOT NULL ORDER BY closed_at"
    ).fetchall()
    return [r for r in rows if r["order_id"] not in notified_ids]


def _earnings_strikes(r) -> str:
    parts = []
    if r["short_strike"] is not None:
        parts.append(f"{r['short_strike']:.0f}S")
    if r["long_put_strike"] is not None:
        parts.append(f"{r['long_put_strike']:.0f}P")
    if r["long_call_strike"] is not None:
        parts.append(f"{r['long_call_strike']:.0f}C")
    return "/".join(parts)


def _fmt_earnings_entry(r) -> str:
    strat = (r["strategy"] or "spread").replace("_", " ")
    strikes = _earnings_strikes(r)
    credit = r["entry_credit"]
    credit_str = f"${credit:.2f}" if credit is not None else "n/a"
    strike_str = f" {strikes}" if strikes else ""
    return (
        f"\U0001f7e2 Earnings paper ENTRY — {r['symbol']} {strat}{strike_str} "
        f"x{r['quantity'] or 1} credit {credit_str} [{r['profile']}]"
    )


def _fmt_earnings_exit(r) -> str:
    pnl = r["pnl"]
    pnl_str = f"${pnl:+.2f}" if pnl is not None else "n/a"
    strat = (r["strategy"] or "spread").replace("_", " ")
    return f"\U0001f534 Earnings paper EXIT — {r['symbol']} {strat} [{r['profile']}], P&L {pnl_str}"


def _earnings_process(conn, st: dict, notifier: Notifier, name: str) -> dict:
    entered = set(st.get("notified_entry_ids", []))
    entries = _earnings_new_entries(conn, entered)
    for r in entries:
        notifier.notify("INFO", f"trade.{name}.entry.{r['order_id']}", "Paper entry", _fmt_earnings_entry(r))
        entered.add(r["order_id"])
    st["notified_entry_ids"] = list(entered)[-_ID_CAP:]

    notified = set(st.get("notified_exit_ids", []))
    exits = _earnings_new_exits(conn, notified)
    for r in exits:
        notifier.notify("INFO", f"trade.{name}.exit.{r['order_id']}", "Paper exit", _fmt_earnings_exit(r))
        notified.add(r["order_id"])
    st["notified_exit_ids"] = list(notified)[-_ID_CAP:]

    return {"entries_notified": len(entries), "exits_notified": len(exits)}


# Registry: paper.trade_schema -> (seed_fn, process_fn). Schemas not listed here skip cleanly.
_SCHEMAS = {
    "meic_ic": (_meic_seed, _meic_process),
    "earnings": (_earnings_seed, _earnings_process),
}


# --------------------------------------------------------------------------- entrypoint
def run(cfg: dict | None = None) -> dict:
    cfg = cfg or cfgmod.load_config()
    notify_cfg = cfg.get("notify", {})
    channels = notify_cfg.get("trade_channels", ["log", "discord"])
    notifier = Notifier({**notify_cfg, "channels": channels})

    state = _load_state()
    summary: dict[str, Any] = {}

    for name, mcfg in cfgmod.enabled_modules(cfg).items():
        paper = mcfg.get("paper", {})
        if not paper.get("notify_trades"):
            continue
        db_path = cfgmod.module_root(mcfg) / paper.get("paper_db", "data/paper_trades.db")
        if not db_path.exists():
            continue
        adapter = _SCHEMAS.get(paper.get("trade_schema", "meic_ic"))
        if adapter is None:  # unknown schema — skip cleanly
            continue
        seed_fn, process_fn = adapter

        conn = _connect_ro(db_path)
        try:
            st = state.get(name)
            if st is None:  # first activation — seed, don't backfill
                state[name] = seed_fn(conn)
                summary[name] = {"seeded": True}
                continue
            summary[name] = process_fn(conn, st, notifier, name)
        finally:
            conn.close()

    _save_state(state)
    return {"ok": True, "modules": summary}
