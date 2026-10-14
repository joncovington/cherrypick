"""Did the recorder keep a whole session? One verdict per recorded series (2026-10-14).

The recorder's series are the suite's only record of their sessions -- the chain's quotes above all,
which nothing can fetch again -- and a recorder that stops writing one of them looks, from the
outside, exactly like a quiet market (the 34-hour streamer stall did). The recorder's heartbeat says
the process is alive; this says whether each series it owns has a full session on disk.

Per series and symbol: rows, first and last sample (ET), and every gap between samples (the session
edges included: the open to the first sample, the last sample to the close) longer than that
series' allowance, set from its own cadence with room for a missed tick or two. The session runs
09:30 to the calendar's close (13:00 on an early-close day). A series the machine does not record
(quote history off, or not for that symbol) is not judged. `daily_sessions` is judged for the
PREVIOUS trading session, the one the recorder's first sample of this session harvests.

Read-only over the history DB; the orchestrator's watchdog runs it by subprocess
(`run.py coverage --session YYYY-MM-DD`) once per session after the close.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

from cherrypick.core import calendar as _calendar
from cherrypick.core.clock import ET as _ET

from cherrypick.gex import service as _service

# Longest silence each series may show inside the session, in seconds, from its own cadence: the
# spot trail and the quotes every ~15 s, the market regime every ~60 s, the GEX regime and the
# per-strike profile every 5 minutes.
MAX_GAP_SECONDS = {
    "spot": 120.0,
    "quotes": 120.0,
    "market_regime": 300.0,
    "gex_regime": 900.0,
    "profile": 900.0,
}
# `daily_sessions` began 2026-10-14 and is harvested at the next session's first sample, so the first
# session that can find its predecessor's row is 10-15. Earlier sessions are not judged on it.
DAILY_SESSIONS_FROM = "2026-10-15"


def session_bounds(session: str) -> tuple[float, float]:
    """Epoch seconds of 09:30 and the calendar's close (ET) on `session`."""
    day = date.fromisoformat(session)
    hh, mm = (int(x) for x in _calendar.session_close_hhmm(day).split(":"))
    open_ = datetime(day.year, day.month, day.day, 9, 30, tzinfo=_ET)
    close = datetime(day.year, day.month, day.day, hh, mm, tzinfo=_ET)
    return open_.timestamp(), close.timestamp()


def _hhmm(ts: float) -> str:
    return datetime.fromtimestamp(ts, _ET).strftime("%H:%M:%S")


def judge(ts: list[float], start: float, end: float, max_gap: float) -> dict:
    """One series' verdict from its sample times inside [start, end]. Pure."""
    inside = sorted(t for t in ts if start <= t <= end)
    edges = [start, *inside, end]
    gaps = [
        {"from": _hhmm(a), "to": _hhmm(b), "seconds": round(b - a, 1)}
        for a, b in zip(edges, edges[1:], strict=False)
        if b - a > max_gap
    ]
    return {
        "rows": len(inside),
        "first": _hhmm(inside[0]) if inside else None,
        "last": _hhmm(inside[-1]) if inside else None,
        "max_gap_allowed_s": max_gap,
        "gaps": gaps,
        "ok": bool(inside) and not gaps,
    }


def _times(conn: sqlite3.Connection, sql: str, args: tuple) -> list[float]:
    try:
        return [float(r[0]) for r in conn.execute(sql, args).fetchall()]
    except sqlite3.OperationalError:
        return []  # a table the recorder never created is a series with no rows, judged as such


def session_coverage(conn: sqlite3.Connection, session: str, cfg: dict) -> dict:
    """Every series the recorder keeps, for `session`, judged against its allowance."""
    start, end = session_bounds(session)
    symbols = [str(s).strip().upper() for s in (cfg.get("symbols") or [])]
    qh = _service.quote_history_settings(cfg)
    quote_symbols = set(qh["symbols"] or []) if qh["enabled"] else set()
    series: dict[str, dict] = {}
    for sym in symbols:
        sources = {
            "spot": ("SELECT ts FROM gex_spot_history WHERE symbol = ? AND trade_date = ?", (sym, session)),
            "gex_regime": (
                "SELECT ts FROM gex_regime_history WHERE symbol = ? AND trade_date = ?",
                (sym, session),
            ),
            "profile": (
                "SELECT DISTINCT ts FROM gex_profile_history WHERE symbol = ? AND trade_date = ?",
                (sym, session),
            ),
        }
        if sym in quote_symbols:
            sources["quotes"] = (
                "SELECT ts FROM gex_quote_samples WHERE symbol = ? AND trade_date = ?",
                (sym, session),
            )
        for name, (sql, args) in sources.items():
            series[f"{name}:{sym}"] = judge(_times(conn, sql, args), start, end, MAX_GAP_SECONDS[name])
    series["market_regime"] = judge(
        _times(conn, "SELECT DISTINCT ts FROM market_regime_history WHERE trade_date = ?", (session,)),
        start,
        end,
        MAX_GAP_SECONDS["market_regime"],
    )
    prev = _calendar.previous_trading_day(date.fromisoformat(session)).isoformat()
    for sym in symbols if session >= DAILY_SESSIONS_FROM else []:
        try:
            row = conn.execute(
                "SELECT day_high, day_low FROM daily_sessions WHERE symbol = ? AND trade_date = ?",
                (sym, prev),
            ).fetchone()
        except sqlite3.OperationalError:
            row = None
        series[f"daily_session:{sym}"] = {"session": prev, "ok": row is not None}
    failing = sorted(k for k, v in series.items() if not v["ok"])
    return {"session": session, "ok": not failing, "failing": failing, "series": series}


def run(cfg: dict, session: str) -> dict:
    """`session_coverage` over the configured history DB, read-only. A trading-day check is the
    caller's: a non-trading day is answered, not refused, so the caller can tell the two apart."""
    path = Path(cfg["history_db_path"])
    head = {"ok": True, "db": str(path), "trading_day": _calendar.is_trading_day(date.fromisoformat(session))}
    if not path.exists():
        return {
            **head,
            "session": session,
            "complete": False,
            "failing": ["history_db_missing"],
            "series": {},
        }
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10)
    try:
        out = session_coverage(conn, session, cfg)
    finally:
        conn.close()
    return {
        **head,
        "session": session,
        "complete": out["ok"],
        "failing": out["failing"],
        "series": out["series"],
    }
