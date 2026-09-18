#!/usr/bin/env python3
"""MEIC LIVE loop — the paper loop's sibling, pinned to one symbol. INERT BY DEFAULT.

This is the rung-1 measurement pilot from the live-loop plan: the same market-data fetch
`paper_loop.py` already uses (reused directly, not duplicated), the same pure decision
functions `paper.py` runs in paper every tick (`evaluate_entry`, `evaluate_open_trade`,
`force_close_active`, `settlement_active`), and the same DB-write helpers paper already uses
(`paper._save_trade`, `_update_trade`, `_apply_exit_decision`, `_get_open_trades`,
`_profile_day_stats`) -- pointed at the live ledger (`meic_trades.db`, `paths.live_db_path()`)
instead of the paper one. Paper and live can therefore never silently diverge on entry/stop
logic: they call the identical functions. Only order construction (`live_orders.py`) and
submission are new.

It will not place a live order today, by construction:

  - `readiness()` must come back empty: `enable_live_trading` true, `live.symbol` set,
    a non-empty `live.gate0_confirmed` human attestation, a designated account, and the
    suite halt flag (`state/halt-live.flag`) absent.
  - Even then, `--dry-run` (the default!) preflights every order against the real account and
    places nothing -- running the loop with `--dry-run --once` during market hours is a repeat
    of the rung-0 smoke (`live_smoke.py`) but through the actual loop code path.
  - `--live` additionally requires every readiness gate AND is refused while the daily-loss
    breaker (`live.daily_loss_halt_dollars`) is tripped on the live ledger.

Scaffold boundaries (deliberate, rung-1 only -- see docs/live-trading-plan.md once written):
no ORB debit spreads, no multi-symbol (one pinned `live.symbol`), no working-order repricing.
Task registration (`--install-task`) is a manual step the user runs themselves; the
orchestrator never installs or runs this loop.

Fill confirmation (2026-09-17). Until then every accepted placement was recorded as a fill at
the submitted limit -- an entry became an OPEN row the moment the broker accepted the order,
with the price asked for as its credit. Now an entry is saved `status='pending'` and the next
tick (and every tick after) asks the broker; a confirmed fill flips it to `open` with the ACTUAL
net credit and `fill_confirmed_at`, and an order that dies unfilled (cancelled / rejected /
expired) becomes `cancelled` with no P&L and frees its slot. A pending row still counts toward
`max_concurrent_ics` -- it is a position at risk -- and is not managed for exits until it fills.
Stop orders are confirmed the same way into `{side}_stop_fill_status`; a stop that dies unfilled
is logged CRITICAL because the ledger has that side closed while the broker holds it open.
Reopening the leg in the ledger (and correcting the recorded exit to the actual fill) is the
documented follow-up: the per-side exit accounting is shared with paper and needs its own verb.

The submission seam is `cherrypick.core.execution.Broker` (2026-09-17): one session on one
process-wide loop, the module's own gates re-checked on every live submit, the deploy governor
applied. It replaced an adapter that shelled out to `tt.py execute_trade` and scraped stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

from cherrypick.core import execution as _execution  # noqa: E402

from cherrypick.meic import credentials as _creds  # noqa: E402
from cherrypick.meic import (
    live_orders,  # noqa: E402
    paper,  # noqa: E402  (the pure decision functions + paper-DB helpers this loop reuses)
)
from cherrypick.meic import (
    paper_loop as _pl,  # noqa: E402  (market-data fetch helpers -- reused, not duplicated)
)
from cherrypick.meic import paths as _paths  # noqa: E402
from cherrypick.meic import stream_request as _stream_request  # noqa: E402

_TASK_NAME = "cherrypick-meic-live-loop"


def halt_flag_path() -> str:
    """The suite-wide live kill switch -- the same path the orchestrator's Live Ops card
    reports (`liveops.halt_flag_path()`); presence is the signal. Recomputed locally (no
    cross-package import), mirroring flies' own `live_loop.halt_flag_path()`."""
    home = os.environ.get("CHERRYPICK_HOME") or os.path.join(os.path.expanduser("~"), ".cherrypick")
    return os.path.join(home, "state", "halt-live.flag")


def _designated_account() -> str | None:
    from cherrypick.core.auth import ACCOUNT_NUMBER, CredentialError

    try:
        return _creds.store.get_secret(ACCOUNT_NUMBER)
    except CredentialError:
        return None


def readiness(config: dict, *, halt_present: bool, designated: str | None) -> list[str]:
    """The unmet live gates, checked every tick -- empty means the loop may act. Pure.

    `enable_live_trading` is MEIC's existing single kill switch (already enforced by
    `tt.py`'s own `cmd_execute_trade`) -- there is no second `live.enabled` flag the way
    flies has one, since flies had no other kill switch before its live scaffold existed."""
    live = config.get("live") or {}
    unmet = []
    if not config.get("enable_live_trading"):
        unmet.append("enable_live_trading is false")
    if not str(live.get("symbol") or "").strip():
        unmet.append("live.symbol is unset -- pin the one symbol this rung trades")
    if not str(live.get("gate0_confirmed") or "").strip():
        unmet.append("live.gate0_confirmed is empty -- a human must attest Gate 0 (who/when)")
    if halt_present:
        unmet.append("halt flag present (state/halt-live.flag) -- live entries halted")
    if not designated:
        unmet.append("no designated account -- run `cherrypick account --module meic --set <last4>`")
    return unmet


def daily_loss_tripped(db_path: str, day: str, limit_dollars: float | None) -> bool:
    """The daily-loss breaker over the LIVE ledger: today's net P&L (summed across every
    status -- a still-open IC with one stopped side already contributes via `_apply_exit_decision`'s
    running total) at or below -limit halts new entries. Direct query, same style as
    `paper.py::_profile_day_stats`."""
    if not limit_dollars:
        return False
    import sqlite3

    try:
        con = sqlite3.connect(db_path)
        row = con.execute(
            "SELECT COALESCE(SUM(pnl), 0) FROM ic_trades WHERE trade_date = ?", (day,)
        ).fetchone()
        con.close()
    except sqlite3.Error:
        return False
    return float(row[0] or 0.0) <= -abs(limit_dollars)


EXECUTION_MODE = "live"


_extract_order_id = _execution.order_id_of  # one reading of a placement result, suite-wide


def _sides_to_close(decision: dict) -> list[str]:
    """Which side(s) an `evaluate_open_trade` decision requires closing -- force_close's
    `put_open`/`call_open` flags mean 'this side is still open', i.e. needs a close order."""
    action = decision["action"]
    if action == "stop_put":
        return ["put"]
    if action == "stop_call":
        return ["call"]
    if action == "stop_both":
        return ["put", "call"]
    if action == "force_close":
        return [s for s in ("put", "call") if decision.get(f"{s}_open")]
    return []


def _manage_open_trades(
    symbol: str, snapshot: dict, params: dict, db_path: str, broker, *, live: bool, log
) -> dict:
    """Mark-to-market + exit every open live IC on `symbol`, submitting a real close order for
    any actionable decision. Reuses `paper.evaluate_open_trade` (the exact function paper_loop
    calls) and `paper._apply_exit_decision` (the exact write path), with the decision's modeled
    exit price(s) overridden by what was actually submitted before the write."""
    open_ics = paper._get_open_trades(symbol, EXECUTION_MODE, snapshot["date"], db_path)
    base_config = paper.load_base_config()
    is_cash = paper._is_cash_settled(symbol, base_config)
    force_close, force_close_reason = paper.force_close_active(snapshot, base_config, is_cash)
    settle = paper.settlement_active(snapshot, base_config, is_cash)
    counts = {"stopped": 0, "force_closed": 0, "expired": 0, "held": 0, "order_failed": 0}
    stop_limit_ratio = params.get("stop_limit_ratio", 1.02)
    leg_quotes = snapshot.get("leg_quotes", {})

    for trade in open_ics:
        if trade.get("status") == "pending":
            # Placed, not yet confirmed filled: nothing is held, so nothing can be closed.
            # `_confirm_fills` resolves it; until then it only counts toward the concurrency cap.
            counts["held"] += 1
            continue
        decision = paper.evaluate_open_trade(
            trade,
            leg_quotes,
            params,
            force_close,
            underlying_price=snapshot.get("underlying_price"),
            is_cash_settled=is_cash,
            force_close_reason=force_close_reason,
            settle=settle,
        )
        action = decision["action"]
        if action == "hold":
            counts["held"] += 1
            paper._apply_exit_decision(trade, decision, symbol, db_path)
            continue
        if action == "expire":
            # Cash-settled left-to-expire: nothing to submit, settlement is automatic.
            counts["expired"] += 1
            paper._apply_exit_decision(trade, decision, symbol, db_path)
            continue

        # Every close action (stop_call / stop_put / stop_both / force_close) reduces to one or
        # two independent per-side 2-leg close orders -- force_close is simply "close whichever
        # side(s) are still open", not a distinct order shape.
        sides = _sides_to_close(decision)
        try:
            specs = {
                side: live_orders.stop_close_spec(trade, side, leg_quotes, stop_limit_ratio) for side in sides
            }
        except ValueError as exc:
            log(f"CRITICAL: could not build close order for {trade['ic_order_id']} ({action}): {exc}")
            counts["order_failed"] += 1
            continue

        results = {side: broker.place(spec, live=live) for side, spec in specs.items()}
        log(
            f"{action} order ({'LIVE' if live else 'dry-run'}) for {trade['ic_order_id']}: "
            f"{json.dumps(results, default=str)[:300]}"
        )
        if not all(r.get("ok") for r in results.values()):
            log(f"CRITICAL: close order failed for {trade['ic_order_id']} ({action}) -- position stays open")
            counts["order_failed"] += 1
            continue

        # Honesty: the exit price recorded is what was SUBMITTED, not the modeled decision
        # price -- fill polling / repricing is rung-2 work (same limitation flies' rung 1
        # accepts). Order ids are stamped in a follow-up update, matching db.py's existing
        # (already-present, previously-unused) live order-id columns.
        adjusted = dict(decision)
        order_ids = {}
        for side, spec in specs.items():
            adjusted[f"{side}_exit_price"] = spec["price"]
            oid = _extract_order_id(results[side])
            order_ids[f"{side}_stop_order_id"] = oid
            if oid is not None and live:
                order_ids[f"{side}_stop_fill_status"] = "pending"

        paper._apply_exit_decision(trade, adjusted, symbol, db_path)
        fields = {k: v for k, v in order_ids.items() if v is not None}
        if fields:
            paper._update_trade(trade["ic_order_id"], fields, db_path)
        counts["force_closed" if action == "force_close" else "stopped"] += 1

    return counts


def _manage_entry(
    symbol: str, snapshot: dict, params: dict, db_path: str, broker, *, live: bool, log
) -> dict:
    """Evaluate and, if admitted, submit one new IC entry for `symbol`. Reuses
    `paper.evaluate_entry` (the exact function paper_loop calls); on a real fill, builds the
    ic_trades row via `paper.synthetic_entry_fill` and overrides the synthetic order id / net
    credit with what was actually submitted before saving."""
    open_ics = paper._get_open_trades(symbol, EXECUTION_MODE, snapshot["date"], db_path)
    if len(open_ics) >= params["max_concurrent_ics"]:
        return {"entry": "skipped", "reason": "max_concurrent_ics_reached"}
    todays_entries, last_entry_min = paper._profile_day_stats(
        EXECUTION_MODE, snapshot["date"], db_path, symbol=symbol
    )
    entered, reason, chosen = paper.evaluate_entry(
        snapshot,
        params,
        open_ics,
        account_open_count=len(open_ics),
        todays_entry_count=todays_entries,
        last_entry_min=last_entry_min,
    )
    if not entered:
        return {"entry": "skipped", "reason": reason}

    quantity = params.get("quantity", 1)
    try:
        spec = live_orders.entry_spec(chosen, quantity)
    except ValueError as exc:
        log(f"CRITICAL: could not build entry order: {exc}")
        return {"entry": "skipped", "reason": f"order_build_failed: {exc}"}

    result = broker.place(spec, live=live)
    log(f"entry order ({'LIVE' if live else 'dry-run'}): {json.dumps(result, default=str)[:300]}")
    if not result.get("ok"):
        return {"entry": "skipped", "reason": f"broker_rejected: {result.get('error')}"}

    if not live:
        # A dry-run preflight opened nothing -- the live ledger must stay untouched.
        return {"entry": "dry_run", "net_credit": spec["price"]}

    order_id = _extract_order_id(result)
    row = paper.synthetic_entry_fill(snapshot, EXECUTION_MODE, chosen, params, EXECUTION_MODE)
    if order_id is None:
        log(f"WARNING: live placement had no extractable order id for {symbol}; keeping synthetic id")
    else:
        row["ic_order_id"] = f"LIVE-{symbol}-{order_id}"
    # The price asked for, until the broker says what filled (`_confirm_fills`). The row is
    # PENDING, not open: it holds a slot (a working order is a position at risk) and nothing else.
    row["net_credit"] = spec["price"]
    row["status"] = "pending"
    row["fill_confirmed_at"] = None
    row["put_spread_entry_order_id"] = order_id
    row["call_spread_entry_order_id"] = order_id
    save_result = paper._save_trade(row, db_path)
    return {
        "entry": "placed",
        "ic_order_id": row["ic_order_id"],
        "net_credit": row["net_credit"],
        "order_id": order_id,
        "save_result": save_result,
    }


def _confirm_fills(symbol: str, snapshot: dict, db_path: str, broker, *, log) -> dict:
    """Ask the broker about every order this ledger is still waiting on, and record the answer.

    Entries: a `pending` row with an entry order id becomes `open` at the ACTUAL net credit once
    filled (`fill_confirmed_at` stamped), or `cancelled` with zero P&L if the order died -- the
    slot it held is freed either way. Stops: a side whose stop order is `pending` is recorded
    `filled` or the terminal state; a terminal state is logged CRITICAL, because the ledger has
    the side closed and the broker does not (see the module docstring for the follow-up)."""
    counts = {"entries_confirmed": 0, "entries_cancelled": 0, "stops_confirmed": 0, "stops_dead": 0}
    now = str(paper._now_et())
    for trade in paper._get_open_trades(symbol, EXECUTION_MODE, snapshot["date"], db_path):
        ic_order_id = trade["ic_order_id"]
        entry_oid = trade.get("put_spread_entry_order_id")
        if trade.get("status") == "pending" and entry_oid:
            state, price = _execution.fill_state(
                broker.status(entry_oid), fallback_price=trade.get("net_credit")
            )
            if state == "filled":
                paper._update_trade(
                    ic_order_id, {"status": "open", "net_credit": price, "fill_confirmed_at": now}, db_path
                )
                log(f"entry FILLED {ic_order_id}: asked {trade.get('net_credit')}, filled {price}")
                counts["entries_confirmed"] += 1
            elif state != "working":
                paper._update_trade(
                    ic_order_id,
                    {
                        "status": "cancelled",
                        "exit_time": now,
                        "exit_reason": f"entry_{state}",
                        "pnl": 0,
                        "fees": 0,
                    },
                    db_path,
                )
                log(f"entry {state.upper()} {ic_order_id} -- never established, slot freed")
                counts["entries_cancelled"] += 1
            continue
        for side in ("put", "call"):
            oid = trade.get(f"{side}_stop_order_id")
            if not oid or trade.get(f"{side}_stop_fill_status") != "pending":
                continue
            state, price = _execution.fill_state(broker.status(oid))
            if state == "working":
                continue
            paper._update_trade(ic_order_id, {f"{side}_stop_fill_status": state}, db_path)
            if state == "filled":
                log(f"{side} stop FILLED {ic_order_id} at {price}")
                counts["stops_confirmed"] += 1
            else:
                log(
                    f"CRITICAL: {side} stop {state.upper()} for {ic_order_id} -- the ledger has this side "
                    f"closed and the broker still holds it OPEN; manage by hand"
                )
                counts["stops_dead"] += 1
    return counts


def run_once(config: dict, snapshot: dict, db_path: str, broker, *, live: bool, log=print) -> dict:
    """One live iteration for the pinned `live.symbol`. `broker` is the injected submission
    seam -- an object with `place(spec, live) -> {ok, response?, error?}`."""
    symbol = (config.get("live") or {}).get("symbol")
    params = paper._merged_params(config, {})
    # Broker truth first: what filled, what died. Only the live ledger has orders to confirm.
    fills = _confirm_fills(symbol, snapshot, db_path, broker, log=log) if live else {}
    manage = _manage_open_trades(symbol, snapshot, params, db_path, broker, live=live, log=log)
    entry = _manage_entry(symbol, snapshot, params, db_path, broker, live=live, log=log)
    return {"symbol": symbol, "live": live, **fills, **manage, "entry": entry}


def make_broker(config: dict, designated: str | None) -> _execution.Broker:
    """The live submission seam: `cherrypick.core.execution.Broker` with this module's session,
    designated account, readiness gates (re-checked on every live submit), serializer and deploy
    cap injected. Replaced (2026-09-17) an adapter that shelled out to `tt.py execute_trade` and
    scraped the last JSON line of its stdout."""
    from cherrypick.meic import tt as _tt
    from cherrypick.meic.session import get_session

    return _execution.Broker(
        get_session=get_session,
        designated_account=lambda: designated,
        live_gates=lambda: readiness(
            config, halt_present=os.path.exists(halt_flag_path()), designated=designated
        ),
        serialize=_tt._serialize,
        deploy_limit_pct=config.get("account_deploy_limit_pct") or None,
    )


def _build_snapshot(cfg: dict, symbol: str):
    """Reuses `paper_loop.py`'s own market-data fetch helpers for one pinned symbol, rather
    than duplicating ~100 lines of fetch logic. Returns (snapshot, error_or_None)."""
    now = _pl._now_et()
    today = now.strftime("%Y-%m-%d")
    now_et = now.strftime("%H:%M")
    vix = _pl._fetch_vix()
    vix1d = _pl._run_json(_pl._TT + ["get_vix1d"]).get("last")
    vix1d_ratio = round(vix1d / vix, 3) if (vix1d and vix) else None
    delta_target = _pl._delta_target(cfg, vix)
    session = _pl._session_quality(now)

    price, ivr, ivp = _pl._fetch_overview(symbol)
    if price is None:
        return None, "no price"
    widths = cfg.get("wing_widths_by_symbol", {}).get(symbol) or cfg.get("wing_widths_by_symbol", {}).get(
        "DEFAULT", []
    )
    candidates, leg_quotes, cand_err = _pl._build_candidates(
        symbol, price, widths, [delta_target], delta_target, today
    )
    if cand_err and not candidates:
        return None, cand_err
    gex = _pl._run_json(_pl._TT + ["get_gex", "--symbol", symbol])
    atr_lookback = int(cfg.get("regime_atr_lookback_days", 5))
    snapshot = {
        "symbol": symbol,
        "date": today,
        "now_et": now_et,
        "expiration": today,
        "dte": 0,
        "underlying_price": price,
        "iv_rank": ivr,
        "iv_pct": ivp,
        "iv_rank_source": "native",
        "vix": vix,
        "vix1d_ratio": vix1d_ratio,
        "atr_5day": _pl._fetch_atr(symbol, atr_lookback),
        "intraday_range_pct": _pl._fetch_intraday_range_pct(symbol),
        "session_quality": session,
        "gex": gex if gex.get("ok") else {"ok": False},
        "candidates": candidates,
        "leg_quotes": leg_quotes,
    }
    return snapshot, None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", required=True, help="Single iteration (the only mode)")
    ap.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="Preflight orders, place nothing (DEFAULT -- this is the rung-1 dry-run smoke)",
    )
    ap.add_argument(
        "--live",
        dest="dry_run",
        action="store_false",
        help="Place real orders. Requires every readiness gate AND the daily-loss breaker clear.",
    )
    args = ap.parse_args()

    cfg = paper.load_base_config()
    designated = _designated_account()
    unmet = readiness(cfg, halt_present=os.path.exists(halt_flag_path()), designated=designated)
    live = not args.dry_run
    if live and unmet:
        print(json.dumps({"ok": False, "error": "live gates unmet", "unmet": unmet}))
        return 1
    if unmet:
        print(f"note: dry-run with unmet live gates: {unmet}", file=sys.stderr)

    symbol = (cfg.get("live") or {}).get("symbol")
    if not symbol:
        print(json.dumps({"ok": False, "error": "live.symbol is unset"}))
        return 1

    # Declare the pinned symbol and the LIVE ledger's open legs to the streamer before anything
    # is priced -- best-effort, never fatal (see stream_request.register_live). Dry-run included:
    # the request file is what keeps an already-open live IC's legs quoted between ticks, and a
    # dry-run tick that manages real positions needs them fresh exactly as much as a live one.
    _stream_request.register_live(cfg)

    snapshot, err = _build_snapshot(cfg, symbol)
    if snapshot is None:
        print(json.dumps({"ok": False, "error": f"no snapshot: {err}"}))
        return 1

    db_path = str(_paths.live_db_path())
    limit = (cfg.get("live") or {}).get("daily_loss_halt_dollars")
    if live and daily_loss_tripped(db_path, snapshot["date"], limit):
        print(json.dumps({"ok": False, "error": "daily-loss breaker tripped -- no new entries"}))
        return 1

    summary = run_once(cfg, snapshot, db_path, make_broker(cfg, designated), live=live)
    print(
        json.dumps({"ok": True, "at": datetime.now().isoformat(timespec="seconds"), **summary}, default=str)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
