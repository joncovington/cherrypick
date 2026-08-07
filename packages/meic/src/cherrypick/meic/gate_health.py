"""Which regime gates are actually armed right now — and which have silently gone quiet.

The gates fail OPEN by design. When the streamer is down, GEX, ATR and intraday-range return
unavailable and their checks simply don't fire; `GATES.md` states the policy outright ("If GEX data
unavailable, proceed without GEX"). That is a defensible choice — blocking every entry on a missing
feed would be its own outage — but it has always been **invisible**. The loop keeps trading and
reports normally; the only difference is that its safety gates are off.

The ATR gate makes this worse than a transient. It needs N *completed* sessions of `stream_summary`
before it will judge anything, so a multi-day streamer outage leaves it disarmed for another week
after the streamer is healthy again — long after anyone would connect the two events.

So this module answers one question: *of the gates that could be protecting me, how many are?* It is
strictly a **read** surface. It does not change the fail-open behaviour, does not touch the loop, and
holds no opinion about whether failing open is right — that is a separate decision, and changing it
silently would be worse than the gap this closes.

File-only, like every other read surface here: it reads the shared stream cache read-only and never
calls the broker, so it is cheap enough to sit on a dashboard card.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from typing import Any

from .paths import stream_cache_path

ARMED = "armed"
DEGRADED = "degraded"

# Age past which a cache row stops being evidence that a feed is alive. The streamer's own stall
# threshold is 240s; this is deliberately looser, because a gate being *armed* only needs data from
# this session, not data from this minute.
_FRESH_SECONDS = 900


def _conn() -> sqlite3.Connection | None:
    path = stream_cache_path()
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def _et_today(now: datetime | None = None) -> str:
    from .tt import _et_today as _tt_today  # one definition of "today in ET" for the whole module

    return now.strftime("%Y-%m-%d") if now else _tt_today()


def _completed_sessions(conn: sqlite3.Connection, symbol: str, today: str, needed: int) -> int:
    """Completed `stream_summary` day-rows available to the ATR gate — today's partial row excluded,
    the same way `tt.cmd_get_atr` counts them, so this can never disagree with the gate itself.

    A row existing is not the same as a row being usable. The streamer writes one as soon as
    *either* high or low arrives (`if high is not None or low is not None`), while `_true_ranges`
    skips any row missing either — so a half-written row is a row the ATR gate will not count.
    Counting rows rather than usable rows reported 5/5 ARMED against a cache the gate itself read
    as 0/5: a false ARMED, which is the precise failure this surface exists to prevent."""
    try:
        rows = conn.execute(
            "SELECT COUNT(*) FROM (SELECT trade_date FROM stream_summary "
            "WHERE symbol = ? AND trade_date < ? "
            "AND day_high IS NOT NULL AND day_low IS NOT NULL "
            "ORDER BY trade_date DESC LIMIT ?)",
            (symbol, today, needed),
        ).fetchone()
        return int(rows[0]) if rows else 0
    except sqlite3.Error:
        return 0


def _open_interest(conn: sqlite3.Connection, symbol: str) -> tuple[int, float | None]:
    """`(row count, newest updated_at)` for a symbol's cached open interest.

    GEX is computed from `stream_oi`, populated by the streamer's Summary events; with no rows,
    `get_gex` returns ok=False and every GEX gate quietly stands down. Matching is by substring
    against the OCC symbol (`.SPXW260806P6300`) because that table holds only
    `symbol`/`open_interest`/`updated_at` —
    there is no underlying column, and assuming one made this report a false DEGRADED for a cache
    holding 2,007 SPX rows."""
    try:
        row = conn.execute(
            "SELECT COUNT(*), MAX(updated_at) FROM stream_oi WHERE symbol LIKE ?",
            (f"%{symbol}%",),
        ).fetchone()
        return (int(row[0]), row[1]) if row else (0, None)
    except sqlite3.Error:
        return 0, None


def _todays_range_row(conn: sqlite3.Connection, symbol: str, today: str) -> bool:
    """Today's row, counted the way `tt.cmd_get_intraday_range` counts it — which rejects a row
    whose high or low is still NULL rather than merely checking that one exists. Same half-written
    row as the ATR gate above: present in the table, useless to the gate."""
    try:
        row = conn.execute(
            "SELECT 1 FROM stream_summary WHERE symbol = ? AND trade_date = ? "
            "AND day_high IS NOT NULL AND day_low IS NOT NULL",
            (symbol, today),
        ).fetchone()
        return bool(row)
    except sqlite3.Error:
        return False


def _oi_reason(rows: int, updated_at: float | None, now: datetime | None) -> str:
    """Armed means the gate has data to judge on, which is what the gate itself tests. Cache age is
    reported alongside rather than folded into the verdict — a stale cache still arms the gate, and
    saying otherwise here would disagree with what the loop actually does."""
    if not rows:
        return "no open interest cached"
    if updated_at is None:
        return f"{rows} contracts cached"
    import time as _time

    age = (now.timestamp() if now else _time.time()) - float(updated_at)
    stale = " (stale)" if age > _FRESH_SECONDS else ""
    return f"{rows} contracts cached, updated {int(age // 60)}m ago{stale}"


def _gate(name: str, armed: bool, reason: str, **extra: Any) -> dict[str, Any]:
    return {"gate": name, "status": ARMED if armed else DEGRADED, "reason": reason, **extra}


def for_symbol(
    symbol: str, params: dict[str, Any] | None = None, now: datetime | None = None
) -> dict[str, Any]:
    """Gate-by-gate arming state for one symbol.

    A gate is ARMED when the data it judges on is present. It says nothing about whether the gate is
    currently *blocking* — a gate that is armed and passing is the healthy case, and the point here
    is only to separate "this gate looked and allowed it" from "this gate never ran".
    """
    params = params or {}
    symbol = symbol.strip().upper()
    needed = int(params.get("regime_atr_lookback_days", 5))
    conn = _conn()
    if conn is None:
        # No cache at all: every data-backed gate is down, and saying so plainly beats reporting
        # each one's absence as if they had been checked independently.
        return {
            "symbol": symbol,
            "gates": [
                _gate("atr", False, "stream cache not found — is the streamer running?", needed=needed),
                _gate("gex", False, "stream cache not found — is the streamer running?"),
                _gate("intraday_range", False, "stream cache not found — is the streamer running?"),
            ],
        }
    try:
        today = _et_today(now)
        have = _completed_sessions(conn, symbol, today, needed)
        oi_rows, oi_at = _open_interest(conn, symbol)
        have_range = _todays_range_row(conn, symbol, today)
        gates = [
            _gate(
                "atr",
                have >= needed,
                f"{have}/{needed} completed sessions in cache"
                + ("" if have >= needed else " — gate stays inactive until the history is rebuilt"),
                sessions_available=have,
                sessions_needed=needed,
                sessions_missing=max(0, needed - have),
            ),
            _gate("gex", oi_rows > 0, _oi_reason(oi_rows, oi_at, now)),
            _gate(
                "intraday_range",
                have_range,
                "today's session row present" if have_range else "no usable session row for today",
            ),
        ]
    finally:
        conn.close()
    return {"symbol": symbol, "gates": gates}


def report(
    symbols: list[str], params: dict[str, Any] | None = None, now: datetime | None = None
) -> dict[str, Any]:
    """`N of M gates armed` across every configured symbol, plus the per-gate detail.

    The headline is the count, because that is the thing worth putting on a card: an operator should
    be able to tell at a glance that something is protecting them, without reading a table."""
    per_symbol = [for_symbol(s, params, now) for s in symbols]
    flat = [g for s in per_symbol for g in s["gates"]]
    armed = sum(1 for g in flat if g["status"] == ARMED)
    degraded = [
        {"symbol": s["symbol"], **g} for s in per_symbol for g in s["gates"] if g["status"] == DEGRADED
    ]
    return {
        "ok": True,
        "armed": armed,
        "total": len(flat),
        "headline": f"{armed} of {len(flat)} regime gates armed",
        "degraded": degraded,
        "symbols": per_symbol,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Which MEIC regime gates are armed right now (read-only).")
    ap.add_argument("--symbols", help="comma-separated; defaults to config `symbols`")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    from .paper_loop import _load_config  # local import: keeps this module import-light

    cfg = _load_config()
    symbols = (
        [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        if args.symbols
        else cfg.get("symbols") or ([cfg["symbol"]] if cfg.get("symbol") else [])
    )
    out = report(symbols, cfg)
    if args.json:
        print(json.dumps(out, indent=2))
        return
    print(out["headline"])
    for item in out["degraded"]:
        print(f"  DEGRADED  {item['symbol']:6s} {item['gate']:15s} {item['reason']}")


if __name__ == "__main__":
    main()
