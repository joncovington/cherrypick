"""The one way to join a timestamp against the suite's recorded market regime.

The gex package's recorder writes two joinable series into its own history database
(``~/.cherrypick/data/gex/gex_history.db``): ``market_regime_history`` (the vol complex, breadth
and cross-asset quotes, one row per reading per ~minute during RTH — raw measures only) and
``gex_regime_history`` (net GEX, zero gamma, walls, per symbol per ~5 minutes). This module is the
read side every consumer joins through — review tagging entries and exits, the advisor's fact pack,
the console — because three consumers doing the nearest-row join by hand is how three subtly
different staleness rules get born.

Two rules, stated once here and relied on everywhere:

- **At-or-before only, never nearest.** ``regime_at(ts)`` answers "what was the regime WHEN that
  happened", and a sample from after the moment is look-ahead. A backtest-shaped consumer that
  joined forward would quietly grade decisions against information they could not have had.
- **Beyond the staleness bound is unmeasured, never the last value.** A join that returns a
  two-hour-old VIX as "the regime" makes a recorder outage look like a calm market — the exact
  failure the recorder's own refusal rows exist to prevent. The default bound (15 minutes) is
  generous against a 1-minute series precisely so that tripping it means something is actually
  wrong, not that a sample landed late.

Derived values (the VIX/VIX3M ratio, RSP/SPY, HYG/LQD, sector dispersion) are computed HERE, at
read time, from the raw readings — recording them would freeze a derivation this module can re-cut
forever (the suite's store-the-measure rule).

This module reads a database another package's recorder writes, which is the suite's ordinary
read-model relationship (the console reads the same file) — not an import reach-back; core stays
import-self-contained.
"""

from __future__ import annotations

import statistics
from pathlib import Path

from cherrypick.core import db as _db
from cherrypick.core import home as _home

DEFAULT_MAX_STALENESS_SECONDS = 900.0

_SECTORS = ("xlb", "xlc", "xle", "xlf", "xli", "xlk", "xlp", "xlre", "xlu", "xlv", "xly")

# (derived name, numerator reading, denominator reading) — computed only when both parts were
# usable at the same sample tick, so a ratio can never mix two moments.
_RATIOS = (
    ("vix_vix3m_ratio", "vix", "vix3m"),
    ("vvix_vix_ratio", "vvix", "vix"),
    ("rsp_spy_ratio", "rsp", "spy"),
    ("hyg_lqd_ratio", "hyg", "lqd"),
    ("gld_spy_ratio", "gld", "spy"),
)


def default_history_db() -> Path:
    """Where the gex recorder keeps its history (the default; a relocated config overrides by
    passing ``history_db`` explicitly)."""
    return _home.data_dir("gex") / "gex_history.db"


def _unmeasured(reason: str) -> dict:
    return {"status": "unmeasured", "reason": reason}


def _market_block(conn, ts: float, max_staleness: float) -> dict:
    row = conn.execute(
        "SELECT MAX(ts) FROM market_regime_history WHERE ts <= ?", (ts,)
    ).fetchone()
    if not row or row[0] is None:
        return _unmeasured("no_sample_at_or_before")
    sample_ts = float(row[0])
    if (ts - sample_ts) > max_staleness:
        return _unmeasured("stale_sample")
    readings: dict[str, dict] = {}
    trade_date: str | None = None
    for r in conn.execute(
        "SELECT trade_date, reading, symbol, value, basis_ts, usable, reason "
        "FROM market_regime_history WHERE ts = ?",
        (sample_ts,),
    ):
        trade_date = r["trade_date"]
        readings[r["reading"]] = {
            "value": None if r["value"] is None else float(r["value"]),
            "symbol": r["symbol"],
            "usable": bool(r["usable"]),
            "reason": r["reason"],
        }
    derived: dict[str, float | None] = {}
    for name, num, den in _RATIOS:
        a, b = readings.get(num), readings.get(den)
        ok = a and b and a["usable"] and b["usable"] and b["value"]
        derived[name] = (a["value"] / b["value"]) if ok else None
    derived["sector_dispersion"] = _sector_dispersion(conn, readings, trade_date)
    return {
        "status": "measured",
        "sample_ts": sample_ts,
        "age_seconds": round(ts - sample_ts, 1),
        "readings": readings,
        "derived": derived,
    }


def _sector_dispersion(conn, readings: dict, trade_date: str | None) -> float | None:
    """Population stdev of the sectors' same-moment % change vs each sector's close from BEFORE the
    sample's own session — bounded by the sample's trade_date, because a retroactive join runs
    after that day's close has landed in ``daily_closes`` and "latest" would be look-ahead.
    Refuses (None) under 8 of 11 measurable sectors — a dispersion over three sectors reads as a
    market statistic and is not one."""
    if trade_date is None:
        return None
    changes: list[float] = []
    for s in _SECTORS:
        r = readings.get(s)
        if not r or not r["usable"] or not r["value"]:
            continue
        row = conn.execute(
            "SELECT close FROM daily_closes WHERE symbol = ? AND trade_date < ? AND close > 0 "
            "ORDER BY trade_date DESC LIMIT 1",
            (r["symbol"], trade_date),
        ).fetchone()
        if not row:
            continue
        changes.append((r["value"] - float(row[0])) / float(row[0]) * 100.0)
    if len(changes) < 8:
        return None
    return statistics.pstdev(changes)


def _gex_block(conn, ts: float, max_staleness: float, symbol: str | None) -> dict:
    if symbol is not None:
        symbols = [symbol.strip().upper()]
    else:
        symbols = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT symbol FROM gex_regime_history WHERE ts <= ?", (ts,)
            )
        ]
    out: dict[str, dict] = {}
    for sym in symbols:
        row = conn.execute(
            "SELECT ts, spot, net_gex, net_gex_vol, zero_gamma, call_wall, put_wall, expiration "
            "FROM gex_regime_history WHERE symbol = ? AND ts <= ? ORDER BY ts DESC LIMIT 1",
            (sym, ts),
        ).fetchone()
        if row is None:
            out[sym] = _unmeasured("no_sample_at_or_before")
        elif (ts - float(row["ts"])) > max_staleness:
            out[sym] = _unmeasured("stale_sample")
        else:
            out[sym] = {
                "status": "measured",
                "sample_ts": float(row["ts"]),
                "age_seconds": round(ts - float(row["ts"]), 1),
                "spot": row["spot"],
                "net_gex": row["net_gex"],
                "net_gex_vol": row["net_gex_vol"],
                "zero_gamma": row["zero_gamma"],
                "call_wall": row["call_wall"],
                "put_wall": row["put_wall"],
                "expiration": row["expiration"],
            }
    return out


def regime_at(
    ts: float,
    *,
    symbol: str | None = None,
    history_db: Path | str | None = None,
    max_staleness_seconds: float = DEFAULT_MAX_STALENESS_SECONDS,
) -> dict:
    """The recorded market regime as of unix timestamp ``ts`` (at-or-before; see module docstring).

    Returns ``{"ts", "market", "gex"}``: ``market`` is one sample tick's readings plus the derived
    ratios, or ``{"status": "unmeasured", "reason"}``; ``gex`` maps each symbol (all recorded, or
    just ``symbol``) to its latest summary row or an unmeasured marker. A missing or table-less
    history database is unmeasured throughout — never a raise, never a default."""
    path = Path(history_db) if history_db is not None else default_history_db()
    if not path.exists():
        missing = _unmeasured("no_history_db")
        return {"ts": ts, "market": missing, "gex": {} if symbol is None else {symbol.upper(): missing}}
    conn = _db.connect_ro(path)
    try:
        try:
            market = _market_block(conn, float(ts), float(max_staleness_seconds))
        except Exception:  # noqa: BLE001 — an older DB without the table is unmeasured, not an error
            market = _unmeasured("no_market_regime_table")
        try:
            gex = _gex_block(conn, float(ts), float(max_staleness_seconds), symbol)
        except Exception:  # noqa: BLE001
            gex = {} if symbol is None else {symbol.upper(): _unmeasured("no_gex_regime_table")}
        return {"ts": ts, "market": market, "gex": gex}
    finally:
        conn.close()
