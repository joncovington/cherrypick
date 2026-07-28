#!/usr/bin/env python3
"""Flies LIVE loop — the paper loop's sibling, scaffolded for the gex arm. INERT BY DEFAULT.

This is the rung-0/rung-1 skeleton from docs/live-trading-plan.md: the same provider
snapshot (read-only stream cache), the same pure `engine` decisions, one arm only, with
submission through `cherrypick.core.broker` — and every gate the plan demands checked on
every tick. It will not place a live order today, by construction:

  - `readiness()` must come back empty: `live.enabled`, a non-empty `live.gate0_confirmed`
    human attestation, `live.arm` pinned to exactly one arm (default `gex`), a designated
    account, and the suite halt flag (`state/halt-live.flag`) absent.
  - Even then, `--dry-run` (the default!) preflights every order against the real account
    and places nothing — running the loop with `--dry-run --once` during market hours IS
    the flies rung-0 smoke.
  - `--live` additionally requires the readiness gates AND is refused while the daily-loss
    breaker (`live.daily_loss_halt_dollars`) is tripped on the live ledger.

Scaffold boundaries (deliberate, marked in-line): fill polling and working-order repricing
are rung-1 work — v0 places the entry/completion orders and records their ids on the live
ledger row, cancels working completions at the cutoff, and refuses everything else. Rule 5
still applies live: no adjustments, hold to cash settlement, settle on the official print.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, "_core")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import clock  # noqa: E402
import db as dbmod  # noqa: E402
import engine  # noqa: E402
import live_orders  # noqa: E402
import provider  # noqa: E402
from cli import load_config  # noqa: E402

DEFAULT_ARM = "gex"


def halt_flag_path() -> str:
    """The suite-wide live kill switch — the same path the orchestrator's Live Ops card
    reports (`liveops.halt_flag_path()`); presence is the signal."""
    home = os.environ.get("CHERRYPICK_HOME") or os.path.join(os.path.expanduser("~"), ".cherrypick")
    return os.path.join(home, "state", "halt-live.flag")


def readiness(config: dict, *, halt_present: bool, designated: str | None) -> list[str]:
    """The unmet live gates, checked every tick — empty means the loop may act. Pure."""
    live = config.get("live") or {}
    unmet = []
    if not live.get("enabled"):
        unmet.append("live.enabled is false")
    if not str(live.get("gate0_confirmed") or "").strip():
        unmet.append("live.gate0_confirmed is empty — a human must attest Gate 0 (who/when)")
    arm = live.get("arm", DEFAULT_ARM)
    arms = config.get("arms") or {}
    if arm not in arms:
        unmet.append(f"live.arm {arm!r} is not a configured arm")
    if halt_present:
        unmet.append("halt flag present (state/halt-live.flag) — live entries halted")
    if not designated:
        unmet.append("no designated account — run `cherrypick account --module flies --set <last4>`")
    return unmet


def daily_loss_tripped(conn, day: str, limit_dollars: float | None) -> bool:
    """The daily-loss breaker over the LIVE ledger: settled net for `day` at or below
    -limit halts new entries. Open structures keep their normal hold-to-settlement rules
    (they are defined-risk; improvising exits is rule 5's territory)."""
    if not limit_dollars:
        return False
    row = conn.execute(
        "SELECT COALESCE(SUM(pnl), 0) FROM fly_positions "
        "WHERE trade_date = ? AND status = 'settled'", (day,)).fetchone()
    return float(row[0] or 0.0) <= -abs(limit_dollars)


def _cutoff_reached(now_min: int | None, params: dict) -> bool:
    cutoff = params.get("completion_cutoff", "15:30")
    return now_min is not None and now_min >= engine.time_to_minutes(cutoff)


def run_once(config: dict, snapshot: dict, conn, broker, *, live: bool, log=print) -> dict:
    """One live iteration for the pinned arm. `broker` is the injected submission seam —
    an object with place(spec, live) -> {ok, order_id?, ...} and cancel(order_id) -> {ok}.
    With live=False every placement is a dry-run preflight (the rung-0 smoke)."""
    arm = (config.get("live") or {}).get("arm", DEFAULT_ARM)
    params = engine.merged_params(config, arm)
    day = snapshot["date"]
    summary = {"arm": arm, "live": live, "entered": 0, "completed_orders": 0,
               "cancelled": 0, "skips": []}

    rows = conn.execute(
        "SELECT * FROM fly_positions WHERE trade_date = ? AND arm = ? AND status = 'open'",
        (day, arm)).fetchall()
    positions = [dict(r) for r in rows]

    # --- completion management (before new entries: finishing a fly beats starting one) ---
    for pos in positions:
        if pos.get("kind") != "short_vertical":
            continue
        if pos.get("completion_order_id"):
            # Rung-1 work: poll the working order's fill status and reprice within the gate.
            # v0 only enforces the cutoff — a working completion is cancelled at 15:30 so the
            # book never carries an unwatched order into the close.
            if _cutoff_reached(snapshot.get("now_min"), params):
                res = broker.cancel(pos["completion_order_id"])
                if res.get("ok"):
                    conn.execute("UPDATE fly_positions SET completion_order_id = NULL WHERE id = ?",
                                 (pos["id"],))
                    conn.commit()
                    summary["cancelled"] += 1
            continue
        complete, reason, plan = engine.evaluate_completion(snapshot, pos, params)
        if not complete:
            summary["skips"].append({"position": pos.get("position_id"), "reason": reason})
            continue
        if _cutoff_reached(snapshot.get("now_min"), params):
            summary["skips"].append({"position": pos.get("position_id"),
                                     "reason": "completion_cutoff_reached"})
            continue
        spec = live_orders.completion_spec(snapshot, pos, plan)
        res = broker.place(spec, live=live)
        log(f"completion order ({'LIVE' if live else 'dry-run'}): {json.dumps(res, default=str)[:200]}")
        if res.get("ok") and live and res.get("order_id"):
            conn.execute("UPDATE fly_positions SET completion_order_id = ? WHERE id = ?",
                         (str(res["order_id"]), pos["id"]))
            conn.commit()
        summary["completed_orders"] += 1

    # --- entry (one structure per tick at most; rung 1 caps structures/day via max_positions) ---
    enter, reason, plan = engine.evaluate_credit_spread_entry(snapshot, params, positions)
    if not enter:
        summary["skips"].append({"entry": reason})
        return summary
    spec = live_orders.entry_spec(snapshot, plan)
    res = broker.place(spec, live=live)
    log(f"entry order ({'LIVE' if live else 'dry-run'}): {json.dumps(res, default=str)[:200]}")
    if res.get("ok") and live and res.get("order_id"):
        # Rung-1 honesty: the row is recorded as open with its order id; fill confirmation and
        # the recorded credit coming from the ACTUAL fill (not the model) are rung-1 work.
        import book as bookmod
        pid = f"live-{day}-{arm}-{int(plan['center'])}"
        dbmod.save_position(conn, {
            "position_id": pid, "book_id": bookmod.book_id_for(day, arm, snapshot["symbol"]),
            "trade_date": day, "arm": arm, "entry_mode": "legged", "symbol": snapshot["symbol"],
            "kind": "short_vertical", "side": plan["side"], "center": plan["center"],
            "wing_width": plan["wing_width"], "quantity": plan["quantity"], "net": plan["credit"],
            "credit": plan["credit"], "fees": plan["open_fee"], "status": "open",
            "entry_window": plan.get("entry_window"),
            "underlying_at_entry": snapshot.get("underlying_price"),
            "entry_time": clock.now_iso(), "entry_order_id": str(res["order_id"]),
        })
    summary["entered"] += 1
    return summary


class BrokerAdapter:
    """The real submission seam over broker_cli's core.broker plumbing. Constructed only
    after readiness() passes (or in dry-run mode, where live submission is impossible)."""

    def place(self, spec: dict, live: bool) -> dict:
        import asyncio

        import broker_cli
        args = argparse.Namespace(order=json.dumps(spec), live=live)
        result = asyncio.run(broker_cli.cmd_execute_trade(args))
        rid = (result.get("response") or {}).get("order", {}) if isinstance(result.get("response"), dict) else {}
        if isinstance(rid, dict) and rid.get("id") is not None:
            result["order_id"] = rid["id"]
        return result

    def cancel(self, order_id: str) -> dict:
        # Rung-1 work: order cancellation via the SDK. v0 refuses so a cutoff cancel is
        # loudly visible in the log rather than silently pretended.
        return {"ok": False, "error": f"cancel not implemented in scaffold (order {order_id})"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--once", action="store_true", required=True,
                    help="Single iteration (the only mode the scaffold supports)")
    ap.add_argument("--dry-run", dest="dry_run", action="store_true", default=True,
                    help="Preflight orders, place nothing (DEFAULT — this is the rung-0 smoke)")
    ap.add_argument("--live", dest="dry_run", action="store_false",
                    help="Place real orders. Requires every readiness gate AND the breaker clear.")
    args = ap.parse_args()

    config = load_config()
    import credentials as creds
    designated = creds.designated_account()
    unmet = readiness(config, halt_present=os.path.exists(halt_flag_path()),
                      designated=designated)
    live = not args.dry_run
    if live and unmet:
        print(json.dumps({"ok": False, "error": "live gates unmet", "unmet": unmet}))
        return 1
    if unmet:
        print(f"note: dry-run with unmet live gates: {unmet}")

    symbol = (config.get("live") or {}).get("symbol", "SPX")
    import paper_loop as _pl
    snapshot = provider.build_snapshot(
        _pl.stream_cache_path(config), symbol,
        max_quote_age_seconds=config.get("defaults", {}).get(
            "max_quote_age_seconds", provider.DEFAULT_MAX_QUOTE_AGE_SECONDS))
    if not snapshot.get("ok"):
        print(json.dumps({"ok": False, "error": f"no snapshot: {snapshot.get('reason')}"}))
        return 1

    conn = dbmod.connect(dbmod.live_db_path())
    try:
        day = snapshot["date"]
        limit = (config.get("live") or {}).get("daily_loss_halt_dollars")
        if live and daily_loss_tripped(conn, day, limit):
            print(json.dumps({"ok": False, "error": "daily-loss breaker tripped — no new entries"}))
            return 1
        summary = run_once(config, snapshot, conn, BrokerAdapter(), live=live)
    finally:
        conn.close()
    print(json.dumps({"ok": True, "at": datetime.now().isoformat(timespec="seconds"), **summary},
                     default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
