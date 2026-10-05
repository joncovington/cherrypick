"""build_gex — the pure, HTTP-free seam: snapshot -> core aggregation -> chart payload.

Both entrypoints (the module's own ``dashboard --serve`` and the umbrella's ``dashboard --serve``,
which subprocesses ``run.py gex --json``) go through here, so the payload shape is defined in one place.
The per-strike OI+volume GEX math itself is ``cherrypick.core.gex.compute_gex_profile`` — shared with
MEIC's dashboard so the two render identically off the same cache.
"""

from __future__ import annotations

import os
import signal
import sqlite3
import time
from datetime import datetime
from pathlib import Path

# One ET for the suite — see cherrypick.core.clock.
from cherrypick.core import looplock
from cherrypick.core.clock import ET as _ET
from cherrypick.core.gex import (
    compute_gex_profile,
    nearest_zero_gamma,
    net_walls,
    volume_totals,
)

from cherrypick.gex import provider as _provider
from cherrypick.gex import regime as _regime


def _today() -> str:
    return datetime.now(_ET).strftime("%Y-%m-%d")


def _market_open_close_ts() -> tuple[float, float]:
    """Today's 09:30 / 16:00 ET as unix timestamps, for mapping the spot trail onto a session x-axis."""
    now = datetime.now(_ET)
    open_dt = now.replace(hour=9, minute=30, second=0, microsecond=0)
    close_dt = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return open_dt.timestamp(), close_dt.timestamp()


_ensured_history_dbs: set[str] = set()


# Flow beside positioning (2026-10-05): what traded, session-cumulative as the feed reports it,
# recorded so "does call volume outweighing put volume move spot?" can be asked of the walls
# themselves. Until then only `net_gex_vol` existed -- a whole-chain, gamma-weighted call-minus-put
# net -- and over 47 sessions its sign tracked spot against the day's open (93% call-heavy 5+ points
# up, 27% 5+ points down) and predicted nothing after it. Raw measures only; any ratio is a read-side
# derivation. NULL on rows before the columns existed, never backfilled (the cache keeps no volume
# history). Added by ALTER TABLE, so readers must select by name.
FLOW_COLUMNS = (
    "call_volume",  # contracts: every call on the chain
    "put_volume",  # contracts: every put on the chain
    "total_call_gex_vol",  # dollar gamma of the call volume (volume_totals)
    "total_put_gex_vol",  # dollar gamma of the put volume, positive
    "call_wall_volume",  # calls traded at the stored call_wall strike
    "put_wall_volume",  # puts traded at the stored put_wall strike
)


def _ensure_history_table(conn: sqlite3.Connection, db_path: Path | str | None = None) -> None:
    """Create the history tables if absent. Idempotent, and skipped once a given file has been
    prepared in this process — the recorder calls this every tick for the life of the daemon, and
    a table cannot go missing underneath a process that already made it."""
    if db_path is not None:
        key = str(Path(db_path).resolve())
        if key in _ensured_history_dbs:
            return
    conn.execute(
        "CREATE TABLE IF NOT EXISTS gex_spot_history ("
        "symbol TEXT NOT NULL, trade_date TEXT NOT NULL, ts REAL NOT NULL, spot REAL NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gsh_sym_date ON gex_spot_history(symbol, trade_date)")
    # Regime history: the per-strike profile was recomputed live and thrown away, so
    # "what did GEX look like when that trade was accepted?" was unanswerable — even
    # though MEIC gates entries on GEX and flies runs a dedicated gex arm. One compact
    # row per (symbol, ~5 min): the regime summary, not the full profile.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS gex_regime_history ("
        "symbol TEXT NOT NULL, trade_date TEXT NOT NULL, ts REAL NOT NULL, spot REAL, "
        "net_gex REAL, net_gex_vol REAL, zero_gamma REAL, call_wall REAL, put_wall REAL, "
        "expiration TEXT)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_grh_sym_date ON gex_regime_history(symbol, trade_date)")
    have = {r[1] for r in conn.execute("PRAGMA table_info(gex_regime_history)")}
    for column in FLOW_COLUMNS:
        if column not in have:
            conn.execute(f"ALTER TABLE gex_regime_history ADD COLUMN {column} REAL")
    if db_path is not None:
        _ensured_history_dbs.add(str(Path(db_path).resolve()))


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


# Default matches the quote-freshness limit the trading modules use. The recorder samples every
# ~15s, so anything approaching two minutes without a print means the underlying is not trading —
# outside RTH, or the producer has stalled. Set `source.max_spot_age_seconds` to null to record
# whatever the cache holds, which is the pre-2026-08-20 behaviour.
DEFAULT_SPOT_MAX_AGE_SECONDS = 120


def spot_max_age_seconds(cfg: dict) -> float | None:
    """How old a cached print may be and still count as a sample, or None to accept any age."""
    source = cfg.get("source") or {}
    if "max_spot_age_seconds" in source:
        value = source["max_spot_age_seconds"]
        return None if value is None else float(value)
    return DEFAULT_SPOT_MAX_AGE_SECONDS


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
    # One read of the shared cache for every symbol, rather than a fresh connection each — the
    # recorder runs this on a 15s cadence for the whole session.
    #
    # Age-gated: a print older than this is treated as absent, so the trail gets a GAP rather than
    # another copy of a frozen price. Before this the recorder had no age check at all and wrote
    # whatever the cache held — on 2026-08-19 that produced 5,737 samples of which 4,193
    # consecutive pairs were the identical value, because it kept sampling through the night and
    # through any stall. A flat line and a dead feed read the same; a gap does not.
    spots = _provider.read_spots(cfg["stream_cache_db"], syms, max_age_seconds=spot_max_age_seconds(cfg))
    if not spots:
        return 0
    conn = sqlite3.connect(db_path)
    try:
        _ensure_history_table(conn, db_path)
        today = _today()
        now = time.time()
        rows = [(sym, today, now, spots[sym]) for sym in syms if sym in spots]
        conn.executemany("INSERT INTO gex_spot_history (symbol, trade_date, ts, spot) VALUES (?,?,?,?)", rows)
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def _recorder_pid_file(cfg: dict) -> Path:
    """PID file for the recorder daemon, alongside its history DB (the module's data home)."""
    return Path(cfg["history_db_path"]).parent / "recorder.pid"


# A live pid is not a live loop: the 2026-07-23 recorder was up, answering --status truthfully, and
# wrong — and a WEDGED loop is the same shape with a different cause, invisible to a pid check. So
# the daemon PUBLISHES its liveness (the flies heartbeat convention: top of every tick, before any
# work), and --status reports `stalled` when the beat goes silent, which is what lets the watchdog
# recycle a wedged recorder the way it already recycles a stale-config one. 180s is 12 missed beats
# at the 15s cadence — generous on purpose, so tripping it means the loop is genuinely stuck rather
# than one slow tick.
RECORDER_STALL_SECONDS = 180


def _recorder_heartbeat_path(cfg: dict) -> Path:
    return Path(cfg["history_db_path"]).parent / "recorder.heartbeat"


def _beat(cfg: dict) -> None:
    """Touch the heartbeat. Best-effort: a beat that cannot be written must never stop the loop —
    the degrade is 'not silence-supervised', the calendars lesson applied here."""
    try:
        hb = _recorder_heartbeat_path(cfg)
        hb.parent.mkdir(parents=True, exist_ok=True)
        hb.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass


_pid_alive = looplock.pid_alive  # noqa: F401  (re-exported: tests monkeypatch this name)


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
    """{"ok", "running", "pid", "stalled", ...} — the daemon-liveness contract the orchestrator's
    status_argv reads. `stalled` is true only when the process is ALIVE and its published heartbeat
    has gone silent past RECORDER_STALL_SECONDS — the signal the watchdog recycles on. A missing
    heartbeat (pre-heartbeat daemon, or a beat that could not be written) reports `stalled: false`:
    restarting on "I can't tell" is the failure the heartbeat convention exists to fix."""
    pid = _running_recorder_pid(cfg)
    status: dict = {"ok": True, "running": pid is not None, "pid": pid}
    if pid is None:
        return status
    status["stalled"] = False
    try:
        age = time.time() - _recorder_heartbeat_path(cfg).stat().st_mtime
    except OSError:
        return status
    status["heartbeat_age_seconds"] = round(age, 1)
    status["stalled"] = age > RECORDER_STALL_SECONDS
    return status


def acquire_recorder_lock(cfg: dict) -> bool:
    """Claim the single-writer recorder lock for THIS process. True if we hold it and should record;
    False if another live process already does. The pid file is the lock, shared by the standalone
    `run.py record` daemon AND the dashboard's own record loop — so exactly one of them ever writes the
    trail, whichever grabs it first. The 2026-07-23 double-write (a stale daemon + the dashboard both
    recording) is exactly what this closes. Claim is via O_EXCL create so two racers can't both win."""
    pf = _recorder_pid_file(cfg)
    existing = _running_recorder_pid(cfg)  # clears a stale (dead-pid) file as a side effect
    if existing is not None:
        return existing == os.getpid()  # already ours (re-entrant), else someone else holds it
    pf.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(pf, "x", encoding="utf-8") as fh:  # exclusive create: loses cleanly to a racer
            fh.write(str(os.getpid()))
        return True
    except FileExistsError:
        return False


def release_recorder_lock(cfg: dict) -> None:
    """Drop the lock iff we hold it (never delete another process's pid file)."""
    pf = _recorder_pid_file(cfg)
    try:
        if pf.exists() and int(pf.read_text().strip()) == os.getpid():
            pf.unlink(missing_ok=True)
    except (ValueError, OSError):
        pass


def _force_kill(pid: int) -> None:
    try:
        if os.name == "nt":
            import ctypes

            PROCESS_TERMINATE = 0x0001
            h = ctypes.windll.kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
            if h:
                ctypes.windll.kernel32.TerminateProcess(h, 1)
                ctypes.windll.kernel32.CloseHandle(h)
        else:
            os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def stop_recorder(cfg: dict) -> dict:
    """Stop a running recorder daemon, confirming it is actually dead before clearing the pid file.

    The old version SIGTERM'd then immediately unlinked the pid file — so a slow or wedged process left
    the file gone while it lived, and the next start saw "nothing running" and spawned a DUPLICATE beside
    the orphan (the 2026-07-23 stale-recorder incident). Now: signal, wait for exit, escalate to a
    force-kill if it ignores SIGTERM, and only then clear the file."""
    pid = _running_recorder_pid(cfg)
    if pid is None:
        return {"ok": True, "running": False, "detail": "not running"}
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        return {"ok": False, "pid": pid, "error": str(exc)}
    for _ in range(50):  # up to ~5s for a graceful exit
        if not _pid_alive(pid):
            _recorder_pid_file(cfg).unlink(missing_ok=True)
            return {"ok": True, "signal": "SIGTERM", "pid": pid}
        time.sleep(0.1)
    _force_kill(pid)  # ignored SIGTERM (common on Windows) — don't leave a lingering writer
    time.sleep(0.2)
    _recorder_pid_file(cfg).unlink(missing_ok=True)
    return {"ok": True, "signal": "SIGKILL", "pid": pid, "detail": "force-killed after SIGTERM timeout"}


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
        r = record_regimes(cfg)
        m = _regime.sample(cfg)
        print(
            f"recorded spot for {n}/{len(syms)} symbols, {r} gex regime row(s), "
            f"market regime: {m['status']} ({m['usable']}/{m['written']} usable)"
        )
        return 0

    if not acquire_recorder_lock(cfg):
        print(f"recorder already running (pid {_running_recorder_pid(cfg)})")
        return 0

    from cherrypick.gex import config as _config  # local — config bootstraps nothing

    def _on_term(*_):  # POSIX: SIGTERM -> graceful stop (Windows kills forcefully; pid cleaned lazily)
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _on_term)

    log_dir = _config.logs_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_dir / "recorder.log", encoding="utf-8"), logging.StreamHandler()],
    )
    log = logging.getLogger("cherrypick-gex.recorder")
    log.info("spot recorder starting: %s every %ss -> %s", syms, interval, cfg["history_db_path"])
    print(f"cherrypick-gex spot recorder: {syms} every {interval}s  (Ctrl-C to stop)")

    # The recorder is the long-lived process, so it (re)declares the regime sampler's quote-only
    # legs itself at startup — the series must not depend on some other module's declaration.
    from cherrypick.gex import stream_request as _stream_request

    _stream_request.register(cfg)

    # The dropped-readings guard (flies' stale-writer lesson, long-row form): a reading the ledger
    # recorded last session that this checkout no longer declares means the running code is behind
    # the database. Logged, never enforced — a stale checkout cannot fix itself, and refusing to
    # record would turn a telemetry gap into an outage.
    try:
        _gconn = sqlite3.connect(Path(cfg["history_db_path"]))
        try:
            _regime.ensure_tables(_gconn, cfg["history_db_path"])
            dropped = _regime.dropped_readings(_gconn, today=_today())
            if dropped:
                log.warning(
                    "stale checkout? readings recorded last session but not declared now: %s",
                    sorted(dropped),
                )
        finally:
            _gconn.close()
    except Exception as exc:  # noqa: BLE001 — the guard is telemetry about telemetry
        log.warning("dropped-readings check failed: %s", exc)

    heartbeat_every = max(1, 300 // interval)  # a ~5-minute INFO heartbeat; otherwise stay quiet
    ticks = 0
    try:
        while True:
            _beat(cfg)  # top of every tick, before any work — liveness is published, never inferred
            try:
                n = record_spots(cfg)
                record_regimes(cfg)  # internally throttled to ~5-minute rows
                _regime.sample(cfg)  # internally throttled to ~1-minute rows, RTH-gated
                ticks += 1
                if ticks % heartbeat_every == 0:
                    log.info("recorded %d/%d symbols (tick %d)", n, len(syms), ticks)
            except Exception as exc:  # a data hiccup must never kill the recorder
                log.warning("record failed: %s", exc)
            time.sleep(interval)
    except KeyboardInterrupt:
        log.info("spot recorder stopped (%d ticks)", ticks)
    finally:
        release_recorder_lock(cfg)
    return 0


def record_regimes(
    cfg: dict,
    symbols: list[str] | None = None,
    min_interval_s: int = 300,
    *,
    now_et: datetime | None = None,
) -> int:
    """Persist a compact GEX regime row per symbol (net GEX by OI and by volume, zero
    gamma, walls, spot) into gex_regime_history — the historical dimension the audit
    found entirely missing: regime-vs-outcome analysis needs to know what GEX WAS, and
    the live profile was recomputed and discarded on every request.

    Throttled internally (one row per symbol per `min_interval_s`), so callers can invoke
    it on whatever cadence they already run; computing a profile is heavier than reading
    a spot, and a 5-minute regime series is plenty for session-level analysis.
    Best-effort per symbol. Returns rows written.

    **RTH only.** Off-hours the session's own chain often has no greeks yet, so the provider falls
    forward to the next chain that does -- usually an extra window another module holds, which
    keeps streaming but has no market to describe. Recorded under today's date, those rows became
    the overview's pre-open gamma flip and walls (2026-09-21 read walls 7500/7725 off the 09-25
    chain against the prior session's 7600/7645) and the advisor's "open" walls. Nothing off-hours
    is a reading of a session, so nothing is written."""
    when = now_et or datetime.now(_ET)
    if not _regime.in_rth(when):
        return 0
    syms = [str(s).strip().upper() for s in (symbols if symbols is not None else cfg.get("symbols") or [])]
    if not syms:
        return 0
    db_path = Path(cfg["history_db_path"])
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        _ensure_history_table(conn)
        today = when.astimezone(_ET).date().isoformat()  # `now_et` is tz-aware, like the default
        now = when.timestamp()
        written = 0
        for sym in syms:
            row = conn.execute(
                "SELECT MAX(ts) FROM gex_regime_history WHERE symbol = ? AND trade_date = ?",
                (sym, today),
            ).fetchone()
            if row and row[0] is not None and (now - row[0]) < min_interval_s:
                continue
            try:
                snap = _provider.snapshot_from_stream_cache(cfg["stream_cache_db"], sym)
                if snap.source == "missing" or snap.expiration is None:
                    continue
                profile = compute_gex_profile(
                    snap.chain_entries,
                    snap.greeks,
                    snap.oi,
                    snap.volume,
                    snap.spot or 0,
                    strike_scale=snap.strike_scale,
                )
                if not profile.get("ok"):
                    continue
                series = profile["series"]
                spot_disp = (snap.spot or 0) * snap.strike_scale
                totals = {**profile["totals"], **volume_totals(series)}
                call_wall, put_wall = net_walls(series, "net_gex")
                flow = flow_measures(series, call_wall, put_wall, totals)
                conn.execute(
                    "INSERT INTO gex_regime_history (symbol, trade_date, ts, spot, net_gex, "
                    "net_gex_vol, zero_gamma, call_wall, put_wall, expiration, "
                    f"{', '.join(FLOW_COLUMNS)}) "
                    f"VALUES (?,?,?,?,?,?,?,?,?,?,{','.join('?' * len(FLOW_COLUMNS))})",
                    (
                        sym,
                        today,
                        now,
                        spot_disp,
                        totals.get("net_gex"),
                        totals.get("net_gex_vol"),
                        nearest_zero_gamma(series, spot_disp, "net_gex"),
                        call_wall,
                        put_wall,
                        str(snap.expiration),
                        *(flow[c] for c in FLOW_COLUMNS),
                    ),
                )
                written += 1
            except Exception:
                continue  # one symbol's hiccup must not lose the others
        conn.commit()
        return written
    finally:
        conn.close()


def flow_measures(series: list[dict], call_wall: float | None, put_wall: float | None, totals: dict) -> dict:
    """The `FLOW_COLUMNS` for one profile: chain-wide call and put contracts, their dollar gamma
    (`volume_totals`), and the volume at the two stored walls -- calls at the call wall, puts at the
    put wall. A wall with no series row records NULL, never zero."""
    at = {s["strike"]: s for s in series}
    return {
        "call_volume": sum(s["call_vol"] for s in series),
        "put_volume": sum(s["put_vol"] for s in series),
        "total_call_gex_vol": totals.get("total_call_gex_vol"),
        "total_put_gex_vol": totals.get("total_put_gex_vol"),
        "call_wall_volume": at[call_wall]["call_vol"] if call_wall in at else None,
        "put_wall_volume": at[put_wall]["put_vol"] if put_wall in at else None,
    }


def repair_regime_history(db_path: Path | str, *, apply: bool = False) -> dict:
    """Find (and with `apply`, delete) `gex_regime_history` rows recorded outside RTH.

    Until 2026-09-30 the recorder wrote around the clock, and an off-hours row is no session's
    reading: the session's own chain had no greeks yet, so the provider fell forward to whatever
    chain was still streaming -- usually another expiry -- and filed it under today. Those rows were
    the overview's pre-open levels and the advisor's "open" walls. Read-only unless `apply`, which
    first copies the DB beside itself, so the report a dry run prints is exactly what an apply
    removes and the removed rows stay recoverable.
    """
    db_path = Path(db_path)
    report: dict = {"ok": True, "applied": bool(apply), "db": str(db_path), "off_hours": 0, "by_date": {}}
    if not db_path.exists():
        return {**report, "ok": False, "reason": "no history db"}
    conn = sqlite3.connect(db_path)
    try:
        doomed: list[int] = []
        for rowid, trade_date, ts in conn.execute("SELECT rowid, trade_date, ts FROM gex_regime_history"):
            if ts is not None and _regime.in_rth(datetime.fromtimestamp(float(ts), _ET)):
                continue
            doomed.append(rowid)
            report["by_date"][trade_date] = report["by_date"].get(trade_date, 0) + 1
        report["off_hours"] = len(doomed)
        report["kept"] = conn.execute("SELECT COUNT(*) FROM gex_regime_history").fetchone()[0] - len(doomed)
        if apply and doomed:
            backup = db_path.with_name(f"{db_path.name}.bak-{datetime.now(_ET):%Y%m%d%H%M%S}")
            conn.execute("VACUUM INTO ?", (str(backup),))
            report["backup"] = str(backup)
            for start in range(0, len(doomed), 900):
                chunk = doomed[start : start + 900]
                conn.execute(
                    f"DELETE FROM gex_regime_history WHERE rowid IN ({', '.join('?' * len(chunk))})", chunk
                )
            conn.commit()
        report["by_date"] = dict(sorted(report["by_date"].items()))
        return report
    finally:
        conn.close()


def build_gex(cfg: dict, symbol: str | None = None) -> dict:
    """Read a snapshot from the configured stream cache, aggregate it, and return the chart payload.

    Payload shape matches MEIC's dashboard ``_build_gex_data`` so the two share the same client JS:
    ``{ok, symbol, expiration, underlying_price, source, series, spot_history, market_open_ts,
    market_close_ts, totals}`` — or ``{ok: False, error}`` when the cache isn't populated yet.
    """
    from cherrypick.gex.config import default_symbol  # local import; config bootstraps nothing

    symbol = (symbol or default_symbol(cfg)).strip().upper()
    snap = _provider.snapshot_from_stream_cache(cfg["stream_cache_db"], symbol)

    if snap.source == "missing":
        return {
            "ok": False,
            "symbol": symbol,
            "error": f"stream cache not found at {cfg['stream_cache_db']} — is the MEIC streamer running?",
        }
    if snap.expiration is None:
        return {
            "ok": False,
            "symbol": symbol,
            "error": f"no cached chain for {symbol} yet — is the MEIC streamer subscribed to it?",
        }

    profile = compute_gex_profile(
        snap.chain_entries,
        snap.greeks,
        snap.oi,
        snap.volume,
        snap.spot or 0,
        strike_scale=snap.strike_scale,
    )
    if not profile.get("ok"):
        return {"ok": False, "symbol": symbol, "error": profile.get("error", "insufficient GEX data")}

    # Read-only: the continuous recording of every symbol's spot is done by the dashboard's background
    # recorder (service.record_spots) so a symbol's trail has no gap while a different one is on screen.
    spot_history = _fetch_spot_history(Path(cfg["history_db_path"]), symbol)
    market_open_ts, market_close_ts = _market_open_close_ts()

    series = profile["series"]
    spot_disp = (snap.spot or 0) * snap.strike_scale
    totals = {**profile["totals"], **volume_totals(series)}
    totals["zero_gamma"] = nearest_zero_gamma(series, spot_disp, "net_gex")
    totals["zero_gamma_vol"] = nearest_zero_gamma(series, spot_disp, "net_gex_vol")
    totals["call_wall"], totals["put_wall"] = net_walls(series, "net_gex")
    totals["call_wall_vol"], totals["put_wall_vol"] = net_walls(series, "net_gex_vol")

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
