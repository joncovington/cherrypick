"""cherrypick.core.streamcache — the shared stream-cache schema + SQLite helpers.

The persistent option-chain cache a streamer daemon writes and readers (GEX, dashboards, a trading
loop) read: latest Quote / Greeks / Trade(volume) / Summary(open-interest) per option symbol, the
option-chain structure, a small daemon-status row, and per-(underlying, day) session OHLC rows
(stream_summary) — the exchange-official day open/high/low/close + prior close off the underlying's
Summary event, which accumulate into a daily series (intraday-range gates read today's row; a
true-range ATR reads the last N completed days). Extracted from MEIC's streamer so any consumer —
MEIC's own daemon, the standalone GEX module — writes and reads one identical schema instead of each
carrying a private copy (plan Phase A of the streamer extraction).

Pure SQLite + stdlib; no broker, no network, no tastytrade import. A streaming *engine*
(`cherrypick.core.streamer`) fills this cache; a provider (`cherrypick-gex`) reads it read-only.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

# The schema every consumer shares. orb_ranges/stream_rest_cache are used only by MEIC's daemon today
# but are kept here so MEIC can adopt this DDL verbatim when it migrates onto the core engine.
DDL = """
CREATE TABLE IF NOT EXISTS stream_chain (
    streamer_symbol   TEXT PRIMARY KEY,
    expiration        TEXT NOT NULL,
    underlying_symbol TEXT,
    data_json         TEXT NOT NULL,
    updated_at        REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chain_expiration ON stream_chain(expiration);
CREATE TABLE IF NOT EXISTS stream_quotes (
    symbol      TEXT PRIMARY KEY,
    bid         REAL,
    ask         REAL,
    mid         REAL,
    bid_size    REAL,
    ask_size    REAL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS stream_greeks (
    symbol      TEXT PRIMARY KEY,
    delta       REAL,
    gamma       REAL,
    theta       REAL,
    vega        REAL,
    rho         REAL,
    iv          REAL,
    price       REAL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS stream_trades (
    symbol      TEXT PRIMARY KEY,
    last        REAL,
    change      REAL,
    volume      REAL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS stream_oi (
    symbol        TEXT PRIMARY KEY,
    open_interest INTEGER,
    updated_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS stream_rest_cache (
    key         TEXT PRIMARY KEY,
    data_json   TEXT NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS stream_status (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    pid                 INTEGER,
    connected_since     TEXT,
    last_event_at       TEXT,
    subscribed_symbols  INTEGER DEFAULT 0,
    reconnect_count     INTEGER DEFAULT 0
);
-- Per-symbol chain-fetch health. A daemon-wide staleness check (stream_status/the freshest-of-any-
-- event age) can stay healthy while ONE symbol's 0DTE chain fetch keeps failing and its window sits
-- permanently disabled -- other symbols' quotes mask it. This is that symbol's own signal: NULL
-- chain_fetch_error means its chain is currently loaded fine.
CREATE TABLE IF NOT EXISTS stream_symbol_health (
    symbol            TEXT PRIMARY KEY,
    chain_loaded_at   TEXT,
    chain_fetch_error TEXT,
    updated_at        REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS orb_ranges (
    symbol      TEXT NOT NULL,
    trade_date  TEXT NOT NULL,
    orb_high    REAL,
    orb_low     REAL,
    captured_at REAL,
    PRIMARY KEY (symbol, trade_date)
);
CREATE TABLE IF NOT EXISTS stream_summary (
    symbol          TEXT NOT NULL,
    trade_date      TEXT NOT NULL,
    day_open        REAL,
    day_high        REAL,
    day_low         REAL,
    day_close       REAL,
    prev_day_close  REAL,
    updated_at      REAL NOT NULL,
    PRIMARY KEY (symbol, trade_date)
);
"""


def to_float(value) -> float | None:
    """NaN-safe float coercion for event fields (DXLink sends NaN for missing greeks/prices)."""
    if value is None:
        return None
    try:
        v = float(value)
        return None if v != v else v  # NaN guard
    except (TypeError, ValueError):
        return None


# How long a writer waits for a busy database before giving up. SQLite's default is ZERO: a
# contended write raises `database is locked` on the spot rather than retrying, which is almost
# never what a daemon wants and was not what this one wanted on 2026-08-17. A burst of history
# backfill held the write lock long enough that the quote/trade/summary writers and the status
# flusher all began failing instantly; the status write happens inside the stream's task group, so
# its exception tore down the DXLink connection, and the producer spent the session reconnecting
# every 60s while every module's quotes went stale.
#
# Five seconds is chosen against what the cache actually does: writes here are single-row upserts
# and short batches, so a wait this long means something genuinely unusual is in progress (a large
# backfill, a checkpoint) and waiting for it is strictly better than dropping the tick. A caller
# that would rather fail fast than block should not be sharing a cache with a daemon.
BUSY_TIMEOUT_MS = 5000

# How large the WAL may stay after a checkpoint resets it. In WAL mode SQLite's automatic checkpoint
# copies pages into the database but can only RESET the file when no reader still needs the frames it
# holds — and this cache always has a reader (the console polls it every few seconds, every module
# provider on its own tick). So the automatic path quietly degraded to copy-but-never-truncate and the
# WAL reached 98MB against a 49MB database, which every reader then had to walk.
#
# The limit alone fixes nothing: it applies when a checkpoint resets the journal, so it needs
# `checkpoint()` below to actually get one through. 16MB leaves room for the history-backfill burst to
# run without thrashing the file size, while bounding the steady state well under the database.
JOURNAL_SIZE_LIMIT_BYTES = 16 * 1024 * 1024

# What a checkpoint attempt is allowed to wait for readers before giving up. Deliberately far below
# BUSY_TIMEOUT_MS: the producer runs one event loop over one connection, so a checkpoint that blocks
# is a checkpoint that stalls the DXLink listeners with it. A contended attempt should cost a
# quarter-second and be retried on the next cadence, not hold the loop for five seconds.
CHECKPOINT_BUSY_TIMEOUT_MS = 250


def _wal_size(conn: sqlite3.Connection) -> int:
    """Bytes currently held by this connection's write-ahead log (0 if absent/unreadable)."""
    try:
        for _seq, name, filename in conn.execute("PRAGMA database_list"):
            if name == "main" and filename:
                wal = Path(filename + "-wal")
                return wal.stat().st_size if wal.exists() else 0
    except Exception:
        pass
    return 0


def checkpoint(conn: sqlite3.Connection) -> tuple[bool, int]:
    """Best-effort `wal_checkpoint(TRUNCATE)`. Returns (reset, reclaimed_bytes).

    Never raises and never blocks for long — see CHECKPOINT_BUSY_TIMEOUT_MS. A busy result is the
    ordinary case while readers are active and means "try again next time", not a failure: one
    success in a quiet stretch is enough to put the WAL back on the floor, which is why the caller
    can run this on a slow cadence and ignore the result.

    Reclaimed bytes are measured off the file rather than taken from the pragma's own counters: a
    successful TRUNCATE reports zero pages checkpointed precisely BECAUSE it reset the log, so those
    counters cannot tell a no-op apart from the case worth logging."""
    before = _wal_size(conn)
    try:
        conn.execute(f"PRAGMA busy_timeout={CHECKPOINT_BUSY_TIMEOUT_MS}")
        try:
            row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        finally:
            conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    except Exception:
        return (False, 0)
    if not row:
        return (False, 0)
    reset = row[0] == 0
    return (reset, max(0, before - _wal_size(conn)) if reset else 0)


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open (creating + migrating) the write-side cache. WAL + NORMAL for a daemon that commits often
    while readers open the same file read-only. `check_same_thread=False`: MEIC's daemon touches the
    connection from its DXLink loop and a status flusher — which is exactly why `busy_timeout` is
    set here rather than left at SQLite's default of 0 (see BUSY_TIMEOUT_MS)."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute(f"PRAGMA journal_size_limit={JOURNAL_SIZE_LIMIT_BYTES}")
    for stmt in DDL.split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)
    # Additive migration for caches created before underlying_symbol existed (XSP/SPX share 0DTE dates,
    # so an expiration-only filter would blend chains — the column lets readers disambiguate).
    existing = {row[1] for row in conn.execute("PRAGMA table_info(stream_chain)")}
    if "underlying_symbol" not in existing:
        conn.execute("ALTER TABLE stream_chain ADD COLUMN underlying_symbol TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chain_underlying ON stream_chain(underlying_symbol, expiration)"
    )
    conn.commit()
    return conn


def upsert_status(conn: sqlite3.Connection, **kwargs) -> None:
    """Upsert the single daemon-status row (id=1) with whatever fields are supplied."""
    fields = dict(kwargs)
    cols = ", ".join(fields)
    vals = ", ".join("?" for _ in fields)
    updates = ", ".join(f"{k} = excluded.{k}" for k in fields if k != "id")
    conn.execute(
        f"INSERT INTO stream_status (id, {cols}) VALUES (1, {vals}) ON CONFLICT(id) DO UPDATE SET {updates}",
        list(fields.values()),
    )
    conn.commit()


def upsert_symbol_health(conn: sqlite3.Connection, symbol: str, **kwargs) -> None:
    """Upsert one symbol's chain-fetch health row with whatever fields are supplied — `updated_at`
    is always refreshed, but an omitted field (e.g. a failure call that doesn't pass
    `chain_loaded_at`) is left untouched rather than blanked, same convention as `upsert_status`."""
    fields = {"symbol": symbol, "updated_at": time.time(), **kwargs}
    cols = ", ".join(fields)
    vals = ", ".join("?" for _ in fields)
    updates = ", ".join(f"{k} = excluded.{k}" for k in fields if k != "symbol")
    conn.execute(
        f"INSERT INTO stream_symbol_health ({cols}) VALUES ({vals}) "
        f"ON CONFLICT(symbol) DO UPDATE SET {updates}",
        list(fields.values()),
    )
    conn.commit()


def write_chain(conn: sqlite3.Connection, option_map: dict) -> int:
    """Persist an option-chain structure ({streamer_symbol: option}). Tags each row with its
    underlying_symbol so lookups can filter by underlying. Returns rows written."""
    now = time.time()
    rows = []
    for sym, o in option_map.items():
        dump = getattr(o, "model_dump", None)
        data = dump(mode="json") if callable(dump) else {"streamer_symbol": sym}
        rows.append(
            (sym, str(data.get("expiration_date", "")), data.get("underlying_symbol"), json.dumps(data), now)
        )
    conn.executemany(
        "INSERT INTO stream_chain (streamer_symbol, expiration, underlying_symbol, data_json, updated_at) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(streamer_symbol) DO UPDATE SET "
        "expiration=excluded.expiration, underlying_symbol=excluded.underlying_symbol, "
        "data_json=excluded.data_json, updated_at=excluded.updated_at",
        rows,
    )
    conn.commit()
    return len(rows)


# --------------------------------------------------------------------------- daily-history backfill
#
# `stream_summary` accumulates one OHLC row per (symbol, trade_date) from the live Summary event —
# a series that starts EMPTY the day a symbol is first requested, which starves any consumer whose
# math needs history (a 20-day ATR waits ~a month of sessions). DXLink daily Candle events carry the
# same series back as far as asked, so the engine backfills a requested number of days once per
# symbol. Two rules keep the two sources honest side by side:
#
# - **Backfill only ever fills ABSENT dates.** The live Summary event is the exchange-official
#   session record and candles come off a different consolidation feed; the two can disagree
#   slightly, so a row the live feed wrote (or a backfill already wrote) is never overwritten —
#   INSERT ... DO NOTHING, enforced here rather than trusted to callers.
# - **Today's date is never backfilled.** The current session's candle is partial; today's row
#   belongs to the live Summary listener alone.


def summary_backfill_rows(bars: list[dict], *, today: str) -> list[dict]:
    """Normalise raw daily bars ({date, open, high, low, close}) into insertable rows: sorted,
    deduped (last wins), dates before `today` only, `prev_day_close` chained from the prior bar's
    close. Pure — the transform the backfill test pins."""
    by_date: dict[str, dict] = {}
    for bar in bars:
        day = str(bar.get("date") or "")
        if not day or day >= str(today):
            continue
        by_date[day] = bar
    out: list[dict] = []
    prev_close = None
    for day in sorted(by_date):
        bar = by_date[day]
        out.append(
            {
                "trade_date": day,
                "day_open": to_float(bar.get("open")),
                "day_high": to_float(bar.get("high")),
                "day_low": to_float(bar.get("low")),
                "day_close": to_float(bar.get("close")),
                "prev_day_close": prev_close,
            }
        )
        prev_close = to_float(bar.get("close"))
    return out


def backfill_summary(conn: sqlite3.Connection, symbol: str, bars: list[dict], *, today: str) -> int:
    """Insert `bars` (see `summary_backfill_rows`) for dates `stream_summary` does not already hold.
    Existing rows — live-written or previously backfilled — are never touched. Returns rows added."""
    rows = summary_backfill_rows(bars, today=today)
    now = time.time()
    added = 0
    for r in rows:
        cur = conn.execute(
            "INSERT INTO stream_summary (symbol, trade_date, day_open, day_high, day_low, "
            "day_close, prev_day_close, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(symbol, trade_date) DO NOTHING",
            (
                symbol,
                r["trade_date"],
                r["day_open"],
                r["day_high"],
                r["day_low"],
                r["day_close"],
                r["prev_day_close"],
                now,
            ),
        )
        added += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    conn.commit()
    return added


def completed_summary_days(conn: sqlite3.Connection, symbol: str, *, today: str) -> int:
    """How many COMPLETED daily rows (close present, date before `today`) the cache holds for one
    symbol — the backfill's deficit check."""
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM stream_summary WHERE symbol = ? AND trade_date < ? "
            "AND day_close IS NOT NULL",
            (symbol, today),
        ).fetchone()
        return int(row[0] or 0)
    except sqlite3.Error:
        return 0


def current_underlying_price(conn: sqlite3.Connection, underlying: str) -> float | None:
    """Latest last-trade price for an underlying from the cache (used to centre the ATM window)."""
    try:
        row = conn.execute("SELECT last FROM stream_trades WHERE symbol = ?", (underlying,)).fetchone()
        return float(row["last"]) if row and row["last"] is not None else None
    except sqlite3.Error:
        return None


def atm_window_syms(option_map: dict, center: float, strike_count: int) -> list[str]:
    """Streamer symbols within `strike_count` strikes of `center` on each side."""
    strikes = sorted({float(o.strike_price) for o in option_map.values()})
    if not strikes:
        return []
    nearest = min(range(len(strikes)), key=lambda i: abs(strikes[i] - center))
    lo = max(0, nearest - strike_count)
    hi = min(len(strikes), nearest + strike_count + 1)
    keep = set(strikes[lo:hi])
    return [sym for sym, o in option_map.items() if float(o.strike_price) in keep]


# --------------------------------------------------------------------------- symbology
# The cache is keyed by *streamer* symbol (".SPY260918P750"), but positions come back from the broker in
# OCC 2010 form ("SPY   260918P00750000"). A reader that wants a mark for a position it already holds
# therefore has to convert, and the conversion has exactly one correct answer — so it lives here rather
# than being re-derived per consumer.
#
# This mirrors `tastytrade.instruments.Option.occ_to_streamer_symbol` deliberately, and a test pins it
# to that method, because the streamer writes the keys the SDK produced: a converter disagreeing by one
# trailing zero would miss every fractional strike and read as "no quote available" rather than as a
# bug. Kept pure stdlib (no SDK import) so stream-cache readers stay credential-free and network-free.
_OCC_RE = re.compile(r"(\d{6})([CP])(\d{5})(\d{3})")


def occ_to_streamer_symbol(occ: str) -> str:
    """Convert an OCC 2010 option symbol to the DXLink streamer symbol used as a cache key.

    Returns "" when `occ` is not an option symbol, which is the useful answer for a mixed position
    list: an equity holding ("SCHD") has no OCC form and is already its own streamer symbol, so callers
    read "" as "not an option" rather than as a failure.
    """
    # The SDK indexes `occ[:6].split()[0]` unguarded, which raises on an empty or all-space root. A
    # converter on a read path must fail closed instead: callers branch on "" already, and a traceback
    # here would take down a whole report over one malformed symbol.
    root = (occ[:6].split() or [""])[0]
    match = _OCC_RE.match(occ[6:])
    if not root or match is None:
        return ""
    exp, right, dollars, thousandths = match.groups()
    out = f".{root}{exp}{right}{int(dollars)}"
    if int(thousandths):
        # 500 -> "0.5" -> ".5". The SDK slices the leading zero off rather than formatting, and the
        # trailing-zero trim that falls out of float repr is part of the key the streamer wrote.
        out += str(int(thousandths) / 1000.0)[1:]
    return out


# How old a cached quote may be before a caller pricing a position should refetch rather than trust it.
# Matches MEIC's own live-path bound (`tt._CACHE_MAX_AGE`) so the two cannot disagree about what
# "current" means: during market hours the producer refreshes a subscribed symbol far more often than
# this, so anything older says the symbol stopped updating, not that the market went quiet.
QUOTE_MAX_AGE_SECONDS = 10.0


def quote_mids(
    conn: sqlite3.Connection,
    symbols: list[str],
    *,
    max_age_seconds: float | None = QUOTE_MAX_AGE_SECONDS,
) -> dict[str, dict]:
    """Cached bid/ask/mid per streamer symbol, for the subset the cache holds *currently*.

    Only rows with a usable `mid` are returned: a row present but mid-less is not a mark, and a caller
    that treated it as one would report a position as worthless. Rows older than `max_age_seconds` are
    withheld for the same reason — a mark is a claim about now, and a caller that cannot tell a
    ten-second-old quote from an hour-old one will happily print a stale number as a live one. Pass
    `max_age_seconds=None` to accept any age (worth it only for symbols too illiquid to requote, and
    then the caller owns saying so).

    Absent symbols are simply missing from the result, so `set(symbols) - result.keys()` is the list
    still needing a live fetch.
    """
    out: dict[str, dict] = {}
    now = time.time()
    for sym in symbols:
        try:
            row = conn.execute(
                "SELECT bid, ask, mid, updated_at FROM stream_quotes WHERE symbol = ?", (sym,)
            ).fetchone()
        except sqlite3.Error:
            continue
        if row is None or row["mid"] is None:
            continue
        age = now - (to_float(row["updated_at"]) or 0.0)
        if max_age_seconds is not None and age > max_age_seconds:
            continue
        out[sym] = {
            "bid": to_float(row["bid"]),
            "ask": to_float(row["ask"]),
            "mid": to_float(row["mid"]),
            "age_seconds": round(age, 1),
            "source": "stream_cache",
        }
    return out
