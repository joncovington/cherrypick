"""The quote-history replay: a recorded session run through the paper loop's own tick.

docs/quote-history-replay-plan.md, Phase 1. The gex recorder keeps SPX's 0DTE chain every ~15 s from
2026-10-13 (`core.quotehistory`); this rebuilds each recorded sample as the snapshot
`provider.build_snapshot` would have built at that instant and hands it to `paper_loop.run_once`
(`snapshot_fn=`, `replay=True`), so the rules that run are the paper loop's, with no second copy, and
the only thing written is a scratch ledger.

How each snapshot field is rebuilt (the plan's "Before Phase 1", gap 2). Each is a declared rule, and
where the recording cannot supply a field the tick is refused, never approximated silently:

- **Quotes**: the sample's bid/ask/mid/delta inside paper's strike window (`strike_window_pct` of
  spot), and only those whose RECORDED age is within paper's `max_quote_age_seconds`. The recorder
  already dropped stale and crossed quotes at its own 120 s; paper's tighter limit applies on top.
- **Spot**: the sample's, joined from the spot trail at or before it (`core.quotehistory`).
- **GEX surface**: `core.gex.compute_gex`, the one engine, over the recorder's per-strike profile row
  (`gex_profile_history`: open interest and gamma per side) at or before the sample, used only within
  `PROFILE_MAX_AGE_SECONDS`. Older is a refusal (`profile_stale`), as paper refuses a stale live
  surface. The surface therefore moves in ~5-minute steps where paper's moved every tick: a declared
  difference, measured in validation, never assumed small. Paper's coverage rule (`min_gex_strikes`)
  applies unchanged.
- **Session bounds** (the `trend` tag's input): the day's open, high and low from the spot trail up to
  the sample, never after it; the prior close and the trailing realized range from the recorder's
  `daily_closes` and earlier sessions' trails. Paper reads the streamer's summary row; a spot trail
  sampled every few seconds is close to it, not identical.
- **Events** and **recorded GEX** (`wall-clear`'s input): the same reads paper makes, which already
  answer for any instant.

Model-driven arms are never re-run (the selector's model and the intraday agent's replies are not a
pure function of the recording): `replay_session` refuses them. Nothing here touches the live loop's
files: `replay=True` turns off the heartbeat, the log file and the advice decision (which WRITES).
"""

from __future__ import annotations

import bisect
from datetime import date, datetime
from pathlib import Path

from cherrypick.core import db as core_db
from cherrypick.core import quotehistory
from cherrypick.core.clock import ET
from cherrypick.core.gex import compute_gex

from cherrypick.flies import db as dbmod
from cherrypick.flies import engine, paper_loop, provider

#: A profile row older than this is no surface: one ~5-minute recorder cadence plus a minute.
PROFILE_MAX_AGE_SECONDS = 360.0
#: The first recorded session.
FIRST_SESSION = "2026-10-13"
RTH_OPEN_MIN = 9 * 60 + 30


def _fail(symbol: str, reason: str, **extra) -> dict:
    return {"ok": False, "symbol": symbol, "reason": reason, **extra}


def _leg_symbol(symbol: str, expiration: str, right: str, strike: float) -> str:
    """A streamer-style symbol for a recorded leg, so the ledger's leg columns read as paper's do."""
    yymmdd = date.fromisoformat(expiration).strftime("%y%m%d")
    k = int(strike) if float(strike).is_integer() else strike
    return f".{symbol}W{yymmdd}{right}{k}"


class RecordedSession:
    """One recorded session of `symbol`, ready to hand out the snapshot paper would have built at any
    recorded instant. Loads the day once; `snapshot` is then pure over what was loaded."""

    def __init__(self, symbol: str, day: str, *, history_db: Path | str | None = None):
        self.symbol = symbol.strip().upper()
        self.day = day
        path = Path(history_db) if history_db is not None else quotehistory.default_history_db()
        self.history_db = path
        self.samples = quotehistory.session(self.symbol, day, history_db=path)
        self.by_ts = {s["ts"]: s for s in self.samples}
        self.profiles: list[tuple[float, list[dict]]] = []
        self.trail: list[tuple[float, float]] = []
        self.prev_close: float | None = None
        self.trailing_range: float | None = None
        if not path.exists():
            return
        conn = core_db.connect_ro(path)
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
                    "SELECT ts, spot FROM gex_spot_history WHERE symbol = ? AND trade_date = ? AND spot IS NOT NULL "
                    "ORDER BY ts",
                    (self.symbol, day),
                )
            ]
            row = conn.execute(
                "SELECT close FROM daily_closes WHERE symbol = ? AND trade_date < ? ORDER BY trade_date DESC LIMIT 1",
                (self.symbol, day),
            ).fetchone()
            self.prev_close = float(row["close"]) if row else None
            self.trailing_range = self._trailing_range(conn)
        finally:
            conn.close()
        self._trail_ts = [t for t, _ in self.trail]
        self._profile_ts = [t for t, _ in self.profiles]

    def _trailing_range(self, conn) -> float | None:
        """Mean high-low of the prior `provider.TRAILING_RANGE_SESSIONS` sessions' spot trails, from
        at least `TRAILING_RANGE_MIN_SESSIONS` (provider's own bounds)."""
        rows = conn.execute(
            "SELECT trade_date, MAX(spot) - MIN(spot) AS width FROM gex_spot_history WHERE symbol = ? "
            "AND trade_date < ? AND spot IS NOT NULL GROUP BY trade_date ORDER BY trade_date DESC LIMIT ?",
            (self.symbol, self.day, provider.TRAILING_RANGE_SESSIONS),
        ).fetchall()
        widths = [float(r["width"]) for r in rows if r["width"] is not None]
        if len(widths) < provider.TRAILING_RANGE_MIN_SESSIONS:
            return None
        return sum(widths) / len(widths)

    def instants(self) -> list[datetime]:
        """Every recorded RTH sample, as the instant a replayed tick stands for."""
        return [datetime.fromtimestamp(s["ts"], ET) for s in self.samples]

    def _bounds(self, ts: float) -> dict:
        open_ts = datetime.fromisoformat(self.day).replace(hour=9, minute=30, tzinfo=ET).timestamp()
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

    def _gex(self, ts: float, spot: float, min_gex_strikes: int) -> tuple[dict, dict]:
        i = bisect.bisect_right(self._profile_ts, ts) - 1
        if i < 0 or ts - self.profiles[i][0] > PROFILE_MAX_AGE_SECONDS:
            age = None if i < 0 else round(ts - self.profiles[i][0], 1)
            return (
                {"ok": False, "error": "recorded GEX profile missing or stale", "profile_stale": True},
                {"strikes_with_data": 0, "min_strikes": min_gex_strikes, "profile_age_seconds": age,
                 "input_rule": "recorded_profile", "refused": "profile_stale"},
            )  # fmt: skip
        profile_ts, rows = self.profiles[i]
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
        gex = compute_gex(entries, greeks, oi, spot)
        stats = {
            "strikes_with_data": gex.get("strikes_with_data", 0) if gex.get("ok") else 0,
            "min_strikes": min_gex_strikes,
            "profile_age_seconds": round(ts - profile_ts, 1),
            "input_rule": "recorded_profile",
        }
        if gex.get("ok") and stats["strikes_with_data"] < min_gex_strikes:
            gex = {
                "ok": False,
                "error": "insufficient GEX coverage (recorded profile)",
                "insufficient_coverage": True,
            }
            stats["refused"] = "insufficient_coverage"
        return gex, stats

    def snapshot(
        self,
        _cache_path,
        symbol: str,
        *,
        when: datetime,
        max_quote_age_seconds: float = provider.DEFAULT_MAX_QUOTE_AGE_SECONDS,
        strike_window_pct: float = provider.DEFAULT_STRIKE_WINDOW_PCT,
        max_gex_input_age_seconds: float | None = None,  # noqa: ARG002 -- the profile rule replaces it
        min_gex_strikes: int = provider.DEFAULT_MIN_GEX_STRIKES,
    ) -> dict:
        """`provider.build_snapshot`'s shape, from the recording at `when` (a recorded instant)."""
        symbol = symbol.strip().upper()
        if symbol != self.symbol:
            return _fail(symbol, "not_recorded_symbol")
        sample = self.by_ts.get(when.timestamp())
        if sample is None:
            return _fail(symbol, "no_recorded_sample")
        spot = sample.get("underlying_price")
        if not spot:
            return _fail(symbol, "no_spot_price", spot_reason=sample.get("spot_reason"))
        window = strike_window_pct * spot
        expiration = sample["expiration"]
        books = {"puts": {}, "calls": {}}
        stale = 0
        for side, right in (("puts", "P"), ("calls", "C")):
            for strike, q in sample[side].items():
                if abs(float(strike) - spot) > window:
                    continue
                age = q.get("age_seconds")
                if age is not None and age > max_quote_age_seconds:
                    stale += 1
                    continue
                quote = {"bid": q["bid"], "ask": q["ask"], "mid": q["mid"], "age_seconds": age}
                if q.get("delta") is not None:
                    quote["delta"] = q["delta"]
                quote["streamer_symbol"] = _leg_symbol(symbol, expiration, right, float(strike))
                quote["occ_symbol"] = None
                quote["instrument_type"] = None
                books[side][float(strike)] = quote
        if not books["puts"] and not books["calls"]:
            return _fail(symbol, "no_fresh_quotes", rejected=stale)
        gex, gex_stats = self._gex(sample["ts"], spot, min_gex_strikes)
        return {
            "ok": True,
            "symbol": symbol,
            "date": self.day,
            "expiration": expiration,
            "dte": sample.get("dte"),
            "underlying_price": spot,
            "now_min": sample["now_min"],
            "puts": books["puts"],
            "calls": books["calls"],
            "strike_window": {"low": round(spot - window, 4), "high": round(spot + window, 4)},
            "gex": gex,
            "session": self._bounds(sample["ts"]),
            "events": provider._day_events(date.fromisoformat(self.day)),
            "quote_stats": {
                "fresh": len(books["puts"]) + len(books["calls"]),
                "rejected": stale,
                "max_age_seconds": max_quote_age_seconds,
                "deltas_fresh": sum(1 for b in books.values() for q in b.values() if "delta" in q),
            },
            "gex_stats": gex_stats,
            # From the history being replayed, not always the recorder's: a vendor session's walls
            # are in its own store (packages/backtest derives them the recorder's way).
            "recorded_gex": provider._recorded_gex(symbol, when, history_db=self.history_db),
            "replayed": True,
        }


#: Arm parameters that make an arm depend on something besides the recording.
MODEL_DRIVEN = ("selector", "trend_gate_source")


def replay_config(config: dict, arms: list[str]) -> dict:
    """`config` with only `arms` enabled. Refuses an arm the replay cannot run honestly."""
    for arm in arms:
        if arm not in engine.ARMS:
            raise ValueError(f"unknown arm {arm!r}")
        params = engine.merged_params(config, arm)
        driven = [k for k in MODEL_DRIVEN if params.get(k)]
        if driven or params.get("tag_closes") == "agent":
            raise ValueError(
                f"arm {arm!r} is model-driven ({', '.join(driven) or 'agent closes'}); it is never replayed"
            )
    registry = dict(config.get("arms") or {})
    out_arms = {
        name: (
            {**(registry.get(name) or {}), "enabled": name in arms}
            if isinstance(registry.get(name, {}), dict)
            else registry[name]  # a comment entry (a `_note` string), passed through
        )
        for name in set(registry) | set(arms)
    }
    return {**config, "arms": out_arms}


def replay_session(
    config: dict,
    day: str,
    ledger_path: Path | str,
    *,
    arms: list[str] | None = None,
    history_db: Path | str | None = None,
    settle_price: float | None = None,
) -> dict:
    """Replay `day` for `arms` (default control) into a fresh scratch ledger at `ledger_path`, then
    settle at `settle_price` (the official print, or paper's own settlement for a comparison)."""
    # The recorder's history starts at FIRST_SESSION; another history in its schema (a vendor's,
    # packages/backtest) carries its own span, and an absent day is refused below by its samples.
    if history_db is None and day < FIRST_SESSION:
        return {"ok": False, "error": f"no quote history before {FIRST_SESSION}"}
    arms = arms or ["control"]
    cfg = replay_config(config, arms)
    symbol = (cfg.get("symbols") or ["SPX"])[0]
    cfg = {**cfg, "symbols": [symbol]}
    session = RecordedSession(symbol, day, history_db=history_db)
    if not session.samples:
        return {"ok": False, "error": f"no recorded samples for {symbol} on {day}"}
    path = Path(ledger_path)
    if path.exists():
        raise FileExistsError(f"{path} exists: a replay writes a fresh scratch ledger")
    conn = dbmod.connect(str(path))
    refusals: dict[str, int] = {}
    ticks = 0
    try:
        for when in session.instants():
            out = paper_loop.run_once(
                cfg, conn, cache_path="", when=when, force=True, snapshot_fn=session.snapshot, replay=True
            )
            ticks += 1
            for r in out.get("results", []):
                if not r.get("ok") and r.get("reason"):
                    refusals[r["reason"]] = refusals.get(r["reason"], 0) + 1
        settled = None
        if settle_price is not None:
            settle_when = datetime.fromisoformat(day).replace(hour=16, minute=20, tzinfo=ET)
            settled = paper_loop.run_settle(
                cfg, conn, cache_path="", when=settle_when, price=settle_price, replay=True
            )
    finally:
        conn.close()
    return {
        "ok": True,
        "day": day,
        "symbol": symbol,
        "arms": arms,
        "ticks": ticks,
        "samples": len(session.samples),
        "profiles": len(session.profiles),
        "refusals": refusals,
        "settled": bool(settled and settled.get("ok")),
        "ledger": str(path),
    }
