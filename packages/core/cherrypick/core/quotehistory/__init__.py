"""The read side of the chain's recorded quotes (gex's history DB, from 2026-10-13).

Stored as `gex_quote_samples` (one row per symbol and tick: date, time, expiration) and `gex_quotes`
(one per side and strike of a sample, the side 0 for a put and 1 for a call); the layout is the
recorder's (`service._ensure_history_table`), and this module hides it.

The gex recorder keeps, every tick (~15 s) in RTH, the usable quotes of the strikes nearest spot on its
chain -- the only quote history in the suite (the stream cache keeps the latest quote per leg and
nothing else). This module hands a session back sample by sample in the shape a module's snapshot
already has (`underlying_price`, `now_min`, `puts`/`calls` as {strike: {bid, ask, mid, age_seconds,
delta}}), so a replay of a rule a module never ran prices against the market as it was through the
module's own pricing functions.

Spot is not stored with the quotes: `gex_spot_history` already records it every recorder tick, and
it is joined here AT OR BEFORE the sample, within `max_spot_staleness_seconds` -- `core.regime`'s
rule, for its reason (a later spot is look-ahead; a stale one is a gap, not a value). A minute whose
spot cannot be joined keeps `underlying_price = None` and says so in `spot_reason`, never borrows a
neighbour's.

Reads a database another package's recorder writes, the suite's ordinary read-model relationship;
core imports nothing from gex.

**`RecordedDay` is what a replay builds its snapshots from** (flies' Phase 1, MEIC's Phase 2): the
day's samples plus everything else a snapshot needs that the recorder kept beside them -- the
per-strike GEX profile, the spot trail, the prior close and the trailing realised range -- loaded once
and read at any recorded instant without look-ahead. It lives here because two replays would
otherwise rebuild the same surface two ways; each module keeps its own snapshot shape on top.
"""

from __future__ import annotations

import bisect
from datetime import date, datetime
from pathlib import Path

from cherrypick.core import clock as _clock
from cherrypick.core import db as _db
from cherrypick.core import home as _home
from cherrypick.core.gex import compute_gex

DEFAULT_MAX_SPOT_STALENESS_SECONDS = 60.0
#: A profile row older than this is no surface: one ~5-minute recorder cadence plus a minute.
PROFILE_MAX_AGE_SECONDS = 360.0
#: The trailing realised range: the mean of this many prior sessions, from at least the minimum.
TRAILING_RANGE_SESSIONS = 5
TRAILING_RANGE_MIN_SESSIONS = 3

# `gex_quotes.side`: the recorder's `service.QUOTE_SIDES` (put 0, call 1). Kept as a literal, since
# core imports nothing from gex; change both or neither.
PUT, CALL = 0, 1


def default_history_db() -> Path:
    return _home.data_dir("gex") / "gex_history.db"


def session(
    symbol: str,
    trade_date: str,
    *,
    history_db: Path | str | None = None,
    max_spot_staleness_seconds: float = DEFAULT_MAX_SPOT_STALENESS_SECONDS,
) -> list[dict]:
    """Every recorded sample of `symbol` on `trade_date`, oldest first. Empty when nothing was kept."""
    path = Path(history_db) if history_db is not None else default_history_db()
    if not path.exists():
        return []
    symbol = symbol.strip().upper()
    conn = _db.connect_ro(path)
    try:
        have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if not {"gex_quote_samples", "gex_quotes"} <= have:
            return []
        minutes: dict[float, dict] = {}
        for r in conn.execute(
            "SELECT s.ts, s.expiration, q.side, q.strike, q.bid, q.ask, q.mid, q.age_s, q.delta "
            "FROM gex_quote_samples s JOIN gex_quotes q ON q.sample_id = s.id "
            "WHERE s.symbol = ? AND s.trade_date = ? ORDER BY s.ts, q.side, q.strike",
            (symbol, trade_date),
        ):
            snap = minutes.get(r["ts"])
            if snap is None:
                when = datetime.fromtimestamp(r["ts"], _clock.ET)
                try:
                    dte = (date.fromisoformat(r["expiration"]) - when.date()).days
                except (TypeError, ValueError):
                    dte = None
                snap = minutes[r["ts"]] = {
                    "ok": True,
                    "symbol": symbol,
                    "date": trade_date,
                    "ts": r["ts"],
                    "now_min": _clock.minute_of_day(when),
                    "expiration": r["expiration"],
                    "dte": dte,
                    "underlying_price": None,
                    "spot_reason": None,
                    "puts": {},
                    "calls": {},
                }
            q = {"bid": r["bid"], "ask": r["ask"], "mid": r["mid"], "age_seconds": r["age_s"]}
            if r["delta"] is not None:
                q["delta"] = r["delta"]
            snap["puts" if r["side"] == PUT else "calls"][r["strike"]] = q
        if not minutes:
            return []
        spots = (
            [
                (row["ts"], row["spot"])
                for row in conn.execute(
                    "SELECT ts, spot FROM gex_spot_history WHERE symbol = ? AND trade_date = ? ORDER BY ts",
                    (symbol, trade_date),
                )
            ]
            if "gex_spot_history" in have
            else []
        )
    finally:
        conn.close()

    i = -1
    out = []
    for ts in sorted(minutes):
        while i + 1 < len(spots) and spots[i + 1][0] <= ts:
            i += 1
        snap = minutes[ts]
        if i < 0:
            snap["spot_reason"] = "no_spot_before"
        elif ts - spots[i][0] > max_spot_staleness_seconds:
            snap["spot_reason"] = "spot_stale"
        else:
            snap["underlying_price"] = spots[i][1]
        out.append(snap)
    return out


def leg_symbol(symbol: str, expiration: str, right: str, strike: float) -> str:
    """A streamer-style symbol for a recorded leg (`.SPXW261016P5800`), so a replayed ledger's leg
    columns read as a live one's do. The `W` root is the 0DTE weekly the recorder keeps."""
    yymmdd = date.fromisoformat(expiration).strftime("%y%m%d")
    k = int(strike) if float(strike).is_integer() else strike
    return f".{symbol}W{yymmdd}{right}{k}"


def profile_surface(rows: list[dict], spot: float) -> dict:
    """The GEX surface from one recorded profile row set (`gex_profile_history`: strike, call/put open
    interest and gamma), through `core.gex.compute_gex`, the one engine."""
    entries, greeks, oi = [], {}, {}
    for r in rows:
        strike = float(r["strike"])
        for right, side in (("C", "call"), ("P", "put")):
            sym = f"{right}{strike}"
            entries.append({"strike_price": strike, "streamer_symbol": sym, "option_type": right})
            if r[f"{side}_gamma"] is not None:
                greeks[sym] = {"gamma": float(r[f"{side}_gamma"])}
            if r[f"{side}_oi"] is not None:
                oi[sym] = float(r[f"{side}_oi"])
    return compute_gex(entries, greeks, oi, spot)


class RecordedDay:
    """One recorded session of `symbol`, loaded once: the samples (`session`), the per-strike GEX
    profiles, the spot trail, the prior close and the trailing realised range. Every read takes an
    instant and sees nothing after it."""

    def __init__(
        self,
        symbol: str,
        day: str,
        *,
        history_db: Path | str | None = None,
        trailing_sessions: int = TRAILING_RANGE_SESSIONS,
        trailing_min_sessions: int = TRAILING_RANGE_MIN_SESSIONS,
    ):
        self.symbol = symbol.strip().upper()
        self.day = day
        path = Path(history_db) if history_db is not None else default_history_db()
        self.history_db = path
        self.samples = session(self.symbol, day, history_db=path)
        self.by_ts = {s["ts"]: s for s in self.samples}
        self.profiles: list[tuple[float, list[dict]]] = []
        self.trail: list[tuple[float, float]] = []
        self.prev_close: float | None = None
        self.trailing_range: float | None = None
        if path.exists():
            conn = _db.connect_ro(path)
            try:
                grouped: dict[float, list[dict]] = {}
                for r in conn.execute(
                    "SELECT ts, strike, call_oi, put_oi, call_gamma, put_gamma FROM gex_profile_history "
                    "WHERE symbol = ? AND trade_date = ? ORDER BY ts",
                    (self.symbol, day),
                ):
                    grouped.setdefault(float(r["ts"]), []).append(dict(r))
                self.profiles = sorted(grouped.items())
                self.trail = [
                    (float(r["ts"]), float(r["spot"]))
                    for r in conn.execute(
                        "SELECT ts, spot FROM gex_spot_history WHERE symbol = ? AND trade_date = ? "
                        "AND spot IS NOT NULL ORDER BY ts",
                        (self.symbol, day),
                    )
                ]
                row = conn.execute(
                    "SELECT close FROM daily_closes WHERE symbol = ? AND trade_date < ? "
                    "ORDER BY trade_date DESC LIMIT 1",
                    (self.symbol, day),
                ).fetchone()
                self.prev_close = float(row["close"]) if row else None
                widths = [
                    float(r["width"])
                    for r in conn.execute(
                        "SELECT trade_date, MAX(spot) - MIN(spot) AS width FROM gex_spot_history "
                        "WHERE symbol = ? AND trade_date < ? AND spot IS NOT NULL "
                        "GROUP BY trade_date ORDER BY trade_date DESC LIMIT ?",
                        (self.symbol, day, trailing_sessions),
                    )
                    if r["width"] is not None
                ]
                if len(widths) >= trailing_min_sessions:
                    self.trailing_range = sum(widths) / len(widths)
            finally:
                conn.close()
        self._trail_ts = [t for t, _ in self.trail]
        self._profile_ts = [t for t, _ in self.profiles]

    def instants(self) -> list[datetime]:
        """Every recorded sample, as the instant a replayed tick stands for."""
        return [datetime.fromtimestamp(s["ts"], _clock.ET) for s in self.samples]

    def bounds(self, ts: float) -> dict:
        """The day's open, high and low from the spot trail from 09:30 up to `ts` (never after it), with
        the prior close and the trailing range; empty before the first RTH spot."""
        open_ts = datetime.fromisoformat(self.day).replace(hour=9, minute=30, tzinfo=_clock.ET).timestamp()
        upto = bisect.bisect_right(self._trail_ts, ts)
        today = [spot for t, spot in self.trail[:upto] if t >= open_ts]
        if not today:
            return {}
        return {
            "day_open": today[0],
            "day_high": max(today),
            "day_low": min(today),
            "prev_day_close": self.prev_close,
            "trailing_range_points": self.trailing_range,
        }

    def gex(self, ts: float, spot: float, min_strikes: int) -> tuple[dict, dict]:
        """The GEX surface from the profile at or before `ts`, refused when that profile is older than
        `PROFILE_MAX_AGE_SECONDS` (`profile_stale`) or covers fewer than `min_strikes`
        (`insufficient_coverage`), with the stats that say which."""
        i = bisect.bisect_right(self._profile_ts, ts) - 1
        if i < 0 or ts - self.profiles[i][0] > PROFILE_MAX_AGE_SECONDS:
            age = None if i < 0 else round(ts - self.profiles[i][0], 1)
            return (
                {"ok": False, "error": "recorded GEX profile missing or stale", "profile_stale": True},
                {"strikes_with_data": 0, "min_strikes": min_strikes, "profile_age_seconds": age,
                 "input_rule": "recorded_profile", "refused": "profile_stale"},
            )  # fmt: skip
        profile_ts, rows = self.profiles[i]
        gex = profile_surface(rows, spot)
        stats = {
            "strikes_with_data": gex.get("strikes_with_data", 0) if gex.get("ok") else 0,
            "min_strikes": min_strikes,
            "profile_age_seconds": round(ts - profile_ts, 1),
            "input_rule": "recorded_profile",
        }
        if gex.get("ok") and stats["strikes_with_data"] < min_strikes:
            gex = {
                "ok": False,
                "error": "insufficient GEX coverage (recorded profile)",
                "insufficient_coverage": True,
            }
            stats["refused"] = "insufficient_coverage"
        return gex, stats
