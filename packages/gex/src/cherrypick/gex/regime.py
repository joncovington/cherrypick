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

import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from cherrypick.core import calendar as _calendar
from cherrypick.core import home as _home
from cherrypick.core import structures as _structures
from cherrypick.core.clock import ET as _ET

from cherrypick.gex import provider as _provider

# reading name -> source symbol. The reading name is the stable identity (it survives a source
# symbol change); the symbol is provenance, recorded on every row. Raw quote readings only — see
# the module docstring for why derived values are excluded on purpose.
READINGS: dict[str, str] = {
    # vol complex. SKEW and VIX9D admitted 2026-08-24 after the entitlement probe printed both
    # through the ordinary legs path (143.9 and 14.53) — see docs/regime-recorder-plan.md. SKEW
    # prices the tail independently of the ATM level; VIX9D/VIX reads event-week pricing
    # (FOMC/CPI) more sharply than VIX/VIX3M does.
    "vix": "VIX",
    "vix3m": "VIX3M",
    "vix1d": "VIX1D",
    "vix9d": "VIX9D",
    "vvix": "VVIX",
    "skew": "SKEW",
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

# Futures readings, resolved rather than assembled (docs/regime-recorder-plan.md).
#
# The reading NAME is stable across a roll and the row's `symbol` carries the contract that was
# actually sampled — so a roll shows up as the symbol changing under `vx1`, and no row is ever a
# blended constant-maturity value. That is the whole reason the long-row shape exists.
#
# The map comes from `scripts/refresh_futures_contracts.py`, outside every package: contract
# resolution needs the broker's instruments endpoint, and this recorder is credential-free and
# network-free. Never assemble a futures symbol here — the 2026-08-24 probe guessed `:XCFE`, saw
# nothing, and would have concluded the exchange was not entitled; the MIC is `XCBF` and only the
# endpoint knows that.
FUTURES_READINGS = {"vx1": ("VX", 0), "vx2": ("VX", 1), "zn1": ("ZN", 0)}

# A map older than this is refused outright rather than sampled: futures roll, and a stale map names
# a contract that has expired or gone illiquid. Dropping the readings leaves a legible gap; sampling
# a rolled-off contract would leave a plausible-looking series that is quietly wrong.
FUTURES_MAP_MAX_AGE_DAYS = 5

_ensured_dbs: set[str] = set()


def futures_symbols(now: datetime | None = None) -> dict[str, str]:
    """`{reading: streamer_symbol}` for the futures readings, or `{}` when the map is missing,
    unreadable or stale. Empty is the safe answer everywhere: the recorder simply records no
    futures rows, and the declaration guard stops asking the producer for them."""
    try:
        raw = json.loads((_home.state_dir() / "futures_contracts.json").read_text(encoding="utf-8"))
        refreshed = datetime.fromisoformat(str(raw["refreshed_at"]))
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    now = now or datetime.now(_ET)
    if (now - refreshed).days > FUTURES_MAP_MAX_AGE_DAYS:
        return {}
    contracts = raw.get("contracts") or {}
    out: dict[str, str] = {}
    for reading, (product, index) in FUTURES_READINGS.items():
        rows = contracts.get(product) or []
        if index < len(rows):
            symbol = rows[index].get("streamer_symbol")
            if symbol:
                out[reading] = str(symbol)
    return out


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

        # Static readings plus whatever the resolved contract map currently names. A missing or
        # stale map simply contributes nothing — see futures_symbols.
        sampled = dict(READINGS)
        sampled.update(futures_symbols(now))
        quotes = _read_trades(cfg["stream_cache_db"], sorted(set(sampled.values())))
        rows = []
        usable_count = 0
        for reading, symbol in sampled.items():
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
        # Tier 2: per-symbol chain math for the underlyings this module offers. Best-effort per
        # symbol — a chain the cache cannot answer for contributes no rows rather than failing the
        # sample, and the quote readings above are unaffected either way.
        chain_rows = 0
        for sym in [str(s).strip().upper() for s in (cfg.get("symbols") or [])]:
            try:
                snap = _provider.snapshot_from_stream_cache(cfg["stream_cache_db"], sym)
                if snap.source == "missing" or snap.expiration is None:
                    continue
                for reading, value in chain_readings(snap).items():
                    conn.execute(
                        "INSERT INTO market_regime_history "
                        "(trade_date, ts, reading, symbol, value, basis_ts, usable, reason) "
                        "VALUES (?,?,?,?,?,?,1,NULL)",
                        (today, now_ts, reading, sym, value, now_ts),
                    )
                    chain_rows += 1
            except Exception:  # noqa: BLE001 — chain math must never cost the quote sample
                continue

        close_symbols = sorted(
            set(READINGS.values()) | {str(s).strip().upper() for s in (cfg.get("symbols") or [])}
        )
        harvest_daily_closes(conn, cfg["stream_cache_db"], close_symbols)
        conn.commit()
        return {
            "status": "sampled",
            "written": len(rows) + chain_rows,
            "usable": usable_count + chain_rows,
            "chain_rows": chain_rows,
        }
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


# --------------------------------------------------------------------------- Tier 2: chain math
#
# Per-symbol readings computed from the option chain the producer already caches (greeks and OI
# included), rather than from a quote. They share the same row shape as everything else — one row
# per (reading, symbol) — so `atm_iv` for SPX and for TQQQ are two rows under one reading name, and
# `cherrypick.core.regime` groups them per symbol on the read side.
#
# Same store-the-measure rule: each is a raw quantity. IV RANK is deliberately absent — it is a
# percentile of `atm_iv` against its own history, which is a read-side derivation over exactly this
# series and would be frozen the moment it were stored.
CHAIN_READINGS = ("atm_iv", "expected_move", "risk_reversal_25d", "put_call_oi_ratio", "gamma_concentration")

# Concentration is measured over the top strikes NEAR SPOT, never the whole chain: flies measured
# its whole-chain version degenerate 60/60 because one strike's share of a 109-strike surface is
# always small. Pinning is a property of a cluster near the money.
_CONCENTRATION_WINDOW = 10
_CONCENTRATION_TOP = 3


def _nearest_strike_ivs(entries: list[dict], greeks: dict, spot: float) -> tuple[float | None, float | None]:
    """(call IV, put IV) at the listed strike nearest spot, or Nones."""
    strikes = {float(e["strike_price"]) for e in entries if e.get("strike_price") is not None}
    if not strikes:
        return (None, None)
    k = min(strikes, key=lambda s: abs(s - spot))
    out: dict[str, float] = {}
    for e in entries:
        if float(e.get("strike_price") or -1) != k:
            continue
        g = greeks.get(e.get("streamer_symbol")) or {}
        iv = g.get("iv")
        if iv is None:
            continue
        side = str(e.get("option_type") or "").upper()[:1]
        if side in ("C", "P"):
            out[side] = float(iv)
    return (out.get("C"), out.get("P"))


def chain_readings(snapshot) -> dict[str, float]:
    """The Tier 2 measures for one symbol's cached chain. Missing inputs simply omit a reading —
    a partial chain yields fewer measures, never a guessed one.

    **The horizon is the NEAREST cached expiration, whatever its DTE**, because that is the chain
    the producer keeps a live window on. For a 0DTE-heavy underlying like SPX that means these
    describe today's surface, not a thirty-day one: measured 2026-08-24 the nearest expiration was
    the same session, `atm_iv` read 23.9 and both `risk_reversal_25d` and `expected_move` were
    correctly absent, because an expiring chain has no strike near 25 delta. Read the series with
    that in mind, and re-cut by DTE later if the horizon ever needs to be held constant — the raw
    measures are recorded, so that stays possible."""
    spot = snapshot.spot
    entries = snapshot.chain_entries or []
    greeks = snapshot.greeks or {}
    oi = snapshot.oi or {}
    if not spot or not entries:
        return {}
    out: dict[str, float] = {}

    call_iv, put_iv = _nearest_strike_ivs(entries, greeks, spot)
    ivs = [v for v in (call_iv, put_iv) if v is not None]
    if ivs:
        out["atm_iv"] = round(sum(ivs) / len(ivs), 6)

    # Expected move through the suite's one straddle formula, so this cannot disagree with the
    # modules that trade on it.
    prices: dict[str, float] = {}
    strikes = {float(e["strike_price"]) for e in entries if e.get("strike_price") is not None}
    if strikes:
        k = min(strikes, key=lambda s: abs(s - spot))
        for e in entries:
            if float(e.get("strike_price") or -1) != k:
                continue
            g = greeks.get(e.get("streamer_symbol")) or {}
            price = g.get("price")
            side = str(e.get("option_type") or "").upper()[:1]
            if price is not None and side in ("C", "P"):
                prices[side] = float(price)
        if "C" in prices and "P" in prices:
            out["expected_move"] = round(_structures.expected_move(prices["C"], prices["P"]), 4)

    # 25-delta risk reversal: put IV minus call IV at the wings. Positive means the market pays more
    # for downside protection than for upside — the direction the chain itself is pricing.
    def _at_delta(target: float, side: str) -> float | None:
        best, best_gap = None, None
        for e in entries:
            if str(e.get("option_type") or "").upper()[:1] != side:
                continue
            g = greeks.get(e.get("streamer_symbol")) or {}
            d, iv = g.get("delta"), g.get("iv")
            if d is None or iv is None:
                continue
            gap = abs(abs(float(d)) - target)
            if best_gap is None or gap < best_gap:
                best, best_gap = float(iv), gap
        return best if (best_gap is not None and best_gap <= 0.10) else None

    rr_put, rr_call = _at_delta(0.25, "P"), _at_delta(0.25, "C")
    if rr_put is not None and rr_call is not None:
        out["risk_reversal_25d"] = round(rr_put - rr_call, 6)

    puts = sum(v for s, v in oi.items() if _is_side(entries, s, "P"))
    calls = sum(v for s, v in oi.items() if _is_side(entries, s, "C"))
    if calls > 0:
        out["put_call_oi_ratio"] = round(puts / calls, 4)

    by_strike: dict[float, int] = {}
    for e in entries:
        k = e.get("strike_price")
        n = oi.get(e.get("streamer_symbol"))
        if k is None or not n:
            continue
        by_strike[float(k)] = by_strike.get(float(k), 0) + int(n)
    near = sorted(by_strike.items(), key=lambda kv: abs(kv[0] - spot))[:_CONCENTRATION_WINDOW]
    total = sum(n for _, n in near)
    if total > 0:
        top = sorted((n for _, n in near), reverse=True)[:_CONCENTRATION_TOP]
        out["gamma_concentration"] = round(sum(top) / total, 4)
    return out


def _is_side(entries: list[dict], streamer_symbol: str, side: str) -> bool:
    for e in entries:
        if e.get("streamer_symbol") == streamer_symbol:
            return str(e.get("option_type") or "").upper().startswith(side)
    return False
