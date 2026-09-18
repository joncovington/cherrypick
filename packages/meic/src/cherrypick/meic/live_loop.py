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
Exits are recorded on confirmation too (2026-09-17, second pass). A close order's decision is
stashed on the row (`pending_exit_json`) and the side marked `{side}_stop_fill_status='pending'`;
the ledger's exit accounting (`paper._apply_exit_decision`, the same function paper runs) is
applied only when the broker confirms the fill, with the ACTUAL price in the modeled price's
place. A close still working on the next tick is cancelled and replaced at the stop rule off
fresh quotes -- paper fills a stop instantly at the limit; live re-prices every minute, and the
gap between the two is the pilot's measurement. A close that dies unfilled simply clears its
marker: the side was never recorded closed, so the next tick re-evaluates it under the same rule.
Nothing is reopened because nothing was closed early. An unfilled end-of-day force-close on a
cash-settled side falls through to settlement, recorded as expired, the path a held side takes.

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
from cherrypick.core import home as _home  # noqa: E402

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
    """The suite-wide live kill switch; presence is the signal. Resolved through
    `cherrypick.core.home.halt_flag_path` -- the hand-rolled copy this replaced skipped `$VAR`
    expansion, so a `CHERRYPICK_HOME` the orchestrator understood could point this loop at a
    file nobody ever touched."""
    return str(_home.halt_flag_path())


def _designated_account() -> str | None:
    return _creds.store.designated_account()


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


def _closing_sides(trade: dict) -> list[str]:
    """Sides with a close order working at the broker: submitted, not yet confirmed either way."""
    return [
        s
        for s in ("put", "call")
        if trade.get(f"{s}_stop_fill_status") == "pending" and trade.get(f"{s}_stop_order_id")
    ]


def _trade_row(ic_order_id: str, db_path: str) -> dict | None:
    """The row as it stands now, read directly -- the exit accounting must see the latest fees,
    P&L and stop costs, not the copy the tick started with."""
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM ic_trades WHERE ic_order_id = ?", (ic_order_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _trades_with_working_orders(symbol: str, trade_date: str, db_path: str) -> list[dict]:
    """Every row with an order the broker still owes an answer on -- a pending entry, or a close
    working on either side -- whatever the row's status. Read directly rather than through
    `get_open_trades`, whose status set would drop a row whose first side's close has already
    been recorded."""
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM ic_trades WHERE symbol = ? AND trade_date = ? AND execution_mode = ? AND ("
            "status = 'pending' OR put_stop_fill_status = 'pending' OR call_stop_fill_status = 'pending')",
            (symbol, trade_date, EXECUTION_MODE),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _exit_for_side(stashed: dict, side: str, price: float | None) -> dict:
    """The per-side decision to apply once `side`'s close has filled at `price`: the stashed
    decision with its action narrowed to this side and the actual price in the modeled one's
    place. A stop becomes `stop_<side>`; a force-close stays `force_close` with only this side
    open. The other side's modeled slippage is dropped so it is not charged on this side's fill."""
    other = "call" if side == "put" else "put"
    base = dict(stashed.get("decision") or {})
    action = str(stashed.get("action") or base.get("action") or "")
    out = {**base, f"{side}_exit_price": price, f"{other}_exit_slippage": None}
    if action == "force_close":
        out.update({"action": "force_close", "put_open": side == "put", "call_open": side == "call"})
    else:
        out["action"] = f"stop_{side}"
    return out


def _submit_close(
    trade: dict, side: str, spec: dict, decision: dict, broker, *, live: bool, db_path: str, log
) -> bool:
    """Place one side's close and, live, mark the side closing with its decision stashed. Nothing
    is recorded closed here -- `_confirm_fills` does that on the broker's word."""
    result = broker.place(spec, live=live)
    log(
        f"close {side} ({'LIVE' if live else 'dry-run'}) for {trade['ic_order_id']}: {json.dumps(result, default=str)[:300]}"
    )
    if not result.get("ok"):
        return False
    if not live:
        return True
    oid = _extract_order_id(result)
    if oid is None:
        log(f"WARNING: close {side} for {trade['ic_order_id']} returned no order id; cannot track it")
        return False
    paper._update_trade(
        trade["ic_order_id"],
        {
            f"{side}_stop_order_id": oid,
            f"{side}_stop_fill_status": "pending",
            # The stash carries the limit ASKED for this side, never the modeled price: it is the
            # fallback if a fill comes back without a parseable price, and the model is not a fill.
            "pending_exit_json": json.dumps(
                {"action": decision["action"], "decision": {**decision, f"{side}_exit_price": spec["price"]}}
            ),
        },
        db_path,
    )
    return True


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
    counts = {"stopped": 0, "force_closed": 0, "expired": 0, "held": 0, "closing": 0, "order_failed": 0}
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
            # Cash-settled left-to-expire: nothing to submit, settlement is automatic. A close
            # still working is cancelled first -- settlement supersedes it (an unfilled end-of-day
            # close on a cash-settled side falls through to expiry by design).
            for side in _closing_sides(trade):
                res = (
                    broker.cancel(trade[f"{side}_stop_order_id"])
                    if hasattr(broker, "cancel")
                    else {"ok": True}
                )
                if res.get("ok"):
                    paper._update_trade(
                        trade["ic_order_id"], {f"{side}_stop_fill_status": "cancelled"}, db_path
                    )
                    log(f"cancelled working {side} close for {trade['ic_order_id']} -- settling instead")
            counts["expired"] += 1
            paper._apply_exit_decision(trade, decision, symbol, db_path)
            continue

        # Every close action (stop_call / stop_put / stop_both / force_close) reduces to one or
        # two independent per-side 2-leg close orders -- force_close is simply "close whichever
        # side(s) are still open", not a distinct order shape. A side whose close is already
        # working is skipped: `_confirm_fills` owns it (confirm, or cancel-and-replace) until the
        # broker answers.
        closing = _closing_sides(trade)
        sides = [side for side in _sides_to_close(decision) if side not in closing]
        if not sides:
            counts["closing"] += 1
            continue
        try:
            specs = {
                side: live_orders.stop_close_spec(trade, side, leg_quotes, stop_limit_ratio) for side in sides
            }
        except ValueError as exc:
            log(f"CRITICAL: could not build close order for {trade['ic_order_id']} ({action}): {exc}")
            counts["order_failed"] += 1
            continue

        placed = {
            side: _submit_close(trade, side, spec, decision, broker, live=live, db_path=db_path, log=log)
            for side, spec in specs.items()
        }
        if not all(placed.values()):
            log(f"CRITICAL: close order failed for {trade['ic_order_id']} ({action}) -- position stays open")
            counts["order_failed"] += 1
            continue
        if not live:
            # A dry run placed nothing, so there is nothing to confirm; the ledger stays as it was.
            counts["force_closed" if action == "force_close" else "stopped"] += 1
            continue
        # Live: recorded on CONFIRMATION (see the module docstring), never here.
        counts["closing"] += 1

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
    # The mid the limit was asked against: the modeled credit is mid minus the haircut, and the
    # row carries the haircut in dollars, so the mid is recoverable here and nowhere later.
    row["entry_mid_at_submit"] = round(
        float(chosen["net_credit"]) + float(row["slippage_dollars"]) / 100.0, 4
    )
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


def _confirm_fills(
    symbol: str, snapshot: dict, db_path: str, broker, *, log, params: dict | None = None
) -> dict:
    """Ask the broker about every order this ledger is still waiting on, and record the answer.

    Entries: a `pending` row with an entry order id becomes `open` at the ACTUAL net credit once
    filled (`fill_confirmed_at` stamped), or `cancelled` with zero P&L if the order died -- the
    slot it held is freed either way.

    Closes (2026-09-17): a side whose close order FILLED has its stashed exit decision applied
    now, through `paper._apply_exit_decision`, with the actual fill price -- the first moment the
    ledger records the side closed. A close that DIED (cancelled / rejected / expired) just clears
    its marker: nothing was recorded, so the next `_manage_open_trades` re-evaluates the side
    under the same rule. A close still WORKING is cancelled and replaced at the stop rule off
    this tick's quotes -- live's minute-by-minute stand-in for paper's instant fill at the limit.
    """
    counts = {
        "entries_confirmed": 0,
        "entries_cancelled": 0,
        "closes_filled": 0,
        "closes_dead": 0,
        "closes_repriced": 0,
    }
    now = str(paper._now_et())
    leg_quotes = snapshot.get("leg_quotes", {})
    ratio = (params or {}).get("stop_limit_ratio", 1.02)
    for trade in _trades_with_working_orders(symbol, snapshot["date"], db_path):
        ic_order_id = trade["ic_order_id"]
        entry_oid = trade.get("put_spread_entry_order_id")
        if trade.get("status") == "pending" and entry_oid:
            state, price = _execution.fill_state(
                broker.status(entry_oid), fallback_price=trade.get("net_credit")
            )
            if state == "filled":
                fields = {"status": "open", "net_credit": price, "fill_confirmed_at": now}
                mid = trade.get("entry_mid_at_submit")
                if mid is not None and price is not None:
                    # Measured, not modeled: what the fill conceded to the spread against the mid
                    # the limit was asked from. Negative is price improvement. Exit sides stay
                    # modeled (their asked price is a crossing limit, not a mid) and say so.
                    qty = int(trade.get("quantity") or 1)
                    fields["slippage_dollars"] = round((float(mid) - float(price)) * 100.0 * qty, 4)
                paper._update_trade(ic_order_id, fields, db_path)
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
        for side in _closing_sides(trade):
            oid = trade[f"{side}_stop_order_id"]
            stashed = {}
            try:
                stashed = json.loads(trade.get("pending_exit_json") or "{}")
            except ValueError:
                stashed = {}
            modeled = (stashed.get("decision") or {}).get(f"{side}_exit_price")
            state, price = _execution.fill_state(broker.status(oid), fallback_price=modeled)
            if state == "filled":
                fresh = _trade_row(ic_order_id, db_path) or trade
                paper._apply_exit_decision(fresh, _exit_for_side(stashed, side, price), symbol, db_path)
                paper._update_trade(ic_order_id, {f"{side}_stop_fill_status": "filled"}, db_path)
                log(f"{side} close FILLED {ic_order_id} at {price} (modeled {modeled})")
                counts["closes_filled"] += 1
            elif state != "working":
                # Never recorded closed, so nothing to reopen: clear the marker and let the next
                # evaluation decide the side again under the same rule.
                paper._update_trade(ic_order_id, {f"{side}_stop_fill_status": state}, db_path)
                log(
                    f"{side} close {state.upper()} {ic_order_id} -- side is open again, re-evaluated next tick"
                )
                counts["closes_dead"] += 1
            else:
                # Still working: cancel and replace at the stop rule off THIS tick's quotes.
                try:
                    spec = live_orders.stop_close_spec(trade, side, leg_quotes, ratio)
                except ValueError as exc:
                    log(
                        f"{side} close for {ic_order_id} still working; cannot re-price ({exc}) -- left resting"
                    )
                    continue
                cancelled = broker.cancel(oid) if hasattr(broker, "cancel") else {"ok": False}
                if not cancelled.get("ok"):
                    # A cancel that fails is usually the fill racing it: the next tick's status
                    # poll resolves it. Never place a second order on top of one we could not cancel.
                    log(
                        f"{side} close for {ic_order_id}: cancel refused ({cancelled.get('error')}) -- re-polling next tick"
                    )
                    continue
                decision = stashed.get("decision") or {"action": f"stop_{side}"}
                decision = {**decision, f"{side}_exit_price": spec["price"]}
                if _submit_close(trade, side, spec, decision, broker, live=True, db_path=db_path, log=log):
                    counts["closes_repriced"] += 1
                else:
                    paper._update_trade(ic_order_id, {f"{side}_stop_fill_status": "cancelled"}, db_path)
                    log(
                        f"CRITICAL: {side} close for {ic_order_id} cancelled but the replacement failed -- side open, re-evaluated next tick"
                    )
    return counts


def run_once(config: dict, snapshot: dict, db_path: str, broker, *, live: bool, log=print) -> dict:
    """One live iteration for the pinned `live.symbol`. `broker` is the injected submission
    seam -- an object with `place(spec, live) -> {ok, response?, error?}`."""
    symbol = (config.get("live") or {}).get("symbol")
    params = paper._merged_params(config, {})
    # Broker truth first: what filled, what died. Only the live ledger has orders to confirm.
    fills = _confirm_fills(symbol, snapshot, db_path, broker, log=log, params=params) if live else {}
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
