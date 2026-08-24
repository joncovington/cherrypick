"""The suite-level market-regime sampler: one row per (reading, ~minute) during RTH.

The GEX half of the regime already has a joinable time series (``gex_regime_history``); this is the
other half — the vol complex, breadth, and cross-asset quotes nothing in the suite recorded intraday
(VIX existed per-tick only inside MEIC's own store, per-day in curve's, once pre-open in overview's).
Design record: ``docs/regime-recorder-plan.md`` at the repo root.

Rules, each inherited from a lesson already paid for elsewhere:

- **Raw measures only, never buckets and never derived values.** Ratios (VIX/VIX3M, RSP/SPY, …) and
  dispersion are pure functions over rows this table already holds, so recording them would freeze a
  derivation that read-side code (``cherrypick.core.regime``) can re-cut forever.
- **RTH-gated and basis-stamped.** Samples exist only 09:30–16:00 ET on trading days, and every row
  carries the quote's own timestamp — a recorder that samples through the night writes a frozen
  feed as a flat line (the 2026-08-19 spot-trail incident, and curve's reason for the same posture).
- **A refusal is a marked row, not a gap.** A stale or missing quote inside RTH writes
  ``usable = 0`` with the reason, so "the feed was thin" and "the recorder was down" read
  differently afterwards (the flies ``fly_snapshots`` distinction). Outside RTH nothing is written
  at all — that gap is the legible "market closed".
- **The long-row shape is deliberate.** One row per reading means the probe-gated additions the plan
  names (SKEW, the internals family, futures) are new READINGS entries, not schema migrations.

``daily_closes`` rides along: confirmed session closes harvested from ``stream_summary``'s
``day_close`` rows (each row's close belongs to its OWN session — the trap ``overview`` documents)
into a permanent table, because the cache offers no retention guarantee and the moving-average /
percentile family needs a durable series. Append-only: a (symbol, date) pair is written once.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime
from pathlib import Path

from cherrypick.core import calendar as _calendar
from cherrypick.core.clock import ET as _ET

from cherrypick.gex import provider as _provider

# reading name -> source symbol. The reading name is the stable identity (it survives a source
# symbol change); the symbol is provenance, recorded on every row. Raw quote readings only — see
# the module docstring for why derived values are excluded on purpose.
READINGS: dict[str, str] = {
    # vol complex
    "vix": "VIX",
    "vix3m": "VIX3M",
    "vix1d": "VIX1D",
    "vvix": "VVIX",
    # breadth / cross-asset
    "spy": "SPY",
    "rsp": "RSP",
    "hyg": "HYG",
    "lqd": "LQD",
    "tlt": "TLT",
    # commodity proxies, the overview pair (labeled proxies, never a WTI/gold spot price): gold+TLT
    # read together split risk-off into fear vs inflation/dollar regimes, and oil is its own vol
    # driver. ETFs, not /GC//CL futures, deliberately — during RTH they track at beta ~1, and the
    # futures would buy contract-roll machinery and an unproven entitlement for hours this
    # RTH-gated sampler never records. Revisit the carrier only if sampling ever extends overnight.
    "gld": "GLD",
    "uso": "USO",
    # the eleven SPDR sectors (dispersion across these is a read-side derivation)
    "xlb": "XLB",
    "xlc": "XLC",
    "xle": "XLE",
    "xlf": "XLF",
    "xli": "XLI",
    "xlk": "XLK",
    "xlp": "XLP",
    "xlre": "XLRE",
    "xlu": "XLU",
    "xlv": "XLV",
    "xly": "XLY",
}

# One sample per minute: matches the finest module tick in the suite; ~390 rows/reading/session.
# The recorder loop runs faster (15s) — sample() throttles itself against the DB, so a restart
# cannot double-sample and the cadence survives whatever interval the caller runs.
SAMPLE_INTERVAL_SECONDS = 60

# A quote older than this is refused, not recorded — matches the spot trail's own gate
# (service.DEFAULT_SPOT_MAX_AGE_SECONDS) and the trading modules' freshness limit.
MAX_QUOTE_AGE_SECONDS = 120

RTH_OPEN_MINUTE = 9 * 60 + 30
RTH_CLOSE_MINUTE = 16 * 60

_ensured_dbs: set[str] = set()


def ensure_tables(conn: sqlite3.Connection, db_path: Path | str | None = None) -> None:
    """Create the regime tables if absent. Idempotent; skipped once a given file has been prepared
    in this process (the sampler runs every recorder tick for the life of the daemon)."""
    if db_path is not None:
        key = str(Path(db_path).resolve())
        if key in _ensured_dbs:
            return
    conn.execute(
        "CREATE TABLE IF NOT EXISTS market_regime_history ("
        "trade_date TEXT NOT NULL, ts REAL NOT NULL, reading TEXT NOT NULL, symbol TEXT, "
        "value REAL, basis_ts REAL, usable INTEGER NOT NULL, reason TEXT)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mrh_date_reading ON market_regime_history(trade_date, reading)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mrh_ts ON market_regime_history(ts)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS daily_closes ("
        "symbol TEXT NOT NULL, trade_date TEXT NOT NULL, close REAL NOT NULL, "
        "recorded_at REAL NOT NULL, source TEXT NOT NULL, "
        "PRIMARY KEY (symbol, trade_date))"
    )
    if db_path is not None:
        _ensured_dbs.add(str(Path(db_path).resolve()))


def in_rth(now: datetime) -> bool:
    """Regular trading hours on a trading day, ET. Half-days deliberately sample to 16:00 — the
    early close leaves refusal rows past the bell, which the age gate turns into refusals, and a
    shorter honest session is not worth a second calendar."""
    if not _calendar.is_trading_day(now.date()):
        return False
    minute = now.hour * 60 + now.minute
    return RTH_OPEN_MINUTE <= minute < RTH_CLOSE_MINUTE


def _read_trades(cache_path: Path | str, symbols: list[str]) -> dict[str, tuple[float | None, float | None]]:
    """{symbol: (last, updated_at)} straight from ``stream_trades``, UNFILTERED — the sampler needs
    to distinguish a missing quote from a stale one, so the age gate is applied by the caller.
    Cash legs get Trade events since the streamer's 2026-08-17 fix, which is what makes this table
    live for index/ETF legs (curve reads the same two symbols the same way)."""
    cache_path = Path(cache_path)
    out: dict[str, tuple[float | None, float | None]] = {}
    if not cache_path.exists():
        return out
    conn = _provider._connect_ro(cache_path)
    try:
        placeholders = ",".join("?" for _ in symbols)
        for r in conn.execute(
            f"SELECT symbol, last, updated_at FROM stream_trades WHERE symbol IN ({placeholders})",
            symbols,
        ):
            last = None if r["last"] is None else float(r["last"])
            updated = None if r["updated_at"] is None else float(r["updated_at"])
            out[str(r["symbol"]).upper()] = (last, updated)
    finally:
        conn.close()
    return out


def harvest_daily_closes(conn: sqlite3.Connection, cache_path: Path | str, symbols: list[str]) -> int:
    """Copy confirmed session closes for ``symbols`` from ``stream_summary`` into ``daily_closes``.
    ``day_close`` belongs to its own row's session (never ``prev_day_close``, whose date is not
    carried), and the streamer's ``history_days`` backfill means this lands a year of closes on day
    one, not one per session. Returns rows newly written."""
    cache_path = Path(cache_path)
    if not cache_path.exists() or not symbols:
        return 0
    src = _provider._connect_ro(cache_path)
    try:
        placeholders = ",".join("?" for _ in symbols)
        rows = src.execute(
            f"SELECT symbol, trade_date, day_close FROM stream_summary "
            f"WHERE symbol IN ({placeholders}) AND day_close IS NOT NULL",
            symbols,
        ).fetchall()
    finally:
        src.close()
    if not rows:
        return 0
    now = time.time()
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO daily_closes (symbol, trade_date, close, recorded_at, source) "
        "VALUES (?,?,?,?,?)",
        [
            (str(r["symbol"]).upper(), r["trade_date"], float(r["day_close"]), now, "stream_summary")
            for r in rows
        ],
    )
    return conn.total_changes - before


def sample(cfg: dict, *, now: datetime | None = None) -> dict:
    """One sampling pass: write a row per reading (usable or refused) plus the daily-close harvest.
    Self-throttled to SAMPLE_INTERVAL_SECONDS against the DB, RTH-gated, best-effort by contract —
    the caller (the recorder loop) already wraps a try/except so a hiccup never kills the daemon.

    Returns {"status", "written", "usable"}; status is "sampled", "closed" or "throttled"."""
    now = now or datetime.now(_ET)
    if not in_rth(now):
        return {"status": "closed", "written": 0, "usable": 0}
    db_path = Path(cfg["history_db_path"])
    db_path.parent.mkdir(parents=True, exist_ok=True)
    now_ts = now.timestamp()
    today = now.strftime("%Y-%m-%d")
    conn = sqlite3.connect(db_path)
    try:
        ensure_tables(conn, db_path)
        last = conn.execute(
            "SELECT MAX(ts) FROM market_regime_history WHERE trade_date = ?", (today,)
        ).fetchone()
        if last and last[0] is not None and (now_ts - last[0]) < SAMPLE_INTERVAL_SECONDS:
            return {"status": "throttled", "written": 0, "usable": 0}

        quotes = _read_trades(cfg["stream_cache_db"], sorted(set(READINGS.values())))
        rows = []
        usable_count = 0
        for reading, symbol in READINGS.items():
            value, basis_ts = quotes.get(symbol, (None, None))
            if value is None or basis_ts is None:
                rows.append((today, now_ts, reading, symbol, None, None, 0, "no_quote"))
            elif (now_ts - basis_ts) > MAX_QUOTE_AGE_SECONDS:
                rows.append((today, now_ts, reading, symbol, None, basis_ts, 0, "stale_quote"))
            else:
                rows.append((today, now_ts, reading, symbol, value, basis_ts, 1, None))
                usable_count += 1
        conn.executemany(
            "INSERT INTO market_regime_history "
            "(trade_date, ts, reading, symbol, value, basis_ts, usable, reason) "
            "VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )
        close_symbols = sorted(
            set(READINGS.values()) | {str(s).strip().upper() for s in (cfg.get("symbols") or [])}
        )
        harvest_daily_closes(conn, cfg["stream_cache_db"], close_symbols)
        conn.commit()
        return {"status": "sampled", "written": len(rows), "usable": usable_count}
    finally:
        conn.close()


def dropped_readings(conn: sqlite3.Connection, *, today: str) -> set[str]:
    """Readings the ledger recorded on its most recent prior session that the RUNNING code no
    longer declares — the long-row adaptation of flies' ``stale_writer_columns`` guard. On a stale
    checkout the code and its declared list are stale together, but the database keeps what a newer
    checkout recorded, and that difference is the signal. Logged at recorder start, never enforced:
    a stale checkout cannot fix itself, and refusing to record would turn a telemetry gap into an
    outage."""
    row = conn.execute(
        "SELECT MAX(trade_date) FROM market_regime_history WHERE trade_date < ?", (today,)
    ).fetchone()
    if not row or row[0] is None:
        return set()
    recorded = {
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT reading FROM market_regime_history WHERE trade_date = ?", (row[0],)
        )
    }
    return recorded - set(READINGS)
