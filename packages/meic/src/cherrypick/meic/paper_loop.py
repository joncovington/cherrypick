"""Standalone paper-trading loop daemon.

Runs the parallel-shadow paper engine (src/paper.py) unattended across every configured
symbol on the live market-hours cadence - the code counterpart to the agent-orchestrated
.claude/commands/paper-loop.md, so a paper session runs in the background like the streamer
instead of needing a per-iteration agent invocation. All writes go to paper_trades.db in the
data home (~/.cherrypick/data/meic by default, see src/paths.py); the live account and
meic_trades.db are never touched.

Each iteration, for every symbol in config.json's `symbols`:
  - fetches the live underlying price + IV rank, the shared VIX / VIX1D (→ ratio), and GEX,
  - builds wing-width candidates (from `wing_widths_by_symbol`) at the VIX-banded short delta,
  - hands the snapshot to paper.process_symbol, which marks/exits open ICs across all four
    profiles (including the physically-settled early force-close + friction) and evaluates
    new entries per profile.

CLI:
  python src/paper_loop.py            # run the loop in the foreground
  python src/paper_loop.py --once     # run a single iteration and exit (for testing)
  python src/paper_loop.py --status   # print daemon status
  python src/paper_loop.py --settle-overdue  # settle past-close sessions at the official close
  python src/paper_loop.py --stop     # stop a running daemon

Launch hidden in the background (like the streamer):
  Start-Process python -ArgumentList 'src/paper_loop.py' -WorkingDirectory $PWD -WindowStyle Hidden
"""

import argparse
import json
import logging
import os
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from cherrypick.core import advice as _core_advice  # bounded-advice validator
from cherrypick.core import calendar as _cal  # shared NYSE trading-day calendar
from cherrypick.core import chaindelta as _chaindelta  # delta from a chain's own mids (2026-10-27)
from cherrypick.core import config as _cfg
from cherrypick.core import home as _core_home  # the shared state dir
from cherrypick.core import logs as _logs
from cherrypick.core import looplock
from cherrypick.core import settlement as _settlement  # the suite's one official-close source
from cherrypick.core import viz as _viz  # the suite's one money formatter

# One ET for the suite — see cherrypick.core.clock.
from cherrypick.core.clock import ET as _ET  # noqa: E402

from cherrypick.meic import (
    paper,
    regime,  # market-dimension tagging for the per-iteration (uncensored) regime row
    stabilization_gate,  # completed-bar stabilization oracle for `stabilization_entry` arms
    stream_request,  # declares symbols + open paper legs to the streamer
)
from cherrypick.meic import paths as _paths  # ~/.cherrypick/data/meic or MEIC_DATA_DIR


def _now_et():
    return datetime.now(_ET)


# packages/meic -- this file sits at src/cherrypick/meic/paper_loop.py, so the package root is four
# parents up. Only used as the `cwd` for the detached --_run child below.
_ROOT = Path(__file__).resolve().parents[3]

# Runtime data (DB, PID, lock) lives in the data home and logs in the logs home; only config stays
# in the package.
# These are on-disk runtime artifact names, deliberately NOT the dotted module path. The namespace
# migration briefly renamed them to `cherrypick.meic.paper_loop.*`, which changed three things it had
# no reason to touch: the log split in two (months of history under the old name, live writes under
# the new, with the orchestrator's config still pointing at the dead one), and the PID and lock files
# moved -- so a process started before the rename would have been invisible to the single-instance
# guard, which is the one thing that guard exists to prevent. flies kept its own names through the
# same migration; this matches it. Renaming these is a data migration, not a refactor.
_PID_FILE = _paths.data_path("paper_loop.pid")
_LOCK_FILE = _paths.data_path("paper_loop.once.lock")
_LOG_FILE = _paths.log_path("paper_loop.log")
_OUTSIDE_FILE = _paths.data_path("paper_loop.outside")
_PAPER_DB = str(_paths.paper_db_path())
# `-m` rather than a path to the script: independent of this file's depth on disk and of the child's
# working directory, which matters because the supervisor runs it from its own.
_TT = [sys.executable, "-m", "cherrypick.meic.tt"]
_DB = [sys.executable, "-m", "cherrypick.meic.db", "--db", _PAPER_DB]

logger = logging.getLogger("paper_loop")
_stop = False


def _pythonw():
    """The windowless interpreter (pythonw.exe) next to sys.executable, so the scheduled task's
    --once runs don't flash a console window every tick. Falls back to sys.executable."""
    cand = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return cand if os.path.exists(cand) else sys.executable


def _emit(obj):
    """Write JSON to stdout when there is one. Under pythonw.exe (the headless task runs)
    sys.stdout is None, so a plain print() would crash the run after its work is done."""
    try:
        sys.stdout.write(json.dumps(obj, default=str) + "\n")
        sys.stdout.flush()
    except Exception:
        pass


def _setup_logging(console: bool = True):
    """Configure this module's log through `cherrypick.core.logs`, the suite's one line format.

    This used to set its own `%Y-%m-%d %H:%M:%S` format via `basicConfig`, which emitted a *naive*
    stamp — indistinguishable from flies' bracketed shape and from the orchestrator's UTC JSON, and
    the reason the dashboard's log card mis-ordered and then dropped whole sources. The shared writer
    emits an offset, so a line is an unambiguous instant. Rotation and the TTY-only console rule (the
    scheduled task runs under pythonw.exe, where writing to an invalid stdout can kill the daemon)
    both moved into it unchanged.
    """
    _logs.configure(logging.getLogger(), _LOG_FILE, console=console)


def _handle_signal(signum, frame):
    global _stop
    _stop = True
    logger.info("Received signal %s - stopping after this iteration.", signum)


# ---------------------------------------------------------------------------
# tt.py / db.py helpers
# ---------------------------------------------------------------------------

# Spawning children from a detached, hidden Windows process is fragile unless the child
# gets no inherited console/stdin: give every subprocess an explicit null stdin and the
# CREATE_NO_WINDOW flag so it can't attach to (or block on) the parent's hidden console.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _subrun(cmd, timeout=90):
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        creationflags=_NO_WINDOW,
    )


def _run_json(cmd, timeout=90):
    try:
        r = _subrun(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"ok": False, "error": (r.stdout[-200:] + r.stderr[-200:]).strip()}


_config_cache: tuple[tuple[float, int], dict] | None = None


def _load_config():
    """Read config, re-parsing only when the file has actually changed.

    The daemon calls this once per iteration on purpose: an edit is meant to take effect on the next
    iteration without a restart, and that property is preserved here — the cache is keyed on the
    file's mtime and size, so a rewritten config is picked up exactly as before. What goes away is
    re-reading and re-parsing an unchanged file every tick for the life of the process.

    Callers must treat the result as READ-ONLY. Iterations used to each get their own freshly parsed
    dict, so an in-place edit could not outlive one tick; they now share this one, and a mutation
    would persist for the life of the process. Nothing writes to it today — profile overlays go
    through `core.profiles.merge_profile`, which returns a new dict — and anything that needs a
    changed config should keep doing it that way."""
    global _config_cache
    path = _paths.config_path()
    try:
        st = os.stat(path)
        stamp = (st.st_mtime, st.st_size)
    except OSError:
        stamp = None
    if stamp is not None and _config_cache is not None and _config_cache[0] == stamp:
        return _config_cache[1]
    with open(path) as f:
        cfg = json.load(f)
    if stamp is not None:
        _config_cache = (stamp, cfg)
    return cfg


def _symbols(cfg):
    if cfg.get("symbols"):
        return [str(s).strip().upper() for s in cfg["symbols"] if str(s).strip()]
    if cfg.get("symbol"):
        return [str(cfg["symbol"]).strip().upper()]
    return ["XSP"]


def _profiles_wanting_stabilization(profiles: dict) -> bool:
    """True when any enabled registry/advice profile opts into the stabilization entry gate, so
    the loop only pays the oracle read on sessions where a trigger arm is live."""
    for name, pdef in (profiles or {}).items():
        if isinstance(pdef, dict) and not str(name).startswith("_") and pdef.get("stabilization_entry"):
            return True
    return False


# ---------------------------------------------------------------------------
# Market-data → snapshot
# ---------------------------------------------------------------------------


def _delta_target(cfg, vix):
    if vix is None:
        return cfg.get("delta_target", 0.18)
    if vix <= cfg.get("vix_band_low_max", 18):
        return cfg.get("delta_target_vix_low", 0.16)
    if vix <= cfg.get("vix_band_elevated_max", 25):
        return cfg.get("delta_target_vix_elevated", 0.14)
    if vix <= cfg.get("vix_band_high_max", 35):
        return cfg.get("delta_target_vix_high", 0.12)
    return cfg.get("delta_target_vix_crisis", 0.10)


def _session_quality(now):
    mins = now.hour * 60 + now.minute
    if mins < 10 * 60 + 15:
        return "open_volatile"
    if mins < 12 * 60:
        return "prime"
    if mins < 14 * 60:
        return "midday"
    if mins < 14 * 60 + 45:
        return "afternoon"
    return "late"


def _fetch_vix():
    d = _run_json(_TT + ["get_market_overview", "--symbols", "VIX"])
    if not d.get("ok"):
        return None
    for m in d.get("metrics", []):
        for k in ("close", "last", "mark"):
            if m.get(k) is not None:
                try:
                    return float(m[k])
                except (TypeError, ValueError):
                    pass
    return None


def _as_float(mapping, key):
    """float(mapping[key]), or None when absent/blank/non-numeric — the market-metrics API returns
    these as strings and occasionally omits them."""
    v = mapping.get(key)
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _fetch_overview(symbol):
    """Return (underlying_price, iv_rank, iv_percentile) or (None, None, None).

    Both vol measures are captured because they answer different questions and can diverge sharply:
    IV *rank* is (current - 52wk low) / (52wk high - low), so a single spike in the lookback
    permanently compresses it; IV *percentile* is the share of days below current IV, which is
    outlier-robust. Observed 2026-07-18: NDX rank 0.192 vs percentile 0.953 on the same reading
    (and NDX rank sat 4x off QQQ's despite tracking the same index). Recording the pair lets the
    entry gate be re-based on evidence rather than on whichever number the vendor happens to serve."""
    d = _run_json(_TT + ["get_market_overview", "--symbols", symbol])
    if not d.get("ok"):
        return None, None, None
    for m in d.get("metrics", []):
        if str(m.get("symbol", "")).upper() != symbol:
            continue
        price = None
        for k in ("close", "last", "mark"):
            if m.get(k) is not None:
                try:
                    price = float(m[k])
                    break
                except (TypeError, ValueError):
                    pass

        return (
            price,
            _as_float(m, "implied_volatility_index_rank"),
            _as_float(m, "implied_volatility_percentile"),
        )
    return None, None, None


def _fetch_atr(symbol, lookback_days):
    """N-day true-range ATR in points from the streamer's accumulated session-OHLC rows
    (tt.py get_atr), or None while the cache is still warming up / the streamer is down.
    None keeps the ATR gate inactive — the fail-open convention the gate documents."""
    d = _run_json(_TT + ["get_atr", "--symbol", symbol, "--days", str(lookback_days)])
    return d.get("atr") if d.get("ok") else None


def _fetch_intraday_range_pct(symbol):
    """Today's session range as a fraction of price (tt.py get_intraday_range), or None
    when no Summary row exists yet. Feeds the quarterly-expiry and FOMC post-blackout
    range gates; None leaves them inactive rather than fabricating a zero range."""
    d = _run_json(_TT + ["get_intraday_range", "--symbol", symbol])
    return d.get("range_pct") if d.get("ok") else None


def _fetch_session(symbol):
    """Today's day_open and range_pct off ONE get_intraday_range call — the regime trend
    dimension (spot vs. today's open) rides the same Summary-row read the range gates already
    fetch every iteration, rather than costing its own round trip. None/None while the cache is
    warming up or the streamer is down (the same fail-open convention _fetch_intraday_range_pct
    and _fetch_atr already follow)."""
    d = _run_json(_TT + ["get_intraday_range", "--symbol", symbol])
    if not d.get("ok"):
        return None, None
    return d.get("day_open"), d.get("range_pct")


def _build_candidates(
    symbol, last, widths, delta_targets, default_delta, today, *, delta_source="stream", now=None
):
    """Fetch the 0DTE chain and build wing-width candidates + leg_quotes. Mirrors the
    strike-selection the live loop/paper-loop.md describe: nearest-delta short strikes,
    wings at each configured width. A wide strike window (80) also covers near-money legs
    of already-open ICs so they mark this iteration.

    `delta_targets` is the set of short-delta bands to build (the VIX-banded default plus any
    `short_delta_target` a profile requests). Each candidate is tagged with its `short_delta` and
    `is_default_delta`; `_select_candidates` then hands each profile only its own band, so profiles
    without a `short_delta_target` see exactly the default-band menu they saw before."""
    chain = _run_json(
        _TT
        + [
            "get_option_chain",
            "--symbol",
            symbol,
            "--expiration",
            today,
            "--include_quotes",
            "--include_greeks",
            "--around_price",
            str(last),
            "--strike_count",
            "80",
        ]
    )
    if not chain.get("ok"):
        return [], {}, chain.get("error", "chain fetch failed")
    entries = []
    for _exp, rows in chain.get("chain", {}).items():
        entries = rows
        break
    years = _chaindelta.years_to_close(now or datetime.now(_chaindelta.ET))
    return candidates_from_chain(
        entries, last, widths, delta_targets, default_delta, delta_source=delta_source, years=years
    )


DELTA_SOURCES = ("stream", "computed")


def delta_source_for(cfg: dict, day: str) -> str:
    """The delta source in force on session `day`: the config's `delta_source`, except "stream" for
    a session before `delta_source_since` (the declared boundary, an ISO date). A loop asks for today;
    a validation replay asks for the session it replays, so it reproduces what that session did."""
    source = cfg.get("delta_source", "stream")
    since = cfg.get("delta_source_since")
    if source != "stream" and since and day < str(since):
        return "stream"
    return source


def candidates_from_chain(
    entries, last, widths, delta_targets, default_delta, *, delta_source="stream", years=None
):
    """The candidate menu and `leg_quotes` from one expiration's chain rows (tt `get_option_chain`'s
    shape: strike_price, option_type, streamer_symbol, bid, ask, delta). Pure, so the quote-history
    replay (`meic.replay`) selects strikes with exactly the code the paper loop runs.

    `delta_source` (2026-10-27): "stream" picks strikes by the data feed's delta, as before; "computed"
    by `core.chaindelta`'s delta from these rows' own mids at `years` to the close. The feed's delta
    lags its quotes by about a minute (measured 2026-10-26; the module docstring there has the
    numbers), so strikes chosen on it are a minute stale and no history source can reproduce them.
    With "computed" every row keeps the feed's value as `delta_stream`. A chain whose deltas cannot be
    computed (no put-call pair) yields no candidates -- never the feed's values as a quiet fallback --
    but still yields `leg_quotes`, which marking open condors needs."""
    if delta_source not in DELTA_SOURCES:
        raise ValueError(f"delta_source must be one of {DELTA_SOURCES}, not {delta_source!r}")
    computed_error = None
    if delta_source == "computed":
        if years is None:
            computed_error = "computed delta needs the time to the close"
        else:
            entries = _chaindelta.with_computed_delta(list(entries), years=years)
            if not any(e.get("delta") is not None for e in entries):
                computed_error = "computed delta unavailable (no put-call parity pair, or no time left)"
    calls, puts = [], []
    for e in entries:
        strike = e.get("strike_price") or e.get("strike")
        if strike is None or e.get("bid") is None or e.get("ask") is None:
            continue
        ot = str(e.get("option_type", "")).lower()
        rec = {
            "strike": float(strike),
            "streamer_symbol": e.get("streamer_symbol"),
            "delta": None if computed_error else e.get("delta"),
            "delta_stream": e.get("delta_stream", e.get("delta")),
            "bid": e.get("bid"),
            "ask": e.get("ask"),
        }
        if "c" in ot and "p" not in ot:
            calls.append(rec)
        elif "p" in ot:
            puts.append(rec)
    leg_quotes = {
        r["streamer_symbol"]: {"bid": r["bid"], "ask": r["ask"], "mid": round((r["bid"] + r["ask"]) / 2, 4)}
        for r in calls + puts
        if r["streamer_symbol"]
    }
    if not calls or not puts:
        return [], leg_quotes, "no call/put quotes"
    if computed_error:
        return [], leg_quotes, computed_error

    def nearest(pool, target):
        c = [x for x in pool if x["delta"] is not None]
        return min(c, key=lambda x: abs(abs(x["delta"]) - target)) if c else None

    by_call = {c["strike"]: c for c in calls}
    by_put = {p["strike"]: p for p in puts}
    candidates = []
    for dt in delta_targets:
        short_call = nearest([c for c in calls if c["strike"] > last], dt)
        short_put = nearest([p for p in puts if p["strike"] < last], dt)
        if not short_call or not short_put:
            continue  # this band has no short strike in range; other bands may still yield candidates
        is_default = abs(dt - default_delta) < 1e-9
        for w in widths:
            lc = by_call.get(short_call["strike"] + w)
            lp = by_put.get(short_put["strike"] - w)
            if lc and lp:
                candidates.append(
                    {
                        "wing_width": w,
                        "short_put": short_put,
                        "long_put": lp,
                        "short_call": short_call,
                        "long_call": lc,
                        "short_delta": dt,
                        "is_default_delta": is_default,
                    }
                )
        # Equalization menu (2026-10-20, `equalize_credit`): off-diagonal width PAIRS, each wing
        # sized independently -- what a trader does when the two side credits don't match: adjust
        # one wing until they do, delta still pinning the shorts. Tagged `is_pair` with
        # `put_wing`/`call_wing`; `wing_width` carries the WIDER of the two so every existing
        # single-width reader (buying power's "widest single-side loss", the credit floors, the
        # widest-first sort) sees the conservative scalar it always has. `_select_candidates`
        # hides these from any profile that has not opted in, so every existing arm's menu is
        # unchanged. Diagonal (w, w) pairs are the singles already built above, not repeated.
        # Built unconditionally to keep this function pure and profile-blind like the rest of it;
        # the menu is shared per symbol across profiles, so visibility is selection's job.
        for wp in widths:
            for wc in widths:
                if wp == wc:
                    continue
                lc = by_call.get(short_call["strike"] + wc)
                lp = by_put.get(short_put["strike"] - wp)
                if lc and lp:
                    candidates.append(
                        {
                            "wing_width": max(wp, wc),
                            "put_wing": wp,
                            "call_wing": wc,
                            "is_pair": True,
                            "short_put": short_put,
                            "long_put": lp,
                            "short_call": short_call,
                            "long_call": lc,
                            "short_delta": dt,
                            "is_default_delta": is_default,
                        }
                    )
    if not candidates:
        return [], leg_quotes, "no short strike near delta"
    return candidates, leg_quotes, None


def _open_trades_today():
    """Today's still-open positions. `get_open_trades` already scopes to `trade_date = today`."""
    d = _run_json(_DB + ["get_open_trades"])
    return d.get("open_trades", []) if d.get("ok") else []


def _open_count():
    return len(_open_trades_today())


# ---------------------------------------------------------------------------
# Settle-overdue: a cash-settled session the in-window pass never settled
# ---------------------------------------------------------------------------

# The paper tick's window ends here (see _is_trading_time): five minutes past the 16:00 close, so
# the in-window pass can settle at the bell. Past it the tick only logs "outside trading window".
_WINDOW_END_MIN = 16 * 60 + 5

# Today's session is caught up no earlier than this many minutes past the settlement time. The public
# fallbacks behind `official_index_close` serve a quote that may lag the tape, and minutes after the
# bell it can still be a pre-close tick; twenty minutes on, it is the close. Earlier than the
# watchdog's 45-minute settlement grace, so the catch-up lands before anyone is told it is missing.
_OFFICIAL_PRINT_GRACE_MIN = 20


def _at_et(session: str, hhmm: str) -> datetime:
    """`session` at `hhmm` ET, as an aware datetime (zoneinfo, or pytz where core fell back to it)."""
    naive = datetime.strptime(f"{session} {hhmm}", "%Y-%m-%d %H:%M")
    localize = getattr(_ET, "localize", None)
    return localize(naive) if localize else naive.replace(tzinfo=_ET)


def _official_close(symbol: str, session: str, today: str) -> tuple[float | None, str]:
    """(price, source) for `symbol`'s official close on `session`, from the suite's one settlement
    source, `cherrypick.core.settlement`. The caller settles only when `source` is in
    `OFFICIAL_SOURCES`.

    A PAST session is read by date (`dated_index_close`, source `yahoo_daily`) -- the flies/bwb
    catch-up path. Today's is `tt.py get_official_close` (`official_index_close`: tastytrade's posted
    close, then Yahoo, then Barchart; a provisional intraday tick is labelled so). Never the market
    overview's close/last/mark, which is what left 2026-10-13 open: it has no close after hours."""
    if session < today:
        price = _settlement.dated_index_close(symbol, session)
        return (price, "yahoo_daily") if price is not None else (None, "no_dated_close")
    d = _run_json(_TT + ["get_official_close", "--symbol", symbol], timeout=60)
    if not d.get("ok"):
        return None, str(d.get("error") or "fetch_failed")[:120]
    return d.get("price"), str(d.get("source") or "unknown")


def _overdue_groups(conn, cfg, now) -> list[dict]:
    """`{session, symbol, arms}` for every (session, symbol) still holding open paper ICs past its
    settlement, oldest first. These are 0DTE, so a past session has expired outright; today's is
    overdue once the tick's window has closed and the official print has had time to post."""
    today = now.strftime("%Y-%m-%d")
    sett_min = paper._time_to_minutes(cfg.get("expiration_settlement_time", "16:00"))
    now_min = now.hour * 60 + now.minute
    today_due = now_min >= max(_WINDOW_END_MIN, sett_min + _OFFICIAL_PRINT_GRACE_MIN)
    rows = conn.execute(
        "SELECT trade_date, symbol, arm, COUNT(*) FROM ic_trades WHERE status IN ('open', 'partial') "
        "AND trade_date <= ? GROUP BY trade_date, symbol, arm ORDER BY trade_date, symbol, arm",
        (today,),
    ).fetchall()
    groups: dict[tuple[str, str], dict] = {}
    for day, symbol, arm, n in rows:
        if day < today or today_due:
            g = groups.setdefault((day, str(symbol).upper()), {"session": day, "symbol": str(symbol).upper()})
            g.setdefault("arms", {})[arm] = n
    return list(groups.values())


def _settled_prints(conn, session: str, symbol: str) -> set[float]:
    """The settlement prices rows of this (session, symbol) were already settled at."""
    rows = conn.execute(
        "SELECT DISTINCT settle_underlying FROM ic_trades WHERE trade_date = ? AND symbol = ? "
        "AND exit_reason LIKE '%expired_settlement' AND settle_underlying IS NOT NULL",
        (session, symbol),
    ).fetchall()
    return {float(r[0]) for r in rows}


def _settlement_profiles(arms, registry: dict, base_default: str) -> tuple[dict, list[str]]:
    """(extra_profiles, unknown arms) for settling `arms`. A registry arm settles under its own def
    (a disabled one too: `enabled: false` stops entries, not the settlement of what it holds). An
    advised book gets the management-only twin `_advice_profiles` would give it, built without
    reading or recording an advice decision. Anything else cannot be evaluated, and is reported."""
    extra, unknown = {}, []
    for arm in arms:
        if arm in registry:
            continue
        if _core_advice.is_advised(arm):
            base_def = registry.get(_advised_base({}, arm, registry, base_default))
            if isinstance(base_def, dict):
                extra[arm] = {**base_def, "max_concurrent_ics": 0}
                continue
        unknown.append(arm)
    return extra, unknown


def settle_overdue(cfg, *, now=None, db_path=None, close_fn=None) -> dict:
    """Settle every cash-settled session the paper ledger still holds open past its settlement, at
    that session's OFFICIAL close, through `paper.process_symbol` -- the 2026-10-13 hand fix, made
    automatic. The tick runs this from its outside-window branch; `--settle-overdue` runs it by hand.

    2026-10-13: an internet outage from ~14:53 ET past the close. The in-window pass settles only
    when the market overview returns a price, every tick logged "SPX: ERROR no price", and after
    16:05 the loop only skipped -- 656 positions stayed open, and `--once --force` could not help
    because the overview carries no close after hours.

    Each (session, symbol) settles at one official print (`core.settlement.OFFICIAL_SOURCES`), as
    the bell would have: the snapshot's time is the settlement time, it carries no candidates and no
    quotes, the engine's clock is pinned to the settlement instant so every exit_time reads the
    close, and no entry is evaluated. The idempotent daily_summary roll-up is then re-run for the
    session. Refusals, each left open and reported: no official print (never an intraday or
    provisional price -- nothing settles, and the next tick asks again); a physically settled symbol
    (force-closed before the bell, never settled at the close); rows of the session already settled
    at a different print (one settlement price per session and symbol); an FOMC or quarterly-expiry
    day, where the engine force-closes at quotes rather than settling; an arm no profile can run.
    """
    db_path = db_path or _PAPER_DB
    now = now or _now_et()
    today = now.strftime("%Y-%m-%d")
    close_fn = close_fn or _official_close
    sett = cfg.get("expiration_settlement_time", "16:00")
    cash = {str(s).upper() for s in cfg.get("cash_settled_symbols", [])}
    registry = paper.load_profiles()
    base_default = _cfg.first_present(cfg.get("advice") or {}, *_cfg.BASE_ARM_KEYS, default="control")
    settled, left = [], []
    try:
        conn = sqlite3.connect(db_path, timeout=10)
    except sqlite3.Error as exc:
        return {"ok": False, "error": f"paper ledger: {exc}", "settled": [], "left": []}
    try:
        groups = _overdue_groups(conn, cfg, now)
        if not groups:
            return {"ok": True, "settled": [], "left": []}
        prints: dict[tuple[str, str], tuple[float | None, str]] = {}
        for g in groups:
            session, symbol, arms = g["session"], g["symbol"], g["arms"]
            n = sum(arms.values())
            if symbol not in cash:
                left.append({**g, "positions": n, "reason": "physically_settled"})
                continue
            if (symbol, session) not in prints:
                prints[(symbol, session)] = close_fn(symbol, session, today)
            price, source = prints[(symbol, session)]
            if price is None or not _settlement.is_official_source(source) or float(price) <= 0:
                left.append({**g, "positions": n, "reason": "no_official_print", "source": source})
                continue
            price = float(price)
            other = {p for p in _settled_prints(conn, session, symbol) if abs(p - price) > 0.005}
            if other:
                left.append(
                    {
                        **g,
                        "positions": n,
                        "reason": "settled_at_another_price",
                        "price": price,
                        "other": sorted(other),
                    }
                )
                continue
            extra, unknown = _settlement_profiles(arms, registry, base_default)
            runnable = [a for a in arms if a not in unknown]
            snapshot = {
                "symbol": symbol,
                "date": session,
                "now_et": sett,
                "expiration": session,
                "dte": 0,
                "underlying_price": price,
                "candidates": [],
                "leg_quotes": {},
            }
            # On an FOMC or quarterly-expiry day the engine force-closes at the bell rather than
            # settling, and a force-close needs leg quotes this pass does not have: it would only
            # count unmarked iterations. Those positions are a person's call, not this one's.
            if paper.force_close_active(snapshot, cfg, True)[0]:
                left.append({**g, "positions": n, "reason": "event_force_close_day"})
                continue
            if runnable:
                with paper.pinned_clock(_at_et(session, sett)):
                    paper.process_symbol(
                        snapshot,
                        db_path,
                        "paper",
                        profiles_filter=runnable,
                        extra_profiles=extra or None,
                        evaluate_entries=False,
                    )
            still = conn.execute(
                "SELECT COUNT(*) FROM ic_trades WHERE status IN ('open', 'partial') AND trade_date = ? "
                "AND symbol = ?",
                (session, symbol),
            ).fetchone()[0]
            if n - still:
                settled.append({**g, "positions": n - still, "price": price, "source": source})
            if still:
                left.append({**g, "positions": still, "reason": "still_open", "unknown_arms": unknown})
        for session in sorted({s["session"] for s in settled}):
            res = paper._db(["rollup_daily_summary", "--date", session], db_path)
            if res.get("ok") is False:
                logger.warning(
                    "settle-overdue: daily_summary roll-up for %s failed: %s", session, res.get("error")
                )
    finally:
        conn.close()
    for s in settled:
        logger.info(
            "settle-overdue: %s %s settled %d position(s) at %.2f (%s)",
            s["session"],
            s["symbol"],
            s["positions"],
            s["price"],
            s["source"],
        )
    for item in left:
        logger.warning(
            "settle-overdue: %s %s left %d position(s) open: %s",
            item["session"],
            item["symbol"],
            item["positions"],
            item["reason"],
        )
    return {"ok": True, "settled": settled, "left": left}


def _settle_overdue_quietly(cfg) -> dict | None:
    """`settle_overdue` from the tick's outside-window branch, never raising: a catch-up that cannot
    run must not take the tick (and its heartbeat) down with it. None when there was nothing to do."""
    try:
        out = settle_overdue(cfg)
    except Exception as exc:  # noqa: BLE001 -- the tick outlives a failed catch-up
        logger.warning("settle-overdue failed: %s: %s", type(exc).__name__, exc)
        return {"ok": False, "error": str(exc)}
    return out if (out.get("settled") or out.get("left") or not out.get("ok")) else None


# ---------------------------------------------------------------------------
# Iteration
# ---------------------------------------------------------------------------


def _is_action_outcome(o) -> bool:
    """A fill or exit (vs. a plain skip-reason string) — these stand out in the one-line log."""
    return isinstance(o, str) and (o.startswith("FILL") or o.startswith("EXIT"))


def _fmt_symbol(sym, info):
    """One readable clause per symbol. With the full experiment roster a per-profile dump would
    be unreadable, so fills/exits are listed explicitly and the (typically many) skipped profiles
    are collapsed — into 'all <reason>' when every profile skipped for the same reason, else a
    bare skip count."""
    if info.get("error"):
        return f"{sym}: ERROR {info['error']}"
    ivr = info.get("ivr")
    ivr_s = f"{ivr:.2f}" if isinstance(ivr, (int, float)) else "-"
    outc = info.get("outcomes") or {}
    if not outc:
        return f"{sym}(ivr {ivr_s}): -"
    active = [f"{p}:{o}" for p, o in outc.items() if _is_action_outcome(o)]
    skips = [o for o in outc.values() if not _is_action_outcome(o)]
    parts = []
    if active:
        parts.append(" ".join(active))
    if skips:
        uniq = set(skips)
        parts.append(f"all {next(iter(uniq))}" if (len(uniq) == 1 and not active) else f"{len(skips)} skip")
    return f"{sym}(ivr {ivr_s}): {'  '.join(parts) if parts else '-'}"


def _log_gate_blocks(symbol, outcomes) -> None:
    """One loop_log row per symbol per iteration, carrying EVERY stream's outcome this tick —
    a fill/exit, or the precise reason evaluate_entry returned — as machine-readable JSON, not
    the collapsed 'N skip' / 'all <reason>' display string _fmt_symbol produces for humans.

    `_fmt_symbol` collapses skips for readability because a per-profile dump is unreadable in the
    log line once the stream roster is more than a couple of entries — but that collapse is where
    the information a gate ledger needs (which stream, which gate, how often) was being thrown
    away. This is the same `outcomes` dict `_fmt_symbol` reads, persisted intact instead of
    summarized, so a zero-entry session is auditable per stream rather than reading as one
    undifferentiated blank. Best-effort and non-fatal, like every other loop_log write here.
    """
    if not outcomes:
        return
    try:
        _subrun(
            _DB
            + [
                "log_loop_action",
                "--action",
                "gate_block",
                "--symbol",
                symbol,
                "--reasoning",
                json.dumps(outcomes, default=str)[:4000],
            ]
        )
    except Exception:
        pass


def _log_iteration_regime(symbol, snapshot, params, result) -> None:
    """One iteration_regime row per symbol per tick — the regime this tick was in, recorded whether
    or not anything was entered.

    This is the denominator `_log_gate_blocks` above is missing. That row records WHICH gate
    refused; this one records WHAT THE MARKET WAS when it did, which is the half needed to ask
    whether a gate is refusing the regime it was built to refuse. Every regime row in ic_trades is
    conditioned on having entered, so without this the recorded regime distribution is censored by
    the very gates it would be used to evaluate.

    Tagged with the BASE config's thresholds, not any one arm's overlay: the market dimensions are
    arm-independent reads, and cutting them per-arm would give each stream its own denominator and
    make the streams incomparable — the opposite of the point. Best-effort and non-fatal, like every
    other telemetry write in this module; a lost row is a lost observation, never a lost iteration.
    """
    entries_n = blocked_n = 0
    for actions in (result or {}).get("results", {}).values():
        for a in actions:
            if a.get("entry") == "filled":
                entries_n += 1
            elif a.get("entry") == "skipped":
                blocked_n += 1
    try:
        payload = regime.market_regime_columns(snapshot, params)
    except Exception:
        return
    try:
        _subrun(
            _DB
            + [
                "save_iteration_regime",
                "--symbol",
                symbol,
                "--entries_n",
                str(entries_n),
                "--blocked_n",
                str(blocked_n),
                "--regime",
                json.dumps(payload, default=str),
            ]
            + (
                ["--underlying_price", str(snapshot["underlying_price"])]
                if snapshot.get("underlying_price") is not None
                else []
            )
        )
    except Exception:
        pass


def _format_iteration(now_et, vix, vix1d_ratio, delta_target, summary):
    """A compact, human-readable one-line iteration summary for the log and the dashboard."""
    vix_s = f"{vix:.1f}" if isinstance(vix, (int, float)) else "-"
    ratio_s = f"{vix1d_ratio:.2f}" if isinstance(vix1d_ratio, (int, float)) else "-"
    header = f"[{now_et} ET] VIX {vix_s}  1D-ratio {ratio_s}  delta {delta_target}"
    body = "   ".join(_fmt_symbol(s, summary[s]) for s in summary)
    return f"{header}   {body}"


# ---------------------------------------------------------------------------
# Deterministic end-of-day report
# ---------------------------------------------------------------------------


# Day already rolled up in THIS process. Replaces a file-exists guard on the retired EOD report --
# a marker for "this ran" must be written by the thing that ran, and the old one was settable by
# anything that could create a file of the right name (flies records the same bug: a test run made
# the marker mid-session and the loop read its own day as finished). Process-local is enough
# because `rollup_daily_summary` is idempotent by design, so a restart simply redoes it.
_ROLLED_UP_DAY: str | None = None


def _rolled_up(day) -> bool:
    return _ROLLED_UP_DAY == day


def _money(x):
    # The suite's one formatter (cherrypick.core.viz), keeping this report's "-" placeholder.
    return _viz.fmt_money(x, none="-")


# ---------------------------------------------------------------------------
# EOD supplement -- arm scorecard (breakeven identity), gate ledger, stop-policy
# table, regime coverage, iteration duration/peak positions. Isolated in its own
# function (rather than threaded through _write_eod_report's existing tables) so
# it's independently testable and the diff against the pre-arms report stays legible.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# EOD analysis report — conversational, 7-section, still fully deterministic
# ---------------------------------------------------------------------------


def _open_advised_tags():
    """DISTINCT advised:* profile tags with open/partial rows -- positions that must keep being
    managed (exits, force-close, settlement) even when today's advice is absent or disabled."""

    try:
        conn = sqlite3.connect(_PAPER_DB)
        rows = conn.execute(
            "SELECT DISTINCT arm FROM ic_trades WHERE arm LIKE 'advised:%' AND status IN ('open', 'partial')"
        ).fetchall()
        conn.close()
        return [r[0] for r in rows]
    except sqlite3.Error:
        return []


def _advice_profiles(cfg, today, *, persist=True):
    """The advised shadow book's synthetic profile(s) for `today`: (profiles dict, reason).

    Tier 1 of the agentic layer, loop side. The orchestrator's `advise` command wrote (or didn't
    write) `state/advice/meic-<today>.json`; this re-validates it with the SAME core code
    (cherrypick.core.advice) against this module's own config `advice.bounds` manifest --
    absent, stale, or invalid means baseline, i.e. no synthetic profile at all.

    The read-once-per-session mechanics live in `cherrypick.core.advice.session_decision`, which
    meic, earnings and five other modules had each written out separately. Folding meic's copy in
    was not tidying: the 2026-08-25 fix -- a baseline decision is never made sticky -- landed in
    core, and meic was one of the two modules that had lost a session to the bug it fixes.

    `persist=False` is passed by an iteration forced outside the trading window. Such an iteration
    still needs a decision to run under, but it must not be the one the session is recorded as
    having made: meic's 08-25 artifact was lost to a 01:05 ET forced run that fixed the day's
    decision four hours before the market-open iteration that would have applied it.

    Each advised book is a synthetic profile `advised:<experiment name>` -- ONE PER EXPERIMENT the
    day's artifact carried (2026-09-17; before that, one `advised:<base>` book per module, so a
    second experiment on control queued behind the first). Each is the registry def of the base
    profile its entry names with that entry's admitted params overlaid, evaluated by
    process_symbol beside the un-advised base (the control) -- the flies-arms pattern, and
    group_by_tag reads the tag for free. The base comes from the entry, never from the tag,
    which no longer carries one. A decision recorded before this date resolves to its single
    legacy `advised:<base>` book (`cherrypick.core.advice.advised_books`), which is what its rows
    were tagged.

    Open advised positions always keep a profile to run their exits: if advice is off today, a
    management-only twin (entries capped to zero) stands in, built on the base `_advised_base`
    resolves for the tag.
    """
    base = _cfg.first_present(cfg.get("advice") or {}, *_cfg.BASE_ARM_KEYS, default="control")
    decision = _core_advice.session_decision(
        _core_home.state_dir(),
        "meic",
        today,
        cfg,
        _paths.data_path("advice_active.json"),
        base_key="base_profile",
        log=lambda message: logger.info("%s", message),
        persist=persist,
    )

    registry = paper.load_profiles()
    out = {}
    for entry in _core_advice.advised_books(decision):
        base_def = registry.get(
            entry.get("base") or _cfg.first_present(decision, *_cfg.BASE_ARM_KEYS) or base
        )
        if not isinstance(base_def, dict):
            logger.warning(
                "advice [%s]: base profile %r is not in the registry; no book",
                entry["tag"],
                entry.get("base"),
            )
            continue
        # `experiment_id` is not a trading parameter: it rides on the synthetic def so the fill
        # row can copy it (paper.synthetic_entry_fill), the same way flies' arm overlay carries
        # its `arm`. Registry profiles never have it, so a control row is stamped None.
        out[entry["tag"]] = {**base_def, **entry["params"], "experiment_id": entry.get("experiment_id")}
    for tag in _open_advised_tags():
        if tag in out:
            continue
        tag_base = registry.get(_advised_base(decision, tag, registry, base))
        if isinstance(tag_base, dict):
            out[tag] = {**tag_base, "max_concurrent_ics": 0}
    return out, decision.get("reason")


def _advised_base(decision, tag, registry, default):
    """The base profile an open advised tag shadows: the decision's entry for that tag when there
    is one (a rejected entry still names its base), else the legacy `advised:<profile>` reading
    when the suffix is a registry profile, else the configured base. Never a bare split of the tag
    -- `advised:entry-window-truncate` names no profile called `entry-window-truncate`."""
    for entry in decision.get("experiments") or []:
        if isinstance(entry, dict) and entry.get("tag") == tag and entry.get("base"):
            return str(entry["base"])
    suffix = tag[len(_core_advice.ADVISED_PREFIX) :] if _core_advice.is_advised(tag) else tag
    if suffix in registry:
        return suffix
    return default


def _ensure_paper_schema() -> None:
    """Bring the paper DB's schema up to date before the iteration writes to it.

    `db.py`'s additive column migrations live in `cmd_init_db`, which is a CLI subcommand nothing
    invoked automatically — so a paper DB created before a column was added stayed missing it
    forever, and every INSERT naming that column failed. That is not hypothetical: on 2026-07-30/31
    the loop reported 186 fills and persisted **zero** rows, because five columns
    (`slippage_dollars`, `gex_net_vol_at_entry`, `pin_risk_applied`, `unmarked_iterations`,
    `last_unmarked_at`) had never been added to the live paper file.

    Idempotent (`CREATE TABLE IF NOT EXISTS` + guarded `ALTER TABLE`), so the cost is one subprocess
    per iteration. Deliberately NON-FATAL: a schema-ensure that cannot run must not take the session
    down — the save-failure check in `paper.process_symbol` is the backstop that makes any resulting
    write failure loud instead of silent.
    """
    try:
        res = subprocess.run([*_DB, "init_db"], capture_output=True, text=True, timeout=60)
        if res.returncode != 0:
            logging.warning("paper schema ensure failed: %s", (res.stderr or "").strip()[:200])
    except Exception as exc:  # noqa: BLE001 -- never let schema maintenance kill the loop
        logging.warning("paper schema ensure errored: %s: %s", type(exc).__name__, exc)


def _beat() -> None:
    """Publish liveness at the top of every tick: `state/meic.heartbeat`, the suite's one
    unconditional signal (`cherrypick.core.home.heartbeat_path`).

    Distinct from `_heartbeat_lock` below, which touches the --once LOCK file so a long iteration
    is not mistaken for a dead one. This is the watchdog's freshness signal, and meic was the last
    module without it: the check takes the freshest of the paper DB's mtime, the log's mtime and
    this file, and the first two are CONDITIONAL writes — a WAL database's main file only moves on
    a checkpoint and a log line is a side effect of having something to say. meic writes trades
    often enough that it never surfaced, which is luck rather than a contract (the calendars
    2026-08-17 lesson: a quiet healthy loop must not be indistinguishable from a wedged one).

    Best-effort: a beat that cannot be written degrades to not-silence-supervised, never to a
    failed tick."""
    _core_home.beat("meic")


def _log_outside_once(now, verb: str) -> None:
    """Log "outside trading window" on the first off-window tick only, not on every one.

    Every off-hours minute used to write this line -- about 80% of the log, and nothing reads it. The
    record that each tick ran is the supervisor's `--- start:` banner in the job's stderr log, which
    stays. Each `--once` tick is its own process, so "already said it" lives in a marker file that
    the next in-window tick removes. Best-effort: a marker that cannot be read or written only costs
    a repeated line."""
    try:
        if _OUTSIDE_FILE.exists():
            return
        _OUTSIDE_FILE.write_text(now.isoformat(timespec="seconds"), encoding="utf-8")
    except OSError:
        pass
    logger.info("outside trading window (%s ET) - %s; quiet until it reopens.", now.strftime("%H:%M"), verb)


def _clear_outside() -> None:
    try:
        _OUTSIDE_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _live_config_warnings(cfg: dict, profiles: dict) -> list[str]:
    """`paper.live_config_warnings` against the shipped example. Best-effort: an unreadable example
    costs the top-level check, never the tick."""
    try:
        example = json.loads(_paths.example_config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        example = {}
    try:
        return paper.live_config_warnings(cfg, profiles, example)
    except Exception as exc:  # a warning helper must never stop the loop
        return [f"could not check the live block: {exc}"]


def run_iteration(cfg, force=False):
    _beat()  # before every gate below: "the loop is turning over", never "it did work"
    # Wall-clock for this whole iteration, logged below alongside open-position count. Was
    # nowhere -- loop_log.duration_ms existed as a column and get_step_timing could read it, but
    # nothing here ever wrote one, so the load ceiling that governs how many streams/positions
    # this loop can carry was invisible until it was already missing ticks. See docs/paper-
    # experiments.md for the incident this closes the gap on.
    _iter_t0 = time.time()
    now = _now_et()
    if not force and not _is_trading_time(now, cfg):
        _log_outside_once(now, "skipping")
        # The one thing this branch does: settle a session the in-window pass never could (an
        # outage across the close, 2026-10-13). Two SQLite reads when nothing is overdue.
        caught_up = _settle_overdue_quietly(cfg)
        if caught_up:
            return {"skipped": "outside_trading_window", "settle_overdue": caught_up}
        return {"skipped": "outside_trading_window"}
    _clear_outside()
    # Refresh the streamer request file every tick (best-effort, never raises): a config symbols
    # change propagates without manual steps, and the paper ledger's open legs stay subscribed.
    # Underlyings still bind at streamer start, so a NEW underlying needs one streamer restart.
    stream_request.register(cfg)
    today = now.strftime("%Y-%m-%d")
    now_et = now.strftime("%H:%M")
    vix = _fetch_vix()
    vix1d = _run_json(_TT + ["get_vix1d"]).get("last")
    vix1d_ratio = round(vix1d / vix, 3) if (vix1d and vix) else None
    delta_target = _delta_target(cfg, vix)
    # Load the profile registry once per iteration so each symbol's candidate menu is the UNION
    # of every profile's wing widths (each profile then picks its own allowed subset in paper.py).
    profiles = paper.load_profiles()
    for warning in _live_config_warnings(cfg, profiles):
        logger.warning("live config: %s", warning)
    advice_profiles, _advice_reason = _advice_profiles(cfg, today, persist=not force)
    session = _session_quality(now)

    summary = {}
    mkt_symbols = {}  # per-symbol price + IV rank/percentile snapshot for the market-context capture
    for symbol in _symbols(cfg):
        # No-op outside a --once invocation (the lock file won't exist); see _heartbeat_lock.
        _heartbeat_lock()
        price, ivr, ivp = _fetch_overview(symbol)
        # Store BOTH vol measures per symbol per day: this is the series the IV-gate re-basing and
        # the rank-vs-percentile band fitting are done from (see _fetch_overview). Captured BEFORE
        # the price check on purpose — a symbol that can't trade for want of a spot price still
        # contributes to that series. RUT currently returns IV metrics but no price, and would
        # otherwise record nothing at all.
        if ivr is not None or ivp is not None:
            mkt_symbols[symbol] = {"price": price, "iv_rank": ivr, "iv_pct": ivp}
        if price is None:
            summary[symbol] = {"error": "no price"}
            continue
        # The ADVISED profile's widths must join the candidate menu too: a width the menu never
        # scanned can never fill, so without this an advisor width experiment would silently take
        # zero entries and read as a market refusal rather than a plumbing one.
        all_profiles = {**profiles, **advice_profiles}
        widths = paper.union_widths_for_symbol(symbol, cfg, all_profiles)
        extra_deltas = paper.union_short_deltas_for_symbol(symbol, cfg, all_profiles)
        delta_targets = [delta_target] + [d for d in extra_deltas if abs(d - delta_target) > 1e-9]
        candidates, leg_quotes, cand_err = _build_candidates(
            symbol,
            price,
            widths,
            delta_targets,
            delta_target,
            today,
            delta_source=delta_source_for(cfg, today),
        )
        gex = _run_json(_TT + ["get_gex", "--symbol", symbol])
        atr_lookback = int(cfg.get("regime_atr_lookback_days", 5))
        day_open, intraday_range_pct = _fetch_session(symbol)
        snapshot = {
            "symbol": symbol,
            "date": today,
            "now_et": now_et,
            "expiration": today,
            "dte": 0,
            "underlying_price": price,
            "iv_rank": ivr,
            "iv_pct": ivp,
            "iv_rank_source": "native",
            "vix": vix,
            "vix1d_ratio": vix1d_ratio,
            # All three feeds come off the streamer's stream_summary day-rows (exchange-official
            # session OHLC). None while warming up / streamer down -> the gates stay inactive and
            # the regime trend dimension tags 'unknown' rather than a fabricated flat/zero read.
            "atr_5day": _fetch_atr(symbol, atr_lookback),
            "intraday_range_pct": intraday_range_pct,
            "day_open": day_open,
            "session_quality": session,
            "gex": gex if gex.get("ok") else {"ok": False},
            "candidates": candidates,
            "leg_quotes": leg_quotes,
        }
        # Stabilization trigger verdicts (2026-10-31): computed BEFORE entries when any enabled
        # profile opted into `stabilization_entry`, from `cherrypick.meic.stabilization_gate` --
        # the completed-bar oracle (the Thinkorswim "Universal Adaptive Market Stabilization
        # Indicator" rule). Best-effort and fail-closed: the module returns None per oracle
        # without a verified read (the arm waits -- it never fires on a guess), and an oracle
        # failure never takes the tick down. `now` is passed through so a pinned-clock run
        # reads the same verdicts a live session at that instant would.
        if _profiles_wanting_stabilization(all_profiles):
            try:
                # Both threshold modes' verdicts in one pass: ATR mode's plain keys plus IV
                # mode's "-iv" keys, so differently-configured arms on the same symbol share
                # one dict without double reads.
                snapshot["stabilization_verdicts"] = {
                    **stabilization_gate.snapshot_verdicts(symbol, today, now),
                    **{
                        f"{k}-iv": v
                        for k, v in stabilization_gate.snapshot_verdicts(
                            symbol, today, now, threshold_mode="iv"
                        ).items()
                    },
                }
            except Exception as exc:  # noqa: BLE001 -- the oracle must not take the tick down
                snapshot["stabilization_verdicts"] = {}
                logger.warning(
                    "stabilization oracle failed for %s: %s: %s", symbol, type(exc).__name__, exc
                )
        result = paper.process_symbol(snapshot, _PAPER_DB, "paper", extra_profiles=advice_profiles)
        # Per-profile outcome for the log - fills and exits made to stand out from skips.
        outcomes = {}
        for prof, actions in result.get("results", {}).items():
            for a in actions:
                if a.get("entry") == "filled":
                    outcomes[prof] = f"FILL ${a.get('net_credit')}"
                elif a.get("entry") == "save_failed":
                    # Loud on purpose, and never rendered as a FILL: the row did not persist, so
                    # the study has no record of it. See paper.process_symbol for what this cost.
                    outcomes[prof] = f"SAVE-FAILED {str(a.get('error'))[:80]}"
                elif a.get("entry") == "skipped":
                    outcomes.setdefault(prof, a.get("reason"))
                elif "decision" in a:
                    act = a["decision"].get("action")
                    if act and act != "hold":
                        outcomes[prof] = f"EXIT {act}"
        summary[symbol] = {
            "ivr": ivr,
            "widths": sorted({c["wing_width"] for c in candidates}),
            "outcomes": outcomes,
            "cand_err": cand_err,
        }
        _log_gate_blocks(symbol, outcomes)
        _log_iteration_regime(symbol, snapshot, cfg, result)

    reason = _format_iteration(now_et, vix, vix1d_ratio, delta_target, summary)
    duration_ms = int((time.time() - _iter_t0) * 1000)
    try:
        open_trades_n = _open_count()
    except Exception:
        open_trades_n = None
    try:
        log_cmd = _DB + [
            "log_loop_action",
            "--action",
            "paper_iteration",
            "--reasoning",
            reason[:900],
            "--duration_ms",
            str(duration_ms),
        ]
        if open_trades_n is not None:
            log_cmd += ["--open_trades", str(open_trades_n)]
        _subrun(log_cmd)
    except Exception:
        pass
    logger.info("%s (open=%s, %dms)", reason, open_trades_n, duration_ms)

    # Capture a per-day market-context snapshot (VIX / VIX1D / per-symbol price+IV rank) for the EOD
    # analysis report. Once per iteration, upserted — the last write of the session lands closest to
    # the close. Best-effort and stdlib/DB-only: it never blocks or fails the iteration.
    try:
        ctx_cmd = _DB + ["save_market_context", "--date", today, "--symbols", json.dumps(mkt_symbols)]
        if vix is not None:
            ctx_cmd += ["--vix", str(vix)]
        if vix1d is not None:
            ctx_cmd += ["--vix1d", str(vix1d)]
        if vix1d_ratio is not None:
            ctx_cmd += ["--vix1d_ratio", str(vix1d_ratio)]
        _subrun(ctx_cmd)
    except Exception:
        pass

    # Once-per-day daily_summary roll-up, on the first pass at/after the settlement time (16:00).
    #
    # The per-module EOD reports were retired 2026-08-13 — packages/review builds one fact set across
    # every module and renders from that, so a module writing its own prose was a second,
    # unreconciled account of the same session. The roll-up stays and matters MORE now: the review
    # reads daily_summary for this module's expected-vs-observed, so it is the source of record the
    # reports used to be. Guarded on the summary row rather than a report file — a marker for "this
    # ran" must be written by the thing that ran.
    sett = cfg.get("expiration_settlement_time", "16:00")
    if (now.hour * 60 + now.minute) >= paper._time_to_minutes(sett) and not _rolled_up(today):
        global _ROLLED_UP_DAY
        try:
            res = subprocess.run(
                [*_DB, "rollup_daily_summary", "--date", today],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if res.returncode != 0:
                logger.warning("daily_summary roll-up failed: %s", (res.stderr or "").strip()[:200])
            else:
                logger.info("rolled up daily_summary for %s", today)
                _ROLLED_UP_DAY = today
        except Exception as exc:
            logger.warning("daily_summary roll-up failed: %s", exc)
    return summary


# ---------------------------------------------------------------------------
# Cadence / time gate
# ---------------------------------------------------------------------------


def _is_trading_time(now, cfg):
    # Weekend + NYSE-holiday gate via the shared calendar (cfg kept for signature compatibility).
    if not _cal.is_trading_day(now.date()):
        return False
    mins = now.hour * 60 + now.minute
    # Runs 09:30 through 16:05 - the extra 5 min past the 16:00 close lets the settlement pass
    # fire so cash-settled positions left to expire get settled at the close (paper.settlement_
    # active fires at expiration_settlement_time, default 16:00). Entries are independently
    # blocked after entry_window_end (14:30), so the extension only affects marking/settlement.
    return 9 * 60 + 30 <= mins < _WINDOW_END_MIN


def _sleep_seconds(now, cfg, open_positions):
    if not _is_trading_time(now, cfg):
        return 600  # idle outside market hours; daemon stays up until --stop
    return 120 if open_positions > 0 else 300


# ---------------------------------------------------------------------------
# Daemon control
# ---------------------------------------------------------------------------


_pid_alive = looplock.pid_alive  # noqa: F401  (re-exported: tests monkeypatch this name)


def _running_pid():
    if not _PID_FILE.exists():
        return None
    try:
        pid = int(_PID_FILE.read_text().strip())
    except ValueError:
        _PID_FILE.unlink(missing_ok=True)
        return None
    if _pid_alive(pid):
        return pid
    _PID_FILE.unlink(missing_ok=True)
    return None


def _cmd_status():
    """Daemon/task status, plus the settlement signal the orchestrator's watchdog reads.

    `session_settled` / `positions_today` / `data_reason` are the three fields
    `watchdog._check_settlement` looks for, and their absence is why meic was the one paper module
    with no `settle_overdue` check while flies, calendars, pmcc, curve and bwb all had one — the
    check reads a module's own `--status` and says nothing when it cannot find the signal, so
    enabling it for meic without these would have been a check that could not fire.

    It matters more since 2026-08-26: `paper.evaluate_open_trade` now REFUSES to settle a position
    without an underlying price rather than booking it at zero intrinsic (i.e. full credit). That is
    the right failure, but it is a silent one — the position simply stays open — and this is what
    makes it audible. The flies 2026-07-22 incident is the same shape: 5 positions unsettled for 9
    hours because settlement refuses a stale price, logged every 2 minutes, alerting nobody.

    `positions_today` deliberately repeats `open_positions` rather than being a second count: both
    come from the one `get_open_trades` call, which already scopes to today. The watchdog's field
    name is kept so this module reads like every other one it checks.
    """
    pid = _running_pid()
    open_today = _open_trades_today()
    # A row the loop could not mark is the most likely reason it is still open past the close. Left
    # absent when it is not the cause: the watchdog supplies its own wording rather than being
    # handed a guess.
    unmarked = [t for t in open_today if (t.get("unmarked_iterations") or 0) > 0]
    info = {
        "daemon_running": pid is not None,  # a long-running --start daemon (if used)
        "pid": pid,
        "open_positions": len(open_today),
        "positions_today": len(open_today),
        "session_settled": not open_today,
    }
    if unmarked:
        info["data_reason"] = f"{len(unmarked)} position(s) could not be marked this session"
    _emit(info)


def _cmd_stop():
    pid = _running_pid()
    if pid is None:
        _emit({"ok": False, "error": "not running"})
        return
    try:
        os.kill(pid, signal.SIGTERM)
        _emit({"ok": True, "signal": "SIGTERM", "pid": pid})
    except Exception as exc:
        _emit({"ok": False, "error": str(exc)})


def _spawn_detached():
    """Launch a fully detached background copy of the daemon and return. On Windows this uses
    DETACHED_PROCESS (no console at all) + a new process group, so the daemon can't be stopped
    by stray CTRL_C/CTRL_CLOSE console events from whatever shell launched it - the failure
    mode that made -WindowStyle Hidden / Task Scheduler launches exit within seconds. Stop it
    explicitly with --stop."""
    existing = _running_pid()
    if existing is not None:
        _emit({"ok": False, "error": f"Paper loop already running (pid {existing})."})
        return
    kwargs = {}
    if os.name == "nt":
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(
        [sys.executable, "-m", "cherrypick.meic.paper_loop", "--_run"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        cwd=str(_ROOT),
        **kwargs,
    )
    time.sleep(2)
    pid = _running_pid()
    _emit(
        {
            "ok": pid is not None,
            "pid": pid,
            "detail": "daemon spawned" if pid else "spawn did not register a PID yet",
        }
    )


def _loop():
    _PID_FILE.parent.mkdir(exist_ok=True)
    _PID_FILE.write_text(str(os.getpid()))
    signal.signal(signal.SIGTERM, _handle_signal)
    if os.name == "nt":
        # A detached daemon can still receive stray CTRL_C/CTRL_BREAK console events (e.g. when
        # the launching shell closes) that would otherwise trip the graceful _stop and exit the
        # loop within seconds. Ignore them; --stop hard-terminates via TerminateProcess instead.
        for _sig in (signal.SIGINT, getattr(signal, "SIGBREAK", None)):
            if _sig is not None:
                try:
                    signal.signal(_sig, signal.SIG_IGN)
                except Exception:
                    pass
    else:
        try:
            signal.signal(signal.SIGINT, _handle_signal)
        except Exception:
            pass
    logger.info("Paper loop daemon started (pid %d).", os.getpid())
    try:
        while not _stop:
            cfg = _load_config()
            now = _now_et()
            if _is_trading_time(now, cfg):
                try:
                    run_iteration(cfg)
                except Exception as exc:
                    logger.exception("iteration error: %s", exc)
            else:
                _log_outside_once(now, "idling")
                _settle_overdue_quietly(cfg)
            delay = _sleep_seconds(_now_et(), cfg, _open_count())
            # Sleep in short slices so a SIGTERM is honored promptly.
            for _ in range(delay):
                if _stop:
                    break
                time.sleep(1)
    finally:
        _PID_FILE.unlink(missing_ok=True)
        logger.info("Paper loop daemon stopped.")


# ---------------------------------------------------------------------------
# --once concurrency lock (the scheduled task fires every 2 min; a slow iteration
# must not overlap the next and double-process the paper DB)
# ---------------------------------------------------------------------------


def _acquire_once_lock():
    """The --once overlap guard. Two processes settling the same trades concurrently is P&L
    corruption, not a skipped tick, which is why PID liveness is the primary guard and the age
    check only a fallback for an unreadable lock file. Semantics live in
    `cherrypick.core.looplock`; `_pid_alive` is passed so tests monkeypatching it still apply."""
    return looplock.acquire(str(_LOCK_FILE), 180, alive=_pid_alive)


def _release_once_lock():
    looplock.release(_LOCK_FILE)


def _heartbeat_lock():
    """Touch the --once lock file's mtime so a long-running iteration doesn't read as stale to
    the age-based fallback above. Best-effort and non-fatal -- PID liveness is the real guard;
    this only matters if the PID itself becomes unreadable mid-run, which should not happen but
    costs nothing to also protect against. Call periodically from inside a long loop (per symbol,
    per settlement batch), not just once."""
    try:
        os.utime(_LOCK_FILE, None)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Unattended running is the orchestrator supervisor's job: it runs `--once` on the configured
# cadence, restarts on death, and works on every OS. The module's own Windows scheduled-task
# launcher (`--install-task`) was removed on 2026-10-08; `--start` remains for a terminal session.
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="MEICAgent paper-trading loop daemon")
    parser.add_argument(
        "--start",
        action="store_true",
        help="Spawn a long-running detached daemon in the background (alternative "
        "to the supervisor; less robust on Windows)",
    )
    parser.add_argument("--status", action="store_true", help="Print daemon status and exit")
    parser.add_argument("--stop", action="store_true", help="Stop a running --start daemon")
    parser.add_argument("--once", action="store_true", help="Run a single iteration and exit")
    parser.add_argument(
        "--force", action="store_true", help="With --once, run even outside market hours (for testing)"
    )
    parser.add_argument(
        "--settle-overdue",
        action="store_true",
        help="Settle cash-settled sessions still open past their settlement, at the official close "
        "only, then exit (the tick also does this outside its window)",
    )
    parser.add_argument("--date", default=None, help="A session day (YYYY-MM-DD); default today")
    parser.add_argument("--_run", action="store_true", help=argparse.SUPPRESS)  # internal: the detached child
    args = parser.parse_args()

    if args.status:
        _cmd_status()
        return
    if args.stop:
        _cmd_stop()
        return
    if args.start:
        _spawn_detached()
        return
    if args.settle_overdue:
        _setup_logging(console=True)
        # Under the tick's own lock: two processes settling the same rows is P&L corruption.
        if not _acquire_once_lock():
            _emit({"ok": False, "error": "a paper tick is running; try again in a minute"})
            return
        try:
            _ensure_paper_schema()
            _emit(settle_overdue(_load_config()))
        finally:
            _release_once_lock()
        return

    if args.once:
        _setup_logging(console=True)
        if not _acquire_once_lock():
            _emit({"ok": True, "skipped": "another --once is already running"})
            return
        try:
            _ensure_paper_schema()
            summary = run_iteration(_load_config(), force=args.force)
        finally:
            _release_once_lock()
        _emit({"ok": True, "summary": summary})
        return

    # Bare invocation (or the internal --_run child): run the loop in this process. Foreground
    # in a terminal, or the detached child spawned by --start.
    existing = _running_pid()
    if existing is not None:
        _emit(
            {
                "ok": False,
                "error": f"Paper loop already running (pid {existing}). "
                f"Run 'python src/paper_loop.py --stop' first.",
            }
        )
        raise SystemExit(1)
    _setup_logging(console=False)  # daemon: file-only, no fragile stdout dependency
    _loop()


if __name__ == "__main__":
    main()
