"""The ladder's live tick (issue #1337, part 3): the one module that touches the broker.

`live_loop` holds the commands (arm, disarm, status, once) and imports nothing that trades; this module
holds the broker adapter, the reconcile and the tick, and a source scan in the tests keeps trading
imports out of every other ladder file. The adapter is built inside `run_once`, never at import.

**The tick, in this order** (`run_once`):

  0. the dead-man's switch: an arm record dated another day is removed before any broker read
  1. gates: every gate that blocks everything (all but `arm.record`) must hold, or nothing is read
  2. orphan sweep: working orders at the broker carrying one of our client ids (`ldr.`) that the
     ledger has never heard of. Other modules' orders in the same account are not ours and are never
     counted. Detection and adoption only: the reconcile records them by their client id
  3. **reconcile**, before any plan: read the broker's word on every order the ledger holds open (and
     adopt ours the ledger lacks), turn filled buys into lots and filled sells into closed lots with
     realized money, then refine fees from the broker's transaction rows. A fill the ledger has not
     seen yet cannot reach `plan`, so a resting buy that already filled is never cancelled and
     re-placed. A read that fails leaves the tick without a plan (`reconcile_incomplete`)
  4. **splits** (before the reconcile, ledger only): each `live.splits` entry gone ex since a lot was
     bought restates that lot once (`apply_splits`), so the planner prices the new terms
  5. **positions cross-check**, after the reconcile and before the plan (`check_positions`): the broker's
     equity shares per fund against the ledger's open live shares (`live_checks`). A shortfall, a
     suspected split or an unreadable read stops that fund's (or every fund's) new buys and leaves
     the resting sells alone
  6. plan inputs, `orders.plan`, `orders.Executor`

**Unarmed**, and before `session_start`, the tick stops after the reconcile: it reads, it places and
cancels nothing. **At `disarm_at`** the tick still runs once more while the arm is valid: the planner
cancels every resting buy (and never a sell), a second reconcile-and-plan pass catches a buy that
filled as it was cancelled and rests its sell, and only then does the loop disarm itself.

**Dry-run is the default** (`live.dry_run`): the Executor logs what it would send into the decisions
journal (`mode = dry_run`) and the broker is read, never written. A real submission needs
`live.dry_run: false` and every gate, re-checked by the adapter on each placement.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from datetime import date, datetime

from cherrypick.core import auth as _auth
from cherrypick.core import broker as _broker
from cherrypick.core import calendar as _cal
from cherrypick.core import execution as _execution
from cherrypick.core import fees as _fees
from cherrypick.core import home as _home
from cherrypick.core import logs as _logs
from cherrypick.core import looplock
from cherrypick.core import redact as _redact
from cherrypick.core.clock import ET, hhmm_to_min, minute_of_day, now_et, now_iso

from cherrypick.ladder import live_checks, live_db, live_gates, live_loop, orders, paper_loop, provider, rules

_logger = logging.getLogger("ladder_live_tick")

FEE_LOOKBACK_DAYS = 5  # a modeled fee is refined from the broker's rows for this long after the fill
FEE_READS_PER_TICK = 3
REVIEW_MARK = "partial_sell_review"  # an open lot's exit_reason while a person looks at a partly sold lot


def log_file():
    return _home.logs_dir("ladder") / "ladder_live.log"


def _log(message: str) -> None:
    _logs.configure(_logger, log_file())
    _logger.info(_redact.redact_accounts(message))


def _data_dir() -> str:
    return os.path.dirname(live_db.live_db_path())


def hold_path() -> str:
    """Where an uncertain submit's hold lives, so it outlasts the one-minute process that made it."""
    return os.path.join(_data_dir(), "live_held.json")


def orphans_path() -> str:
    return os.path.join(_data_dir(), "live_orphans.json")


def once_lock_path() -> str:
    return os.path.join(_data_dir(), "live_once.lock")


def checks_path() -> str:
    """The last tick's splits, positions cross-check and dry-run intents, for `--status` (no broker)."""
    return os.path.join(_data_dir(), "live_checks.json")


def read_checks() -> dict:
    try:
        with open(checks_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_checks(state: dict) -> None:
    try:
        os.makedirs(_data_dir(), exist_ok=True)
        tmp = checks_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, default=str)
        os.replace(tmp, checks_path())
    except OSError:
        pass  # a status note must never break a tick


def dry_run(config: dict) -> bool:
    """Whether the loop sends nothing. True unless `live.dry_run` is exactly `false`: a missing key, a
    string or a null all stay dry."""
    return live_gates.live_block(config).get("dry_run") is not False


def read_held() -> dict:
    """What a persisted hold refuses, from the file alone (no broker): for `--status`."""
    return _execution.read_hold(hold_path())


def read_orphans() -> list[dict]:
    return _execution.read_orphans(orphans_path())


# --------------------------------------------------------------------------- the broker seam
class BrokerAdapter(_execution.Broker):
    """`cherrypick.core.execution.Broker` with the ladder's credentials, designated account and live
    gates injected, and a hold that outlives the process.

    Every live placement re-reads the gates, so a gate flipped mid-tick (the arm record removed, the
    designation cleared, the halt flag set) bites on the next order. Gates that block everything stop
    a sell too; the halt flag stops an order that opens a position (a buy) and nothing else. Real
    submission also needs `live.dry_run: false`: the adapter refuses while it is true, whatever the
    caller passed."""

    def __init__(self, config: dict):
        from cherrypick.ladder import credentials

        self._config = config
        self.gate_when: datetime | None = (
            None  # the clock the gates are read at; the tick pins it at disarm_at
        )
        sessions = _auth.SessionManager(credentials.store)
        super().__init__(
            get_session=sessions.get_session,
            designated_account=lambda: live_loop._designated(),
            live_gates=self._unmet_for_all,
            hold_path=hold_path(),
        )

    def _gates(self):
        return live_loop.gates_now(self._config, when=self.gate_when or now_et())

    def _unmet_for_all(self) -> list[str]:
        unmet = [f"{g.name}: {g.reason}" for g in self._gates() if not g.ok and g.blocks == "all"]
        if dry_run(self._config):
            unmet.append("live.dry_run: true -- the loop sends nothing")
        return unmet

    def place(self, spec: dict, live: bool) -> dict:
        if live and _broker.opens_position(spec):
            blocked = [f"{g.name}: {g.reason}" for g in self._gates() if not g.ok and g.blocks == "new_buys"]
            if blocked:
                return {"ok": False, "error": "live submission gated", "unmet_gates": blocked}
        return super().place(spec, live)


# --------------------------------------------------------------------------- the journal
def _journal(conn, *, day: str, arm: str, symbol: str, mode: str, reason: str, accepted: bool, detail=None):
    live_db.record_decision(
        conn,
        trade_date=day,
        arm=arm,
        symbol=symbol or "-",
        mode=mode,
        reason=_redact.redact_accounts(reason),
        accepted=accepted,
        detail=_redact.redact_accounts(json.dumps(detail, default=str)) if detail is not None else None,
    )


# --------------------------------------------------------------------------- orphans
def sweep_orphans(conn, broker, log=_log) -> int | None:
    """Working orders carrying one of our client ids that the ledger has never recorded, or None when
    the broker could not be read (unknown is not zero). Other modules' orders in the account have other
    client ids (or none) and are not counted. Also publishes our order ids for the other loops' sweeps."""
    known = live_db.known_order_ids(conn)
    _execution.publish_known_orders("ladder", known, now_iso=now_iso())
    try:
        found = _execution.orphans(broker, known)
    except Exception as exc:  # noqa: BLE001 -- a failed sweep must not break the tick, and must not read as clean
        log(f"orphan sweep failed ({type(exc).__name__}: {exc}); will retry next tick")
        return None
    found = [o for o in found if str(o.get("external_identifier") or "").startswith(orders.PREFIX + ".")]
    try:
        os.makedirs(_data_dir(), exist_ok=True)
        with open(orphans_path(), "w", encoding="utf-8") as f:
            json.dump({"at": now_iso(), "orphans": found}, f)
    except OSError:
        pass
    if found:
        log(
            f"ORPHANED LADDER ORDERS at the broker, unknown to the ledger: "
            f"{[o.get('order_id') for o in found]}; "
            "adopting them by client id in the reconcile"
        )
    return len(found)


# --------------------------------------------------------------------------- reconcile
def _fills(status: dict) -> tuple[int, float | None, str | None]:
    """(shares filled, their average price, latest fill time) from an order status row's fills."""
    qty, cash, latest = 0.0, 0.0, None
    for f in status.get("fills") or []:
        try:
            q, p = abs(float(f["quantity"])), float(f["fill_price"])
        except (KeyError, TypeError, ValueError):
            continue
        qty += q
        cash += q * p
        at = f.get("filled_at")
        if at and (latest is None or str(at) > latest):
            latest = str(at)
    if qty <= 0:
        return 0, None, latest
    return int(round(qty)), cash / qty, latest


def _et_date(stamp: str | None, fallback: datetime) -> date:
    try:
        return datetime.fromisoformat(str(stamp)).astimezone(ET).date()
    except (TypeError, ValueError):
        return fallback.astimezone(ET).date()


def _lot_key(position_id: str) -> str:
    return orders._slug(position_id)


def _adopt(conn, o: dict, parsed: dict, account: str | None, day: str) -> dict:
    row = {
        "client_id": str(o["external_identifier"]),
        "order_id": str(o["order_id"]),
        "side": parsed["side"],
        "symbol": parsed.get("symbol") or "",
        "lot_id": None,
        "shares": parsed["shares"],
        "limit_price": parsed["price"],
        "account": account,
        "state": str(o.get("status") or "working").strip().lower(),
        "final": 0,
        "placed_at": now_iso(),
        "placed_session": day,
        "adopted": 1,
    }
    if parsed["side"] == "sell":
        lots = {_lot_key(lot["position_id"]): lot for lot in live_db.open_lots(conn)}
        match = lots.get(parsed["lot"])
        if match:
            row["lot_id"], row["symbol"] = match["position_id"], match["symbol"]
        else:
            row["symbol"] = str(o.get("underlying_symbol") or "")
    live_db.save_order(conn, row)
    return row


def _lot_from_buy(conn, row: dict, rule: dict, arm: str, qty: int, avg: float, at: datetime) -> dict | None:
    parsed = orders.parse_id(row["client_id"])
    if parsed is None or parsed["side"] != "buy":
        return None
    session = f"{parsed['session'][:4]}-{parsed['session'][4:6]}-{parsed['session'][6:]}"
    position_id = f"{arm}:{parsed['symbol']}:{session}:{parsed['n']}"
    if live_db.lot(conn, position_id) is not None:
        return None
    fee = _fees.stock_trade_fee(qty, avg, side="buy", ndigits=4)
    tgt = rules.target(rule["target"], cost=avg, shares=qty, age=0, gain=float(rule["gain"]))
    lot = {
        "position_id": position_id,
        "arm": arm,
        "symbol": parsed["symbol"],
        "entry_rule": rule["entry"],
        "target_rule": rule["target"],
        "quantity": qty,
        "buy_level": parsed["price"],
        "buy_price": round(avg, 4),
        "buy_session": session,
        "buy_time": at.astimezone(ET).isoformat(timespec="seconds"),
        "status": "open",
        "target_price": tgt,
        "target_stage": "gain",
        "entry_value": round(-qty * avg, 2),
        "account": row.get("account"),
        "buy_order_id": row["order_id"],
        "buy_client_id": row["client_id"],
        "buy_fees": fee,
        "fees_source": "modeled",
    }
    live_db.save_position(conn, lot)
    return lot


def _close_lot(conn, row: dict, lot: dict, rule: dict, qty: int, avg: float, at: datetime) -> dict:
    sell_session = at.astimezone(ET).date()
    age = rules.age_days(date.fromisoformat(lot["buy_session"]), sell_session)
    stage = rules.target_stage(rule["target"], age)
    sell_fee = _fees.stock_trade_fee(qty, avg, side="sell", ndigits=4)
    update = {
        "position_id": lot["position_id"],
        "status": "closed",
        "sell_price": round(avg, 4),
        "sell_session": sell_session.isoformat(),
        "sell_time": at.astimezone(ET).isoformat(timespec="seconds"),
        "exit_reason": f"target_{stage}",
        "target_stage": stage,
        "hold_days": age,
        "sell_order_id": row["order_id"],
        "sell_client_id": row["client_id"],
        "sell_fees": sell_fee,
        "fees_source": "modeled",
        **_money(lot, qty, avg, lot.get("buy_fees"), sell_fee),
    }
    live_db.save_position(conn, update)
    return update


def _money(lot: dict, qty: int, sell_price: float, buy_fees, sell_fees) -> dict:
    """A closed lot's money: signed cash flow, whole-position dollars, rows that add up."""
    entry = float(lot["entry_value"])
    exit_value = round(qty * sell_price, 2)
    gross = round(entry + exit_value, 2)
    fees = round(float(buy_fees or 0.0) + float(sell_fees or 0.0), 2)
    net = round(gross - fees, 2)
    return {
        "exit_value": exit_value,
        "gross_pnl": gross,
        "fees": fees,
        "settlement": 0.0,
        "slippage": 0.0,
        "net_pnl": net,
        "net_pct": round(100.0 * net / -entry, 4) if entry else None,
    }


def _apply_status(
    conn, row: dict, status: dict, rule: dict, arm: str, when: datetime, out: dict, log
) -> None:
    """Fold one order's broker status into the ledger. Idempotent: a final order is never touched again."""
    state, price = _execution.fill_state(status, fallback_price=row["limit_price"])
    day = when.astimezone(ET).date().isoformat()
    if state == "working":
        text = str(status.get("status") or "").strip().lower()
        if text and text != row["state"]:
            live_db.save_order(conn, {"client_id": row["client_id"], "state": text})
        return
    qty, avg, latest = _fills(status)
    source = "fills"
    if state == "filled" and qty == 0:  # filled, but the broker itemised nothing: the order's own price
        qty, avg, source = int(row["shares"]), price, "order_price"
    at = datetime.fromisoformat(latest) if latest else when
    update = {
        "client_id": row["client_id"],
        "state": state,
        "final": 1,
        "filled_shares": qty or None,
        "fill_price": round(avg, 4) if avg else None,
        "filled_at": at.astimezone(ET).isoformat(timespec="seconds") if qty else None,
        "fill_source": source if qty else None,
        "ended_session": None if qty else day,
    }
    if qty:
        update["fees"] = _fees.stock_trade_fee(qty, avg, side=row["side"], ndigits=4)
        update["fees_source"] = "modeled"
    live_db.save_order(conn, update)
    row = {**row, **update}
    if not qty:
        log(f"order {row['client_id']} ended {state}, unfilled")
        return
    if row["side"] == "buy":
        lot = _lot_from_buy(conn, row, rule, arm, qty, avg, at)
        if lot:
            out["new_lots"].append(lot["position_id"])
            if state != "filled" and qty < int(row["shares"]):
                out["alerts"].append({"kind": "partial_buy", "lot": lot["position_id"], "filled": qty})
            log(f"buy filled: {lot['symbol']} {qty} at {avg:.4f} (lot {lot['position_id']})")
        return
    parsed = orders.parse_id(row["client_id"]) or {}
    lot = next((x for x in live_db.open_lots(conn) if _lot_key(x["position_id"]) == parsed.get("lot")), None)
    if lot is None:
        out["alerts"].append({"kind": "sell_filled_for_no_open_lot", "order": row["client_id"]})
        return
    if qty < int(lot["quantity"]):
        # Shares were sold and the order ended. The lot's cost no longer matches its shares, so it
        # waits for a person: it stays open, and no further sell is planned for it (REVIEW_MARK).
        live_db.save_position(conn, {"position_id": lot["position_id"], "exit_reason": REVIEW_MARK})
        out["alerts"].append({"kind": "partial_sell", "lot": lot["position_id"], "sold": qty})
        log(f"PARTIAL SELL on {lot['position_id']}: {qty} of {lot['quantity']}; left open for a person")
        return
    live_db.save_order(conn, {"client_id": row["client_id"], "lot_id": lot["position_id"]})
    closed = _close_lot(conn, row, lot, rule, qty, avg, at)
    out["closed_lots"].append(lot["position_id"])
    log(f"sold {lot['symbol']} {qty} at {avg:.4f} (lot {lot['position_id']}), net {closed['net_pnl']:.2f}")


def _refine_fees(conn, broker, when: datetime, log) -> int:
    """Replace a filled order's modeled fee with the broker's own transaction rows, when they are
    readable and carry the order. Never invents a fee: a read that fails or finds nothing leaves the
    model in place for the next tick."""
    today = when.astimezone(ET).date()
    pending = [
        r
        for r in live_db.orders(conn)
        if r["final"] and r["state"] == "filled" and r.get("fees_source") == "modeled" and r.get("filled_at")
    ]
    by_day: dict[str, list[dict]] = {}
    for r in pending:
        d = _et_date(r["filled_at"], when)
        if 0 <= (today - d).days <= FEE_LOOKBACK_DAYS:
            by_day.setdefault(d.isoformat(), []).append(r)
    refined = 0
    for d in sorted(by_day)[:FEE_READS_PER_TICK]:
        transactions, error = broker.history(d)
        if transactions is None:
            log(f"fee read for {d} failed ({error}); the modeled fees stand")
            continue
        for r in by_day[d]:
            rows = [
                t
                for t in transactions
                if t.get("transaction_type") == "Trade" and str(t.get("order_id")) == str(r["order_id"])
            ]
            if not rows:
                continue
            fee = round(sum(_fees.broker_fee_total(t) for t in rows), 4)
            live_db.save_order(conn, {"client_id": r["client_id"], "fees": fee, "fees_source": "broker"})
            _refresh_lot_fees(conn, r, fee)
            refined += 1
    return refined


def _refresh_lot_fees(conn, order_row: dict, fee: float) -> None:
    column = "buy_fees" if order_row["side"] == "buy" else "sell_fees"
    id_column = "buy_order_id" if order_row["side"] == "buy" else "sell_order_id"
    r = conn.execute(
        f"SELECT * FROM ladder_positions WHERE {id_column} = ?", (order_row["order_id"],)
    ).fetchone()
    if r is None:
        return
    lot = {**dict(r), column: fee}
    update = {"position_id": lot["position_id"], column: fee}
    if lot["status"] == "closed":
        update.update(
            _money(lot, int(lot["quantity"]), float(lot["sell_price"]), lot["buy_fees"], lot["sell_fees"])
        )
    both = [o for o in (lot.get("buy_order_id"), lot.get("sell_order_id")) if o is not None]
    sources = [(live_db.order_by_broker_id(conn, o) or {}).get("fees_source") for o in both]
    update["fees_source"] = "broker" if sources and all(s == "broker" for s in sources) else "modeled"
    live_db.save_position(conn, update)


def reconcile(conn, broker, config: dict, *, when: datetime, account: str | None, log=_log) -> dict:
    """Bring the ledger up to the broker's word. Read only toward the broker."""
    live = live_gates.live_block(config)
    arm = str(live.get("arm") or "").strip()
    rule = orders.arm_rule(config)
    out: dict = {
        "complete": True,
        "new_lots": [],
        "closed_lots": [],
        "adopted": [],
        "alerts": [],
        "unreadable": [],
    }
    if rule is None:
        return {**out, "complete": False, "error": f"live.arm {arm!r} is not an enabled paper arm"}
    day = when.astimezone(ET).date().isoformat()
    try:
        seen = {
            str(o["order_id"]): o
            for o in [*broker.orders_today(), *broker.working_orders()]
            if o.get("order_id") is not None
        }
    except Exception as exc:  # noqa: BLE001
        log(f"reconcile could not read the broker's orders ({type(exc).__name__}: {exc})")
        return {**out, "complete": False, "error": f"{type(exc).__name__}: {exc}"}
    for o in seen.values():
        eid = str(o.get("external_identifier") or "")
        parsed = orders.parse_id(eid)
        if parsed is None or live_db.order(conn, eid) is not None:
            continue
        _adopt(conn, o, parsed, account, day)
        out["adopted"].append(eid)
        log(f"adopted {eid} (order {o['order_id']}) found at the broker with no ledger row")
    for row in live_db.open_orders(conn):
        if not row.get("order_id"):
            continue
        status = broker.status(row["order_id"])
        if status.get("status") is None:
            out["complete"] = False
            out["unreadable"].append(row["client_id"])
            continue
        _apply_status(conn, row, status, rule, arm, when, out, log)
    if out["unreadable"]:
        log(f"reconcile incomplete: no status for {out['unreadable']}")
    else:
        out["fees_refined"] = _refine_fees(conn, broker, when, log)
    return out


# --------------------------------------------------------------------------- splits
def restate_lot(lot: dict, split: live_checks.Split) -> dict:
    """The lot's columns at the split's terms. Shares and cost go through the planner's own `restate`,
    so the ledger and the plan agree on how a reverse split rounds. The money columns are cash that
    moved and do not change."""
    view = orders.restate(
        {"symbol": lot["symbol"], "shares": int(lot["quantity"]), "cost": float(lot["buy_price"])},
        split.factor,
    )
    out = {"quantity": view["shares"], "buy_price": round(view["cost"], 6)}
    if lot.get("buy_level") is not None:
        out["buy_level"] = round(float(lot["buy_level"]) / split.factor, 6)
    if lot.get("target_price") is not None:
        out["target_price"] = round(float(lot["target_price"]) / split.factor, 6)
    return out


def apply_splits(conn, config: dict, *, when: datetime, arm: str, log=_log) -> dict:
    """Restate the open lots for every `live.splits` entry gone ex since the lot was bought, once each.

    Ledger-only (no broker), so it runs before the reconcile: a sell the broker adjusted for the split
    then fills against shares the ledger already counts the same way. Idempotent: each application is
    recorded with the lot in one transaction (`live_db.apply_lot_split`), so a second tick finds it
    done. The planner then sees new terms, so a resting sell at the old terms is cancelled and the
    new one placed. Returns {"problems", "applied", "alerts"}."""
    live = live_gates.live_block(config)
    splits, problems = live_checks.parse_splits(live)
    out: dict = {"problems": problems, "applied": [], "alerts": []}
    if not splits:
        return out
    today = when.astimezone(ET).date()
    day = today.isoformat()
    done = live_db.lot_splits(conn)
    for lot in live_db.open_lots(conn):
        for s in live_checks.due(splits, lot, today, done):
            update = restate_lot(lot, s)
            record = {
                "symbol": s.fund,
                "ex_date": s.ex_date,
                "to_shares": s.to,
                "for_shares": s.per,
                "shares_before": int(lot["quantity"]),
                "shares_after": update["quantity"],
                "price_before": float(lot["buy_price"]),
                "price_after": update["buy_price"],
                "applied_at": now_iso(),
            }
            if not live_db.apply_lot_split(conn, lot["position_id"], update, record):
                continue
            done.add((lot["position_id"], s.ex_date))
            what = (
                f"restated {lot['position_id']} for the {s.ratio} split of {s.fund} (ex {s.ex_date}): "
                f"{record['shares_before']} -> {record['shares_after']} shares"
            )
            log(what)
            out["applied"].append(lot["position_id"])
            _journal(conn, day=day, arm=arm, symbol=s.fund, mode="split", reason=what, accepted=True,
                     detail=record)  # fmt: skip
            if update["quantity"] < 1:
                alert = {"kind": "split_leaves_no_whole_share", "lot": lot["position_id"], "symbol": s.fund}
                out["alerts"].append(alert)
                _journal(conn, day=day, arm=arm, symbol=s.fund, mode="alert",
                         reason=f"split_leaves_no_whole_share {lot['position_id']}", accepted=False,
                         detail=alert)  # fmt: skip
            lot = {**lot, **update}
    return out


# --------------------------------------------------------------------------- positions cross-check
def check_positions(conn, broker, funds: list[str]) -> dict:
    """The ledger's open live shares per fund against the broker's equity shares (`live_checks`).
    Read only. A read that raises, or a broker with no positions read, is unreadable, never empty."""
    ledger: dict[str, int] = {}
    for lot in live_db.open_lots(conn):
        symbol = str(lot["symbol"]).upper()
        ledger[symbol] = ledger.get(symbol, 0) + int(lot["quantity"])
    try:
        rows = broker.positions()
    except Exception:  # noqa: BLE001
        rows = None
    return live_checks.cross_check(ledger, rows, funds)


# --------------------------------------------------------------------------- plan inputs
def plan_inputs(conn, config: dict, rule: dict, *, when: datetime, cache_path: str) -> dict:
    live = live_gates.live_block(config)
    arm = str(live["arm"]).strip()
    today = when.astimezone(ET).date()
    session = today.isoformat()
    symbols = [str(s).upper() for s in live.get("symbols") or []]
    held = [lot for lot in live_db.open_lots(conn, arm) if lot.get("exit_reason") != REVIEW_MARK]
    max_age = float(paper_loop.defaults(config)["max_quote_age_seconds"])
    marks: dict[str, float | None] = {}
    for symbol in dict.fromkeys([*symbols, *(lot["symbol"].upper() for lot in held)]):
        q = provider.quote(cache_path, symbol, max_quote_age_seconds=max_age)
        marks[symbol] = float(q["bid"]) if q else None
    anchors = {}
    if rule["entry"] == "chain":
        anchors = {s: paper_loop.chain_anchor(live_db.lots_of(conn, arm, s)) for s in symbols}
    # The ledger's lots are restated by `apply_splits` before this runs, so the planner is given no
    # factor; only the reference close (the previous session's, from the cache) can still be on the
    # old side of a split, and `restated_reference` settles that against the fund's bid.
    splits, _ = live_checks.parse_splits(live)
    prev = _cal.previous_trading_day(today)
    return {
        "ref_close": {
            s: live_checks.restated_reference(
                provider.reference_close(cache_path, s, today),
                splits,
                s,
                bid=marks.get(s),
                prev_session=prev,
                today=today,
            )
            for s in symbols
        },
        "lots": [
            {
                "id": lot["position_id"],
                "symbol": lot["symbol"],
                "shares": int(lot["quantity"]),
                "cost": float(lot["buy_price"]),
                "buy_date": lot["buy_session"],
            }
            for lot in held
        ],
        "working": [
            {"order_id": r["order_id"], "external_identifier": r["client_id"]}
            for r in live_db.open_orders(conn)
            if r.get("order_id")
        ],
        "buys_today": {s: live_db.bought_in_session(conn, arm, s, session) for s in symbols},
        "marks": marks,
        "anchors": anchors,
        "ended_ids": live_db.ended_unfilled(conn, session),
    }


# --------------------------------------------------------------------------- apply and record
def _same_terms(prior: dict | None, detail: dict) -> bool:
    """Whether the last dry-run row for this intended order states the terms it states now."""
    if prior is None:
        return False
    try:
        old = json.loads(prior.get("detail") or "null")
    except ValueError:
        return False
    return isinstance(old, dict) and all(old.get(k) == detail.get(k) for k in ("shares", "limit", "tif"))


def _record_results(
    conn, results: list[dict], *, arm: str, day: str, dry: bool, account: str | None
) -> list[dict]:
    """Journal what the executor did. Returns the dry-run intents (the orders it would send), which
    `--status` lists; the journal takes one only when it is new or its terms changed since the last
    row for that client id, so a standing intent is one row and not one per minute."""
    intents: list[dict] = []
    for r in results:
        a = r["action"]
        spec = a.get("spec") or {}
        leg = (spec.get("legs") or [{}])[0]
        what = f"{a['kind']} {a['side']} {a['symbol']} {a.get('external_identifier')}"
        detail = {"reason": a.get("reason"), "lot": a.get("lot"), "ok": r.get("ok")}
        if spec:
            detail.update(shares=leg.get("quantity"), limit=spec.get("price"), tif=spec.get("time_in_force"))
        if dry:
            intents.append({"what": what, **detail})
            reason = f"would {what}"
            if not _same_terms(live_db.last_dry_run(conn, arm, a["symbol"], reason), detail):
                _journal(conn, day=day, arm=arm, symbol=a["symbol"], mode="dry_run", reason=reason,
                         accepted=True, detail=detail)  # fmt: skip
            continue
        if not r.get("ok"):
            detail.update(error=r.get("error"), skipped=r.get("skipped"), uncertain=r.get("uncertain"),
                          unmet_gates=r.get("unmet_gates"))  # fmt: skip
            _journal(conn, day=day, arm=arm, symbol=a["symbol"], mode="refused", reason=f"{what} failed",
                     accepted=False, detail=detail)  # fmt: skip
            continue
        if a["kind"] == "place":
            live_db.save_order(
                conn,
                {
                    "client_id": a["external_identifier"],
                    "order_id": str(r["order_id"]),
                    "side": a["side"],
                    "symbol": a["symbol"],
                    "lot_id": a.get("lot"),
                    "shares": int(leg["quantity"]),
                    "limit_price": float(spec["price"]),
                    "time_in_force": spec.get("time_in_force"),
                    "account": account,
                    "state": "working",
                    "final": 0,
                    "placed_at": now_iso(),
                    "placed_session": day,
                },
            )
            if a["side"] == "sell" and a.get("lot"):
                live_db.save_position(
                    conn,
                    {
                        "position_id": a["lot"],
                        "sell_order_id": str(r["order_id"]),
                        "sell_client_id": a["external_identifier"],
                    },
                )
        else:
            live_db.save_order(
                conn, {"client_id": a["external_identifier"], "cancel_requested_at": now_iso()}
            )
        _journal(
            conn,
            day=day,
            arm=arm,
            symbol=a["symbol"],
            mode="order",
            reason=what,
            accepted=True,
            detail=detail,
        )
    return intents


def _cycle(conn, broker, config, rule, *, when, cache_path, account, armed, halted, dry, log) -> dict:
    """Splits, reconcile, the positions cross-check, then plan and apply. One pass."""
    live = live_gates.live_block(config)
    arm = str(live["arm"]).strip()
    day = when.astimezone(ET).date().isoformat()
    # Ledger only, so before the broker is read: lots restated for a split the human has declared.
    split = apply_splits(conn, config, when=when, arm=arm, log=log)
    rec = reconcile(conn, broker, config, when=when, account=account, log=log)
    summary: dict = {"reconcile": {k: v for k, v in rec.items() if v not in ([], 0)}}
    if split["applied"] or split["problems"]:
        summary["splits"] = {k: v for k, v in split.items() if v}
    if not rec["complete"]:
        return {**summary, "planned": False, "skipped": "reconcile_incomplete", "new_lots": rec["new_lots"]}
    summary["new_lots"], summary["closed_lots"] = rec["new_lots"], rec["closed_lots"]

    def note(mode: str, symbol: str, reason: str, detail=None) -> None:
        _journal(
            conn, day=day, arm=arm, symbol=symbol, mode=mode, reason=reason, accepted=False, detail=detail
        )

    for alert in rec["alerts"]:
        note("alert", str(alert.get("lot", "-")), alert["kind"], alert)
    if armed is None:  # reconcile only
        return {**summary, "planned": False}
    for lot in live_db.open_lots(conn, arm):
        if lot.get("exit_reason") == REVIEW_MARK:
            note("alert", lot["symbol"], f"{REVIEW_MARK} {lot['position_id']}")
    funds = [str(s).upper() for s in live.get("symbols") or []]
    check = check_positions(conn, broker, funds)
    block = dict(check["blocked"])
    for problem in split["problems"]:
        note("alert", "-", f"splits_invalid: {problem}")
    if split["problems"]:  # a split entry it cannot read may be a split it is not applying
        block.update({f: "splits_invalid" for f in funds})
    if not check["readable"]:
        note("alert", "-", "positions_unreadable: the broker's positions could not be read; no new buys")
    for fund, why in check["blocked"].items():
        if check["readable"]:
            note("alert", fund, why, check["funds"].get(fund))
    for fund, row in check["funds"].items():
        if row["status"] == "surplus" and fund not in check["blocked"]:
            _journal(conn, day=day, arm=arm, symbol=fund, mode="positions", accepted=True, detail=row,
                     reason=f"surplus: broker holds {row['broker']:g}, ledger {row['ledger']}; "
                            "the rest is not the ladder's and is left alone")  # fmt: skip
    summary["positions"] = {
        "readable": check["readable"],
        "blocked": check["blocked"],
        "funds": check["funds"],
    }
    inputs = plan_inputs(conn, config, rule, when=when, cache_path=cache_path)
    the_plan = orders.plan(live, rule, when=when, armed=armed, halted=halted, block_buys=block, **inputs)
    for alert in the_plan.alerts:
        note("alert", str(alert.get("symbol", "-")), f"{alert['kind']} {alert.get('lot', '')}".strip(), alert)
    for text in the_plan.buys_blocked:
        symbol, _, why = text.partition(": ")
        note("blocked", symbol if why else "-", why or text)
    results = orders.Executor(broker, dry_run=dry, log=log).apply(the_plan)
    intents = _record_results(conn, results, arm=arm, day=day, dry=dry, account=account)
    _write_checks(
        {
            "at": now_iso(),
            "date": day,
            "positions": summary["positions"],
            "splits": {
                "declared": [s.__dict__ for s in live_checks.parse_splits(live)[0]],
                "problems": split["problems"],
            },
            "intents": intents,
        }
    )
    summary.update(
        planned=True,
        plan=the_plan.as_dict(),
        placed=sum(1 for r in results if r["action"]["kind"] == "place" and r.get("ok")),
        cancelled=sum(1 for r in results if r["action"]["kind"] == "cancel" and r.get("ok")),
        failed=sum(1 for r in results if not r.get("ok")),
    )
    return summary


# --------------------------------------------------------------------------- the tick
def run_once(
    config: dict,
    *,
    broker=None,
    broker_factory: Callable[[dict], object] | None = None,
    conn=None,
    cache_path: str | None = None,
    when: datetime | None = None,
    designated: str | None = None,
    force: bool = False,
    log=_log,
) -> dict:
    """One live tick. `broker` (or `broker_factory`) is the injected seam: the adapter in production,
    a fake in tests. With neither, the adapter is built here, after the gates have passed."""
    when = (when or now_et()).astimezone(ET)
    today = when.date()
    day = today.isoformat()
    now_min = minute_of_day(when)
    live = live_gates.live_block(config)
    summary: dict = {"ok": True, "date": day, "order_path": True, "dry_run": dry_run(config)}
    if not force and not _cal.is_trading_day(today):
        return {**summary, "skipped": "not_a_trading_day"}

    # 0. the dead-man's switch, before any broker read
    armed_for = live_loop.arm_stamp_date()
    if armed_for is not None and armed_for != day:
        log(f"self-disarm: arm stamp is {armed_for!r}, today is {day}")
        live_loop.uninstall_task()
        armed_for = None

    # 1. the gates that block everything (the arm is read below)
    gates = live_loop.gates_now(config, designated=designated, when=when)
    blockers = [g for g in live_gates.unmet(gates) if g.blocks == "all" and g.name != "arm.record"]
    summary["armed_today"] = armed_for == day
    if blockers:
        log("tick: gates unmet, nothing read: " + "; ".join(f"{g.name}: {g.reason}" for g in blockers))
        return {**summary, "skipped": "gates_unmet", "unmet_gates": [g.name for g in blockers]}

    start_min = hhmm_to_min(live.get("session_start") or live_gates.DEFAULT_SESSION_START, 8 * 60 + 15)
    end_min = live_gates.disarm_minute(config)
    armed_day = armed_for == day
    disarm_tick = armed_day and now_min >= end_min
    in_window = armed_day and (start_min <= now_min < end_min or disarm_tick)
    if disarm_tick:
        # The cancel pass runs while the arm still counts: gates are read as of a minute before disarm_at.
        gate_when = when.replace(hour=(end_min - 1) // 60, minute=(end_min - 1) % 60, second=0, microsecond=0)
        gates = live_loop.gates_now(config, designated=designated, when=gate_when)
    else:
        gate_when = when
    armed_ok = live_gates.ready(gates) and in_window
    halted = not next(g.ok for g in gates if g.name == "halt.flag")
    dry = dry_run(config)

    if not looplock.acquire(once_lock_path(), 180, alive=looplock.pid_alive):
        return {**summary, "skipped": "another live tick is running"}
    try:
        if broker is None:
            broker = (broker_factory or BrokerAdapter)(config)
        if isinstance(broker, BrokerAdapter):
            broker.gate_when = gate_when
        own = conn is None
        conn = conn if conn is not None else live_db.connect()
        try:
            return _tick(
                config, broker, conn, summary,
                when=when, cache_path=cache_path or provider.stream_cache_path(config),
                designated=designated, armed=armed_ok, halted=halted, dry=dry,
                in_window=in_window, disarm_tick=disarm_tick, log=log,
            )  # fmt: skip
        finally:
            if own:
                conn.close()
    finally:
        looplock.release(once_lock_path())


def _tick(
    config,
    broker,
    conn,
    summary,
    *,
    when,
    cache_path,
    designated,
    armed,
    halted,
    dry,
    in_window,
    disarm_tick,
    log,
):
    live = live_gates.live_block(config)
    arm = str(live["arm"]).strip()
    day = when.astimezone(ET).date().isoformat()
    account = _redact.mask_account(designated if designated is not None else live_loop._designated())
    rule = orders.arm_rule(config)
    # 2. broker truth against ledger belief
    summary["orphans"] = sweep_orphans(conn, broker, log)
    if summary["orphans"] is None:
        live_db.record_iteration(
            conn, ran_at=time.time(), session_date=day, phase="sweep_failed", status="skipped"
        )
        return {**summary, "skipped": "orphan_sweep_unreadable"}
    if rule is None:
        return {**summary, "skipped": "no_arm_rule"}
    # 3-4. reconcile, then (inside the window, armed) plan and apply
    passes = []
    cycle = dict(config=config, rule=rule, when=when, cache_path=cache_path, account=account,
                 halted=halted, dry=dry, log=log)  # fmt: skip
    first = _cycle(conn, broker, armed=armed if in_window else None, **cycle)
    passes.append(first)
    tried_cancel = any(a["kind"] == "cancel" for a in (first.get("plan") or {}).get("actions", []))
    if disarm_tick and not first.get("skipped") and (tried_cancel or first.get("new_lots")):
        # A buy may have filled as it was cancelled: read again and rest its sell before disarming.
        passes.append(_cycle(conn, broker, armed=armed, **cycle))
    summary.update(passes[-1])
    summary["passes"] = len(passes)
    if disarm_tick:
        log(f"disarm: past disarm_at ({live.get('disarm_at')}); buys cancelled, disarming")
        live_loop.uninstall_task()
        summary["disarmed"] = True
    phase = "dry_run" if dry and in_window else "live" if in_window else "reconcile"
    live_db.record_iteration(
        conn,
        ran_at=time.time(),
        session_date=day,
        phase=phase,
        status="ok" if not summary.get("skipped") else "skipped",
        open_positions=len(live_db.open_lots(conn, arm)),
        actions_taken=int(summary.get("placed") or 0) + int(summary.get("cancelled") or 0),
        note=summary.get("skipped"),
    )
    return summary
