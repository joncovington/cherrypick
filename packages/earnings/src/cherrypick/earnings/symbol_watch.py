"""Forward-looking earnings preview scan -- the source of the console's read-only Earnings
page "Upcoming" section. Unlike rank_strategies' daily entry scan (today/tomorrow's actionable
candidates, hard-filtered against strategy-specific accept/reject thresholds), this walks the
next `--days` **trading days** (`cherrypick.core.calendar.nth_trading_day` -- weekends/holidays
never silently shrink the window) of Dolt's earnings_calendar, pre-filtered to a liquid-enough
universe (see below), and records an honest metric vector for each survivor -- there is no
accept/reject decision here, only a `tier` badge (see classify_tier) ranking what's already
in. The universe filter (`symbol_watch.liquid_only`, config default True): the union of
tastytrade's "Liquid Symbols", "High Options Volume", and "tasty Earnings" public watchlists
(`scanner.fetch_watch_universe`) -- scout's own Upcoming section only ever displays rows that
made it into this scan, so computing a full metric vector for a name outside that union would
just be a wasted broker round trip, never seen.

Written to a JSON snapshot (symbol_watch.json in the earnings data home -- see paths.py) rather
than a SQLite table: the result is always a small, fixed-shape, whole-collection document (one
entry per symbol currently in the `--days`-wide window, replaced wholesale pass over pass, no
history kept) -- exactly what a single JSON document fits, with no query/join/incremental-update
need a database would earn its complexity for. Write is atomic (temp file + os.replace in the
same directory) so a reader (scout, running as a separate process) never sees a partial file
mid-write. Progress (pass_started_at/total/done/pass_completed_at) is written into the same
document after every symbol, not just at the end, so scout's Upcoming page can render a real
"N of M done" indicator during a multi-minute pass instead of a bare spinner -- and each pass
starts from the PREVIOUS pass's symbols still in scope this pass (merged, not replaced), so a
page load mid-pass still sees every not-yet-reached symbol's last-known-good reading rather than
it appearing to vanish. A symbol that fell OUT of scope (aged past the `--days` window, or out of
the universe filter) is pruned at the start of the new pass, not carried forward forever -- no
future pass would ever refresh it, so keeping it around would only accumulate stale debris no
scout row can honestly attach to (see `_merge_symbol_watch`'s date-matching in
earnings_metrics_service.py, which would eventually stop matching it anyway once its recorded
earnings_date rolls out of the calendar's own display window -- pruning here just does that
immediately instead of waiting on that coincidence).

Reused from scanner.py without duplication: fetch_dolthub_calendar_range (broad Dolt coverage,
every symbol reporting in the window, not just the caller's own watchlist), fetch_watch_universe
(the union of tastytrade watchlists this scan's universe filter reads, one call per pass, not per
symbol), fetch_quote_and_expirations / select_front_expiration / select_back_expiration /
fetch_front_back_atm_entries / compute_expected_move_and_term_structure (the same chain-based
expected-move/term-structure machinery every strategy's entry scan already uses),
fetch_liquidity_criteria (spread/market-cap/OI/volume/IV-rank/IV-percentile),
fetch_avg_volume / fetch_iv_rv_ratio (Dolt-only, cheap), compute_winrate /
move_stats_from_quarters (historical move stats), and apply_common_signals (the same
tastytrade-preferred-over-Dolt IV/RV selection and move-stats merge every entry_reviews row
already uses, so a symbol's numbers here won't disagree with what its actual entry scan records
once it reaches the entry window). Each symbol costs one broker chain round trip
(~1s, dominated by fetch_quote_and_expirations' full-chain fetch) -- exactly the per-symbol cost
the calendar redesign moved OUT of scout's own request path (see scout's calendar_service.py
docstring) and into this scheduled, off-request-path scan. Scanned sequentially, not via a
thread pool: this runs on a recurring schedule (minutes to run is fine), and staying single-
threaded avoids any coordination around the shared progress file or scanner.py's per-symbol
thread-local caches, which are designed for the daily entry scan's own worker pool, not this one.

`classify_tier` (EarningsEdgeDetection-derived, github.com/Jayesh-Chhabra/EarningsEdgeDetection)
scores each finished entry into "recommended" / "near_miss" / "fail" (or `None` when a
chain-dependent input never resolved) -- a display ranking only, computed after the fact, never
gating which symbols get scanned or written.

Usage: python -m cherrypick.earnings.symbol_watch refresh [--days 10]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import tempfile
import time
from datetime import date as _date

from cherrypick.core import calendar as _calendar

from cherrypick.earnings import paths as _paths
from cherrypick.earnings import scanner as _scanner

_SNAPSHOT_NAME = "symbol_watch.json"
_DEFAULT_DAYS = 10  # trading days, not calendar days -- see refresh_symbol_watch
_DEFAULT_BACK_MONTH_MIN_DAYS_AFTER = 21

# EarningsEdgeDetection-derived screener tier bar (github.com/Jayesh-Chhabra/
# EarningsEdgeDetection) -- a display signal only, never a scan/gate decision (see
# classify_tier's own docstring). Deliberately a flat, standalone threshold set rather than
# reusing strategy_defaults/strategies.* (scanner._load_config's per-strategy merge) -- those
# are a strategy's actual accept/reject bar and can legitimately differ per strategy; this badge
# is one bar for the whole preview, closer to EED's own single scanner than to any one strategy.
_TIER_DEFAULTS = {
    "min_price": 10.0,
    "near_miss_min_price": 5.0,
    "min_iv_rv_ratio": 1.25,
    "near_miss_min_iv_rv_ratio": 1.00,
    "min_winrate": 0.50,
    "near_miss_min_winrate": 0.40,
    "min_avg_volume": 1_500_000,
    "near_miss_min_avg_volume": 1_000_000,
    "min_term_structure": -0.004,
    "min_expected_move_dollars": 0.90,
    "min_combined_open_interest": 2000,
}


def _tier_thresholds(config: dict) -> dict:
    overrides = (config.get("symbol_watch") or {}).get("tier_thresholds") or {}
    return {**_TIER_DEFAULTS, **overrides}


# Criteria the entry scan may pre-filter on, hours after this snapshot was taken.
#
# Deliberately NOT the tier badge, and deliberately not every criterion measured here. A name is only
# dropped on something that cannot meaningfully move between the pre-market scan and the afternoon
# entry window:
#
#   winrate      12 quarters of history; changes once a quarter, not once a day
#   avg_volume   a daily figure off the stocks dataset
#   market_cap   moves with price, but nowhere near fast enough to cross a $1B floor intraday
#
# `iv_rv_ratio` is excluded on purpose even though it is Dolt-derived and cheap: implied vol RISES
# into an announcement, so a name below the floor in the morning can legitimately clear it by 15:35,
# and pre-filtering on it would drop exactly the candidates the strategy exists to find. Price, term
# structure, expected move and open interest are excluded for the same reason, more obviously.
_STABLE_PREFILTER = ("winrate", "avg_volume", "market_cap")


def stable_prefilter_verdict(entry: dict, config: dict) -> tuple[bool, str | None]:
    """`(drop, reason)` — whether this morning's row disqualifies a symbol on stable criteria alone.

    Measured against the **near-miss** floor, the loosest bar any `symbol_screen` setting can ask
    for, so this only ever drops a name that could not pass under any configuration. A missing value
    never drops anything: "couldn't determine" is not "known bad", the same posture `classify_tier`
    takes.

    This narrows what the entry scan has to price; it never decides an entry. Every survivor is
    re-screened live, so no value from here reaches an accept/reject decision.
    """
    thresholds = _tier_thresholds(config)
    floors = {
        "winrate": thresholds["near_miss_min_winrate"],
        "avg_volume": thresholds["near_miss_min_avg_volume"],
        "market_cap": (config.get("near_miss_min_market_cap") or 0),
    }
    for key in _STABLE_PREFILTER:
        value, floor = entry.get(key), floors.get(key)
        if value is None or not floor:
            continue
        try:
            if float(value) < float(floor):
                return True, f"{key} {value} below near-miss floor {floor}"
        except (TypeError, ValueError):
            continue
    return False, None


def prefilter_symbols(symbols, config: dict, *, session: str | None = None) -> tuple[list, dict]:
    """Split `symbols` into `(keep, {dropped_symbol: reason})` using today's snapshot.

    Only a snapshot whose pass COMPLETED today is consulted. A stale one is ignored entirely rather
    than partially trusted — filtering today's calendar against last week's readings is exactly the
    kind of quiet wrongness this module exists to avoid.
    """
    snapshot = read_snapshot()
    completed = snapshot.get("pass_completed_at")
    fresh = False
    if completed:
        try:
            fresh = _dt.date.fromtimestamp(float(completed)).isoformat() == (
                session or _dt.date.today().isoformat()
            )
        except (TypeError, ValueError, OSError, OverflowError):
            fresh = False
    if not fresh:
        return list(symbols), {}

    rows = snapshot.get("symbols") or {}
    keep, dropped = [], {}
    for symbol in symbols:
        entry = rows.get(symbol if isinstance(symbol, str) else symbol.get("symbol", ""))
        if not entry:
            keep.append(symbol)
            continue
        drop, reason = stable_prefilter_verdict(entry, config)
        if drop:
            dropped[entry.get("symbol") or str(symbol)] = reason
        else:
            keep.append(symbol)
    return keep, dropped


def classify_tier(entry: dict, config: dict) -> tuple[str | None, list[str]]:
    """EarningsEdgeDetection-style recommended/near_miss/fail badge for one symbol's preview
    row. A screener signal only -- it never gates which symbols get scanned (that's the
    universe filter in refresh_symbol_watch) or which rows scout displays; it just ranks what's
    already there.

    Returns `(None, [reason])` when a chain-dependent mandatory input never resolved (price,
    term_structure, expected_move_pct, or combined_open_interest all come from the same
    broker-chain branch in `_compute_symbol_entry` -- if the chain fetch failed, the entry's own
    `error` already explains why, and there is nothing meaningful to score). Otherwise:
    - `"fail"`: at least one gate is below even its near-miss floor.
    - `"near_miss"`: every gate clears its near-miss floor, at least one below the strict bar
      (this also covers a soft signal -- iv_rv_ratio/winrate/avg_volume -- that's simply
      unavailable rather than known-low, the same "couldn't determine" != "known bad" posture
      the rest of this module uses for liquidity).
    - `"recommended"`: every gate clears its strict bar.
    """
    t = _tier_thresholds(config)
    price = entry.get("price")
    oi = entry.get("combined_open_interest")
    term_structure = entry.get("term_structure")
    expected_move_pct = entry.get("expected_move_pct")

    if price is None or term_structure is None or expected_move_pct is None or oi is None:
        return None, [entry.get("error") or "insufficient data to classify"]

    expected_move_dollars = expected_move_pct * price
    iv_rv_ratio = entry.get("iv_rv_ratio")
    winrate = entry.get("winrate")
    avg_volume = entry.get("avg_volume")

    fails: list[str] = []
    near_misses: list[str] = []

    if price < t["near_miss_min_price"]:
        fails.append(f"price ${price:.2f} below ${t['near_miss_min_price']:.2f}")
    elif price < t["min_price"]:
        near_misses.append(f"price ${price:.2f} in near-miss band (<${t['min_price']:.2f})")

    if oi < t["min_combined_open_interest"]:
        fails.append(f"open interest {oi:.0f} below {t['min_combined_open_interest']}")

    if term_structure > t["min_term_structure"]:
        fails.append(f"term structure {term_structure:.4f} above {t['min_term_structure']:.4f}")

    if expected_move_dollars < t["min_expected_move_dollars"]:
        fails.append(
            f"expected move ${expected_move_dollars:.2f} below ${t['min_expected_move_dollars']:.2f}"
        )

    if iv_rv_ratio is None:
        near_misses.append("iv/rv ratio unavailable")
    elif iv_rv_ratio < t["near_miss_min_iv_rv_ratio"]:
        fails.append(f"iv/rv {iv_rv_ratio:.2f} below {t['near_miss_min_iv_rv_ratio']:.2f}")
    elif iv_rv_ratio < t["min_iv_rv_ratio"]:
        near_misses.append(f"iv/rv {iv_rv_ratio:.2f} in near-miss band (<{t['min_iv_rv_ratio']:.2f})")

    if winrate is None:
        near_misses.append("winrate unavailable")
    elif winrate < t["near_miss_min_winrate"]:
        fails.append(f"winrate {winrate:.0%} below {t['near_miss_min_winrate']:.0%}")
    elif winrate < t["min_winrate"]:
        near_misses.append(f"winrate {winrate:.0%} in near-miss band (<{t['min_winrate']:.0%})")

    if avg_volume is None:
        near_misses.append("avg volume unavailable")
    elif avg_volume < t["near_miss_min_avg_volume"]:
        fails.append(f"avg volume {avg_volume:.0f} below {t['near_miss_min_avg_volume']:.0f}")
    elif avg_volume < t["min_avg_volume"]:
        near_misses.append(f"avg volume {avg_volume:.0f} in near-miss band (<{t['min_avg_volume']:.0f})")

    if fails:
        return "fail", fails
    if near_misses:
        return "near_miss", near_misses
    return "recommended", []


def _collapse_to_nearest_date(raw_rows: list[dict]) -> dict[str, dict]:
    """One row per symbol -- its nearest (soonest) reporting date in the window. A symbol
    reporting more than once in a `--days`-wide window is practically never real (Dolt data
    quality noise, not a genuine double-report), and the soonest date is the one a forward
    preview actually cares about."""
    by_symbol: dict[str, dict] = {}
    for row in raw_rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        report_date = row.get("date")
        if not symbol or report_date is None:
            continue
        if isinstance(report_date, str):
            try:
                report_date = _date.fromisoformat(report_date[:10])
            except ValueError:
                continue
        existing = by_symbol.get(symbol)
        if existing is None or report_date < existing["date"]:
            by_symbol[symbol] = {"date": report_date, "timing": row.get("timing")}
    return by_symbol


def _compute_symbol_entry(
    symbol: str, earnings_date: _date, earnings_timing: str | None, config: dict
) -> dict:
    """One symbol's full preview row. Dolt-only fields (avg_volume, iv_rv_ratio's Dolt side,
    winrate, historical move stats) are always attempted; broker-chain-dependent fields (price,
    term_structure, expected_move_pct, market_cap, iv_rank/percentile, OI/volume, spread) are
    skipped with `error` explaining why on any failure, but never block the Dolt-only fields
    that already succeeded -- a name whose chain fetch fails still shows what Dolt alone knows.
    `tier`/`tier_reasons` (see classify_tier) are computed last, over the finished entry, so
    they always reflect exactly what's in the row -- including `None` when price/term_structure/
    expected_move_pct/combined_open_interest never resolved."""
    lookback = config.get("winrate_lookback_quarters", 8)

    dolt_iv_rv = _scanner.fetch_iv_rv_ratio(symbol, config)
    dolt_iv_rv_ratio = dolt_iv_rv.get("iv_rv_ratio") if dolt_iv_rv.get("ok") else None
    avg_volume = _scanner.fetch_avg_volume(symbol, config)
    winrate_result = _scanner.compute_winrate(symbol, config, lookback)
    move_stats = None
    if winrate_result.get("ok"):
        move_stats = _scanner.move_stats_from_quarters(
            symbol, winrate_result.get("realized_move_quarters", []), config
        )

    criteria: dict = {}
    error = None
    price = None
    quote = _scanner.fetch_quote_and_expirations(symbol)
    if not quote.get("ok"):
        error = quote.get("error", "quote/expirations fetch failed")
    else:
        price, expirations = quote["price"], quote["expirations"]
        front_exp, err = _scanner.select_front_expiration(expirations, earnings_date, earnings_timing or "")
        if front_exp is None:
            error = err
        else:
            min_days = config.get("back_month_min_days_after", _DEFAULT_BACK_MONTH_MIN_DAYS_AFTER)
            back_exp = _scanner.select_back_expiration(expirations, front_exp, min_days)
            if back_exp is None:
                error = "no back-month expiration available"
            else:
                atm = _scanner.fetch_front_back_atm_entries(symbol, front_exp, back_exp, price)
                if not atm.get("ok"):
                    error = atm.get("error", "ATM entries fetch failed")
                else:
                    move = _scanner.compute_expected_move_and_term_structure(
                        atm["front_call"]["mid"],
                        atm["front_put"]["mid"],
                        atm["front_call"]["iv"],
                        atm["back_call"]["iv"],
                        price,
                    )
                    criteria["term_structure"] = move["term_structure"]
                    criteria["expected_move_pct"] = move["expected_move_pct"]
                    criteria.update(
                        _scanner.fetch_liquidity_criteria(
                            symbol, front_exp, expirations, atm["front_call"], atm["front_put"]
                        )
                    )

    _scanner.apply_common_signals(
        criteria,
        avg_volume,
        dolt_iv_rv_ratio,
        winrate_result.get("winrate"),
        winrate_result.get("sample_size", 0),
        move_stats,
    )

    entry = {
        "symbol": symbol,
        "earnings_date": earnings_date.isoformat(),
        "earnings_timing": earnings_timing,
        "price": price,
        "avg_volume": criteria.get("avg_volume"),
        "iv_rv_ratio": criteria.get("iv_rv_ratio"),
        "iv_rv_source": criteria.get("iv_rv_source"),
        "winrate": criteria.get("winrate"),
        "winrate_sample": criteria.get("winrate_sample_size"),
        "avg_actual_move_pct": criteria.get("avg_actual_move_pct"),
        "move_dispersion_pct": criteria.get("move_dispersion_pct"),
        "max_actual_move_pct": criteria.get("max_actual_move_pct"),
        "move_tail_veto": criteria.get("move_tail_veto"),
        "term_structure": criteria.get("term_structure"),
        "expected_move_pct": criteria.get("expected_move_pct"),
        "implied_vs_avg_actual": criteria.get("implied_vs_avg_actual"),
        "market_cap": criteria.get("market_cap"),
        "iv_rank": criteria.get("iv_rank"),
        "iv_percentile": criteria.get("iv_percentile"),
        "combined_open_interest": criteria.get("combined_open_interest"),
        "combined_option_volume": criteria.get("combined_option_volume"),
        "bid_ask_spread_pct": criteria.get("bid_ask_spread_pct"),
        "net_combo_spread_pct": criteria.get("net_combo_spread_pct"),
        "error": error,
        "refreshed_at": time.time(),
    }
    entry["tier"], entry["tier_reasons"] = classify_tier(entry, config)
    return entry


def _snapshot_path() -> os.PathLike[str]:
    return _paths.data_path(_SNAPSHOT_NAME)


def read_snapshot() -> dict:
    """The current snapshot, or an empty shell if none has ever been written. Never raises --
    a missing/corrupt file (a fresh install, or a reader racing a first-ever write) degrades to
    an empty result, same posture as every other read-only surface in this suite."""
    target = _snapshot_path()
    if not os.path.exists(target):
        return {"pass_started_at": None, "pass_completed_at": None, "total": 0, "done": 0, "symbols": {}}
    try:
        with open(target, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"pass_started_at": None, "pass_completed_at": None, "total": 0, "done": 0, "symbols": {}}


def _write_snapshot(
    symbols: dict, pass_started_at: float, pass_completed_at: float | None, total: int, done: int
) -> None:
    payload = {
        "pass_started_at": pass_started_at,
        "pass_completed_at": pass_completed_at,
        "total": total,
        "done": done,
        "symbols": symbols,
    }
    target = _snapshot_path()
    fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(target) or ".", prefix=".symbol_watch-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, default=str)
        os.replace(tmp_path, target)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def refresh_symbol_watch(days: int = _DEFAULT_DAYS, config: dict | None = None) -> dict:
    """Run one full pass: fetch the Dolt calendar range, compute each symbol's preview row, and
    write progress into the snapshot after every symbol. Starts from the previous pass's symbols
    for anything still in THIS pass's scope (merged, not replaced, so a concurrent reader never
    sees a not-yet-reached symbol disappear mid-pass) -- but a symbol that dropped out of scope
    (aged out of the `--days` window, or fell out of the universe filter) is pruned immediately at
    the start of the new pass rather than lingering with a stale reading no future pass will ever
    refresh.

    `days` is **trading days**, not calendar days (`cherrypick.core.calendar.nth_trading_day`) --
    scout's Upcoming section shows the same "next N trading days" window, so a weekend or holiday
    inside the range never silently shrinks what either side means by N days.

    Pre-filtered to `symbol_watch.liquid_only`'s universe (config default True) *before* the
    expensive per-symbol chain fetch: the union of tastytrade's "Liquid Symbols", "High Options
    Volume", and "tasty Earnings" public watchlists (`scanner.fetch_watch_universe`) -- scout's
    own Upcoming section only ever displays rows that made it into this scan, so anything outside
    the union would be a wasted broker round trip for a row nobody sees. A failed/empty universe
    fetch degrades to scanning everyone rather than silently scanning no one -- "couldn't
    determine" must never read as "nothing qualifies", same discipline scout's own
    `liquidity_service.py` uses for its own watchlist reads.
    """
    config = config or _scanner._load_config()
    today = _date.today()
    end = _calendar.nth_trading_day(today, max(1, days))

    raw_rows = _scanner.fetch_dolthub_calendar_range(today, end, config)
    by_symbol = _collapse_to_nearest_date(raw_rows)

    liquid_only = (config.get("symbol_watch") or {}).get("liquid_only", True)
    if liquid_only:
        universe = _scanner.fetch_watch_universe()
        if universe:
            by_symbol = {s: info for s, info in by_symbol.items() if s in universe}

    total = len(by_symbol)

    pass_started_at = time.time()
    previous_symbols = read_snapshot().get("symbols") or {}
    symbols = {symbol: previous_symbols[symbol] for symbol in by_symbol if symbol in previous_symbols}
    _write_snapshot(symbols, pass_started_at, None, total, 0)

    done = 0
    for symbol, info in sorted(by_symbol.items()):
        symbols[symbol] = _compute_symbol_entry(symbol, info["date"], info["timing"], config)
        done += 1
        _write_snapshot(symbols, pass_started_at, None, total, done)

    pass_completed_at = time.time()
    _write_snapshot(symbols, pass_started_at, pass_completed_at, total, done)
    return {
        "ok": True,
        "total": total,
        "done": done,
        "pass_started_at": pass_started_at,
        "pass_completed_at": pass_completed_at,
    }


def cmd_refresh(args) -> dict:
    return refresh_symbol_watch(days=args.days)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p_refresh = sub.add_parser("refresh")
    p_refresh.add_argument("--days", type=int, default=_DEFAULT_DAYS)

    args = parser.parse_args()
    dispatch = {"refresh": cmd_refresh}
    result = dispatch[args.command](args)
    json.dump(result, sys.stdout, default=str)


if __name__ == "__main__":
    main()
