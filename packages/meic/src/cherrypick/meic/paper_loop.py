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

try:  # stdlib zoneinfo first (tzdata supplies the db on Windows); pytz only as fallback
    from zoneinfo import ZoneInfo

    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - only where zoneinfo has no tz database
    import pytz

    _ET = pytz.timezone("America/New_York")


from cherrypick.core import advice as _core_advice  # bounded-advice validator
from cherrypick.core import calendar as _cal  # shared NYSE trading-day calendar
from cherrypick.core import home as _core_home  # the shared state dir
from cherrypick.core import logs as _logs
from cherrypick.core import viz as _viz  # the suite's one money formatter

from cherrypick.meic import analytics as _an
from cherrypick.meic import (
    paper,
    regime,  # market-dimension tagging for the per-iteration (uncensored) regime row
    stream_request,  # declares symbols + open paper legs to the streamer
)
from cherrypick.meic import paths as _paths  # ~/.cherrypick/data/meic or MEIC_DATA_DIR
from cherrypick.meic import stop_policies as _sp


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
_TASK_NAME = "cherrypick-meic-paper-loop"
_PAPER_DB = str(_paths.paper_db_path())
# `-m` rather than a path to the script: independent of this file's depth on disk and of the child's
# working directory, which matters because the scheduled task runs from wherever schtasks starts it.
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


def _load_config():
    with open(_paths.config_path()) as f:
        return json.load(f)


def _symbols(cfg):
    if cfg.get("symbols"):
        return [str(s).strip().upper() for s in cfg["symbols"] if str(s).strip()]
    if cfg.get("symbol"):
        return [str(cfg["symbol"]).strip().upper()]
    return ["XSP"]


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


def _build_candidates(symbol, last, widths, delta_targets, default_delta, today):
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
    calls, puts = [], []
    for e in entries:
        strike = e.get("strike_price") or e.get("strike")
        if strike is None or e.get("bid") is None or e.get("ask") is None:
            continue
        ot = str(e.get("option_type", "")).lower()
        rec = {
            "strike": float(strike),
            "streamer_symbol": e.get("streamer_symbol"),
            "delta": e.get("delta"),
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
    if not candidates:
        return [], leg_quotes, "no short strike near delta"
    return candidates, leg_quotes, None


def _open_count():
    d = _run_json(_DB + ["get_open_trades"])
    return len(d.get("open_trades", [])) if d.get("ok") else 0


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


def _eod_report_path(day):
    return _LOG_FILE.parent / f"paper-eod-{day}.md"


def _money(x):
    # The suite's one formatter (cherrypick.core.viz), keeping this report's "-" placeholder.
    return _viz.fmt_money(x, none="-")


def _write_eod_report(day):
    """Write a deterministic end-of-day paper report for `day` to logs/paper-eod-<day>.md.
    Code-generated (no agent) so it runs unattended from the daemon's settlement pass. Uses
    db.py get_range_summary for the tested per-profile metrics, plus a direct read for the
    exit-reason breakdown and per-symbol P&L. Returns the path written."""
    summ = _run_json(_DB + ["get_range_summary", "--start", day, "--end", day])
    profiles = summ.get("profiles", {}) if summ.get("ok") else {}

    exits, by_symbol, prof_symbols = {}, {}, {}
    entries = open_n = 0
    net_total = 0.0
    try:
        con = sqlite3.connect(_PAPER_DB)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT symbol, risk_profile, status, exit_reason, pnl, fees FROM ic_trades WHERE trade_date=?",
            (day,),
        ).fetchall()
        con.close()
    except sqlite3.Error:
        rows = []
    for r in rows:
        st = r["status"]
        if st == "cancelled":
            continue
        entries += 1
        net = (r["pnl"] or 0) - (r["fees"] or 0)
        net_total += net
        by_symbol[r["symbol"]] = by_symbol.get(r["symbol"], 0.0) + net
        prof_symbols.setdefault(r["risk_profile"], set()).add(r["symbol"])
        if st in ("open", "partial", "pending", "partial_entry"):
            open_n += 1
            reason = "(still open)"
        else:
            reason = r["exit_reason"] or st
        exits[reason] = exits.get(reason, 0) + 1

    L = [f"# Paper Trading - EOD Report {day}", ""]
    L.append(
        "_Deterministic parallel-shadow engine. MEIC has no profit target - exits are "
        "per-side stops, non-cash-settled time force-close, or cash-settled "
        "expiration-settlement._"
    )
    L.append("")
    L.append("## Account-wide (all profiles)")
    L.append(f"- Entries filled: **{entries}**")
    L.append(f"- Net P&L (net of fees): **{_money(round(net_total, 2))}**")
    L.append(f"- Still open at report time: {open_n}")
    if by_symbol:
        L.append(
            "- By symbol: " + ", ".join(f"{s} {_money(round(v, 2))}" for s, v in sorted(by_symbol.items()))
        )
    L.append("")

    # Per-(profile × symbol) portfolios — the atomic unit the paper study runs on. Each pair is its
    # own book with its own max_concurrent_ics and daily-target budget, so nothing nets across
    # profiles OR symbols here; the sections below are roll-up lenses over these same trades.
    portfolios = summ.get("portfolios", {}) if summ.get("ok") else {}
    L.append("## Per portfolio (profile × symbol)")
    L.append(
        "_Each pair is a standalone book — these never net against one another. The per-profile "
        "and by-symbol views below are lenses over the same trades, not separate accounting._"
    )
    L.append(
        "| Profile | Symbol | Trades | Wins | Losses | Win % | Net P&L | Expectancy/IC | Profit Factor | Max DD |"
    )
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for _, s in sorted(portfolios.items(), key=lambda kv: -(kv[1].get("net_pnl") or 0)):
        wr = f"{s['win_rate_pct']:.0f}%" if s.get("win_rate_pct") is not None else "-"
        pf = f"{s['profit_factor']:.2f}" if s.get("profit_factor") is not None else "-"
        exp = _money(s.get("expectancy_per_trade")) if s.get("expectancy_per_trade") is not None else "-"
        L.append(
            f"| {s.get('profile', '-')} | {s.get('symbol', '-')} | {s.get('total_trades', 0)} | "
            f"{s.get('win_count', 0)} | {s.get('loss_count', 0)} | {wr} | {_money(s.get('net_pnl'))} | "
            f"{exp} | {pf} | {_money(s.get('max_drawdown'))} |"
        )
    if not portfolios:
        L.append("| _(none)_ | - | 0 | - | - | - | $0.00 | - | - | - |")
    L.append("")

    # Roster order: the canonical ladder first, then experiment/exploratory profiles, then any
    # tag present in the data but not the registry (e.g. 'unassigned'). Empty profiles are noted,
    # not tabled.
    try:
        ordered = paper.all_profile_names()
    except Exception:
        ordered = list(profiles.keys())
    for tag in profiles:
        if tag not in ordered:
            ordered.append(tag)

    L.append("## Per profile (roll-up across symbols)")
    L.append(
        "| Profile | Symbol | Trades | Wins | Losses | Win % | Net P&L | Expectancy/IC | Profit Factor | Max DD |"
    )
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    active_names = [n for n in ordered if profiles.get(n)]
    for name in active_names:
        s = profiles[name]
        syms = ",".join(sorted(prof_symbols.get(name, set()))) or "-"
        wr = f"{s['win_rate_pct']:.0f}%" if s.get("win_rate_pct") is not None else "-"
        pf = f"{s['profit_factor']:.2f}" if s.get("profit_factor") is not None else "-"
        exp = _money(s.get("expectancy_per_trade")) if s.get("expectancy_per_trade") is not None else "-"
        L.append(
            f"| {name} | {syms} | {s.get('total_trades', 0)} | {s.get('win_count', 0)} | "
            f"{s.get('loss_count', 0)} | {wr} | {_money(s.get('net_pnl'))} | {exp} | {pf} | "
            f"{_money(s.get('max_drawdown'))} |"
        )
    if not active_names:
        L.append("| _(none)_ | - | 0 | - | - | - | $0.00 | - | - | - |")
    idle = [n for n in ordered if not profiles.get(n)]
    if idle:
        L.append("")
        L.append(f"_No entries today: {', '.join(idle)}._")
    L.append("")

    L.append("## Exits by reason")
    if exits:
        L.append("| Reason | Count |")
        L.append("|---|---|")
        for reason, cnt in sorted(exits.items(), key=lambda kv: -kv[1]):
            L.append(f"| {reason} | {cnt} |")
    else:
        L.append("_No entries today - flat session._")
    L.append("")

    L.extend(_eod_supplement(day))

    L.append(
        f"_Generated {_now_et().strftime('%Y-%m-%d %H:%M:%S %Z')} · paper DB only; live account untouched._"
    )

    path = _eod_report_path(day)
    path.write_text("\n".join(L), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# EOD supplement -- arm scorecard (breakeven identity), gate ledger, stop-policy
# table, regime coverage, iteration duration/peak positions. Isolated in its own
# function (rather than threaded through _write_eod_report's existing tables) so
# it's independently testable and the diff against the pre-arms report stays legible.
# ---------------------------------------------------------------------------


def _eod_supplement(day) -> list[str]:
    try:
        conn = sqlite3.connect(_PAPER_DB)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return []
    try:
        return _eod_supplement_lines(conn, day)
    finally:
        conn.close()


def _eod_supplement_lines(conn, day) -> list[str]:
    L = []

    # --- Arm scorecard: the breakeven identity, per stream, read per-session rather than
    # waiting on a bootstrap. ---
    L.append("## Arm scorecard (breakeven identity)")
    L.append(
        "_P(clean OTM) - P(double stop) vs. the fees/credit bar - the health line for each "
        "stream. See docs/paper-experiments.md for the derivation._"
    )
    arms = _an.by_arm(conn, start=day, end=day)
    if arms:
        L.append(
            "| Arm | Trades | Sessions | Net P&L | Win % | Clean % | Double-stop % | "
            "Breakeven bar % | Margin % |"
        )
        L.append("|---|---|---|---|---|---|---|---|---|")
        for a in arms:
            bc = _an.breakeven_scorecard(conn, start=day, end=day, arm=a["arm"])
            wr = f"{a['win_rate'] * 100:.0f}%" if a.get("win_rate") is not None else "-"
            clean = f"{bc['clean_pct']:.1f}%" if bc.get("clean_pct") is not None else "-"
            double = f"{bc['double_stop_pct']:.1f}%" if bc.get("double_stop_pct") is not None else "-"
            bar = f"{bc['breakeven_bar_pct']:.1f}%" if bc.get("breakeven_bar_pct") is not None else "-"
            margin = f"{bc['margin_pct']:+.1f}%" if bc.get("margin_pct") is not None else "-"
            L.append(
                f"| {a['arm']} | {a['trades']} | {a['sessions']} | {_money(a['net_pnl'])} | {wr} | "
                f"{clean} | {double} | {bar} | {margin} |"
            )
    else:
        L.append("_No resolved trades today._")
    L.append("")

    # --- Gate ledger: per-stream block/fill/exit outcomes from the gate_block loop_log rows,
    # so a zero-entry stream states which gate held it back instead of a collapsed "N skip". ---
    L.append("## Gate ledger")
    blocks = _an.gate_blocks(conn, day)
    if blocks:
        L.append("| Stream | Outcomes |")
        L.append("|---|---|")
        for stream in sorted(blocks):
            parts = ", ".join(
                f"{label} x{count}" for label, count in sorted(blocks[stream].items(), key=lambda kv: -kv[1])
            )
            L.append(f"| {stream} | {parts} |")
    else:
        L.append("_No gate_block rows logged today (pre-Phase-1d data, or the loop didn't run)._")
    L.append("")

    # --- Stop-policy table: read-side derivations from `open`'s recorded paths, not separate
    # entry streams -- see stop_policies.py and analytics.stop_counterfactual. ---
    L.append("## Stop-policy table (derived from `open`)")
    L.append(
        "_Computed read-side from `open`'s recorded per-side paths, not separate entry "
        "streams; validated against `control`'s real 0.95x-net mechanism (see "
        "analytics.validate_stop_derivation)._"
    )
    open_today = _an.stats_for_period(conn, start=day, end=day, arm="open")
    if open_today.get("trades"):
        L.append(
            "| Policy | Derivable | Actual net (`open`) | Derived net | Delta | Put fires | Call fires |"
        )
        L.append("|---|---|---|---|---|---|---|")
        for name in _sp.POLICIES:
            if name == "control":
                continue
            sc = _an.stop_counterfactual(conn, name, start=day, end=day, arm="open")
            L.append(
                f"| {name} | {sc['derivable']}/{sc['trades']} | {_money(sc['actual_net_pnl'])} | "
                f"{_money(sc['derived_net_pnl'])} | {_money(sc['delta'])} | {sc['put_fired']} | "
                f"{sc['call_fired']} |"
            )
    else:
        L.append("_`open` entered no trades today - nothing to derive against._")
    L.append("")

    # --- Regime coverage: tagged/untagged/degenerate per dimension; a degenerate dimension's
    # P&L-by-bucket table is withheld since it reports no contrast, not no effect. ---
    L.append("## Regime coverage")
    cov = _an.regime_coverage(conn, start=day, end=day)
    if cov["resolved_trades"]:
        L.append("| Dimension | Tagged | Untagged | Coverage % | Sessions | Effective n | Degenerate |")
        L.append("|---|---|---|---|---|---|---|")
        non_degenerate, underpowered = [], []
        for dim, d in cov["dimensions"].items():
            cov_pct = f"{d['coverage_pct']:.0f}%" if d.get("coverage_pct") is not None else "-"
            eff = f"{d['effective_n']}" + (" (daily)" if d.get("daily_scale") else "")
            L.append(
                f"| {dim} | {d['tagged']} | {d['untagged']} | {cov_pct} | {d['sessions']} | {eff} "
                f"| {'yes' if d['degenerate'] else 'no'} |"
            )
            if d["tagged"] and not d["degenerate"]:
                non_degenerate.append(dim)
            if d.get("underpowered"):
                underpowered.append(dim)
        if underpowered:
            # Stated rather than left to be inferred from a row count. A trade count in the
            # thousands and a session count of two look identical in the table above unless the
            # sessions column is read, and the second is what bounds any threshold re-cut: rows
            # inside one session share that session's market. Distinct from `degenerate` on
            # purpose — that one says re-cut the float, this one says collect more sessions.
            L.append("")
            L.append(
                f"⚠️ **{', '.join(underpowered)}** rest on fewer than {_an.MIN_EFFECTIVE_N} sessions. "
                "Rows are not independent draws — a dimension marked `(daily)` above moves only "
                "between sessions, so its effective n IS the session count. Do not re-cut a "
                "threshold against a sample this size; the split below is a description of these "
                "days, not evidence about the strategy."
            )
        for dim in non_degenerate:
            rows = _an.by_regime(conn, dim, start=day, end=day)
            if not rows:
                continue
            L.append("")
            L.append(f"**{dim}** by bucket:")
            L.append("| Bucket | Trades | Net P&L | Win % |")
            L.append("|---|---|---|---|")
            for r in rows:
                wr = f"{r['win_rate'] * 100:.0f}%" if r.get("win_rate") is not None else "-"
                L.append(f"| {r['bucket']} | {r['trades']} | {_money(r['net_pnl'])} | {wr} |")
    else:
        L.append("_No resolved trades today._")
    L.append("")

    # --- Iteration duration & peak open positions, from Phase 0's loop_log instrumentation. ---
    L.append("## Iteration duration & peak open positions")
    stats = conn.execute(
        "SELECT COUNT(*) n, AVG(duration_ms) avg_ms, MAX(duration_ms) max_ms, "
        "MAX(open_trades_n) peak_open FROM loop_log WHERE loop_date = ? AND duration_ms IS NOT NULL",
        (day,),
    ).fetchone()
    if stats and stats["n"]:
        L.append(f"- Iterations timed: {stats['n']} · avg {stats['avg_ms']:.0f}ms · max {stats['max_ms']}ms")
        L.append(f"- Peak open positions (account-wide, any timed iteration): {stats['peak_open']}")
    else:
        L.append("_No timed iterations today (pre-instrumentation, or the loop didn't run)._")
    L.append("")

    return L


# ---------------------------------------------------------------------------
# EOD analysis report — conversational, 7-section, still fully deterministic
# ---------------------------------------------------------------------------


def _analysis_path(day):
    return _LOG_FILE.parent / f"eod-analysis-{day}.md"


def _read_market_context(day):
    """Return (today_row, prior_row) dicts from the paper DB's market_context table, or (None, None).
    prior_row is the most recent earlier day, used only for the VIX/underlying deltas."""
    try:
        con = sqlite3.connect(_PAPER_DB)
        con.row_factory = sqlite3.Row
        today = con.execute("SELECT * FROM market_context WHERE context_date=?", (day,)).fetchone()
        prior = con.execute(
            "SELECT * FROM market_context WHERE context_date < ? ORDER BY context_date DESC LIMIT 1", (day,)
        ).fetchone()
        con.close()
    except sqlite3.Error:
        return None, None
    return (dict(today) if today else None, dict(prior) if prior else None)


def _signed(x):
    return f"+{x:.2f}" if x is not None and x >= 0 else (f"{x:.2f}" if x is not None else "?")


def _loop_gate_tail(day):
    """(iteration_count, per-symbol gate line) from the day's loop log — what the entry gates decided
    per symbol, at the last iteration. This is the 'why entries did/didn't fire' the trade tables can't
    show (e.g. `SPX(ivr 0.43): all regime_gex_negative`), so a flat/gated day is explainable rather than
    a blank. Best-effort; (0, None) if the log is unavailable."""
    try:
        con = sqlite3.connect(_PAPER_DB)
        con.row_factory = sqlite3.Row
        n = con.execute(
            "SELECT COUNT(*) FROM loop_log WHERE loop_date=? AND action='paper_iteration'", (day,)
        ).fetchone()[0]
        r = con.execute(
            "SELECT reasoning FROM loop_log WHERE loop_date=? AND action='paper_iteration' "
            "ORDER BY loop_time DESC LIMIT 1",
            (day,),
        ).fetchone()
        con.close()
    except sqlite3.Error:
        return 0, None
    if not r or not r["reasoning"]:
        return n, None
    # Strip the leading "[HH:MM ET] VIX .. 1D-ratio .. delta .." prefix; keep the per-symbol gate part.
    import re

    m = re.search(r"[A-Z]{2,5}\(ivr", r["reasoning"])
    tail = r["reasoning"][m.start() :].strip() if m else r["reasoning"].strip()
    return n, tail


def _write_eod_analysis(day):
    """Write a conversational 7-section end-of-day analysis for `day` to logs/eod-analysis-<day>.md.
    Deterministic templated prose (no agent/LLM/network) so it runs unattended from the settlement
    pass, sitting alongside the terse paper-eod-<day>.md. Reads only the paper DB + the day's captured
    market_context, so its numbers reconcile with the paper report and the suite digest."""
    cfg = _load_config()
    cash_settled = {str(s).upper() for s in cfg.get("cash_settled_symbols", ["SPX", "XSP", "NDX", "RUT"])}

    try:
        con = sqlite3.connect(_PAPER_DB)
        con.row_factory = sqlite3.Row
        rows = [
            dict(r)
            for r in con.execute(
                "SELECT * FROM ic_trades WHERE trade_date=? ORDER BY entry_time", (day,)
            ).fetchall()
        ]
        legs = [
            dict(r)
            for r in con.execute(
                "SELECT l.side, l.exit_time, l.exit_reason, l.exit_price, l.pnl, l.ic_order_id "
                "FROM ic_spread_legs l JOIN ic_trades t ON l.ic_order_id=t.ic_order_id "
                "WHERE t.trade_date=? AND l.status='closed'",
                (day,),
            ).fetchall()
        ]
        con.close()
    except sqlite3.Error:
        rows, legs = [], []
    active = [r for r in rows if r.get("status") != "cancelled"]

    def _net(r):
        return (r.get("pnl") or 0.0) - (r.get("fees") or 0.0)

    nets = [_net(r) for r in active]
    gross = sum(r.get("pnl") or 0.0 for r in active)
    fees = sum(r.get("fees") or 0.0 for r in active)
    net_total = sum(nets)
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n <= 0]
    avg_win = sum(wins) / len(wins) if wins else None
    avg_loss = sum(losses) / len(losses) if losses else None
    open_n = sum(1 for r in active if r.get("status") in ("open", "partial", "pending", "partial_entry"))
    by_symbol = {}
    for r in active:
        by_symbol[r["symbol"]] = by_symbol.get(r["symbol"], 0.0) + _net(r)

    today_ctx, prior_ctx = _read_market_context(day)
    loop_iters, gate_tail = _loop_gate_tail(day)

    L = [f"# MEIC Paper - EOD Analysis {day}", ""]
    L.append(
        "_Plain-English read on the paper session. Auto-generated from the paper DB (no agent) - "
        "conversational, but rule-based, not a hand-written synthesis. MEIC has no profit target: "
        "exits are per-side stops, non-cash-settled time force-close, or cash-settled expiration._"
    )
    L.append("")

    # 1. Executive snapshot ----------------------------------------------------
    L.append("## 1. Executive snapshot")
    if not active:
        L.append(
            "Flat session - no ICs were filled today. A day with no trade is a decision, not a gap: "
            "the entry gates (IV-rank floor, credit floor, VIX/ATR/GEX regime, late-entry bias) held "
            "the book out."
        )
        if gate_tail:
            L.append(
                f"Across **{loop_iters}** loop iterations, the end-of-session gate by symbol was: "
                f"`{gate_tail}`. (e.g. `regime_gex_negative` = net GEX below the gamma flip, the "
                "trending regime where short condors are gated off; `skip`/IV-rank = below the "
                "premium floor.)"
            )
    else:
        best = max(by_symbol.items(), key=lambda kv: kv[1]) if by_symbol else None
        worst = min(by_symbol.items(), key=lambda kv: kv[1]) if by_symbol else None
        drag = (
            f" - fees were {_money(round(fees, 2))}, {(fees / gross * 100):.0f}% of the {_money(round(gross, 2))} gross"
            if gross > 0
            else f" - fees added {_money(round(fees, 2))} on top of a losing gross"
        )
        L.append(
            f"The book filled **{len(active)}** IC{'s' if len(active) != 1 else ''} today and closed the "
            f"session **{_money(round(net_total, 2))}** net of fees ({len(wins)} up, {len(losses)} down"
            f"{f', {open_n} still open at report time' if open_n else ''}){drag}."
        )
        line = "Average winner ran " + (_money(round(avg_win, 2)) if avg_win is not None else "-")
        line += ", average loser " + (_money(round(avg_loss, 2)) if avg_loss is not None else "-") + "."
        if best and worst and best[0] != worst[0]:
            line += f" {best[0]} carried the day ({_money(round(best[1], 2))}); {worst[0]} lagged ({_money(round(worst[1], 2))})."
        elif best:
            line += f" All of it came from {best[0]}."
        L.append(line)
    L.append("")

    # 2. Position-level detail -------------------------------------------------
    L.append("## 2. Position-level detail")
    L.append(
        "_Iron condors, 0DTE. Greeks are captured at entry; there is no end-of-day greek snapshot "
        "because the positions settle the same day._"
    )
    if active:
        L.append("")
        L.append(
            "| Symbol | Profile | Put / Call strikes | Width | Net credit | Short-call Δ | Call OTM | IV rank | Net P&L |"
        )
        L.append("|---|---|---|---|---|---|---|---|---|")
        for r in active:
            up = r.get("underlying_price_entry")
            call_otm = f"{((r['call_strike'] - up) / up * 100):.2f}%" if up and r.get("call_strike") else "-"
            cd = f"{r['call_delta_at_entry']:.2f}" if r.get("call_delta_at_entry") is not None else "-"
            ivr = f"{r['iv_rank_at_entry'] * 100:.0f}%" if r.get("iv_rank_at_entry") is not None else "-"
            strikes = f"{r.get('put_strike', '-')} / {r.get('call_strike', '-')}"
            L.append(
                f"| {r['symbol']} | {r.get('risk_profile', '-')} | {strikes} | "
                f"{r.get('wing_width', '-')} | {_money(r.get('net_credit'))} | {cd} | {call_otm} | "
                f"{ivr} | {_money(round(_net(r), 2))} |"
            )
    else:
        L.append("")
        L.append("_No open or closed positions to detail._")
    L.append("")

    # 3. Trade activity log ----------------------------------------------------
    L.append("## 3. Trade activity log")
    if active:
        L.append("**Entries** (credit collected, fees at open):")
        L.append("")
        L.append("| Entry time | Symbol | Profile | Net credit | Open fees |")
        L.append("|---|---|---|---|---|")
        for r in active:
            et = (r.get("entry_time") or "")[11:19] or (r.get("entry_time") or "-")
            L.append(
                f"| {et} | {r['symbol']} | {r.get('risk_profile', '-')} | "
                f"{_money(r.get('net_credit'))} | {_money(r.get('fees'))} |"
            )
        if legs:
            L.append("")
            L.append("**Side exits** (per-side stops / settlements):")
            L.append("")
            L.append("| Exit time | Side | Reason | Exit price | Side P&L |")
            L.append("|---|---|---|---|---|")
            for lg in sorted(legs, key=lambda x: x.get("exit_time") or ""):
                xt = (lg.get("exit_time") or "")[11:19] or (lg.get("exit_time") or "-")
                px = f"{lg['exit_price']:.2f}" if lg.get("exit_price") is not None else "-"
                L.append(
                    f"| {xt} | {lg.get('side', '-')} | {lg.get('exit_reason', '-')} | {px} | "
                    f"{_money(lg.get('pnl'))} |"
                )
    else:
        L.append("_No fills - nothing to log._")
    L.append("")

    # 4. Risk metrics ----------------------------------------------------------
    L.append("## 4. Risk metrics")
    if active:
        # Net position delta at entry: short legs contribute the negative of the option's delta.
        net_delta = 0.0
        have_delta = False
        for r in active:
            q = r.get("quantity") or 1
            parts = [
                (-1, r.get("call_delta_at_entry")),
                (-1, r.get("put_delta_at_entry")),
                (1, r.get("long_call_delta_at_entry")),
                (1, r.get("long_put_delta_at_entry")),
            ]
            for sign, d in parts:
                if d is not None:
                    net_delta += sign * d * q
                    have_delta = True
        max_loss = sum(
            ((r.get("wing_width") or 0) - (r.get("net_credit") or 0))
            * (r.get("dollar_multiplier") or 100)
            * (r.get("quantity") or 1)
            for r in active
        )
        max_gain = sum(
            (r.get("net_credit") or 0) * (r.get("dollar_multiplier") or 100) * (r.get("quantity") or 1)
            for r in active
        )
        delta_txt = (
            f"**{_signed(net_delta)}** (short legs negated; near zero = the book entered delta-balanced)"
            if have_delta
            else "not available (entry greeks missing)"
        )
        L.append(f"- Net position delta at entry: {delta_txt}.")
        L.append(
            f"- Defined risk on the book: max loss **{_money(round(max_loss, 2))}**, max credit "
            f"**{_money(round(max_gain, 2))}** (wing width minus credit, times the multiplier - "
            f"paper carries no NLV/buying-power basis, so defined risk stands in for margin used)."
        )
        conc = ", ".join(
            f"{s} {len([r for r in active if r['symbol'] == s])} IC(s), {_money(round(v, 2))}"
            for s, v in sorted(by_symbol.items())
        )
        L.append(f"- Concentration by underlying: {conc}.")
        if len(by_symbol) == 1:
            L.append(
                "  - Single-underlying day: all risk sat in one name (no cross-symbol diversification, "
                "but also no correlated double-up)."
            )
    else:
        L.append("- No open risk - the book is flat.")
    L.append("")

    # 5. Market context --------------------------------------------------------
    L.append("## 5. Market context")
    if today_ctx and today_ctx.get("vix") is not None:
        vix = today_ctx["vix"]
        dv = (
            f" ({_signed(vix - prior_ctx['vix'])} vs the prior session)"
            if (prior_ctx and prior_ctx.get("vix") is not None)
            else ""
        )
        line = f"VIX sat around **{vix:.1f}**{dv}."
        if today_ctx.get("vix1d_ratio") is not None:
            line += f" VIX1D/VIX ratio {today_ctx['vix1d_ratio']:.2f} (>1.30 flags an event-day regime)."
        L.append(line)
        try:
            syms = json.loads(today_ctx.get("symbols_json") or "{}")
            prior_syms = json.loads(prior_ctx.get("symbols_json") or "{}") if prior_ctx else {}
        except (TypeError, ValueError):
            syms, prior_syms = {}, {}
        for s, info in sorted(syms.items()):
            px = info.get("price")
            ivr = info.get("iv_rank")
            move = ""
            if px is not None and prior_syms.get(s, {}).get("price"):
                pv = prior_syms[s]["price"]
                move = f", {((px - pv) / pv * 100):+.2f}% vs prior close" if pv else ""
            ivr_txt = f"IV rank {ivr * 100:.0f}%" if ivr is not None else "IV rank -"
            L.append(f"- {s}: last ~{px}{move}; {ivr_txt}.")
    else:
        L.append(
            "No market-context snapshot was captured for this session (the loop records VIX / VIX1D / "
            "per-symbol IV rank each iteration - none landed, e.g. a backfilled or pre-capture day)."
        )
    try:
        d = datetime.strptime(day, "%Y-%m-%d").date()
        catalysts = []
        if _cal.is_fomc_day(d):
            catalysts.append("FOMC decision day")
        if _cal.is_triple_witching(d):
            catalysts.append("triple-witching expiry")
        elif _cal.is_quarterly_expiry(d):
            catalysts.append("quarterly expiry")
        if catalysts:
            L.append(
                f"- Calendar catalyst: {', '.join(catalysts)} - the loop applies its blackout / "
                "tightened-gate rules on these dates."
            )
    except ValueError:
        pass
    if gate_tail:
        L.append(
            f"- Entry gates at the last of **{loop_iters}** iterations: `{gate_tail}` "
            "(what the regime/IV/GEX gates decided per symbol - the reason entries did or didn't fire)."
        )
    L.append("")

    # 6. Tax / accounting notes ------------------------------------------------
    L.append("## 6. Tax / accounting notes")
    L.append("_Informational only - not tax advice. Paper book, so nothing here is a real taxable event._")
    if active:
        syms = sorted({r["symbol"] for r in active})
        s1256 = [s for s in syms if s in cash_settled]
        equity = [s for s in syms if s not in cash_settled]
        if s1256:
            L.append(
                f"- **Section 1256** (broad-based cash-settled index options): {', '.join(s1256)}. "
                "These mark-to-market at 60% long-term / 40% short-term regardless of holding period "
                "and are exempt from the wash-sale rule."
            )
        if equity:
            L.append(
                f"- **Equity-option treatment** (not 1256): {', '.join(equity)}. Ordinary "
                "short-term/long-term rules apply and wash-sale can bite on repeated same-name losses."
            )
        L.append(
            "- Holding period: every position is 0DTE (opened and settled same day) - short-term / "
            "intraday across the board."
        )
    else:
        L.append("- No positions - no lots to classify.")
    L.append("")

    # 7. Notes / journal -------------------------------------------------------
    L.append("## 7. Notes / journal")
    if not active:
        L.append(
            "- Quiet by design. Worth a glance at the loop log to confirm the gates that fired match "
            "the day's conditions (a flat day and a silently-broken entry check look identical in P&L)."
        )
    else:
        # Which exit reasons dominated, and on which side.
        exit_counts = {}
        side_counts = {"put": 0, "call": 0}
        for lg in legs:
            exit_counts[lg.get("exit_reason") or "?"] = exit_counts.get(lg.get("exit_reason") or "?", 0) + 1
            if lg.get("side") in side_counts:
                side_counts[lg["side"]] += 1
        if exit_counts:
            top = max(exit_counts.items(), key=lambda kv: kv[1])
            L.append(
                f"- Exits were dominated by **{top[0]}** ({top[1]} of {sum(exit_counts.values())} side "
                f"exits). Put-side stops: {side_counts['put']}, call-side: {side_counts['call']}."
            )
            if side_counts["call"] > side_counts["put"] and side_counts["call"] >= 2:
                L.append(
                    "  - Call side did most of the stopping - consistent with an up-drift pressing the "
                    "short calls. Wider call OTM (or the VIX-band delta scale) is the lever if this repeats."
                )
            elif side_counts["put"] > side_counts["call"] and side_counts["put"] >= 2:
                L.append("  - Put side did most of the stopping - a down-move pressed the short puts.")
        else:
            L.append(
                "- No side stops fired - the ICs rode to settlement (the intended path for cash-settled "
                "names with an OTM close)."
            )
        if gross > 0 and fees / gross > 0.30:
            L.append(
                f"- **Recommendation:** fees ate {(fees / gross * 100):.0f}% of gross - lean toward wider "
                "widths / fewer narrow entries, where the fixed per-contract fee is a smaller drag."
            )
        if avg_loss is not None and avg_win is not None and abs(avg_loss) > 2 * avg_win:
            L.append(
                "- **Recommendation:** average loser is more than 2x the average winner - the stop is "
                "letting losses run relative to the credit; revisit stop_trigger_ratio for these profiles."
            )
        if net_total < 0 and not wins:
            L.append(
                "- Every fill lost today. One session is noise, but if the pattern holds, check whether "
                "entries are clearing the fee-adjusted credit floor with real margin."
            )
    L.append("")
    L.append(
        f"_Generated {_now_et().strftime('%Y-%m-%d %H:%M:%S %Z')} · paper DB only; live account "
        "untouched. Companion to paper-eod-" + day + ".md._"
    )

    path = _analysis_path(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L), encoding="utf-8")
    return path


def _open_advised_tags():
    """DISTINCT advised:* profile tags with open/partial rows -- positions that must keep being
    managed (exits, force-close, settlement) even when today's advice is absent or disabled."""
    import sqlite3

    try:
        conn = sqlite3.connect(_PAPER_DB)
        rows = conn.execute(
            "SELECT DISTINCT risk_profile FROM ic_trades "
            "WHERE risk_profile LIKE 'advised:%' AND status IN ('open', 'partial')"
        ).fetchall()
        conn.close()
        return [r[0] for r in rows]
    except sqlite3.Error:
        return []


def _advice_profiles(cfg, today):
    """The advised shadow book's synthetic profile(s) for `today`: (profiles dict, reason).

    Tier 1 of the agentic layer, loop side. The orchestrator's `advise` command wrote (or didn't
    write) `state/advice/meic-<today>.json`; this re-validates it with the SAME core code
    (cherrypick.core.advice) against this module's own config `advice.bounds` manifest --
    absent, stale, or invalid means baseline, i.e. no synthetic profile at all.

    Read-once-per-session across --once processes: the first iteration of the day records its
    decision in advice_active.json in the data home, and every later iteration replays that
    decision -- so advice can never start, stop, or change mid-session, however late an
    artifact lands or however the config is flipped intraday.

    The advised book is a synthetic profile `advised:<base>`: the base profile's registry def
    with the admitted params overlaid, evaluated by process_symbol beside the un-advised base
    (the control) -- the flies-arms pattern, and compare_profiles reads the tag for free.
    Open advised positions always keep a profile to run their exits: if advice is off today, a
    management-only twin (entries capped to zero) stands in.
    """
    acfg = cfg.get("advice") or {}
    base = acfg.get("base_profile", "control")
    state_file = _paths.data_path("advice_active.json")
    decision = None
    if state_file.exists():
        try:
            decision = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            decision = None
        if decision is not None and decision.get("day") != today:
            decision = None  # yesterday's decision; today re-derives its own
    if decision is None:
        if acfg.get("enabled") and acfg.get("bounds"):
            res = _core_advice.load(_core_home.state_dir(), "meic", today, acfg.get("bounds") or {})
            params = {p["param"]: p["value"] for p in res["proposals"]} if res["proposals"] else None
            decision = {
                "day": today,
                "base_profile": base,
                "params": params,
                "reason": res["reason"],
                "proposals": res["proposals"],
                "rejected": res.get("rejected") or [],
            }
            for prop in res["proposals"]:
                logger.info(
                    "advice applied: %s=%r -- %s", prop["param"], prop["value"], prop.get("rationale", "")
                )
            if not res["proposals"]:
                logger.info("advice: baseline (%s)", res["reason"] or "no proposals")
        else:
            decision = {"day": today, "base_profile": base, "params": None, "reason": "advice_disabled"}
        try:
            state_file.write_text(json.dumps(decision, indent=2), encoding="utf-8")
        except OSError:
            pass  # the decision still applies this process; the next --once re-derives it

    registry = paper.load_profiles()
    out = {}
    dbase = decision.get("base_profile") or base
    base_def = registry.get(dbase)
    if decision.get("params") and isinstance(base_def, dict):
        out[f"advised:{dbase}"] = {**base_def, **decision["params"]}
    for tag in _open_advised_tags():
        if tag in out:
            continue
        tag_base = registry.get(tag.split(":", 1)[1]) if ":" in tag else None
        if isinstance(tag_base, dict):
            out[tag] = {**tag_base, "max_concurrent_ics": 0}
    return out, decision.get("reason")


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


def run_iteration(cfg, force=False):
    # Wall-clock for this whole iteration, logged below alongside open-position count. Was
    # nowhere -- loop_log.duration_ms existed as a column and get_step_timing could read it, but
    # nothing here ever wrote one, so the load ceiling that governs how many streams/positions
    # this loop can carry was invisible until it was already missing ticks. See docs/paper-
    # experiments.md for the incident this closes the gap on.
    _iter_t0 = time.time()
    now = _now_et()
    if not force and not _is_trading_time(now, cfg):
        logger.info("outside trading window (%s ET) - skipping.", now.strftime("%H:%M"))
        return {"skipped": "outside_trading_window"}
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
    advice_profiles, _advice_reason = _advice_profiles(cfg, today)
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
        widths = paper.union_widths_for_symbol(symbol, cfg, profiles)
        extra_deltas = paper.union_short_deltas_for_symbol(symbol, cfg, profiles)
        delta_targets = [delta_target] + [d for d in extra_deltas if abs(d - delta_target) > 1e-9]
        candidates, leg_quotes, cand_err = _build_candidates(
            symbol, price, widths, delta_targets, delta_target, today
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

    # Once-per-day EOD report, written on the first pass at/after the settlement time (16:00),
    # i.e. after positions have settled. The file-exists guard makes it fire exactly once even
    # though the daemon keeps ticking through the 16:00–16:05 settlement window.
    sett = cfg.get("expiration_settlement_time", "16:00")
    if (now.hour * 60 + now.minute) >= paper._time_to_minutes(sett) and not _eod_report_path(today).exists():
        try:
            p = _write_eod_report(today)
            logger.info("wrote EOD report: %s", p)
        except Exception as exc:
            logger.warning("EOD report failed: %s", exc)
        # Companion conversational analysis, written the same once-per-day pass (guarded on its own file).
        try:
            pa = _write_eod_analysis(today)
            logger.info("wrote EOD analysis: %s", pa)
        except Exception as exc:
            logger.warning("EOD analysis failed: %s", exc)
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
    return 9 * 60 + 30 <= mins < 16 * 60 + 5


def _sleep_seconds(now, cfg, open_positions):
    if not _is_trading_time(now, cfg):
        return 600  # idle outside market hours; daemon stays up until --stop
    return 120 if open_positions > 0 else 300


# ---------------------------------------------------------------------------
# Daemon control (mirrors src/streamer.py)
# ---------------------------------------------------------------------------


def _pid_alive(pid: int) -> bool:
    try:
        import psutil

        return psutil.pid_exists(pid)
    except ImportError:
        try:
            os.kill(pid, 0)
            return True
        except PermissionError:
            return True
        except (OSError, SystemError):
            return False


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


def _task_installed():
    if os.name != "nt":
        return False
    r = subprocess.run(
        ["schtasks", "/Query", "/TN", _TASK_NAME], capture_output=True, text=True, creationflags=_NO_WINDOW
    )
    return r.returncode == 0


def _cmd_status():
    pid = _running_pid()
    info = {
        "daemon_running": pid is not None,  # a long-running --start daemon (if used)
        "pid": pid,
        "scheduled_task": _task_installed(),  # the recommended --install-task automation
        "open_positions": _open_count(),
    }
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
                logger.info("outside trading window (%s ET) - idling.", now.strftime("%H:%M"))
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
    try:
        fd = os.open(str(_LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        # A held-but-alive lock is NEVER stolen, regardless of age. Age-only staleness let a
        # still-running-but-slow iteration (chiefly the 16:00 settlement pass, which can touch
        # hundreds of open positions) get its lock stolen by the next --once at minute 3, so both
        # processes settled the same trades concurrently -- P&L corruption, not a skipped tick.
        # PID liveness is the primary guard; the age check below is only a fallback for a lock
        # file whose PID can't be read (corrupt/truncated write), never a substitute for it.
        try:
            holder_pid = int(_LOCK_FILE.read_text().strip())
        except (OSError, ValueError):
            holder_pid = None
        if holder_pid is not None and _pid_alive(holder_pid):
            return False
        try:
            if holder_pid is not None or time.time() - os.path.getmtime(_LOCK_FILE) > 180:
                os.unlink(_LOCK_FILE)
                return _acquire_once_lock()
        except OSError:
            pass
        return False


def _release_once_lock():
    try:
        os.unlink(_LOCK_FILE)
    except OSError:
        pass


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
# Scheduled-task automation (the robust unattended launcher on Windows)
# ---------------------------------------------------------------------------
# A long-running detached daemon proved fragile on Windows (stray console events / abrupt
# death of a child-spawning process). Instead the automation registers a Task Scheduler job
# that runs `--once` every 2 minutes - each run is a short-lived process that reliably
# completes, self-heals if one fails, no-ops outside market hours (time-gated), and persists
# across sessions independent of any launching shell.


def _install_task():
    if os.name != "nt":
        _emit(
            {
                "ok": False,
                "error": "scheduled-task install is Windows-only; on "
                "other OSes run `python src/paper_loop.py` in a terminal or via cron",
            }
        )
        return
    # pythonw.exe = no console window, so the every-2-min --once run is truly headless.
    # `-m` rather than an absolute script path: the registered task string no longer bakes in this
    # file's location, so moving/reinstalling the package doesn't strand the task pointing at a path
    # that no longer exists. Requires cherrypick-meic to be installed in this interpreter, which is
    # the documented setup (scripts/dev-install).
    tr = f'"{_pythonw()}" -m cherrypick.meic.paper_loop --once'
    r = subprocess.run(
        ["schtasks", "/Create", "/TN", _TASK_NAME, "/TR", tr, "/SC", "MINUTE", "/MO", "2", "/F", "/IT"],
        capture_output=True,
        text=True,
        creationflags=_NO_WINDOW,
    )
    ok = r.returncode == 0
    # Fire one run immediately so positions are managed without waiting for the first tick.
    if ok:
        subprocess.run(
            ["schtasks", "/Run", "/TN", _TASK_NAME], capture_output=True, text=True, creationflags=_NO_WINDOW
        )
    _emit({"ok": ok, "task": _TASK_NAME, "cadence": "every 2 min", "detail": (r.stdout or r.stderr).strip()})


def _uninstall_task():
    if os.name != "nt":
        _emit({"ok": False, "error": "Windows-only"})
        return
    subprocess.run(
        ["schtasks", "/End", "/TN", _TASK_NAME], capture_output=True, text=True, creationflags=_NO_WINDOW
    )
    r = subprocess.run(
        ["schtasks", "/Delete", "/TN", _TASK_NAME, "/F"],
        capture_output=True,
        text=True,
        creationflags=_NO_WINDOW,
    )
    _emit({"ok": r.returncode == 0, "detail": (r.stdout or r.stderr).strip()})


def main():
    parser = argparse.ArgumentParser(description="MEICAgent paper-trading loop daemon")
    parser.add_argument(
        "--install-task",
        action="store_true",
        help="Register a Windows scheduled task that runs --once every 2 min "
        "(the recommended unattended launcher) and fire one run now",
    )
    parser.add_argument(
        "--uninstall-task",
        action="store_true",
        help="Remove the scheduled task (stops the unattended paper session)",
    )
    parser.add_argument(
        "--start",
        action="store_true",
        help="Spawn a long-running detached daemon in the background (alternative "
        "to the scheduled task; less robust on Windows)",
    )
    parser.add_argument("--status", action="store_true", help="Print daemon/task status and exit")
    parser.add_argument("--stop", action="store_true", help="Stop a running --start daemon")
    parser.add_argument("--once", action="store_true", help="Run a single iteration and exit")
    parser.add_argument(
        "--force", action="store_true", help="With --once, run even outside market hours (for testing)"
    )
    parser.add_argument(
        "--eod-report",
        action="store_true",
        help="Write the deterministic paper report AND the conversational analysis now (regenerates both)",
    )
    parser.add_argument(
        "--eod-analysis",
        action="store_true",
        help="Write only the conversational 7-section EOD analysis now (regenerates)",
    )
    parser.add_argument(
        "--date", default=None, help="With --eod-report/--eod-analysis, the day (YYYY-MM-DD); default today"
    )
    parser.add_argument("--_run", action="store_true", help=argparse.SUPPRESS)  # internal: the detached child
    args = parser.parse_args()

    if args.status:
        _cmd_status()
        return
    if args.eod_report:
        day = args.date or _now_et().strftime("%Y-%m-%d")
        path = _write_eod_report(day)
        analysis = _write_eod_analysis(day)
        _emit({"ok": True, "report": str(path), "analysis": str(analysis)})
        return
    if args.eod_analysis:
        day = args.date or _now_et().strftime("%Y-%m-%d")
        analysis = _write_eod_analysis(day)
        _emit({"ok": True, "analysis": str(analysis)})
        return
    if args.stop:
        _cmd_stop()
        return
    if args.install_task:
        _install_task()
        return
    if args.uninstall_task:
        _uninstall_task()
        return
    if args.start:
        _spawn_detached()
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
