"""The ledger WRITES calendars, pmcc and curve share: traded closes, share disposal, finalization.

The three modules were built from one design and their `book.py` files carried five functions that
were byte-identical once the table prefix (`dc_` / `pmcc_` / `curve_`) and the docstrings were
normalised out: `close_open_legs`, `dispose_assignment`, `finalize_if_done`,
`_accumulate_exit_costs` and `_position_quantity`. Their pure cost and P&L rules had already moved
to `cherrypick.core.fees` and `cherrypick.core.settlement`; this is the write path over them, so a
fourth copy cannot price a close, charge a disposal or arm a result differently from the other
three. Proven before the modules switched by a differential harness that drove the same sequences
through the old copies and this, on identical ledgers, and compared every row of every table.

`SpreadBook` wraps a module's `LedgerStore`, which is everything the functions need: the prefix
for the two direct queries, and the store's `save_*`/reader methods for the rest. Each module keeps
its old names as aliases of a bound instance, so no caller changes.

**What stays in the modules, on purpose:** entry (every module's position row is its own shape) and
`settle_expiring_legs`, which genuinely differs — curve is always physical and prices a call-only
intrinsic, pmcc labels a worthless leg `expired` where calendars says `cash_settled`, and the three
pass different finalize reasons. bwb's book is a four-leg, two-sell structure with an add-on and
keeps its own writer.

The conventions every row written here obeys (the ledger readers depend on them):

- `gross_pnl` is mid-priced and cost-free: the sum of per-leg P&L x100 x qty, plus any delivered
  shares' realized move (already in dollars, so added after the per-share legs are scaled).
- `fees` is the TOTAL modeled cost, so net is always `gross_pnl - fees`, one subtraction.
- `settlement_fees` is the part of `fees` that is settlement or assignment -- recorded beside the
  total, never an extra cost.
"""

from __future__ import annotations

from cherrypick.core import fees as _fees
from cherrypick.core import settlement as _settlement
from cherrypick.core.clock import now_iso
from cherrypick.core.ledgerstore import LedgerStore

__all__ = ["SpreadBook", "exit_spread_blocks"]


def exit_spread_blocks(mark_snapshot: dict, params: dict) -> bool:
    """Whether any leg is too wide to act on -- wide in PERCENT and in MONEY, both, per leg.

    A percentage alone is the wrong instrument for a cheap leg, and on the way OUT that is the
    common case: a short that has done its job quotes 0.00/0.01, a one-cent buyback and, as a ratio,
    exactly a 200% spread. A percentage-only gate refused calendars' scheduled Friday exit on every
    tick of its window (2026-08-28), and earnings measured 32 profit-target exits refused the same
    way before its own fix. On exit there is no premium being protected -- every leg is being closed
    -- so the absolute floor applies to all of them (curve's ENTRY gate keeps a short-leg exception;
    that belongs to entry only).

    An older snapshot with no per-leg detail falls back to the percentage test alone, so a stored
    mark cannot silently widen what this admits.
    """
    max_pct = params.get("max_leg_spread_pct", 0.25)
    legs = mark_snapshot.get("leg_spreads")
    if not legs:
        widest = mark_snapshot.get("max_spread_pct")
        return widest is not None and widest > max_pct
    max_abs = params.get("max_leg_spread_abs", 0.05)
    return any(leg["pct"] > max_pct and leg["abs"] > max_abs for leg in legs)


class SpreadBook:
    """The shared close / dispose / finalize writes over one module's prefixed ledger."""

    def __init__(self, store: LedgerStore):
        self.store = store
        self._positions = store.table("positions")
        self._legs = store.table("legs")

    def position_quantity(self, conn, pid: str) -> int:
        row = conn.execute(f"SELECT quantity FROM {self._positions} WHERE position_id = ?", (pid,)).fetchone()
        return int((row["quantity"] if row else 1) or 1)

    def accumulate_exit_costs(
        self, conn, pid: str, *, fee: float, slippage: float, settlement: bool = False
    ) -> None:
        """Add an exit's costs to the position's running totals. A settlement or assignment charge
        (`settlement=True`) is also added to `settlement_fees`, the part of `fees` it is -- recorded
        beside the total, never an extra cost."""
        row = conn.execute(
            f"SELECT exit_cost, exit_slippage, fees, settlement_fees FROM {self._positions} "
            "WHERE position_id = ?",
            (pid,),
        ).fetchone()
        update = {
            "position_id": pid,
            "exit_cost": round((row["exit_cost"] or 0.0) + fee, 2),
            "exit_slippage": round((row["exit_slippage"] or 0.0) + slippage, 2),
            "fees": round((row["fees"] or 0.0) + fee + slippage, 2),
        }
        if settlement:
            update["settlement_fees"] = round((row["settlement_fees"] or 0.0) + fee, 2)
        self.store.save_position(conn, update)

    def finalize_if_done(self, conn, pid: str, *, reason: str, session_date: str) -> bool:
        """Once nothing is open, the position closes: gross P&L from the recorded per-leg closes plus
        any delivered shares' realized move, the exit reason from whichever path finished it (a
        reason already on the row wins). `closed_session` is what the ledger readers report as the
        session -- the day the LAST leg or share position closed, the day the result became a fact.

        An undisposed share position holds the close open exactly as an open option leg does:
        closing while shares are outstanding would arm a result the account has not yet realized,
        and on a physically-settled underlying that gap can span a weekend. An unpriced leg close
        holds it too -- never finalize on a guess."""
        store = self.store
        legs = store.legs_for(conn, pid)
        if any(leg["status"] == "open" for leg in legs):
            return False
        if store.open_assignment_count(conn, pid):
            return False
        position = conn.execute(f"SELECT * FROM {self._positions} WHERE position_id = ?", (pid,)).fetchone()
        if position is None or position["status"] == "closed":
            return False
        quantity = int(position["quantity"] or 1)
        per_share = 0.0
        exit_value = 0.0
        for leg in legs:
            pnl = _settlement.leg_pnl(leg)
            if pnl is None:
                return False
            per_share += pnl
            exit_value += leg["close_value"] * (1 if leg["action"] == "Buy to Open" else -1)
        shares_pnl = sum(a["share_pnl"] or 0.0 for a in store.assignments_for(conn, pid))
        store.save_position(
            conn,
            {
                "position_id": pid,
                "status": "closed",
                "exit_reason": position["exit_reason"] or reason,
                "closed_at": now_iso(),
                "closed_session": session_date,
                "exit_value": round(exit_value, 4),
                "gross_pnl": round(per_share * 100 * quantity + shares_pnl, 2),
            },
        )
        return True

    def close_open_legs(
        self, conn, position: dict, mark_snapshot: dict, config: dict, *, reason: str, session_date: str
    ) -> dict:
        """Close every still-open leg of one position at the mark's mids -- the traded close, one
        arithmetic path whichever exit asked for it, so no two exits can price differently.

        The caller has already run the execution gate; this refuses only on an unpriceable leg,
        which should not happen after a gated `ok` snapshot but is checked anyway -- closing one
        leg of a pair on a guess is worse than holding both a tick longer. The refusal writes
        nothing."""
        legs = self.store.open_legs_for(conn, position["position_id"])
        quotes = mark_snapshot.get("quotes") or {}
        for leg in legs:
            if quotes.get(leg["streamer_symbol"]) is None:
                return {"ok": False, "reason": "missing_leg_quotes", "position_id": position["position_id"]}

        now = now_iso()
        quantity = int(position.get("quantity") or 1)
        leg_quotes = []
        sell_legs = 0
        for leg in legs:
            quote = quotes[leg["streamer_symbol"]]
            leg_quotes.append({"bid": quote["bid"], "ask": quote["ask"]})
            # Closing inverts the opening action: an opened-long leg is SOLD to close.
            if leg["action"] == "Buy to Open":
                sell_legs += 1
            self.store.save_leg(
                conn,
                {
                    "position_id": leg["position_id"],
                    "leg_role": leg["leg_role"],
                    "status": "closed",
                    "close_kind": "traded",
                    "closed_at": now,
                    "close_bid": quote["bid"],
                    "close_ask": quote["ask"],
                    "close_value": quote["mid"],
                },
            )
        cost = _fees.spread_close_cost(position["symbol"], leg_quotes, quantity, config, sell_legs=sell_legs)
        self.accumulate_exit_costs(conn, position["position_id"], fee=cost["fee"], slippage=cost["slippage"])
        self.finalize_if_done(conn, position["position_id"], reason=reason, session_date=session_date)
        return {"ok": True, "position_id": position["position_id"], "legs_closed": len(legs), "cost": cost}

    def dispose_assignment(self, conn, assignment: dict, price: float, *, session_date: str) -> dict:
        """Close one delivered share position at `price` and finalize its position if that was the
        last thing outstanding. The fee lands here rather than at settlement because only now is
        the disposal price known, and the equity pass-throughs are computed on it."""
        pnl = _settlement.share_pnl(assignment["direction"], assignment["shares"], assignment["basis"], price)
        fee = _fees.assignment_fee(assignment, price)
        self.store.save_assignment(
            conn,
            {
                "position_id": assignment["position_id"],
                "leg_role": assignment["leg_role"],
                "status": "disposed",
                "disposed_session": session_date,
                "disposed_at": now_iso(),
                "disposal_price": round(float(price), 4),
                "share_pnl": pnl,
                "fees": fee,
            },
        )
        self.accumulate_exit_costs(conn, assignment["position_id"], fee=fee, slippage=0.0, settlement=True)
        self.finalize_if_done(
            conn, assignment["position_id"], reason="shares_disposed", session_date=session_date
        )
        return {"position_id": assignment["position_id"], "share_pnl": pnl, "fee": fee, "price": price}
