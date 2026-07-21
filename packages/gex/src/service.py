"""build_gex — the pure, HTTP-free seam: snapshot -> core aggregation -> chart payload.

Both entrypoints (the module's own ``dashboard --serve`` and the umbrella's ``dashboard --serve``,
which subprocesses ``run.py gex --json``) go through here, so the payload shape is defined in one place.
The per-strike OI+volume GEX math itself is ``cherrypick.core.gex.compute_gex_profile`` — shared with
MEIC's dashboard so the two render identically off the same cache.
"""

from __future__ import annotations

# Bootstrap the cherrypick-core submodule (src/_core) onto sys.path without an install, so a fresh
# `git clone --recursive` works out of the box (mirrors MEIC's credentials.py).
import os
import signal
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


def _ensure_history_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS gex_spot_history ("
        "symbol TEXT NOT NULL, trade_date TEXT NOT NULL, ts REAL NOT NULL, spot REAL NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gsh_sym_date ON gex_spot_history(symbol, trade_date)")


def _fetch_spot_history(db_path: Path, symbol: str) -> list[dict]:
    """Today's recorded spot trail for `symbol` (read-only) from this module's OWN sqlite — we never
    write to MEIC's read-only stream cache."""
    db_path = Path(db_path)
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT ts, spot FROM gex_spot_history WHERE symbol = ? AND trade_date = ? ORDER BY ts",
            (symbol.strip().upper(), _today()),
        ).fetchall()
        return [{"ts": r["ts"], "spot": r["spot"]} for r in rows]
    finally:
        conn.close()


def record_spots(cfg: dict, symbols: list[str] | None = None) -> int:
    """Record the current spot for EVERY offered symbol (default `cfg['symbols']`) into this module's
    history DB — not just the one on screen — so each symbol's trail stays continuous and there is no
    gap when the viewer switches symbols. Best-effort: spots come from the read-only stream cache, and a
    symbol with no cached spot is skipped. The dashboard server calls this on a fixed cadence. Persisted
    so the trail survives page reloads/restarts. Returns how many symbols were recorded."""
    syms = [str(s).strip().upper() for s in (symbols if symbols is not None else cfg.get("symbols") or [])]
    if not syms:
        return 0
    db_path = Path(cfg["history_db_path"])
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        _ensure_history_table(conn)
        today = _today()
        now = time.time()
        n = 0
        for sym in syms:
            spot = _provider.read_spot(cfg["stream_cache_db"], sym)
            if spot is not None:
                conn.execute(
                    "INSERT INTO gex_spot_history (symbol, trade_date, ts, spot) VALUES (?,?,?,?)",
                    (sym, today, now, spot),
                )
                n += 1
        conn.commit()
        return n
    finally:
        conn.close()


def _recorder_pid_file(cfg: dict) -> Path:
    """PID file for the recorder daemon, alongside its history DB (the module's data home)."""
    return Path(cfg["history_db_path"]).parent / "recorder.pid"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes

            SYNCHRONIZE = 0x00100000
            h = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
            if h:
                ctypes.windll.kernel32.CloseHandle(h)
                return True
            return False
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError, ValueError):
        return False


def _running_recorder_pid(cfg: dict) -> int | None:
    """The live recorder daemon's pid, or None. Clears a stale pid file (process gone)."""
    pf = _recorder_pid_file(cfg)
    if not pf.exists():
        return None
    try:
        pid = int(pf.read_text().strip())
    except (ValueError, OSError):
        pf.unlink(missing_ok=True)
        return None
    if _pid_alive(pid):
        return pid
    pf.unlink(missing_ok=True)
    return None


def recorder_status(cfg: dict) -> dict:
    """{"ok", "running", "pid"} — the daemon-liveness contract the orchestrator's status_argv reads."""
    pid = _running_recorder_pid(cfg)
    return {"ok": True, "running": pid is not None, "pid": pid}


def stop_recorder(cfg: dict) -> dict:
    """SIGTERM a running recorder daemon (idempotent — 'not running' is a fine result)."""
    pid = _running_recorder_pid(cfg)
    if pid is None:
        return {"ok": True, "running": False, "detail": "not running"}
    try:
        os.kill(pid, signal.SIGTERM)
        _recorder_pid_file(cfg).unlink(missing_ok=True)
        return {"ok": True, "signal": "SIGTERM", "pid": pid}
    except OSError as exc:
        return {"ok": False, "pid": pid, "error": str(exc)}


def run_recorder(cfg: dict, *, interval: int | None = None, once: bool = False) -> int:
    """Always-on spot-trail recorder: sample every offered symbol's spot into the history DB on a fixed
    cadence, independent of the dashboard. Run it alongside the streamer (its data source) so each
    symbol's trail builds all session and persists across dashboard restarts — the dashboard then only
    reads the trail. ``once`` samples a single tick and returns; otherwise it loops until Ctrl-C.

    Single-instance: refuses to start if a recorder is already running (so the orchestrator's install +
    watchdog keep-alive can call start freely without spawning duplicates)."""
    import logging

    syms = [str(s).strip().upper() for s in (cfg.get("symbols") or [])]
    interval = int(interval or (cfg.get("serve", {}) or {}).get("refresh_seconds", 15))

    if once:
        n = record_spots(cfg)
        print(f"recorded spot for {n}/{len(syms)} symbols")
        return 0

    existing = _running_recorder_pid(cfg)
    if existing is not None:
        print(f"recorder already running (pid {existing})")
        return 0

    import config as _config  # local — config bootstraps nothing

    pid_file = _recorder_pid_file(cfg)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))

    def _on_term(*_):  # POSIX: SIGTERM -> graceful stop (Windows kills forcefully; pid cleaned lazily)
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _on_term)

    log_dir = _config.logs_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_dir / "recorder.log", encoding="utf-8"), logging.StreamHandler()],
    )
    log = logging.getLogger("cherrypick-gex.recorder")
    log.info("spot recorder starting: %s every %ss -> %s", syms, interval, cfg["history_db_path"])
    print(f"cherrypick-gex spot recorder: {syms} every {interval}s  (Ctrl-C to stop)")

    heartbeat_every = max(1, 300 // interval)  # a ~5-minute INFO heartbeat; otherwise stay quiet
    ticks = 0
    try:
        while True:
            try:
                n = record_spots(cfg)
                ticks += 1
                if ticks % heartbeat_every == 0:
                    log.info("recorded %d/%d symbols (tick %d)", n, len(syms), ticks)
            except Exception as exc:  # a data hiccup must never kill the recorder
                log.warning("record failed: %s", exc)
            time.sleep(interval)
    except KeyboardInterrupt:
        log.info("spot recorder stopped (%d ticks)", ticks)
    finally:
        pid_file.unlink(missing_ok=True)
    return 0


def _flip_nearest_spot(series: list[dict], key: str, spot: float) -> float | None:
    """Interpolated strike where per-strike `key` changes sign, nearest to spot.

    This is gexbot's zero-gamma definition (confirmed empirically) — distinct
    from the cumulative-sum crossing in cherrypick.core.gex.interpolate_zero_gamma,
    which stays as MEIC's trading gate uses it. Series is sorted ascending.
    """
    crossings = []
    for a, b in zip(series, series[1:], strict=False):
        va, vb = a[key], b[key]
        if (va < 0 <= vb) or (va >= 0 > vb):
            den = vb - va
            t = (-va / den) if den else 0.5
            crossings.append(round(a["strike"] + t * (b["strike"] - a["strike"]), 2))
    return min(crossings, key=lambda z: abs(z - spot)) if crossings else None


def _volume_totals(series: list[dict]) -> dict:
    """Volume-basis rollups (total call/put GEX, net, walls) mirroring
    compute_gex_profile's OI totals, over the per-strike *_vol fields.

    Zero-gamma is NOT computed here — build_gex sets both display zero-gammas
    via _flip_nearest_spot (gexbot's per-strike sign-flip-nearest-spot definition).
    """
    total_call = sum(s["call_gex_vol"] for s in series if s["call_gex_vol"] > 0)
    total_put = abs(sum(s["put_gex_vol"] for s in series if s["put_gex_vol"] < 0))
    net = sum(s["net_gex_vol"] for s in series)
    call_wall = max(series, key=lambda s: s["call_gex_vol"], default=None)
    put_wall = min(series, key=lambda s: s["put_gex_vol"], default=None)
    return {
        "total_call_gex_vol": round(total_call),
        "total_put_gex_vol": round(total_put),
        "net_gex_vol": round(net),
        "call_wall_vol": call_wall["strike"] if call_wall else None,
        "put_wall_vol": put_wall["strike"] if put_wall else None,
    }


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

    # Read-only: the continuous recording of every symbol's spot is done by the dashboard's background
    # recorder (service.record_spots) so a symbol's trail has no gap while a different one is on screen.
    spot_history = _fetch_spot_history(Path(cfg["history_db_path"]), symbol)
    market_open_ts, market_close_ts = _market_open_close_ts()

    series = profile["series"]
    spot_disp = (snap.spot or 0) * snap.strike_scale
    totals = {**profile["totals"], **_volume_totals(series)}
    totals["zero_gamma"] = _flip_nearest_spot(series, "net_gex", spot_disp)
    totals["zero_gamma_vol"] = _flip_nearest_spot(series, "net_gex_vol", spot_disp)

    return {
        "ok": True,
        "symbol": symbol,
        "expiration": snap.expiration,
        "underlying_price": snap.spot,
        "source": snap.source,
        "series": series,
        "spot_history": spot_history,
        "market_open_ts": market_open_ts,
        "market_close_ts": market_close_ts,
        "totals": totals,
    }
