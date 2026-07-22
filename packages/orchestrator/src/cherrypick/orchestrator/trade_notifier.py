"""Notify paper-trade entries and exits (e.g. to Discord) as they happen.

Reads each module's paper DB — files only, no broker — finds trades that opened, had a wing stopped, or
closed since the last check, and pushes one concise line per event through the notifier. Distinct from
the watchdog's health
alerts: each trade is a one-shot event tracked by an id watermark (not deduped or re-notified). On first
activation the watermark is seeded to the current DB state, so pre-existing paper trades aren't
backfilled as a burst.

Called both by the dedicated fast trade-notify task (low latency, ~2 min) and as a fallback on each
watchdog tick; the per-schema id watermark keeps either path from re-sending the same event, and the
state file is written atomically so overlapping runs can't corrupt it.

Three paper-DB schemas are wired, dispatched by `paper.trade_schema`:
  - "meic_ic"  : MEICAgent's `ic_trades` table (integer id; entry, per-wing stop, and exit watermarks).
                 A single wing hitting its stop sets status='partial' with a non-null put/call_stop_cost
                 but leaves exit_time NULL, so it is watermarked per (id, wing) — independent of the
                 later whole-IC exit — and fires once the moment that wing's stop cost is recorded.
  - "earnings" : EarningsAgent's `trades` table (text order_id key, opened_at/closed_at timestamps).
  - "fly_book" : cherrypick-flies' `fly_positions` (text position_id key). Three stages rather than
                 two — entry, completion, settlement — because a credit spread turning into a
                 net-credit butterfly is the event the whole module exists to catch.

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
def _meic_stopped_wing_keys(conn) -> list:
    """ "{id}:put"/"{id}:call" for every wing that already has a stop cost recorded — the per-wing
    watermark. A wing is stopped exactly when its put/call_stop_cost is non-null (MEIC writes the
    stop's exit price there)."""
    keys = []
    for r in conn.execute(
        "SELECT id, put_stop_cost, call_stop_cost FROM ic_trades "
        "WHERE put_stop_cost IS NOT NULL OR call_stop_cost IS NOT NULL"
    ):
        if r["put_stop_cost"] is not None:
            keys.append(f"{r['id']}:put")
        if r["call_stop_cost"] is not None:
            keys.append(f"{r['id']}:call")
    return keys


def _meic_seed(conn) -> dict:
    max_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM ic_trades").fetchone()[0]
    exited = [r[0] for r in conn.execute("SELECT id FROM ic_trades WHERE exit_time IS NOT NULL")]
    return {
        "last_entry_id": max_id,
        "notified_exit_ids": exited,
        "notified_stop_keys": _meic_stopped_wing_keys(conn),
    }


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


def _meic_new_stops(conn) -> list:
    return conn.execute(
        "SELECT id, symbol, risk_profile, put_strike, call_strike, put_stop_cost, call_stop_cost "
        "FROM ic_trades WHERE put_stop_cost IS NOT NULL OR call_stop_cost IS NOT NULL ORDER BY id"
    ).fetchall()


def _fmt_meic_stop(r, wing: str) -> str:
    if wing == "put":
        strike, cost, label = r["put_strike"], r["put_stop_cost"], "PUT"
        strike_str = f"{strike:.0f}P" if strike is not None else "put"
    else:
        strike, cost, label = r["call_strike"], r["call_stop_cost"], "CALL"
        strike_str = f"{strike:.0f}C" if strike is not None else "call"
    cost_str = f"${cost:.2f}" if cost is not None else "n/a"
    return (
        f"\U0001f6d1 MEIC paper STOP — {r['symbol']} {label} wing {strike_str} "
        f"stopped @ {cost_str} [{r['risk_profile']}]"
    )


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

    # Per-wing stops: a wing hitting its stop sets put/call_stop_cost but not exit_time, so it is a
    # distinct event from the whole-IC exit and gets its own per-(id, wing) watermark. State that
    # predates this feature carries no stop watermark — seed it to the current stops (like first
    # activation, don't backfill) so pre-existing partials aren't blasted out in one burst.
    stops_notified = 0
    if "notified_stop_keys" not in st:
        st["notified_stop_keys"] = _meic_stopped_wing_keys(conn)
    else:
        stopped = set(st["notified_stop_keys"])
        for r in _meic_new_stops(conn):
            for wing, cost in (("put", r["put_stop_cost"]), ("call", r["call_stop_cost"])):
                if cost is None:
                    continue
                key = f"{r['id']}:{wing}"
                if key in stopped:
                    continue
                notifier.notify("INFO", f"trade.{name}.stop.{key}", "Paper stop", _fmt_meic_stop(r, wing))
                stopped.add(key)
                stops_notified += 1
        st["notified_stop_keys"] = list(stopped)[-_ID_CAP:]

    notified = set(st.get("notified_exit_ids", []))
    exits = _meic_new_exits(conn, notified)
    for r in exits:
        notifier.notify("INFO", f"trade.{name}.exit.{r['id']}", "Paper exit", _fmt_meic_exit(r))
        notified.add(r["id"])
    st["notified_exit_ids"] = sorted(notified)[-_ID_CAP:]

    return {"entries_notified": len(entries), "stops_notified": stops_notified, "exits_notified": len(exits)}


# --------------------------------------------------------------------------- Earnings trades schema
# EarningsAgent's `trades` table keys on a text `order_id` (no integer id) and timestamps opens/closes
# with `opened_at`/`closed_at` (epoch seconds). We watermark by remembering notified order_ids per
# direction — earnings paper volume is a handful of trades a day, so the capped id lists never grow big.
def _all_review_ids(conn) -> list:
    """Every entry_reviews id (guarded — the table is absent on DBs predating the feature)."""
    try:
        return [r["id"] for r in conn.execute("SELECT id FROM entry_reviews").fetchall()]
    except sqlite3.Error:
        return []


def _earnings_seed(conn) -> dict:
    rows = conn.execute("SELECT order_id, closed_at FROM trades").fetchall()
    return {
        "notified_entry_ids": [r["order_id"] for r in rows],
        "notified_exit_ids": [r["order_id"] for r in rows if r["closed_at"] is not None],
        "notified_review_ids": _all_review_ids(conn),
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


def _earnings_new_reviews(conn, notified_ids: set) -> list:
    """New per-symbol entry reviews (id watermark). Guarded — the table is absent on older DBs."""
    try:
        rows = conn.execute(
            "SELECT id, scan_date, symbol, timing, price, volume, winrate, winrate_sample, "
            "iv_rv_ratio, term_structure, market_cap, expected_move, best_tier, selected, reason, "
            "profile FROM entry_reviews ORDER BY id"
        ).fetchall()
    except sqlite3.Error:
        return []
    return [r for r in rows if r["id"] not in notified_ids]


def _fmt_earnings_review(r) -> str:
    """Per-symbol review summary — the data reviewed for entry plus the chosen/rejected decision, in the
    bullet layout the account owner asked for."""
    icon = "\U0001f7e2" if r["selected"] else "⚪"  # green vs white circle
    decision = "chosen" if r["selected"] else "rejected"
    timing = f" ({r['timing']})" if r["timing"] else ""
    lines = [f"{icon} Earnings review — {r['symbol']}{timing}: {decision} — {r['reason']} [{r['profile']}]"]
    if r["price"] is not None:
        lines.append(f"• Price: ${r['price']:,.2f}")
    if r["volume"] is not None:
        lines.append(f"• Volume: {int(r['volume']):,}")
    if r["winrate"] is not None:
        wr = f"{r['winrate'] * 100:.1f}%"
        if r["winrate_sample"] is not None:
            wr += f" over last {int(r['winrate_sample'])} earnings"
        lines.append(f"• Winrate: {wr}")
    if r["iv_rv_ratio"] is not None:
        lines.append(f"• IV/RV Ratio: {r['iv_rv_ratio']:.2f}")
    if r["term_structure"] is not None:
        lines.append(f"• Term Structure: {r['term_structure']:.3f}")
    if r["market_cap"] is not None:
        lines.append(f"• Market Cap: {int(r['market_cap']):,}")
    if r["expected_move"] is not None:
        lines.append(f"• Expected Move: ${r['expected_move']:,.2f}")
    if r["best_tier"]:
        lines.append(f"• Best tier: {r['best_tier']}")
    return "\n".join(lines)


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

    # Per-symbol entry reviews: the data reviewed for each symbol during the entry scan + the
    # chosen/rejected decision. One push per symbol, id-watermarked like entries/exits.
    reviewed = set(st.get("notified_review_ids", []))
    reviews = _earnings_new_reviews(conn, reviewed)
    for r in reviews:
        notifier.notify("INFO", f"trade.{name}.review.{r['id']}", "Earnings review", _fmt_earnings_review(r))
        reviewed.add(r["id"])
    st["notified_review_ids"] = list(reviewed)[-_ID_CAP:]

    return {"entries_notified": len(entries), "exits_notified": len(exits), "reviews_notified": len(reviews)}


# --------------------------------------------------------------------------- flies fly_positions schema
# cherrypick-flies keys on a text `position_id`. A position has THREE notifiable moments, not two: it
# opens as a credit spread, may later be completed into a butterfly, and finally settles. The middle
# one is the whole point of the strategy — it is the moment the position's floor becomes a guarantee —
# so it gets its own watermark rather than being folded into the entry or the exit.
def _flies_seed(conn) -> dict:
    rows = conn.execute("SELECT position_id, kind, status FROM fly_positions").fetchall()
    return {
        "notified_entry_ids": [r["position_id"] for r in rows],
        "notified_completion_ids": [r["position_id"] for r in rows if r["kind"] == "fly"],
        "notified_exit_ids": [r["position_id"] for r in rows if r["status"] == "settled"],
    }


def _fmt_flies_entry(r) -> str:
    if r["entry_mode"] == "outright":
        return (
            f"\U0001f7e2 Flies paper ENTRY — {r['symbol']} fly {r['center']:.0f} w{r['wing_width']:.0f} "
            f"bought for ${abs(r['net']):.2f} debit [{r['arm']}]"
        )
    return (
        f"\U0001f7e2 Flies paper ENTRY — {r['symbol']} short {r['side']} spread {r['center']:.0f} "
        f"w{r['wing_width']:.0f} credit ${r['net']:.2f} [{r['arm']}] — needs spot "
        f"{r['completing_direction'] or '?'} to complete"
    )


def _fmt_flies_completion(r) -> str:
    """The moment worth waking up for: a credit spread became a butterfly held for a net credit, so
    its worst case at expiry is now a profit. The floor is stated after fees or it means nothing."""
    return (
        f"\U0001f98b Flies COMPLETED — {r['symbol']} {r['side']} fly {r['center']:.0f} "
        f"w{r['wing_width']:.0f} for ${r['net']:.2f} net credit, floor "
        f"${r['floor_dollars']:.2f} after fees [{r['arm']}]"
    )


def _fmt_flies_exit(r) -> str:
    pnl = r["pnl"]
    pnl_str = f"${pnl:+.2f}" if pnl is not None else "n/a"
    what = "fly" if r["kind"] == "fly" else f"short {r['side']} spread"
    pinned = " (pinned)" if r["pinned"] else ""
    return (
        f"\U0001f534 Flies paper SETTLED — {r['symbol']} {what} {r['center']:.0f}"
        f"{pinned}, P&L {pnl_str} [{r['arm']}]"
    )


def _flies_process(conn, st: dict, notifier: Notifier, name: str) -> dict:
    counts = {}
    stages = [
        ("notified_entry_ids", "entry", "Paper entry", _fmt_flies_entry, "SELECT * FROM fly_positions"),
        (
            "notified_completion_ids",
            "completion",
            "Fly completed",
            _fmt_flies_completion,
            "SELECT * FROM fly_positions WHERE kind = 'fly' AND completed_at IS NOT NULL",
        ),
        (
            "notified_exit_ids",
            "exit",
            "Paper settled",
            _fmt_flies_exit,
            "SELECT * FROM fly_positions WHERE status = 'settled'",
        ),
    ]
    for key, event, title, fmt, query in stages:
        notified = set(st.get(key, []))
        rows = [r for r in conn.execute(query).fetchall() if r["position_id"] not in notified]
        for r in rows:
            notifier.notify("INFO", f"trade.{name}.{event}.{r['position_id']}", title, fmt(r))
            notified.add(r["position_id"])
        st[key] = sorted(notified)[-_ID_CAP:]
        counts[f"{event}s_notified"] = len(rows)
    return counts


# Registry: paper.trade_schema -> (seed_fn, process_fn). Schemas not listed here skip cleanly.
_SCHEMAS = {
    "meic_ic": (_meic_seed, _meic_process),
    "earnings": (_earnings_seed, _earnings_process),
    "fly_book": (_flies_seed, _flies_process),
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
        db_path = cfgmod.paper_db_path(mcfg, name)
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
