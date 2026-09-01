"""cherrypick.core.fees — tastytrade cost model (one home for the fee schedule).

Three related pieces, the first two of which the suite previously kept in two places:

1. **Cost-adjusted paper fills** (originally from EarningsAgent's `costs.py`): tastytrade's open-only
   commission ($1/contract open, $0 close, $10/leg cap) + clearing/regulatory pass-throughs + a slippage
   haircut off each leg's bid-ask width (recalibrated 2026-07-16 to 12.5% of spread, capped at 15% of
   leg mid so deep-OTM "junk" wings don't dominate -- see `_slippage`). Used to keep paper P&L honest.

2. **The IC open-fee schedule** (behind MEICAgent's hardcoded `fee_estimate_fallback_per_contract`
   constants): the same tastytrade schedule plus the per-symbol *broad-based index exchange fee* that
   makes SPX materially pricier per IC than XSP. `ic_open_fee` computes those constants from the
   schedule (SPX→6.89, XSP/DEFAULT→4.49, NDX→5.49, RUT→5.21) instead of hand-maintaining them.

3. **The share side** (added for calendars' move to SPY): what an AMERICAN-style option costs once it
   finishes ITM and delivers stock rather than cash. A cash-settled symbol never reaches this, which is
   why the suite had no equity pass-throughs at all until a module needed to hold delivered shares.

Source: tastytrade.com/pricing + the Commissions & Fees doc (rates change — re-check and update here).
Pure functions; no broker, no I/O.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- 1. cost-adjusted fills
DEFAULT_COSTS = {
    "commission_open_per_contract": 1.00,
    "commission_close_per_contract": 0.00,
    "commission_cap_per_leg": 10.00,
    "clearing_fee_per_contract": 0.10,
    "regulatory_fee_per_contract": 0.04,
    # Slippage: concede this fraction of each leg's bid-ask from mid, per fill. 0.125 of the full spread
    # = a quarter of the way from mid to the far touch, a realistic worked-combo-limit fill (recalibrated
    # 2026-07-16 from 0.25, a market-order assumption that made slippage ~98% of earnings paper cost).
    # Slippage-literature check 2026-07-17: this sits at the OPTIMISTIC edge of the practitioner
    # backtest band (25-50% of the way to the touch, i.e. frac 0.125-0.25) and below the ~half-of-posted
    # effective spread empirically measured on price-improved equity marketable orders -- defensible for
    # liquid names worked as combo limits (what the scanner's liquidity gates screen toward), optimistic
    # for the illiquid tail. Left at 0.125 deliberately; raise toward 0.15-0.1875 to be more conservative.
    "slippage_frac_of_spread": 0.125,
    # Guardrail: never charge a leg more slippage than this fraction of its mid. A bid>=0 quote always
    # has spread <= 2*mid, so at frac 0.125 this binds only when spread > 1.2*mid -- i.e. deep-OTM wings
    # quoted wide relative to their value, which would otherwise contribute outsized slippage. This cap
    # has empirical grounding: effective-spread studies find realized cost is CONCAVE in quoted width --
    # only ~10-22% of a spread *widening* passes through to the effective spread -- so a flat fraction of
    # a wide junk-wing spread would overstate the fill cost, which is exactly what the cap prevents.
    "slippage_cap_frac_of_mid": 0.15,
}


def _costs_config(config: dict) -> dict:
    return {**DEFAULT_COSTS, **config.get("tastytrade_costs", {})}


def _leg_quantities(order: dict, quantity: int) -> list[int]:
    """Total contracts traded per price level: the leg's own structure ratio (e.g. a broken-wing
    butterfly's x2 body) times the position quantity. A leg without a quantity field is ratio 1,
    so flat 1-1-1-1 structures are unchanged. Commissions, pass-throughs, and slippage are all
    per CONTRACT, not per price level -- a 1-2-1 fly at quantity 1 trades 4 contracts, not 3."""
    legs = order.get("order", {}).get("legs", [])
    return [int(leg.get("quantity", 1) or 1) * quantity for leg in legs]


def _commission(leg_quantities: list[int], per_contract: float, cap_per_leg: float) -> float:
    """Open-only model: min(leg's contracts * per_contract, cap) per leg, summed. Passing
    commission_close_per_contract (0.00 by default) yields $0 to close with no special-casing."""
    return sum(min(leg_qty * per_contract, cap_per_leg) for leg_qty in leg_quantities)


def _pass_through(leg_quantities: list[int], clearing: float, regulatory: float) -> float:
    return sum(leg_quantities) * (clearing + regulatory)


def _slippage(
    leg_quotes: list[dict],
    leg_quantities: list[int],
    frac_of_spread: float,
    cap_frac_of_mid: float | None = None,
) -> float:
    """Per-leg slippage = frac_of_spread of that leg's bid-ask width, optionally capped at
    cap_frac_of_mid of the leg's mid; x100 x that leg's total contracts, summed across legs.

    Summing per-leg spreads is deliberate and correct: a multi-leg combo's net bid-ask exactly equals
    the sum of its legs' spreads (the mids net out, the spreads add), so this is identical to
    fractioning the net combo spread -- there is nothing to de-duplicate. A ratioed leg is quoted at
    one price level but crosses its spread once per contract, so its haircut scales with its own
    contract count. The cap is a realism guardrail for deep-OTM wings whose spread is large relative
    to their value."""
    total = 0.0
    for q, leg_qty in zip(leg_quotes, leg_quantities, strict=True):
        bid = q.get("bid", 0.0)
        ask = q.get("ask", 0.0)
        slip = max(ask - bid, 0.0) * frac_of_spread
        if cap_frac_of_mid is not None:
            slip = min(slip, cap_frac_of_mid * max((bid + ask) / 2.0, 0.0))
        total += slip * leg_qty
    return total * 100


def _apply_costs(
    order: dict, leg_quotes: list[dict], quantity: int, config: dict, commission_key: str
) -> dict:
    costs_cfg = _costs_config(config)
    leg_qtys = _leg_quantities(order, quantity)
    commission = _commission(leg_qtys, costs_cfg[commission_key], costs_cfg["commission_cap_per_leg"])
    pass_through = _pass_through(
        leg_qtys, costs_cfg["clearing_fee_per_contract"], costs_cfg["regulatory_fee_per_contract"]
    )
    slippage = _slippage(
        leg_quotes, leg_qtys, costs_cfg["slippage_frac_of_spread"], costs_cfg.get("slippage_cap_frac_of_mid")
    )
    total = commission + pass_through + slippage
    return {
        "commission": round(commission, 2),
        "pass_through_fees": round(pass_through, 2),
        "slippage": round(slippage, 2),
        "total_cost": round(total, 2),
    }


def apply_entry_costs(order: dict, leg_quotes: list[dict], quantity: int, config: dict) -> dict:
    """Cost of opening `order` at `quantity` contracts, given `leg_quotes` (one {"bid","ask"} per leg,
    in order["order"]["legs"] order). Returns commission / pass_through_fees / slippage / total_cost."""
    return _apply_costs(order, leg_quotes, quantity, config, "commission_open_per_contract")


def apply_exit_costs(order: dict, leg_quotes: list[dict], quantity: int, config: dict) -> dict:
    """Cost of closing `order`. Same shape; commission uses commission_close_per_contract (0.00 by
    tastytrade's open-only default, but computed rather than hardcoded so a charge-to-close schedule
    would work)."""
    return _apply_costs(order, leg_quotes, quantity, config, "commission_close_per_contract")


# --------------------------------------------------------------------------- 2. IC open-fee schedule
COMMISSION_OPEN_PER_CONTRACT = 1.00  # tastytrade: $1/contract to open
CLEARING_FEE_PER_CONTRACT = 0.10
ORF_PER_CONTRACT = 0.02  # Options Regulatory Fee
TAF_PER_SELL_CONTRACT = 0.00329  # FINRA Trading Activity Fee — sell legs only

# Single-Listed Exchange Proprietary Index Options fee per contract (broad-based index options).
# XSP is $0.00 under 10 contracts/leg. Symbols not listed use 0.00 (plain equity/ETF options schedule).
INDEX_EXCHANGE_FEE_PER_CONTRACT = {"SPX": 0.60, "XSP": 0.00, "NDX": 0.25, "RUT": 0.18}


def _ic_fee(
    symbol: str, quantity: int, legs: int, sell_legs: int, *, commission_per_contract: float, ndigits: int
) -> float:
    """Shared IC fee stack: (commission + clearing + ORF + per-symbol index exchange fee) per leg
    per contract, plus FINRA TAF on the sell legs. `commission_per_contract` is the only difference
    between opening ($1) and closing/expiring ($0). `ndigits` sets the rounding precision."""
    exch = INDEX_EXCHANGE_FEE_PER_CONTRACT.get(symbol.upper(), 0.0)
    per_contract = commission_per_contract + CLEARING_FEE_PER_CONTRACT + ORF_PER_CONTRACT + exch
    fee = legs * quantity * per_contract + sell_legs * quantity * TAF_PER_SELL_CONTRACT
    return round(fee, ndigits)


def ic_open_fee(
    symbol: str, quantity: int = 1, legs: int = 4, sell_legs: int = 2, *, ndigits: int = 2
) -> float:
    """Open-only fee for one iron condor (4 legs; 2 sells) at `quantity` contracts, per tastytrade's
    schedule including the per-symbol index exchange fee. Reproduces MEICAgent's
    `fee_estimate_fallback_per_contract` constants (SPX 6.89, XSP 4.49, NDX 5.49, RUT 5.21, else 4.49).
    `ndigits` chooses display precision: 2 (dollars-and-cents) by default; a caller wanting exact
    sub-cent parity (e.g. MEIC's paper engine) passes 4."""
    return _ic_fee(
        symbol,
        quantity,
        legs,
        sell_legs,
        commission_per_contract=COMMISSION_OPEN_PER_CONTRACT,
        ndigits=ndigits,
    )


def ic_close_fee(
    symbol: str, quantity: int = 1, legs: int = 4, sell_legs: int = 2, *, ndigits: int = 2
) -> float:
    """Fee to actively close IC legs — the same schedule MINUS the open-only $1/contract commission
    (clearing + ORF + per-symbol index exchange fee per leg, plus FINRA TAF on the sell legs).
    `legs`/`sell_legs` let a one-side close (2 legs, 1 sell) fee correctly vs a full 4-leg close
    (4 legs, 2 sells). `ndigits` as in `ic_open_fee`."""
    return _ic_fee(symbol, quantity, legs, sell_legs, commission_per_contract=0.0, ndigits=ndigits)


# $5 per SETTLEMENT EVENT, charged the next business day (not at expiry itself) on every distinct
# option symbol that finishes ITM at cash settlement and is therefore exercised/assigned -- OTM
# legs expire worthless and cost nothing.
#
# Per EVENT, not per contract: the broker settles one symbol as one transaction and charges it
# once, however many contracts rest on it. Corrected 2026-07-31 against real tastytrade
# transactions (the support article was previously read as per-contract, which these disprove):
#
#   XSP 260730P00744000  Cash Settled Assignment  qty 2  clearing_fees -5.00
#   XSP 260730P00745000  Cash Settled Exercise    qty 1  clearing_fees -5.00
#
# The 2-contract leg was charged $5.00, not $10.00 -- so a butterfly's doubled centre is ONE
# event, and quantity does not multiply the charge. (Both observed samples are 1-2 contracts;
# if the broker ever tiers at larger size this would under-model, which is what the per-symbol
# comparison in flies' `fee_reconcile` exists to catch.) The charge lands in `clearing_fees`.
ASSIGNMENT_FEE_PER_SETTLEMENT = 5.00


def ic_expire_fee(itm_legs: int = 0) -> float:
    """Cash-settlement cost: $0 for OTM legs (nothing to exercise), `ASSIGNMENT_FEE_PER_SETTLEMENT`
    for each of `itm_legs` -- DISTINCT ITM option symbols, not contracts (see the comment above).
    Defaults to 0 (all legs OTM / not yet known) so existing zero-arg callers are unaffected."""
    return round(itm_legs * ASSIGNMENT_FEE_PER_SETTLEMENT, 2)


def ic_open_fee_table(symbols=("SPX", "XSP", "NDX", "RUT")) -> dict:
    """{symbol: ic_open_fee(symbol)} plus a DEFAULT (equity/ETF, no index exchange fee)."""
    table = {s: ic_open_fee(s) for s in symbols}
    table["DEFAULT"] = ic_open_fee("__default__")  # unknown symbol -> 0.0 exchange fee
    return table


# --------------------------------------------------------------------------- 3. the SHARE side
# What a cash-settled symbol never reaches: an American-style option that finishes ITM delivers
# STOCK, and the stock then has to be disposed of. Two pass-throughs land on the disposal, both on
# SELLS only, and neither is on the option schedule above:
#
#   SEC Section 31 fee   $27.80 per $1,000,000 of principal, sells only. Re-rated annually by the
#                        SEC -- this is the FY2024 rate and it is the one line here most likely to
#                        be stale; it is a constant rather than a config key so that a suite-wide
#                        re-check updates every consumer at once.
#   FINRA TAF (equity)   $0.000166 per share sold, capped at $8.30 per trade. Note this is the
#                        SHARE rate, an order of magnitude below the per-contract option TAF above.
#
# tastytrade charges no commission on stock, so there is no open-side charge and no per-share
# commission on the close: the disposal cost is these two pass-throughs and nothing else.
SEC_FEE_PER_DOLLAR_SOLD = 27.80 / 1_000_000
EQUITY_TAF_PER_SHARE_SOLD = 0.000166
EQUITY_TAF_CAP_PER_TRADE = 8.30


def stock_trade_fee(shares: int, price: float, *, side: str, ndigits: int = 2) -> float:
    """Pass-through cost of one stock fill. Buys are free at tastytrade; sells carry the SEC fee on
    principal plus the per-share FINRA TAF, capped.

    `side` is the direction of THIS fill ("buy" or "sell"), not of the position it closes — a short
    share position is opened by a sell and closed by a buy, so the charge lands on the opening
    fill, and a long one is the other way round.
    """
    if side != "sell" or shares <= 0 or price <= 0:
        return 0.0
    sec = shares * price * SEC_FEE_PER_DOLLAR_SOLD
    taf = min(shares * EQUITY_TAF_PER_SHARE_SOLD, EQUITY_TAF_CAP_PER_TRADE)
    return round(sec + taf, ndigits)


def assignment_round_trip_fee(
    shares: int, assign_price: float, dispose_price: float, *, direction: str, ndigits: int = 2
) -> float:
    """Every pass-through one assignment costs, from delivery to disposal.

    `ASSIGNMENT_FEE_PER_SETTLEMENT` for the exercise/assignment event itself -- the same charge and
    the same per-event (not per-contract) rule as cash settlement, which is why it is the constant
    above and not a second one -- plus `stock_trade_fee` on whichever of the two share fills is a
    sell. A long delivery sells at disposal; a short delivery sold at assignment and buys back.
    """
    if direction == "long":
        share_side = stock_trade_fee(shares, dispose_price, side="sell", ndigits=4)
    else:
        share_side = stock_trade_fee(shares, assign_price, side="sell", ndigits=4)
    return round(ASSIGNMENT_FEE_PER_SETTLEMENT + share_side, ndigits)
