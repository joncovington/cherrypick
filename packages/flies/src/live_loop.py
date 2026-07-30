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

Fill polling and real order cancellation (rung-1 work) are now built: every tick first polls
any position with a pending entry/completion order, records the ACTUAL fill price (not the
model's) once confirmed, and flips a completed short vertical to kind='fly' only then — not
at order placement. Working-order repricing (moving the completion limit) is still not built;
an unfilled completion still just gets cancelled at the cutoff, now for real. Rule 5 still
applies live: no adjustments, hold to cash settlement, settle on the official print.

Live concurrency (2026-07-30, at the user's explicit direction — a deliberately tight pilot):
at most one position may be "incomplete" at a time for the live arm — an open short vertical
always counts; a completed fly counts only if its floor is negative (not risk-free) after
fees, in which case a NEW entry is refused until a human sets `live.negative_floor_override`
to that exact position_id (see `_blocking_positions`). A completed, risk-free fly frees the
slot immediately, without waiting for settlement.
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
import fly  # noqa: E402
import live_orders  # noqa: E402
import provider  # noqa: E402
from cli import load_config  # noqa: E402

DEFAULT_ARM = "gex"
_TERMINAL_UNFILLED = {"cancelled", "rejected", "expired"}


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
        "SELECT COALESCE(SUM(pnl), 0) FROM fly_positions WHERE trade_date = ? AND status = 'settled'", (day,)
    ).fetchone()
    return float(row[0] or 0.0) <= -abs(limit_dollars)


def _cutoff_reached(now_min: int | None, params: dict) -> bool:
    cutoff = params.get("completion_cutoff", "15:30")
    return now_min is not None and now_min >= engine.time_to_minutes(cutoff)


def _is_blocking(pos: dict, override_position_id: str | None) -> bool:
    """True if this open position should prevent a new entry.

    An open short vertical always blocks — it IS the one incomplete spread the pilot allows.
    A completed fly blocks only when its floor is negative (not risk-free) after fees, and
    even then only until a human names this exact position_id in
    `live.negative_floor_override` — so a stale override can never silently cover a different,
    later stuck position."""
    if pos.get("kind") != "fly":
        return True
    if fly.is_risk_free(pos):
        return False
    return pos.get("position_id") != override_position_id


def _blocking_positions(positions: list[dict], override_position_id: str | None) -> list[dict]:
    return [p for p in positions if p.get("status") == "open" and _is_blocking(p, override_position_id)]


def _confirm_entry_fill(conn, pos: dict, broker, log) -> dict:
    """Poll a pending entry order; record the ACTUAL fill credit once confirmed. Returns the
    (possibly updated) position dict."""
    status = broker.status(pos["entry_order_id"])
    state = str(status.get("status") or "").strip().lower()
    if state == "filled":
        try:
            actual_credit = abs(float(status.get("price")))
        except (TypeError, ValueError):
            actual_credit = pos["net"]  # can't parse a real price — keep the model rather than corrupt it
        conn.execute(
            "UPDATE fly_positions SET net = ?, credit = ?, entry_fill_status = 'filled' WHERE id = ?",
            (actual_credit, actual_credit, pos["id"]),
        )
        conn.commit()
        log(
            f"entry FILLED {pos['position_id']}: modeled {pos['net']:.2f} credit -> "
            f"actual {actual_credit:.2f}"
        )
        return {**pos, "net": actual_credit, "credit": actual_credit, "entry_fill_status": "filled"}
    if state in _TERMINAL_UNFILLED:
        conn.execute(
            "UPDATE fly_positions SET entry_fill_status = ?, status = 'cancelled' WHERE id = ?",
            (state, pos["id"]),
        )
        conn.commit()
        log(f"entry {state.upper()} {pos['position_id']} — never established")
        return {**pos, "entry_fill_status": state, "status": "cancelled"}
    return pos  # still working — stays pending, still blocks a second entry


def _confirm_completion_fill(conn, pos: dict, broker, log) -> dict:
    """Poll a pending completion order; flip kind='fly' with the ACTUAL debit once confirmed."""
    status = broker.status(pos["completion_order_id"])
    state = str(status.get("status") or "").strip().lower()
    if state == "filled":
        try:
            actual_debit = abs(float(status.get("price")))
        except (TypeError, ValueError):
            actual_debit = pos.get("debit") or 0.0
        completion_fee = fly.vertical_open_fee(pos["symbol"], pos.get("quantity", 1))
        new_net = pos["net"] - actual_debit
        new_fees = (pos.get("fees") or 0.0) + completion_fee
        updated = {**pos, "kind": "fly", "net": new_net, "fees": new_fees}
        floor = fly.position_floor(updated)
        risk_free = fly.is_risk_free(updated)
        conn.execute(
            "UPDATE fly_positions SET kind = 'fly', net = ?, debit = ?, fees = ?, floor_dollars = ?, "
            "risk_free = ?, completion_fill_status = 'filled', completed_at = ? WHERE id = ?",
            (new_net, actual_debit, new_fees, floor, int(risk_free), clock.now_iso(), pos["id"]),
        )
        conn.commit()
        log(
            f"completion FILLED {pos['position_id']}: floor ${floor:.2f} after fees "
            f"({'risk-free' if risk_free else 'NOT risk-free'})"
        )
        return {**updated, "floor_dollars": floor, "risk_free": int(risk_free)}
    if state in _TERMINAL_UNFILLED:
        conn.execute(
            "UPDATE fly_positions SET completion_order_id = NULL, completion_fill_status = ? WHERE id = ?",
            (state, pos["id"]),
        )
        conn.commit()
        log(f"completion {state.upper()} {pos['position_id']} — still a short vertical, may retry")
        return {**pos, "completion_order_id": None, "completion_fill_status": state}
    return pos  # still working


def run_once(config: dict, snapshot: dict, conn, broker, *, live: bool, log=print) -> dict:
    """One live iteration for the pinned arm. `broker` is the injected submission seam —
    an object with place(spec, live) -> {ok, order_id?, ...}, cancel(order_id) -> {ok}, and
    status(order_id) -> {status, price, filled, ...}. With live=False every placement is a
    dry-run preflight (the rung-0 smoke) and no fill polling happens (there is nothing to
    poll — a dry run places no real order)."""
    arm = (config.get("live") or {}).get("arm", DEFAULT_ARM)
    params = engine.merged_params(config, arm)
    live_cfg = config.get("live") or {}
    day = snapshot["date"]
    summary = {"arm": arm, "live": live, "entered": 0, "completed_orders": 0, "cancelled": 0, "skips": []}

    rows = conn.execute(
        "SELECT * FROM fly_positions WHERE trade_date = ? AND arm = ? AND status = 'open'", (day, arm)
    ).fetchall()
    positions = [dict(r) for r in rows]

    # --- fill confirmation (before anything else acts on a position's current state) ---
    if live:
        updated = []
        for pos in positions:
            if pos.get("completion_order_id") and pos.get("completion_fill_status") != "filled":
                pos = _confirm_completion_fill(conn, pos, broker, log)
            elif pos.get("entry_order_id") and pos.get("entry_fill_status") not in (
                "filled",
                *_TERMINAL_UNFILLED,
            ):
                pos = _confirm_entry_fill(conn, pos, broker, log)
            if pos.get("status") == "open":
                updated.append(pos)
        positions = updated

    # --- completion management (before new entries: finishing a fly beats starting one) ---
    for pos in positions:
        if pos.get("kind") != "short_vertical":
            continue
        if live and pos.get("entry_fill_status") != "filled":
            # Entry not confirmed yet — nothing to complete until we know we actually hold it.
            continue
        if pos.get("completion_order_id"):
            if _cutoff_reached(snapshot.get("now_min"), params):
                res = broker.cancel(pos["completion_order_id"])
                if res.get("ok"):
                    conn.execute(
                        "UPDATE fly_positions SET completion_order_id = NULL, "
                        "completion_fill_status = NULL WHERE id = ?",
                        (pos["id"],),
                    )
                    conn.commit()
                    summary["cancelled"] += 1
                else:
                    log(f"cutoff cancel FAILED for {pos['position_id']}: {res.get('error')}")
            continue
        complete, reason, plan = engine.evaluate_completion(snapshot, pos, params)
        if not complete:
            summary["skips"].append({"position": pos.get("position_id"), "reason": reason})
            continue
        if _cutoff_reached(snapshot.get("now_min"), params):
            summary["skips"].append(
                {"position": pos.get("position_id"), "reason": "completion_cutoff_reached"}
            )
            continue
        spec = live_orders.completion_spec(snapshot, pos, plan)
        res = broker.place(spec, live=live)
        log(f"completion order ({'LIVE' if live else 'dry-run'}): {json.dumps(res, default=str)[:200]}")
        if res.get("ok") and live and res.get("order_id"):
            conn.execute(
                "UPDATE fly_positions SET completion_order_id = ?, completion_fill_status = 'pending' "
                "WHERE id = ?",
                (str(res["order_id"]), pos["id"]),
            )
            conn.commit()
        summary["completed_orders"] += 1

    # --- entry (one structure per tick at most; concurrency gated below, not by max_positions —
    # see module docstring. Paper's max_positions stays a forced-sampling knob and is irrelevant here.) ---
    blockers = _blocking_positions(positions, live_cfg.get("negative_floor_override"))
    if blockers:
        negative = [p for p in blockers if p.get("kind") == "fly"]
        if negative:
            p = negative[0]
            summary["skips"].append(
                {
                    "entry": f"blocked: completed fly {p['position_id']} has a negative floor "
                    f"(${fly.position_floor(p):.2f}) — set live.negative_floor_override to "
                    f"{p['position_id']!r} to permit a new entry"
                }
            )
        else:
            summary["skips"].append(
                {"entry": f"blocked: {blockers[0]['position_id']} is still an incomplete spread"}
            )
        return summary

    enter, reason, plan = engine.evaluate_credit_spread_entry(snapshot, params, positions)
    if not enter:
        summary["skips"].append({"entry": reason})
        return summary
    spec = live_orders.entry_spec(snapshot, plan)
    res = broker.place(spec, live=live)
    log(f"entry order ({'LIVE' if live else 'dry-run'}): {json.dumps(res, default=str)[:200]}")
    if res.get("ok") and live and res.get("order_id"):
        import book as bookmod

        pid = f"live-{day}-{arm}-{int(plan['center'])}"
        dbmod.save_position(
            conn,
            {
                "position_id": pid,
                "book_id": bookmod.book_id_for(day, arm, snapshot["symbol"]),
                "trade_date": day,
                "arm": arm,
                "entry_mode": "legged",
                "symbol": snapshot["symbol"],
                "kind": "short_vertical",
                "side": plan["side"],
                "center": plan["center"],
                "wing_width": plan["wing_width"],
                "quantity": plan["quantity"],
                "net": plan["credit"],
                "credit": plan["credit"],
                "fees": plan["open_fee"],
                "status": "open",
                "entry_window": plan.get("entry_window"),
                "underlying_at_entry": snapshot.get("underlying_price"),
                "entry_time": clock.now_iso(),
                "entry_order_id": str(res["order_id"]),
                "entry_fill_status": "pending",
            },
        )
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
        rid = (
            (result.get("response") or {}).get("order", {})
            if isinstance(result.get("response"), dict)
            else {}
        )
        if isinstance(rid, dict) and rid.get("id") is not None:
            result["order_id"] = rid["id"]
        return result

    def status(self, order_id: str) -> dict:
        import asyncio

        import broker_cli

        args = argparse.Namespace(order_id=order_id)
        return asyncio.run(broker_cli.cmd_order_status(args))

    def cancel(self, order_id: str) -> dict:
        import asyncio

        import broker_cli

        args = argparse.Namespace(order_id=order_id)
        return asyncio.run(broker_cli.cmd_cancel_order(args))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--once",
        action="store_true",
        required=True,
        help="Single iteration (the only mode the scaffold supports)",
    )
    ap.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="Preflight orders, place nothing (DEFAULT — this is the rung-0 smoke)",
    )
    ap.add_argument(
        "--live",
        dest="dry_run",
        action="store_false",
        help="Place real orders. Requires every readiness gate AND the breaker clear.",
    )
    args = ap.parse_args()

    config = load_config()
    import credentials as creds

    designated = creds.designated_account()
    unmet = readiness(config, halt_present=os.path.exists(halt_flag_path()), designated=designated)
    live = not args.dry_run
    if live and unmet:
        print(json.dumps({"ok": False, "error": "live gates unmet", "unmet": unmet}))
        return 1
    if unmet:
        print(f"note: dry-run with unmet live gates: {unmet}")

    symbol = (config.get("live") or {}).get("symbol", "SPX")
    import paper_loop as _pl

    snapshot = provider.build_snapshot(
        _pl.stream_cache_path(config),
        symbol,
        max_quote_age_seconds=config.get("defaults", {}).get(
            "max_quote_age_seconds", provider.DEFAULT_MAX_QUOTE_AGE_SECONDS
        ),
    )
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
    print(
        json.dumps({"ok": True, "at": datetime.now().isoformat(timespec="seconds"), **summary}, default=str)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
