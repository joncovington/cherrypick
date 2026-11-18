"""Stored history for the automation driver: the twin of the paper feed, read from a SQLite file in the
gex recorder's schema instead of the stream cache.

`StoreHistory` reads one symbol from any file in that schema, read-only: the recorder's own
`gex_history.db` (SPX from 2026-10-13) or a vendor store (`SPX-60s.db` and the rest, while licensed),
whose fetch writes the recorder's tables. It reads through core's own readers, never a second copy:
`core.quotehistory` for the samples, the spot trail, the GEX profiles and the prior close (the reader
flies' and MEIC's replays use), and `core.regime` for the VIX at a sample (at or before, never after,
and unmeasured past its staleness bound). The only queries of its own list the stored sessions and
the daily closes. Daily OHLC bars and vol index closes come from other stores and are passed in.

`core.quotehistory` merges a sample's quotes by timestamp, so one sample holds one expiry: true of
every store `StoreHistory` reads (same-day expiries). Several expiries per instant are
`IntradayChainHistory`'s, over rows its caller loads; `DailyChainHistory` is the close only.
"""

from __future__ import annotations

import bisect
import sqlite3
from datetime import date, datetime
from pathlib import Path

from cherrypick.core import calendar as cal
from cherrypick.core import clock, regime
from cherrypick.core import quotehistory as qh

OPEN_MINUTE = 570


class StoreHistory:
    """One symbol's sessions in a recorder-schema file. A day is read once and kept until the next."""

    #: Sampled through the session, so an arm's entry window is read minute by minute.
    close_only = False

    def __init__(self, path: str | Path, symbol: str, *, daily: list[dict] | None = None):
        self.path, self.symbol = Path(path), symbol.upper()
        conn = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True)
        try:
            self.closes = {
                d: float(c)
                for d, c in conn.execute(
                    "SELECT trade_date, close FROM daily_closes WHERE symbol = ? AND close IS NOT NULL",
                    (self.symbol,),
                )
            }
            self.sessions = [
                d
                for (d,) in conn.execute(
                    "SELECT DISTINCT trade_date FROM gex_quote_samples WHERE symbol = ? ORDER BY trade_date",
                    (self.symbol,),
                )
            ]
        finally:
            conn.close()
        #: Daily OHLC rows ({session, open, high, low, close}) from another store (index sessions, the
        #: stock history), for a daily signal; none means a daily signal reads stale bars.
        self.daily = sorted(daily or [], key=lambda r: r["session"])
        self._day: dict[str, dict] = {}
        #: Each session's spot trail as `prior_trails` returns it, read once.
        self._trails: dict[str, list[tuple[int, float]]] = {}

    def day(self, session: str) -> dict:
        if session in self._day:
            return self._day[session]
        rec = qh.RecordedDay(self.symbol, session, history_db=self.path)
        samples = {int(s["now_min"]): s for s in rec.samples}
        markets = regime.markets_at([s["ts"] for s in rec.samples], history_db=self.path)
        vix = {}
        for s, m in zip(rec.samples, markets, strict=True):
            r = (m.get("readings") or {}).get("vix") or {}
            if m.get("status") == "measured" and r.get("usable") and r.get("value") is not None:
                vix[int(s["now_min"])] = float(r["value"])
        trail = {}
        for ts, spot in rec.trail:
            trail[int(clock.minute_of_day(datetime.fromtimestamp(ts, clock.ET)))] = spot
        out = {"rec": rec, "samples": samples, "vix": vix, "trail": trail}
        self._day = {session: out}
        return out

    def minutes(self, session: str) -> list[int]:
        """The minutes the store sampled the chain that session: the ticks a backtest decides on."""
        return sorted(self.day(session)["samples"])

    def trail(self, session: str, upto: int) -> list[tuple[int, float]]:
        return [(m, s) for m, s in sorted(self.day(session)["trail"].items()) if m <= upto]

    def day_open(self, session: str, minute: int) -> float | None:
        """The first spot at or after 09:30, as paper takes the session's open from its trail."""
        opening = [s for m, s in self.trail(session, minute) if m >= OPEN_MINUTE]
        return opening[0] if opening else None

    def spot_at(self, session: str, minute: int) -> float | None:
        """The sample's spot (`core.quotehistory` refuses a stale one), as paper reads a fresh spot."""
        s = self.day(session)["samples"].get(minute)
        return None if s is None else s.get("underlying_price")

    def listed(self, session: str, minute: int) -> list[str]:
        s = self.day(session)["samples"].get(minute)
        return [s["expiration"]] if s and s.get("expiration") else []

    def rows(self, session: str, expiry: str, minute: int) -> list[dict]:
        """The expiry's quotes sampled at `minute`, with the GEX profile at or before it merged on."""
        d = self.day(session)
        s = d["samples"].get(minute)
        if s is None or s.get("expiration") != expiry:
            return []
        prof = {}
        before = [p for t, p in d["rec"].profiles if t <= s["ts"]]
        for r in before[-1] if before else []:
            prof[float(r["strike"])] = r
        out = []
        for right, side in (("P", "puts"), ("C", "calls")):
            for k, q in s[side].items():
                p = prof.get(float(k)) or {}
                out.append(
                    {
                        "strike": float(k),
                        "right": right,
                        "bid": q.get("bid"),
                        "ask": q.get("ask"),
                        "delta": q.get("delta"),
                        "gamma": p.get("call_gamma" if right == "C" else "put_gamma"),
                        "oi": p.get("call_oi" if right == "C" else "put_oi"),
                    }
                )
        return out

    def vix_at(self, session: str, minute: int) -> float | None:
        return self.day(session)["vix"].get(minute)

    def closes_before(self, session: str) -> list[float]:
        return [self.closes[d] for d in sorted(self.closes) if d < session]

    def prev_close(self, session: str) -> float | None:
        earlier = [d for d in self.closes if d < session]
        return self.closes[max(earlier)] if earlier else None

    def settle(self, session: str) -> float | None:
        """The session's official close; the last stored spot when the store has none."""
        if session in self.closes:
            return self.closes[session]
        trail = self.day(session)["trail"]
        return trail[max(trail)] if trail else None

    def prior_trails(self, session: str, n: int) -> dict[str, list[tuple[int, float]]]:
        """The spot trails of the `n` stored sessions before `session`, as paper's recorded trail. Each
        session's trail is read from the store once and kept (a few hundred numbers), since the driver
        asks for the same sessions at every minute of the next one."""
        out = {}
        for d in [x for x in self.sessions if x < session][-n:]:
            if d not in self._trails:
                rec = qh.RecordedDay(self.symbol, d, history_db=self.path)
                self._trails[d] = [
                    (clock.minute_of_day(datetime.fromtimestamp(ts, clock.ET)), spot)
                    for ts, spot in rec.trail
                ]
            if self._trails[d]:
                out[d] = list(self._trails[d])
        return out

    def daily_before(self, session: str) -> list[dict]:
        return [r for r in self.daily if r["session"] < session]


class DailyChainHistory:
    """End-of-day chains: one tick a session, at 15:59 (the last minute paper acts in; on an early close
    the minute before it). The spot and the open are the session's daily bar, the chains come from
    `chains(session) -> {expiry: rows}` (Dolt's or the vendor's end-of-day store, loaded by the caller),
    and a position settles at its expiry's close. Nothing intraday: no trail, no earlier sessions'
    trails, no VIX through the day (the vol index's close, when given, stands for the session).

    The cadence is the limit: an arm whose entry window does not hold the tick cannot be priced from
    this source, and the driver counts it so (`needs_intraday`), never moving its entry."""

    close_only = True

    def __init__(self, symbol: str, bars: list[dict], chains, *, vix_daily: dict[str, float] | None = None):
        self.symbol = symbol.upper()
        self.bars = {r["session"]: r for r in sorted(bars, key=lambda r: r["session"])}
        self.daily = list(self.bars.values())
        self.closes = {d: float(r["close"]) for d, r in self.bars.items() if r.get("close") is not None}
        self._chains = chains
        self._cache: dict[str, dict] = {}
        self.vix_daily = vix_daily or {}
        self.sessions = sorted(self.bars)

    @staticmethod
    def tick(session: str) -> int:
        h, m = cal.session_close_hhmm(date.fromisoformat(session)).split(":")
        return int(h) * 60 + int(m) - 1

    def chains(self, session: str) -> dict:
        if session not in self._cache:
            self._cache = {session: self._chains(session) or {}}
        return self._cache[session]

    def minutes(self, session: str) -> list[int]:
        return [self.tick(session)] if self.chains(session) else []

    def trail(self, session: str, upto: int) -> list[tuple[int, float]]:
        return []

    def day_open(self, session: str, minute: int) -> float | None:
        bar = self.bars.get(session) or {}
        return None if bar.get("open") is None else float(bar["open"])

    def spot_at(self, session: str, minute: int) -> float | None:
        bar = self.bars.get(session) or {}
        return None if bar.get("close") is None else float(bar["close"])

    def listed(self, session: str, minute: int) -> list[str]:
        return sorted(self.chains(session))

    def rows(self, session: str, expiry: str, minute: int) -> list[dict]:
        return list(self.chains(session).get(expiry) or [])

    def vix_at(self, session: str, minute: int) -> float | None:
        return self.vix_daily.get(session)

    def closes_before(self, session: str) -> list[float]:
        return [c for d, c in self.closes.items() if d < session]

    def prev_close(self, session: str) -> float | None:
        earlier = [d for d in self.closes if d < session]
        return self.closes[max(earlier)] if earlier else None

    def settle(self, session: str) -> float | None:
        return self.closes.get(session)

    def prior_trails(self, session: str, n: int) -> dict[str, list[tuple[int, float]]]:
        return {}

    def daily_before(self, session: str) -> list[dict]:
        return [r for d, r in self.bars.items() if d < session]


class IntradayChainHistory:
    """Several expiries through the session, held across sessions: a multi-day automation replayed at
    the bars an intraday chain store holds (the full set's one-minute near and five-minute middle
    expiries), not only at the close. The caller loads the data, so this stays free of any store's
    schema:

    - `expiries(session) -> [expiry]`, every expiry stored that session;
    - `quotes(session, expiry) -> {minute: rows}`, an expiry's rows at each of its bars;
    - `spot(session) -> ({minute: spot}, source)`, the session's spot trail and where it came from.

    Each stored minute is a tick. An expiry stored at a coarser bar than the tick reads its latest bar
    at or before it, up to one bar old (`stale`): a five-minute expiry at 15:59 reads its 15:55 bar.
    Daily bars give the closes, the open of a session without a trail and the settlement; the VIX is
    the vol index's close for the session, as on end-of-day chains."""

    close_only = False

    def __init__(
        self,
        symbol: str,
        bars: list[dict],
        *,
        expiries,
        quotes,
        spot,
        vix_daily: dict[str, float] | None = None,
        sessions: list[str] | None = None,
    ):
        self.symbol = symbol.upper()
        self.bars = {r["session"]: r for r in sorted(bars, key=lambda r: r["session"])}
        self.daily = list(self.bars.values())
        self.closes = {d: float(r["close"]) for d, r in self.bars.items() if r.get("close") is not None}
        self._expiries, self._quotes, self._spot = expiries, quotes, spot
        self.vix_daily = vix_daily or {}
        self.sessions = sorted(sessions if sessions is not None else self.bars)
        self._day: dict[str, dict] = {}
        self._trails: dict[str, list[tuple[int, float]]] = {}
        #: Each session's spot source as the loader named it, for the result to state.
        self.spot_sources: dict[str, str] = {}

    def day(self, session: str) -> dict:
        if session not in self._day:
            trail, source = self._spot(session) or ({}, None)
            if source:
                self.spot_sources[session] = source
            self._day = {
                session: {"trail": dict(trail), "expiries": list(self._expiries(session) or []), "q": {}}
            }
        return self._day[session]

    def _bars(self, session: str, expiry: str) -> tuple[list[int], dict[int, list[dict]], int]:
        """An expiry's bar minutes, its rows by minute and how stale a read of it may be."""
        d = self.day(session)
        if expiry not in d["q"]:
            by_minute = self._quotes(session, expiry) or {}
            ms = sorted(by_minute)
            d["q"][expiry] = (ms, by_minute, self.stale(ms))
        return d["q"][expiry]

    @staticmethod
    def stale(minutes: list[int]) -> int:
        """How far back a tick may read an expiry's bar: its own spacing (one minute when it has one bar)."""
        gaps = [b - a for a, b in zip(minutes, minutes[1:], strict=False) if b > a]
        return min(gaps) if gaps else 1

    def _bar_at(self, session: str, expiry: str, minute: int) -> int | None:
        ms, _, stale = self._bars(session, expiry)
        i = bisect.bisect_right(ms, minute)
        if not i:
            return None
        m = ms[i - 1]
        return m if minute - m < stale else None

    def minutes(self, session: str) -> list[int]:
        d = self.day(session)
        return sorted(d["trail"]) if d["expiries"] else []

    def trail(self, session: str, upto: int) -> list[tuple[int, float]]:
        return [(m, s) for m, s in sorted(self.day(session)["trail"].items()) if m <= upto]

    def day_open(self, session: str, minute: int) -> float | None:
        opening = [s for m, s in self.trail(session, minute) if m >= OPEN_MINUTE]
        if opening:
            return opening[0]
        bar = self.bars.get(session) or {}
        return None if bar.get("open") is None else float(bar["open"])

    def spot_at(self, session: str, minute: int) -> float | None:
        return self.day(session)["trail"].get(minute)

    def listed(self, session: str, minute: int) -> list[str]:
        return [e for e in self.day(session)["expiries"] if self._bar_at(session, e, minute) is not None]

    def rows(self, session: str, expiry: str, minute: int) -> list[dict]:
        m = self._bar_at(session, expiry, minute)
        if m is None:
            return []
        return [dict(r) for r in self._bars(session, expiry)[1][m]]

    def vix_at(self, session: str, minute: int) -> float | None:
        return self.vix_daily.get(session)

    def closes_before(self, session: str) -> list[float]:
        return [c for d, c in self.closes.items() if d < session]

    def prev_close(self, session: str) -> float | None:
        earlier = [d for d in self.closes if d < session]
        return self.closes[max(earlier)] if earlier else None

    def settle(self, session: str) -> float | None:
        """The session's daily close: what a position expiring that day settles at."""
        return self.closes.get(session)

    def prior_trails(self, session: str, n: int) -> dict[str, list[tuple[int, float]]]:
        out = {}
        for d in [x for x in self.sessions if x < session][-n:]:
            if d not in self._trails:
                trail, _ = self._spot(d) or ({}, None)
                self._trails[d] = sorted(trail.items())
            if self._trails[d]:
                out[d] = list(self._trails[d])
        return out

    def daily_before(self, session: str) -> list[dict]:
        return [r for d, r in self.bars.items() if d < session]
