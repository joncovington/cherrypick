"""The PMCC view: a covered call written against a long call instead of shares,
recognised, linked from the broker's trade history, and checked against today.

**Recognition.** A name is a PMCC candidate when its calls are exactly one long and one short of the
same size, the short expiring first, at or above the long's strike (and no shares, which would make
the short a covered call). Neither leg's days left decides it: the standard definition (user,
2026-11-04) lets the short's expiry and delta vary, and a long running short of time is the
`roll_long` flag's, not a reason to drop the view. Shape alone does
not make a PMCC: the long must have been **entered at `entry_min_delta` or more** (0.70), or the
operator must say so. The operator says it on the long's own contract (`trades.<LONG CONTRACT>.intent`,
spaces ignored, e.g. `"XYZ 280121C00040000"`), or on the whole name (`positions.<SYMBOL>.intent`);
the contract wins. A rolled long is a new PMCC (user, 2026-11-06): an intent set on the long it was
rolled from does not carry over. The view names the key (`intent_key`) and where an intent came from
(`intent_source`: `contract` or `symbol`). The delta at entry is the one recorded
from the first full snapshot after the fill when there is one (`entrydelta.py`, carried in
`snapshot.entry_deltas`), else solved from the opening fill and that day's close (the snapshot's
`underlying_history`, v13), with the dividend yield carried (Merton) either way. A recorded delta
whose fill-time band (the underlying's likely move between the fill and the snapshot) straddles the
floor says so in `recognized_by`.
`intent: "diagonal"` keeps a trade out whatever its delta. A delta at entry that cannot be solved (no
close for the day in the snapshot, a split since, a fill no volatility prices) is UNKNOWN, never
guessed, and an unknown delta does not recognise: the candidate is listed, says why, and stays under
the diagonal rules it always had. `recognition` names which of the three a candidate is: `pmcc`,
`diagonal` (decided: an intent or a delta under the floor) or `unknown` (not yet decided), so a
reader never shows an undecided candidate as a diagonal. `structure` names the spread whatever it is
recognised as: `calendar` (the short at the long's strike) or `diagonal` (a higher short), so a
decided non-PMCC is named for what it is.

**Linking.** The long and its shorts are often not one order chain: a short sold after the last one
expired opens a chain of its own. So the view links them as the wheel view does, through the contracts
each order touches (`wheel._components`): the long's component, and every component on the name made
only of calls sold to open and bought to close, expiring before the long, opened since the long was
bought. A component holding anything else (a put, a bought call) is not this PMCC's. The linked orders
must replay, with the expiry and assignment rows on their contracts, to exactly the long and short held
today; when they do not (history that starts after the long was bought, an assignment, a contract the
history never opened) the PMCC is `unreconciled`, its money is shown as far as the rows go, and no
PMCC rule judges it.

**Money** is the broker's: each row's own `value`, never price x quantity, with fees apart. The long's
net cost is every linked row's cash (the long bought, any long it was rolled from, every short sold
and bought back), so a premium collected lowers it. The width check is the strategy's own: the short
strike less the long strike must exceed that net cost per share, or an assignment (the long exercised
to deliver, its time value forfeited) locks in a loss. The P&L if assigned is (short strike - long
strike - net cost per share) x 100 x contracts, gross; with the linked fees, net. A net cost that
passes but is over `width.warn_share` of the width (0.75) is flagged `thin`: a call-away then earns
under a third of the cost, and it is a flag, not a rule that fires. Skate yield is a
COMPARISON figure: the shorts' P&L to date (closed ones realised, the open one at its mark) over the
long's own cost, x 365 / days since it was bought; None under a week, flagged past 500%.

**Today.** The long: delta at entry and now, extrinsic as a share of its price, days left, and a
"roll the long" flag at `roll_long.days_left` or fewer (60, under the 90-150 days a long is best
bought at) or under `roll_long.min_delta`. The short: tested or not, against today's skate call band
(`skate_bands`, moved to today's price as the wheel view moves it), early-assignment exposure (in the
money with extrinsic under the next dividend before an ex-date inside its life, or under
`assignment.near_zero_extrinsic` at any time, the pmcc package's near-zero flag; a cash-settled root
never), its chance of finishing in the money (N(d2)), and an earnings report inside its life.

**Roll candidates** (for a failed width check or an exposed short): on each quoted call expiry later
than the short and before the long, the next `roll.expiries` of them, the strike with the most
premium whose delta is at most `roll.max_delta` and whose roll costs no more than `roll.max_debit` a
share, and that keeps the width check passing. Priced from the snapshot's quotes: the roll-out chains
read for the short (v14: the next expiries after it and before the long, quoted that session), then
the income reading's cycle monthly and covered-call expiry and any quoted roll chain, which are all an
older snapshot has; none priced is said, never modelled.

Pure: positions, history rows and snapshot fields in, the view out. Advice only.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from cherrypick.core import bs

from cherrypick.manage import pricing
from cherrypick.manage import wheel as _wheel

MULTIPLIER = pricing.MULTIPLIER
EPS = 1e-9
# A long call sold within this many days of a new long's purchase, either way, may be the same roll
# done in two orders (`_link`); and how far back a chain of confirmed rolls is followed.
ROLL_WINDOW_DAYS = 5
MAX_ROLLS = 20
SHORT_SIDE = _wheel.SHORT_SIDE


def _num(v: Any) -> float | None:
    return _wheel._num(v)


def _sym(v: Any) -> str:
    return _wheel._sym(v)


def _days(a: str, b: date) -> int:
    return (date.fromisoformat(a[:10]) - b).days


# --------------------------------------------------------------------------- the shape
def candidate(raw_legs: list[dict[str, Any]], session: date, cfg: dict[str, Any]) -> dict[str, Any] | None:
    """(long, short) raw legs when the name's calls are a PMCC's shape, else None."""
    if any(leg.get("right") not in ("C", "P") for leg in raw_legs):
        return None  # shares: the short is a covered call, the wheel view's
    calls = [leg for leg in raw_legs if leg.get("right") == "C" and (_num(leg.get("quantity")) or 0)]
    longs = [leg for leg in calls if _num(leg["quantity"]) > 0]
    shorts = [leg for leg in calls if _num(leg["quantity"]) < 0]
    if len(longs) != 1 or len(shorts) != 1:
        return None
    long, short = longs[0], shorts[0]
    if abs(_num(long["quantity"]) + _num(short["quantity"])) > EPS:
        return None
    lk, sk = _num(long.get("strike")), _num(short.get("strike"))
    le, se = str(long.get("expiration") or ""), str(short.get("expiration") or "")
    if lk is None or sk is None or not le or not se or se >= le or sk < lk - EPS:
        return None
    if _days(se, session) < 0:
        return None  # an expired short the broker still lists is no position to judge
    return {"long": long, "short": short}


# --------------------------------------------------------------------------- dividends
def _yield(hist: dict[str, Any] | None, day: str, price: float | None) -> float | None:
    """Dividends gone ex in the year to `day` over `price`: the yield the solver carries. None when the
    snapshot carries no dividend record for the name (never zero)."""
    if not hist or hist.get("dividends") is None or not price:
        return None
    since = (date.fromisoformat(day) - timedelta(days=365)).isoformat()
    paid = sum(float(a) for ex, a in hist["dividends"] if since < str(ex) <= day)
    return paid / price


def _solve(spot, strike, years, mid, right, rate, div) -> tuple[float | None, float | None]:
    """(iv, delta) at the dividend yield; (None, None) when no volatility prices the mid."""
    if spot is None or mid is None or years <= 0 or div is None:
        return None, None
    iv = bs.implied_vol(spot, strike, years, mid, right, rate, div)
    if not iv:
        return None, None
    return iv, bs.delta(spot, strike, years, iv, right, rate, div)


# --------------------------------------------------------------------------- linking
def _episodes(rows: list[dict[str, Any]], symbol: str) -> list[dict[str, Any]]:
    """A contract's holdings, oldest first, each from a buy to open off flat to the row that made it flat
    again: {opened_rows, opened, closed (None while held), sold (closed by a sale to close)}."""
    out: list[dict[str, Any]] = []
    pos, cur = 0.0, None
    for r in sorted((r for r in rows if _sym(r.get("symbol")) == symbol), key=_wheel._key):
        pos += _wheel._signed(r)
        if r.get("action") == "Buy to Open" and r.get("transaction_type") == "Trade":
            if cur is None:
                cur = {
                    "opened_rows": [],
                    "opened": str(r.get("executed_at") or ""),
                    "closed": None,
                    "sold": False,
                }
                out.append(cur)
            cur["opened_rows"].append(r)
        if abs(pos) < EPS and cur is not None:
            cur["closed"] = str(r.get("executed_at") or "")
            cur["sold"] = r.get("action") == "Sell to Close" and r.get("transaction_type") == "Trade"
            cur = None
    return out


def _days_apart(a: str, b: str) -> int:
    return abs((date.fromisoformat(a[:10]) - date.fromisoformat(b[:10])).days)


def _link(
    rows: list[dict[str, Any]],
    long_sym: str,
    short_sym: str,
    held: dict[str, float],
    long_exp: str,
    rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The orders behind this long and every short sold against it, and whether they reconcile.

    The chain runs back through every long it was rolled from: in one order (the roll touches both
    contracts, so they share a component), or in two that `trades.<LONG>.continues` names (a sale of the
    old long and a separate buy of the new, which no order links). Its money, and the shorts sold
    against it, start at the chain's first long. A long call sold within `ROLL_WINDOW_DAYS` of this
    one's purchase that nothing links is reported as a possible two-order roll, never stitched on a
    guess: until it is confirmed the chain's cost leaves the old long out."""
    rows = sorted(rows, key=_wheel._key)
    trades = [r for r in rows if r.get("transaction_type") == "Trade" and r.get("order_id") is not None]
    options = [r for r in trades if _wheel._is_option(r)]
    deliveries = [r for r in rows if r.get("transaction_type") == "Receive Deliver" and _wheel._is_option(r)]
    problems: list[str] = []

    # The long's position since it was last flat: when it was bought, and at what.
    current = [e for e in _episodes(options + deliveries, long_sym) if e["closed"] is None]
    opened_rows = current[-1]["opened_rows"] if current else []
    if not opened_rows:
        return {"problems": ["the long's opening predates the history"], "orders": [], "opened_rows": []}
    since = str(opened_rows[0].get("executed_at") or "")

    comp_of, _ = _wheel._components(options)
    home = comp_of[opened_rows[0]["order_id"]]
    homes = {home}
    # Two-order rolls the operator confirmed, followed back (each old long may continue another).
    entries = {
        contract_key(k): v for k, v in ((rules or {}).get("trades") or {}).items() if isinstance(v, dict)
    }
    continued: list[str] = []
    at, opened_at = long_sym, since
    for _ in range(MAX_ROLLS):
        old = _sym((entries.get(contract_key(at)) or {}).get("continues") or "")
        if not old:
            break
        if old in continued or old == long_sym:
            problems.append(f"trades.{at}.continues: {_wheel._label(old)} is already in this chain")
            break
        sold = [
            e
            for e in _episodes(options + deliveries, old)
            if e["sold"] and e["closed"] and _days_apart(e["closed"], opened_at) <= ROLL_WINDOW_DAYS
        ]
        if not sold:
            problems.append(
                f"trades.{at}.continues: no {_wheel._label(old)} sold to close within "
                f"{ROLL_WINDOW_DAYS} days of {opened_at[:10]}; not linked"
            )
            break
        episode = sold[-1]
        homes.add(comp_of[episode["opened_rows"][0]["order_id"]])
        continued.append(old)
        at, opened_at = old, episode["opened"]
    # The chain's first long: the earliest other call bought to open in any of its components, a long it
    # was rolled from. This long's own earlier holdings are not a roll and do not move the start.
    starts = [
        str(r.get("executed_at") or "")
        for r in options
        if comp_of[r["order_id"]] in homes
        and r.get("action") == "Buy to Open"
        and _sym(r.get("symbol")) != long_sym
        and (_wheel.occ(_sym(r.get("symbol"))) or {}).get("right") == "C"
    ]
    chain_since = min([since, *starts])

    # A long call sold close to this purchase that nothing links: possibly a roll in two orders.
    possible_roll = None
    if not continued:
        for sym in sorted({_sym(r.get("symbol")) for r in options} - {long_sym}):
            leg = _wheel.occ(sym) or {}
            if leg.get("right") != "C":
                continue
            for e in _episodes(options + deliveries, sym):
                c = comp_of.get(e["opened_rows"][0]["order_id"]) if e["opened_rows"] else None
                near = e["sold"] and e["closed"] and _days_apart(e["closed"], since) <= ROLL_WINDOW_DAYS
                if (
                    near
                    and c not in homes
                    and (possible_roll is None or e["closed"] > possible_roll["closed"])
                ):
                    possible_roll = {"symbol": sym, "leg": _wheel._label(sym), "closed": e["closed"][:10]}
    rights: dict[int, set] = defaultdict(set)
    short_only: dict[int, bool] = defaultdict(lambda: True)
    first: dict[int, str] = {}
    expiries: dict[int, set] = defaultdict(set)
    for r in options:
        c = comp_of[r["order_id"]]
        leg = _wheel.occ(_sym(r.get("symbol"))) or {}
        rights[c].add(leg.get("right"))
        expiries[c].add(leg.get("expiration"))
        if str(r.get("action") or "") not in SHORT_SIDE:
            short_only[c] = False
        first.setdefault(c, str(r.get("executed_at") or ""))
    if any(rights[h] != {"C"} for h in homes):
        problems.append("the long's order chain holds legs other than calls")
    linked = homes | {
        c
        for c in rights
        if c not in homes
        and rights[c] == {"C"}
        and short_only[c]
        and first[c] >= chain_since
        and all(str(e) < long_exp for e in expiries[c])
    }
    mine = [r for r in options if comp_of[r["order_id"]] in linked]
    symbols = {_sym(r.get("symbol")) for r in mine}
    delivered = [
        r for r in deliveries if _sym(r.get("symbol")) in symbols and str(r.get("executed_at")) >= chain_since
    ]
    for r in delivered:
        if str(r.get("transaction_sub_type") or "") in _wheel.SHARE_MOVING:
            problems.append(
                f"{_wheel._label(_sym(r.get('symbol')))} was assigned or exercised on {_wheel._day(r)}: "
                "the PMCC broke there; check it by hand"
            )
    replay: dict[str, float] = defaultdict(float)
    for r in mine + delivered:
        replay[_sym(r.get("symbol"))] += _wheel._signed(r)
    for s in sorted(symbols | {long_sym, short_sym}):
        want = held.get(s, 0.0)
        if abs(replay.get(s, 0.0) - want) > EPS:
            problems.append(
                f"{_wheel._label(s)}: the history replays to {replay.get(s, 0.0):+g}, {want:+g} held"
            )
    return {
        "problems": problems,
        "rows": mine,
        "delivered": delivered,
        "opened_rows": opened_rows,
        "since": since[:10],
        "chain_since": chain_since[:10],
        "continued": continued,
        "possible_roll": possible_roll,
        "orders": sorted({r["order_id"] for r in mine}, key=str),
    }


def contract_key(symbol: Any) -> str:
    """A contract as a `trades` key matches it: spaces dropped (the broker pads the root), upper case."""
    return "".join(str(symbol or "").split()).upper()


def contract_intent(rules: dict[str, Any], long_sym: str) -> str | None:
    """The "pmcc" or "diagonal" intent `trades` sets on this exact long, else None. A rolled long is a
    new PMCC (user, 2026-11-06): the long it was rolled from does not lend it an intent; it is decided
    by its own entry, or by an intent set on its own contract."""
    by_key = {contract_key(k): v for k, v in (rules.get("trades") or {}).items() if isinstance(v, dict)}
    intent = (by_key.get(contract_key(long_sym)) or {}).get("intent")
    return intent if intent in ("pmcc", "diagonal") else None


def _shorts(rows: list[dict[str, Any]], delivered: list[dict[str, Any]], short_sym: str) -> list[dict]:
    """Every call sold against the long, oldest first, with its cash and how it ended."""
    by_sym: dict[str, list] = defaultdict(list)
    for r in rows:
        if r.get("action") in SHORT_SIDE:
            by_sym[_sym(r.get("symbol"))].append(r)
    out = []
    for sym, legs in by_sym.items():
        legs.sort(key=_wheel._key)
        sold = [r for r in legs if r.get("action") == "Sell to Open"]
        ended = [
            str(r.get("transaction_sub_type") or "").lower()
            for r in delivered
            if _sym(r.get("symbol")) == sym
        ]
        status = (
            "open" if sym == short_sym else ("bought back" if any(_wheel._closes(r) for r in legs) else None)
        )
        if ended and sym != short_sym:
            status = ended[-1]
        out.append(
            {
                "symbol": sym,
                "leg": _wheel._label(sym),
                "sold": str(_wheel._day(sold[0])) if sold else None,
                "sold_price": _num(sold[0].get("price")) if sold else None,
                "cash": _wheel._sum(_num(r.get("value")) for r in legs),
                "fees": sum(_wheel._fees(r) for r in legs),
                "status": status or "unknown",
            }
        )
    return sorted(out, key=lambda s: (s["sold"] or "", s["symbol"]))


# --------------------------------------------------------------------------- the long at entry
def _entry_delta(
    opened_rows: list[dict[str, Any]],
    long_leg: dict[str, Any],
    hist: dict[str, Any] | None,
    session: date,
    rate: float,
    recorded: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The long's delta at entry, per opening fill: the delta recorded at the fill when there is one
    (entrydelta.py, `snapshot.entry_deltas`), else solved from the fill and that day's close; the
    lowest when it was bought in more than one fill (every lot must clear the bar). Unknown, with why,
    otherwise. A recorded delta stands through a later split: a delta has no share basis."""
    out: dict[str, Any] = {"delta": None, "why": None, "fills": []}
    if not opened_rows:
        out["why"] = "no opening fill in the history"
        return out
    recorded = recorded or {}
    strike, exp = float(long_leg["strike"]), str(long_leg["expiration"])
    deltas = []
    for r in opened_rows:
        day = str(_wheel._day(r))
        price = _num(r.get("price"))
        rec = recorded.get(str(r.get("id") or ""))
        if rec and _num(rec.get("delta")) is not None:
            out["fills"].append(
                {
                    "date": day,
                    "price": price,
                    "close": None,
                    "div_yield": rec.get("div_yield"),
                    "iv": rec.get("iv"),
                    "delta": rec["delta"],
                    "basis": "recorded",
                    "spot": rec.get("spot"),
                    "gap_seconds": rec.get("gap_seconds"),
                    "delta_low": rec.get("delta_low"),
                    "delta_high": rec.get("delta_high"),
                    "broker_delta": rec.get("broker_delta"),
                }
            )
            deltas.append(float(rec["delta"]))
            continue
        close = _num(((hist or {}).get("closes") or {}).get(day))
        fill = {"date": day, "price": price, "close": close, "div_yield": None, "iv": None, "delta": None}
        fill["basis"] = "close"
        out["fills"].append(fill)
        if not hist:
            out["why"] = "the snapshot carries no close for the name (underlying_history, v13)"
            continue
        if close is None:
            out["why"] = f"no close for {day} in the snapshot"
            continue
        if any(day < str(s) <= session.isoformat() for s in hist.get("splits") or []):
            out["why"] = f"a split since {day}: that day's close is on another share basis"
            continue
        fill["div_yield"] = _yield(hist, day, close)
        years = (date.fromisoformat(exp) - date.fromisoformat(day)).days / 365.0
        fill["iv"], fill["delta"] = _solve(close, strike, years, price, "C", rate, fill["div_yield"])
        if fill["delta"] is None:
            out["why"] = f"the {price} fill on {day} is priced by no volatility at the {close} close"
            continue
        deltas.append(fill["delta"])
    if deltas and len(deltas) == len(opened_rows):
        out["delta"], out["why"] = min(deltas), None
    return out


def entry_band(entry: dict[str, Any], floor: float) -> str | None:
    """Words for a recorded delta at entry whose fill-time band straddles `floor`, else None. The band
    is the lowest fill's: the one the recognition rests on."""
    recorded = [
        f for f in entry.get("fills") or [] if f.get("basis") == "recorded" and f.get("delta") is not None
    ]
    if not recorded:
        return None
    low = min(recorded, key=lambda f: float(f["delta"]))
    lo, hi = _num(low.get("delta_low")), _num(low.get("delta_high"))
    if lo is None or hi is None or float(low["delta"]) != entry["delta"]:
        return None
    if lo < floor <= hi:
        gap = _num(low.get("gap_seconds"))
        when = f" over the {gap:.0f} s to the snapshot" if gap is not None else ""
        return f"close to the floor: {lo:.2f}-{hi:.2f} at the underlying's likely move{when}"
    return None


# --------------------------------------------------------------------------- roll candidates
def _quoted_expiries(
    underlying: str,
    income: dict[str, Any] | None,
    roll_chains: dict[str, Any] | None,
    roll_out: dict[str, Any] | None = None,
):
    """{expiry: [two-sided call quotes with mids]} from every quoted chain the snapshot carries for
    the name: the roll-out chains read for its short (v14), first, since they are the session's own
    quotes; then the income reading's cycle monthly and covered-call expiry, and any roll chain. A
    strike quoted by two sources keeps the first."""
    out: dict[str, list] = defaultdict(list)
    sources = [(c["expiration"], c["options"]) for c in _wheel.roll_out_for(roll_out, underlying, "C")]
    entry = ((income or {}).get("symbols") or {}).get(underlying) if not (income or {}).get("error") else None
    if entry:
        sources.append((entry.get("expiry"), entry.get("options")))
        cc = entry.get("cc") or {}
        sources.append((cc.get("expiry"), cc.get("options")))
    for key, chain in (roll_chains or {}).items():
        name, _, exp = str(key).partition("|")
        if name.upper() == underlying.upper() and isinstance(chain, dict):
            sources.append((exp, chain.get("options")))
    for exp, options in sources:
        if not exp:
            continue
        for o in options or []:
            if o.get("right") != "C":
                continue
            k, bid, ask = _num(o.get("strike")), _num(o.get("bid")), _num(o.get("ask"))
            if k is None or bid is None or ask is None or ask <= 0 or bid < 0:
                continue  # two-sided quotes only: a candidate is priced, never modelled
            if all(abs(q["strike"] - k) > EPS for q in out[str(exp)]):
                out[str(exp)].append({"strike": k, "bid": bid, "ask": ask, "mid": (bid + ask) / 2.0})
    return out


def roll_candidates(
    view: dict[str, Any], quotes: dict[str, list], session: date, rate: float, cfg: dict
) -> dict:
    """Up to `expiries` rolls of the short, one per quoted expiry: the most premium at a delta of at
    most `max_delta`, costing no more than `max_debit` a share, keeping the width check passing."""
    short, long = view["short"], view["long"]
    out: dict[str, Any] = {"candidates": [], "why": None, "quoted_expiries": []}
    later = sorted(e for e in quotes if short["expiration"] < e < long["expiration"])[: int(cfg["expiries"])]
    out["quoted_expiries"] = later
    if not later:
        out["why"] = "the snapshot quotes no call expiry after the short's and before the long's"
        return out
    if short["mid"] is None:
        out["why"] = "the short has no mark to buy it back at"
        return out
    if view["div_yield"] is None:
        out["why"] = "no dividend yield for the name in the snapshot: no delta can be solved"
        return out
    spot, net_ps = view["spot"], (view["width"] or {}).get("net_cost_per_share")
    for exp in later:
        years = (date.fromisoformat(exp) - session).days / 365.0
        best = None
        for q in quotes[exp]:
            _, d = _solve(spot, q["strike"], years, q["mid"], "C", rate, view["div_yield"])
            if d is None or d > float(cfg["max_delta"]) + EPS:
                continue
            net = q["mid"] - short["mid"]
            if net < -float(cfg["max_debit"]) - EPS:
                continue
            if net_ps is not None and not q["strike"] - long["strike"] > net_ps - net + EPS:
                continue
            if best is None or d > best["delta"]:
                best = {"expiration": exp, "open": q, "delta": d, "close_price": short["mid"], "net": net}
        if best:
            out["candidates"].append(best)
    if not out["candidates"]:
        out["why"] = (
            f"no quoted call on {', '.join(later)} at or under {float(cfg['max_delta']):.2f} delta "
            f"pays the {short['mid']:.2f} buy-back within {float(cfg['max_debit']):.2f} and keeps the width"
        )
    return out


# --------------------------------------------------------------------------- one name
def judge(
    underlying: str,
    raw_legs: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    session: date,
    metrics: dict[str, Any] | None,
    snapshot: dict[str, Any],
    rules: dict[str, Any],
) -> dict[str, Any] | None:
    """The PMCC view of one name, or None when its calls are not a PMCC's shape."""
    cfg = rules["pmcc"]
    pair = candidate(raw_legs, session, cfg["recognize"])
    if pair is None:
        return None
    metrics = metrics or {}
    rate = float(rules["pricing"]["rate"])
    spot = _num(metrics.get("last"))
    long_raw, short_raw = pair["long"], pair["short"]
    long_sym, short_sym = _sym(long_raw.get("symbol")), _sym(short_raw.get("symbol"))
    qty = _num(long_raw["quantity"])
    held = {_sym(leg.get("symbol")): _num(leg.get("quantity")) or 0.0 for leg in raw_legs}
    intent = ((rules.get("positions") or {}).get(underlying) or {}).get("intent")
    hist = (snapshot.get("underlying_history") or {}).get(underlying)
    div_today = _yield(hist, session.isoformat(), spot)

    linked = _link(rows, long_sym, short_sym, held, str(long_raw["expiration"]), rules)
    reconciled = not linked["problems"]
    intent_source = "symbol" if intent in ("pmcc", "diagonal") else None
    on_contract = contract_intent(rules, long_sym)
    if on_contract is not None:
        intent, intent_source = on_contract, "contract"
    # The long's days left when it was bought (shown, never a gate); today's when the purchase is not in
    # the history.
    opened = str(_wheel._day(linked["opened_rows"][0])) if linked["opened_rows"] else None
    long_days = _days(str(long_raw["expiration"]), date.fromisoformat(opened) if opened else session)

    # ---- the long
    lk, lexp = float(long_raw["strike"]), str(long_raw["expiration"])
    lmid = _num(long_raw.get("mid")) if long_raw.get("priced", True) else None
    ldays = _days(lexp, session)
    _, ldelta = _solve(spot, lk, ldays / 365.0, lmid, "C", rate, div_today)
    lintr = bs.intrinsic(spot, lk, "C") if spot else None
    lext = None if lmid is None or lintr is None else lmid - lintr
    entry = _entry_delta(linked["opened_rows"], long_raw, hist, session, rate, snapshot.get("entry_deltas"))
    rl = cfg["roll_long"]
    roll_long = []
    if ldays <= int(rl["days_left"]):
        roll_long.append(f"{ldays} days left (roll at {int(rl['days_left'])})")
    if ldelta is not None and ldelta < float(rl["min_delta"]):
        roll_long.append(f"{ldelta:.2f} delta (under {float(rl['min_delta']):.2f})")
    long_view = {
        "symbol": long_sym,
        "leg": _wheel._label(long_sym),
        "strike": lk,
        "expiration": lexp,
        "dte": ldays,
        "quantity": qty,
        "mid": lmid,
        "opened": opened,
        "days_at_entry": long_days if opened else None,
        "delta_at_entry": entry["delta"],
        "delta_at_entry_why": entry["why"],
        "entry_fills": entry["fills"],
        "delta": ldelta,
        "intrinsic": lintr,
        "extrinsic": lext,
        "extrinsic_share": None if lext is None or not lmid else lext / lmid,
        "roll": roll_long,
        "roll_delta_unknown": ldelta is None,
    }

    # ---- recognition
    floor = float(cfg["recognize"]["entry_min_delta"])
    where = " (set on this long)" if intent_source == "contract" else ""
    if intent == "diagonal":
        recognized, by = False, f"intent: diagonal{where}"
    elif intent == "pmcc":
        recognized, by = True, f"intent: pmcc{where}"
    elif entry["delta"] is None:
        recognized, by = False, f"delta at entry unknown ({entry['why']})"
    elif entry["delta"] >= floor - EPS:
        recognized, by = True, f"entered at {entry['delta']:.2f} delta (at least {floor:.2f})"
    else:
        recognized, by = False, f"entered at {entry['delta']:.2f} delta (under {floor:.2f}): a diagonal"
    # Which of the three it is. `recognized` stays a bool so an unknown keeps the diagonal rules; this
    # says whether "not recognised" was decided (a diagonal) or could not be (unknown, for now).
    undecided = intent not in ("pmcc", "diagonal") and entry["delta"] is None
    recognition = "pmcc" if recognized else ("unknown" if undecided else "diagonal")
    # A recorded delta whose fill-time band straddles the floor is said so: the underlying moved between
    # the fill and the snapshot it was read from, so the side of the floor it lands on is not certain.
    if intent not in ("pmcc", "diagonal") and entry["delta"] is not None:
        band = entry_band(entry, floor)
        if band:
            by += f"; {band}"

    # ---- money
    cash_rows = linked.get("rows") or []
    net_cash = _wheel._sum(_num(r.get("value")) for r in cash_rows) if cash_rows else None
    fees = sum(_wheel._fees(r) for r in cash_rows)
    shorts = _shorts(cash_rows, linked.get("delivered") or [], short_sym)
    long_cash = (
        _wheel._sum(_num(r.get("value")) for r in cash_rows if r.get("action") not in SHORT_SIDE)
        if cash_rows
        else None
    )
    premiums = _wheel._sum(s["cash"] for s in shorts) if shorts else None
    if reconciled and net_cash is not None:
        net_cost, basis = -net_cash, "linked orders"
    else:
        # As far as the open legs go: the positions' own prices, which miss every earlier short.
        lo, so = _num(long_raw.get("open_price")), _num(short_raw.get("open_price"))
        net_cost = None if lo is None or so is None else (lo - so) * MULTIPLIER * qty
        basis = "open_legs"
    sk, sexp = float(short_raw["strike"]), str(short_raw["expiration"])
    width = sk - lk
    net_ps = None if net_cost is None else net_cost / (MULTIPLIER * qty)
    passes = None if net_ps is None else width > net_ps + EPS
    warn = float(cfg["width"]["warn_share"])
    share = None if net_ps is None or width <= EPS else net_ps / width
    width_view = {
        "width": width,
        "net_cost": net_cost,
        "net_cost_per_share": net_ps,
        "basis": basis,
        "passes": passes,
        "cost_share": share,
        "warn_share": warn,
        "thin": None if share is None else bool(passes) and share > warn + EPS,
        "pnl_if_assigned": None if net_ps is None else (width - net_ps) * MULTIPLIER * qty,
        "pnl_if_assigned_net": None
        if net_ps is None or basis != "linked orders"
        else (width - net_ps) * MULTIPLIER * qty + fees,
        "assumes": "the long exercised to deliver the shares; selling it instead also keeps its time value",
    }

    # ---- the short
    smid = _num(short_raw.get("mid")) if short_raw.get("priced", True) else None
    sdays = _days(sexp, session)
    sintr = bs.intrinsic(spot, sk, "C") if spot else None
    sext = None if smid is None or sintr is None else smid - sintr
    siv, _ = _solve(spot, sk, sdays / 365.0, smid, "C", rate, div_today)
    p_itm = bs.prob_itm(spot, sk, sdays / 365.0, siv, "C", rate, div_today) if siv else None
    band, band_why = (
        _wheel._band(snapshot.get("skate_bands"), underlying, "C", sexp, spot)
        if snapshot.get("skate_bands")
        else (None, "band not in the snapshot")
    )
    beyond = None
    if band:
        beyond = {
            w: None if band.get(w) is None else sk >= band[w] - EPS
            for w in ("fixed", "by_expiry")
            if w in band
        }
    cash_roots = {r.upper() for r in rules["expiry"]["cash_settled_roots"]}
    asg = cfg["assignment"]
    itm = sintr is not None and sintr > 0
    exposure: dict[str, Any] = {"near_zero": None, "dividend": None, "exposed": None, "why": None}
    xd = str(metrics.get("ex_dividend_date") or "")[:10]
    div_amt = _num(metrics.get("dividend_rate_per_share"))
    if underlying.upper() in cash_roots:
        exposure.update(
            near_zero=False, dividend=False, exposed=False, why="cash-settled: no early assignment"
        )
    elif sext is not None:
        exposure["near_zero"] = itm and sext < float(asg["near_zero_extrinsic"]) - EPS
        if not itm:
            exposure["dividend"] = False
        elif xd and session.isoformat() < xd <= sexp:
            exposure["dividend"] = None if div_amt is None else sext < div_amt - EPS
            exposure["ex_dividend_date"] = xd
            exposure["dividend_amount"] = div_amt
        else:
            exposure["dividend"] = False
        exposure["exposed"] = bool(exposure["near_zero"]) or bool(exposure["dividend"])
        if exposure["near_zero"]:
            exposure["why"] = (
                f"in the money with {sext:.2f} of time value (under {float(asg['near_zero_extrinsic']):.2f})"
            )
        elif exposure["dividend"]:
            exposure["why"] = (
                f"in the money with {sext:.2f} of time value, under the {div_amt:.2f} dividend going ex {xd}"
            )
        elif exposure["dividend"] is None:
            exposure["why"] = f"in the money over the {xd} ex-date; the dividend amount is unknown"
    ed = str(metrics.get("earnings_date") or "")[:10]
    # The broker keeps the last report's date until it lists the next one, so a date can already be
    # out: before today, or today before the open (the earnings rule's own test). Out is neither
    # inside the short's life nor after it.
    from cherrypick.manage.evaluate import before_open  # evaluate imports this module

    today = session.isoformat()
    out = bool(ed) and (ed < today or (ed == today and before_open(metrics.get("earnings_time"))))
    if not ed:
        earnings = {"date": None, "inside": None, "past": None, "why": "no report date in the snapshot"}
    elif out:
        earnings = {
            "date": ed,
            "inside": False,
            "past": True,
            "why": f"last report {ed}, already out; the next date is not in the snapshot yet",
        }
    else:
        earnings = {"date": ed, "inside": ed <= sexp, "past": False}
    short_view = {
        "symbol": short_sym,
        "leg": _wheel._label(short_sym),
        "strike": sk,
        "expiration": sexp,
        "dte": sdays,
        "quantity": -qty,
        "mid": smid,
        "open_price": _num(short_raw.get("open_price")),
        "tested": None if spot is None else spot >= sk,
        "intrinsic": sintr,
        "extrinsic": sext,
        "prob_itm": p_itm,
        "band": band,
        "band_missing": band_why,
        "beyond_band": beyond,
        "assignment": exposure,
        "earnings": earnings,
    }

    # ---- the shorts' P&L to date, net of their fees (signed negative, as the broker writes them),
    # on the long's own cost. "P&L" is net across the suite; the wheel's yield is net too.
    open_short = next((s for s in shorts if s["symbol"] == short_sym), None)
    short_pnl = None
    if premiums is not None and smid is not None and open_short is not None:
        short_pnl = premiums - smid * MULTIPLIER * qty + sum(s["fees"] for s in shorts)
    days = None if long_view["opened"] is None else (session - date.fromisoformat(long_view["opened"])).days
    long_cost = None if long_cash is None else -long_cash
    yld = None
    if (
        reconciled
        and short_pnl is not None
        and long_cost
        and long_cost > 0
        and days
        and days >= _wheel.MIN_YIELD_DAYS
    ):
        yld = short_pnl / long_cost * 365.0 / days

    view = {
        "underlying": underlying,
        "recognized": recognized,
        "recognition": recognition,
        # The call time spread's structure, whatever it is recognised as: a calendar sells the long's
        # own strike, a diagonal a higher one. The PMCC tab lists PMCCs and undecided ones, and names
        # the rest by this.
        "structure": "calendar" if abs(sk - lk) < EPS else "diagonal",
        "recognized_by": by,
        "intent": intent,
        "intent_source": intent_source,
        # The `trades` key that sets this PMCC's intent: the long's contract, as the broker spells it.
        "intent_key": long_sym,
        "reconciled": reconciled,
        "problems": list(dict.fromkeys(linked["problems"])),
        "spot": spot,
        "div_yield": div_today,
        "long": long_view,
        "short": short_view,
        "width": width_view,
        "shorts": shorts,
        "orders": linked.get("orders") or [],
        # The chain: the first long's purchase it runs from, the longs a confirmed two-order roll
        # stitched on (`trades.<LONG>.continues`), and a long sold near this purchase that nothing links.
        "chain_since": linked.get("chain_since"),
        "continued": linked.get("continued") or [],
        "possible_roll": linked.get("possible_roll"),
        "net_cash": net_cash,
        "fees": fees,
        "long_cost": long_cost,
        "premiums": premiums,
        "skate_yield": {
            "value": yld,
            "capped": yld is not None and abs(yld) > _wheel.YIELD_CAP,
            "basis": "comparison figure: the shorts' net P&L to date (their fees subtracted) / the long's "
            "cost x 365 / days held",
        },
        "rolls": None,
    }
    if recognized and reconciled and (width_view["passes"] is False or exposure["exposed"]):
        roll_out = snapshot.get("roll_out_chains")
        quotes = _quoted_expiries(underlying, snapshot.get("income"), snapshot.get("roll_chains"), roll_out)
        view["rolls"] = roll_candidates(view, quotes, session, rate, cfg["roll"])
        view["rolls"]["quoted_at"] = (snapshot.get("income") or {}).get("taken_at")
        if _wheel.roll_out_for(roll_out, underlying, "C"):
            view["rolls"]["chains_taken_at"] = roll_out.get("taken_at")
    return view


# --------------------------------------------------------------------------- the account
def build(
    underlyings: list[dict[str, Any]],
    history: dict[str, list[dict[str, Any]]] | None,
    session: date,
    metrics: dict[str, Any] | None,
    snapshot: dict[str, Any],
    rules: dict[str, Any],
) -> dict[str, Any]:
    """The account's PMCC view: one entry per name whose calls are a PMCC's shape, recognised or not."""
    history, metrics = history or {}, metrics or {}
    out = []
    for u in underlyings or []:
        name = str(u.get("underlying") or "").upper()
        view = judge(
            name, u.get("legs") or [], history.get(name) or [], session, metrics.get(name), snapshot, rules
        )
        if view is not None:
            out.append(view)
    out.sort(key=lambda v: (not v["recognized"], v["underlying"]))
    return {
        "positions": out,
        "underlying_history_error": snapshot.get("underlying_history_error")
        if "underlying_history" in snapshot
        else "the snapshot carries no underlying history (taken before v13)",
        "bands_missing": None if snapshot.get("skate_bands") else "band not in the snapshot",
    }
