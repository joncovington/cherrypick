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
from datetime import UTC, date, datetime
from typing import Any

from cherrypick.core import calendar as _calendar
from cherrypick.core import db as _db

# One ET for the suite — see cherrypick.core.clock.
from cherrypick.core.clock import ET as _ET

from . import gates as _gates
from . import paths as _paths
from . import score as _score
from . import symbols as _symbols

# v2 adds the record-only `deployment` block (and the HYG/TLT credit-proxy readings it reads).
# Every prior key keeps its meaning, so a v1 pack still renders -- readers must tolerate the block
# being absent on packs built before this version.
FACT_VERSION = 3  # 3: + vol_regime block and its readings (2026-08-25)
PACK = "overview.morning"

# A pre-open quote older than this is not "live". Two hours spans the 07:00 producer start the
# suite actually runs against an 08:30 build without ever accepting yesterday's close as a live tick.
FRESH_QUOTE_SECONDS = 2 * 3600


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
        return datetime.fromtimestamp(float(ts), tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _et_date(ts: float) -> str | None:
    try:
        return datetime.fromtimestamp(float(ts), tz=_ET).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _reading(
    value, *, basis: str | None, session: str | None, as_of: str | None, source: str, label: str, **extra
) -> dict:
    return {
        "value": value,
        "basis": basis,
        "session": session,
        "as_of": as_of,
        "source": source,
        "label": label,
        **extra,
    }


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
    rows = _rows(
        conn,
        "SELECT trade_date, prev_day_close, updated_at FROM stream_summary "
        "WHERE symbol = ? AND trade_date = ?",
        (symbol, trade_date),
    )
    return rows[0] if rows else None


def _latest_summary_before(conn, symbol: str, before: str):
    rows = _rows(
        conn,
        "SELECT trade_date, prev_day_close, updated_at FROM stream_summary "
        "WHERE symbol = ? AND trade_date < ? ORDER BY trade_date DESC LIMIT 1",
        (symbol, before),
    )
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
        # The base is the close of the session BEFORE the one this print belongs to, and it is read
        # off that session's own summary row (`prev_day_close`). Pre-open — which is the only window
        # this package runs in — there is no row for today yet, so keying the base to the print's
        # CALENDAR date found nothing and the change came back None every single morning: the gate
        # had never once been measured. An overnight print also carries yesterday's close while
        # `_et_date` calls it today, so the same lookup mislabelled the prior session as the current
        # one. Falling back to the newest completed session's row fixes both — and the session that
        # row names IS the session the print belongs to, in either branch.
        base_row = _summary_row(conn, symbol, trade_day) if trade_day else None
        if base_row is None:
            base_row = _latest_summary_before(conn, symbol, session)
        base = (
            float(base_row["prev_day_close"]) if base_row and base_row["prev_day_close"] is not None else None
        )
        return {
            "close": close,
            "session": base_row["trade_date"] if base_row else trade_day,
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
        return _reading(
            live["value"],
            basis="live",
            session=session,
            as_of=live["as_of"],
            source=source,
            label=label,
            prior_close=(prior or {}).get("close"),
            prior_change_pct=(prior or {}).get("change_pct"),
            prior_session=(prior or {}).get("session"),
        )
    if prior:
        detail = " (last trade)" if prior["via"] == "last_trade" else ""
        return _reading(
            prior["close"],
            basis="prior",
            session=prior["session"],
            as_of=prior["as_of"],
            source=source + detail,
            label=label,
            prior_close=prior["close"],
            prior_change_pct=prior["change_pct"],
            prior_session=prior["session"],
        )
    return _unmeasured(source, label)


def _vix_fallback(session: str) -> dict | None:
    """MEIC records VIX in its market_context table every session it runs. Read-only, last resort,
    and labeled as what it is -- so a pre-registration cache still yields a vol reading."""
    try:
        conn = _db.connect_ro(_paths.meic_paper_db())
    except Exception:  # noqa: BLE001
        return None
    try:
        rows = _rows(
            conn,
            "SELECT context_date, vix, updated_at FROM market_context "
            "WHERE vix IS NOT NULL ORDER BY context_date DESC LIMIT 1",
        )
    finally:
        conn.close()
    if not rows:
        return None
    row = rows[0]
    return _reading(
        float(row["vix"]),
        basis="prior",
        session=row["context_date"],
        as_of=_iso(row["updated_at"]),
        source="meic.market_context",
        label="VIX (MEIC context fallback)",
    )


def _gex_levels(readings: dict[str, Any]) -> dict:
    """The last confirmed GEX regime row for SPX, plus the reference price the gates compare it
    against. Pre-open this is the prior session's final recording -- labeled so."""
    spx = readings.get("spx") or {}
    ref = spx.get("value")
    levels: dict[str, Any] = {
        "symbol": "SPX",
        "reference_price": ref if isinstance(ref, (int, float)) else None,
        "reference_basis": spx.get("basis"),
        "zero_gamma": None,
        "call_wall": None,
        "put_wall": None,
        "net_gex": None,
        "session": None,
        "as_of": None,
        "source": "gex.gex_regime_history",
    }
    try:
        conn = _db.connect_ro(_paths.gex_history_db())
    except Exception:  # noqa: BLE001
        return levels
    try:
        rows = _rows(
            conn,
            "SELECT trade_date, ts, zero_gamma, call_wall, put_wall, net_gex "
            "FROM gex_regime_history WHERE symbol = 'SPX' "
            "ORDER BY ts DESC LIMIT 1",
        )
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
        board.append(
            {
                "symbol": symbol,
                "sector": name,
                "change_pct": (prior or {}).get("change_pct"),
                "close": (prior or {}).get("close"),
                "session": (prior or {}).get("session"),
            }
        )
    measured = [s for s in board if isinstance(s["change_pct"], (int, float))]
    strongest = max(measured, key=lambda s: s["change_pct"]) if measured else None
    weakest = min(measured, key=lambda s: s["change_pct"]) if measured else None
    return {"board": board, "strongest": strongest, "weakest": weakest, "measured": len(measured)}


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
        rows = _rows(
            conn,
            "SELECT trade_date, day_close, prev_day_close FROM stream_summary "
            "WHERE symbol = ? AND trade_date <= ? "
            "ORDER BY trade_date DESC LIMIT ?",
            (symbol, session, days + 1),
        )
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


# --------------------------------------------------------------------------- the vol term structure
# The curve, front to back. `dte` is nominal and is what the slope is quoted against; the symbol is
# provenance. VIX6M/VIX1Y were admitted 2026-08-25 after an entitlement probe (docs/regime-recorder-plan.md).
_TERM_POINTS = (
    # VIX1D admitted 2026-09-02 at the advisor's request (flies band-containment, proposal #110):
    # the one-day implied range is the front-most point the curve can carry, and the recorder has
    # streamed it since 08-24.
    ("vix1d", "VIX1D", 1),
    ("vix9d", "VIX9D", 9),
    ("vix", "VIX", 30),
    ("vix3m", "VIX3M", 91),
    ("vix6m", "VIX6M", 182),
    ("vix1y", "VIX1Y", 365),
)

# Readings whose position in their own trailing range is worth recording beside the level. A level
# alone does not say whether it is unusual, and "VIX 15.7" means something different in a year that
# never left 12-18 than in one that touched 60.
_VOL_HISTORY_SYMBOLS = frozenset({"VIX1D", "VIX9D", "VIX6M", "VIX1Y", "VVIX", "SKEW"})

# Readings whose LIVE quote the feed serves but whose DAILY series it does not. Declared, not
# inferred: nothing in the data can distinguish "no history yet" from "no history ever", and the
# difference is the whole message. SKEW's 270-day backfill returned five scattered rows across seven
# months (one of them a zero), on the same connection that delivered a clean ~378 for every other
# vol reading -- so its percentile is not waiting to fill, it is unavailable, and saying "only 5
# closes on file" would promise a gap that closes and never does. A permanent refusal that looks
# temporary is the thing that teaches a reader to skim the row.
_NO_DAILY_SERIES = frozenset({"skew"})

_PERCENTILE_READINGS = (
    ("vix1d", "VIX1D"),
    ("vix9d", "VIX9D"),
    ("vix", "VIX"),
    ("vix3m", "VIX3M"),
    ("vix6m", "VIX6M"),
    ("vix1y", "VIX1Y"),
    ("vvix", "VVIX"),
    ("skew", "SKEW"),
)

# The window, the floor and the formula all come from `score`, which already ranks VIX against its
# own history and renders the answer on this same page. Restating any of the three here would put two
# different percentiles for one reading in front of one reader -- not a rounding difference but a
# contradiction. Landed at 270 closes and an inclusive count before that was noticed on 2026-08-25:
# the card read "18th pctile of 270" directly above the score's "17th percentile of 252 sessions".
_PERCENTILE_LOOKBACK = _score.PERCENTILE_LOOKBACK
_PERCENTILE_MIN_SAMPLES = _score.MIN_HISTORY_FOR_RANK

# A monthly norm needs YEARS, not a year -- one August tells you about one August. This reads the
# multi-year `daily_closes` series rather than the 270-day cache window the percentiles use, and
# refuses below three observations of the month in question.
_SEASONAL_MIN_YEARS = 3

# The suite already has ONE definition of contango and it is not a slope sign: `curve.regime` calls
# it VIX/VIX3M below `contango_max`, with the buffer below 1.0 so a knife-edge 0.999 day is not read
# as the harvest regime. This block restates that value rather than inventing a second answer to the
# same question -- two definitions of contango across two packages is exactly the drift the shared
# GEX engine exists to prevent, and `tests/test_vol_regime.py` pins these equal so the copy cannot
# rot quietly. Overview cannot import curve (no package here imports another), so the guard lives in
# the test, where it can.
_CONTANGO_MAX = 0.97


def _seasonal_norm(month: int) -> dict:
    """The mean VIX close for this calendar month across every year on file.

    Reads gex's `daily_closes` (years) rather than the cache's 270-day window (one year), because a
    month-of-year norm built from a single observation of that month is not a norm.
    """
    out = {"month": month, "norm": None, "years": 0, "reason": None}
    try:
        conn = _db.connect_ro(_paths.gex_history_db())
    except Exception:  # noqa: BLE001 -- no history is unmeasured, never a crash
        out["reason"] = "no_history_db"
        return out
    try:
        rows = _rows(conn, "SELECT trade_date, close FROM daily_closes WHERE symbol = 'VIX' AND close > 0")
    except Exception:  # noqa: BLE001
        out["reason"] = "no_daily_closes_table"
        return out
    finally:
        conn.close()
    vals = [float(r["close"]) for r in rows if str(r["trade_date"])[5:7] == f"{month:02d}"]
    years = {str(r["trade_date"])[:4] for r in rows if str(r["trade_date"])[5:7] == f"{month:02d}"}
    out["years"] = len(years)
    if len(years) < _SEASONAL_MIN_YEARS:
        out["reason"] = "too_few_years"
        return out
    out["norm"] = round(sum(vals) / len(vals), 2)
    return out


def _vol_regime(readings: dict, history: dict[str, list[dict]], session: str) -> dict:
    """The vol term structure, its slope, and where each point sits in its own trailing range.

    **Record-only, and deliberately wired to no gate.** The phase gates decide whether the suite
    deploys, and their semantics are a measurement boundary -- adding an input would change what a
    GREEN means and make every prior session incomparable. This block is here to be READ (by the
    console panel, the morning narrative and the advisor's fact pack); promoting any of it to a gate
    is a separate, journalled decision.

    Every value is refused rather than guessed: an unmeasured reading, a curve point the feed did
    not serve, a percentile with too thin a sample. That matters more here than in most blocks,
    because a vol panel is read at a glance and a confidently-wrong percentile is worse than a gap.
    """
    curve = []
    for key, symbol, dte in _TERM_POINTS:
        reading = readings.get(key) or {}
        curve.append(
            {
                "point": key,
                "symbol": symbol,
                "dte": dte,
                "value": reading.get("value"),
                "basis": reading.get("basis"),
            }
        )
    by_key = {c["point"]: c["value"] for c in curve}

    def _slope(a: str, b: str) -> float | None:
        lo, hi = by_key.get(a), by_key.get(b)
        if lo in (None, 0) or hi is None:
            return None
        return round(100.0 * (hi - lo) / lo, 1)

    # Three readings of one curve, because they answer different questions. The FRONT (9D vs 30D) is
    # event pricing -- an FOMC or CPI inside the next nine days lifts it and nothing further out.
    # The MID (30D vs 3M) is the classic term-structure read and the one the regime label uses. The
    # BACK (9D vs 1Y) is the structural carry a short-premium book is actually paid for.
    front = _slope("vix9d", "vix")
    mid = _slope("vix", "vix3m")
    back = _slope("vix9d", "vix1y")

    vix, vix3m = by_key.get("vix"), by_key.get("vix3m")
    ratio = round(vix / vix3m, 4) if (vix is not None and vix3m) else None
    if ratio is None:
        shape, shape_reason = None, "vix_or_vix3m_unmeasured"
    else:
        shape, shape_reason = ("contango" if ratio < _CONTANGO_MAX else "backwardation"), None

    percentiles = {}
    for key, symbol in _PERCENTILE_READINGS:
        value = (readings.get(key) or {}).get("value")
        series = [row["close"] for row in history.get(symbol, [])][-_PERCENTILE_LOOKBACK:]
        entry = {"value": value, "samples": len(series), "percentile": None, "reason": None}
        if value is None:
            entry["reason"] = "reading_unmeasured"
        elif key in _NO_DAILY_SERIES:
            entry["reason"] = "no_daily_series"
        elif len(series) < _PERCENTILE_MIN_SAMPLES:
            entry["reason"] = "too_few_closes"
        else:
            entry["percentile"] = round(_score.percentile_rank(series, float(value)), 1)
        percentiles[key] = entry

    seasonal = _seasonal_norm(int(session[5:7]))
    vix_value = (readings.get("vix") or {}).get("value")
    if seasonal.get("norm") and vix_value is not None:
        seasonal["vix_vs_norm_pct"] = round(
            100.0 * (float(vix_value) - seasonal["norm"]) / seasonal["norm"], 1
        )
    else:
        seasonal["vix_vs_norm_pct"] = None

    return {
        "curve": curve,
        "slope": {"front_9d_30d_pct": front, "mid_30d_3m_pct": mid, "back_9d_1y_pct": back},
        "vix_vix3m_ratio": ratio,
        "shape": shape,
        "shape_reason": shape_reason,
        "percentiles": percentiles,
        "seasonality": seasonal,
        "measured_points": sum(1 for c in curve if c["value"] is not None),
        "total_points": len(curve),
        "record_only": True,
    }


def _calendar_block(session: str) -> dict:
    day = date.fromisoformat(session)
    year_known = _calendar.fomc_year_known(day.year)
    upcoming_fomc = None
    if year_known:
        candidates = [
            d
            for d in (
                _calendar.fomc_dates(day.year)
                + (_calendar.fomc_dates(day.year + 1) if _calendar.fomc_year_known(day.year + 1) else [])
            )
            if d >= day
        ]
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
    now = now or datetime.now(tz=UTC)
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
            # The rest of the term structure, plus the tail reading. Added 2026-08-25 for the
            # vol-regime block; additive to the pack and read by no gate.
            "vix1d": _symbol_reading(cache, "VIX1D", session, now_ts, label="VIX1D (1-day implied)"),
            "vix9d": _symbol_reading(cache, "VIX9D", session, now_ts, label="VIX9D"),
            "vix6m": _symbol_reading(cache, "VIX6M", session, now_ts, label="VIX6M"),
            "vix1y": _symbol_reading(cache, "VIX1Y", session, now_ts, label="VIX1Y"),
            "skew": _symbol_reading(cache, "SKEW", session, now_ts, label="SKEW (tail pricing)"),
            "vvix": _symbol_reading(cache, "VVIX", session, now_ts, label="VVIX (vol of vol)"),
            "wti_proxy": _symbol_reading(
                cache, "USO", session, now_ts, label=_symbols.COMMODITY_PROXIES["USO"]
            ),
            "gold_proxy": _symbol_reading(
                cache, "GLD", session, now_ts, label=_symbols.COMMODITY_PROXIES["GLD"]
            ),
            "hy_credit_proxy": _symbol_reading(
                cache, "HYG", session, now_ts, label=_symbols.CREDIT_PROXIES["HYG"]
            ),
            "treasury_proxy": _symbol_reading(
                cache, "TLT", session, now_ts, label=_symbols.CREDIT_PROXIES["TLT"]
            ),
        }
        if readings["vix"]["value"] is None:
            fallback = _vix_fallback(session)
            if fallback:
                readings["vix"] = fallback

        spx = readings["spx"]
        readings["spx_prior_change_pct"] = _reading(
            spx.get("prior_change_pct"),
            basis="prior",
            session=spx.get("prior_session"),
            as_of=spx.get("as_of"),
            source="stream_cache:SPX",
            label="SPX prior-session change %",
        )

        sectors = _sectors(cache, session)
        # The live score reads the tail of a longer stored series -- only a year of it. The vol
        # complex rides along for the percentile block; the union is deliberate rather than two
        # reads, so one series can never disagree with itself between two consumers.
        history = _close_history(
            cache,
            sorted(set(_symbols.HISTORY_DAYS) | _VOL_HISTORY_SYMBOLS),
            session,
            _symbols.HISTORY_LOOKBACK,
        )
    finally:
        if cache is not None:
            cache.close()

    levels = _gex_levels(readings)
    gate_list = _gates.evaluate(readings, levels)
    return {
        "pack": PACK,
        "fact_version": FACT_VERSION,
        "session": session,
        "generated_at": now.astimezone(UTC).isoformat(),
        "readings": readings,
        "levels": levels,
        "sectors": sectors,
        "gates": gate_list,
        "phase": _gates.phase(gate_list),
        "deployment": _score.evaluate(readings, history, _symbols.SECTOR_ETFS),
        "vol_regime": _vol_regime(readings, history, session),
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
