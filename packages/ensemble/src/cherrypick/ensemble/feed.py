"""Building a `Snapshot` from the stream cache and the loop's own ledger, for one symbol on one tick.

This is the paper side of the one constructor both drivers call (`snapshot.build`): the cache's rows have
the shape the backtest's store rows have, so a decision made here is the decision the backtest would make
on the same market. Nothing here reaches the network, and nothing writes the cache.

What each arm reads decides what is fetched (`reads`): an arm that places a strike by delta needs the
chain's Greeks, one with a GEX gate or strike needs gamma and open interest, one that reads the opening
range or the intraday RSI or stochastic needs the day's spot trail from the open, one that reads the
standard-deviation move needs the VIX and its previous close. A symbol with no such arm is not asked for them.

A refusal is a dict with a `reason` (and a `detail`), never an exception and never a partial snapshot: the
caller records it against every arm that was due.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from cherrypick.core import calendar as _cal
from cherrypick.core import volindex

from . import arms as arms_mod
from . import db, provider
from . import indicators as ind
from . import strikes as strikes_mod
from .model import Chain, OpenPosition, Snapshot, hhmm
from .snapshot import build

OPEN_MINUTE = ind.OPEN_MINUTE
VIX_GATES = ("sd_change", "vix_level", "vix_change_pct")
TRAIL_GATES = ("rsi", "stoch")
#: Gates that read the underlying's vol index closes, and how many closes each needs.
IV_GATES = ("vol_index_rank", "vol_index_percentile", "vol_index_minus_rv")


def _vol_closes_needed(gate: dict) -> int:
    """Closes of the vol index a gate reads: a rank or percentile its lookback and the last close; the
    IV-less-RV gate only the last close."""
    if gate["gate"] == "vol_index_minus_rv":
        return 1
    return int(gate.get("lookback", 252)) + 1


def reads(arm: dict) -> dict:
    """What `arm`'s rules read beyond a quote and the spot."""
    gates = [g["gate"] for g in arm.get("gates", [])]
    rules = [leg["strike"]["rule"] for leg in arm["legs"]]
    gex = any(g.startswith("gex_") for g in gates) or any(r in strikes_mod.GEX_RULES for r in rules)
    mas = [g["n"] for g in arm.get("gates", []) if g["gate"] == "above_ma"]
    return {
        "trail": arm["entry"]["kind"] == "orb"
        or any(g in TRAIL_GATES for g in gates)
        or "opening_range" in rules,
        "gex": gex,
        "greeks": gex or "delta" in rules or (arm.get("exit") or {}).get("delta_limit") is not None,
        "vix": any(g in VIX_GATES for g in gates),
        "closes": max(
            [
                *mas,
                arms_mod.DAILY_HISTORY if arms_mod.reads_daily(arm) else 1,
                *(int(g.get("n", 20)) + 1 for g in arm.get("gates", []) if g["gate"] == "vol_index_minus_rv"),
            ]
        ),
        "vol_closes": max(
            [0, *(_vol_closes_needed(g) for g in arm.get("gates", []) if g["gate"] in IV_GATES)]
        ),
        "daily": arms_mod.reads_daily(arm),
        "prior": arms_mod.reads_prior_trail(arm),
        "events": bool((arm.get("skip_events") or {}).get("labels")),
    }


def entry_window(arm: dict) -> tuple[int, int]:
    """(first, last) minute at which `arm` may enter: a time entry's window, an opening-range entry's
    range end to its latest minute, a trigger's: a fixed time through its `within` minutes, a signal's
    window open through close (core's `entry_due` decides inside it)."""
    e = arm["entry"]
    if e["kind"] == "time":
        at = hhmm(e["at"])
        return at, at + e.get("window", 5) - 1
    if e["kind"] == "trigger":
        t = e["trigger"]
        if "at" in t:
            at = hhmm(t["at"])
            return at, at + int(t["within"]) - 1
        return hhmm(t["window"]["open"]), hhmm(t["window"]["close"])
    return OPEN_MINUTE + e["minutes"], hhmm(e["latest"])


#: The calendar days past its first an `at_least` rule's stream request reaches before expirations
#: are listed.
AT_LEAST_REACH = 7


def target_dates(arm: dict, today: date) -> list[date]:
    """The dates `arm`'s expiry rule can name today, for a cache that has not listed the symbol's
    expirations yet (the stream request still has to ask for them). An open-ended `at_least` asks for
    the week from its first day."""
    rule = arm["expiry"]
    if rule["kind"] in ("at_least", "between"):
        lo = rule["days"] if rule["kind"] == "at_least" else rule["min"]
        hi = lo + AT_LEAST_REACH if rule["kind"] == "at_least" else rule["max"]
        return [today + timedelta(days=d) for d in range(lo, hi + 1)]
    if rule["kind"] == "market_days":
        centre = _cal.nth_trading_day(today, rule["days"])
    else:
        centre = today + timedelta(days=rule["days"])
    tol = int(rule.get("tolerance", 0))
    return [centre + timedelta(days=d) for d in range(-tol, tol + 1) if centre + timedelta(days=d) >= today]


def expiries_for(arm: dict, today: date, listed: list[str]) -> list[str]:
    """The expiry `arm` names today among the listed ones; with none listed, every date its rule allows."""
    from .engine import resolve_expiry

    if not listed:
        return [d.isoformat() for d in target_dates(arm, today)]
    skeleton = Snapshot(
        session=today.isoformat(),
        minute=0,
        symbol=arm["symbol"],
        spot=0.0,
        prev_close=0.0,
        day_open=0.0,
        chains={e: Chain(e, []) for e in listed},
        trail=[],
        closes=[],
    )
    e = resolve_expiry(arm, skeleton)
    return [e] if e else []


def trail_complete(trail: list[tuple[int, float]], grace: int) -> bool:
    """The record began within `grace` minutes of the open (a loop started at noon cannot give the
    opening range or the morning's bars)."""
    return bool(trail) and trail[0][0] <= OPEN_MINUTE + grace


def market(cache_path: str, conn, symbol: str, when: datetime, params: dict, *, vix: bool) -> dict:
    """The inputs every snapshot for `symbol` shares, or {"refusal", "detail"}."""
    today, minute = when.date(), when.hour * 60 + when.minute
    spot = provider.read_spot(cache_path, symbol, max_age_seconds=float(params["spot_max_age_seconds"]))
    if spot is None:
        return {"refusal": "no_spot", "detail": symbol}
    bars = provider.daily_bars(cache_path, symbol, today)
    prior = _cal.previous_trading_day(today).isoformat()
    if not bars or bars[-1]["session"] != prior:
        return {"refusal": "no_prev_close", "detail": prior}
    trail = db.trail(conn, symbol, today.isoformat(), through=minute)
    out = {
        "spot": spot,
        "prev_close": float(bars[-1]["close"]),
        "closes": [float(b["close"]) for b in bars],
        "bars": bars,
        "trail": trail,
        "trail_complete": trail_complete(trail, int(params["trail_grace_minutes"])),
        "day_open": provider.day_open(cache_path, symbol, today) or (trail[0][1] if trail else spot),
        "vix": None,
        "vix_prev_close": None,
    }
    if vix:
        out["vix"] = provider.read_spot(
            cache_path, "VIX", max_age_seconds=float(params["spot_max_age_seconds"])
        )
        vbars = provider.daily_bars(cache_path, "VIX", today)
        if vbars and vbars[-1]["session"] == prior:
            out["vix_prev_close"] = float(vbars[-1]["close"])
    return out


def snapshot_for(
    cache_path: str,
    conn,
    spec: dict,
    symbol: str,
    when: datetime,
    params: dict,
    arms: list[dict],
    *,
    events,
    chains: bool = True,
) -> tuple[Snapshot | None, dict]:
    """(snapshot, info) for `symbol` at `when`, built for `arms` (the ones due). With `chains` off only the
    market inputs are read (the cheap check of whether any arm is due). info carries `refusal`/`detail`
    when there is no snapshot, and the quote stats and the expiries read otherwise."""
    today = when.date()
    minute = when.hour * 60 + when.minute
    needs = [reads(a) for a in arms]
    m = market(cache_path, conn, symbol, when, params, vix=any(n["vix"] for n in needs))
    if "refusal" in m:
        return None, m
    quotes: dict[str, list[dict]] = {}
    info: dict = {
        "spot": m["spot"],
        "trail_complete": m["trail_complete"],
        "expiries": [],
        "fresh": 0,
        "rejected": 0,
    }
    if chains:
        listed = provider.listed_expirations(cache_path, symbol)
        wanted = sorted({e for a in arms for e in expiries_for(a, today, listed)})
        greeks = any(n["greeks"] for n in needs)
        for e in wanted:
            got = provider.chain_rows(
                cache_path,
                symbol,
                e,
                root=spec["root"],
                max_age_seconds=float(params["max_quote_age_seconds"]),
                greeks=greeks,
            )
            stats = got.get("quote_stats") or {}
            info["fresh"] += stats.get("fresh", 0)
            info["rejected"] += stats.get("rejected", 0)
            if got["ok"]:
                quotes[e] = got["rows"]
                info["expiries"].append(e)
    snap = build(
        session=today.isoformat(),
        minute=minute,
        symbol=symbol,
        spot=m["spot"],
        prev_close=m["prev_close"],
        day_open=m["day_open"],
        quotes=quotes,
        trail=m["trail"],
        closes=m["closes"],
        vix=m["vix"],
        vix_prev_close=m["vix_prev_close"],
        events=events,
        entries=db.entry_sessions(conn),
        open_positions=open_positions(conn, today),
        close_minute=_cal_close_minute(today),
        with_gex=any(n["gex"] for n in needs),
        daily=m["bars"] if any(n["daily"] for n in needs) else None,
        prior=prior_trails(conn, symbol, today) if any(n["prior"] for n in needs) else None,
        vol_closes=vol_history(symbol, today, max(n["vol_closes"] for n in needs) if needs else 0),
        volumes=volumes_for(conn, arms, today),
    )
    return snap, info


def vol_history(symbol: str, today: date, n: int) -> list[float]:
    """The last `n` closes of the symbol's vol index before `today` (`core.volindex`); empty when no gate
    reads them or the symbol has no index, so an IV gate fails rather than guesses."""
    index = volindex.index_for(symbol)
    if n <= 0 or index is None:
        return []
    return volindex.before(volindex.closes(index), today.isoformat(), n)


def volumes_for(conn, arms: list[dict], today: date) -> dict[str, dict[str, dict[int, float]]]:
    """The recorded one-minute volume of every symbol `arms` declare, today's and the prior trail's
    sessions (`db.minute_volumes`)."""
    syms = sorted({s for a in arms for s in arms_mod.volume_symbols(a)})
    if not syms or conn is None:
        return {}
    days = [today]
    d = today
    for _ in range(arms_mod.PRIOR_SESSIONS):
        d = _cal.previous_trading_day(d)
        days.append(d)
    return {s: {x.isoformat(): db.minute_volumes(conn, s, x.isoformat()) for x in days} for s in syms}


def prior_trails(conn, symbol: str, today: date) -> dict[str, list[tuple[int, float]]]:
    """The recorded trail of up to `arms.PRIOR_SESSIONS` trading days before `today`, the ones the loop
    recorded (a session it did not run is absent, and the signal reads around the gap)."""
    out, d = {}, today
    for _ in range(arms_mod.PRIOR_SESSIONS):
        d = _cal.previous_trading_day(d)
        tr = db.trail(conn, symbol, d.isoformat())
        if tr:
            out[d.isoformat()] = tr
    return out


def open_positions(conn, today: date) -> list[OpenPosition]:
    """What the engine counts as open: a position expiring today stops counting toward an arm's limit at
    that day's open, as the backtest counts it (item 75's declared reading)."""
    return [
        OpenPosition(p["arm"], p["entry_session"], p["expiration"])
        for p in db.open_positions(conn)
        if p["expiration"] > today.isoformat()
    ]


def _cal_close_minute(today: date) -> int:
    h, m = _cal.session_close_hhmm(today).split(":")
    return int(h) * 60 + int(m)


def position_snapshot(
    pos: dict,
    leg_rows: list[dict],
    spot: float,
    when: datetime,
    *,
    exit_rules: dict | None = None,
    conn=None,
    cache_path: str | None = None,
    events=None,
) -> Snapshot:
    """The snapshot `engine.manage` and `close_position` read for an open position: its own legs' quotes,
    the spot, the clock, and what its exit reads. A signal exit gets today's recorded trail, the earlier
    sessions' (an intraday signal) or the daily bars (a daily one) and the event calendar, exactly as an
    entry trigger does; without them a signal exit could never fire in paper while it fires in the
    backtest. No gates run on it, so nothing else is read."""
    today = when.date()
    minute = when.hour * 60 + when.minute
    as_arm = {"entry": {}, "exit": exit_rules or {}}
    signal = bool((exit_rules or {}).get("signal")) and conn is not None
    daily = None
    if signal and arms_mod.reads_daily(as_arm) and cache_path:
        daily = provider.daily_bars(cache_path, pos["symbol"], today)
    return build(
        session=today.isoformat(),
        minute=minute,
        symbol=pos["symbol"],
        spot=spot,
        prev_close=spot,
        day_open=spot,
        quotes={pos["expiration"]: leg_rows},
        trail=db.trail(conn, pos["symbol"], today.isoformat(), through=minute) if signal else [],
        closes=[],
        events=events,
        close_minute=_cal_close_minute(today),
        with_gex=False,
        daily=daily,
        prior=prior_trails(conn, pos["symbol"], today)
        if signal and arms_mod.reads_prior_trail(as_arm)
        else None,
        volumes=volumes_for(conn, [as_arm], today) if signal else None,
    )
