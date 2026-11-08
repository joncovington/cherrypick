"""The trade: pick the put, price the sale, settle it. Pure over the quotes it is handed.

Item 57's unhedged arm, as item 46's script computes it (`scripts/variance_premium_item46.py`):

- **The forward** is put-call parity at the strike where the call and put mids are closest.
- **The leg** is the put whose Black delta, on that forward at zero rate, is nearest 0.10, among the
  puts with a positive bid and an implied vol, up to 5% in the money. The puts nearest 8 and 12
  delta are recorded beside it and never traded.
- **The sale** is one contract at mid; slippage is `core.fees`' fraction of the quoted spread, its
  own cost column; trading fees are `core.fees.ic_open_fee` for one sold leg.
- **Settlement** is intrinsic at the expiry's settlement print. A cash-settled leg in the money pays
  the cash-settlement charge; an in-the-money ETF put delivers shares, booked as sold at the same
  print with the assignment's pass-throughs (`assignment_round_trip_fee`), which is how the study
  booked it. No share is carried past the expiry session, so a weekend gap after a real assignment
  is not in this number. Early assignment is measured on the marks, never modelled.

One refusal the study did not need: a pick whose 0.10 is not **bracketed** (no quoted put on one
side of it) is refused, because the stream window may simply not reach the true 10-delta strike,
and the nearest quoted put would then be a different trade. A study chain held every strike.

Money is signed cash flow, whole-position dollars: the credit is `+`, the settlement value `-`,
gross = credit + settlement value, net = gross - fees - settlement - slippage.
"""

from __future__ import annotations

from cherrypick.core import bs, fees

TARGET_DELTA = 0.10
NEIGHBOUR_DELTAS = (0.08, 0.12)
ITM_REACH = 0.05
MULTIPLIER = 100
CASH, PHYSICAL = "cash", "physical"


def mid(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None or ask <= 0 or bid < 0 or bid > ask:
        return None
    return (bid + ask) / 2.0


def forward(chain: list[tuple[float, str, float | None, float | None]]) -> float | None:
    """Parity at the strike where the call and put mids are closest. `chain` is (strike, right, bid,
    ask), right "C" or "P"."""
    mids: dict[float, dict[str, float]] = {}
    for k, right, b, a in chain:
        m = mid(b, a)
        if m is not None:
            mids.setdefault(k, {})[right] = m
    pairs = [(k, s["C"], s["P"]) for k, s in mids.items() if "C" in s and "P" in s]
    if not pairs:
        return None
    k, c, p = min(pairs, key=lambda kcp: (abs(kcp[1] - kcp[2]), kcp[0]))
    return k + c - p


def priced_puts(chain, fwd: float, years: float) -> list[dict]:
    """Every put a leg could use: a positive bid, a usable mid, an implied vol and a Black delta on
    the forward at zero rate; up to `ITM_REACH` over the forward."""
    out = []
    for k, right, b, a in chain:
        if right != "P" or k > fwd * (1 + ITM_REACH):
            continue
        m = mid(b, a)
        if m is None or not b or b <= 0:
            continue
        iv = bs.implied_vol(fwd, k, years, m, "P", 0.0)
        if iv is None or iv <= 0.0001:
            continue
        d = bs.delta(fwd, k, years, iv, "P", 0.0)
        if d is None:
            continue
        out.append({"strike": k, "bid": float(b), "ask": float(a), "mid": m, "iv": iv, "delta": d})
    return out


def nearest(puts: list[dict], target: float) -> dict | None:
    if not puts:
        return None
    return min(puts, key=lambda c: (abs(abs(c["delta"]) - target), c["strike"]))


def pick_put(chain, fwd: float, years: float, target: float = TARGET_DELTA) -> dict:
    """{"ok": True, "leg": put, "neighbours": {...}} or {"ok": False, "reason": ...}."""
    puts = priced_puts(chain, fwd, years)
    if not puts:
        return {"ok": False, "reason": "no_priced_put"}
    leg = nearest(puts, target)
    below = any(abs(c["delta"]) < target for c in puts)
    above = any(abs(c["delta"]) > target for c in puts)
    if not (below and above):
        return {"ok": False, "reason": "delta_not_bracketed", "nearest": leg}
    return {
        "ok": True,
        "leg": leg,
        "neighbours": {f"put{round(d * 100)}": nearest(puts, d) for d in NEIGHBOUR_DELTAS},
        "puts_priced": len(puts),
    }


def plan_entry(*, chain, years: float, fee_symbol: str, quantity: int = 1, costs: dict | None = None) -> dict:
    """The sale of one cycle's put on one name, or a refusal. `costs` is the config whose
    `tastytrade_costs` may override `core.fees`' defaults (none do by default)."""
    fwd = forward(chain)
    if fwd is None or fwd <= 0:
        return {"ok": False, "reason": "no_forward"}
    pick = pick_put(chain, fwd, years)
    if not pick["ok"]:
        return {**pick, "forward": fwd}
    leg = pick["leg"]
    q = int(quantity)
    premium = round(MULTIPLIER * q * leg["mid"], 4)
    slippage = fees.slippage_dollars(
        [{"bid": leg["bid"], "ask": leg["ask"]}], q, costs or {}, symbol=fee_symbol
    )
    fee = fees.ic_open_fee(fee_symbol, q, legs=1, sell_legs=1, ndigits=4)
    return {
        "ok": True,
        "forward": fwd,
        "years": years,
        "strike": leg["strike"],
        "bid": leg["bid"],
        "ask": leg["ask"],
        "mid": leg["mid"],
        "iv": leg["iv"],
        "delta": leg["delta"],
        "neighbours": pick["neighbours"],
        "puts_priced": pick["puts_priced"],
        "quantity": q,
        "entry_value": premium,
        "entry_fees": round(fee, 4),
        "entry_slippage": round(slippage, 4),
        "notional": round(MULTIPLIER * q * fwd, 2),
    }


def settlement_fee(style: str, strike: float, price: float, quantity: int) -> float:
    """What settling an in-the-money short put costs: the cash-settlement charge, or for an ETF the
    assignment and the delivered shares' sale at the print. Zero out of the money."""
    if bs.intrinsic(price, strike, "P") <= 0:
        return 0.0
    if style == CASH:
        return fees.ic_expire_fee(1)
    if style == PHYSICAL:
        return fees.assignment_round_trip_fee(
            MULTIPLIER * int(quantity), strike, price, direction="long", ndigits=4
        )
    raise ValueError(f"unknown settlement style {style!r}")


def settle(position: dict, price: float, style: str) -> dict:
    """The settled position's money at the expiry's print. `position` carries the entry columns."""
    q = int(position["quantity"])
    intrinsic = bs.intrinsic(price, float(position["strike"]), "P")
    exit_value = round(-MULTIPLIER * q * intrinsic, 4)
    settle_cost = round(settlement_fee(style, float(position["strike"]), price, q), 4)
    gross = round(float(position["entry_value"]) + exit_value, 4)
    fee = float(position["entry_fees"] or 0.0)
    slip = float(position["entry_slippage"] or 0.0)
    net = round(gross - fee - settle_cost - slip, 4)
    notional = float(position["notional"])
    if intrinsic <= 0:
        how = "expired"
    else:
        how = "settled" if style == CASH else "assigned"
    return {
        "exit_value": exit_value,
        "settlement": settle_cost,
        "gross_pnl": gross,
        "fees": round(fee, 4),
        "slippage": round(slip, 4),
        "net_pnl": net,
        "net_bp": round(net / notional * 1e4, 4) if notional else None,
        "exit_reason": how,
    }


def extrinsic(mark_mid: float | None, spot: float | None, strike: float) -> float | None:
    """A mark's time value: what an early exercise would throw away. Near zero on an in-the-money
    put is when assignment before expiry becomes likely."""
    if mark_mid is None or spot is None:
        return None
    return mark_mid - bs.intrinsic(spot, strike, "P")


def mark_value(mark_mid: float | None, quantity: int) -> float | None:
    """The open short's value as a signed cash flow to close it at mid."""
    return None if mark_mid is None else round(-MULTIPLIER * int(quantity) * mark_mid, 4)
