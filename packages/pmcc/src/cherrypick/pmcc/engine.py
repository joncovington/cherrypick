"""Pure decisions over a pre-fetched snapshot: leg selection, worksheet math, and the fee stack.

No I/O, no clock reads, no network — the same split every module in the suite keeps (provider
fetches, engine decides, arm persists, paper_loop owns the clock), which is both what makes the
strategy testable and the suite guardrail on loop-decision paths.

The structure: one deep-ITM long call (85-90 delta, ~21 DTE — a stock substitute, NOT a LEAP)
against one ATM short call (~7 DTE). The long is selected to fall inside a DELTA BAND rather than
past a floor (2026-08-23 redesign: the 99-delta/yield-targeted design gave a near-zero-extrinsic
long and a yield-chased ITM short; the replacement trades a little more long extrinsic for real
leverage and prices the short at the market's own nearest-the-money strike, whichever side of spot
that lands). The short's strike is simply the one nearest spot — no yield floor — so it can land OTM
as easily as ITM depending on where the chain quotes; the worksheet math below is written to handle
either.
"""

from __future__ import annotations

from cherrypick.core import advice as _core_advice
from cherrypick.core import config as _cfg
from cherrypick.core import fees as _fees
from cherrypick.core import settlement as _settlement

ARMS = ("control", "shield", "shield_hold")

# What makes each arm itself, in code (2026-10-04). Merged between `defaults` and the arm's own
# config block, so the config tunes an arm but a thin or missing block cannot turn a held-long arm
# into a copy of control -- which `defaults` alone would make it, with a P&L that still looks
# plausible. Open positions are managed through the same merge (`management.effective_params`), so
# an arm dropped from the config still manages what it holds by its own rules. The values are the
# shield study's (docs/shield-study.md): a year-long long chosen by delta alone (no extrinsic
# fallback) in 0.88-0.92, nearest 0.90 -- Tom King's own "90ish" (0.90-0.95, nearest 0.925, until the
# 2026-10-06 boundary) -- a 0.70-delta weekly short, his 30% stop; `shield` alone rolls early (at 85%
# of the short's extrinsic decayed, or on a breach). The delta is the broker's, as the live loop will
# see it: on American-style ETFs its deep-call delta runs below parity, so 0.90 sits ~36-41% ITM.
_HELD_LONG_RULES = {
    "lifecycle": "held_long",
    "short_rule": "delta",
    "long_delta_min": 0.88,
    "long_delta_max": 0.92,
    "allow_extrinsic_fallback": False,
    "stop_loss_frac": 0.30,
}
ARM_RULES = {
    "control": {},
    "shield": {**_HELD_LONG_RULES, "early_roll_decay": 0.85, "breach_roll": True},
    "shield_hold": {**_HELD_LONG_RULES, "early_roll_decay": None, "breach_roll": False},
}
# Whether an arm the config does not declare may enter. Only control: an arm joins a suite because
# its config says so, never because the code grew one (each new arm widens the stream request).
DEFAULT_ENABLED = {"control": True}

# How an expiring leg settles, per underlying. The module models both styles and refuses a symbol it
# has been told nothing about — the calendars guard, kept verbatim: an unmodelled settlement produces
# bookkeeping that is wrong at its first Friday, and wrong quietly. TQQQ is American physical
# delivery; XSP (added 2026-08-23) is European, cash-settled — `cash` exists so the shared
# settlement math stays the calendars decomposition with the share term zeroed, not a second model.
# The two symbols are NOT interchangeable substitutes for each other: different settlement
# mechanics, different early-assignment risk profile (XSP has none), different fee schedule (XSP is
# a broad-based index option; TQQQ is an ETF) — see `assignment_from`, `management.assignment_exposed`,
# and `entry_cost`/`close_cost` for where each of those differences is actually applied.
SETTLEMENT_STYLES = ("cash", "physical")


def settlement_style(config: dict, symbol: str) -> str | None:
    """How `symbol` settles, or None if nothing declares it — which is a refusal, not a default."""
    declared = config.get("settlement_style")
    if isinstance(declared, dict) and declared:
        style = declared.get(symbol.upper())
        return style if style in SETTLEMENT_STYLES else None
    return None


# --------------------------------------------------------------------------- the dividend calendar
#
# A short ITM call on a physical underlying is really assigned at the close BEFORE the ex-date — and
# this module sells ITM calls BY DESIGN, so the ex-div question is central, not an edge case. It is
# answered the calendars way (user decision 2026-08-16): entries whose short leg spans a declared
# ex-date are REFUSED, never modelled. The dates are DECLARED config data from each issuer's own
# distribution schedule, refreshed by hand — leveraged-ETF distributions are irregular and cannot be
# computed, and nothing on a loop-decision path may touch the network. A missing table and "no
# dividend in that span" must not look alike, so coverage is explicit: a symbol with no dividends
# block is never covered, and a span past `declared_through` is refused rather than assumed clean.


def ex_dividend_dates(config: dict, symbol: str) -> list[str]:
    """The declared ex-dates for `symbol`, else empty."""
    block = (config.get("dividends") or {}).get(symbol.upper()) or {}
    return [str(d) for d in (block.get("ex_dates") or [])]


def dividend_coverage_ok(config: dict, symbol: str, through_day: str) -> bool:
    """Whether the declared calendar can answer questions up to `through_day` (ISO date). No block,
    or a horizon short of the day, is not-covered — never "probably no dividend"."""
    block = (config.get("dividends") or {}).get(symbol.upper()) or {}
    declared_through = block.get("declared_through")
    return isinstance(declared_through, str) and str(through_day) <= declared_through


def ex_date_in_span(config: dict, symbol: str, start_day: str, end_day: str) -> str | None:
    """The first declared ex-date inside the CLOSED span [start_day, end_day], or None. The span is
    the short leg's whole life — entry through its expiration — because that is the window in which
    the short can be standing ITM across an ex-date."""
    for d in sorted(ex_dividend_dates(config, symbol)):
        if str(start_day) <= d <= str(end_day):
            return d
    return None


def base_book(arm: str, *, config: dict | None = None, decision: dict | None = None) -> str:
    """The arm whose rules an advised twin runs under. A base arm is its own base.

    **An advised tag no longer carries its base (2026-09-17).** Each advisor experiment gets its
    own arm, `advised:<experiment name>`, and the base it shadows is named by the session
    decision's experiment entry rather than by the tag. Resolution, in order: the decision's entry
    for this tag when one is given; the tag's last segment when it names a base arm -- the legacy
    `advised:control` every row before this date carries (and the retired `advised:keltner` /
    `advised:roll` history, through the config's `arms` keys); else the configured
    `advice.base_book` (default `control`)."""
    if not _core_advice.is_advised(arm):
        return arm
    for entry in _core_advice.advised_books(decision):
        tag = entry.get("tag")
        if tag and (arm == tag or arm.startswith(tag + ":")) and entry.get("base"):
            return str(entry["base"])
    tail = arm.split(":", 1)[1]
    if tail in ARMS or tail in _cfg.registry(config, label="pmcc"):
        return tail
    # Any accepted spelling: an operator who renames this key must not silently get
    # "control" as the base, which is a wrong A/B that still reads as a valid one.
    advice_cfg = (config or {}).get("advice") or {}
    return str(_cfg.first_present(advice_cfg, *_cfg.BASE_ARM_KEYS) or "control")


def merged_params(config: dict, arm: str) -> dict:
    """`defaults`, then the arm's own rules (`ARM_RULES`), then its config block — the flies
    `merged_params` shape, so an advised arm resolves through the same path as every other."""
    params = {
        **(config.get("defaults") or {}),
        **ARM_RULES.get(arm, {}),
        **(_cfg.registry(config, label="pmcc").get(arm) or {}),
    }
    params["arm"] = arm
    return params


# --------------------------------------------------------------------------- leg selection
def _quoted_calls(entries: list[dict], quotes: dict) -> list[dict]:
    """Call entries with a usable quote, quote attached, sorted by strike ascending."""
    out = []
    for e in entries:
        if e["option_type"] != "call":
            continue
        quote = quotes.get(e["streamer_symbol"])
        if quote is None:
            continue
        out.append({**e, "quote": quote})
    return sorted(out, key=lambda e: e["strike_price"])


def select_long(entries: list[dict], quotes: dict, greeks: dict, spot: float, params: dict) -> dict:
    """The deep-ITM long call: the candidate whose delta falls inside
    `[long_delta_min, long_delta_max]` (default 0.85-0.90) — a delta BAND, not a floor
    (2026-08-23 redesign). Among candidates inside the band the one nearest the band's midpoint
    wins; a tie prefers the higher strike (marginally less capital).

    Greeks are refused-when-stale upstream and genuinely absent for deep strikes on a cold window,
    so the band check DEGRADES rather than blocks ONLY for a candidate with NO delta on file at
    all — that candidate is judged on `max_long_extrinsic` instead, and the selection records
    `selected_by` ("delta" or "extrinsic") so degraded entries stay excludable later (the flies
    `center_reason` lesson). A candidate whose delta IS known but falls outside the band is simply
    excluded — a known delta outside the band is a real disqualification, not something the
    no-delta fallback should launder through. The fallback's own heuristic — highest strike whose
    extrinsic is at most `max_long_extrinsic` per share, i.e. the deepest-still-qualifying candidate
    by the extrinsic bound — is a deliberate choice documented here rather than in config: it is the
    closest available proxy to "still deep enough to behave like stock" when the feed cannot say
    where in the band a strike actually sits.

    The fallback itself is config-gated: `allow_extrinsic_fallback` (default True) must be True
    for a no-delta candidate to be admitted at all. Set False to require a real delta on every
    candidate — a strike with no delta on file is then skipped rather than admitted, useful for
    isolating whether the extrinsic-only fallback is itself shaping results.
    """
    max_extrinsic = params.get("max_long_extrinsic", 0.15)
    delta_min = params.get("long_delta_min", 0.85)
    delta_max = params.get("long_delta_max", 0.90)
    delta_mid = (delta_min + delta_max) / 2.0
    allow_fallback = params.get("allow_extrinsic_fallback", True)
    best_delta = None
    best_extrinsic = None
    for e in _quoted_calls(entries, quotes):
        strike = e["strike_price"]
        if strike >= spot:
            continue
        mid = e["quote"]["mid"]
        extrinsic = mid - (spot - strike)
        delta = (greeks.get(e["streamer_symbol"]) or {}).get("delta")
        if delta is not None:
            if not (delta_min <= delta <= delta_max):
                continue
            candidate = {
                "entry": e,
                "strike": strike,
                "mid": mid,
                "extrinsic": round(extrinsic, 4),
                "delta": delta,
                "selected_by": "delta",
            }
            if (
                best_delta is None
                or abs(delta - delta_mid) < abs(best_delta["delta"] - delta_mid)
                or (
                    abs(delta - delta_mid) == abs(best_delta["delta"] - delta_mid)
                    and strike > best_delta["strike"]
                )
            ):
                best_delta = candidate
            continue
        if not allow_fallback:
            continue
        if extrinsic > max_extrinsic:
            continue
        candidate = {
            "entry": e,
            "strike": strike,
            "mid": mid,
            "extrinsic": round(extrinsic, 4),
            "delta": None,
            "selected_by": "extrinsic",
        }
        if best_extrinsic is None or strike > best_extrinsic["strike"]:
            best_extrinsic = candidate
    best = best_delta if best_delta is not None else best_extrinsic
    if best is None:
        return {"ok": False, "reason": "no_deep_itm_long"}
    return {"ok": True, **best}


def select_short(entries: list[dict], quotes: dict, spot: float, params: dict) -> dict:
    """The ATM short call: the strike nearest spot by absolute distance, whichever side of spot it
    falls on (2026-08-23 redesign — no yield floor, no ITM-only restriction). A tie prefers the
    ITM side (the strike below spot), matching the strategy's original downside-buffer intent when
    the market happens to straddle spot exactly."""
    best = None
    best_dist = None
    for e in _quoted_calls(entries, quotes):
        strike = e["strike_price"]
        mid = e["quote"]["mid"]
        dist = abs(strike - spot)
        if best is None or dist < best_dist or (dist == best_dist and strike < best["strike"]):
            best = {"entry": e, "strike": strike, "mid": mid}
            best_dist = dist
    if best is None:
        return {"ok": False, "reason": "no_short_candidate"}
    intrinsic = max(0.0, spot - best["strike"])
    tv = best["mid"] - intrinsic
    return {
        "ok": True,
        "entry": best["entry"],
        "strike": best["strike"],
        "mid": best["mid"],
        "intrinsic": round(intrinsic, 4),
        "tv": round(tv, 4),
    }


def select_short_by_delta(
    entries: list[dict],
    quotes: dict,
    greeks: dict,
    spot: float,
    params: dict,
    *,
    floor_strike: float | None = None,
) -> dict:
    """The held-long arm's weekly short: the call whose delta is nearest `short_delta_target`
    (default 0.70, Tom King's "about 70 delta") inside `[short_delta_min, short_delta_max]`
    (0.65-0.78), strictly above `floor_strike` (the long's strike -- a short below its own cover is
    a different, uncovered position). A tie takes the higher strike: nearer the money, more
    extrinsic for the same delta reading.

    No fallback: a strike with no delta on file is skipped, and none qualifying refuses
    `no_short_delta`. The short is the measured half of this structure, so it is never chosen on a
    guess at its delta."""
    target = params.get("short_delta_target", 0.70)
    lo = params.get("short_delta_min", 0.65)
    hi = params.get("short_delta_max", 0.78)
    best = None
    for e in _quoted_calls(entries, quotes):
        strike = e["strike_price"]
        if floor_strike is not None and strike <= floor_strike:
            continue
        delta = (greeks.get(e["streamer_symbol"]) or {}).get("delta")
        if delta is None or not lo <= delta <= hi:
            continue
        gap = abs(delta - target)
        if best is None or gap < best[0] or (gap == best[0] and strike > best[1]["strike_price"]):
            best = (gap, e, delta)
    if best is None:
        return {"ok": False, "reason": "no_short_delta"}
    _, e, delta = best
    mid = e["quote"]["mid"]
    intrinsic = max(0.0, spot - e["strike_price"])
    return {
        "ok": True,
        "entry": e,
        "strike": e["strike_price"],
        "mid": mid,
        "intrinsic": round(intrinsic, 4),
        "tv": round(mid - intrinsic, 4),
        "delta": delta,
    }


def _spread_pct(quote: dict) -> float | None:
    """A leg's bid/ask spread as a fraction of its mid, or None when it cannot be computed.

    None is "unknown", never "fine": a leg the cache cannot price is refused earlier by the
    selectors, so reaching here without a mid means the quote is malformed rather than absent.
    """
    mid = quote.get("mid")
    if mid in (None, 0) or quote.get("bid") is None or quote.get("ask") is None:
        return None
    return (quote["ask"] - quote["bid"]) / mid


def plan_entry(snapshot: dict, params: dict) -> dict:
    """The position off one snapshot: `{"ok": True, "plan": ...}` or a refusal.

    Refusal reasons are the entry-attempt vocabulary — each names the one thing that blocked, so
    the attempts table can say whether a skipped day was a feed problem, a listing problem, or a
    market problem (`yield_unreachable` carries the best yield the chain actually offered).
    """
    spot = snapshot["spot"]
    quotes = snapshot["quotes"]
    greeks = snapshot.get("greeks") or {}

    long_pick = select_long(snapshot["long_chain"], quotes, greeks, spot, params)
    if not long_pick["ok"]:
        return long_pick
    if params.get("short_rule", "atm") == "delta":
        short_pick = select_short_by_delta(
            snapshot["short_chain"], quotes, greeks, spot, params, floor_strike=long_pick["strike"]
        )
    else:
        short_pick = select_short(snapshot["short_chain"], quotes, spot, params)
    if not short_pick["ok"]:
        return short_pick

    # The entry-side spread check, added 2026-08-28. `max_leg_spread_pct` existed from the start and
    # was enforced ONLY in `management.execution_gate` -- on whether a mark may be ACTED on when
    # closing -- so nothing ever measured the spread being paid at entry. curve and bwb both hold
    # the same parameter at the same default and check it in their own `plan_entry`; this module
    # was the outlier, and its CLAUDE.md already described a gate the code did not have ("deep-ITM
    # spreads may trip max_leg_spread_pct ... the attempts table measures").
    #
    # Measured over all 8 entries before landing: seven sat at 0.018-0.072 -- including the deep-ITM
    # long legs the docs worried about -- and one sat at 0.293. So this refuses the anomaly and not
    # the strategy. The refused entry is the XSP advised:control fill of 2026-08-27, whose long_call
    # went in $9.95 wide against its control twin's $0.13, which is a 76x difference in execution
    # cost on the two arms of one A/B.
    max_leg_spread_pct = params.get("max_leg_spread_pct", 0.25)
    for role, pick in (("long_call", long_pick), ("short_call_1", short_pick)):
        pct = _spread_pct(pick["entry"]["quote"])
        if pct is not None and pct > max_leg_spread_pct:
            return {
                "ok": False,
                "reason": "spread_too_wide",
                "detail": {"leg": role, "spread_pct": round(pct, 4)},
            }

    metrics = worksheet_metrics(
        spot=spot,
        long_strike=long_pick["strike"],
        long_mid=long_pick["mid"],
        short_strike=short_pick["strike"],
        short_mid=short_pick["mid"],
        short_dte=snapshot["short_dte"],
    )
    long_greeks = greeks.get(long_pick["entry"]["streamer_symbol"]) or {}
    short_greeks = greeks.get(short_pick["entry"]["streamer_symbol"]) or {}
    return {
        "ok": True,
        "plan": {
            "symbol": snapshot["symbol"],
            "spot": spot,
            "short_expiration": snapshot["short_expiration"],
            "long_expiration": snapshot["long_expiration"],
            "short_dte": snapshot["short_dte"],
            "long_dte": snapshot["long_dte"],
            "long_selected_by": long_pick["selected_by"],
            **metrics,
            "legs": [
                _leg(
                    "long_call",
                    "Buy to Open",
                    long_pick["entry"],
                    long_pick["entry"]["quote"],
                    long_greeks,
                    snapshot["long_expiration"],
                ),
                _leg(
                    "short_call_1",
                    "Sell to Open",
                    short_pick["entry"],
                    short_pick["entry"]["quote"],
                    short_greeks,
                    snapshot["short_expiration"],
                ),
            ],
        },
    }


def _leg_too_wide(quote: dict, params: dict) -> bool:
    """A leg too wide to trade: wide in PERCENT and in MONEY, both -- the exit gate's rule
    (`core.spreadbook.exit_spread_blocks`), because a roll's buyback is an exit and a percentage
    alone refuses every penny-wide buyback of a short that has done its job."""
    pct = _spread_pct(quote)
    if pct is None:
        return False
    too_wide_abs = (quote["ask"] - quote["bid"]) > params.get("max_leg_spread_abs", 0.05)
    return pct > params.get("max_leg_spread_pct", 0.25) and too_wide_abs


def plan_short(snapshot: dict, params: dict, *, long_strike: float, buyback: dict | None = None) -> dict:
    """The next weekly short of a held-long position, off a roll snapshot (one short expiration's
    chain, quotes, greeks and spot): `{"ok": True, "leg", "net_credit", ...}` or a refusal.

    With `buyback` (the current short's quote) this is a ROLL -- one two-leg ticket buying the old
    short and selling the new one, `net_credit` being new mid minus buyback mid. Without it, a SALE
    into a position that holds no short. Either leg too wide refuses `spread_too_wide`, naming it."""
    spot = snapshot["spot"]
    pick = select_short_by_delta(
        snapshot["short_chain"],
        snapshot["quotes"],
        snapshot.get("greeks") or {},
        spot,
        params,
        floor_strike=long_strike,
    )
    if not pick["ok"]:
        return pick
    for role, quote in (("buyback", buyback), ("new_short", pick["entry"]["quote"])):
        if quote is not None and _leg_too_wide(quote, params):
            return {
                "ok": False,
                "reason": "spread_too_wide",
                "detail": {"leg": role, "spread_pct": round(_spread_pct(quote) or 0.0, 4)},
            }
    greeks = (snapshot.get("greeks") or {}).get(pick["entry"]["streamer_symbol"]) or {}
    leg = _leg(
        "short_call",
        "Sell to Open",
        pick["entry"],
        pick["entry"]["quote"],
        greeks,
        snapshot["short_expiration"],
    )
    buyback_mid = buyback["mid"] if buyback is not None else None
    return {
        "ok": True,
        "leg": leg,
        "strike": pick["strike"],
        "mid": pick["mid"],
        "intrinsic": pick["intrinsic"],
        "tv": pick["tv"],
        "delta": pick["delta"],
        "short_expiration": snapshot["short_expiration"],
        "short_dte": snapshot.get("short_dte"),
        "buyback_mid": buyback_mid,
        "net_credit": round(pick["mid"] - (buyback_mid or 0.0), 4),
    }


def _leg(role: str, action: str, entry: dict, quote: dict, greeks: dict, expiration: str) -> dict:
    return {
        "leg_role": role,
        "occ_symbol": entry["occ_symbol"],
        "streamer_symbol": entry["streamer_symbol"],
        "expiration": expiration,
        "strike": entry["strike_price"],
        "option_type": "call",
        "action": action,
        "bid": quote["bid"],
        "ask": quote["ask"],
        "mid": quote["mid"],
        "iv": greeks.get("iv"),
        "delta": greeks.get("delta"),
    }


def worksheet_metrics(
    *, spot: float, long_strike: float, long_mid: float, short_strike: float, short_mid: float, short_dte: int
) -> dict:
    """The user's worksheet, computed once and stored as MEASURES on the position (never only as
    buckets — a threshold can be re-cut later, a bucket cannot). All per share except nothing; the
    ledger scales by ×100×quantity.

    The short can now land on either side of spot (the 2026-08-23 ATM redesign dropped the
    ITM-only yield search), so `short_intrinsic` is floored at 0 rather than assumed positive: an
    OTM short has zero intrinsic and its whole mid is extrinsic/time value."""
    long_extrinsic = long_mid - (spot - long_strike)
    short_intrinsic = max(0.0, spot - short_strike)
    short_tv = short_mid - short_intrinsic
    net_tv = short_tv - long_extrinsic
    net_debit = long_mid - short_mid
    breakeven = long_strike + net_debit
    return {
        "long_strike": long_strike,
        "long_mid": round(long_mid, 4),
        "long_extrinsic": round(long_extrinsic, 4),
        "short_strike": short_strike,
        "short_mid": round(short_mid, 4),
        "total_premium": round(short_mid, 4),
        "short_intrinsic": round(short_intrinsic, 4),
        "short_tv": round(short_tv, 4),
        "net_tv": round(net_tv, 4),
        "net_debit": round(net_debit, 4),
        "profit_pct": round(net_tv / net_debit, 6) if net_debit > 0 else None,
        "weekly_yield_pct": (
            round((net_tv / net_debit) * (7.0 / max(short_dte, 1)), 6) if net_debit > 0 else None
        ),
        "downside_protection_pct": round((spot - short_strike) / spot, 6) if spot else None,
        "breakeven": round(breakeven, 4),
        "buffer_to_breakeven_pct": round((spot - breakeven) / spot, 6) if spot else None,
    }


# --------------------------------------------------------------------------- structure math
def position_value(leg_marks: dict) -> float | None:
    """The position's per-share value at current marks: what closing it would COLLECT at mid
    (long mid minus short mid). None on any missing leg — never zero, `not recorded` and
    `worthless` are different facts."""
    long_mid = short_mid = None
    for role, mark in leg_marks.items():
        if mark is None or mark.get("mid") is None:
            return None
        if role == "long_call":
            long_mid = mark["mid"]
        else:
            short_mid = mark["mid"]
    if long_mid is None:
        return None
    return round(long_mid - (short_mid or 0.0), 4)


def pnl_to_date(
    position: dict, legs: list[dict], leg_marks: dict, assignments: list[dict], spot: float | None
) -> dict | None:
    """A position's P&L so far, in whole-position dollars: every closed or settled leg at its own
    close (`leg_pnl`), every open leg at its mark (`leg_marks`: `{leg_role: mid}`), delivered
    shares at their disposal or, still held, at `spot` -- minus every cost booked so far (`fees`).

    The one P&L-to-date rule: the held-long stop, `analytics.headline`'s open mark-to-market, the
    tracker and `excursions` all read it, so no two can disagree about what a position is worth.
    None when any part cannot be priced -- never a partial figure dressed as a whole one."""
    mult = 100 * int(position.get("quantity") or 1)
    long_pnl = short_realised = short_open = 0.0
    for leg in legs:
        sign = 1 if leg["action"] == "Buy to Open" else -1
        if leg["status"] == "open":
            mid = leg_marks.get(leg["leg_role"])
            if mid is None or leg.get("entry_mid") is None:
                return None
            pnl = (mid - leg["entry_mid"]) * sign * mult
        else:
            per_share = leg_pnl(leg)
            if per_share is None:
                return None
            pnl = per_share * mult
        if leg["leg_role"] == "long_call":
            long_pnl += pnl
        elif leg["status"] == "open":
            short_open += pnl
        else:
            short_realised += pnl
    shares = 0.0
    for a in assignments:
        if a.get("status") == "disposed" and a.get("share_pnl") is not None:
            shares += a["share_pnl"]
        elif spot is None:
            return None
        else:
            shares += share_pnl(a["direction"], a["shares"], a["basis"], spot)
    gross = long_pnl + short_realised + short_open + shares
    fees = float(position.get("fees") or 0.0)
    return {
        "long": round(long_pnl, 2),
        "short_realised": round(short_realised, 2),
        "short_open": round(short_open, 2),
        "shares": round(shares, 2),
        "gross": round(gross, 2),
        "fees": round(fees, 2),
        "net": round(gross - fees, 2),
    }


def short_time_value(short_mid: float, spot: float, short_strike: float) -> float:
    """The short call's per-share extrinsic at a mark — the number the whole exit rule reads."""
    return round(short_mid - max(0.0, spot - short_strike), 4)


def settle_intrinsic(strike: float, option_type: str, spot: float) -> float:
    """Intrinsic value of one leg at the settlement print. Under PHYSICAL settlement it is still the
    option's own value at expiry — what changes is that the leg also delivers stock, which
    `assignment_from` arms separately (the calendars decomposition, adopted whole)."""
    if option_type == "put":
        return round(max(0.0, strike - spot), 4)
    return round(max(0.0, spot - strike), 4)


# --------------------------------------------------------------------------- physical settlement
#
# The calendars decomposition, unchanged: arm delivered shares at the SETTLEMENT SPOT, not the
# strike. For this module's short call at strike K, credit E, settlement spot S_f, cover price S_m:
#
#     option leg  E - (S_f - K)      the existing intrinsic accounting, untouched
#     share leg   S_f - S_m          short shares, basis S_f
#     total       E + K - S_m        = +E premium, sell (deliver) at K, buy back at S_m
#
# so physical settlement is exactly cash settlement PLUS a share leg. The long call does NOT expire
# with the short — it has ~12 DTE left and stays an open leg; the next session's disposal covers the
# short shares and sells the long together, which is the honest model of "both legs closed at
# assignment" (a real desk would SELL a 12-DTE long, not exercise it and abandon its extrinsic —
# which is ≈0 here by construction, making the two readings nearly identical anyway).


def assignment_from(leg: dict, spot: float, quantity: int) -> dict | None:
    """The share position one ITM leg delivers at expiry, or None if it expires worthless. `basis`
    is the settlement spot, per the decomposition above. You end up SHORT shares when a short call
    is assigned, LONG shares when a long call is exercised at its own expiry.

    This function has NO settlement-style opinion of its own — it does not look at the symbol at
    all, only at the leg's own strike/type/action. That is deliberate: a cash-settled leg (XSP)
    never delivers shares, and the guard against that lives at the ONE call site
    (`book.settle_expiring_legs`, gated `if physical:`) rather than duplicated here. Do not call
    this for a cash-settled leg — nothing downstream would catch a phantom share assignment."""
    option_type = leg["option_type"]
    if settle_intrinsic(leg["strike"], option_type, spot) <= 0:
        return None
    sold = leg.get("action") == "Sell to Open"
    long_shares = (sold and option_type == "put") or (not sold and option_type == "call")
    return {
        "direction": "long" if long_shares else "short",
        "shares": 100 * int(quantity or 1),
        "basis": round(float(spot), 4),
        "strike": leg["strike"],
        "option_type": option_type,
    }


# Delivered-share P&L lives in core: calendars and pmcc both model physical settlement and must
# not disagree about the money. Re-exported under the local name every call site already uses.
share_pnl = _settlement.share_pnl  # noqa: F401


# --------------------------------------------------------------------------- the fee stack


def settlement_fee(itm_settlements: int) -> float:
    """$5 per DISTINCT ITM settlement symbol (never per contract), charged the next business day."""
    return _fees.ic_expire_fee(itm_settlements)


def allocate(total: float, raw: list[float]) -> list[float]:
    """Split one ticket's rounded `total` across its legs: each leg its own share rounded to the
    cent, the LAST leg the remainder -- so the legs sum to the ticket exactly, never a cent off.
    `raw` is each leg's own unrounded share, in leg order."""
    if not raw:
        return []
    shares = [round(x, 2) for x in raw[:-1]]
    shares.append(round(total - sum(shares), 2))
    return shares


def leg_fee(symbol: str, quantity: int, *, opening: bool, selling: bool) -> float:
    """One leg's own share of a ticket's fee schedule -- the core schedule is per leg and linear
    (commission, its per-leg cap, clearing, ORF, the index exchange fee, TAF on a sell), so a
    ticket's legs priced one at a time sum to the ticket."""
    fn = _fees.ic_open_fee if opening else _fees.ic_close_fee
    return fn(symbol, quantity, legs=1, sell_legs=1 if selling else 0, ndigits=4)


def leg_costs(
    symbol: str, legs: list[dict], quantity: int, config: dict, ticket: dict, *, opening: bool
) -> list[dict]:
    """Each leg's `{"fee", "slippage"}` share of one ticket (`ticket` is that ticket's own
    `entry_cost`/`close_cost` result). `legs` carry `bid`, `ask` and `selling` (whether this leg
    is a sell on THIS ticket: a short opening, or a long closing)."""
    fees = allocate(
        ticket["fee"],
        [leg_fee(symbol, quantity, opening=opening, selling=leg["selling"]) for leg in legs],
    )
    slips = allocate(
        ticket["slippage"],
        [_fees.slippage_dollars([{"bid": leg["bid"], "ask": leg["ask"]}], quantity, config) for leg in legs],
    )
    return [{"fee": f, "slippage": s} for f, s in zip(fees, slips, strict=True)]


# The two-leg spread books' cost and leg-P&L rules live once in core; calendars, curve and pmcc
# carried identical copies (and bwb its own leg_pnl). Kept under the old names for every caller.
_slippage_dollars = _fees.slippage_dollars
entry_cost = _fees.spread_entry_cost
close_cost = _fees.spread_close_cost
assignment_fee = _fees.assignment_fee
leg_pnl = _settlement.leg_pnl
