"""Replay the VIX/VIX3M regime classification over stored daily history — the read side of the
signal, in the exact mold of `overview`'s `score-history`.

**The signal backtests; the trade does not** (the plan's own framing). This module answers one
question: does the contango/backwardation/hook classification separate anything in VXX's own
forward move? It is a SEPARATION BENCHMARK, never suite P&L — no trade was taken on any of these
sessions, no credit spread was priced, and this schedules nothing and decides nothing.

No look-ahead, enforced by construction: a session's regime comes from the ratio computed off the
PRIOR session's closes — the only reading a pre-open reader could have had — via `_as_of` slicing.
The scored day and the day whose forward move it is credited with are never the same day.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from statistics import fmean, median
from typing import Any

from cherrypick.core import db as _core_db
from cherrypick.core import home as _home

from cherrypick.curve import regime as _regime

HISTORY_READ_CAP = 5000


def _close_series(conn, symbol: str, *, cap: int) -> list[dict]:
    """`[{"session", "close"}]` oldest-first from `stream_summary`, whatever the cache holds."""
    if conn is None:
        return []
    rows = conn.execute(
        "SELECT trade_date, day_close FROM stream_summary WHERE symbol = ? AND day_close IS NOT NULL "
        "ORDER BY trade_date DESC LIMIT ?",
        (symbol, cap),
    ).fetchall()
    return [{"session": r["trade_date"], "close": float(r["day_close"])} for r in reversed(rows)]


def _sessions(history: dict[str, list[dict]]) -> list[str]:
    days: set[str] = set()
    for series in history.values():
        days.update(row["session"] for row in series)
    return sorted(days)


def _close_on(series: list[dict], session: str) -> float | None:
    matches = [row["close"] for row in series if row["session"] == session]
    return matches[-1] if matches else None


def regime_series(history: dict[str, list[dict]], params: dict | None = None) -> list[dict]:
    """One entry per session that could be classified: the ratio/regime/hook computed off the
    PRIOR session's VIX/VIX3M closes — never today's, which is the no-look-ahead rule."""
    sessions = _sessions(history)
    out: list[dict] = []
    prior_ratio: float | None = None
    prior_session_ratio: float | None = None
    for session in sessions:
        vix = _close_on(history.get("VIX") or [], session)
        vix3m = _close_on(history.get("VIX3M") or [], session)
        if vix is None or vix3m is None:
            continue
        r = _regime.ratio(vix, vix3m)
        if r is None:
            prior_session_ratio = None
            continue
        # The reading FOR this session uses yesterday's ratio as the hook's prior — that ratio
        # was itself computed on a strictly earlier session's closes, so no same-day information
        # ever enters the classification.
        classified = {
            "session": session,
            "ratio": r,
            "regime": _regime.classify(r, params),
            "hook": _regime.hook_signal(r, prior_session_ratio, params),
        }
        out.append(classified)
        prior_session_ratio = r
    del prior_ratio
    return out


def _vxx_forward_moves(history: dict[str, list[dict]]) -> dict[str, float]:
    """Next-session VXX return keyed by the session it FOLLOWS — the same no-look-ahead keying
    `overview.backtest` uses for SPX."""
    series = history.get("VXX") or []
    out: dict[str, float] = {}
    for earlier, later in zip(series, series[1:], strict=False):
        if earlier["close"]:
            out[earlier["session"]] = (later["close"] / earlier["close"] - 1.0) * 100.0
    return out


def _distribution(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"min": None, "median": None, "max": None, "mean": None}
    return {
        "min": round(min(values), 4),
        "median": round(median(values), 4),
        "max": round(max(values), 4),
        "mean": round(fmean(values), 4),
    }


REGIMES = ("contango", "backwardation")


def run(history: dict[str, list[dict]], params: dict | None = None) -> dict[str, Any]:
    classified = regime_series(history, params)
    moves = _vxx_forward_moves(history)

    rows: list[dict] = []
    for entry in classified:
        forward = moves.get(entry["session"])
        if forward is None:
            continue
        rows.append({**entry, "vxx_forward_return_pct": round(forward, 4)})

    regimes: dict[str, Any] = {}
    for r in REGIMES:
        vals = [row["vxx_forward_return_pct"] for row in rows if row["regime"] == r]
        regimes[r] = {
            "sessions": len(vals),
            "share_pct": round(len(vals) / len(rows) * 100.0, 1) if rows else None,
            "vxx_mean_forward_return_pct": round(fmean(vals), 4) if vals else None,
            "vxx_median_forward_return_pct": round(median(vals), 4) if vals else None,
        }
    hook_vals = [row["vxx_forward_return_pct"] for row in rows if row["hook"]]
    hook = {
        "sessions": len(hook_vals),
        "share_pct": round(len(hook_vals) / len(rows) * 100.0, 1) if rows else None,
        "vxx_mean_forward_return_pct": round(fmean(hook_vals), 4) if hook_vals else None,
    }

    return {
        "sessions_available": len(_sessions(history)),
        "sessions_classified": len(classified),
        "sessions_joined": len(rows),
        "ratio_distribution": _distribution([row["ratio"] for row in rows]),
        "regimes": regimes,
        "hook": hook,
        "series": rows,
        "benchmark": "VXX next-session close-to-close change, in percent",
        "not_pnl": (
            "a signal separation benchmark, not suite P&L -- no trade was taken on any of these "
            "sessions, no credit spread was priced, and this schedules nothing and decides nothing"
        ),
        "no_look_ahead": (
            "a session's regime/hook comes from the ratio computed on the PRIOR session's VIX/"
            "VIX3M closes; the classified day and the day whose forward move it is credited with "
            "are never the same day"
        ),
    }


def _data_dir():
    return _home.data_dir("curve")


def write(result: dict[str, Any]) -> str:
    path = _data_dir() / "regime-history.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)
    return str(path)


def build(config: dict) -> dict[str, Any]:
    """Read whatever the shared stream cache holds (not the request size — a load decision is
    separate from what a read-side replay is allowed to score) and run the overlay."""
    from cherrypick.curve import paper_loop

    cache_path = paper_loop.stream_cache_path(config)
    try:
        conn = _core_db.connect_ro(cache_path)
    except Exception:  # noqa: BLE001 — no cache is an empty result, not a crash
        conn = None
    try:
        history = {
            symbol: _close_series(conn, symbol, cap=HISTORY_READ_CAP) for symbol in ("VIX", "VIX3M", "VXX")
        }
    finally:
        if conn is not None:
            conn.close()

    result = run(history, config.get("defaults") or {})
    result["generated_at"] = datetime.now(tz=UTC).isoformat()
    result["history_read_cap"] = HISTORY_READ_CAP
    return result
