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

from datetime import date, datetime
from pathlib import Path

from cherrypick.core import quotehistory
from cherrypick.core.clock import ET

from cherrypick.flies import db as dbmod
from cherrypick.flies import engine, paper_loop, provider

#: A profile row older than this is no surface (`core.quotehistory`'s rule, shared with MEIC's replay).
PROFILE_MAX_AGE_SECONDS = quotehistory.PROFILE_MAX_AGE_SECONDS
#: The first recorded session.
FIRST_SESSION = "2026-10-13"
RTH_OPEN_MIN = 9 * 60 + 30

_leg_symbol = quotehistory.leg_symbol


def _fail(symbol: str, reason: str, **extra) -> dict:
    return {"ok": False, "symbol": symbol, "reason": reason, **extra}


class RecordedSession(quotehistory.RecordedDay):
    """One recorded session of `symbol`, ready to hand out the snapshot paper would have built at any
    recorded instant. The day is loaded once by `core.quotehistory.RecordedDay` (with flies' own
    trailing-range bounds); `snapshot` is then pure over what was loaded."""

    def __init__(self, symbol: str, day: str, *, history_db: Path | str | None = None):
        super().__init__(
            symbol,
            day,
            history_db=history_db,
            trailing_sessions=provider.TRAILING_RANGE_SESSIONS,
            trailing_min_sessions=provider.TRAILING_RANGE_MIN_SESSIONS,
        )

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
        gex, gex_stats = self.gex(sample["ts"], spot, min_gex_strikes)
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
            "session": self.bounds(sample["ts"]),
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
        # An advised arm is defined by the config itself (`advised:<name>`: a base's params with an
        # overlay, as paper builds one), so it is known when the config defines it.
        advised = arm.startswith("advised:") and isinstance((config.get("arms") or {}).get(arm), dict)
        if arm not in engine.ARMS and not advised:
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
