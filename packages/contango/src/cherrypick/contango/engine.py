"""The switch as fills: what selling one holding and buying the other costs. Pure functions over
quotes the caller already read.

**Fills are at mid, and the concession is a separate cost.** That is the suite's model for mid-filled
modules (root CLAUDE.md, "Slippage is its own column"): a marketable order pays half the quoted
spread, so `slippage` per share is that half-spread, floored at `slippage_floor_bps` of the mid --
a one-cent quote on a $50 fund is half a basis point, and nothing fills that well at size. The
replay this module was built from charged 2 bps a side; the floor is that number.

**Fees** are `cherrypick.core.fees.stock_trade_fee`: buys free, sells carry the SEC fee and FINRA
TAF. No commission -- tastytrade charges none on shares.

Whole shares only. What a buy cannot spend stays as cash earning nothing, and is shown, not hidden:
on $10,000 that is under one share's price.
"""

from __future__ import annotations

import math

from cherrypick.core import fees as _fees

ENGINE_DEFAULTS = {
    "slippage_floor_bps": 2.0,
    "max_spread_bps": 50.0,
}


def _p(params: dict | None) -> dict:
    return {**ENGINE_DEFAULTS, **{k: v for k, v in (params or {}).items() if k in ENGINE_DEFAULTS}}


def spread_bps(quote: dict) -> float | None:
    mid = quote.get("mid")
    if not mid or mid <= 0 or quote.get("bid") is None or quote.get("ask") is None:
        return None
    return (quote["ask"] - quote["bid"]) / mid * 10_000


def slippage_per_share(quote: dict, params: dict | None = None) -> float:
    p = _p(params)
    half = (quote["ask"] - quote["bid"]) / 2
    return max(half, quote["mid"] * p["slippage_floor_bps"] / 10_000)


def quote_refusal(symbol: str, quote: dict | None, params: dict | None = None) -> str | None:
    """Why `quote` cannot be traded on, or None. A spread wider than `max_spread_bps` is a market
    the switch should wait out (the next tick in the window may be better), not one to pay."""
    if quote is None:
        return f"no_quote_{symbol}"
    bps = spread_bps(quote)
    if bps is None:
        return f"unpriceable_{symbol}"
    if bps > _p(params)["max_spread_bps"]:
        return f"spread_too_wide_{symbol}"
    return None


def sell_fill(symbol: str, shares: int, quote: dict, params: dict | None = None) -> dict:
    slip = round(shares * slippage_per_share(quote, params), 2)
    fee = _fees.stock_trade_fee(shares, quote["mid"], side="sell")
    return {
        "symbol": symbol,
        "side": "sell",
        "shares": shares,
        "bid": quote["bid"],
        "ask": quote["ask"],
        "mid": quote["mid"],
        "value": round(shares * quote["mid"], 2),
        "slippage": slip,
        "fees": fee,
        "cash_delta": round(shares * quote["mid"] - slip - fee, 2),
    }


def buy_fill(symbol: str, cash: float, quote: dict, params: dict | None = None) -> dict | None:
    """The largest whole-share buy `cash` pays for, slippage included; None if not one share."""
    per_share = quote["mid"] + slippage_per_share(quote, params)
    shares = int(math.floor(cash / per_share + 1e-9))
    if shares <= 0:
        return None
    slip = round(shares * slippage_per_share(quote, params), 2)
    fee = _fees.stock_trade_fee(shares, quote["mid"], side="buy")
    return {
        "symbol": symbol,
        "side": "buy",
        "shares": shares,
        "bid": quote["bid"],
        "ask": quote["ask"],
        "mid": quote["mid"],
        "value": round(shares * quote["mid"], 2),
        "slippage": slip,
        "fees": fee,
        "cash_delta": round(-(shares * quote["mid"]) - slip - fee, 2),
    }


def plan_switch(
    *, cash: float, holding: dict | None, target_symbol: str, quotes: dict, params: dict | None = None
) -> dict:
    """Sell `holding` (`{"symbol", "shares"}`, or None) and buy `target_symbol` with everything.

    `{"ok": True, "sell": fill | None, "buy": fill, "cash_after"}` or a refusal. Both legs are
    checked before either is planned: a switch is one decision, and a sell whose buy then refuses
    would leave an arm in cash that its rule never chose.
    """
    if holding is not None and holding["symbol"] == target_symbol:
        return {"ok": False, "reason": "already_held"}
    legs = [target_symbol] + ([holding["symbol"]] if holding else [])
    for sym in legs:
        why = quote_refusal(sym, quotes.get(sym), params)
        if why:
            return {"ok": False, "reason": why}
    sell = (
        sell_fill(holding["symbol"], int(holding["shares"]), quotes[holding["symbol"]], params)
        if holding
        else None
    )
    available = round(cash + (sell["cash_delta"] if sell else 0.0), 2)
    buy = buy_fill(target_symbol, available, quotes[target_symbol], params)
    if buy is None:
        return {"ok": False, "reason": "insufficient_cash", "cash": available}
    return {"ok": True, "sell": sell, "buy": buy, "cash_after": round(available + buy["cash_delta"], 2)}


def stint_result(position: dict, sell: dict) -> dict:
    """The closed row's money for one holding stint, laid out so it adds up (root CLAUDE.md):
    entry (a debit, negative) + exit (a credit) + distributions = gross; gross - fees - slippage = net.
    """
    entry_value = -round(position["shares"] * position["entry_mid"], 2)
    exit_value = sell["value"]
    distributions = round(position.get("distributions") or 0.0, 2)
    gross = round(entry_value + exit_value + distributions, 2)
    fees = round((position.get("entry_fees") or 0.0) + sell["fees"], 2)
    slippage = round((position.get("entry_slippage") or 0.0) + sell["slippage"], 2)
    return {
        "entry_value": entry_value,
        "exit_value": exit_value,
        "distributions": distributions,
        "gross_pnl": gross,
        "fees": fees,
        "slippage": slippage,
        "net_pnl": round(gross - fees - slippage, 2),
    }


def nav(cash: float, holding: dict | None, mark: float | None) -> float | None:
    """Cash plus the holding at mid. None when a holding cannot be marked -- a NAV with a hole in it
    is not a NAV, and the caller records why."""
    if holding is None:
        return round(cash, 2)
    if mark is None:
        return None
    return round(cash + holding["shares"] * mark, 2)


def dividend_credits(
    positions: list[dict], dividends: list[dict], credited: set[tuple[str, str]]
) -> list[dict]:
    """Distributions owed: a stint holding `symbol` at the open of `ex_date` -- entered on an earlier
    session, and either still open or exited on or after the ex-date (the decision fills ten minutes
    before the close, so a stint sold ON the ex-date held through its open). `credited` is the
    `(position_id, ex_date)` pairs already paid, so a re-run pays nothing twice."""
    out = []
    for pos in positions:
        for div in dividends:
            if div["symbol"] != pos["symbol"]:
                continue
            ex = div["ex_date"]
            if not pos["entry_session"] < ex:
                continue
            if pos.get("exit_session") is not None and pos["exit_session"] < ex:
                continue
            if (pos["position_id"], ex) in credited:
                continue
            out.append(
                {
                    "position_id": pos["position_id"],
                    "arm": pos["arm"],
                    "symbol": pos["symbol"],
                    "ex_date": ex,
                    "per_share": div["amount"],
                    "shares": pos["shares"],
                    "amount": round(div["amount"] * pos["shares"], 2),
                }
            )
    return out
