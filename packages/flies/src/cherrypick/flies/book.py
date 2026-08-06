"""One session's book: wires the pure engine to the paper database.

A book is one (trade_date, arm, symbol) triple. Each arm keeps its own book so the arms never share
positions, capital, or luck — the reason MEIC moved to per-(profile x symbol) portfolios was exactly
this, that a cumulative book lets one lucky structure paper over a strategy that doesn't work.
"""

from __future__ import annotations

from datetime import datetime

from cherrypick.flies import (
    clock,  # noqa: E402
    engine,  # noqa: E402
    fly,  # noqa: E402
)
from cherrypick.flies import db as dbmod  # noqa: E402


def book_id_for(trade_date: str, arm: str, symbol: str) -> str:
    return f"{trade_date}:{arm}:{symbol}"


def _now() -> str:
    """ET, with offset — see clock.py. Was machine-local until 2026-07-27, which put every stored
    entry_time two hours out from the entry_window recorded beside it."""
    return clock.now_iso()


def _minutes_since(started: str | None, ended: str) -> float | None:
    """Whole-minute gap between two ISO timestamps, or None if either is unparseable."""
    if not started:
        return None
    try:
        delta = datetime.fromisoformat(ended) - datetime.fromisoformat(started)
    except (ValueError, TypeError):
        return None
    return round(delta.total_seconds() / 60.0, 1)


def _record_best_debit(conn, position: dict, debit: float, when: str) -> None:
    """Keep the running minimum completing debit seen for an open spread.

    Recorded on every evaluation, including the ones that refuse. Afterwards this is what separates
    "the market never got there" from "we asked for a couple of cents too much" — a distinction that
    is invisible without it and that points at opposite remedies.
    """
    best = position.get("best_completing_debit")
    if best is not None and debit >= best:
        return
    position["best_completing_debit"] = debit
    dbmod.save_position(
        conn,
        {
            "position_id": position["position_id"],
            "best_completing_debit": round(debit, 4),
            "best_debit_at": when,
        },
    )


def regime_columns(prefix: str, snapshot: dict, params: dict, center: float | None = None) -> dict:
    """The regime columns for `prefix` ('entry' or 'completion') -- buckets AND the continuous
    measures behind them -- ready to fold straight into a `save_position` call. See
    `engine.classify_regime`; descriptive telemetry only, nothing here gates a decision.

    Public because `live_loop` writes these too (since 2026-08-01): keeping paper and live on one
    prefix convention is what lets `analytics.by_regime` read both ledgers with the same query.

    `center` (2026-08-04) is the position's centre, needed only by the centre-offset dimension --
    the one tag that is a property of our own choice rather than of the market alone. Optional so a
    caller with no centre in hand still gets the other four dimensions instead of an error; that
    path records the offset as 'unknown' rather than guessing one."""
    regime = engine.classify_regime(snapshot, params, center=center)
    return {f"{prefix}_{key}": value for key, value in regime.items()}


def _record_best_credit(conn, position: dict, credit: float, when: str) -> None:
    """Keep the running MAXIMUM completing credit seen for an open `debit_first` long vertical --
    the mirror of `_record_best_debit`'s running minimum. Recorded on every evaluation, including
    refusals, so a miss can be read afterwards as "the market never paid enough" vs "our buffer
    was too tight."""
    best = position.get("best_completing_credit")
    if best is not None and credit <= best:
        return
    position["best_completing_credit"] = credit
    dbmod.save_position(
        conn,
        {
            "position_id": position["position_id"],
            "best_completing_credit": round(credit, 4),
            "best_credit_at": when,
        },
    )


def _record_best_roll_debit(conn, position: dict, roll_debit: float, when: str) -> None:
    """Keep the running MINIMUM roll debit seen for an open bwb -- the same counterfactual role as
    `_record_best_debit`: afterwards, "the roll was never cheap enough" vs "our buffer was too
    tight" call for opposite remedies."""
    best = position.get("best_roll_debit")
    if best is not None and roll_debit >= best:
        return
    position["best_roll_debit"] = roll_debit
    dbmod.save_position(
        conn,
        {
            "position_id": position["position_id"],
            "best_roll_debit": round(roll_debit, 4),
            "best_roll_debit_at": when,
        },
    )


def _record_post_best_debit(conn, position: dict, debit: float, when: str) -> None:
    """Keep the running minimum completing debit seen AFTER a legged fly completed.

    `_record_best_debit` stops at the completion tick by construction — a completed position leaves
    the completion loop — so it can say "the market never offered it" for a miss but not "how much
    cheaper did the completing debit get after we took the first qualifying one". That second number
    is what a wait-for-better completion rule would be built from, and the stream cache keeps no
    quote history, so it is recorded here or lost. Telemetry only: nothing reads this on a decision
    path.
    """
    best = position.get("post_best_completing_debit")
    if best is not None and debit >= best:
        return
    position["post_best_completing_debit"] = debit
    dbmod.save_position(
        conn,
        {
            "position_id": position["position_id"],
            "post_best_completing_debit": round(debit, 4),
            "post_best_debit_at": when,
        },
    )


def _record_post_best_credit(conn, position: dict, credit: float, when: str) -> None:
    """Running MAXIMUM completing credit seen AFTER a debit_first fly completed — the mirror of
    `_record_post_best_debit`, and the direct measurement behind "we locked in the win; how much
    richer would waiting have been?". Telemetry only."""
    best = position.get("post_best_completing_credit")
    if best is not None and credit <= best:
        return
    position["post_best_completing_credit"] = credit
    dbmod.save_position(
        conn,
        {
            "position_id": position["position_id"],
            "post_best_completing_credit": round(credit, 4),
            "post_best_credit_at": when,
        },
    )


def _to_position(row: dict) -> dict:
    """Database row -> the plain dict the pure math in fly.py consumes."""
    return {
        "kind": row["kind"],
        "side": row["side"],
        "center": row["center"],
        "wing_width": row["wing_width"],
        "net": row["net"],
        "quantity": row["quantity"] or 1,
        "fees": row["fees"] or 0.0,
        "entry_mode": row["entry_mode"],
        "status": row["status"],
        "position_id": row["position_id"],
        # Carried because the session stats are recomputed from these dicts after settlement, and
        # pin rate is one of the three numbers the whole thesis turns on.
        "pinned": bool(row["pinned"]),
        # Carried so the running-minimum comparison and the latency clock survive a loop restart —
        # both are cumulative over a session, not per-iteration.
        "best_completing_debit": row["best_completing_debit"],
        "best_completing_credit": row["best_completing_credit"],
        "post_best_completing_debit": row["post_best_completing_debit"],
        "post_best_completing_credit": row["post_best_completing_credit"],
        "entry_time": row["entry_time"],
        # Carried so `max_positions_per_window` can count what this window has already spent. Without
        # it the cap would read every position as window-less and never bind.
        "entry_window": row["entry_window"],
        # bwb_roll: the wide wing's width, needed by every fly.py function that touches a bwb's
        # geometry. Kept after the roll too (wing_width alone can't tell a rolled bwb from a
        # legged fly in history/rewind without it).
        "far_width": row["far_width"],
        "best_roll_debit": row["best_roll_debit"],
    }


def process_snapshot(snapshot: dict, config: dict, conn, arm: str) -> dict:
    """Run one iteration of one arm against one snapshot. Returns a summary of what it did.

    Order matters: completions are evaluated BEFORE new entries. A credit spread that can be squared
    into a risk-free fly right now is worth more than a new credit spread, and evaluating entries
    first could consume the position slot that the completion needs.
    """
    params = engine.merged_params(config, arm)
    symbol = snapshot["symbol"]
    trade_date = snapshot["date"]
    book_id = book_id_for(trade_date, arm, symbol)
    actions: list[dict] = []
    now = _now()

    rows = dbmod.book_positions(conn, book_id)
    positions = [_to_position(r) for r in rows]

    def journal(mode, reason, *, accepted=False, center=None, position_id=None, detail=None):
        dbmod.record_decision(
            conn,
            trade_date=trade_date,
            arm=arm,
            symbol=symbol,
            mode=mode,
            reason=reason,
            accepted=accepted,
            center=center,
            position_id=position_id,
            detail=detail,
            when=now,
        )

    # What this arm WANTED this iteration, recorded before any gate can veto it. Written even when
    # nothing trades, because arm divergence is measured over intentions, not fills.
    wanted_center, wanted_reason = engine.select_center(snapshot, params)
    dbmod.record_iteration(
        conn,
        iteration_ts=now,
        trade_date=trade_date,
        symbol=symbol,
        arm=arm,
        center=wanted_center,
        center_reason=wanted_reason,
        underlying_price=snapshot.get("underlying_price"),
    )

    # --- 1. complete any open credit spread that has become cheap enough to square off, either by
    # buying the completing debit spread (kind -> fly) or, if this arm's completion_modes allows
    # it, by selling the OPPOSITE-type credit spread instead (kind -> iron_fly). When both are
    # possible on the same iteration, take whichever leaves the higher post-fee floor.
    #
    # The iron branch is RETIRED and unreachable in config (completion_modes is ["debit"]
    # everywhere; the `iron` arm is disabled) -- see docs/iron-completion.md. Put-call parity makes
    # the two completions the same trade, so both gates fire on the same tick and the completed
    # positions have the same net at every price; the iron just pays more in assignment fees. The
    # code stays because it is correct and tested, but note the floor tiebreak below is NOT valid
    # across kinds and must be fixed before anything re-enables this.
    for pos in [p for p in positions if p["kind"] == "short_vertical" and p["status"] == "open"]:
        debit_done, debit_reason, debit_plan = engine.evaluate_completion(snapshot, pos, params)
        if debit_plan is not None:
            _record_best_debit(conn, pos, debit_plan["debit"], now)

        iron_done = iron_plan = None
        if "iron" in params.get("completion_modes", ["debit"]):
            iron_done, _iron_reason, iron_plan = engine.evaluate_iron_completion(snapshot, pos, params)

        # NOT a valid comparison across kinds: each floor reserves its own kind's worst-case
        # assignment fee (fly 3 strikes, iron_fly 2) at its own worst-case settlement PRICE, so
        # iron's floor reads exactly $5.00 high at every spot. Retired-path only; see the note
        # above and docs/iron-completion.md before reviving.
        take_iron = iron_done and (not debit_done or iron_plan["floor"] > debit_plan["floor"])

        if not debit_done and not (iron_done and take_iron):
            journal(
                "completion",
                debit_reason,
                center=pos["center"],
                position_id=pos["position_id"],
                detail=None
                if debit_plan is None
                else f"debit {debit_plan['debit']:.2f} vs gate {debit_plan['gate_debit']:.2f}",
            )
            if iron_plan is not None and not iron_done:
                journal(
                    "iron_completion",
                    _iron_reason,
                    center=pos["center"],
                    position_id=pos["position_id"],
                    detail=f"iron credit {iron_plan['credit']:.2f} vs gate {iron_plan['gate_credit']:.2f}",
                )
            actions.append(
                {"action": "completion_skipped", "position_id": pos["position_id"], "reason": debit_reason}
            )
            continue

        if take_iron:
            plan = iron_plan
            pos["kind"] = "iron_fly"
            pos["net"] = plan["net"]
            pos["fees"] = pos["fees"] + plan["completion_fee"]
            latency = _minutes_since(pos.get("entry_time"), now)
            dbmod.save_position(
                conn,
                {
                    "position_id": pos["position_id"],
                    "kind": "iron_fly",
                    "net": plan["net"],
                    "credit": plan["credit"],
                    "completion_mode": "iron",
                    "fees": pos["fees"],
                    "floor_dollars": plan["floor"],
                    "risk_free": int(fly.is_risk_free(pos)),
                    "completed_at": now,
                    "completion_latency_min": latency,
                    "spot_at_completion": snapshot.get("underlying_price"),
                    **regime_columns("completion", snapshot, params, center=pos.get("center")),
                },
            )
            journal(
                "iron_completion",
                "completed",
                accepted=True,
                center=pos["center"],
                position_id=pos["position_id"],
                detail=f"iron credit {plan['credit']:.2f}, floor ${plan['floor']:.2f} after fees",
            )
            actions.append(
                {
                    "action": "iron_completed",
                    "position_id": pos["position_id"],
                    "credit": plan["credit"],
                    "net": plan["net"],
                    "floor": plan["floor"],
                    "latency_min": latency,
                }
            )
            continue

        plan = debit_plan
        pos["kind"] = "fly"
        pos["net"] = plan["net"]
        pos["fees"] = pos["fees"] + plan["completion_fee"]
        latency = _minutes_since(pos.get("entry_time"), now)
        dbmod.save_position(
            conn,
            {
                "position_id": pos["position_id"],
                "kind": "fly",
                "net": plan["net"],
                "debit": plan["debit"],
                "completion_mode": "debit",
                "fees": pos["fees"],
                "floor_dollars": plan["floor"],
                "risk_free": int(fly.is_risk_free(pos)),
                "completed_at": now,
                "completion_latency_min": latency,
                "spot_at_completion": snapshot.get("underlying_price"),
                **regime_columns("completion", snapshot, params, center=pos.get("center")),
            },
        )
        journal(
            "completion",
            "completed",
            accepted=True,
            center=pos["center"],
            position_id=pos["position_id"],
            detail=f"debit {plan['debit']:.2f}, floor ${plan['floor']:.2f} after fees",
        )
        actions.append(
            {
                "action": "completed",
                "position_id": pos["position_id"],
                "debit": plan["debit"],
                "net": plan["net"],
                "floor": plan["floor"],
                "latency_min": latency,
            }
        )

    # --- 1b. complete any open `debit_first` long vertical whose completing sale has richened
    # enough to beat the debit already paid -- the same idea as step 1, direction reversed.
    for pos in [p for p in positions if p["kind"] == "long_vertical" and p["status"] == "open"]:
        done, reason, plan = engine.evaluate_debit_completion(snapshot, pos, params)
        if plan is not None:
            _record_best_credit(conn, pos, plan["credit"], now)
        if not done:
            journal(
                "debit_completion",
                reason,
                center=pos["center"],
                position_id=pos["position_id"],
                detail=None
                if plan is None
                else f"credit {plan['credit']:.2f} vs gate {plan['gate_credit']:.2f}",
            )
            actions.append(
                {"action": "debit_completion_skipped", "position_id": pos["position_id"], "reason": reason}
            )
            continue
        pos["kind"] = "fly"
        pos["net"] = plan["net"]
        pos["fees"] = pos["fees"] + plan["completion_fee"]
        latency = _minutes_since(pos.get("entry_time"), now)
        dbmod.save_position(
            conn,
            {
                "position_id": pos["position_id"],
                "kind": "fly",
                "net": plan["net"],
                "credit": plan["credit"],
                "fees": pos["fees"],
                "floor_dollars": plan["floor"],
                "risk_free": int(fly.is_risk_free(pos)),
                "completed_at": now,
                "completion_latency_min": latency,
                "spot_at_completion": snapshot.get("underlying_price"),
                **regime_columns("completion", snapshot, params, center=pos.get("center")),
            },
        )
        journal(
            "debit_completion",
            "completed",
            accepted=True,
            center=pos["center"],
            position_id=pos["position_id"],
            detail=f"credit {plan['credit']:.2f}, floor ${plan['floor']:.2f} after fees",
        )
        actions.append(
            {
                "action": "debit_completed",
                "position_id": pos["position_id"],
                "credit": plan["credit"],
                "net": plan["net"],
                "floor": plan["floor"],
                "latency_min": latency,
            }
        )

    # --- 1c. roll any open bwb whose roll (buy near wing, sell held far wing) has cheapened
    # enough to beat the credit already collected, converting it into a symmetric fly.
    for pos in [p for p in positions if p["kind"] == "bwb" and p["status"] == "open"]:
        done, reason, plan = engine.evaluate_roll(snapshot, pos, params)
        if plan is not None:
            _record_best_roll_debit(conn, pos, plan["roll_debit"], now)
        if not done:
            journal(
                "roll",
                reason,
                center=pos["center"],
                position_id=pos["position_id"],
                detail=None
                if plan is None
                else f"roll debit {plan['roll_debit']:.2f} vs gate {plan['gate_debit']:.2f}",
            )
            actions.append({"action": "roll_skipped", "position_id": pos["position_id"], "reason": reason})
            continue
        pos["kind"] = "fly"
        pos["net"] = plan["net"]
        pos["fees"] = pos["fees"] + plan["roll_fee"]
        latency = _minutes_since(pos.get("entry_time"), now)
        dbmod.save_position(
            conn,
            {
                "position_id": pos["position_id"],
                "kind": "fly",
                "net": plan["net"],
                "roll_debit": plan["roll_debit"],
                "fees": pos["fees"],
                "floor_dollars": plan["floor"],
                "risk_free": int(fly.is_risk_free(pos)),
                # One "finished structure" column for every reader -- for a bwb, completed_at and
                # rolled_at are the SAME moment (the roll IS the completion), unlike a fly's
                # completed_at which is set once at a genuinely separate step.
                "completed_at": now,
                "rolled_at": now,
                "completion_latency_min": latency,
                "roll_latency_min": latency,
                "spot_at_completion": snapshot.get("underlying_price"),
                "spot_at_roll": snapshot.get("underlying_price"),
                **regime_columns("completion", snapshot, params, center=pos.get("center")),
            },
        )
        journal(
            "roll",
            "rolled",
            accepted=True,
            center=pos["center"],
            position_id=pos["position_id"],
            detail=f"rolled far wing in for {plan['roll_debit']:.2f} debit, floor ${plan['floor']:.2f} after fees",
        )
        actions.append(
            {
                "action": "rolled",
                "position_id": pos["position_id"],
                "roll_debit": plan["roll_debit"],
                "net": plan["net"],
                "floor": plan["floor"],
                "latency_min": latency,
            }
        )

    # --- 1d. post-completion counterfactual telemetry: for every completed (not yet settled) fly,
    # keep pricing the spread that completed it. The best-price trackers in steps 1/1b stop at the
    # completion tick by construction, so without this the module can never say how much richer the
    # completing price became after the first qualifying tick was taken — the one number a
    # wait-for-better completion rule needs, and one the stream cache (latest-value-only) cannot
    # reconstruct offline. Records, never gates: rule 5 is untouched, and positions completed
    # earlier THIS tick get their completion-tick price as the baseline. Skips iron/bwb completions
    # (different geometry) and skips silently on a missing leg quote, like the trackers it extends.
    slip = params.get("slippage_frac", fly.DEFAULT_SLIPPAGE_FRAC)
    for pos in [p for p in positions if p["kind"] == "fly" and p["status"] == "open"]:
        side, center, width = pos["side"], pos["center"], pos["wing_width"]
        if pos["entry_mode"] == "legged":
            long_strike = center + width if side == fly.PUT else center - width
            far_q, center_q = engine.quote(snapshot, side, long_strike), engine.quote(snapshot, side, center)
            if far_q is not None and center_q is not None:
                _record_post_best_debit(conn, pos, fly.vertical_debit(far_q, center_q, slip), now)
        elif pos["entry_mode"] == "debit_first":
            wing_strike = center - width if side == fly.PUT else center + width
            center_q, wing_q = engine.quote(snapshot, side, center), engine.quote(snapshot, side, wing_strike)
            if center_q is not None and wing_q is not None:
                _record_post_best_credit(conn, pos, fly.vertical_credit(center_q, wing_q, slip), now)

    open_positions = [p for p in positions if p["status"] == "open"]

    # --- 2. legged entry: sell a new credit spread
    if "legged" in params.get("entry_modes", ["legged"]):
        enter, reason, plan = engine.evaluate_credit_spread_entry(snapshot, params, open_positions)
        if enter:
            position_id = f"FLY-{arm}-{symbol}-{clock.now_et().strftime('%Y%m%d%H%M%S%f')}"
            pos = {
                "kind": "short_vertical",
                "side": plan["side"],
                "center": plan["center"],
                "wing_width": plan["wing_width"],
                "net": plan["credit"],
                "quantity": plan["quantity"],
                "fees": plan["open_fee"],
                "entry_mode": "legged",
                "status": "open",
                "position_id": position_id,
                # Same reason as in `_to_position`: this dict is appended to the live list the entry
                # gates read, so it has to carry the window or the per-window cap misses it until the
                # next iteration re-reads from the DB.
                "entry_window": plan["entry_window"],
            }
            positions.append(pos)
            open_positions.append(pos)
            dbmod.save_position(
                conn,
                {
                    "position_id": position_id,
                    "book_id": book_id,
                    "trade_date": trade_date,
                    "arm": arm,
                    "entry_mode": "legged",
                    "symbol": symbol,
                    "kind": "short_vertical",
                    "side": plan["side"],
                    "center": plan["center"],
                    "wing_width": plan["wing_width"],
                    "quantity": plan["quantity"],
                    "net": plan["credit"],
                    "credit": plan["credit"],
                    "fees": plan["open_fee"],
                    "entry_time": now,
                    "entry_window": plan["entry_window"],
                    "center_reason": plan["center_reason"],
                    "completing_direction": plan["completing_direction"],
                    "underlying_at_entry": snapshot.get("underlying_price"),
                    **regime_columns("entry", snapshot, params, center=plan["center"]),
                    # Full defined risk (-W) net of trading fees AND the worst-case exercise-
                    # assignment fee (both legs ITM) -- the uncompleted branch's honest worst case,
                    # not left blank until (if ever) it completes into a fly.
                    "floor_dollars": fly.position_floor(pos),
                    "risk_free": 0,
                    "status": "open",
                },
            )
            journal(
                "legged",
                "entered",
                accepted=True,
                center=plan["center"],
                position_id=position_id,
                detail=f"{plan['side']} spread for {plan['credit']:.2f} credit, needs spot "
                f"{plan['completing_direction']} to complete",
            )
            actions.append(
                {
                    "action": "credit_spread_opened",
                    "position_id": position_id,
                    "side": plan["side"],
                    "center": plan["center"],
                    "credit": plan["credit"],
                }
            )
        else:
            journal("legged", reason, center=wanted_center)
            actions.append({"action": "entry_skipped", "mode": "legged", "reason": reason})

    # --- 2.5. debit-first entry: buy a debit vertical, complete later by SELLING a credit spread
    if "debit_first" in params.get("entry_modes", []):
        enter, reason, plan = engine.evaluate_debit_vertical_entry(snapshot, params, open_positions)
        if enter:
            position_id = f"FLY-{arm}-{symbol}-{clock.now_et().strftime('%Y%m%d%H%M%S%f')}-D"
            pos = {
                "kind": "long_vertical",
                "side": plan["side"],
                "center": plan["center"],
                "wing_width": plan["wing_width"],
                "net": -plan["debit"],
                "quantity": plan["quantity"],
                "fees": plan["open_fee"],
                "entry_mode": "debit_first",
                "status": "open",
                "position_id": position_id,
                "entry_window": plan["entry_window"],
            }
            positions.append(pos)
            open_positions.append(pos)
            dbmod.save_position(
                conn,
                {
                    "position_id": position_id,
                    "book_id": book_id,
                    "trade_date": trade_date,
                    "arm": arm,
                    "entry_mode": "debit_first",
                    "symbol": symbol,
                    "kind": "long_vertical",
                    "side": plan["side"],
                    "center": plan["center"],
                    "wing_width": plan["wing_width"],
                    "quantity": plan["quantity"],
                    "net": -plan["debit"],
                    "debit": plan["debit"],
                    "fees": plan["open_fee"],
                    "entry_time": now,
                    "entry_window": plan["entry_window"],
                    "center_reason": plan["center_reason"],
                    "completing_direction": plan["completing_direction"],
                    "underlying_at_entry": snapshot.get("underlying_price"),
                    **regime_columns("entry", snapshot, params, center=plan["center"]),
                    # Bounded at 0, never a -W tail (a long vertical can't lose more than its
                    # debit) -- but negative, since the debit paid is a real cost with no credit
                    # collected yet. See fly.position_floor's long_vertical branch for the
                    # assignment-fee reserve this also carries.
                    "floor_dollars": fly.position_floor(pos),
                    "risk_free": 0,
                    "status": "open",
                },
            )
            journal(
                "debit_first",
                "entered",
                accepted=True,
                center=plan["center"],
                position_id=position_id,
                detail=f"{plan['side']} debit spread for {plan['debit']:.2f}, needs spot "
                f"{plan['completing_direction']} to complete",
            )
            actions.append(
                {
                    "action": "debit_vertical_opened",
                    "position_id": position_id,
                    "side": plan["side"],
                    "center": plan["center"],
                    "debit": plan["debit"],
                }
            )
        else:
            journal("debit_first", reason, center=wanted_center)
            actions.append({"action": "entry_skipped", "mode": "debit_first", "reason": reason})

    # --- 2.75. bwb_roll entry: buy a broken-wing butterfly whole for a net credit
    if "bwb_roll" in params.get("entry_modes", []):
        enter, reason, plan = engine.evaluate_bwb_entry(snapshot, params, open_positions)
        if enter:
            position_id = f"FLY-{arm}-{symbol}-{clock.now_et().strftime('%Y%m%d%H%M%S%f')}-B"
            pos = {
                "kind": "bwb",
                "side": plan["side"],
                "center": plan["center"],
                "wing_width": plan["wing_width"],
                "far_width": plan["far_width"],
                "net": plan["credit"],
                "quantity": plan["quantity"],
                "fees": plan["open_fee"],
                "entry_mode": "bwb_roll",
                "status": "open",
                "position_id": position_id,
                "entry_window": plan["entry_window"],
            }
            positions.append(pos)
            open_positions.append(pos)
            dbmod.save_position(
                conn,
                {
                    "position_id": position_id,
                    "book_id": book_id,
                    "trade_date": trade_date,
                    "arm": arm,
                    "entry_mode": "bwb_roll",
                    "symbol": symbol,
                    "kind": "bwb",
                    "side": plan["side"],
                    "center": plan["center"],
                    "wing_width": plan["wing_width"],
                    "far_width": plan["far_width"],
                    "quantity": plan["quantity"],
                    "net": plan["credit"],
                    "credit": plan["credit"],
                    "fees": plan["open_fee"],
                    "entry_time": now,
                    "entry_window": plan["entry_window"],
                    "center_reason": plan["center_reason"],
                    "underlying_at_entry": snapshot.get("underlying_price"),
                    **regime_columns("entry", snapshot, params, center=plan["center"]),
                    # The real, negative-capable tail -- (wing_width - far_width) -- net of fees
                    # and the full 4-contract assignment-fee reserve. Never reported as a fly's
                    # floor; see fly.position_floor's bwb branch.
                    "floor_dollars": fly.position_floor(pos),
                    "risk_free": int(fly.is_risk_free(pos)),
                    "status": "open",
                },
            )
            journal(
                "bwb_roll",
                "entered",
                accepted=True,
                center=plan["center"],
                position_id=position_id,
                detail=f"{plan['side']} BWB (wing {plan['wing_width']}, far {plan['far_width']}) "
                f"for {plan['credit']:.2f} credit",
            )
            actions.append(
                {
                    "action": "bwb_opened",
                    "position_id": position_id,
                    "side": plan["side"],
                    "center": plan["center"],
                    "credit": plan["credit"],
                }
            )
        else:
            journal("bwb_roll", reason, center=wanted_center)
            actions.append({"action": "entry_skipped", "mode": "bwb_roll", "reason": reason})

    # --- 3. outright entry: buy a cheap fly, funded only by premium already taken in
    if "outright" in params.get("entry_modes", []):
        cash = fly.book_cash(positions)
        # Whether an OPEN credit spread's premium counts as funding is a real choice, not a detail.
        # The reference book did fund flies from a still-open iron condor, so this defaults on to stay
        # faithful to it — but that premium is not yet earned, which is precisely why the book-level
        # floor below reports `unbounded_below` instead of claiming the book is risk-free.
        realized = (
            cash["net_cash"]
            if params.get("fund_from_open_credit", True)
            else max(sum(fly.position_pnl(p, p["center"]) for p in positions if p["status"] != "open"), 0.0)
        )
        enter, reason, plan = engine.evaluate_outright_entry(snapshot, params, open_positions, realized)
        if enter:
            position_id = f"FLY-{arm}-{symbol}-{clock.now_et().strftime('%Y%m%d%H%M%S%f')}-O"
            pos = {
                "kind": "fly",
                "side": plan["side"],
                "center": plan["center"],
                "wing_width": plan["wing_width"],
                "net": -plan["debit"],
                "quantity": plan["quantity"],
                "fees": plan["open_fee"],
                "entry_mode": "outright",
                "status": "open",
                "position_id": position_id,
                "entry_window": plan["entry_window"],
            }
            positions.append(pos)
            dbmod.save_position(
                conn,
                {
                    "position_id": position_id,
                    "book_id": book_id,
                    "trade_date": trade_date,
                    "arm": arm,
                    "entry_mode": "outright",
                    "symbol": symbol,
                    "kind": "fly",
                    "side": plan["side"],
                    "center": plan["center"],
                    "wing_width": plan["wing_width"],
                    "quantity": plan["quantity"],
                    "net": -plan["debit"],
                    "debit": plan["debit"],
                    "fees": plan["open_fee"],
                    "entry_time": now,
                    "entry_window": plan["entry_window"],
                    "center_reason": plan["center_reason"],
                    "underlying_at_entry": snapshot.get("underlying_price"),
                    **regime_columns("entry", snapshot, params, center=plan["center"]),
                    "floor_dollars": fly.position_floor(pos),
                    "risk_free": int(fly.is_risk_free(pos)),
                    "status": "open",
                },
            )
            journal(
                "outright",
                "entered",
                accepted=True,
                center=plan["center"],
                position_id=position_id,
                detail=f"fly bought for {plan['debit']:.2f} debit, funded from ${realized:.2f}",
            )
            actions.append(
                {
                    "action": "fly_bought",
                    "position_id": position_id,
                    "center": plan["center"],
                    "debit": plan["debit"],
                }
            )
        else:
            journal("outright", reason, center=wanted_center)
            actions.append({"action": "entry_skipped", "mode": "outright", "reason": reason})

    summary = _save_book(conn, book_id, trade_date, arm, symbol, positions, params)
    return {"book_id": book_id, "actions": actions, **summary}


def _save_book(conn, book_id, trade_date, arm, symbol, positions, params, settlement_price=None) -> dict:
    cash = fly.book_cash(positions)
    floor = fly.book_floor(positions, step=params.get("book_scan_step", 1.0))
    stats = engine.session_stats(positions)
    band = floor["band"] or (None, None)
    row = {
        "book_id": book_id,
        "trade_date": trade_date,
        "arm": arm,
        "symbol": symbol,
        **cash,
        "worst": floor["worst"],
        "worst_at": floor["worst_at"],
        "floor_holds": int(floor["floor_holds"]),
        "band_low": band[0],
        "band_high": band[1],
        "unbounded_below": int(floor["unbounded_below"]),
        "completion_rate": stats["completion_rate"],
        "risk_free_rate": stats["risk_free_rate"],
        "pin_rate": stats["pin_rate"],
        "status": "settled" if settlement_price is not None else "open",
    }
    if settlement_price is not None:
        row["settlement_price"] = settlement_price
        row["pnl"] = round(fly.book_pnl(positions, settlement_price), 2)
    dbmod.save_book(conn, row)
    return {"cash": cash, "floor": floor, "stats": stats}


def settle_book(conn, trade_date: str, arm: str, symbol: str, settlement_price: float, config: dict) -> dict:
    """Cash-settle every open position in a book at the settlement print and close the book out."""
    params = engine.merged_params(config, arm)
    book_id = book_id_for(trade_date, arm, symbol)
    rows = dbmod.book_positions(conn, book_id)
    positions = [_to_position(r) for r in rows]

    settled = engine.settle([p for p in positions if p["status"] == "open"], settlement_price)
    for p in settled:
        gross = (p["net"] + p["expiry_payoff"]) * fly.CONTRACT_MULTIPLIER * p["quantity"]
        dbmod.save_position(
            conn,
            {
                "position_id": p["position_id"],
                "settlement_price": settlement_price,
                "expiry_payoff": p["expiry_payoff"],
                "gross_pnl": round(gross, 2),
                "fees": p["fees"],
                "pnl": p["pnl"],
                "pinned": int(p["pinned"]),
                "status": "settled",
                "exit_time": _now(),
            },
        )

    final = [_to_position(r) for r in dbmod.book_positions(conn, book_id)]
    for p in final:
        p["status"] = "settled"
    summary = _save_book(conn, book_id, trade_date, arm, symbol, final, params, settlement_price)
    return {
        "book_id": book_id,
        "settled": len(settled),
        "pnl": round(fly.book_pnl(final, settlement_price), 2),
        "itm_legs": sum(p.get("itm_legs", 0) for p in settled),
        "assignment_fees": round(sum(p.get("assignment_fee", 0.0) for p in settled), 2),
        **summary,
    }
