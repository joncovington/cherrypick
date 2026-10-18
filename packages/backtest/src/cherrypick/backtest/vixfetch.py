"""The VIX minute series for the vendor store: what MEIC's replay reads its VIX from, tick by tick.

MEIC moves its short-delta band the moment VIX crosses 18, 25 or 35, and a design-window session's VIX
range straddled one of those about one time in five, so a VIX fixed for the day cannot reproduce it
(docs/quote-history-replay-plan.md, Phase 2, amended 2026-10-18). The account's index tier is refused
(`index_history_price` needs a paid index subscription), but every VIX option reports the VIX index as
its underlying price, so one narrow request a session (the nearest expiry after it, one strike either
side, one-minute bars) gives the day's series.

Written into the store's `market_regime_history`, the recorder's own table, in the recorder's own form
(`reading = 'vix'`, `symbol = 'VIX'`, `usable = 1`, `basis_ts` the minute), so `core.regime.markets_at`
reads a vendor session exactly as it reads a recorded one. A session is filled whole or not at all; a
session already holding VIX rows is skipped (resumable), and `refetch` replaces them.
"""

from __future__ import annotations

from . import store

READING = "vix"
SYMBOL = "VIX"
#: Expiries tried per session, nearest first, before the session is refused.
EXPIRIES_TRIED = 3


def _sessions(conn, symbol: str, start: str, end: str) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            "SELECT trade_date FROM vendor_sessions WHERE symbol = ? AND status = ? "
            "AND trade_date BETWEEN ? AND ? "
            "ORDER BY trade_date",
            (symbol.upper(), store.COMPLETE, start, end),
        )
    ]


def _has_vix(conn, day: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM market_regime_history WHERE trade_date = ? AND reading = ? LIMIT 1", (day, READING)
        ).fetchone()
        is not None
    )


def write_series(conn, day: str, minutes: list[tuple[float, float]]) -> int:
    """Replace `day`'s VIX rows with `minutes` ((epoch, value)), in one transaction."""
    conn.execute("DELETE FROM market_regime_history WHERE trade_date = ? AND reading = ?", (day, READING))
    conn.executemany(
        "INSERT INTO market_regime_history "
        "(trade_date, ts, reading, symbol, value, basis_ts, usable, reason) "
        "VALUES (?, ?, ?, ?, ?, ?, 1, NULL)",
        [(day, ts, READING, SYMBOL, value, ts) for ts, value in minutes],
    )
    conn.commit()
    return len(minutes)


def fetch_vix(
    settings: dict,
    client,
    symbol: str,
    start: str,
    end: str,
    *,
    cadence: float | None = None,
    refetch: bool = False,
    log=print,
) -> dict:
    """Fill the VIX minute series for every complete session of `symbol`'s store in [start, end]."""
    conn = store.connect(settings, symbol, cadence)
    try:
        days = [d for d in _sessions(conn, symbol, start, end) if refetch or not _has_vix(conn, d)]
        expirations = sorted(client.expirations(SYMBOL)) if days else []
        written, empty, refused = {}, [], {}
        for day in days:
            # The nearest expiry is sometimes a thin weekly the vendor holds nothing for (2022-12-01,
            # 2022-12-28, 2022-12-30, 2026-05-20); every expiry reports the same spot index, so the
            # next one is as good. Up to EXPIRIES_TRIED, nearest first.
            later = [e for e in expirations if e > day][:EXPIRIES_TRIED]
            if not later:
                refused[day] = "no_vix_expiry_after_session"
                continue
            minutes, last_error = [], None
            for expiry in later:
                try:
                    minutes = client.vix_minutes(day, expiry)
                except Exception as exc:  # noqa: BLE001 -- one session's refusal, never the run's
                    last_error = f"{type(exc).__name__}: {exc}"[:200]
                    continue
                if minutes:
                    break
            if not minutes and last_error is not None:
                refused[day] = last_error
                continue
            if not minutes:
                empty.append(day)  # never an empty series: the replay falls back to the declared open
                continue
            written[day] = write_series(conn, day, minutes)
            log(f"{day} vix {written[day]} minutes")
        return {"sessions": len(days), "written": written, "empty": empty, "refused": refused}
    finally:
        conn.close()
