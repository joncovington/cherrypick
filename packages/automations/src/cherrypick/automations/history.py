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
every store this reads today (same-day expiries). A store holding several expiries per instant would
need that reader widened first.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

from cherrypick.core import calendar as cal
from cherrypick.core import chaindelta, clock, regime
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


SESSIONS_SQL = "SELECT DISTINCT session FROM intraday_chain_quotes WHERE symbol = ?"
EXPIRIES_SQL = "SELECT DISTINCT expiration FROM intraday_chain_quotes WHERE symbol = ? AND session = ?"


def _minute(bar: str) -> int:
    h, _, m = str(bar).partition(":")
    return int(h) * 60 + int(m)


class IntradayChainHistory:
    """Later expiries through the session: the full set's intraday chain stores (backtest `fullset`,
    `<SYM>-near-1m.db` for expiries 1-7 days out, `<SYM>-mid-5m.db` for 8-90), with the same-day store
    for the day's own expiry and its spot. Read-only, one session at a time.

    - **The ticks** are the same-day store's sampled minutes where it holds the session, else the
      minutes the nearest held expiry was stored at (one-minute bars).
    - **The spot** is the same-day store's where it holds the session. Elsewhere it is the parity
      forward of the nearest held expiry at that bar (`core.chaindelta.forward_from_parity`), the
      method the Value tier's fetch already uses for delta: a forward one to seven days out, within a
      few cents of spot, and a declared limit of every run that reads it.
    - **A later expiry's quotes** at a minute are its latest stored bar at or before it, and never
      older than its store's interval: a five-minute expiry reads a bar up to four minutes old, a
      minute with none reads nothing. Gamma and open interest are not in these stores (None).
    - **Closes** (the previous close, settlement at expiry) come from the daily bars passed in.

    `chains` lists the intraday stores as (path, bar minutes), finest first: an expiry held by two is
    read from the first. `root` is the option root the stores hold the symbol under (SPXW for SPX).
    """

    close_only = False

    def __init__(
        self,
        symbol: str,
        chains: list[tuple[str | Path, int]],
        *,
        same_day: str | Path | None = None,
        root: str | None = None,
        daily: list[dict] | None = None,
        vix_daily: dict[str, float] | None = None,
    ):
        self.symbol = symbol.upper()
        self.root = (root or symbol).upper()
        self.stores = [(Path(p), int(step)) for p, step in chains]
        self.zero = StoreHistory(same_day, symbol, daily=daily) if same_day else None
        self.daily = sorted(daily or [], key=lambda r: r["session"])
        self.closes = {r["session"]: float(r["close"]) for r in self.daily if r.get("close") is not None}
        self.vix_daily = vix_daily or {}
        held: set[str] = set(self.zero.sessions) if self.zero else set()
        for path, _ in self.stores:
            held |= set(self._query(path, SESSIONS_SQL, (self.root,)))
        self.sessions = sorted(held)
        self._session: str | None = None
        self._expiries: dict[str, tuple[Path, int]] = {}
        self._bars: dict[str, dict[int, list[dict]]] = {}
        self._spot: dict[int, float] = {}
        self._trails: dict[str, list[tuple[int, float]]] = {}

    @staticmethod
    def _query(path: Path, sql: str, args: tuple) -> list:
        if not path.exists():
            return []
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            return [r[0] if len(r) == 1 else r for r in conn.execute(sql, args)]
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def _open(self, session: str) -> None:
        """Read which expiries the stores hold for `session`, once; a new session drops the last."""
        if self._session == session:
            return
        self._session, self._expiries, self._bars, self._spot = session, {}, {}, {}
        for path, step in self.stores:
            for exp in self._query(path, EXPIRIES_SQL, (self.root, session)):
                if exp >= session:
                    self._expiries.setdefault(exp, (path, step))
        if not self._zero_holds(session):
            self._spot = dict(self._derived_trail(session))

    def _zero_holds(self, session: str) -> bool:
        return self.zero is not None and session in set(self.zero.sessions)

    def _expiry_bars(self, session: str, expiry: str) -> dict[int, list[dict]]:
        self._open(session)
        if expiry not in self._bars:
            path, _ = self._expiries[expiry]
            bars: dict[int, list[dict]] = {}
            for bar, k, right, bid, ask, delta in self._query(
                path,
                "SELECT bar, strike, right, bid, ask, delta FROM intraday_chain_quotes "
                "WHERE symbol = ? AND session = ? AND expiration = ?",
                (self.root, session, expiry),
            ):
                r = "C" if str(right).upper().startswith("C") else "P"
                bars.setdefault(_minute(bar), []).append(
                    {
                        "strike": float(k),
                        "right": r,
                        "bid": bid,
                        "ask": ask,
                        "delta": delta,
                        "gamma": None,
                        "oi": None,
                    }
                )
            self._bars[expiry] = bars
        return self._bars[expiry]

    def _derived_trail(self, session: str) -> list[tuple[int, float]]:
        """The nearest held expiry's parity forward at each of its bars: the spot where the same-day
        store has none."""
        if session in self._trails:
            return self._trails[session]
        if not self._expiries:
            return []
        nearest = min(self._expiries)
        out = []
        for minute, rows in sorted(self._expiry_bars(session, nearest).items()):
            f = chaindelta.forward_from_parity(rows)
            if f is not None:
                out.append((minute, round(f, 4)))
        self._trails = {session: out}
        return out

    # --- the driver's reads --------------------------------------------------------------------------

    def minutes(self, session: str) -> list[int]:
        if self._zero_holds(session):
            return self.zero.minutes(session)
        self._open(session)
        return [m for m, _ in self._derived_trail(session)]

    def trail(self, session: str, upto: int) -> list[tuple[int, float]]:
        if self._zero_holds(session):
            return self.zero.trail(session, upto)
        self._open(session)
        return [(m, s) for m, s in self._derived_trail(session) if m <= upto]

    def day_open(self, session: str, minute: int) -> float | None:
        opening = [s for m, s in self.trail(session, minute) if m >= OPEN_MINUTE]
        return opening[0] if opening else None

    def spot_at(self, session: str, minute: int) -> float | None:
        if self._zero_holds(session):
            return self.zero.spot_at(session, minute)
        self._open(session)
        return self._spot.get(minute)

    def listed(self, session: str, minute: int) -> list[str]:
        self._open(session)
        today = self.zero.listed(session, minute) if self._zero_holds(session) else []
        return sorted(set(today) | set(self._expiries))

    def rows(self, session: str, expiry: str, minute: int) -> list[dict]:
        if self._zero_holds(session) and expiry in self.zero.listed(session, minute):
            return self.zero.rows(session, expiry, minute)
        self._open(session)
        if expiry not in self._expiries:
            return []
        _, step = self._expiries[expiry]
        bars = self._expiry_bars(session, expiry)
        fresh = [m for m in bars if minute - step < m <= minute]
        return list(bars[max(fresh)]) if fresh else []

    def vix_at(self, session: str, minute: int) -> float | None:
        if self._zero_holds(session):
            return self.zero.vix_at(session, minute)
        return self.vix_daily.get(session)

    def closes_before(self, session: str) -> list[float]:
        return [c for d, c in sorted(self.closes.items()) if d < session]

    def prev_close(self, session: str) -> float | None:
        earlier = [d for d in self.closes if d < session]
        return self.closes[max(earlier)] if earlier else None

    def settle(self, session: str) -> float | None:
        return self.closes.get(session)

    def prior_trails(self, session: str, n: int) -> dict[str, list[tuple[int, float]]]:
        """Earlier sessions' trails: the same-day store's where it holds them; a derived one is not
        kept across sessions, so an arm reading earlier trails reads only those."""
        if self.zero is None:
            return {}
        return self.zero.prior_trails(session, n)

    def daily_before(self, session: str) -> list[dict]:
        return [r for r in self.daily if r["session"] < session]
