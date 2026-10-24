"""Our own credit-spread list: the chart setups' fresh entries as spreads, priced on the afternoon chain.

The vendor's credit-spread tab froze with its subscription (2026-10-23 lists). This is its replacement,
built to the user's stated trade (2026-10-24) and to be judged by item 17 of
`docs/edge-hypotheses-plan.md`, which has **not been run**: until it is, every row is untested, and the
file says so (`TESTED`).

- **Names:** the short-put list's universe, every name the afternoon income chains read.
- **Signal:** a position one of the four chart setups holds (`setups.py`), entered on one of the last
  `RECENT` closes and still open, walked over the history store (`study.load`), the item-17 study's
  source. A long setup is a put spread, its short mirror a call spread. One row per name and side,
  every setup behind it named; a name signalling both sides carries `conflict`.
- **Expiry:** the 30-45 day monthly (the income chains' `cs` block). No such monthly, no row.
- **Short:** the out-of-the-money option nearest `SHORT_DELTA`, within the lists' delta tolerance.
- **The pick (the user's rule):** the widest long beyond the short whose working credit is
  `CREDIT_SHARE` of the width. None lands there often (item 14: ~15% of entries); the row stays, with
  `pick` null, and the width table shows how near each width comes.
- **The width table, per row:** `STEP_WIDTHS` listed strikes beyond the short and `MOVE_WIDTHS` of the
  1-SD move to expiry, each with its working credit, credit / width, credit / max loss and the leg
  bars' verdict on the spread as one order -- the per-symbol width item 17 will score.

Prices are per share, at the WORKING price (`legbars.structure_prices`: halfway between the mid and
the natural fill), signed so a credit is +. No row is a recommendation: rows are by symbol, never by
credit.
"""

from __future__ import annotations

import math
from typing import Any

from cherrypick.core import incomerules, legbars

from . import setups

# The four setups, each side's spread. Every other id in `setups.RULES` is study-only.
SPREAD_SIDE = {
    "trend": "P",
    "pullback": "P",
    "reversion": "P",
    "breakout": "P",
    "trend-short": "C",
    "pullback-short": "C",
    "reversion-short": "C",
    "breakout-short": "C",
}
RECENT = 5
SHORT_DELTA = 0.40
CREDIT_SHARE = (0.35, 0.40)
STEP_WIDTHS = (1, 2, 3, 5)
MOVE_WIDTHS = (0.25, 0.5, 0.75)
TESTED = "not yet: item 17 of docs/edge-hypotheses-plan.md"


def fresh_entries(runs: dict[str, list[setups.Trade]], dates: list[str], recent: int = RECENT) -> list[dict]:
    """The open positions entered on one of the last `recent` bars, by setup: {setup, side, signal
    date, sessions ago (0 = the last close)}. `runs` is each setup id's walk (`setups.run`)."""
    n = len(dates)
    out = []
    for sid, trades in runs.items():
        side = SPREAD_SIDE.get(sid)
        if side is None:
            continue
        for t in trades:
            if t.exit is None and n - recent <= t.entry < n:
                out.append({"setup": sid, "side": side, "date": dates[t.entry], "ago": n - 1 - t.entry})
    return sorted(out, key=lambda e: (e["side"], e["ago"], e["setup"]))


def expected_move(spot: float, vol: float | None, dte: int) -> float | None:
    """One standard deviation to expiry, in dollars: the close x vol x the root of the year's share."""
    if not spot or not vol or vol <= 0 or not dte or dte <= 0:
        return None
    return spot * vol * math.sqrt(dte / 365.0)


def beyond(pool: list[dict], short: dict) -> list[dict]:
    """The options of the short's right further out of the money than it, nearest first."""
    if short["right"] == "P":
        return sorted(
            (o for o in pool if o["right"] == "P" and o["strike"] < short["strike"]),
            key=lambda o: -o["strike"],
        )
    return sorted(
        (o for o in pool if o["right"] == "C" and o["strike"] > short["strike"]), key=lambda o: o["strike"]
    )


def spread(short: dict, long: dict, bars: dict | None) -> dict | None:
    """One spread's figures, per share: width, working and mid credit, credit / width, max loss and
    credit / max loss, and the leg bars' verdict as one order. None when it does not price."""
    legs = [(short, -1), (long, 1)]
    p = legbars.structure_prices(legs)
    if p is None:
        return None
    width = abs(short["strike"] - long["strike"])
    credit = p["working"]
    loss = width - credit
    return {
        "long": long["strike"],
        "long_option": long.get("option"),
        "long_delta": round(long["delta"], 4),
        "width": round(width, 4),
        "credit": credit,
        "credit_mid": p["mid"],
        "credit_width": round(credit / width, 4) if width else None,
        "max_loss": round(loss, 4),
        "credit_max_loss": round(credit / loss, 4) if loss > 0 else None,
        "passes": legbars.structure_passes(legs, bars) if bars else None,
    }


def width_table(short: dict, pool: list[dict], move: float | None, bars: dict | None) -> list[dict]:
    """The spread at each declared width: `STEP_WIDTHS` listed strikes beyond the short, and the
    listed strike nearest `MOVE_WIDTHS` of the move beyond it (the nearer on a tie). A width the chain
    does not reach, or that lands on the short itself, is a row with no figures."""
    out = []
    far = beyond(pool, short)
    for n in STEP_WIDTHS:
        s = spread(short, far[n - 1], bars) if len(far) >= n else None
        out.append({"unit": "strikes", "size": n, **(s or {})})
    sign = -1 if short["right"] == "P" else 1
    for f in MOVE_WIDTHS:
        s = None
        if move and far:
            target = short["strike"] + sign * f * move
            pick = min(far, key=lambda o: (abs(o["strike"] - target), abs(o["strike"] - short["strike"])))
            s = spread(short, pick, bars)
        out.append({"unit": "move", "size": f, **(s or {})})
    return out


def pick_width(short: dict, pool: list[dict], bars: dict | None) -> dict | None:
    """The user's rule: the widest long beyond the short whose working credit is inside
    `CREDIT_SHARE` of the width; None when no listed width lands there."""
    lo, hi = CREDIT_SHARE
    best = None
    for o in beyond(pool, short):
        s = spread(short, o, bars)
        if s and s["credit_width"] is not None and lo <= s["credit_width"] <= hi:
            best = s
    return best


def build_rows(
    sym: str,
    entry: dict,
    signals: list[dict],
    priced: list[dict],
    session: str,
    kind: str | None,
    bars: dict | None,
    leveraged: str | None = None,
) -> tuple[list[dict], list[str]]:
    """(rows, drop reasons) for one name: a row per side its fresh entries call for. `priced` is the
    `cs` block's options, priced (`incomelists.priced`)."""
    from . import incomelists

    cs = entry.get("cs") or {}
    spot = float(entry["spot"])
    sides = sorted({s["side"] for s in signals})
    rows, drops = [], []
    for side in sides:
        short = incomelists.within(incomelists.nearest(priced, side, SHORT_DELTA, spot), SHORT_DELTA)
        if short is False:
            drops.append("no short within the delta tolerance")
            continue
        if short is None:
            drops.append("no priced option out of the money")
            continue
        vol = entry.get("iv_index") or (entry.get("hv_30") or 0) / 100.0 or None
        move = expected_move(spot, vol, int(cs["dte"]))
        ex = entry.get("ex_dividend_date")
        rows.append(
            {
                "symbol": sym,
                "type": kind,
                "spread": "put" if side == "P" else "call",
                "setups": [s["setup"] for s in signals if s["side"] == side],
                "signals": [s for s in signals if s["side"] == side],
                "conflict": len(sides) > 1,
                "spot": round(spot, 4),
                "expiry": cs["expiry"],
                "dte": cs["dte"],
                "move": None if move is None else round(move, 4),
                "short": {
                    "strike": short["strike"],
                    "option": short.get("option"),
                    "delta": round(short["delta"], 4),
                    "iv": round(short["iv"], 4),
                    "bid": short.get("bid"),
                    "ask": short.get("ask"),
                },
                "pick": pick_width(short, priced, bars),
                "widths": width_table(short, priced, move, bars),
                "earnings": incomelists.earnings_state(entry, session, cs["expiry"], kind),
                "earnings_date": entry.get("earnings_date"),
                "earnings_estimated": entry.get("earnings_estimated"),
                # A short call across an ex-date can be assigned early for the dividend: flagged.
                "ex_dividend": incomelists.ex_dividend_state(ex, session, cs["expiry"])
                if side == "C"
                else None,
                "ex_dividend_date": ex if side == "C" else None,
                "iv_index": entry.get("iv_index"),
                "leveraged": leveraged,
            }
        )
    return rows, drops


def rule() -> dict[str, Any]:
    """What the list's rows were built by, for the file."""
    return {
        "setups": SPREAD_SIDE,
        "recent_sessions": RECENT,
        "short_delta": SHORT_DELTA,
        "delta_tolerance": incomerules.DELTA_TOLERANCE,
        "credit_share": CREDIT_SHARE,
        "step_widths": STEP_WIDTHS,
        "move_widths": MOVE_WIDTHS,
        "price": "working (halfway between the mid and the natural fill)",
        "tested": TESTED,
    }
