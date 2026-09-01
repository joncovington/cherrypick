"""Which prior-day GEX level did SPX actually settle nearest — call wall, flip, or put wall?

A read-side study over this module's own recorded history, and nothing else: no trades, no new
recording, no network. It exists to answer a strategy-shopping question with the suite's own data
before any module grows an entry rule around it. The claim under test comes from the pinning
literature (and from vendors selling it): the call wall is the strongest pin candidate, because it
is the largest POSITIVE dealer-gamma concentration under the naive convention and long-gamma
hedging is a restoring force; the flip is a regime boundary with no restoring force at all; and the
put wall is a NEGATIVE-gamma concentration that amplifies rather than pins. If that ranking does
not show up in this table, no bot placement rule built on it deserves an arm.

Two variants of "the level", because the trade can be keyed off either and they are not the same:

* ``open``  — the first RTH reading of the session itself (09:30 + a window). Overnight OI has been
  refreshed by then, so this is the freshest positioning read the open ever gets, computed on the
  chain that actually expires that day. This is what a bot placing at the open would use.
* ``prior_final`` — the last RTH reading of the PREVIOUS session. Knowable the night before, but
  computed on the previous session's front expiration, which is already dead by the scored day
  (SPX expires daily). Scoring it measures whether strike concentration PERSISTS across days,
  not whether yesterday's exact chain still binds — the writeup must not conflate the two.

Honesty rules, the module's own:

* **RTH-gated the hard way.** `gex_regime_history` carries overnight rows (stamped with the next
  trade_date), and this history has already crossed midnight mid-session once — so a row is
  admitted only when its timestamp's ET *calendar date equals its own trade_date* AND falls inside
  09:30-16:00. A pre-open row leaking into "the open reading" would score levels nobody could have
  read at the open.
* **A session missing its close or its open reading is reported as skipped with the reason,
  never silently dropped.** 34 sessions on file and 23 with a close is a fact the output states.
* **No pooling across regimes.** Pinning is a positive-gamma phenomenon; the winners are counted
  per net-GEX sign as well as overall, because a call-wall "win" logged in a negative regime is
  not evidence for the mechanism.
* **Expired-chain rows are excluded, the same way `core.regime` excludes them.** Until the
  2026-08-26 forward-only fix the provider could compute a reading off a chain that had already
  expired (documented in this module's CLAUDE.md; 2,888 of 9,740 SPX rows here). Those rows carry
  the expiration they used, so the filter is the module's own: ``expiration >= trade_date``. A
  "level" off a dead chain is not a level anyone could have traded.
* **The sample is what it is.** This history starts 2026-07-29. The output carries n everywhere
  and draws no conclusion by itself.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, time
from pathlib import Path
from statistics import median

from cherrypick.core.clock import ET

LEVELS = ("call_wall", "zero_gamma", "put_wall")

RTH_START = time(9, 30)
RTH_END = time(16, 0)


def _rth_rows(conn: sqlite3.Connection, symbol: str, trade_date: str) -> list[sqlite3.Row]:
    """A session's regime rows, RTH only, in time order.

    The calendar-date equality check is not redundant with the hour check: an overnight row's ET
    timestamp lands on the PREVIOUS calendar day (dropped by equality), and a past-midnight row —
    the 2026-08-28 incident — lands on the next one (also dropped). Hours alone would admit both
    whenever they happened to fall between 09:30 and 16:00.
    """
    rows = conn.execute(
        # `expiration >= trade_date` is core.regime's own forward-only rule: pre-2026-08-26 rows can
        # carry levels computed off an already-expired chain, and those are not levels anyone could
        # have traded (see the module CLAUDE.md's horizon note).
        "SELECT * FROM gex_regime_history WHERE symbol = ? AND trade_date = ? AND expiration >= trade_date"
        " ORDER BY ts",
        (symbol, trade_date),
    ).fetchall()
    out = []
    for r in rows:
        stamp = datetime.fromtimestamp(r["ts"], ET)
        if stamp.date().isoformat() != trade_date:
            continue
        if not (RTH_START <= stamp.time() < RTH_END):
            continue
        out.append(r)
    return out


def _open_reading(rows: list[sqlite3.Row], window_minutes: int) -> sqlite3.Row | None:
    """The first reading inside the opening window, or None if the recorder missed it."""
    for r in rows:
        stamp = datetime.fromtimestamp(r["ts"], ET)
        minutes = (stamp.hour - 9) * 60 + stamp.minute - 30
        if 0 <= minutes < window_minutes:
            return r
    return None


def _score(reading: sqlite3.Row, close: float, open_spot: float | None) -> dict:
    """Distances from the close to each level, the nearest one, and the regime it was read in."""
    distances = {}
    for level in LEVELS:
        value = reading[level]
        distances[level] = None if value is None else round(abs(close - value), 2)
    known = {k: v for k, v in distances.items() if v is not None}
    winner = None
    if known:
        best = min(known.values())
        nearest = [k for k, v in known.items() if v == best]
        winner = nearest[0] if len(nearest) == 1 else "tie"
    return {
        "levels": {level: reading[level] for level in LEVELS},
        "distance_to_close": distances,
        "winner": winner,
        "net_gex": reading["net_gex"],
        "regime": "positive" if (reading["net_gex"] or 0) > 0 else "negative",
        "open_spot_to_level": None
        if open_spot is None
        else {
            level: None if reading[level] is None else round(abs(open_spot - reading[level]), 2)
            for level in LEVELS
        },
    }


def pin_study(db_path: Path | str, *, symbol: str = "SPX", open_window_minutes: int = 30) -> dict:
    """Score every session with both a close and the needed readings; report the rest as skipped."""
    conn = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        dates = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT trade_date FROM gex_regime_history WHERE symbol = ? ORDER BY trade_date",
                (symbol,),
            )
        ]
        closes = {
            r["trade_date"]: r["close"]
            for r in conn.execute("SELECT trade_date, close FROM daily_closes WHERE symbol = ?", (symbol,))
        }

        sessions: list[dict] = []
        skipped: list[dict] = []
        prior_final: sqlite3.Row | None = None
        for day in dates:
            rows = _rth_rows(conn, symbol, day)
            # Whatever happens to today's scoring, today's last RTH reading is the next session's
            # "prior final" — assigned at the end of the loop body, never earlier, so a skipped
            # session still hands its levels forward.
            close = closes.get(day)
            opening = _open_reading(rows, open_window_minutes)
            if close is None:
                skipped.append({"trade_date": day, "reason": "no_close_on_file"})
            elif opening is None:
                skipped.append({"trade_date": day, "reason": "no_rth_open_reading"})
            else:
                entry: dict = {
                    "trade_date": day,
                    "close": close,
                    "open_spot": opening["spot"],
                    "open": _score(opening, close, opening["spot"]),
                }
                if prior_final is not None:
                    entry["prior_final"] = _score(prior_final, close, opening["spot"])
                sessions.append(entry)
            if rows:
                prior_final = rows[-1]

        return {
            "symbol": symbol,
            "open_window_minutes": open_window_minutes,
            "sessions_on_file": len(dates),
            "scored": len(sessions),
            "skipped": skipped,
            "summary": {
                "open": _summarise(sessions, "open"),
                "prior_final": _summarise(sessions, "prior_final"),
            },
            "sessions": sessions,
        }
    finally:
        conn.close()


def _summarise(sessions: list[dict], variant: str) -> dict:
    """Winner counts overall and per regime, plus median distances — n carried everywhere."""
    scored = [s for s in sessions if variant in s]
    winners: dict[str, int] = {}
    by_regime: dict[str, dict[str, int]] = {"positive": {}, "negative": {}}
    dist: dict[str, list[float]] = {level: [] for level in LEVELS}
    reach: dict[str, list[float]] = {level: [] for level in LEVELS}
    for s in scored:
        v = s[variant]
        if v["winner"] is not None:
            winners[v["winner"]] = winners.get(v["winner"], 0) + 1
            by_regime[v["regime"]][v["winner"]] = by_regime[v["regime"]].get(v["winner"], 0) + 1
        for level in LEVELS:
            if v["distance_to_close"][level] is not None:
                dist[level].append(v["distance_to_close"][level])
            spans = v.get("open_spot_to_level")
            if spans and spans[level] is not None:
                reach[level].append(spans[level])
    return {
        "n": len(scored),
        "winners": winners,
        "winners_by_regime": by_regime,
        "median_close_distance": {k: (round(median(v), 2) if v else None) for k, v in dist.items()},
        "median_open_distance": {k: (round(median(v), 2) if v else None) for k, v in reach.items()},
    }
