"""Income trades from our own universe, sized to the account (docs/portfolio-guidance-plan.md, 4).

A pure function of the evening `income` reading the snapshot carries (`fetch_screener_greeks.py
income`: every tradable name's cycle-monthly chain, quoted in regular hours, with its earnings dates,
beta and stress move) and the portfolio reading manage has already made of the book. For each name it
builds the structures the suite's screener rules describe, keeps the ones whose every leg is quoted,
liquid by the leg bars (`cherrypick.core.legbars`: one option as a leg, a spread as one order) and
clear of an earnings report, and gives each a QUANTITY: the largest that clears every cap, with the
cap that stops it named.

The structures, the Screeners lists' rules from 2026-10-19 (before, the vendor's as `packages/technicals`
`screeners.py` reproduced them: a 0.40-delta put and a 0.165-delta call, which the delta studies
measured as worse), on the user's cycle monthly:

- a **short put** at the strike nearest `short_put_delta` (0.16);
- a **put or call credit spread**: short the first strike out of the money, long the strike nearest
  `spread_long_delta` (0.25) beyond it, offered only at a credit of `spread_min_credit` (a third) of
  the width at the WORKING price (halfway from the mid to the natural fill; from 2026-10-19 -- over
  seven sessions of cycle-monthly spreads 78% cleared a third at the mid and 62% at the natural);
- a **covered call** at the strike nearest `covered_call_delta` (0.16), on its OWN expiry: the
  reading's `cc` block (the monthly 30-45 days out, else the cycle monthly), only on a name the account
  holds at least 100 shares of, never more contracts than the shares cover. Shares already written
  against are spoken for: the calls still sellable are (shares - 100 x the short calls already open
  on the name) // 100, counting every expiry and every short call leg, including the short in a
  call spread or a diagonal whose long would cover it. That last part is deliberately cautious: a
  long call covers a short only at a strike and expiry the broker accepts, and in an IRA a spread
  needs its own approval, so the shares are never assumed free on the strength of a long. A name
  whose shares are all written against gets no covered call (counted in `excluded`). A call being
  sold to open and not yet filled (snapshot v10's `working_orders`, `pending_short_calls`) counts as
  written too; before v10 the snapshot has no orders and only filled calls are seen.

Deltas are Black-Scholes on each strike's own IV, solved from its mid, as the screener rules found.

The caps, each scaled by net liq, so the same rules size a larger account larger:

- **per-trade risk** -- what one unit can lose: a spread's max loss; a short put's STRESS loss, the
  name's own 1st-percentile move over the holding period at expiry, less the credit;
- **notional per name** -- a short put's strike x 100 against a share of net liq that depends on the
  account (a cash account or IRA secures it in full; margin does not, which is why leverage is held
  by notional, not buying power);
- **buying power** -- used after the trade under the VIX-scaled cap the portfolio reading states;
- **delta** -- the book's beta-weighted delta stays inside its band, or moves toward it;
- **theta** -- the book's theta stays under the band's ceiling;
- **concentration** -- the name stays under its share of the book's gross theta.

Buying power per unit follows the account kind the snapshot read: cash or an IRA secures a put at
its strike; Reg T margin takes the greater of 20% of the underlying less the out-of-the-money amount
plus the premium, and 10% of the strike plus the premium; portfolio margin is not modelled, and says
so. An unknown kind is sized as cash-secured, the cautious reading.

Every figure is modelled at mid, beside the working and natural prices; the credit floor alone is
tested at the working price. Nothing here places an order, and the evening reading is a day old
by the morning: re-quote before sending.
"""

from __future__ import annotations

import math
import re
from datetime import date
from typing import Any

from cherrypick.core import legbars

from cherrypick.manage import portfolio as _portfolio
from cherrypick.manage import pricing

MULTIPLIER = pricing.MULTIPLIER


def liquid(legs: list[tuple[dict, int]], bars: dict) -> bool:
    """One option by the reading's own verdict; a spread as one order (`cherrypick.core.legbars`):
    its cost to trade against its credit, and each leg's open interest -- never leg by leg, where the
    cheap long's wide-in-percent spread failed a third of spreads that cost under 10% to trade."""
    if len(legs) == 1:
        return bool(legs[0][0].get("passes"))
    return legbars.structure_passes(legs, bars)


def _f(value: Any) -> float | None:
    return _portfolio._f(value)


# --------------------------------------------------------------------------- one name's chain
def priced_options(entry: dict[str, Any], session: date, rate: float) -> list[dict[str, Any]]:
    """The name's quoted options with mid, IV solved from it, and model greeks. An option without a
    two-sided quote, or whose mid no vol can price, is left out."""
    spot, dte = _f(entry.get("spot")), entry.get("dte")
    if not spot or not dte or dte <= 0:
        return []
    years = dte / 365.0
    out = []
    for o in entry.get("options") or []:
        bid, ask, k = _f(o.get("bid")), _f(o.get("ask")), _f(o.get("strike"))
        if (
            bid is None
            or ask is None
            or ask < bid
            or ask <= 0
            or k is None
            or o.get("right") not in ("C", "P")
        ):
            continue
        mid = (bid + ask) / 2.0
        if mid <= 0:
            continue
        iv = pricing.implied_vol(spot, k, years, mid, o["right"], rate)
        g = pricing.greeks(spot, k, years, iv, o["right"], rate) if iv else None
        if g is None:
            continue
        out.append({**o, "strike": k, "bid": bid, "ask": ask, "mid": mid, "iv": iv, **g})
    return out


def _nearest(options: list[dict], right: str, target: float, where=lambda o: True) -> dict | None:
    pool = [o for o in options if o["right"] == right and where(o)]
    return min(pool, key=lambda o: abs(abs(o["delta"]) - target)) if pool else None


# An order still able to fill. The broker's day list also holds the day's filled, cancelled, expired
# and rejected orders, which are either in the positions already or never will be.
WORKING = {"Received", "Routed", "In Flight", "Live", "Contingent", "Cancel Requested", "Replace Requested"}
_OCC_RIGHT = re.compile(r"\d{6}([CP])\d{8}\s*$")


def pending_short_calls(orders: list[dict[str, Any]] | None) -> dict[str, float]:
    """{underlying: call contracts being sold to open on a working order, not yet filled}."""
    out: dict[str, float] = {}
    for o in orders or []:
        if o.get("status") not in WORKING:
            continue
        und = str(o.get("underlying_symbol") or "").upper()
        for leg in o.get("legs") or []:
            m = _OCC_RIGHT.search(str(leg.get("symbol") or ""))
            if not m or m.group(1) != "C" or leg.get("action") != "Sell to Open":
                continue
            left = _f(leg.get("remaining_quantity"))
            qty = left if left is not None else _f(leg.get("quantity")) or 0.0
            if und and qty:
                out[und] = out.get(und, 0.0) + qty
    return out


def uncovered_shares(shares: float, short_calls: float) -> float:
    """Shares not already written against: each short call open on the name ties up 100."""
    return shares - 100 * short_calls


ALL_KINDS = frozenset({"short_put", "put_spread", "call_spread", "covered_call"})


def structures(
    entry: dict[str, Any],
    options: list[dict],
    cfg: dict[str, Any],
    shares: float,
    short_calls: float = 0.0,
    kinds: frozenset[str] = ALL_KINDS,
) -> list[dict]:
    """Every structure the rules build on this name, legs as (option, signed quantity per unit), of
    the `kinds` asked for. `short_calls` is the count of short call contracts already open on the
    name, every one of which takes 100 shares out of what a covered call may be written against."""
    spot = _f(entry.get("spot")) or 0.0
    out = []
    tol = float(cfg.get("delta_tolerance", 1.0))

    def near_enough(o: dict | None, target: float) -> dict | None:
        return o if o is not None and abs(abs(o["delta"]) - target) <= tol + 1e-9 else None

    sp = (
        near_enough(_nearest(options, "P", float(cfg["short_put_delta"])), float(cfg["short_put_delta"]))
        if "short_put" in kinds
        else None
    )
    if sp:
        out.append({"kind": "short_put", "legs": [(sp, -1)]})
    for right in ("P", "C"):
        if ("put_spread" if right == "P" else "call_spread") not in kinds:
            continue
        otm = [
            o
            for o in options
            if o["right"] == right and (o["strike"] <= spot if right == "P" else o["strike"] >= spot)
        ]
        if not otm:
            continue
        short = max(otm, key=lambda o: o["strike"]) if right == "P" else min(otm, key=lambda o: o["strike"])
        k_short = short["strike"]
        beyond = (
            (lambda o, k=k_short: o["strike"] < k) if right == "P" else (lambda o, k=k_short: o["strike"] > k)
        )
        long_ = _nearest(options, right, float(cfg["spread_long_delta"]), beyond)
        if long_:
            out.append(
                {"kind": "put_spread" if right == "P" else "call_spread", "legs": [(short, -1), (long_, 1)]}
            )
    free = uncovered_shares(shares, short_calls)
    if free >= 100 and "covered_call" in kinds:
        cc = near_enough(
            _nearest(options, "C", float(cfg["covered_call_delta"]), lambda o: o["strike"] > spot),
            float(cfg["covered_call_delta"]),
        )
        if cc:
            out.append({"kind": "covered_call", "legs": [(cc, -1)], "max_units": int(free // 100)})
    return out


def fitted_spread(
    options: list[dict], short: dict, max_loss_cap: float, min_credit_share: float, was: str
) -> dict | None:
    """The nearest version that fits: a credit spread on `short`'s strike whose long is the WIDEST
    listed strike beyond it that keeps one unit's max loss under `max_loss_cap` and the credit at
    least `min_credit_share` of the width. A short put or a spread the rule built too wide for the
    account becomes this; None when no listed long fits."""
    right = short["right"]
    beyond = [
        o
        for o in options
        if o["right"] == right
        and (o["strike"] < short["strike"] if right == "P" else o["strike"] > short["strike"])
    ]
    best = None
    for o in sorted(beyond, key=lambda o: abs(o["strike"] - short["strike"])):
        width = abs(o["strike"] - short["strike"])
        credit = short["mid"] - o["mid"]
        working = credit - ((short["ask"] - short["bid"]) + (o["ask"] - o["bid"])) / 4.0
        if working < min_credit_share * width or (width - credit) * MULTIPLIER > max_loss_cap:
            continue
        best = o
    if best is None:
        return None
    return {
        "kind": "put_spread" if right == "P" else "call_spread",
        "legs": [(short, -1), (best, 1)],
        "alternative_to": was,
    }


# --------------------------------------------------------------------------- one unit
def unit(
    s: dict[str, Any], entry: dict[str, Any], account_kind: str, scale: float | None
) -> dict[str, Any] | None:
    """Per-unit economics of a structure, or None when it fails its own rule (a spread under the
    minimum credit). Signed cash: a credit received is +."""
    legs = s["legs"]
    spot = _f(entry.get("spot")) or 0.0
    credit = -sum(q * o["mid"] for o, q in legs)  # per share
    natural = -sum(q * (o["ask"] if q > 0 else o["bid"]) for o, q in legs)
    # The price a patient order is modelled to get (`cherrypick.core.legbars`): halfway from the mid
    # to the natural fill. The credit floor is tested there, never at the mid alone.
    working = (credit + natural) / 2.0
    stress = entry.get("stress") or {}
    kind = s["kind"]
    if kind in ("put_spread", "call_spread"):
        width = abs(legs[0][0]["strike"] - legs[1][0]["strike"])
        max_loss = (width - credit) * MULTIPLIER
        bp = max_loss
        stress_loss = max_loss  # defined: the worst case is the stress case
        notional = max_loss
    elif kind == "short_put":
        k = legs[0][0]["strike"]
        width = None
        max_loss = (k - credit) * MULTIPLIER
        notional = k * MULTIPLIER
        if account_kind == "margin":
            otm = max(spot - k, 0.0)
            bp = max(0.20 * spot - otm + credit, 0.10 * k + credit) * MULTIPLIER
        elif account_kind == "portfolio_margin":
            bp = None
        else:
            bp = (k - credit) * MULTIPLIER
        p01 = _f(stress.get("p01"))
        stress_loss = (
            None if p01 is None else max(k - spot * (1.0 + p01), 0.0) * MULTIPLIER - credit * MULTIPLIER
        )
    else:  # covered call on held shares: the shares secure it; their risk is already in the book
        width = None
        max_loss = None
        notional = 0.0
        bp = 0.0
        stress_loss = 0.0
    if kind in ("put_spread", "call_spread") and width and working < s.get("min_credit_share", 0.0) * width:
        return None
    theta = sum(q * MULTIPLIER * o["theta"] for o, q in legs)
    delta = sum(q * MULTIPLIER * o["delta"] for o, q in legs)
    gamma = sum(q * MULTIPLIER * o["gamma"] for o, q in legs)
    return {
        "credit": credit,
        "natural": natural,
        "working": working,
        "width": width,
        "max_loss": max_loss,
        "bp": bp,
        "stress_loss": stress_loss,
        "notional": notional,
        "theta": theta,
        "bwd": delta * scale if scale is not None else None,
        "gamma_bench": gamma * scale * scale if scale is not None else None,
    }


# --------------------------------------------------------------------------- sizing
def account_basis(account_type: dict[str, Any] | None) -> str:
    """'cash' (a cash account or IRA, and an unknown one), 'margin' or 'portfolio_margin'."""
    kind = str((account_type or {}).get("margin_or_cash") or "").lower()
    name = str((account_type or {}).get("type_name") or "").lower()
    if "ira" in name or kind in ("cash", ""):
        return "cash"
    if "portfolio" in kind or "portfolio" in name:
        return "portfolio_margin"
    return "margin"


# The caps that measure how full the book already is, not the trade: a candidate stopped by one of
# these is WAITING -- it fits once positions close and free the room.
ROOM_CAPS = frozenset({"buying power", "theta ceiling"})


def _bp_cap(book: dict[str, Any], cfg: dict[str, Any]) -> float:
    """The buying-power ceiling as a share of net liq: the portfolio reading's VIX-scaled band, else
    the configured fallback."""
    cap = ((book.get("bands") or {}).get("buying_power") or {}).get("high")
    return float(cfg["max_buying_power_pct"]) if cap is None else float(cap)


def _band_theta(reading: dict[str, Any]) -> float:
    """The theta the band reads -- without the legs about to expire -- for the book or one name's
    row; the whole figure on a reading written before the two were split."""
    held = reading.get("theta_ex_front")
    return float(held if held is not None else reading.get("theta") or 0.0)


def if_room(
    u: dict[str, Any],
    book: dict[str, Any],
    underlying: str,
    cfg: dict[str, Any],
    basis: str,
    max_units: int,
) -> dict[str, Any] | None:
    """A waiting candidate's size once the book has room (ROOM_CAPS set aside), with where taking it
    TODAY would leave the account: buying power used after it against the VIX-scaled cap, graded
    `ok` (under the cap), `over` (within `bp_warn_over` of it) or `far_over`, and the book's theta
    after it against the theta ceiling. None when even room would not size a unit."""
    units, binding = size(u, book, underlying, cfg, basis, max_units, room=True)
    net_liq = book.get("net_liq")
    if units < 1 or not net_liq:
        return None
    cap = _bp_cap(book, cfg)
    used = book.get("buying_power_used")
    bp_after = (used + units * u["bp"]) / net_liq if used is not None and u["bp"] is not None else None
    if bp_after is None:
        level = None
    elif bp_after <= cap:
        level = "ok"
    elif bp_after <= cap + float(cfg.get("bp_warn_over", 0.10)):
        level = "over"
    else:
        level = "far_over"
    theta_hi = ((book.get("bands") or {}).get("theta") or {}).get("high")
    theta_after = (_band_theta(book) + units * u["theta"]) / net_liq
    return {
        "units": units,
        "binding": binding,
        "bp_after_pct": None if bp_after is None else round(bp_after, 4),
        "bp_cap_pct": round(cap, 4),
        "bp_level": level,
        "theta_after_pct": round(theta_after, 5),
        "theta_ceiling_pct": theta_hi,
        "theta_over": theta_hi is not None and theta_after > theta_hi,
    }


def size(
    u: dict[str, Any],
    book: dict[str, Any],
    underlying: str,
    cfg: dict[str, Any],
    basis: str,
    max_units: int,
    room: bool = False,
) -> tuple[int, str | None]:
    """(units, binding cap): the largest count that clears every cap, and the cap the next unit
    breaks (None when `max_units` itself is what stops it). With `room`, the caps that measure how
    full the book already is (ROOM_CAPS: buying power, the theta ceiling) are set aside: the size the
    trade would take once the book has room for it."""
    net_liq = book.get("net_liq")
    if not net_liq:
        return 0, "no net liq"
    used = book.get("buying_power_used")
    bp_cap = _bp_cap(book, cfg)
    band = (book.get("bands") or {}).get("delta") or {}
    theta_hi = ((book.get("bands") or {}).get("theta") or {}).get("high")
    theta = _band_theta(book)
    bwd = book.get("bwd")
    row = next((x for x in book.get("underlyings") or [] if x["underlying"] == underlying), None)
    name_theta = _band_theta(row or {})
    # One name's share of the theta the account may CARRY (the band's ceiling in dollars), not of
    # what it carries now: against the book as it is, the first trade in an empty book is 100% of it.
    name_theta_cap = (
        float(cfg["max_name_theta_share"]) * (theta_hi if theta_hi is not None else 0.003) * net_liq
    )
    notional_pct = float(cfg["notional_per_name_pct"]["margin" if basis != "cash" else "cash"])

    def breaks(n: int) -> str | None:
        if u["stress_loss"] is None:
            return "no stress reading"
        if n * u["stress_loss"] > float(cfg["per_trade_risk_pct"]) * net_liq:
            return "per-trade risk"
        if n * u["notional"] > notional_pct * net_liq:
            return "notional per name"
        if not room and u["bp"] is not None and used is not None and used + n * u["bp"] > bp_cap * net_liq:
            return "buying power"
        if (
            bwd is not None
            and u["bwd"] is not None
            and band.get("low") is not None
            and band.get("high") is not None
        ):
            after = bwd + n * u["bwd"]
            lo, hi = band["low"], band["high"]
            inside_after = lo <= after <= hi
            closer = abs(after - min(max(after, lo), hi)) < abs(bwd - min(max(bwd, lo), hi))
            if not inside_after and not closer:
                return "delta band"
        if not room and theta_hi is not None and (theta + n * u["theta"]) / net_liq > theta_hi:
            return "theta ceiling"
        if n * u["theta"] > 0 and name_theta + n * u["theta"] > name_theta_cap:
            return "concentration"
        return None

    n = 0
    while n < max_units:
        reason = breaks(n + 1)
        if reason:
            return n, reason
        n += 1
    return n, None


def _describe(
    s: dict[str, Any],
    u: dict[str, Any],
    entry: dict[str, Any],
    underlying: str,
    units: int,
    binding: str | None,
) -> dict[str, Any]:
    return {
        "underlying": underlying,
        "kind": s["kind"],
        "alternative_to": s.get("alternative_to"),
        "expiry": entry.get("expiry"),
        "dte": entry.get("dte"),
        "spot": entry.get("spot"),
        "legs": [
            {
                "option": o.get("option"),
                "strike": o["strike"],
                "right": o["right"],
                "quantity": q,
                "bid": o["bid"],
                "ask": o["ask"],
                "mid": round(o["mid"], 4),
                "delta": round(o["delta"], 3),
                "iv": round(o["iv"], 4),
            }
            for o, q in s["legs"]
        ],
        "units": units,
        "binding": binding,
        "per_unit": {
            "credit": round(u["credit"], 2),
            "natural": round(u["natural"], 2),
            "working": round(u["working"], 2),
            "cash": round(u["credit"] * MULTIPLIER, 2),
            "max_loss": None if u["max_loss"] is None else round(u["max_loss"], 2),
            "buying_power": None if u["bp"] is None else round(u["bp"], 2),
            "stress_loss": None if u["stress_loss"] is None else round(u["stress_loss"], 2),
            "theta": round(u["theta"], 2),
            "bwd": None if u["bwd"] is None else round(u["bwd"], 2),
        },
        "theta_per_bp": round(u["theta"] / u["bp"], 5) if u["bp"] else None,
        "earnings_date": entry.get("earnings_date"),
        "earnings_unknown": entry.get("earnings_date") is None,
        "beta": entry.get("beta"),
    }


# --------------------------------------------------------------------------- the whole list
def views(entry: dict[str, Any]) -> list[tuple[dict[str, Any], frozenset[str]]]:
    """(the chain a structure is built on, the kinds built there). The covered call has its own
    expiry where the reading carries one (`cc`, from 2026-10-19), with its own options and stress
    move; a reading without it writes the call on the cycle monthly, as before."""
    cc = entry.get("cc")
    if not cc:
        return [(entry, ALL_KINDS)]
    cc_entry = {
        **entry,
        "expiry": cc.get("expiry"),
        "dte": cc.get("dte"),
        "stress": cc.get("stress"),
        "options": cc.get("options") or [],
    }
    return [(entry, ALL_KINDS - {"covered_call"}), (cc_entry, frozenset({"covered_call"}))]


def suggest(
    income: dict[str, Any] | None,
    book: dict[str, Any],
    trades: list[dict[str, Any]],
    session: date,
    rules: dict[str, Any],
    account_type: dict[str, Any] | None,
    benchmark_spot: float | None,
    orders: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    cfg = rules["portfolio"]["candidates"]
    if cfg.get("enabled") is False:
        return None
    if not income or not income.get("symbols"):
        return {
            "error": (income or {}).get("error") or "the snapshot carries no income reading",
            "fits": [],
            "too_big": [],
        }
    rate = float(rules["pricing"]["rate"])
    bars = {**legbars.LEG_BARS, **(income.get("bars") or {})}
    basis = account_basis(account_type)
    shares: dict[str, float] = {}
    short_calls: dict[str, float] = {}
    for t in trades:
        for leg in t.get("legs") or []:
            qty = float(leg.get("quantity") or 0.0)
            if leg.get("right") not in ("C", "P"):
                shares[t["underlying"]] = shares.get(t["underlying"], 0.0) + qty
            elif leg.get("right") == "C" and qty < 0:
                # Every short call, whatever its expiry or structure, ties up 100 shares.
                short_calls[t["underlying"]] = short_calls.get(t["underlying"], 0.0) - qty
    for und, qty in pending_short_calls(orders).items():
        short_calls[und] = short_calls.get(und, 0.0) + qty
    excluded: dict[str, int] = {}

    def drop(reason: str) -> None:
        excluded[reason] = excluded.get(reason, 0) + 1

    fits, too_big, waiting = [], [], []

    def _wait(row, u, sym, max_units) -> None:
        """A row sized to nothing by a cap on how full the book is: kept, with its size once there
        is room, for the waiting list."""
        if row["units"] >= 1 or row["binding"] not in ROOM_CAPS:
            return
        room = if_room(u, book, sym, cfg, basis, max_units)
        if room:
            row["if_room"] = room
            waiting.append(row)

    def _build(entry, options, kinds, sym, held, written, scale) -> None:
        for s in structures(entry, options, cfg, held, written, kinds):
            if not liquid(s["legs"], bars):
                drop("a leg is illiquid or unquoted")
                continue
            s["min_credit_share"] = float(cfg["spread_min_credit"])
            u = unit(s, entry, basis, scale)
            if u is None:
                drop("credit under a third of the width")
                continue
            max_units = int(s.get("max_units", cfg["max_units"]))
            units, binding = size(u, book, sym, cfg, basis, max_units)
            row = _describe(s, u, entry, sym, units, binding)
            if units >= 1:
                fits.append(row)
                continue
            _wait(row, u, sym, max_units)
            if s["kind"] in ("short_put", "put_spread", "call_spread"):
                # The nearest version that fits: the same short strike, the widest long under the
                # per-trade cap. Offered in the fits list itself when it sizes to a unit or more.
                cap = float(cfg["per_trade_risk_pct"]) * (book.get("net_liq") or 0.0)
                min_share = float(cfg["spread_min_credit"])
                alt = fitted_spread(options, s["legs"][0][0], cap, min_share, s["kind"])
                same = alt and s["kind"] != "short_put" and alt["legs"][1][0] is s["legs"][1][0]
                if alt and not same and liquid(alt["legs"], bars):
                    alt["min_credit_share"] = min_share
                    au = unit(alt, entry, basis, scale)
                    if au is not None:
                        a_units, a_binding = size(au, book, sym, cfg, basis, int(cfg["max_units"]))
                        alt_row = _describe(alt, au, entry, sym, a_units, a_binding)
                        row["alternative"] = alt_row
                        if a_units >= 1:
                            fits.append(alt_row)
                        elif "if_room" not in row:
                            # Only when the trade itself does not wait: one trade, listed once.
                            _wait(alt_row, au, sym, int(cfg["max_units"]))
            too_big.append(row)

    for sym, entry in sorted(income["symbols"].items()):
        if entry.get("error"):
            drop("no chain")
            continue
        spot, beta = _f(entry.get("spot")), _f(entry.get("beta"))
        scale = beta * spot / benchmark_spot if beta is not None and spot and benchmark_spot else None
        held, written = shares.get(sym, 0.0), short_calls.get(sym, 0.0)
        if held >= 100 and uncovered_shares(held, written) < 100:
            drop("shares already written against")
        for view, kinds in views(entry):
            exp, er = view.get("expiry"), view.get("earnings_date")
            if er and exp and session.isoformat() <= er <= exp:
                drop("earnings inside the trade")
                continue
            options = priced_options(view, session, rate)
            _build(view, options, kinds, sym, held, written, scale)

    def rank(r: dict) -> float:
        return r["theta_per_bp"] if r["theta_per_bp"] is not None else -math.inf

    fits.sort(key=rank, reverse=True)
    too_big.sort(key=rank, reverse=True)
    waiting.sort(key=rank, reverse=True)
    net_liq = book.get("net_liq")
    used = book.get("buying_power_used")
    return {
        "source_session": income.get("session"),
        "taken_at": income.get("taken_at"),
        "complete": income.get("complete"),
        "account_basis": basis,
        "names": len(income["symbols"]),
        "excluded": excluded,
        "fits": fits[: int(cfg["show"])],
        "fits_total": len(fits),
        "too_big": too_big[: int(cfg["show_too_big"])],
        "too_big_total": len(too_big),
        # Stopped only by how full the book is (ROOM_CAPS), each with its size once there is room.
        "waiting": waiting[: int(cfg["show"])],
        "waiting_total": len(waiting),
        "bp_used_pct": round(used / net_liq, 4) if used is not None and net_liq else None,
        "bp_cap_pct": round(_bp_cap(book, cfg), 4),
        "bp_warn_over": float(cfg.get("bp_warn_over", 0.10)),
        "error": None,
    }
