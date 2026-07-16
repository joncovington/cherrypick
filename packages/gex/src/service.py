"""build_gex — the pure, HTTP-free seam: snapshot -> core aggregation -> chart payload.

Both entrypoints (the module's own ``dashboard --serve`` and the umbrella's ``dashboard --serve``,
which subprocesses ``run.py gex --json``) go through here, so the payload shape is defined in one place.
The per-strike OI+volume GEX math itself is ``cherrypick.core.gex.compute_gex_profile`` — shared with
MEIC's dashboard so the two render identically off the same cache.
"""

from __future__ import annotations

# Bootstrap the cherrypick-core submodule (src/_core) onto sys.path without an install, so a fresh
# `git clone --recursive` works out of the box (mirrors MEIC's credentials.py).
import sys as _sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_CORE = Path(__file__).resolve().parent / "_core"
if _CORE.is_dir() and str(_CORE) not in _sys.path:
    _sys.path.insert(0, str(_CORE))

import sqlite3  # noqa: E402

from cherrypick.core.gex import compute_gex_profile  # noqa: E402

import provider as _provider  # noqa: E402

_ET = ZoneInfo("America/New_York")


def _today() -> str:
    return datetime.now(_ET).strftime("%Y-%m-%d")


def _market_open_close_ts() -> tuple[float, float]:
    """Today's 09:30 / 16:00 ET as unix timestamps, for mapping the spot trail onto a session x-axis."""
    now = datetime.now(_ET)
    open_dt = now.replace(hour=9, minute=30, second=0, microsecond=0)
    close_dt = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return open_dt.timestamp(), close_dt.timestamp()


def _record_and_fetch_spot_history(db_path: Path, symbol: str, spot: float | None) -> list[dict]:
    """Append one spot tick and return today's trail, in this module's OWN sqlite — we never write to
    MEIC's read-only stream cache. Persisted so the chart's spot trail survives page reloads/restarts.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS gex_spot_history ("
            "symbol TEXT NOT NULL, trade_date TEXT NOT NULL, ts REAL NOT NULL, spot REAL NOT NULL)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_gsh_sym_date ON gex_spot_history(symbol, trade_date)")
        today = _today()
        if spot is not None:
            conn.execute(
                "INSERT INTO gex_spot_history (symbol, trade_date, ts, spot) VALUES (?,?,?,?)",
                (symbol, today, time.time(), spot),
            )
            conn.commit()
        rows = conn.execute(
            "SELECT ts, spot FROM gex_spot_history WHERE symbol = ? AND trade_date = ? ORDER BY ts",
            (symbol, today),
        ).fetchall()
        return [{"ts": r["ts"], "spot": r["spot"]} for r in rows]
    finally:
        conn.close()


def build_gex(cfg: dict, symbol: str | None = None) -> dict:
    """Read a snapshot from the configured stream cache, aggregate it, and return the chart payload.

    Payload shape matches MEIC's dashboard ``_build_gex_data`` so the two share the same client JS:
    ``{ok, symbol, expiration, underlying_price, source, series, spot_history, market_open_ts,
    market_close_ts, totals}`` — or ``{ok: False, error}`` when the cache isn't populated yet.
    """
    from config import default_symbol  # local import; config bootstraps nothing

    symbol = (symbol or default_symbol(cfg)).strip().upper()
    snap = _provider.snapshot_from_stream_cache(cfg["stream_cache_db"], symbol)

    if snap.source == "missing":
        return {"ok": False, "symbol": symbol,
                "error": f"stream cache not found at {cfg['stream_cache_db']} — is the MEIC streamer running?"}
    if snap.expiration is None:
        return {"ok": False, "symbol": symbol,
                "error": f"no cached chain for {symbol} yet — is the MEIC streamer subscribed to it?"}

    profile = compute_gex_profile(
        snap.chain_entries, snap.greeks, snap.oi, snap.volume,
        snap.spot or 0, strike_scale=snap.strike_scale,
    )
    if not profile.get("ok"):
        return {"ok": False, "symbol": symbol, "error": profile.get("error", "insufficient GEX data")}

    spot_history = _record_and_fetch_spot_history(Path(cfg["history_db_path"]), symbol, snap.spot)
    market_open_ts, market_close_ts = _market_open_close_ts()

    return {
        "ok": True,
        "symbol": symbol,
        "expiration": snap.expiration,
        "underlying_price": snap.spot,
        "source": snap.source,
        "series": profile["series"],
        "spot_history": spot_history,
        "market_open_ts": market_open_ts,
        "market_close_ts": market_close_ts,
        "totals": profile["totals"],
    }
