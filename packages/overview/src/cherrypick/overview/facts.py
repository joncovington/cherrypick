"""Build the morning fact pack: every reading the pre-open report may cite, from suite data only.

The pack is the morning sibling of review's end-of-day fact set: one versioned JSON per session,
the only thing any surface reads. The markdown render, the console page and the narrative all read
the same artifact, so they cannot disagree.

**Pre-open is the constraint everything here is shaped by.** At 08:30 ET the stream cache holds the
prior session's data, the GEX history holds the prior session's last confirmed regime, and a live
quote exists only if the producer is up pre-market. So every reading carries its own provenance: a
``basis`` of ``live`` (a quote fresh within FRESH_QUOTE_SECONDS) or ``prior`` (the last completed
session's value), the ``session`` the value describes, and an ``as_of`` timestamp. The render
prints prior values as prior -- the reference reports do exactly this with their "prior confirmed
readings" labels -- and a value nobody measured is ``null``, never a guess and never zero.

**Where a prior close actually lives** (verified against the production cache, 2026-08-17): the
producer stops at the bell, so ``stream_summary.day_close`` is NULL on every row. The settled close
for session T is ``prev_day_close`` on the row for the session AFTER T -- once today's row exists
(the producer writes it when it starts pre-market), it carries yesterday's settle directly. Before
today's row exists (early builds, weekends), the last recorded trade is the best confirmed prior
value there is, and it is labeled as exactly that.

Read-only over everything it touches: the shared stream cache, the GEX regime history, and (as a
VIX fallback only) MEIC's ``market_context`` table. Writes only into overview's own home.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from cherrypick.core import calendar as _calendar
from cherrypick.core import db as _db

from . import gates as _gates
from . import paths as _paths
from . import score as _score
from . import symbols as _symbols

# v2 adds the record-only `deployment` block (and the HYG/TLT credit-proxy readings it reads).
# Every prior key keeps its meaning, so a v1 pack still renders -- readers must tolerate the block
# being absent on packs built before this version.
FACT_VERSION = 2
PACK = "overview.morning"

# A pre-open quote older than this is not "live". Two hours spans the 07:00 producer start the
# suite actually runs against an 08:30 build without ever accepting yesterday's close as a live tick.
FRESH_QUOTE_SECONDS = 2 * 3600

_ET = ZoneInfo("America/New_York")


def default_session(now: datetime | None = None) -> str:
    """Today in ET when it is a trading day, else the previous trading day. The scheduled job only
    fires on trading days; the fallback is for by-hand runs on a weekend."""
    now = now or datetime.now(tz=_ET)
    day = now.astimezone(_ET).date()
    if not _calendar.is_trading_day(day):
        day = _calendar.previous_trading_day(day)
    return day.isoformat()


def _iso(ts: float | None) -> str | None:
    if not isinstance(ts, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _et_date(ts: float) -> str | None:
    try:
        return datetime.fromtimestamp(float(ts), tz=_ET).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _reading(value, *, basis: str | None, session: str | None, as_of: str | None,
             source: str, label: str, **extra) -> dict:
    return {"value": value, "basis": basis, "session": session, "as_of": as_of,
            "source": source, "label": label, **extra}


def _unmeasured(source: str, label: str) -> dict:
    return _reading(None, basis=None, session=None, as_of=None, source=source, label=label)


def _rows(conn, sql: str, params: tuple = ()) -> list:
    """Tolerant query: a missing table (older cache, empty producer) is no rows, never a raise."""
    try:
        return list(conn.execute(sql, params))
    except Exception:  # noqa: BLE001 -- a reading is never worth raising over
        return []


def _live_quote(conn, symbol: str, now_ts: float) -> dict | None:
    rows = _rows(conn, "SELECT last, updated_at FROM stream_trades WHERE symbol = ?", (symbol,))
    if not rows or rows[0]["last"] is None:
        return None
    updated = rows[0]["updated_at"]
    if not isinstance(updated, (int, float)) or now_ts - float(updated) > FRESH_QUOTE_SECONDS:
        return None
    return {"value": float(rows[0]["last"]), "as_of": _iso(updated)}


def _summary_row(conn, symbol: str, trade_date: str):
    rows = _rows(conn, "SELECT trade_date, prev_day_close, updated_at FROM stream_summary "
                       "WHERE symbol = ? AND trade_date = ?", (symbol, trade_date))
    return rows[0] if rows else None


def _latest_summary_before(conn, symbol: str, before: str):
    rows = _rows(conn, "SELECT trade_date, prev_day_close, updated_at FROM stream_summary "
                       "WHERE symbol = ? AND trade_date < ? ORDER BY trade_date DESC LIMIT 1",
                 (symbol, before))
    return rows[0] if rows else None


def _prior_close_info(conn, symbol: str, session: str) -> dict | None:
    """The prior session's confirmed close and its own daily change, from wherever it actually
    lives (see the module docstring): today's summary row first, the last recorded trade second."""
    today = _summary_row(conn, symbol, session)
    prior = _latest_summary_before(conn, symbol, session)
    if today is not None and today["prev_day_close"] is not None:
        close = float(today["prev_day_close"])
        base = float(prior["prev_day_close"]) if prior and prior["prev_day_close"] is not None else None
        return {
            "close": close,
            "session": prior["trade_date"] if prior else None,
            "as_of": _iso(today["updated_at"]),
            "change_pct": ((close - base) / base * 100.0) if base else None,
            "via": "summary",
        }
    rows = _rows(conn, "SELECT last, updated_at FROM stream_trades WHERE symbol = ?", (symbol,))
    if rows and rows[0]["last"] is not None and isinstance(rows[0]["updated_at"], (int, float)):
        close = float(rows[0]["last"])
        ts = float(rows[0]["updated_at"])
        trade_day = _et_date(ts)
        base_row = _summary_row(conn, symbol, trade_day) if trade_day else None
        base = (float(base_row["prev_day_close"])
                if base_row and base_row["prev_day_close"] is not None else None)
        return {
            "close": close,
            "session": trade_day,
            "as_of": _iso(ts),
            "change_pct": ((close - base) / base * 100.0) if base else None,
            "via": "last_trade",
        }
    return None


def _symbol_reading(conn, symbol: str, session: str, now_ts: float, *, label: str) -> dict:
    """Live-if-fresh, else prior confirmed, else unmeasured -- with the basis recorded.

    Extra fields: ``prior_close``/``prior_change_pct``/``prior_session`` always describe the prior
    completed session (so when basis is ``prior``, ``prior_close`` equals ``value``)."""
    source = f"stream_cache:{symbol}"
    if conn is None:
        return _unmeasured(source, label)
    prior = _prior_close_info(conn, symbol, session)
    live = _live_quote(conn, symbol, now_ts)
    if live:
        return _reading(live["value"], basis="live", session=session, as_of=live["as_of"],
                        source=source, label=label,
                        prior_close=(prior or {}).get("close"),
                        prior_change_pct=(prior or {}).get("change_pct"),
                        prior_session=(prior or {}).get("session"))
    if prior:
        detail = " (last trade)" if prior["via"] == "last_trade" else ""
        return _reading(prior["close"], basis="prior", session=prior["session"],
                        as_of=prior["as_of"], source=source + detail, label=label,
                        prior_close=prior["close"], prior_change_pct=prior["change_pct"],
                        prior_session=prior["session"])
    return _unmeasured(source, label)


def _vix_fallback(session: str) -> dict | None:
    """MEIC records VIX in its market_context table every session it runs. Read-only, last resort,
    and labeled as what it is -- so a pre-registration cache still yields a vol reading."""
    try:
        conn = _db.connect_ro(_paths.meic_paper_db())
    except Exception:  # noqa: BLE001
        return None
    try:
        rows = _rows(conn, "SELECT context_date, vix, updated_at FROM market_context "
                           "WHERE vix IS NOT NULL ORDER BY context_date DESC LIMIT 1")
    finally:
        conn.close()
    if not rows:
        return None
    row = rows[0]
    return _reading(float(row["vix"]), basis="prior", session=row["context_date"],
                    as_of=_iso(row["updated_at"]), source="meic.market_context",
                    label="VIX (MEIC context fallback)")


def _gex_levels(readings: dict[str, Any]) -> dict:
    """The last confirmed GEX regime row for SPX, plus the reference price the gates compare it
    against. Pre-open this is the prior session's final recording -- labeled so."""
    spx = readings.get("spx") or {}
    ref = spx.get("value")
    levels: dict[str, Any] = {
        "symbol": "SPX",
        "reference_price": ref if isinstance(ref, (int, float)) else None,
        "reference_basis": spx.get("basis"),
        "zero_gamma": None, "call_wall": None, "put_wall": None, "net_gex": None,
        "session": None, "as_of": None, "source": "gex.gex_regime_history",
    }
    try:
        conn = _db.connect_ro(_paths.gex_history_db())
    except Exception:  # noqa: BLE001
        return levels
    try:
        rows = _rows(conn, "SELECT trade_date, ts, zero_gamma, call_wall, put_wall, net_gex "
                           "FROM gex_regime_history WHERE symbol = 'SPX' "
                           "ORDER BY ts DESC LIMIT 1")
    finally:
        conn.close()
    if rows:
        row = rows[0]
        for key in ("zero_gamma", "call_wall", "put_wall", "net_gex"):
            value = row[key]
            levels[key] = float(value) if value is not None else None
        levels["session"] = row["trade_date"]
        levels["as_of"] = _iso(row["ts"])
    return levels


def _sectors(conn, session: str) -> dict:
    """Prior-session sector board from the eleven SPDR ETFs. Strongest/weakest only among measured
    movers -- two measured sectors do not pretend to rank eleven."""
    board = []
    for symbol, name in sorted(_symbols.SECTOR_ETFS.items()):
        prior = _prior_close_info(conn, symbol, session) if conn else None
        board.append({
            "symbol": symbol,
            "sector": name,
            "change_pct": (prior or {}).get("change_pct"),
            "close": (prior or {}).get("close"),
            "session": (prior or {}).get("session"),
        })
    measured = [s for s in board if isinstance(s["change_pct"], (int, float))]
    strongest = max(measured, key=lambda s: s["change_pct"]) if measured else None
    weakest = min(measured, key=lambda s: s["change_pct"]) if measured else None
    return {"board": board, "strongest": strongest, "weakest": weakest,
            "measured": len(measured)}


def _close_history(conn, symbols, session: str, days: int) -> dict[str, list[dict]]:
    """Completed daily closes per symbol, oldest first, for the deployment score's math.

    Two columns carry a close and they are dated DIFFERENTLY, which is the whole subtlety here.
    The backfill writes ``day_close`` on the row for the session it belongs to. The live producer
    stops at the bell and never writes ``day_close``, so a live-written session's settle appears
    only as ``prev_day_close`` on the row for the session AFTER it (see the module docstring).
    Attributing ``prev_day_close`` to its own row's date would shift the whole series one session
    and quietly corrupt every SMA and percentile built on it, so it is attributed to the preceding
    row's date instead, and ``day_close`` wins wherever both exist.

    ``session``'s own row is READ but never appears in the series. It has to be read, because the
    prior session's settle is precisely what its ``prev_day_close`` carries -- excluding the row
    outright would leave the series permanently one session stale, comparing today's VIX against a
    year that stops the day before yesterday. Its own date is then dropped from the result, since
    today's bar is partial pre-open and is never a completed close.
    """
    out: dict[str, list[dict]] = {}
    if conn is None:
        return out
    for symbol in symbols:
        rows = _rows(conn, "SELECT trade_date, day_close, prev_day_close FROM stream_summary "
                           "WHERE symbol = ? AND trade_date <= ? "
                           "ORDER BY trade_date DESC LIMIT ?", (symbol, session, days + 1))
        rows = list(reversed(rows))
        closes: dict[str, float] = {}
        for index, row in enumerate(rows):
            if row["prev_day_close"] is not None and index > 0:
                closes[rows[index - 1]["trade_date"]] = float(row["prev_day_close"])
        for row in rows:  # day_close is the row's own session and outranks the chained value
            if row["day_close"] is not None:
                closes[row["trade_date"]] = float(row["day_close"])
        closes.pop(session, None)  # read for its prev_day_close only; a partial bar is not a close
        series = [{"session": day, "close": closes[day]} for day in sorted(closes)][-days:]
        if series:
            out[symbol] = series
    return out


def _calendar_block(session: str) -> dict:
    day = date.fromisoformat(session)
    year_known = _calendar.fomc_year_known(day.year)
    upcoming_fomc = None
    if year_known:
        candidates = [d for d in (_calendar.fomc_dates(day.year)
                                  + (_calendar.fomc_dates(day.year + 1)
                                     if _calendar.fomc_year_known(day.year + 1) else []))
                      if d >= day]
        upcoming_fomc = candidates[0].isoformat() if candidates else None
    return {
        "is_fomc_day": _calendar.is_fomc_day(day) if year_known else None,
        "next_fomc": upcoming_fomc,
        "fomc_year_known": year_known,
        "is_triple_witching": _calendar.is_triple_witching(day),
        "is_quarterly_expiry": _calendar.is_quarterly_expiry(day),
        "next_trading_day": _calendar.next_trading_day(day).isoformat(),
    }


def build(session: str | None = None, now: datetime | None = None) -> dict:
    session = session or default_session(now)
    now = now or datetime.now(tz=timezone.utc)
    now_ts = now.timestamp()

    try:
        cache = _db.connect_ro(_paths.stream_cache_db())
    except Exception:  # noqa: BLE001 -- no cache is a pack of unmeasured readings, not a crash
        cache = None

    try:
        readings: dict[str, Any] = {
            "spx": _symbol_reading(cache, "SPX", session, now_ts, label="S&P 500 (SPX)"),
            "vix": _symbol_reading(cache, "VIX", session, now_ts, label="VIX"),
            "vix3m": _symbol_reading(cache, "VIX3M", session, now_ts, label="VIX3M"),
            "vvix": _symbol_reading(cache, "VVIX", session, now_ts, label="VVIX (vol of vol)"),
            "wti_proxy": _symbol_reading(cache, "USO", session, now_ts,
                                         label=_symbols.COMMODITY_PROXIES["USO"]),
            "gold_proxy": _symbol_reading(cache, "GLD", session, now_ts,
                                          label=_symbols.COMMODITY_PROXIES["GLD"]),
            "hy_credit_proxy": _symbol_reading(cache, "HYG", session, now_ts,
                                               label=_symbols.CREDIT_PROXIES["HYG"]),
            "treasury_proxy": _symbol_reading(cache, "TLT", session, now_ts,
                                              label=_symbols.CREDIT_PROXIES["TLT"]),
        }
        if readings["vix"]["value"] is None:
            fallback = _vix_fallback(session)
            if fallback:
                readings["vix"] = fallback

        spx = readings["spx"]
        readings["spx_prior_change_pct"] = _reading(
            spx.get("prior_change_pct"), basis="prior",
            session=spx.get("prior_session"), as_of=spx.get("as_of"),
            source="stream_cache:SPX", label="SPX prior-session change %",
        )

        sectors = _sectors(cache, session)
        history = _close_history(cache, _symbols.HISTORY_DAYS, session,
                                 _symbols.HISTORY_LOOKBACK)
    finally:
        if cache is not None:
            cache.close()

    levels = _gex_levels(readings)
    gate_list = _gates.evaluate(readings, levels)
    return {
        "pack": PACK,
        "fact_version": FACT_VERSION,
        "session": session,
        "generated_at": now.astimezone(timezone.utc).isoformat(),
        "readings": readings,
        "levels": levels,
        "sectors": sectors,
        "gates": gate_list,
        "phase": _gates.phase(gate_list),
        "deployment": _score.evaluate(readings, history, _symbols.SECTOR_ETFS),
        "calendar": _calendar_block(session),
    }


def write(facts: dict) -> str:
    """Atomic tmp-then-replace, same as every artifact writer in the suite."""
    path = _paths.facts_path(facts["session"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(facts, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)
    return str(path)


def read(session: str) -> dict | None:
    try:
        return json.loads(_paths.facts_path(session).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
