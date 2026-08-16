"""Read-side exit-policy derivation — the experiment the module exists to run.

One entry stream, two real books, and a recorded per-tick mark path make every candidate exit rule
answerable after the fact WITHOUT running it as its own book: `derive` replays a policy tick by
tick over the path book's recorded marks (an exact replay at the recorded prices, not MEIC's
max-cost proxy), prices the exit it would have taken at that tick's own bid/ask through the same
cost stack the live books use, and reports the week's net. Pairing is exact by construction —
every book shares the same entry fills — so a policy table is a like-for-like comparison, not an
estimate.

Two honesty rails, both load-bearing:

- **A hole in the path is `derivable: False`, never zero.** A week whose marks cannot answer a
  policy is excluded and counted as excluded — silently pricing a missing tick would let a feed
  outage flatter whichever policy it happened to favor.
- **The derivation is validated against reality every time it runs.** `validate_against_control`
  re-derives the `control` policy from the control book's OWN marks and compares it to that book's
  real recorded net (they should agree to the cent — same ticks, same mids, same cost model), and
  the `expiry-longs-mon` policy against the path book's real net. A derivation that cannot
  reproduce the books it is derived beside has no business ranking the policies between them.

Granularity caveat, stated rather than hidden: a trigger is evaluated at the recorded tick cadence,
so a threshold crossed and re-crossed between ticks is invisible — the derived exit is the first
RECORDED tick where the trigger held. A cadence change is a journaled measurement break and
derivations are never pooled across one.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from cherrypick.core import calendar as _cal

from cherrypick.calendars import clock, db, engine

# The grid. Whole-structure policies read the COMBINED double calendar (both sides); `touch` is the
# one per-side rule. Every non-expiry policy that never triggers falls through to the control
# terminal (close everything in the Friday exit window); the expiry policies instead let the shorts
# cash-settle and differ only in when the longs go.
POLICIES = {
    "control": {"kind": "time", "when": "fri_close"},
    "pt-10": {"kind": "pt", "pct": 0.10},
    "pt-20": {"kind": "pt", "pct": 0.20},
    "pt-30": {"kind": "pt", "pct": 0.30},
    "sl-25": {"kind": "sl", "pct": 0.25},
    "sl-50": {"kind": "sl", "pct": 0.50},
    "sl-100": {"kind": "sl", "pct": 1.00},
    "touch-close-side": {"kind": "touch"},
    "time-thu-close": {"kind": "time", "when": "thu_close"},
    "time-fri-noon": {"kind": "time", "when": "fri_noon"},
    "expiry-longs-fri": {"kind": "expiry", "longs": "fri_close"},
    "expiry-longs-mon": {"kind": "expiry", "longs": "mon_open"},
}

_ROLES = ("front_put", "back_put", "front_call", "back_call")


# --------------------------------------------------------------------------- path assembly
def week_data(conn, week_of: str, book: str) -> dict | None:
    """One week's positions, legs, and merged tick path for `book`, or None if the book never
    entered. Ticks merge both sides on `marked_at` (one shared timestamp per loop tick), each
    carrying whatever legs were marked usable at that instant."""
    positions = {p["side"]: p for p in db.positions_for_week(conn, week_of) if p["book"] == book}
    if not positions:
        return None
    legs: dict[str, dict] = {}
    ticks: dict[float, dict] = {}
    for position in positions.values():
        for leg in db.legs_for(conn, position["position_id"]):
            legs[leg["leg_role"]] = leg
        for row in conn.execute(
            "SELECT * FROM dc_marks WHERE position_id = ? AND leg_role IS NOT NULL ORDER BY marked_at",
            (position["position_id"],),
        ):
            tick = ticks.setdefault(
                row["marked_at"],
                {"ts": row["marked_at"], "session_date": row["session_date"], "spot": None, "legs": {}},
            )
            if row["spot"] is not None:
                tick["spot"] = row["spot"]
            if row["usable"]:
                tick["legs"][row["leg_role"]] = {"bid": row["bid"], "ask": row["ask"], "mid": row["mid"]}
    assignments: dict[str, dict] = {}
    for position in positions.values():
        for row in db.assignments_for(conn, position["position_id"]):
            assignments[row["leg_role"]] = row
    return {
        "week_of": week_of,
        "positions": positions,
        "legs": legs,
        "assignments": assignments,
        "ticks": [ticks[ts] for ts in sorted(ticks)],
    }


def _tick_minute(tick: dict) -> int:
    return clock.minute_of_day(datetime.fromtimestamp(tick["ts"], clock.ET))


def _usable(tick: dict, roles: tuple[str, ...]) -> bool:
    return all(role in tick["legs"] for role in roles)


def _combined_value(tick: dict) -> float | None:
    if not _usable(tick, _ROLES):
        return None
    legs = tick["legs"]
    return round(
        (legs["back_put"]["mid"] - legs["front_put"]["mid"])
        + (legs["back_call"]["mid"] - legs["front_call"]["mid"]),
        4,
    )


def _terminal_day(week: dict, when: str) -> str:
    front = date.fromisoformat(next(iter(week["positions"].values()))["front_expiration"])
    if when == "thu_close":
        day = front - timedelta(days=1)
        while not _cal.is_trading_day(day):
            day -= timedelta(days=1)
        return day.isoformat()
    return front.isoformat()


def _first_tick(week: dict, roles: tuple[str, ...], *, day: str, minute: int) -> dict | None:
    """The first tick on `day` at/after `minute` where every role in `roles` is usable — the tick
    the real loop would have acted on."""
    for tick in week["ticks"]:
        if tick["session_date"] != day or _tick_minute(tick) < minute:
            continue
        if _usable(tick, roles):
            return tick
    return None


# --------------------------------------------------------------------------- exit pricing
def _close_roles(week: dict, tick: dict, roles: tuple[str, ...], config: dict, exits: dict) -> dict:
    """Price closing `roles` at this tick's marks: per-leg exit values into `exits`, and the cost
    of the trade (close fee + slippage off the tick's own bid/ask)."""
    position = next(iter(week["positions"].values()))
    quantity = int(position["quantity"] or 1)
    leg_quotes = []
    sell_legs = 0
    for role in roles:
        mark = tick["legs"][role]
        leg_quotes.append({"bid": mark["bid"], "ask": mark["ask"]})
        if role.startswith("back"):
            sell_legs += 1
        exits[role] = {"value": mark["mid"], "kind": "traded", "session": tick["session_date"]}
    return engine.close_cost(position["symbol"], leg_quotes, quantity, config, sell_legs=sell_legs)


def _settle_shorts(week: dict, exits: dict) -> tuple[float, float, str] | None:
    """Price the shorts at settlement off the PATH book's recorded settlement, as
    `(exit_costs, share_pnl_dollars, label)`.

    The position row carries the settlement spot and intrinsic is recomputed from it, so the derived
    number and the recorded leg agree by construction. Under a PHYSICAL style the option half is
    unchanged — intrinsic is the option's value at expiry either way — and what the policy also
    inherits is the delivered shares, taken from the recorded assignment rather than re-simulated:
    the disposal happened at a price the record holds, and inventing one would be the same guess the
    `mon_open` fallback below already refuses to make.

    None when settlement never happened, or when shares were delivered and not yet disposed — both
    are derivation holes, not zeros.
    """
    position = next(iter(week["positions"].values()))
    spot = position["settlement_spot"]
    for p in week["positions"].values():
        if p["settlement_spot"] is not None:
            spot = p["settlement_spot"]
    if spot is None:
        return None
    itm = 0
    costs = 0.0
    share_pnl = 0.0
    for role in ("front_put", "front_call"):
        leg = week["legs"].get(role)
        if leg is None:
            return None
        intrinsic = engine.settle_intrinsic(leg["strike"], leg["option_type"], spot)
        assignment = (week.get("assignments") or {}).get(role)
        exits[role] = {
            "value": intrinsic,
            "kind": "assigned" if assignment is not None else "cash_settled",
            "session": leg["expiration"],
        }
        if assignment is not None:
            if assignment["status"] != "disposed" or assignment["share_pnl"] is None:
                return None
            share_pnl += assignment["share_pnl"]
            costs += assignment["fees"] or 0.0
        elif intrinsic > 0:
            itm += 1
    return costs + engine.settlement_fee(itm), round(share_pnl, 2), "settled"


# --------------------------------------------------------------------------- the derivation
def derive(week: dict, policy_name: str, config: dict) -> dict:
    """One week under one policy: `{"derivable": True, gross, fees, net, exits}` or a refusal.

    `fees` = the REAL recorded entry costs (shared fills — the entry already happened) plus the
    DERIVED exit costs this policy would have paid. `net = gross - fees`, same convention as the
    ledger reader.
    """
    spec = POLICIES[policy_name]
    defaults = config.get("defaults") or {}
    exit_min = clock.hhmm_to_min(defaults.get("exit_window_start"), 15 * 60 + 45)
    noon_min = clock.hhmm_to_min(defaults.get("noon_exit_start"), 12 * 60)
    disposition_min = clock.hhmm_to_min(defaults.get("mon_disposition_time"), 9 * 60 + 45)

    if set(week["legs"]) != set(_ROLES) or len(week["positions"]) != 2:
        return _not_derivable(week, policy_name, "incomplete_week")

    exits: dict[str, dict] = {}
    exit_costs = 0.0
    # Dollars, not per-share: delivered shares are a whole-position quantity, so this is added after
    # the per-share legs are scaled. Zero for every policy that exits before expiry, which is every
    # policy that never lets a short be assigned.
    share_pnl = 0.0
    trigger = None

    if spec["kind"] in ("pt", "sl"):
        entry_debit = sum(p["entry_debit"] or 0 for p in week["positions"].values())
        if not entry_debit:
            return _not_derivable(week, policy_name, "no_entry_debit")
        for tick in week["ticks"]:
            value = _combined_value(tick)
            if value is None:
                continue
            move = (value - entry_debit) / entry_debit
            hit = move >= spec["pct"] if spec["kind"] == "pt" else move <= -spec["pct"]
            if hit:
                exit_costs += _close_roles(week, tick, _ROLES, config, exits)["total"]
                trigger = {"reason": spec["kind"], "session": tick["session_date"], "move": round(move, 4)}
                break

    elif spec["kind"] == "touch":
        for side, roles in (("put", ("front_put", "back_put")), ("call", ("front_call", "back_call"))):
            strike = week["positions"][side]["strike"]
            for tick in week["ticks"]:
                spot = tick["spot"]
                if spot is None or not _usable(tick, roles):
                    continue
                touched = spot <= strike if side == "put" else spot >= strike
                if touched:
                    exit_costs += _close_roles(week, tick, roles, config, exits)["total"]
                    trigger = {"reason": "touch", "side": side, "session": tick["session_date"]}
                    break

    elif spec["kind"] == "time" and spec["when"] != "fri_close":
        day = _terminal_day(week, spec["when"])
        minute = noon_min if spec["when"] == "fri_noon" else exit_min
        tick = _first_tick(week, _ROLES, day=day, minute=minute)
        if tick is None:
            return _not_derivable(week, policy_name, f"no_mark_{spec['when']}")
        exit_costs += _close_roles(week, tick, _ROLES, config, exits)["total"]
        trigger = {"reason": spec["when"], "session": tick["session_date"]}

    elif spec["kind"] == "expiry":
        settled = _settle_shorts(week, exits)
        if settled is None:
            return _not_derivable(week, policy_name, "no_settlement_on_file")
        exit_costs += settled[0]
        share_pnl += settled[1]
        long_roles = ("back_put", "back_call")
        if spec["longs"] == "fri_close":
            day = _terminal_day(week, "fri_close")
            tick = _first_tick(week, long_roles, day=day, minute=exit_min)
            if tick is None:
                return _not_derivable(week, policy_name, "no_mark_fri_close")
            exit_costs += _close_roles(week, tick, long_roles, config, exits)["total"]
        else:  # mon_open — the path book's own shape
            day = next(iter(week["positions"].values()))["back_expiration"]
            tick = _first_tick(week, long_roles, day=day, minute=disposition_min)
            if tick is not None:
                exit_costs += _close_roles(week, tick, long_roles, config, exits)["total"]
            else:
                # Never disposed — fall back to what the real book recorded (its own expiry
                # settlement); a derivation inventing a Monday price the record does not hold
                # would be a guess.
                for role in long_roles:
                    leg = week["legs"][role]
                    if leg["close_value"] is None:
                        return _not_derivable(week, policy_name, "no_mark_mon_open")
                    exits[role] = {
                        "value": leg["close_value"],
                        "kind": leg["close_kind"] or "cash_settled",
                        "session": leg["expiration"],
                    }
        trigger = {"reason": f"expiry_longs_{spec['longs']}", "session": day}

    # Anything not exited by its trigger falls through to the control terminal.
    remaining = tuple(role for role in _ROLES if role not in exits)
    if remaining:
        day = _terminal_day(week, "fri_close")
        tick = _first_tick(week, remaining, day=day, minute=exit_min)
        if tick is None:
            return _not_derivable(week, policy_name, "no_terminal_mark")
        exit_costs += _close_roles(week, tick, remaining, config, exits)["total"]
        trigger = trigger or {"reason": "fri_close", "session": day}

    quantity = int(next(iter(week["positions"].values()))["quantity"] or 1)
    per_share = 0.0
    for role, leg in week["legs"].items():
        exit_value = exits[role]["value"]
        entry_mid = leg["entry_mid"]
        per_share += (entry_mid - exit_value) if leg["action"] == "Sell to Open" else (exit_value - entry_mid)
    gross = round(per_share * 100 * quantity + share_pnl, 2)
    entry_costs = sum((p["entry_cost"] or 0) + (p["entry_slippage"] or 0) for p in week["positions"].values())
    fees = round(entry_costs + exit_costs, 2)
    return {
        "week_of": week["week_of"],
        "policy": policy_name,
        "structure": next(iter(week["positions"].values()))["structure"],
        "derivable": True,
        "gross_pnl": gross,
        "fees": fees,
        "net_pnl": round(gross - fees, 2),
        "trigger": trigger,
        "exits": exits,
    }


def _not_derivable(week: dict, policy_name: str, reason: str) -> dict:
    return {
        "week_of": week["week_of"],
        "policy": policy_name,
        "structure": next(iter(week["positions"].values()))["structure"] if week["positions"] else None,
        "derivable": False,
        "reason": reason,
    }


# --------------------------------------------------------------------------- the read surfaces
def _completed_weeks(conn, book: str) -> list[str]:
    """Weeks whose `book` positions are all closed — a week still in flight has no answer yet."""
    return [
        r["week_of"]
        for r in conn.execute(
            "SELECT week_of, COUNT(*) AS n, SUM(status = 'closed') AS done FROM dc_positions "
            "WHERE book = ? GROUP BY week_of HAVING n = done ORDER BY week_of",
            (book,),
        )
    ]


def comparison_table(conn, config: dict) -> dict:
    """Every policy over every completed path-book week, grouped by structure tag (distinct tags
    are distinct trades and never pool). This table is the module's answer to "which exit
    parameters work" — read beside `validation`, which says whether to believe it."""
    weeks = [w for w in (week_data(conn, week_of, "path") for week_of in _completed_weeks(conn, "path")) if w]
    breaks = [dict(r) for r in conn.execute("SELECT * FROM measurement_breaks ORDER BY break_date")]
    table: dict[str, dict] = {}
    for policy_name in POLICIES:
        by_structure: dict[str, dict] = {}
        for week in weeks:
            result = derive(week, policy_name, config)
            bucket = by_structure.setdefault(
                result.get("structure") or "unknown",
                {"weeks": 0, "derivable": 0, "total_net": 0.0, "wins": 0, "worst": None},
            )
            bucket["weeks"] += 1
            if not result["derivable"]:
                continue
            bucket["derivable"] += 1
            bucket["total_net"] = round(bucket["total_net"] + result["net_pnl"], 2)
            if result["net_pnl"] > 0:
                bucket["wins"] += 1
            if bucket["worst"] is None or result["net_pnl"] < bucket["worst"]["net_pnl"]:
                bucket["worst"] = {"week_of": result["week_of"], "net_pnl": result["net_pnl"]}
        for bucket in by_structure.values():
            n = bucket["derivable"]
            bucket["avg_net"] = round(bucket["total_net"] / n, 2) if n else None
            bucket["win_rate"] = round(bucket["wins"] / n, 4) if n else None
        table[policy_name] = by_structure
    return {
        "policies": table,
        "weeks_considered": len(weeks),
        "measurement_breaks": breaks,
        "caveat": (
            "triggers are evaluated at the recorded tick cadence; a threshold crossed and "
            "re-crossed between ticks is invisible"
        ),
        "validation": validate_against_control(conn, config),
    }


def validate_against_control(conn, config: dict, tolerance: float = 0.50) -> dict:
    """The derivation reproduced against reality: derived `control` vs the control book's real
    recorded net (from the control book's OWN marks), and derived `expiry-longs-mon` vs the path
    book's real net. A mismatch past `tolerance` dollars means the replay and the books disagree
    about the same trade, and the policy table should not be trusted until it is explained."""
    checks = []
    for book, policy_name in (("control", "control"), ("path", "expiry-longs-mon")):
        for week_of in _completed_weeks(conn, book):
            week = week_data(conn, week_of, book)
            if week is None:
                continue
            real_gross = sum(p["gross_pnl"] or 0 for p in week["positions"].values())
            real_fees = sum(p["fees"] or 0 for p in week["positions"].values())
            derived = derive(week, policy_name, config)
            if not derived["derivable"]:
                checks.append({"week_of": week_of, "book": book, "ok": False, "reason": derived["reason"]})
                continue
            diff = round(derived["net_pnl"] - round(real_gross - real_fees, 2), 2)
            checks.append(
                {
                    "week_of": week_of,
                    "book": book,
                    "derived_net": derived["net_pnl"],
                    "real_net": round(real_gross - real_fees, 2),
                    "diff": diff,
                    "ok": abs(diff) <= tolerance,
                }
            )
    return {
        "compared": len(checks),
        "ok": all(c["ok"] for c in checks) if checks else True,
        "mismatches": [c for c in checks if not c["ok"]],
    }
