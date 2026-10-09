"""cherrypick.core.impliedvar — model-free implied variance over a whole option strip.

Cboe's VIX arithmetic, generalised to any expiration and any European-style chain: the variance the
market prices for one expiration, read from every out-of-the-money option out to the point where the
bids run dry, not just the two at-the-money legs `structures.expected_move` reads. A short-premium
module recording this at entry and the realised variance to expiry has the premium it sold in one
number per trade, and the wings are in it — which is where a broken-wing or a fly carries its risk.

Reproduces the worked example in Cboe's VIX methodology (v6.0, Appendix 3–5: 2022-09-27 10:45:15
ET, near/next variances 0.019233906 / 0.019423884, VIX 13.93) to the published digits;
`tests/test_impliedvar.py` holds the example's full strip.

Four things the formula alone does not say, and each is a way to produce a number that looks real
and is not:

* **A wing is complete only if it ran dry.** Cboe walks out from K0 and stops after two consecutive
  zero bids. The stream cache holds a strike WINDOW around the money, so a wing can instead stop
  because the quotes ran out — and a variance missing its tail reads low, never high. Every result
  says, per wing, which happened (`complete`); a truncated strip is a lower bound and must be
  recorded as one.
* **One instant.** A strip assembled from quotes of different ages is no market's price. The cache
  reader takes `now_ts` and `max_age_seconds` and drops what is older (a gap in the strip, never a
  stale mark), and `usable_quote` rejects crossed and ask-less rows as everywhere else in the suite.
* **No extrapolation.** `constant_maturity` interpolates between two expirations that bracket the
  target and refuses otherwise. Cboe's index never extrapolates either.
* **European exercise.** The replication argument assumes no early exercise. An American chain
  (SPY, TQQQ, VXX) gives an approximation, and the reader reports `exercise_style` so a caller can
  say so rather than discover it.

The risk-free rate is a required argument: Cboe interpolates Treasury CMT yields, and the suite does
not carry a curve. Its effect is second order (e^{rT} on each price and on the forward), but a caller
states the rate it used rather than inheriting a silent default.

Pure arithmetic plus one read-only cache reader (`strip_from_cache`); no broker, no network. The CLI
(`python -m cherrypick.core.impliedvar check`) compares the arithmetic against a published Cboe
index from the same cache.

**The cache does not hold a full strip unless someone asks for one.** The producer serves a ±30
strike window per declared expiration, and widening SPX's `window_hints` would widen every SPX window
(0DTE included, at four event types each). `declare` asks for exactly the two expirations that
bracket a target instead: their chain metadata through `expirations`, and their whole strip, 0.5x to
1.2x spot, through a `leg_sources` query over the cache itself, at Quote and Greeks only. Neither key
is in the producer's launch snapshot, so declaring or clearing never recycles it. The cost (about
2,000 subscriptions for an SPXW weekly pair) is NOT in `streamrequests.estimate_subscriptions`, which
models windows only. It is a validation instrument, declared for a session and cleared after.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta

from cherrypick.core import calendar as _cal
from cherrypick.core import streamcache

MINUTES_PER_YEAR = 525_600
MINUTES_PER_DAY = 1_440

# The strip `declare` subscribes, as fractions of spot. Wide enough that a 9- to 30-day SPX wing runs
# dry inside it (the strikes past it are the far listings at 25- to 100-point spacing); a wing that
# does not is still reported incomplete, never padded.
STRIP_BOUNDS = (0.5, 1.2)


def years_between(now_ts: float, expires_ts: float) -> float:
    """Time to expiration in years, to the minute as Cboe counts it (calendar time, holidays
    included — the market's clock runs through them)."""
    return (expires_ts - now_ts) / 60.0 / MINUTES_PER_YEAR


def _refuse(reason: str, **extra) -> dict:
    return {"ok": False, "reason": reason, **extra}


def _by_strike(quotes: Iterable[Mapping]) -> dict[float, dict[str, dict]]:
    """{strike: {"call": {bid, ask, mid}, "put": {...}}} from flat quote rows. The mid is recomputed
    from bid and ask, as Cboe defines it, whatever mid a feed supplied."""
    out: dict[float, dict[str, dict]] = {}
    for q in quotes:
        bid, ask = float(q["bid"]), float(q["ask"])
        side = "call" if str(q["option_type"]).lower().startswith("c") else "put"
        out.setdefault(float(q["strike"]), {})[side] = {"bid": bid, "ask": ask, "mid": (bid + ask) / 2.0}
    return out


def _walk(strikes: list[float], legs: dict, side: str) -> tuple[list[float], bool]:
    """Out from K0 along `strikes` (already ordered away from the money): skip a zero bid, stop
    after two consecutive ones. Returns the included strikes and whether the walk ran dry (True)
    or ran out of quotes first (False). A strike with no `side` quote at all is a gap in the data,
    not a zero bid, and neither counts toward nor breaks the two-zero run."""
    included, zeros = [], 0
    for k in strikes:
        leg = legs[k].get(side)
        if leg is None:
            continue
        if leg["bid"] <= 0:
            zeros += 1
            if zeros == 2:
                return included, True
            continue
        zeros = 0
        included.append(k)
    return included, False


def _wing(strikes: list[float], dry: bool) -> dict:
    return {"strikes": len(strikes), "last_strike": strikes[-1] if strikes else None, "complete": dry}


def single_term(quotes: Iterable[Mapping], *, years: float, rate: float, detail: bool = False) -> dict:
    """Model-free implied variance for ONE expiration (Cboe single-term method).

    `quotes` are flat rows `{"strike", "option_type" ("call"/"put"), "bid", "ask"}` for one
    expiration, zero bids included (they mark where a wing ends). `years` is time to expiration
    (`years_between`), `rate` the continuously-compounded risk-free rate as a decimal.

    Returns `{"ok": True, "variance", "vol", "forward", "k0", "years", "rate", "strikes", "put_wing",
    "call_wing", "complete"}`: `variance` is annualised sigma^2, `vol` is 100*sqrt(variance) in the
    index's points, each wing is `{"strikes", "last_strike", "complete"}`, and `complete` holds only
    when both wings ran dry. With `detail`, `contributions` lists every strike's
    `{strike, type, mid, dk, contribution}` as Cboe's Appendix 5 does.

    Refuses (`ok: False`, `reason`) rather than guess: `nonpositive_time`, `no_parity_strike` (no
    strike carries a call and a put both bid), `forward_below_strip`, `k0_unpriced` (the K0 call or
    put has no quote), `nonpositive_variance`.

    The forward is struck where |call mid - put mid| is smallest among strikes with both legs bid; a
    tie (rare on real quotes) takes the lower strike.
    """
    if years <= 0:
        return _refuse("nonpositive_time", years=years)
    legs = _by_strike(quotes)
    growth = math.exp(rate * years)

    def both_bid(s: dict) -> bool:
        return "call" in s and "put" in s and s["call"]["bid"] > 0 and s["put"]["bid"] > 0

    paired = [k for k, s in legs.items() if both_bid(s)]
    if not paired:
        return _refuse("no_parity_strike")
    atm = min(sorted(paired), key=lambda k: abs(legs[k]["call"]["mid"] - legs[k]["put"]["mid"]))
    forward = atm + growth * (legs[atm]["call"]["mid"] - legs[atm]["put"]["mid"])

    strikes = sorted(legs)
    below = [k for k in strikes if k <= forward]
    if not below:
        return _refuse("forward_below_strip", forward=forward)
    k0 = below[-1]
    if "call" not in legs[k0] or "put" not in legs[k0]:
        return _refuse("k0_unpriced", forward=forward, k0=k0)

    puts, put_dry = _walk([k for k in reversed(strikes) if k < k0], legs, "put")
    calls, call_dry = _walk([k for k in strikes if k > k0], legs, "call")

    strip = [(k, "put", legs[k]["put"]["mid"]) for k in reversed(puts)]
    strip.append((k0, "put/call", (legs[k0]["call"]["mid"] + legs[k0]["put"]["mid"]) / 2.0))
    strip += [(k, "call", legs[k]["call"]["mid"]) for k in calls]

    total, rows = 0.0, []
    for i, (k, kind, mid) in enumerate(strip):
        if len(strip) == 1:
            dk = 0.0
        elif i == 0:
            dk = strip[1][0] - k
        elif i == len(strip) - 1:
            dk = k - strip[i - 1][0]
        else:
            dk = (strip[i + 1][0] - strip[i - 1][0]) / 2.0
        contribution = dk / (k * k) * growth * mid
        total += contribution
        if detail:
            rows.append({"strike": k, "type": kind, "mid": mid, "dk": dk, "contribution": contribution})

    variance = 2.0 / years * total - (forward / k0 - 1.0) ** 2 / years
    if variance <= 0:
        return _refuse("nonpositive_variance", forward=forward, k0=k0, variance=variance)

    out = {
        "ok": True,
        "variance": variance,
        "vol": 100.0 * math.sqrt(variance),
        "forward": forward,
        "k0": k0,
        "years": years,
        "rate": rate,
        "strikes": len(strip),
        "put_wing": _wing(puts, put_dry),
        "call_wing": _wing(calls, call_dry),
        "complete": put_dry and call_dry,
    }
    if detail:
        out["contributions"] = rows
    return out


def constant_maturity(near: Mapping, next_: Mapping, *, days: float) -> dict:
    """Cboe's constant-maturity interpolation of two `single_term` results to `days` calendar days:
    100 * sqrt{[T1 s1^2 (N2 - Nt)/(N2 - N1) + T2 s2^2 (Nt - N1)/(N2 - N1)] * N365 / Nt}, in minutes.

    Refuses `term_unavailable` when either term refused, and `not_bracketed` unless
    near.years <= target <= next.years with near < next — this interpolates, never extrapolates.
    `complete` holds only when both terms' strips are complete.
    """
    if not near.get("ok") or not next_.get("ok"):
        return _refuse("term_unavailable")
    n1 = near["years"] * MINUTES_PER_YEAR
    n2 = next_["years"] * MINUTES_PER_YEAR
    nt = days * MINUTES_PER_DAY
    if not (n1 <= nt <= n2 and n1 < n2):
        return _refuse("not_bracketed", near_days=n1 / MINUTES_PER_DAY, next_days=n2 / MINUTES_PER_DAY)
    w1, w2 = (n2 - nt) / (n2 - n1), (nt - n1) / (n2 - n1)
    weighted = near["years"] * near["variance"] * w1 + next_["years"] * next_["variance"] * w2
    variance = weighted * MINUTES_PER_YEAR / nt
    return {
        "ok": True,
        "variance": variance,
        "vol": 100.0 * math.sqrt(variance),
        "days": days,
        "complete": bool(near["complete"] and next_["complete"]),
    }


def expiry_fields(conn: sqlite3.Connection, symbol: str, expiration: str, root: str) -> dict:
    """`expires_at` (epoch seconds), `exercise_style` and `settlement_type` for one (underlying,
    expiration, root), from the broker's own chain row — the settlement instant is the broker's word
    (16:00 ET for a P.M.-settled index weekly, the open for an A.M.-settled monthly)."""
    for (raw,) in conn.execute(
        "SELECT data_json FROM stream_chain WHERE expiration = ? AND underlying_symbol = ?",
        (expiration, symbol),
    ):
        try:
            opt = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if streamcache.occ_root(opt.get("symbol")) != root or not opt.get("expires_at"):
            continue
        try:
            expires = datetime.fromisoformat(str(opt["expires_at"]).replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        return {
            "expires_at": expires,
            "exercise_style": opt.get("exercise_style"),
            "settlement_type": opt.get("settlement_type"),
        }
    return {"expires_at": None, "exercise_style": None, "settlement_type": None}


def strip_from_cache(
    conn: sqlite3.Connection,
    symbol: str,
    expiration: str,
    root: str,
    *,
    now_ts: float,
    max_age_seconds: float,
) -> dict:
    """One expiration's quotes from the stream cache, shaped for `single_term`.

    `{"quotes": [...], "listed": n, "missing": n, "expires_at", "exercise_style",
    "settlement_type"}` — `missing` counts listed options with no usable quote at `now_ts` (absent,
    stale past `max_age_seconds`, or crossed). They are gaps in the strip, never zero bids; a wing
    that runs into them before running dry reads `complete: False`.
    """
    chain = streamcache.chain_for_expiration(conn, symbol, expiration, root)
    by_sym = {e["streamer_symbol"]: e for e in chain}
    quotes = []
    syms = list(by_sym)
    for i in range(0, len(syms), 900):
        chunk = syms[i : i + 900]
        placeholders = ", ".join("?" * len(chunk))
        cur = conn.cursor()
        cur.row_factory = sqlite3.Row
        cur.execute(
            f"SELECT symbol, bid, ask, mid, updated_at FROM stream_quotes WHERE symbol IN ({placeholders})",
            chunk,
        )
        for row in cur:
            q = streamcache.usable_quote(row, now_ts, max_age_seconds)
            if q is None:
                continue
            e = by_sym[row["symbol"]]
            quotes.append({"strike": e["strike_price"], "option_type": e["option_type"], **q})
    return {
        "quotes": quotes,
        "listed": len(chain),
        "missing": len(chain) - len(quotes),
        **expiry_fields(conn, symbol, expiration, root),
    }


def bracket_dates(today: date, days: int) -> tuple[date, date]:
    """The P.M.-settled daily expirations (SPXW) that bracket `days` calendar days for the WHOLE of
    `today`'s session: the latest trading day on or before today + days - 1 (still within `days` of
    the 09:30 open) and the earliest on or after today + days (still at least `days` from the close).
    Holidays are skipped through `core.calendar`; an early close still settles, so it still counts."""
    near = today + timedelta(days=days - 1)
    while not _cal.is_trading_day(near):
        near -= timedelta(days=1)
    nxt = today + timedelta(days=days)
    while not _cal.is_trading_day(nxt):
        nxt += timedelta(days=1)
    return near, nxt


def strip_query(symbol: str, root: str, expirations: Iterable[date], *, bounds=STRIP_BOUNDS) -> str:
    """The `leg_sources` SELECT `declare` writes: every listed `root` option of `symbol` on the given
    expirations with a strike inside `bounds` x the underlying's last print, read from the stream cache
    itself. Literal values because a leg source takes no parameters, so each is validated here; an
    expiration already past (UTC) selects nothing, so a file left behind decays to an empty set."""
    if not (symbol.isalnum() and root.isalnum()):
        raise ValueError(f"symbol and root must be alphanumeric, got {symbol!r} / {root!r}")
    lo, hi = float(bounds[0]), float(bounds[1])
    if not 0 < lo < 1 < hi:
        raise ValueError(f"bounds must straddle spot, got {bounds!r}")
    dates = ", ".join(f"'{d.isoformat()}'" for d in expirations)
    spot = f"(SELECT last FROM stream_trades WHERE symbol = '{symbol}')"
    return (
        "SELECT streamer_symbol FROM stream_chain"
        f" WHERE underlying_symbol = '{symbol}' AND expiration IN ({dates})"
        " AND expiration >= date('now')"
        f" AND substr(json_extract(data_json, '$.symbol'), 1, 6) = '{root:<6}'"
        " AND CAST(json_extract(data_json, '$.strike_price') AS REAL)"
        f" BETWEEN {lo} * {spot} AND {hi} * {spot}"
    )
