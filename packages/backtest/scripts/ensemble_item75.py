"""Item 75 (research/edge-hypotheses-plan.md): a third-party portfolio of eight short-dated option rules,
read over all stored history in one pass. The rules are `cherrypick.ensemble`'s arms; this is the driver
that builds a snapshot from the vendor stores for each session and minute and hands it to the engine.

    python packages/backtest/scripts/ensemble_item75.py requests [--out DIR]       # fetch request files
    python packages/backtest/scripts/ensemble_item75.py study [--arms A,B] [--json PATH]

`requests` writes, under `<backtest>/results/research/item75/`, the three fetch files the queue runs
(`fetch-intraday-chains` for the SPX day-before-expiry chains and for GLD, `fetch-oi` for the SPX
expiry's open interest) from the sessions `SPX-60s.db` holds. `study` replays every arm over every stored
session, once, with no design/holdout split (the user's choice, declared): it reads `SPX-60s.db` for the
0DTE arms, `intraday_chains.db` and `open_interest.db` for the others, and writes
`results/research/item75/study-<first>-all.json`. Nothing here leaves the backtest data directory.

The verdict is the declaration's, per arm: net at the suite's slippage model above zero with a one-sided
stationary-bootstrap lower bound above zero (mean block 5, 20,000 draws, seed 75), Holm at 5% across the
arms; positive in both time-halves; not below zero at the stress fill; positive at 1.5x fees. Fewer than
100 entered sessions is no verdict. The 2022-05..2023-06 sessions are reported as a descriptive row and
judge nothing. `study` refuses to run until the declaration is merged (`DECLARATION_MERGED`).

Where an input cannot be rebuilt (an event date, a chain that was not fetched, a zero bid on a sold leg)
the arm runs without it for that session and the row says how many sessions that was.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.core import calendar as cal
from cherrypick.core import home, killcriteria
from cherrypick.core import stats as core_stats
from cherrypick.ensemble import arms as arm_mod
from cherrypick.ensemble import engine, pricing, snapshot
from cherrypick.ensemble.events import EventCalendar
from cherrypick.ensemble.model import OpenPosition

from cherrypick.backtest import greeks, paths
from cherrypick.backtest import settings as _settings

ET = ZoneInfo("America/New_York")
ITEM = 75
STORE_START = "2022-05-16"
SOURCE_WINDOW_START = "2023-07-01"
#: Flipped to True by the commit that merges the declaration, never before: no figure is read until then.
DECLARATION_MERGED = False

#: Corrections to how this replay reads its data (working-rules): one entry per fix, with the date, the
#: change, the reading that prompted it, who decided, and whether it came before any arm was read.
REPLAY_CORRECTIONS: list[dict] = []

BOOT = {"block": 5.0, "draws": 20000, "seed": 75}
MIN_SESSIONS = 100
KINDS = pricing.KINDS
SPX_STRIKES_EACH_SIDE = 20  # the 60 s store keeps this many each side of its spot
GLD_STRIKE_RANGE = 30
SPX_1D_STRIKE_RANGE = 40
OPEN_MIN, CLOSE_MIN = 570, 960
CHAIN_STORE, OI_STORE = "intraday_chains.db", "open_interest.db"
OI_SYMBOL = "SPXW-1D"


def minute_of(ts: float) -> int:
    t = datetime.fromtimestamp(ts, ET)
    return t.hour * 60 + t.minute


def hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def bar_minute(bar: str) -> int:
    h, m = bar.split(":")
    return int(h) * 60 + int(m)


# --------------------------------------------------------------------------- the SPX 60 s store
class Spx:
    """Reads `SPX-60s.db` (the recorder's tables): a session's samples, spot trail, GEX profile, VIX
    series and official close. Days are cached one at a time."""

    def __init__(self, db: sqlite3.Connection, vix_daily: dict[str, float]):
        self.db, self.vix_daily = db, vix_daily
        self.closes = {
            d: c for d, c in db.execute("SELECT trade_date, close FROM daily_closes WHERE symbol='SPX'")
        }
        self.sessions = [
            d
            for (d,) in db.execute(
                "SELECT trade_date FROM vendor_sessions WHERE symbol='SPX' AND status='complete' "
                "ORDER BY trade_date"
            )
        ]
        self._cache: dict[str, dict] = {}

    def prior(self, session: str, series: dict) -> str | None:
        earlier = [d for d in series if d < session]
        return max(earlier) if earlier else None

    def closes_before(self, session: str) -> list[float]:
        return [self.closes[d] for d in sorted(self.closes) if d < session]

    def day(self, session: str) -> dict:
        if session in self._cache:
            return self._cache[session]
        self._cache.clear()
        samples = {}
        for sid, ts, exp in self.db.execute(
            "SELECT id, ts, expiration FROM gex_quote_samples WHERE symbol='SPX' AND trade_date=?", (session,)
        ):
            if exp == session:
                samples[minute_of(ts)] = sid
        spot = {}
        for ts, s in self.db.execute(
            "SELECT ts, spot FROM gex_spot_history WHERE symbol='SPX' AND trade_date=? ORDER BY ts",
            (session,),
        ):
            spot[minute_of(ts)] = s
        profile: dict[int, dict[float, tuple]] = defaultdict(dict)
        for ts, k, coi, poi, cg, pg, civ, piv in self.db.execute(
            "SELECT ts, strike, call_oi, put_oi, call_gamma, put_gamma, call_iv, put_iv "
            "FROM gex_profile_history WHERE symbol='SPX' AND trade_date=?",
            (session,),
        ):
            profile[minute_of(ts)][k] = (coi, poi, cg, pg, civ, piv)
        vix = {
            minute_of(ts): v
            for ts, v in self.db.execute(
                "SELECT ts, value FROM market_regime_history WHERE trade_date=? AND reading='vix' "
                "AND usable=1 ORDER BY ts",
                (session,),
            )
        }
        out = {"samples": samples, "spot": spot, "profile": profile, "vix": vix}
        self._cache[session] = out
        return out

    def trail(self, session: str, upto: int) -> list[tuple[int, float]]:
        return [(m, s) for m, s in sorted(self.day(session)["spot"].items()) if m <= upto]

    def vix_at(self, session: str, minute: int) -> float | None:
        v = self.day(session)["vix"]
        ms = [m for m in v if m <= minute]
        return v[max(ms)] if ms else None

    def rows(self, session: str, minute: int) -> list[dict]:
        """The 0DTE chain at a minute, with the latest profile's gamma and open interest at or before it
        merged onto each strike."""
        d = self.day(session)
        sid = d["samples"].get(minute)
        if sid is None:
            return []
        pm = [m for m in d["profile"] if m <= minute]
        prof = d["profile"][max(pm)] if pm else {}
        out = []
        for side, k, b, a, delta in self.db.execute(
            "SELECT side, strike, bid, ask, delta FROM gex_quotes WHERE sample_id=?", (sid,)
        ):
            right = "C" if side == 1 else "P"
            coi, poi, cg, pg, civ, piv = prof.get(k, (None,) * 6)
            out.append(
                {
                    "strike": k,
                    "right": right,
                    "bid": b,
                    "ask": a,
                    "delta": delta,
                    "gamma": cg if right == "C" else pg,
                    "iv": (civ if right == "C" else piv),
                    "oi": coi if right == "C" else poi,
                }
            )
        return out

    def settle(self, session: str) -> float | None:
        c = self.closes.get(session)
        if c is not None:
            return c
        spot = self.day(session)["spot"]
        return spot[max(spot)] if spot else None


# --------------------------------------------------------------------------- the intraday chain store
class Intraday:
    """`intraday_chains.db` (GLD and the SPX day-before-expiry chains) and `open_interest.db`."""

    def __init__(self, chains: sqlite3.Connection | None, oi: sqlite3.Connection | None):
        self.chains, self.oi = chains, oi

    def rows(self, symbol: str, session: str, expiry: str, minute: int) -> tuple[list[dict], float | None]:
        if self.chains is None:
            return [], None
        out, spot = [], None
        for strike, right, bid, ask, delta, iv, und in self.chains.execute(
            "SELECT strike, right, bid, ask, delta, iv, underlying FROM intraday_chain_quotes "
            "WHERE symbol=? AND session=? AND expiration=? AND bar=?",
            (symbol, session, expiry, hhmm(minute)),
        ):
            if bid is None or ask is None:
                continue
            spot = und or spot
            out.append(
                {"strike": strike, "right": right[0], "bid": bid, "ask": ask, "delta": delta, "iv": iv}
            )
        return out, spot

    def with_gamma_and_oi(self, rows, session, expiry, spot, minute) -> list[dict]:
        """Gamma from the vendor's IV (Black-Scholes, as the 60 s store does) and the open interest the
        morning reported. A row without either keeps none, and carries zero gex."""
        oi = {}
        if self.oi is not None:
            for right, strike, n in self.oi.execute(
                "SELECT right, strike, open_interest FROM oi_contracts WHERE symbol=? AND session=? "
                "AND expiration=?",
                (OI_SYMBOL, session, expiry),
            ):
                oi[(strike, right)] = n
        close = datetime.fromisoformat(expiry).replace(hour=16, tzinfo=ET).timestamp()
        now = datetime.fromisoformat(session).replace(hour=minute // 60, minute=minute % 60, tzinfo=ET)
        t = greeks.year_fraction(close - now.timestamp())
        out = []
        for r in rows:
            g = greeks.bs_gamma(spot, r["strike"], r.get("iv"), t) if spot else None
            out.append({**r, "gamma": g, "oi": oi.get((r["strike"], r["right"]))})
        return out

    def sessions_with(self, symbol: str) -> set[tuple[str, str]]:
        if self.chains is None:
            return set()
        return {
            (s, e)
            for s, e in self.chains.execute(
                "SELECT DISTINCT session, expiration FROM intraday_chain_requests WHERE symbol=? "
                "AND status='complete'",
                (symbol,),
            )
        }


# --------------------------------------------------------------------------- events
def load_events(path: Path | None = None) -> EventCalendar:
    """The event data `scripts/fetch_event_history.py` wrote, if any. Without it only the rule labels
    answer, and every data label (CPI, sentiment) is unknown, which passes its gate and is counted."""
    path = path or home.data_dir("public") / "item75" / "events.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return EventCalendar()
    return EventCalendar(doc.get("data"), doc.get("covered"))


# --------------------------------------------------------------------------- one arm, one session
def _snap(spx: Spx, arm: dict, session: str, minute: int, ctx: dict, *, rows=None, expiry=None, spot=None):
    s = spx
    prev = s.prior(session, s.closes)
    day = s.day(session)
    spot_now = spot if spot is not None else day["spot"].get(minute)
    if prev is None or spot_now is None:
        return None
    first = min(day["spot"]) if day["spot"] else minute
    vix_prev_day = s.prior(session, s.vix_daily)
    quotes = {expiry or session: rows if rows is not None else s.rows(session, minute)}
    return snapshot.build(
        session=session,
        minute=minute,
        symbol=arm["symbol"],
        spot=spot_now,
        prev_close=s.closes[prev],
        day_open=day["spot"][first],
        quotes=quotes,
        trail=s.trail(session, minute),
        closes=s.closes_before(session),
        vix=s.vix_at(session, minute),
        vix_prev_close=s.vix_daily.get(vix_prev_day) if vix_prev_day else None,
        events=ctx["events"],
        entries=ctx["entries"],
        open_positions=ctx["open"],
        close_minute=CLOSE_MIN,
        gex_strike_cap=2 * SPX_STRIKES_EACH_SIDE,
    )


def candidate_minutes(arm: dict, spx: Spx, session: str) -> list[int]:
    e = arm["entry"]
    have = sorted(spx.day(session)["samples"])
    if e["kind"] == "time":
        at = engine.hhmm(e["at"])
        return [m for m in have if at <= m < at + e.get("window", 5)]
    latest = engine.hhmm(e["latest"])
    b = engine.breakout_minute(spx.trail(session, latest), e["minutes"], latest)
    if b is None:
        return []
    return [m for m in have if b <= m <= (latest if not e.get("single_shot", True) else b)]


def run_zero_dte(arm: dict, spx: Spx, ctx: dict, sessions: list[str]) -> tuple[list[dict], Counter]:
    """Every session of a same-day-expiry SPX arm. Returns (trades, why-not counts by first reason)."""
    trades, why = [], Counter()
    for session in sessions:
        if date.fromisoformat(session).weekday() not in arm["weekdays"]:
            continue
        entry = None
        reason = "no_trigger"  # no sample in the entry window, or no break
        unknown = False
        for m in candidate_minutes(arm, spx, session):
            snap = _snap(spx, arm, session, m, ctx)
            if snap is None:
                reason = "no_snapshot"
                continue
            dec = engine.decide(arm, snap)
            reason = dec.reason
            unknown = unknown or _unknown(dec)
            if dec.enter:
                entry = dec.entry
                break
        why["event_data_unknown"] += unknown
        if entry is None:
            why[reason] += 1
            continue
        ctx["entries"].setdefault(arm["name"], []).append(session)
        trades.append(hold_zero_dte(arm, spx, ctx, entry, session))
        why["entered"] += 1
    return trades, why


def hold_zero_dte(arm: dict, spx: Spx, ctx: dict, entry, session: str) -> dict:
    pos = engine.Position(entry, arm)
    rules = arm.get("exit", {})
    if not rules:
        return engine.settle_position(pos, spx.settle(session) or entry.spot, session)
    needs_chain = bool(rules.get("profit_target_pct")) or bool(rules.get("close_before_expiry_min"))
    spots = spx.day(session)["spot"]
    for m in sorted(spots):
        if m <= entry.minute:
            continue
        quotes = {session: spx.rows(session, m)} if needs_chain else {}
        snap = _mk(spx, session, m, spots[m], quotes, ctx)
        sig = engine.manage(pos, snap)
        if sig is None:
            continue
        if not quotes:  # a touch: the close needs the chain at this minute
            snap = _mk(spx, session, m, spots[m], {session: spx.rows(session, m)}, ctx)
        trade = engine.close_position(pos, sig[0], snap)
        if trade is not None:
            return trade
    return engine.settle_position(pos, spx.settle(session) or entry.spot, session)


# --------------------------------------------------------------------------- multi-session arms
def _unknown(dec) -> bool:
    """The decision passed an event gate only because the label had no data for the date."""
    return any(c[2] == "unknown" for c in dec.checks)


def next_session(session: str) -> str:
    return cal.next_trading_day(date.fromisoformat(session)).isoformat()


def run_one_dte_spx(arm: dict, spx: Spx, intra: Intraday, ctx: dict, sessions: list[str]):
    """The SPX fly to the next market day: entry from the fetched chain at the entry bar, held through
    the next session's 60 s store (that session's 0DTE chain IS the position's expiry)."""
    trades, why = [], Counter()
    at = engine.hhmm(arm["entry"]["at"])
    for session in sessions:
        if date.fromisoformat(session).weekday() not in arm["weekdays"]:
            continue
        exp = next_session(session)
        rows, spot = intra.rows("SPXW", session, exp, at)
        prev = spx.prior(session, spx.closes)
        if not rows or spot is None or prev is None:
            why["no_chain"] += 1
            continue
        rows = intra.with_gamma_and_oi(rows, session, exp, spot, at)
        day = spx.day(session)
        first = min(day["spot"]) if day["spot"] else at
        vix_prev_day = spx.prior(session, spx.vix_daily)
        snap = snapshot.build(
            session=session,
            minute=at,
            symbol="SPX",
            spot=spot,
            prev_close=spx.closes[prev],
            day_open=day["spot"].get(first, spot),
            quotes={exp: rows},
            trail=spx.trail(session, at),
            closes=spx.closes_before(session),
            vix=spx.vix_at(session, at),
            vix_prev_close=spx.vix_daily.get(vix_prev_day) if vix_prev_day else None,
            events=ctx["events"],
            entries=ctx["entries"],
            open_positions=ctx["open"],
            gex_strike_cap=None,
        )
        dec = engine.decide(arm, snap)
        why[dec.reason] += 1
        why["event_data_unknown"] += _unknown(dec)
        if not dec.enter:
            continue
        ctx["entries"].setdefault(arm["name"], []).append(session)
        pos = engine.Position(dec.entry, arm)
        trades.append(_hold_one_dte(arm, spx, intra, ctx, pos, session, exp))
    return trades, why


def _hold_one_dte(arm, spx, intra, ctx, pos, session, exp) -> dict:
    """Day one on the fetched 5-minute bars (touch also on the 1-minute spot), day two on the store."""
    last_rows: list[dict] = []  # the latest fetched bar's chain: a touch between bars closes on it
    for m in range(pos.entry.minute + 1, CLOSE_MIN):
        spot = spx.day(session)["spot"].get(m)
        if spot is None:
            continue
        if (m - OPEN_MIN) % 5 == 0:
            fresh, _ = intra.rows("SPXW", session, exp, m)
            last_rows = fresh or last_rows
        snap = _mk(spx, session, m, spot, {exp: last_rows} if last_rows else {}, ctx)
        sig = engine.manage(pos, snap)
        if sig and last_rows:
            trade = engine.close_position(pos, sig[0], snap)
            if trade is not None:
                return trade
    for m in sorted(spx.day(exp)["samples"]):
        spot = spx.day(exp)["spot"].get(m)
        if spot is None:
            continue
        snap = _mk(spx, exp, m, spot, {exp: spx.rows(exp, m)}, ctx)
        sig = engine.manage(pos, snap)
        if sig:
            trade = engine.close_position(pos, sig[0], snap)
            if trade is not None:
                return trade
    return engine.settle_position(pos, spx.settle(exp) or pos.entry.spot, exp)


def _mk(spx, session, minute, spot, quotes, ctx):
    return snapshot.build(
        session=session,
        minute=minute,
        symbol="SPX",
        spot=spot,
        prev_close=0.0,
        day_open=spot,
        quotes=quotes,
        trail=[],
        closes=[],
        events=ctx["events"],
        with_gex=False,
    )


def run_gld(arm: dict, intra: Intraday, closes: dict[str, float], ctx: dict, sessions: list[str]):
    """The GLD call spreads. Entry from the fetched entry-bar chain; exit from the expiry day's fetched
    bar (`close_before_expiry_min` before the close). A spread whose exit chain was not fetched or did not
    price is settled at the expiry's close and counted."""
    trades, why = [], Counter()
    at = engine.hhmm(arm["entry"]["at"])
    open_pos: list[tuple[engine.Position, str]] = []
    ctx["open"][:] = []
    for session in sessions:
        open_pos = _close_due(arm, intra, closes, open_pos, session, trades, why)
        ctx["open"][:] = [OpenPosition(arm["name"], p.entry.session, p.entry.expiry) for p, _ in open_pos]
        if date.fromisoformat(session).weekday() not in arm["weekdays"]:
            continue
        prev = max((d for d in closes if d < session), default=None)
        if prev is None:
            why["no_prior_close"] += 1
            continue
        expiry = _first_listed(intra, arm, session)
        if expiry is None:
            why["no_chain"] += 1
            continue
        rows, spot = intra.rows(arm["symbol"], session, expiry, at)
        if not rows or spot is None:
            why["no_chain"] += 1
            continue
        snap = snapshot.build(
            session=session,
            minute=at,
            symbol=arm["symbol"],
            spot=spot,
            prev_close=closes[prev],
            day_open=spot,
            quotes={expiry: rows},
            trail=[(at, spot)],
            closes=[closes[d] for d in sorted(closes) if d < session],
            events=ctx["events"],
            entries=ctx["entries"],
            open_positions=ctx["open"],
            with_gex=False,
        )
        dec = engine.decide(arm, snap)
        why[dec.reason] += 1
        why["event_data_unknown"] += _unknown(dec)
        if dec.enter:
            ctx["entries"].setdefault(arm["name"], []).append(session)
            open_pos.append((engine.Position(dec.entry, arm), session))
            ctx["open"].append(OpenPosition(arm["name"], session, dec.entry.expiry))
    for pos, _ in open_pos:  # still open at the last stored session: settled at the last close
        last = max(closes)
        trades.append(engine.settle_position(pos, closes[last], last))
        why["open_at_end"] += 1
    return trades, why


def _first_listed(intra: Intraday, arm: dict, session: str) -> str | None:
    """The expiry the arm's rule names that the store holds an entry chain for."""
    held = sorted(e for s, e in intra.sessions_with(arm["symbol"]) if s == session)
    rule = arm["expiry"]
    target = (
        date.fromisoformat(next_session(session))
        if rule["kind"] == "market_days"
        else date.fromisoformat(session) + timedelta(days=rule["days"])
    )
    tol = rule.get("tolerance", 0)
    best = [(abs((date.fromisoformat(e) - target).days), e) for e in held]
    best = [b for b in best if b[0] <= tol]
    return min(best)[1] if best else None


def _close_due(arm, intra, closes, open_pos, session, trades, why):
    keep = []
    rule = arm["exit"].get("close_before_expiry_min", 0)
    for pos, opened in open_pos:
        if session < pos.entry.expiry:
            keep.append((pos, opened))
            continue
        minute = CLOSE_MIN - rule
        rows, spot = intra.rows(arm["symbol"], pos.entry.expiry, pos.entry.expiry, minute)
        trade = None
        if rows and spot:
            snap = snapshot.build(
                session=pos.entry.expiry,
                minute=minute,
                symbol=arm["symbol"],
                spot=spot,
                prev_close=spot,
                day_open=spot,
                quotes={pos.entry.expiry: rows},
                trail=[],
                closes=[],
                with_gex=False,
            )
            trade = engine.close_position(pos, "time", snap)
        if trade is None:
            why["exit_unpriced"] += 1
            trade = engine.settle_position(
                pos, closes.get(pos.entry.expiry, pos.entry.spot), pos.entry.expiry
            )
        trades.append(trade)
    return keep


# --------------------------------------------------------------------------- statistics
def bootstrap(values: list[float]) -> dict:
    """The mean's stationary-bootstrap one-sided 95% lower bound and the share of draws at or below zero."""
    n = len(values)
    if n < 2:
        return {"lower95": None, "p": None}
    rng = random.Random(BOOT["seed"])
    means = sorted(
        statistics.fmean(values[i] for i in core_stats.stationary_indices(n, BOOT["block"], rng))
        for _ in range(BOOT["draws"])
    )
    return {
        "lower95": means[int(0.05 * (BOOT["draws"] - 1))],
        "p": sum(1 for x in means if x <= 0) / BOOT["draws"],
    }


def holm(ps: dict[str, float | None], alpha: float = 0.05) -> dict[str, bool]:
    """Holm's step-down at `alpha` over the arms that have a p."""
    live = sorted((p, a) for a, p in ps.items() if p is not None)
    out = dict.fromkeys(ps, False)
    for i, (p, a) in enumerate(live):
        if p > alpha / (len(live) - i):
            break
        out[a] = True
    return out


def session_series(trades: list[dict], kind: str) -> list[dict]:
    by: dict[str, float] = defaultdict(float)
    for t in trades:
        by[t["session"]] += t["net"][kind]  # a trade is dated by its entry session
    return [{"session": s, "net": round(v, 2), "trades": 1} for s, v in sorted(by.items())]


def judge_arm(trades: list[dict]) -> dict:
    """The declaration's pass test for one arm's trades (one entered session, one observation)."""
    ser = {k: session_series(trades, k) for k in KINDS}
    nets = [r["net"] for r in ser["verdict"]]
    out = {"sessions": len(nets)}
    if len(nets) < MIN_SESSIONS:
        return {**out, "verdict": "no_verdict", "why": f"fewer than {MIN_SESSIONS} entered sessions"}
    mid = ser["verdict"][len(nets) // 2]["session"]
    first = [r["net"] for r in ser["verdict"] if r["session"] < mid]
    second = [r["net"] for r in ser["verdict"] if r["session"] >= mid]
    cost = defaultdict(float)
    for t in trades:
        cost[t["session"]] += t["fees"] + t["settlement"]
    stressed = [r["net"] - 0.5 * cost[r["session"]] for r in ser["verdict"]]
    boot = bootstrap(nets)
    checks = {
        "mean_positive": statistics.fmean(nets) > 0,
        "bound_positive": boot["lower95"] is not None and boot["lower95"] > 0,
        "both_halves": bool(
            first and second and statistics.fmean(first) > 0 and statistics.fmean(second) > 0
        ),
        "stress_fill": statistics.fmean([r["net"] for r in ser["stress"]]) >= 0,
        "fees_x1_5": statistics.fmean(stressed) > 0,
    }
    return {
        **out,
        "mean_verdict": round(statistics.fmean(nets), 2),
        "mean_stress": round(statistics.fmean([r["net"] for r in ser["stress"]]), 2),
        "mean_mid": round(statistics.fmean([r["net"] for r in ser["mid"]]), 2),
        "halves": [round(statistics.fmean(first), 2), round(statistics.fmean(second), 2)]
        if first and second
        else None,
        "bootstrap": boot,
        "checks": checks,
        "pre_pass": all(checks.values()),
    }


def descriptive_row(trades: list[dict]) -> dict:
    early = [t for t in trades if t["session"] < SOURCE_WINDOW_START]
    nets = [t["net"]["verdict"] for t in early]
    return {"sessions": len(nets), "mean_verdict": round(statistics.fmean(nets), 2) if nets else None}


def portfolio(by_arm: dict[str, list[dict]]) -> dict:
    """All arms stacked: the day's summed net, at the verdict and stress fills, and the 99th-percentile
    100-session drawdown against the mean session net."""
    out = {}
    for kind in ("verdict", "stress"):
        day: dict[str, float] = defaultdict(float)
        for ts in by_arm.values():
            for t in ts:
                day[t["session"]] += t["net"][kind]
        out[kind] = [round(v, 2) for _, v in sorted(day.items())]
    nets = out["verdict"]
    result = {"sessions": len(nets)}
    if len(nets) < MIN_SESSIONS:
        return {**result, "verdict": "no_verdict"}
    rng = random.Random(BOOT["seed"])
    dds = sorted(
        killcriteria.drawdown_and_streak(
            [nets[i] for i in core_stats.stationary_indices(len(nets), 5.0, rng, length=100)]
        )[0]
        for _ in range(2000)
    )
    p99 = dds[int(0.99 * (len(dds) - 1))]
    mean_v, mean_s = statistics.fmean(nets), statistics.fmean(out["stress"])
    return {
        **result,
        "mean_session_verdict": round(mean_v, 2),
        "mean_session_stress": round(mean_s, 2),
        "drawdown_p99_100": round(p99, 2),
        "pre_pass": mean_v > 0 and mean_s > 0 and p99 < 100 * mean_v,
    }


# --------------------------------------------------------------------------- requests
def request_files(sessions: list[str]) -> dict[str, dict]:
    """The three fetch files. GLD's 20-day spread needs the entry chain and the expiry-day exit bar; the
    1DTE spread the same around one night; the SPX fly the entry-day chain to the close (its second day
    is already in the 60 s store)."""
    gld_20, gld_1, spx_1 = [], [], []
    for s in sessions:
        d = date.fromisoformat(s)
        target = d + timedelta(days=20)
        fri = target + timedelta(days=(4 - target.weekday()) % 7)
        for e in {fri.isoformat(), (fri - timedelta(days=7)).isoformat()}:
            if abs((date.fromisoformat(e) - target).days) <= 3:
                gld_20.append(_req("GLD", s, e, "12:30", "12:30", GLD_STRIKE_RANGE))
                gld_20.append(_req("GLD", e, e, "15:00", "15:00", GLD_STRIKE_RANGE))
        nxt = next_session(s)
        gld_1.append(_req("GLD", s, nxt, "12:20", "12:20", GLD_STRIKE_RANGE))
        gld_1.append(_req("GLD", nxt, nxt, "15:45", "15:45", GLD_STRIKE_RANGE))
        spx_1.append(_req("SPXW", s, nxt, "13:30", "16:00", SPX_1D_STRIKE_RANGE))
    chains = {"requests": _unique(gld_20 + gld_1 + spx_1)}
    oi = {"sessions": {s: [OI_SYMBOL] for s in sessions}}
    return {"chains": chains, "oi": oi}


def _req(symbol, session, expiry, start, end, width) -> dict:
    return {
        "symbol": symbol,
        "session": session,
        "expiration": expiry,
        "start": start,
        "end": end,
        "interval": "5m",
        "strike_range": width,
    }


def _unique(reqs: list[dict]) -> list[dict]:
    seen, out = set(), []
    for r in reqs:
        k = (r["symbol"], r["session"], r["expiration"], r["start"], r["end"])
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


# --------------------------------------------------------------------------- the study
ZERO_DTE = ("orb-put-spread", "iron-fly-1000", "iron-condor-1345", "put-spread-1300", "long-put-1030")


def study(spx: Spx, intra: Intraday, gld_closes: dict[str, float], events: EventCalendar, only=None) -> dict:
    arms = {k: v for k, v in arm_mod.load_arms().items() if not only or k in only}
    ctx = {"events": events, "entries": {}, "open": []}
    sessions = [s for s in spx.sessions if s >= STORE_START]
    by_arm, why = {}, {}
    for name in [a for a in ZERO_DTE if a in arms]:
        by_arm[name], why[name] = run_zero_dte(arms[name], spx, ctx, sessions)
    if "iron-fly-1dte-1330" in arms:
        by_arm["iron-fly-1dte-1330"], why["iron-fly-1dte-1330"] = run_one_dte_spx(
            arms["iron-fly-1dte-1330"], spx, intra, ctx, sessions
        )
    for name in ("gld-call-spread-20d", "gld-call-spread-1dte"):
        if name in arms:
            by_arm[name], why[name] = run_gld(arms[name], intra, gld_closes, ctx, sessions)
    verdicts = {name: judge_arm(ts) for name, ts in by_arm.items()}
    flags = holm({n: v.get("bootstrap", {}).get("p") for n, v in verdicts.items()})
    for name, v in verdicts.items():
        if "pre_pass" in v:
            v["holm_reject"] = flags[name]
            v["verdict"] = "pass" if v["pre_pass"] and flags[name] else "fail"
    return {
        "licence": "derived from licensed vendor history: stays under ~/.cherrypick, never in the repo",
        "item": ITEM,
        "window": [sessions[0], sessions[-1]] if sessions else None,
        "verdict": {n: v["verdict"] for n, v in verdicts.items()},
        "arms": {
            n: {
                **verdicts[n],
                "why_not": dict(why[n]),
                "descriptive_2022_05_to_2023_06": descriptive_row(by_arm[n]),
                "trades": by_arm[n],
            }
            for n in by_arm
        },
        "portfolio": portfolio(by_arm),
        "replay_corrections": REPLAY_CORRECTIONS,
    }


def open_stores(cfg: dict) -> tuple[Spx, Intraday]:
    vendor = paths.ensure_vendor_dir(cfg)
    ro = lambda name: (  # noqa: E731
        sqlite3.connect(f"file:{vendor / name}?mode=ro", uri=True) if (vendor / name).exists() else None
    )
    import csv

    vix: dict[str, float] = {}
    with (home.data_dir("market-files") / "cboe" / "VIX.csv").open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            m, d, y = row["DATE"].split("/")
            vix[f"{y}-{m}-{d}"] = float(row["CLOSE"])
    return Spx(ro("SPX-60s.db"), vix), Intraday(ro(CHAIN_STORE), ro(OI_STORE))


def gld_daily_closes() -> dict[str, float]:
    from cherrypick.technicals import paths as tpaths
    from cherrypick.technicals import store

    hist = store.connect(tpaths.history_db())
    return {b.date: b.close for b in store.raw_bars(hist, "GLD") if b.close}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    rq = sub.add_parser("requests")
    rq.add_argument("--out", type=Path)
    st = sub.add_parser("study")
    st.add_argument("--arms", default="")
    st.add_argument("--json", type=Path)
    args = ap.parse_args(argv)
    base = home.data_dir("backtest") / "results" / "research" / f"item{ITEM}"
    if args.cmd == "study" and not DECLARATION_MERGED:
        print(
            "refused: item 75's declaration is not merged yet; no figure is read before it is",
            file=sys.stderr,
        )
        return 2
    cfg = _settings.load()
    spx, intra = open_stores(cfg)
    if args.cmd == "requests":
        out = args.out or base
        out.mkdir(parents=True, exist_ok=True)
        files = request_files([s for s in spx.sessions if s >= STORE_START])
        (out / "requests-chains.json").write_text(json.dumps(files["chains"]), encoding="utf-8")
        (out / "plan-oi.json").write_text(json.dumps(files["oi"]), encoding="utf-8")
        print(
            json.dumps(
                {
                    "chains": len(files["chains"]["requests"]),
                    "oi_days": len(files["oi"]["sessions"]),
                    "written": str(out),
                }
            )
        )
        return 0
    only = {a for a in args.arms.split(",") if a}
    out = study(spx, intra, gld_daily_closes(), load_events(), only or None)
    target = args.json or base / f"study-{out['window'][0]}-all.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"verdict": out["verdict"], "written": str(target)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
