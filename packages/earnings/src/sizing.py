"""Code-enforced position sizing under a profile's risk cap.

Before this module, the `max_risk_per_trade_pct` gate (CLAUDE.md Step 4b) was
applied by the operator by hand at entry time -- reading the order's debit/credit,
the account NLV, and the cap, then eyeballing whether it fit. That is fine for a
one-off but fatal for a multi-week, multi-profile experiment: the gate has to be
identical and reproducible every night, independent of who (or what) is running
the loop. This module makes it code.

`compute_position_size(order, strategy_config, config)` returns how many contracts
a given profile may open for a given order -- or rejects it -- from three inputs:

  risk_budget      = capital * max_risk_per_trade_pct * risk_pct_multiplier
  per_contract_loss = strategy-specific max loss for one contract (see below)
  quantity          = floor(risk_budget / per_contract_loss), capped by
                      max_contracts_per_leg, rejected if < 1

Capital in paper mode is config's `available_capital_paper_mode` (never a real
broker balance). In live mode the caller passes the real NLV in via
config["_live_nlv"]; this module does not call the broker.

Per-contract max loss (strikes in points, one contract = 100 shares):

  iron_fly / iron_condor           (widest wing - credit) * 100
  directional_credit_spread        (|long - short| - credit) * 100
  atm_calendar / double_calendar   debit * 100
  reverse_fly                      max_loss field * 100
  broken_wing_butterfly            (far_width - near_width + net_debit) * 100

Every strategy is defined-risk (max loss known at entry from the order's own strikes/
debit) -- there is no undefined-risk/naked margin-proxy path. The BWB gap term is a
Phase-1 approximation (see docs/paper-trading-profiles.md), to be refined once real
paper fills accumulate.
"""

from math import floor

CONTRACT_MULTIPLIER = 100


def per_contract_max_loss(order: dict, config: dict) -> float | None:
    """Max loss for a single contract of `order`, in dollars, or None if it
    can't be computed from the order's fields. `order` is a strategy's
    get_order result dict (must include `strategy`)."""
    strategy = order.get("strategy")

    if strategy in ("iron_fly", "iron_condor"):
        credit = order.get("credit")
        if strategy == "iron_fly":
            short_c = short_p = order.get("short_strike")
        else:
            short_c = order.get("short_call_strike")
            short_p = order.get("short_put_strike")
        long_c = order.get("long_call_strike")
        long_p = order.get("long_put_strike")
        if None in (credit, short_c, short_p, long_c, long_p):
            return None
        widest_wing = max(long_c - short_c, short_p - long_p)
        return (widest_wing - credit) * CONTRACT_MULTIPLIER

    if strategy == "directional_credit_spread":
        credit = order.get("credit")
        short_s = order.get("short_strike")
        long_s = order.get("long_strike")
        if None in (credit, short_s, long_s):
            return None
        return (abs(long_s - short_s) - credit) * CONTRACT_MULTIPLIER

    if strategy in ("atm_calendar", "double_calendar"):
        debit = order.get("debit")
        if debit is None:
            return None
        return debit * CONTRACT_MULTIPLIER

    if strategy == "reverse_fly":
        max_loss = order.get("max_loss")
        if max_loss is None:
            return None
        return max_loss * CONTRACT_MULTIPLIER

    if strategy == "broken_wing_butterfly":
        near_w = order.get("near_width")
        far_w = order.get("far_width")
        net_debit = order.get("net_debit", 0.0)
        if None in (near_w, far_w):
            return None
        # Broken-wing gap is the structural risk; a net debit adds to it, a
        # net credit (negative net_debit) offsets it.
        return max((far_w - near_w) + net_debit, 0.0) * CONTRACT_MULTIPLIER

    return None


def _capital_basis(config: dict) -> float:
    """Simulated paper capital, or the real NLV the caller injected for live."""
    if config.get("enable_live_trading", False):
        return float(config.get("_live_nlv", 0.0))
    return float(config.get("available_capital_paper_mode", 0.0))


def compute_position_size(order: dict, strategy_config: dict, config: dict) -> dict:
    """Contracts a profile may open for `order` under its risk cap.

    Returns {ok, quantity, per_contract_loss, risk_budget, capital_at_risk,
    capital_basis, reason}. `ok` False with reason "risk_cap_exceeded" when even
    one contract's max loss exceeds the risk budget, or "max_loss_unverified"
    when per-contract loss can't be computed from the order.
    """
    capital = _capital_basis(config)
    base_pct = strategy_config.get("max_risk_per_trade_pct")
    multiplier = config.get("risk_pct_multiplier", 1.0)
    if base_pct is None:
        return {"ok": False, "reason": "max_risk_per_trade_pct_missing"}
    risk_budget = capital * base_pct * multiplier

    per_contract = per_contract_max_loss(order, config)
    if per_contract is None:
        return {"ok": False, "reason": "max_loss_unverified"}
    if per_contract <= 0:
        return {"ok": False, "reason": "non_positive_max_loss"}

    max_by_risk = floor(risk_budget / per_contract)
    hard_cap = int(config.get("max_contracts_per_leg", 1))
    quantity = min(max_by_risk, hard_cap)

    if quantity < 1:
        return {
            "ok": False,
            "reason": "risk_cap_exceeded",
            "quantity": 0,
            "per_contract_loss": round(per_contract, 2),
            "risk_budget": round(risk_budget, 2),
            "capital_basis": round(capital, 2),
        }

    return {
        "ok": True,
        "quantity": quantity,
        "per_contract_loss": round(per_contract, 2),
        "risk_budget": round(risk_budget, 2),
        "capital_at_risk": round(per_contract * quantity, 2),
        "capital_basis": round(capital, 2),
        "reason": None,
    }
