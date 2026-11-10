"""Ways to move the book's beta-weighted delta without closing what it holds (`portfolio.delta_ideas`).

The direction card's ideas. Worked out on every evaluation, inside the band or out, in both
directions ("add" delta and "cut" it), because the question comes up before delta leaves its band.
Three parts:

- **drivers**: the trades whose delta makes the book's, largest first, each with the reason in a line
  (a condor's short call nearer the money than its put; a calendar's strike below spot);
- **ideas**, four kinds, each priced and ranked by the delta it adds in that direction:
  - `front_roll`: a time spread's short within `front_days` of expiry is rolled anyway, so roll it to
    the strike that moves delta most, for a credit, with its short at most `roll_short_delta`. Priced
    on the snapshot's roll-out chains, the nearest quoted expiry first and never on or after the long.
    The plain roll (same strike, that expiry) is stated beside it, since that is what moves nothing;
  - `untested_side`: a credit trade selling both sides in one expiry rolls the untested side's
    spread toward the money, keeping its width (so no buying power moves), its short nearest
    `untested_short_delta` and never past the other side's short. Priced on the snapshot's
    `roll_chains` for that expiry;
  - `pmcc_roll`: a recognised, reconciled PMCC (pmcc.py) is a stock substitute, not a plain diagonal:
    its driver row says so, and its short rolls by the PMCC's own rules (`pmcc.roll`: short at most
    `max_delta`, at most `max_debit` a share as a debit, the width check still passing), on any quoted
    call expiry before the long, not only in its front week;
  - `new_spread`: one unit of the hedge's own credit and debit spreads (`hedge.py`) on its chain.
- **together**: every roll idea in one direction, one per trade, summed;
- **unquoted**: a trade a roll applies to whose chain the snapshot does not quote, named with why.

A roll that takes a credit at mid but pays at the natural price is not offered (a PMCC's: one that
pays more than its `max_debit` at the natural price).

Each idea carries the flags a person would weigh: the ruleset has `roll.untested_side` off, the trade
is already past the per-trade risk budget (`risk.oversize` fired), the roll inverts a time spread and
ties up its width as collateral, earnings fall before the new expiry, a debit where the ruleset rolls
for credit only. Every leg is priced at its quoted mid, its delta at its own IV (the portfolio
reading's model), so the before and after are one model's figures; the mid is not a fill and each
idea also states the natural price where every leg is quoted. Ideas inform; they size, place and close
nothing, and carry no confidence because none is a rule.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from cherrypick.manage import hedge as _hedge
from cherrypick.manage import pmcc as _pmcc
from cherrypick.manage import portfolio as _portfolio
from cherrypick.manage import pricing

MULTIPLIER = pricing.MULTIPLIER
DIRECTIONS = ("add", "cut")


def _f(value: Any) -> float | None:
    return _portfolio._f(value)


def _r(value: float | None, places: int = 2) -> float | None:
    return None if value is None else round(value, places)


def _label(expiration: str, strike: float, right: str) -> str:
    return f"{expiration} {strike:g}{right}"


def _scales(book: dict[str, Any], trades: list[dict[str, Any]]) -> dict[str, float]:
    """Each name's benchmark shares per share of delta: beta x spot / the benchmark's spot."""
    bench = _f(book.get("benchmark_spot"))
    betas = {u["underlying"]: _f(u.get("beta")) for u in book.get("underlyings") or []}
    out: dict[str, float] = {}
    for t in trades:
        beta, spot = betas.get(t["underlying"]), _f(t.get("spot"))
        if bench and beta is not None and spot:
            out[t["underlying"]] = beta * spot / bench
    return out


def _trade_bwd(t: dict[str, Any], scale: float | None) -> float | None:
    if scale is None:
        return None
    total = 0.0
    for leg in t.get("legs") or []:
        qty = _f(leg.get("quantity")) or 0.0
        if leg.get("right") in ("C", "P"):
            d = _f(leg.get("delta"))
            if d is None:
                return None
            total += qty * MULTIPLIER * d
        else:
            total += qty
    return total * scale


def _options(t: dict[str, Any]) -> list[dict[str, Any]]:
    return [leg for leg in t.get("legs") or [] if leg.get("right") in ("C", "P") and _f(leg.get("quantity"))]


def _both_sides(t: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str] | None:
    """(short put, short call, expiry) when the trade sells one put and one call in the same expiry."""
    opts = _options(t)
    shorts = [leg for leg in opts if _f(leg["quantity"]) < 0]
    puts = [leg for leg in shorts if leg["right"] == "P"]
    calls = [leg for leg in shorts if leg["right"] == "C"]
    if len(puts) != 1 or len(calls) != 1 or puts[0]["expiration"] != calls[0]["expiration"]:
        return None
    return puts[0], calls[0], str(puts[0]["expiration"])[:10]


def _time_spread(t: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """(short, long) when the trade is one short and one long option of one right and size, the short
    expiring first: a calendar or a diagonal."""
    opts = _options(t)
    if len(opts) != 2 or opts[0]["right"] != opts[1]["right"]:
        return None
    short = next((leg for leg in opts if _f(leg["quantity"]) < 0), None)
    long_ = next((leg for leg in opts if _f(leg["quantity"]) > 0), None)
    if short is None or long_ is None or abs(_f(short["quantity"]) + _f(long_["quantity"])) > 1e-9:
        return None
    if str(short["expiration"])[:10] >= str(long_["expiration"])[:10]:
        return None
    return short, long_


def _away(strike: float, spot: float) -> str:
    return f"{abs(strike - spot) / spot * 100:.1f}%"


def _reason(t: dict[str, Any]) -> str:
    spot = _f(t.get("spot"))
    if not spot:
        return t.get("shape") or "no price"
    sides = _both_sides(t)
    if sides:
        put, call, _ = sides
        near = "calls" if abs(call["strike"] - spot) < abs(spot - put["strike"]) else "puts"
        return (
            f"short call {_away(call['strike'], spot)} away, short put {_away(put['strike'], spot)}: "
            f"the {near} are nearer the money"
        )
    spread = _time_spread(t)
    if spread:
        short, _ = spread
        where = "below" if spot > short["strike"] else "above"
        leg = f"{short['strike']:g}{short['right']}"
        return f"the {leg} short is {_away(short['strike'], spot)} {where} spot ({spot:g})"
    return t.get("shape") or "held"


def _flags_for(t: dict[str, Any]) -> list[dict[str, str]]:
    out = []
    for f in t.get("fired") or []:
        if f.get("rule") == "risk.oversize":
            out.append(
                {
                    "key": "oversize",
                    "text": f"already past the per-trade risk budget: {f.get('reason') or ''}".strip(),
                }
            )
    return out


def _earnings_flag(name: str, metrics: dict[str, Any], session: date, expiry: str) -> dict[str, str] | None:
    raw = ((metrics or {}).get(name) or {}).get("earnings_date")
    try:
        when = date.fromisoformat(str(raw)[:10]) if raw else None
    except ValueError:
        return None
    if when and session <= when <= date.fromisoformat(expiry):
        return {"key": "earnings", "text": f"earnings {when.isoformat()}, before the new expiry"}
    return None


def _quote(
    o: dict[str, Any], spot: float, years: float, right: str, rate: float, q: float
) -> dict[str, Any] | None:
    """A chain option with its delta and theta at its own IV, solved from its mid; None if unpriceable."""
    mid, strike = _f(o.get("mid")), _f(o.get("strike"))
    if mid is None or mid <= 0 or strike is None or o.get("right") != right or years <= 0:
        return None
    iv = pricing.implied_vol(spot, strike, years, mid, right, rate, q)
    g = pricing.greeks(spot, strike, years, iv, right, rate, q) if iv else None
    if g is None:
        return None
    return {"strike": strike, "mid": mid, "bid": _f(o.get("bid")), "ask": _f(o.get("ask")), "iv": iv, **g}


def _leg_greeks(leg: dict[str, Any], spot: float, rate: float) -> dict[str, Any] | None:
    iv, years = _f(leg.get("iv")), _f(leg.get("years"))
    if not iv or not years:
        return None
    return pricing.greeks(
        spot, float(leg["strike"]), years, iv, leg["right"], rate, float(leg.get("div_yield") or 0.0)
    )


def _natural(rows: list[tuple[str, dict[str, Any]]]) -> float | None:
    """Per share, + a credit: every sale at the bid, every purchase at the ask."""
    total = 0.0
    for action, o in rows:
        bid, ask = o.get("bid"), o.get("ask")
        if bid is None or ask is None:
            return None
        total += bid if action in ("STO", "STC") else -ask
    return total


def _leg_row(action: str, qty: float, expiration: str, o: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": action,
        "quantity": abs(qty),
        "label": _label(expiration, float(o["strike"]), o["right"]),
        "mid": _r(_f(o.get("mid"))),
        "bid": _f(o.get("bid")),
        "ask": _f(o.get("ask")),
        "delta": _r(_f(o.get("delta")), 3),
    }


def _held_quote(leg: dict[str, Any], chain_opts: list[dict[str, Any]]) -> dict[str, Any]:
    """A held leg with the chain's bid and ask where the chain quotes its strike."""
    match = next(
        (
            o
            for o in chain_opts
            if o.get("right") == leg["right"] and _f(o.get("strike")) == float(leg["strike"])
        ),
        None,
    )
    return {
        "strike": float(leg["strike"]),
        "right": leg["right"],
        "mid": _f(leg.get("mid")),
        "delta": _f(leg.get("delta")),
        "bid": _f((match or {}).get("bid")),
        "ask": _f((match or {}).get("ask")),
    }


# --------------------------------------------------------------------------- front roll


def _front_roll(
    t: dict[str, Any],
    direction: str,
    scale: float,
    roll_out: dict[str, Any] | None,
    session: date,
    rules: dict[str, Any],
    cfg: dict[str, Any],
    metrics: dict[str, Any],
) -> dict[str, Any] | None:
    spread = _time_spread(t)
    spot = _f(t.get("spot"))
    if not spread or not spot:
        return None
    short, long_ = spread
    if short.get("dte") is None or int(short["dte"]) > int(cfg["front_days"]):
        return None
    if _f(short.get("mid")) is None or _f(short.get("delta")) is None:
        return None
    name, right = t["underlying"].upper(), short["right"]
    sexp, lexp = str(short["expiration"])[:10], str(long_["expiration"])[:10]
    chains = (roll_out or {}).get("chains") or {}
    expiries = sorted(
        k.split("|")[2] for k in chains if k.startswith(f"{name}|{right}|") and sexp < k.split("|")[2] < lexp
    )
    if not expiries:
        return None
    rate = float(rules["pricing"]["rate"])
    q = float(short.get("div_yield") or 0.0)
    qty = _f(short["quantity"])  # negative
    old_mid, old_delta = _f(short["mid"]), _f(short["delta"])
    sign = 1.0 if direction == "add" else -1.0
    cap = float(cfg["roll_short_delta"])
    min_credit = float(cfg["min_credit"])
    for expiry in expiries:
        chain = chains[f"{name}|{right}|{expiry}"]
        years = (date.fromisoformat(expiry) - session).days / 365.0
        quoted = [
            x for x in (_quote(o, spot, years, right, rate, q) for o in chain.get("options") or []) if x
        ]
        plain = next((x for x in quoted if x["strike"] == float(short["strike"])), None)
        old_q = _held_quote(short, chain.get("options") or [])
        best = None
        for x in quoted:
            change = qty * MULTIPLIER * (x["delta"] - old_delta) * scale
            credit = x["mid"] - old_mid
            if change * sign <= 0 or abs(x["delta"]) > cap or credit < min_credit:
                continue
            # A credit at mid that is a debit at the natural price is no credit roll.
            natural = _natural([("BTC", old_q), ("STO", {**x, "right": right})])
            if natural is not None and natural < 0:
                continue
            if best is None or change * sign > best[0] * sign:
                best = (change, x, credit)
        if best is None:
            continue
        change, x, credit = best
        flags = _flags_for(t)
        long_k, new_k = float(long_["strike"]), x["strike"]
        inverted = new_k > long_k if right == "P" else new_k < long_k
        was = max(
            0.0, (float(short["strike"]) - long_k) if right == "P" else (long_k - float(short["strike"]))
        )
        width = abs(new_k - long_k) if inverted else 0.0
        bp_change = (width - was) * MULTIPLIER * abs(qty)
        if inverted:
            flags.append(
                {
                    "key": "inverts",
                    "text": f"the short sits {'above' if right == 'P' else 'below'} the long: a "
                    f"{width:g}-point spread that ties up ${width * MULTIPLIER * abs(qty):,.0f}",
                }
            )
        earn = _earnings_flag(name, metrics, session, expiry)
        if earn:
            flags.append(earn)
        old_g = _leg_greeks(short, spot, rate)
        theta_change = qty * MULTIPLIER * (x["theta"] - old_g["theta"]) if old_g else None
        x_row = {**x, "right": right}
        legs = [_leg_row("BTC", qty, sexp, old_q), _leg_row("STO", qty, expiry, x_row)]
        natural = _natural([("BTC", old_q), ("STO", x_row)])
        plain_change = qty * MULTIPLIER * (plain["delta"] - old_delta) * scale if plain else None
        return {
            "kind": "front_roll",
            "header": f"Roll the {sexp[5:]} {float(short['strike']):g}{right} short "
            f"to {expiry[5:]} {new_k:g}{right}",
            "explanation": (
                f"The short expires in {int(short['dte'])} days and is rolled anyway. Rolling it to the "
                f"{new_k:g} strike instead of the same one moves delta "
                f"{'up' if direction == 'add' else 'down'} for a credit"
                + (
                    f"; the plain roll to {expiry[5:]} moves it {plain_change:+.1f}."
                    if plain_change is not None
                    else "."
                )
            ),
            "legs": legs,
            "price": _r(credit),
            "natural": _r(natural),
            "cash": _r(credit * MULTIPLIER * abs(qty)),
            "bwd_change": _r(change),
            "theta_change": _r(theta_change),
            "buying_power_change": _r(bp_change),
            "plain_bwd_change": _r(plain_change),
            "new_expiry": expiry,
            "flags": flags,
        }
    return None


# --------------------------------------------------------------------------- untested side


def _untested_side(
    t: dict[str, Any],
    direction: str,
    scale: float,
    roll_chains: dict[str, Any],
    rules: dict[str, Any],
    cfg: dict[str, Any],
) -> dict[str, Any] | None:
    sides = _both_sides(t)
    spot = _f(t.get("spot"))
    if not sides or not spot:
        return None
    put, call, expiry = sides
    if _f(put.get("delta")) is None or _f(call.get("delta")) is None:
        return None
    # Adding delta moves the put side up; cutting it moves the call side down. Only the untested side moves.
    moving, other = (put, call) if direction == "add" else (call, put)
    if abs(_f(moving["delta"])) >= abs(_f(other["delta"])):
        return None
    right = moving["right"]
    out_sign = -1.0 if right == "P" else 1.0  # the wing sits further out: lower for puts, higher for calls
    wings = [
        leg
        for leg in _options(t)
        if leg["right"] == right
        and _f(leg["quantity"]) > 0
        and str(leg["expiration"])[:10] == expiry
        and (float(leg["strike"]) - float(moving["strike"])) * out_sign > 0
    ]
    wing = wings[0] if len(wings) == 1 else None
    if len(wings) > 1 or (wing is not None and _f(wing.get("delta")) is None):
        return None
    chain = roll_chains.get(f"{t['underlying'].upper()}|{expiry}") or {}
    opts = chain.get("options") or []
    if not opts:
        return None
    rate = float(rules["pricing"]["rate"])
    q = float(moving.get("div_yield") or 0.0)
    years = _f(moving.get("years")) or 0.0
    quoted = {x["strike"]: x for x in (_quote(o, spot, years, right, rate, q) for o in opts) if x}
    width = abs(float(wing["strike"]) - float(moving["strike"])) if wing else None
    target = min(float(cfg["untested_short_delta"]), abs(_f(other["delta"])))
    qty = _f(moving["quantity"])  # negative
    wqty = _f(wing["quantity"]) if wing else 0.0
    old_net = _f(moving["mid"]) - (_f(wing["mid"]) if wing else 0.0)
    old_rows = [("BTC", _held_quote(moving, opts))] + ([("STC", _held_quote(wing, opts))] if wing else [])
    best = None
    for k, x in quoted.items():
        toward = k > float(moving["strike"]) if right == "P" else k < float(moving["strike"])
        past = k >= float(other["strike"]) if right == "P" else k <= float(other["strike"])
        if not toward or past:
            continue
        w = quoted.get(k + out_sign * width) if width else None
        if width and w is None:
            continue
        credit = x["mid"] - (w["mid"] if w else 0.0) - old_net
        rows = [("STO", {**x, "right": right})] + ([("BTO", {**w, "right": right})] if w else [])
        natural = _natural(old_rows + rows)
        # A credit at mid that is a debit at the natural price is no credit roll.
        if credit < float(cfg["min_credit"]) or (natural is not None and natural < 0):
            continue
        score = abs(abs(x["delta"]) - target)
        if best is None or score < best[0]:
            best = (score, x, w, credit, natural)
    if best is None:
        return None
    _, x, w, credit, natural = best
    change = qty * MULTIPLIER * (x["delta"] - _f(moving["delta"]))
    if wing and w:
        change += wqty * MULTIPLIER * (w["delta"] - _f(wing["delta"]))
    change *= scale
    old_g, wing_g = _leg_greeks(moving, spot, rate), _leg_greeks(wing, spot, rate) if wing else None
    theta_change = None
    if old_g and (not wing or (wing_g and w)):
        theta_change = qty * MULTIPLIER * (x["theta"] - old_g["theta"])
        if wing:
            theta_change += wqty * MULTIPLIER * (w["theta"] - wing_g["theta"])
    flags = _flags_for(t)
    ur = (rules.get("roll") or {}).get("untested_side") or {}
    if ur.get("enabled") is False:
        flags.append({"key": "rule_off", "text": "your ruleset has roll.untested_side off"})
    side = "put" if right == "P" else "call"
    new_rows = [("STO", {**x, "right": right})] + ([("BTO", {**w, "right": right})] if w else [])
    legs = [_leg_row(a, qty if a in ("BTC", "STO") else wqty, expiry, o) for a, o in old_rows + new_rows]
    shape = f"{side} spread" if wing else f"short {side}"
    return {
        "kind": "untested_side",
        "header": f"Roll the {side} side up to {x['strike']:g}{right}"
        if right == "P"
        else f"Roll the {side} side down to {x['strike']:g}{right}",
        "explanation": (
            f"The {side}s are the untested side ({abs(_f(moving['delta'])):.2f} delta against "
            f"{abs(_f(other['delta'])):.2f}). Moving the {shape} to {x['strike']:g} in the same expiry "
            "takes a "
            f"credit{', keeps its width' if wing else ''} and puts the short {_away(x['strike'], spot)} "
            f"from spot (was {_away(float(moving['strike']), spot)}): a "
            f"{'fall' if right == 'P' else 'rise'} reaches it sooner."
        ),
        "legs": legs,
        "price": _r(credit),
        "natural": _r(natural),
        "cash": _r(credit * MULTIPLIER * abs(qty)),
        "bwd_change": _r(change),
        "theta_change": _r(theta_change),
        "buying_power_change": 0.0 if wing else None,
        "new_expiry": expiry,
        "flags": flags,
    }


# --------------------------------------------------------------------------- PMCC


def _pmcc_views(account_pmcc: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """The recognised, reconciled PMCCs (pmcc.py), by name."""
    return {
        str(v["underlying"]).upper(): v
        for v in (account_pmcc or {}).get("positions") or []
        if v.get("recognized") and v.get("reconciled")
    }


def _is_pmcc(t: dict[str, Any], views: dict[str, dict[str, Any]]) -> bool:
    return bool((t.get("pmcc") or {}).get("recognized")) and t["underlying"].upper() in views


def _pmcc_roll(
    t: dict[str, Any],
    direction: str,
    scale: float,
    view: dict[str, Any],
    snapshot: dict[str, Any],
    metrics: dict[str, Any],
    session: date,
    rules: dict[str, Any],
) -> dict[str, Any] | None:
    """A PMCC's short rolled by the PMCC's own roll rules (`pmcc.roll`): any quoted call expiry after the
    short's and before the long's, the short at most `max_delta`, the roll costing at most `max_debit` a
    share (at mid and at the natural price), and the width check still passing. Of those, the strike that
    moves delta most in `direction`. Not tied to `front_days`: a PMCC's short is a monthly, rolled when
    its own rules say."""
    spread = _time_spread(t)
    spot = _f(t.get("spot"))
    if not spread or not spot or view.get("div_yield") is None:
        return None
    short, long_ = spread
    if short["right"] != "C" or _f(short.get("mid")) is None or _f(short.get("delta")) is None:
        return None
    pcfg = rules["pmcc"]["roll"]
    max_delta, max_debit = float(pcfg["max_delta"]), float(pcfg["max_debit"])
    net_ps = (view.get("width") or {}).get("net_cost_per_share")
    warn = float(rules["pmcc"]["width"]["warn_share"])
    quotes = _pmcc._quoted_expiries(
        t["underlying"].upper(),
        snapshot.get("income"),
        snapshot.get("roll_chains"),
        snapshot.get("roll_out_chains"),
    )
    sexp, lexp = str(short["expiration"])[:10], str(long_["expiration"])[:10]
    later = sorted(e for e in quotes if sexp < e < lexp)[: int(pcfg["expiries"])]
    rate = float(rules["pricing"]["rate"])
    qty = _f(short["quantity"])  # negative
    old_mid, old_delta = _f(short["mid"]), _f(short["delta"])
    long_k = float(long_["strike"])
    sign = 1.0 if direction == "add" else -1.0
    old_q = {
        "strike": float(short["strike"]),
        "right": "C",
        "mid": old_mid,
        "delta": old_delta,
        "bid": None,
        "ask": None,
    }
    # The held short's bid and ask, where any of the PMCC's quoted chains carries its own expiry.
    if quotes.get(sexp):
        old_q = _held_quote(short, [{**q, "right": "C"} for q in quotes[sexp]])
    best = None
    for exp in later:
        years = (date.fromisoformat(exp) - session).days / 365.0
        for q in quotes[exp]:
            _, d = _pmcc._solve(spot, q["strike"], years, q["mid"], "C", rate, view["div_yield"])
            if d is None or d > max_delta + 1e-9:
                continue
            net = q["mid"] - old_mid
            if net < -max_debit - 1e-9:
                continue
            if net_ps is not None and not q["strike"] - long_k > net_ps - net + 1e-9:
                continue
            change = qty * MULTIPLIER * (d - old_delta) * scale
            if change * sign <= 0:
                continue
            row = {**q, "right": "C", "delta": d}
            natural = _natural([("BTC", old_q), ("STO", row)])
            if natural is not None and natural < -max_debit - 1e-9:
                continue
            if best is None or change * sign > best[0] * sign:
                best = (change, exp, row, net, natural, years)
    if best is None:
        return None
    change, exp, row, net, natural, years = best
    q_div = view["div_yield"]
    iv = pricing.implied_vol(spot, row["strike"], years, row["mid"], "C", rate, q_div)
    g = pricing.greeks(spot, row["strike"], years, iv, "C", rate, q_div) if iv else None
    old_g = _leg_greeks(short, spot, rate)
    theta_change = qty * MULTIPLIER * (g["theta"] - old_g["theta"]) if g and old_g else None
    flags = _flags_for(t)
    if net_ps is not None:
        kept = (row["strike"] - long_k) - (net_ps - net)
        share = (net_ps - net) / (row["strike"] - long_k)
        flags.append(
            {
                "key": "thin" if share > warn else "width",
                "text": f"width holds: assigned at {row['strike']:g} it keeps "
                f"${kept * MULTIPLIER * abs(qty):,.0f}"
                + (f", a thin {share:.0%} of the width used" if share > warn else ""),
            }
        )
    earn = _earnings_flag(t["underlying"].upper(), metrics, session, exp)
    if earn:
        flags.append(earn)
    word = "credit" if net >= 0 else "debit"
    return {
        "kind": "pmcc_roll",
        "header": f"Roll the PMCC's {sexp[5:]} {float(short['strike']):g}C short "
        f"to {exp[5:]} {row['strike']:g}C",
        "explanation": (
            "A PMCC stands in for the shares, so its long delta is the point: "
            + (
                "rolling the short up gives more of it back to the long. "
                if direction == "add"
                else "rolling the short down caps more of the move, by choice. "
            )
            + f"Its own roll rules apply: the short at most {max_delta:.2f} delta, at most "
            f"{max_debit:.2f} a share as a debit, and the width check still passing. This roll is a {word}."
        ),
        "legs": [_leg_row("BTC", qty, sexp, old_q), _leg_row("STO", qty, exp, row)],
        "price": _r(net),
        "natural": _r(natural),
        "cash": _r(net * MULTIPLIER * abs(qty)),
        "bwd_change": _r(change),
        "theta_change": _r(theta_change),
        "buying_power_change": 0.0,
        "plain_bwd_change": None,
        "new_expiry": exp,
        "flags": flags,
    }


# --------------------------------------------------------------------------- new spread


def _new_spreads(
    direction: str,
    book: dict[str, Any],
    chain: dict[str, Any] | None,
    metrics: dict[str, Any],
    rules: dict[str, Any],
) -> list[dict[str, Any]]:
    if not chain or not chain.get("options"):
        return []
    root_beta = _f(((metrics or {}).get(chain.get("root")) or {}).get("beta"))
    spot, bench = _f(chain.get("spot")), _f(book.get("benchmark_spot"))
    if root_beta is None or not spot or not bench or book.get("bwd") is None:
        return []
    hcfg = rules["portfolio"]["hedge"]
    options = _hedge._priced(chain, float(rules["pricing"]["rate"]))
    need = 1e-9 if direction == "add" else -1e-9  # one unit: the sign alone picks the side
    out = []
    for kind in ("credit", "debit"):
        built = _hedge._structure(kind, need, options, hcfg)
        if built is None:
            continue
        s = _hedge._size(kind, *built, need, root_beta * spot / bench, book, rules, chain)
        if s.get("error"):
            continue
        out.append(
            {
                "kind": "new_spread",
                "header": f"Open one {chain['root']} {s['structure']}",
                "explanation": (
                    f"A new defined-risk {s['structure']} in {chain['root']} "
                    f"({chain.get('dte')} days), one unit. "
                    f"It moves delta without touching what is held, and ties up its "
                    f"${abs(s['max_loss']):,.0f} max loss."
                ),
                "legs": [
                    {
                        "action": "BTO" if leg["quantity"] > 0 else "STO",
                        "quantity": abs(leg["quantity"]),
                        "label": leg["label"].split(" ", 1)[1],
                        "mid": leg["mid"],
                        "bid": leg["bid"],
                        "ask": leg["ask"],
                        "delta": leg["delta"],
                    }
                    for leg in s["legs"]
                ],
                "underlying": chain["root"],
                "price": s["price"],
                "natural": s["natural"],
                "cash": s["cash"],
                "bwd_change": s["bwd_change"],
                "theta_change": s["theta_change"],
                "buying_power_change": s["max_loss"],
                "new_expiry": chain.get("expiration"),
                "flags": [{"key": "past_cap", "text": "past the buying-power cap"}]
                if s.get("past_cap")
                else [],
            }
        )
    return out


def _unquoted(
    t: dict[str, Any],
    roll_out: dict[str, Any] | None,
    roll_chains: dict[str, Any],
    cfg: dict[str, Any],
    snapshot: dict[str, Any],
    views: dict[str, dict[str, Any]],
) -> str | None:
    """Why a trade one of the rolls applies to has no idea for want of quotes, or None."""
    name = t["underlying"].upper()
    spread = _time_spread(t)
    if spread and _is_pmcc(t, views):
        short, long_ = spread
        sexp, lexp = str(short["expiration"])[:10], str(long_["expiration"])[:10]
        quotes = _pmcc._quoted_expiries(name, snapshot.get("income"), roll_chains, roll_out)
        if any(sexp < e < lexp for e in quotes):
            return None
        return "no quoted call expiry between the PMCC's short and its long"
    if spread:
        short, long_ = spread
        if short.get("dte") is None or int(short["dte"]) > int(cfg["front_days"]):
            return None
        sexp, lexp = str(short["expiration"])[:10], str(long_["expiration"])[:10]
        chains = (roll_out or {}).get("chains") or {}
        if any(
            k.startswith(f"{name}|{short['right']}|")
            and sexp < k.split("|")[2] < lexp
            and (chains[k].get("options"))
            for k in chains
        ):
            return None
        return f"no quoted expiry to roll the {sexp[5:]} {float(short['strike']):g}{short['right']} short to"
    sides = _both_sides(t)
    if sides and not (roll_chains.get(f"{name}|{sides[2]}") or {}).get("options"):
        return f"no quoted {sides[2]} chain to roll a side in"
    return None


# --------------------------------------------------------------------------- the block


def suggest(
    book: dict[str, Any],
    trades: list[dict[str, Any]],
    snapshot: dict[str, Any],
    metrics: dict[str, Any],
    session: date,
    rules: dict[str, Any],
    pmcc: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """The direction card's ideas, or None when the ruleset turns them off. `pmcc` is the account's
    PMCC view (pmcc.py): a recognised PMCC is rolled by its own rules, not as a plain time spread."""
    cfg = (rules.get("portfolio") or {}).get("delta_ideas") or {}
    if not cfg or cfg.get("enabled") is False:
        return None
    bwd = _f(book.get("bwd"))
    band = _portfolio.delta_band(book, rules["portfolio"]["delta"])
    out: dict[str, Any] = {
        "bwd": bwd,
        "benchmark": book.get("benchmark"),
        "band": list(band) if band else None,
        "inside": band is not None and bwd is not None and band[0] <= bwd <= band[1],
        "drivers": [],
        "add": [],
        "cut": [],
        "together": {},
        "error": None,
    }
    if bwd is None:
        out["error"] = "no beta-weighted delta to move"
        return out
    scales = _scales(book, trades)
    views = _pmcc_views(pmcc)
    drivers = []
    for t in trades:
        v = _trade_bwd(t, scales.get(t["underlying"]))
        if v is not None:
            is_pmcc = _is_pmcc(t, views)
            drivers.append(
                {
                    "trade_id": t.get("trade_id"),
                    "underlying": t["underlying"],
                    "shape": t.get("shape"),
                    "bwd": round(v, 2),
                    # A PMCC's long delta is what it is held for, so it is named as such.
                    "pmcc": is_pmcc,
                    "reason": ("PMCC, a stock substitute: " if is_pmcc else "") + _reason(t),
                }
            )
    drivers.sort(key=lambda d: -abs(d["bwd"]))
    out["drivers"] = drivers[: int(cfg["drivers"])]
    roll_out, roll_chains = snapshot.get("roll_out_chains"), snapshot.get("roll_chains") or {}
    # A roll left out for want of quotes is named, never dropped silently.
    out["unquoted"] = [
        {"trade_id": t.get("trade_id"), "underlying": t["underlying"], "why": why}
        for t in trades
        if (why := _unquoted(t, roll_out, roll_chains, cfg, snapshot, views))
    ]
    for direction in DIRECTIONS:
        ideas = []
        for t in trades:
            scale = scales.get(t["underlying"])
            if scale is None:
                continue
            if _is_pmcc(t, views):
                idea = _pmcc_roll(
                    t, direction, scale, views[t["underlying"].upper()], snapshot, metrics, session, rules
                )
            else:
                idea = _front_roll(
                    t, direction, scale, roll_out, session, rules, cfg, metrics
                ) or _untested_side(t, direction, scale, roll_chains, rules, cfg)
            if idea:
                ideas.append(
                    {
                        **idea,
                        "trade_id": t.get("trade_id"),
                        "underlying": t["underlying"],
                        "shape": t.get("shape"),
                    }
                )
        rolls_change = sum(i["bwd_change"] or 0.0 for i in ideas)
        if cfg.get("new_spread", True):
            ideas += [
                {**i, "trade_id": None, "shape": None}
                for i in _new_spreads(direction, book, snapshot.get("hedge_chain"), metrics, rules)
            ]
        for i in ideas:
            after = bwd + (i["bwd_change"] or 0.0)
            i["after_bwd"] = round(after, 2)
            i["in_band_after"] = band is not None and band[0] <= after <= band[1]
        sign = 1.0 if direction == "add" else -1.0
        # The rolls first, largest first; a new spread is the fallback, after them.
        ideas.sort(key=lambda i: (i["kind"] == "new_spread", -sign * (i["bwd_change"] or 0.0)))
        out[direction] = ideas
        rolls = [i for i in ideas if i["kind"] != "new_spread"]
        out["together"][direction] = (
            {
                "ideas": len(rolls),
                "bwd_change": round(rolls_change, 2),
                "after_bwd": round(bwd + rolls_change, 2),
                "cash": round(sum(i["cash"] or 0.0 for i in rolls), 2),
            }
            if rolls
            else None
        )
    return out
