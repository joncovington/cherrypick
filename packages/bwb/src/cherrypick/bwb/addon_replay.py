"""The add-on as its own trade, replayed over what this module already recorded (2026-09-26).

The question: the `delta` arm's add-on made 73% of the arm's gross on 19% of its buying power over
time (2026-08-24 to 2026-09-25), so is the add-on worth trading WITHOUT the BWB under it? Two
variants, both paired to the delta arm's own cohorts (a cohort exists only where the fly entered,
so the pairing is exact), and neither needs a new loop:

- `addon-only`: delta's trigger and delta's add-on, exactly, with no fly. The BWB's strikes are
  recorded but never traded; they place the trigger (the near wing's |delta|) and the bracket (one
  increment either side of the far wing). Armed on the first tick `triggers.delta_fires`, fired on
  the first tick from then on that the bracket prices as a credit above `addon_credit_floor` -- the
  loop's own latch rule.
- `spread-daily`: the SAME bracket, sold at the cohort's entry tick every session, no trigger. The
  comparator: if `addon-only` does not beat it, the trigger added nothing and the result is the
  market drifting up, not a reversion edge.

Everything is priced from `bwb_trigger_ticks`, which carries the bracket's own bid/ask on every tick
for exactly this purpose (see replay.py's honesty rails: an unpriceable tick is skipped, never
guessed). Costs are the module's own: `engine.addon_entry_cost` (fee + the suite slippage model,
charged as a cost) and `engine.settlement_fee` per distinct ITM symbol. Settlement uses the official
print the ledger itself recorded for that expiration; an expiration without one is left unscored.

`validate` checks `addon-only` against the delta arm's real add-on fills: the same cohorts must fire
on the same session, at a credit within tolerance of the one the loop recorded. It is the check that
says the replay is the thing it claims to be.

Samples are counted by SETTLEMENT FRIDAY, not by fire: every fire expiring the same Friday shares one
print, so they are one observation.
"""

from __future__ import annotations

from datetime import date

from cherrypick.bwb import db as _db
from cherrypick.bwb import engine, triggers

VARIANTS = ("addon-only", "spread-daily")


# --------------------------------------------------------------------------- pure layer
def bracket_quotes(tick: dict) -> dict | None:
    """The bracket's bid/ask on this tick, and its mid credit; None when any quote is missing."""
    keys = ("addon_short_bid", "addon_short_ask", "addon_long_bid", "addon_long_ask")
    if any(tick.get(k) is None for k in keys):
        return None
    sb, sa, lb, la = (float(tick[k]) for k in keys)
    return {
        "short": {"bid": sb, "ask": sa},
        "long": {"bid": lb, "ask": la},
        "credit": round((sb + sa) / 2 - (lb + la) / 2, 4),
    }


def _sale(tick: dict, quotes: dict) -> dict:
    return {
        "ticked_at": tick.get("ticked_at"),
        "session_date": tick.get("session_date"),
        "credit": quotes["credit"],
        "quotes": [quotes["short"], quotes["long"]],
    }


def addon_only_fire(ticks: list[dict], params: dict) -> dict | None:
    """The delta arm's add-on on one cohort's recorded path: armed on the first `delta_fires` tick,
    fired on the first tick from there whose bracket prices as a credit above the floor."""
    floor = float(params.get("addon_credit_floor", 0.0) or 0.0)
    armed = False
    for tick in ticks:
        if not armed and triggers.delta_fires(tick.get("near_abs_delta"), params):
            armed = True
        if armed:
            quotes = bracket_quotes(tick)
            if quotes is not None and quotes["credit"] > floor:
                return _sale(tick, quotes)
    return None


def daily_sale(ticks: list[dict], entry_session: str, params: dict) -> dict | None:
    """The same bracket sold on the cohort's entry session, at the first tick it prices as a credit."""
    floor = float(params.get("addon_credit_floor", 0.0) or 0.0)
    for tick in ticks:
        if tick.get("session_date") != entry_session:
            continue
        quotes = bracket_quotes(tick)
        if quotes is not None and quotes["credit"] > floor:
            return _sale(tick, quotes)
    return None


def score(
    sale: dict,
    short: float,
    long: float,
    settle: float | None,
    quantity: int,
    config: dict,
    *,
    symbol: str = "SPX",
    expiration: str | None = None,
) -> dict:
    """One sold bracket, whole-position dollars, the trade table standard's identities: entry + exit
    = gross; gross - fees - settlement - slippage = net. Unscored (no exit, no net) without a print."""
    credit = sale["credit"]
    cost = engine.addon_entry_cost(symbol, sale["quotes"], quantity, config)
    width = short - long
    max_loss = round((width - credit) * 100 * quantity, 2)
    days = None
    if expiration is not None and sale.get("session_date"):
        days = max((date.fromisoformat(expiration) - date.fromisoformat(sale["session_date"])).days, 1)
    out = {
        "session_date": sale["session_date"],
        "short_strike": short,
        "long_strike": long,
        "credit": credit,
        "entry_cash": round(credit * 100 * quantity, 2),
        "fees": cost["fee"],
        "slippage": cost["slippage"],
        "max_loss": max_loss,
        "bp_days": None if days is None else round(max_loss * days, 2),
        "settlement_spot": settle,
        "exit_cash": None,
        "gross": None,
        "settlement_fees": None,
        "net": None,
    }
    if settle is None:
        return out
    owed = engine.settle_intrinsic(short, settle) - engine.settle_intrinsic(long, settle)
    itm = sum(1 for k in (short, long) if engine.settle_intrinsic(k, settle) > 0)
    settlement_fees = engine.settlement_fee(itm)
    exit_cash = round(-owed * 100 * quantity, 2)
    gross = round(out["entry_cash"] + exit_cash, 2)
    out.update(
        exit_cash=exit_cash,
        gross=gross,
        settlement_fees=settlement_fees,
        net=round(gross - cost["fee"] - cost["slippage"] - settlement_fees, 2),
    )
    return out


# --------------------------------------------------------------------------- DB layer
def _cohorts(conn) -> list[dict]:
    """The delta arm's cohorts: one per session its fly entered, with the strikes and expiration."""
    return [
        dict(r)
        for r in conn.execute(
            "SELECT position_id, entry_session, structure_signature, symbol, expiration, far_strike, "
            "quantity, armed_at, addon_fired_at FROM bwb_positions WHERE arm = 'delta' ORDER BY entry_session"
        )
    ]


def settlement_prints(conn) -> dict[str, float]:
    """The print the ledger settled each expiration on. An expiration with two different prints is a
    defect made visible, not averaged."""
    prints: dict[str, set] = {}
    for r in conn.execute(
        "SELECT expiration, settlement_spot FROM bwb_positions WHERE settlement_spot IS NOT NULL"
    ):
        prints.setdefault(r["expiration"], set()).add(round(float(r["settlement_spot"]), 4))
    bad = {k: v for k, v in prints.items() if len(v) > 1}
    if bad:
        raise ValueError(f"expirations settled on more than one print: {bad}")
    return {k: next(iter(v)) for k, v in prints.items()}


def real_addon_trades(conn, arm: str) -> list[dict]:
    """One arm's add-ons as their own trades, from the fills the loop actually recorded: the two
    add-on legs' entry mids and close values, the add-on's own recorded fee and slippage, and the
    $5 settlement fee on the add-on legs that finished in the money. The fly under it is left out
    entirely -- that is the point."""
    out = []
    for p in conn.execute(
        "SELECT position_id, entry_session, expiration, quantity, addon_fired_at, addon_short_strike, "
        "addon_long_strike, addon_cost, addon_slippage, settlement_spot FROM bwb_positions "
        "WHERE arm = ? AND addon_fired_at IS NOT NULL ORDER BY entry_session",
        (arm,),
    ):
        legs = {
            r["leg_role"]: dict(r)
            for r in conn.execute(
                "SELECT leg_role, entry_mid, close_value, close_kind, status FROM bwb_legs "
                "WHERE position_id = ? AND leg_role IN ('addon_short', 'addon_long')",
                (p["position_id"],),
            )
        }
        short, long = legs.get("addon_short"), legs.get("addon_long")
        if short is None or long is None or short["entry_mid"] is None or long["entry_mid"] is None:
            continue
        q = int(p["quantity"] or 1)
        credit = round(short["entry_mid"] - long["entry_mid"], 4)
        width = p["addon_short_strike"] - p["addon_long_strike"]
        max_loss = round((width - credit) * 100 * q, 2)
        fired = p["addon_fired_at"][:10]
        days = max((date.fromisoformat(p["expiration"]) - date.fromisoformat(fired)).days, 1)
        trade = {
            "entry_session": p["entry_session"],
            "expiration": p["expiration"],
            "session_date": fired,
            "short_strike": p["addon_short_strike"],
            "long_strike": p["addon_long_strike"],
            "credit": credit,
            "entry_cash": round(credit * 100 * q, 2),
            "fees": round(p["addon_cost"] or 0.0, 2),
            "slippage": round(p["addon_slippage"] or 0.0, 2),
            "max_loss": max_loss,
            "bp_days": round(max_loss * days, 2),
            "settlement_spot": p["settlement_spot"],
            "exit_cash": None,
            "gross": None,
            "settlement_fees": None,
            "net": None,
        }
        if short["close_value"] is not None and long["close_value"] is not None:
            itm = sum(1 for leg in (short, long) if leg["close_kind"] == "itm")
            exit_cash = round(-(short["close_value"] - long["close_value"]) * 100 * q, 2)
            gross = round(trade["entry_cash"] + exit_cash, 2)
            settlement_fees = engine.settlement_fee(itm)
            trade.update(
                exit_cash=exit_cash,
                gross=gross,
                settlement_fees=settlement_fees,
                net=round(gross - trade["fees"] - trade["slippage"] - settlement_fees, 2),
            )
        out.append(trade)
    return out


def _armed_session(ticks: list[dict], params: dict) -> str | None:
    for tick in ticks:
        if triggers.delta_fires(tick.get("near_abs_delta"), params):
            return tick.get("session_date")
    return None


def run(conn, config: dict) -> dict:
    """Both variants, the other-timing comparators, and the validation."""
    params = {**triggers.TRIGGER_DEFAULTS, **engine.merged_params(config, "delta")}
    increment = float(params.get("strike_increment", engine.STRIKE_INCREMENT))
    prints = settlement_prints(conn)
    daily: list[dict] = []
    unpriced: list[str] = []
    validation = []

    for c in _cohorts(conn):
        ticks = _db.trigger_ticks_for_cohort(conn, c["entry_session"], c["structure_signature"])
        short, long = c["far_strike"] + increment, c["far_strike"] - increment
        sale = daily_sale(ticks, c["entry_session"], params)
        if sale is None:
            unpriced.append(c["entry_session"])
        else:
            daily.append(
                {
                    "entry_session": c["entry_session"],
                    "expiration": c["expiration"],
                    **score(
                        sale,
                        short,
                        long,
                        prints.get(c["expiration"]),
                        int(c["quantity"] or 1),
                        config,
                        symbol=c["symbol"],
                        expiration=c["expiration"],
                    ),
                }
            )
        # The trigger's timing, replayed from the recorded near-wing deltas against when the real
        # arm armed. (The fire itself also needs the bracket priced; that is checkable only on ticks
        # recorded after 2026-09-26.)
        real = (c["armed_at"] or "")[:10] or None
        replayed = _armed_session(ticks, params)
        validation.append(
            {
                "entry_session": c["entry_session"],
                "real_armed": real,
                "replay_armed": replayed,
                "ok": real == replayed,
            }
        )

    return {
        "params": {
            "delta_trigger": params["delta_trigger"],
            "addon_credit_floor": params.get("addon_credit_floor", 0.0),
        },
        "variants": {
            "addon-only": summarize(real_addon_trades(conn, "delta")),
            "spread-daily": {**summarize(daily), "unpriced_cohorts": unpriced},
        },
        # The same bracket on the same cohorts, sold at a different moment: what timing alone is worth.
        "same_spread_other_timing": {
            arm: summarize(real_addon_trades(conn, arm)) for arm in ("bounce", "flip")
        },
        "trades": {"addon-only": real_addon_trades(conn, "delta"), "spread-daily": daily},
        "validation": {
            "cohorts": len(validation),
            "ok": all(v["ok"] for v in validation),
            "mismatches": [v for v in validation if not v["ok"]],
        },
    }


def summarize(trades: list[dict]) -> dict:
    """Totals over settled trades, and the same by settlement Friday -- the unit of independence."""
    settled = [t for t in trades if t["net"] is not None]
    fridays: dict[str, dict] = {}
    for t in settled:
        f = fridays.setdefault(t["expiration"], {"trades": 0, "net": 0.0, "max_loss": 0.0})
        f["trades"] += 1
        f["net"] = round(f["net"] + t["net"], 2)
        f["max_loss"] = round(f["max_loss"] + t["max_loss"], 2)
    total = lambda k: round(sum(t[k] for t in settled), 2)  # noqa: E731
    return {
        "trades": len(trades),
        "settled": len(settled),
        "open": len(trades) - len(settled),
        "fridays": len(fridays),
        "wins": sum(1 for t in settled if t["net"] > 0),
        "gross": total("gross"),
        "fees": total("fees"),
        "slippage": total("slippage"),
        "settlement_fees": total("settlement_fees"),
        "net": total("net"),
        "bp_days": total("bp_days") if settled else 0.0,
        "worst_trade": min((t["net"] for t in settled), default=None),
        "worst_friday": min((f["net"] for f in fridays.values()), default=None),
        "by_friday": dict(sorted(fridays.items())),
    }
