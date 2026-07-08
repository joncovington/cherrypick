"""Strategy-agnostic earnings-signal engine, shared by every strategy in
src/strategies/. Nothing in this file assumes iron flies specifically --
calendar lookup, average volume, IV/RV ratio, historical winrate, generic
option-chain ATM/wing helpers, and candidate ranking/position selection
are all reusable by any earnings-vol strategy added later.

Strategy-specific screening thresholds, order construction, and tiering
logic live in src/strategies/<strategy_name>.py (see strategies/iron_fly.py
for the first and currently only implementation), which import from this
module rather than duplicating it.

Commands (see CLAUDE.md's Tool Reference):
  get_calendar --date MM/DD/YYYY
  get_iv_rv --symbol X
  get_winrate --symbol X [--lookback_quarters N]
"""

import argparse
import json
import sys
from datetime import date as _date, datetime, timedelta
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"


def _load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


def fetch_dolthub_calendar(date: str, config: dict) -> list[dict]:
    """Query a locally-running `dolt sql-server` for post-no-preference/earnings.

    Requires `dolt clone post-no-preference/earnings && cd earnings && dolt sql-server`
    running separately (see README's DoltHub setup notes) and
    `pip install mysql-connector-python`.

    Schema verified live against the DoltHub SQL API (2026-07-06):
    earnings_calendar(act_symbol varchar(64), date date, "when" text),
    e.g. `('EPAC', '2026-07-07', 'After market close')`. `when` is a MySQL
    reserved word and must stay backtick-quoted in the query.
    """
    import mysql.connector

    conn = mysql.connector.connect(
        host=config.get("dolthub_host", "127.0.0.1"),
        port=config.get("dolthub_port", 3306),
        user=config.get("dolthub_user", "root"),
        database=config.get("dolthub_database", "earnings"),
    )
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT act_symbol AS symbol, date, `when` AS timing "
            "FROM earnings_calendar WHERE date = %s",
            (date,),
        )
        return cur.fetchall()
    finally:
        conn.close()


def fetch_entry_window_calendar(config: dict, today: _date | None = None) -> list[dict]:
    """Merges today's After-market-close earnings with tomorrow's
    Before-market-open earnings into one entry-window candidate list --
    generalizes the merge that previously only existed as CLAUDE.md prose
    (Step 4b's "merge two calendar dates" bullet, iron_fly-specific) into
    shared, testable code any strategy/orchestrator can call.

    A same-day BMO report already happened this morning and must not be
    re-entered; a report tomorrow morning is still ahead of us this
    afternoon -- same reasoning CLAUDE.md's Step 4b already documented,
    just executable now instead of hand-derived each time.

    `today` is a test hook (real callers omit it and get `_date.today()`).
    """
    today = today or _date.today()
    tomorrow = today + timedelta(days=1)

    today_rows = fetch_dolthub_calendar(str(today), config)
    tomorrow_rows = fetch_dolthub_calendar(str(tomorrow), config)

    merged = [r for r in today_rows if r.get("timing") == "After market close"]
    merged += [r for r in tomorrow_rows if r.get("timing") == "Before market open"]
    return merged


def fetch_iv_rv_ratio(symbol: str, config: dict) -> dict:
    """Query post-no-preference/options's volatility_history for IV/RV.
    Requires a second locally-cloned Dolt repo alongside the earnings one,
    served by the same `dolt sql-server` (run it with `--data-dir` pointing
    at a parent directory containing both `earnings/` and `options/` clones
    -- Dolt serves every repo under that directory as its own database on
    one server).

    Schema verified live against the DoltHub SQL API (2026-07-06):
    volatility_history(date, act_symbol, hv_current, iv_current, ...).
    `iv_current` is sometimes null even when `hv_current` isn't (observed on
    the most recent row for a liquid large-cap) — falls back to the most
    recent non-null iv/hv pair within the last 5 rows rather than failing
    or silently treating the ratio as unavailable.
    """
    import mysql.connector

    conn = mysql.connector.connect(
        host=config.get("dolthub_host", "127.0.0.1"),
        port=config.get("dolthub_port", 3306),
        user=config.get("dolthub_user", "root"),
        database=config.get("dolthub_options_database", "options"),
    )
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT date, hv_current, iv_current FROM volatility_history "
            "WHERE act_symbol = %s ORDER BY date DESC LIMIT 5",
            (symbol,),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    for row in rows:
        if row["hv_current"] is not None and row["iv_current"] is not None:
            hv = float(row["hv_current"])
            iv = float(row["iv_current"])
            if hv == 0:
                continue
            return {
                "ok": True,
                "symbol": symbol,
                "as_of_date": str(row["date"]),
                "hv_current": hv,
                "iv_current": iv,
                "iv_rv_ratio": iv / hv,
            }
    return {"ok": False, "symbol": symbol, "error": "no non-null iv/hv pair in the last 5 trading days"}


def cmd_get_iv_rv(args) -> dict:
    config = _load_config()
    return fetch_iv_rv_ratio(args.symbol.strip().upper(), config)


def _dolt_connect(config: dict, database: str):
    import mysql.connector

    return mysql.connector.connect(
        host=config.get("dolthub_host", "127.0.0.1"),
        port=config.get("dolthub_port", 3306),
        user=config.get("dolthub_user", "root"),
        database=database,
    )


def fetch_historical_earnings_dates(symbol: str, before_date: str, limit: int, config: dict) -> list[dict]:
    """Past earnings dates for `symbol`, most recent first, strictly before `before_date`."""
    conn = _dolt_connect(config, config.get("dolthub_database", "earnings"))
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT act_symbol AS symbol, date, `when` AS timing FROM earnings_calendar "
            "WHERE act_symbol = %s AND date < %s ORDER BY date DESC LIMIT %s",
            (symbol, before_date, limit),
        )
        return cur.fetchall()
    finally:
        conn.close()


def _nearest_close(symbol: str, date, direction: str, config: dict) -> dict | None:
    """Nearest trading-day close from stocks.ohlcv. direction 'on_or_before' or 'after'."""
    conn = _dolt_connect(config, config.get("dolthub_stocks_database", "stocks"))
    try:
        cur = conn.cursor(dictionary=True)
        if direction == "on_or_before":
            cur.execute(
                "SELECT date, close FROM ohlcv WHERE act_symbol = %s AND date <= %s "
                "ORDER BY date DESC LIMIT 1",
                (symbol, date),
            )
        else:
            cur.execute(
                "SELECT date, close FROM ohlcv WHERE act_symbol = %s AND date > %s "
                "ORDER BY date ASC LIMIT 1",
                (symbol, date),
            )
        return cur.fetchone()
    finally:
        conn.close()


def pre_and_reaction_closes(symbol: str, earnings_date, timing: str, config: dict) -> tuple[dict, dict] | None:
    """Pick the pre-earnings and post-reaction trading-day closes based on `when`.

    'After market close' -> pre = earnings_date's own close, reaction = next trading day.
    'Before market open' -> pre = the prior trading day, reaction = earnings_date's own close.
    Anything else (unknown/during-market) is deliberately not guessed at — callers should
    skip the quarter rather than risk silently comparing the wrong two days.
    """
    if timing == "After market close":
        pre = _nearest_close(symbol, earnings_date, "on_or_before", config)
        reaction = _nearest_close(symbol, earnings_date, "after", config)
    elif timing == "Before market open":
        # strictly-before day for "pre", so subtract a day off the earnings_date's own
        # date before doing an on_or_before lookup (which would otherwise just return
        # earnings_date itself if the market happened to be open that day).
        from datetime import timedelta
        pre = _nearest_close(symbol, earnings_date - timedelta(days=1), "on_or_before", config)
        reaction = _nearest_close(symbol, earnings_date - timedelta(days=1), "after", config)
    else:
        return None
    if pre is None or reaction is None:
        return None
    return pre, reaction


def fetch_atm_straddle_price(symbol: str, as_of_date, reaction_date, underlying_price: float, config: dict) -> dict | None:
    """ATM straddle mid-price from the option_chain as of `as_of_date`, using the
    nearest expiration on or after `reaction_date`. Returns None (not an exception)
    on any data gap — missing expirations/strikes are a real, expected occurrence
    in older historical data, not a bug to raise on.
    """
    conn = _dolt_connect(config, config.get("dolthub_options_database", "options"))
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT MIN(expiration) AS expiration FROM option_chain "
            "WHERE act_symbol = %s AND date = %s AND expiration >= %s",
            (symbol, as_of_date, reaction_date),
        )
        exp_row = cur.fetchone()
        if not exp_row or exp_row["expiration"] is None:
            return None
        expiration = exp_row["expiration"]

        cur.execute(
            "SELECT strike, call_put, bid, ask FROM option_chain "
            "WHERE act_symbol = %s AND date = %s AND expiration = %s "
            "AND bid IS NOT NULL AND ask IS NOT NULL",
            (symbol, as_of_date, expiration),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    by_strike: dict[float, dict] = {}
    for row in rows:
        strike = float(row["strike"])
        by_strike.setdefault(strike, {})[row["call_put"]] = (float(row["bid"]) + float(row["ask"])) / 2

    complete_strikes = [s for s, legs in by_strike.items() if "Call" in legs and "Put" in legs]
    if not complete_strikes:
        return None
    atm_strike = min(complete_strikes, key=lambda s: abs(s - underlying_price))
    legs = by_strike[atm_strike]
    straddle = legs["Call"] + legs["Put"]
    return {
        "expiration": str(expiration),
        "atm_strike": atm_strike,
        "straddle_mid": straddle,
    }


def compute_winrate(symbol: str, config: dict, lookback_quarters: int = 8) -> dict:
    """Historical: over the last `lookback_quarters` earnings dates, what
    fraction had the option-implied move (ATM straddle price) exceed the
    actual realized move? A high winrate means this name's options have
    historically overpriced its earnings moves -- relevant to any strategy
    that sells that overpriced premium, not just iron flies specifically.
    Quarters with any data gap (missing chain, ambiguous timing, no ohlcv
    row) are skipped and counted separately, not silently treated as
    losses or excluded from the reported sample size.
    """
    today = config.get("_as_of_date")  # test hook; production callers omit this
    before_date = today or "9999-12-31"
    earnings_dates = fetch_historical_earnings_dates(symbol, before_date, lookback_quarters + 4, config)

    results = []
    skipped = []
    for row in earnings_dates:
        if len(results) >= lookback_quarters:
            break
        closes = pre_and_reaction_closes(symbol, row["date"], row["timing"], config)
        if closes is None:
            skipped.append({"date": str(row["date"]), "reason": "ambiguous_timing_or_no_price_data"})
            continue
        pre, reaction = closes
        pre_close = float(pre["close"])
        straddle = fetch_atm_straddle_price(symbol, pre["date"], reaction["date"], pre_close, config)
        if straddle is None:
            skipped.append({"date": str(row["date"]), "reason": "no_matching_option_chain_data"})
            continue
        realized_move = abs(float(reaction["close"]) - pre_close)
        implied_move = straddle["straddle_mid"]
        results.append({
            "earnings_date": str(row["date"]),
            "pre_close": pre_close,
            "reaction_close": float(reaction["close"]),
            "realized_move": realized_move,
            "implied_move": implied_move,
            "win": implied_move > realized_move,
            "expiration_used": straddle["expiration"],
        })

    sample_size = len(results)
    wins = sum(1 for r in results if r["win"])
    return {
        "ok": True,
        "symbol": symbol,
        "sample_size": sample_size,
        "winrate": (wins / sample_size) if sample_size else None,
        "quarters": results,
        "skipped": skipped,
    }


def fetch_avg_volume(symbol: str, config: dict, days: int = 30) -> float | None:
    """30-day average daily volume from stocks.ohlcv. No broker dependency --
    this is real trailing exchange volume, computable entirely from the
    DoltHub stocks dataset already used for the winrate backtest.
    """
    conn = _dolt_connect(config, config.get("dolthub_stocks_database", "stocks"))
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT AVG(volume) AS avg_volume FROM ("
            "  SELECT volume FROM ohlcv WHERE act_symbol = %s "
            "  ORDER BY date DESC LIMIT %s"
            ") recent",
            (symbol, days),
        )
        row = cur.fetchone()
    finally:
        conn.close()
    if row is None or row["avg_volume"] is None:
        return None
    return float(row["avg_volume"])


def call_tt(args_list: list[str]) -> dict:
    """Shell out to tt.py, matching this project's documented CLI-tool
    architecture (see CLAUDE.md's Tool Reference) rather than importing it,
    so this engine stays decoupled from tt.py's broker/credential setup.
    Raises RuntimeError on a non-zero exit (a real crash, not a normal
    {"ok": false} response, which tt.py returns for expected failures like
    missing credentials -- callers must check the returned dict's "ok" key
    themselves rather than rely on this raising for every failure mode.
    """
    import subprocess

    tt_path = Path(__file__).resolve().parent / "tt.py"
    result = subprocess.run(
        [sys.executable, str(tt_path), *args_list],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"tt.py {' '.join(args_list)} failed: {result.stderr.strip().splitlines()[-1] if result.stderr else 'unknown error'}")
    return json.loads(result.stdout)


def has_weekly_options(expirations: list) -> bool:
    """True if `expirations` (need not be sorted) shows at least one gap of
    <=10 days between consecutive dates -- a name with only monthly cycles
    can still incidentally have its nearest monthly expiration fall inside
    a strategy's front-expiration-window filter some weeks (by luck of the
    calendar), which would otherwise pass a generic expiration-window check
    without actually being a liquid, weekly-optioned name. Shared by every
    strategy that wants a genuine weekly-cadence requirement, not just
    "some expiration happens to be close."
    """
    exps = sorted(expirations)
    return any((exps[i + 1] - exps[i]).days <= 10 for i in range(len(exps) - 1))


def reaction_date(earnings_date, earnings_timing: str):
    """The day the market can actually trade on an earnings print: the next
    trading day for an "After market close" report, the report day itself
    for "Before market open". NOT "nearest expiration to today" -- a
    same-day 0DTE expiration having nothing to do with a multi-day-out
    earnings event was a real bug caught live during iron_fly.py's testing.
    """
    return earnings_date + timedelta(days=1) if earnings_timing == "After market close" else earnings_date


def select_front_expiration(expirations: list, earnings_date, earnings_timing: str):
    """Nearest expiration on or after the earnings reaction date. Returns
    (front_expiration, None) on success or (None, error_message) so callers
    can return {"ok": False, "error": ...} without re-deriving the message.
    """
    rd = reaction_date(earnings_date, earnings_timing)
    eligible = [e for e in expirations if e >= rd]
    if not eligible:
        return None, f"no expiration on/after reaction date {rd}"
    return min(eligible), None


def select_back_expiration(expirations: list, front_expiration, min_days_after: int):
    """Nearest genuine monthly-cycle expiration at least `min_days_after`
    days after `front_expiration` (documented convention for real
    term-structure separation, not just "some later date"); falls back to
    the nearest expiration at least that many days out if this name has no
    monthly listing in the fetched window, rather than failing outright.
    """
    back_exp = nearest_expiration_at_least_days_after(expirations, front_expiration, min_days_after, monthly_only=True)
    if back_exp is None:
        back_exp = nearest_expiration_at_least_days_after(expirations, front_expiration, min_days_after, monthly_only=False)
    return back_exp


def fetch_quote_and_expirations(symbol: str) -> dict:
    """Live underlying price + this symbol's full set of listed expirations,
    via tt.py. The shared preamble every strategy's scan-side and
    order-builder functions need before picking a front/back expiration.
    Returns {"ok": False, "error": ...} on any missing data rather than
    raising.
    """
    quote = call_tt(["get_quote", "--symbol", symbol])
    if not quote.get("ok"):
        return {"ok": False, "error": quote.get("error", "get_quote failed")}
    price = quote.get("price")
    if price is None:
        return {"ok": False, "error": "get_quote returned no price"}

    chain_all = call_tt(["get_option_chain", "--symbol", symbol])
    if not chain_all.get("ok"):
        return {"ok": False, "error": chain_all.get("error", "get_option_chain failed")}
    expirations = sorted(_date.fromisoformat(e) for e in chain_all["chain"].keys())
    if not expirations:
        return {"ok": False, "error": "no expirations in chain"}

    return {"ok": True, "price": price, "expirations": expirations}


def fetch_front_back_atm_entries(symbol: str, front_expiration, back_expiration, price: float) -> dict:
    """Narrow (+/-3 strike) front/back-month chain fetch + ATM call/put/call
    lookups needed for a term-structure/expected-move calculation. Shared by
    every strategy that computes expected move off a front-month ATM
    straddle and compares it against a back-month IV. Returns
    {"ok": True, "front_call": ..., "front_put": ..., "back_call": ...} or
    an error dict.
    """
    front_chain = call_tt([
        "get_option_chain", "--symbol", symbol, "--expiration", str(front_expiration),
        "--include_greeks", "--include_quotes", "--strike_count", "3",
        "--around_price", str(price),
    ])
    back_chain = call_tt([
        "get_option_chain", "--symbol", symbol, "--expiration", str(back_expiration),
        "--include_greeks", "--strike_count", "3", "--around_price", str(price),
    ])
    if not front_chain.get("ok") or not back_chain.get("ok"):
        return {"ok": False, "error": "front/back chain fetch failed"}

    front_entries = front_chain["chain"][str(front_expiration)]
    back_entries = back_chain["chain"][str(back_expiration)]
    front_call = atm_entry(front_entries, "call", price)
    front_put = atm_entry(front_entries, "put", price)
    back_call = atm_entry(back_entries, "call", price)
    if front_call is None or front_put is None or back_call is None:
        return {"ok": False, "error": "incomplete ATM strikes in front/back chain"}
    if front_call.get("mid") is None or front_put.get("mid") is None:
        return {"ok": False, "error": "no quote data for front-month ATM strikes"}
    if front_call.get("iv") is None or back_call.get("iv") is None:
        return {"ok": False, "error": "no greeks/iv data for front/back ATM strikes"}

    return {"ok": True, "front_call": front_call, "front_put": front_put, "back_call": back_call}


def compute_expected_move_and_term_structure(front_call_mid: float, front_put_mid: float, front_iv: float, back_iv: float, underlying_price: float) -> dict:
    """Pure calculation, no I/O. Term structure mirrors EarningsEdgeDetection's
    convention: negative values mean the front-month is richer than the
    back-month (the earnings-event IV premium the trade is designed to
    capture) -- (back_iv - front_iv) / back_iv, NOT (front - back) / back,
    which is backwards from docs/screening-criteria.md's -0.004 threshold
    (caught live during iron_fly.py's testing: the naive formula would have
    rejected a real earnings candidate whose front IV was ~64% richer than
    back). expected_move applies the standard 0.85x straddle-to-expected-
    move correction (e.g. AAPL $14.00 straddle -> $11.90 expected move) --
    this only affects screening thresholds, not wing/strike sizing, which
    each strategy's order-builder computes independently from its own
    freshly-fetched straddle/strike prices.
    """
    term_structure = (back_iv - front_iv) / back_iv
    expected_move = 0.85 * (front_call_mid + front_put_mid)
    return {
        "term_structure": term_structure,
        "expected_move_dollars": expected_move,
        "expected_move_pct": expected_move / underlying_price,
    }


def fetch_liquidity_criteria(symbol: str, front_expiration, expirations: list, front_call: dict, front_put: dict) -> dict:
    """Shared liquidity signals every earnings strategy should screen on:
    bid/ask spread width at the front-month ATM strikes, weekly-vs-monthly
    expiration cadence, market cap, and front-month chain-wide combined
    open interest + combined daily option volume. One network round trip
    (OI + volume together) instead of each strategy fetching its own OI-only
    chain separately -- previously duplicated per strategy. Any individual
    signal that can't be fetched/computed comes back None (never guessed),
    matching this project's existing "unverified is a hard-fail, not a
    silent pass" discipline -- enforced by the caller's apply_liquidity_gates,
    not here.
    """
    spread_pct = None
    if (
        all(front_call.get(k) is not None for k in ("bid", "ask", "mid")) and front_call["mid"]
        and all(front_put.get(k) is not None for k in ("bid", "ask", "mid")) and front_put["mid"]
    ):
        spread_pct = max(
            (front_call["ask"] - front_call["bid"]) / front_call["mid"],
            (front_put["ask"] - front_put["bid"]) / front_put["mid"],
        )

    market_cap = None
    mc = call_tt(["get_market_metrics", "--symbol", symbol])
    if mc.get("ok"):
        market_cap = mc.get("market_cap")

    combined_open_interest = None
    combined_option_volume = None
    chain = call_tt([
        "get_option_chain", "--symbol", symbol, "--expiration", str(front_expiration),
        "--include_oi", "--include_volume", "--strike_count", "999",
    ])
    if chain.get("ok"):
        entries = chain["chain"].get(str(front_expiration), [])
        ois = [e["open_interest"] for e in entries if e.get("open_interest") is not None]
        vols = [e["day_volume"] for e in entries if e.get("day_volume") is not None]
        if ois:
            combined_open_interest = sum(ois)
        if vols:
            combined_option_volume = sum(vols)

    return {
        "bid_ask_spread_pct": spread_pct,
        "has_weekly_options": has_weekly_options(expirations),
        "market_cap": market_cap,
        "combined_open_interest": combined_open_interest,
        "combined_option_volume": combined_option_volume,
    }


def _band(value, min_pass, min_near_miss, name: str, near_miss: list, hard_fail: list) -> None:
    """Shared near-miss banding: value >= min_pass passes silently, value in
    [min_near_miss, min_pass) is a near-miss (Tier 2 candidate), below
    min_near_miss is a hard fail, missing entirely is also a near-miss
    (unverified, not an automatic pass). Mutates `near_miss`/`hard_fail` in
    place, matching the list-building style already used throughout every
    strategy's apply_tiering.
    """
    if value is None:
        near_miss.append(f"{name}_unknown")
        return
    if value >= min_pass:
        return
    if value >= min_near_miss:
        near_miss.append(name)
        return
    hard_fail.append(f"{name}_below_near_miss")


def naked_strategies_allowed(full_config: dict) -> bool:
    """Whether a genuinely undefined-risk strategy (short_strangle, jade_lizard's
    put side) is allowed to actually build/submit an order right now.

    Paper mode is always allowed regardless of allow_naked_strategies -- there is
    no real capital or margin at risk in paper mode (tt.py execute_trade is never
    called; save_trade just records a simulated entry_credit), so the reason
    live entries are gated (no live margin-check mechanism built yet) doesn't
    apply. Live mode still requires the explicit allow_naked_strategies flag,
    default False.
    """
    paper_mode = not full_config.get("enable_live_trading", False)
    return paper_mode or full_config.get("allow_naked_strategies", False)


def apply_liquidity_gates(criteria: dict, config: dict, hard_fail: list, near_miss: list) -> None:
    """Shared liquidity hard-filters/near-miss bands, applied identically by
    every earnings strategy's apply_tiering. `config` is the calling
    strategy's own sub-config. Mutates `hard_fail`/`near_miss` in place.
    """
    if criteria.get("combined_open_interest") is None:
        hard_fail.append("combined_open_interest_unverified")
    elif criteria["combined_open_interest"] < config["min_combined_open_interest"]:
        hard_fail.append("combined_open_interest_below_minimum")

    if criteria.get("bid_ask_spread_pct") is None:
        hard_fail.append("bid_ask_spread_pct_unverified")
    elif criteria["bid_ask_spread_pct"] > config["max_bid_ask_spread_pct"]:
        hard_fail.append("bid_ask_spread_too_wide")

    if config.get("require_weekly_options", True):
        if criteria.get("has_weekly_options") is None:
            hard_fail.append("has_weekly_options_unverified")
        elif not criteria["has_weekly_options"]:
            hard_fail.append("no_weekly_options")

    _band(criteria.get("market_cap"), config["min_market_cap"], config["near_miss_min_market_cap"], "market_cap", near_miss, hard_fail)
    _band(criteria.get("combined_option_volume"), config["min_combined_option_volume"], config["near_miss_min_combined_option_volume"], "combined_option_volume", near_miss, hard_fail)


def is_monthly_expiration(d) -> bool:
    """True if `d` is a standard monthly options expiration (the third
    Friday of its month). Used to distinguish "true" monthly cycles from
    weekly expirations that happen to be some number of days out --
    relevant wherever a strategy specifically wants term-structure
    separation from a genuine monthly cycle, not just any later date.
    """
    if d.weekday() != 4:  # Friday
        return False
    return 15 <= d.day <= 21


def nearest_expiration_at_least_days_after(expirations: list, after: object, min_days: int, monthly_only: bool = False):
    """Nearest expiration that is at least `min_days` after `after`, optionally
    restricted to true monthly cycles (see is_monthly_expiration). Returns
    None if no candidate qualifies. `expirations` need not be pre-sorted.
    """
    candidates = [e for e in expirations if (e - after).days >= min_days]
    if monthly_only:
        candidates = [e for e in candidates if is_monthly_expiration(e)]
    if not candidates:
        return None
    return min(candidates)


def atm_entry(entries: list[dict], option_type: str, underlying_price: float) -> dict | None:
    """Closest-to-ATM entry of the given type ('call' or 'put') from a
    tt.py get_option_chain 'chain' list for one expiration. tt.py's
    entries come from the tastytrade SDK's serialized Option model, whose
    `option_type` field is typically 'C'/'P' (or 'Call'/'Put' depending on
    SDK version) -- matched case-insensitively on the first letter to be
    resilient to either form, mirroring MEICAgent's own _is_call/_is_put.
    """
    want = option_type[0].lower()
    matches = [e for e in entries if str(e.get("option_type", "")).strip().lower().startswith(want)]
    if not matches:
        return None
    return min(matches, key=lambda e: abs(float(e["strike_price"]) - underlying_price))


def nearest_strike_entry(entries: list[dict], option_type: str, target_strike: float, exclude_strike: float) -> dict | None:
    """Like atm_entry, but targets an arbitrary strike (for wing/spread
    selection) and excludes a given strike so a degenerate zero-width
    spread can't be picked when strikes are sparse.
    """
    want = option_type[0].lower()
    matches = [
        e for e in entries
        if str(e.get("option_type", "")).strip().lower().startswith(want)
        and float(e["strike_price"]) != exclude_strike
    ]
    if not matches:
        return None
    return min(matches, key=lambda e: abs(float(e["strike_price"]) - target_strike))


def _shrunk_winrate(winrate: float | None, sample_size: int, target_sample: int = 8) -> float:
    """Shrink winrate toward a neutral 0.5 prior when sample_size is small,
    so an 8-quarter 85% winrate doesn't lose to a 1-quarter 100% winrate --
    the latter carries far less information despite the higher raw number.
    """
    if winrate is None or not sample_size:
        return 0.5
    shrink = min(sample_size / target_sample, 1.0)
    return 0.5 + shrink * (winrate - 0.5)


def compute_composite_score(criteria: dict, winrate_sample_size: int = 0) -> float | None:
    """Composite ranking score for a Tier 1/2 candidate, built from signals
    common to any earnings-vol-selling strategy: term structure (or, absent
    that, skew_abs -- expected_move_butterfly has no back-month/term-
    structure signal at all, only a call/put skew reading, and needs a
    comparable score too so it isn't silently unrankable next to the five
    strategies that do have term_structure), IV/RV ratio, winrate. No new
    data, just combining what a strategy's tiering already required to be
    present.

    Returns None if neither term_structure nor skew_abs is present; a
    candidate can't be ranked without one of them. IV/RV ratio and winrate
    are secondary confirmations of the same "is IV overpriced" question as
    the core edge signal, not independent signals, so they're applied as
    multiplicative adjustments rather than summed as separate scores --
    summing would let a strong core signal and a merely-neutral IV/RV ratio
    look identical to two moderate signals combined, which isn't the intent
    (a strong core signal should still rank higher than two average ones).
    """
    edge = criteria.get("term_structure")
    if edge is None:
        edge = criteria.get("skew_abs")
        if edge is None:
            return None
    iv_rv = criteria.get("iv_rv_ratio") or 1.0
    wr = _shrunk_winrate(criteria.get("winrate"), winrate_sample_size)
    return abs(edge) * iv_rv * wr


def fetch_quotes_by_symbol(underlying_symbol: str, expiration, option_symbols: list, price: float) -> dict:
    """Live quotes for a specific set of already-known option symbols (e.g.
    a stored position's entry legs, parsed from `trades.legs_json`), keyed
    by exact symbol -- not a nearest-strike lookup, since the legs are
    already known precisely from when the position was opened. Shared by
    every single-expiration strategy's close mechanism (see
    compute_generic_exit_debit) so no strategy re-implements its own
    quote-fetch-and-match logic for closing.
    """
    chain = call_tt([
        "get_option_chain", "--symbol", underlying_symbol, "--expiration", str(expiration),
        "--include_quotes", "--include_greeks", "--strike_count", "999", "--around_price", str(price),
    ])
    if not chain.get("ok"):
        return {}
    entries = chain["chain"].get(str(expiration), [])
    wanted = set(option_symbols)
    return {e["symbol"]: e for e in entries if e["symbol"] in wanted}


def compute_generic_exit_debit(legs: list[dict], quotes: dict) -> float | None:
    """Signed exit debit for closing an arbitrary multi-leg single-expiration
    position, given its original entry legs (`{symbol, action, quantity}`,
    e.g. parsed from `trades.legs_json`) and live quotes keyed by exact
    option symbol (see fetch_quotes_by_symbol). Entry-`"Sell to Open"` legs
    are bought back at ask, entry-`"Buy to Open"` legs are sold at bid --
    the same conservative same-side-of-spread convention iron_fly's close
    already used, generalized to any leg count/shape (iron_condor's two
    distinct short strikes, expected_move_butterfly's asymmetric strikes
    with a x2 short quantity) instead of duplicated per strategy.

    Same sign convention as entry_credit: positive means it costs money to
    close, negative means closing nets a credit -- `pnl = (entry_credit -
    exit_debit) * 100` works unchanged for both credit and debit strategies.

    Returns None if any leg's required quote side is missing -- callers
    should retry next tick rather than close on incomplete data, same
    discipline as iron_fly's existing close logic.
    """
    total = 0.0
    for leg in legs:
        q = quotes.get(leg["symbol"])
        if q is None:
            return None
        qty = leg.get("quantity", 1)
        if leg["action"] == "Sell to Open":
            if q.get("ask") is None:
                return None
            total += qty * q["ask"]
        elif leg["action"] == "Buy to Open":
            if q.get("bid") is None:
                return None
            total -= qty * q["bid"]
    return total


def evaluate_debit_spread_exit(entry_credit: float, exit_debit: float, config: dict) -> dict:
    """Shared profit-target/stop-loss check for simple debit-spread
    strategies that close as a single unit (expected_move_butterfly today;
    reusable by any future single-unit debit strategy). `entry_credit` is
    stored negative (debit paid); `exit_debit` follows the same sign
    convention (negative = nets a credit on close).

    Calibrated against debit-butterfly-specific research: 25-50% of max
    profit as a target, a stop around a 25-50% loss of the debit paid --
    deliberately tighter than double_calendar's calendar-spread-calibrated
    stop_loss_pct_of_debit (1.0), since this is a structurally different,
    faster-resolving overnight position, not a multi-week calendar. Not
    backtested for this project specifically.
    """
    entry_debit = abs(entry_credit)
    value_received_on_close = -exit_debit
    profit = value_received_on_close - entry_debit
    profit_target_pct = config.get("profit_target_pct", 0.25)
    stop_loss_pct_of_debit = config.get("stop_loss_pct_of_debit", 0.40)
    if profit >= entry_debit * profit_target_pct:
        return {"action": "close_all", "reason": "profit_target"}
    if (entry_debit - value_received_on_close) >= entry_debit * stop_loss_pct_of_debit:
        return {"action": "close_all", "reason": "stop_loss"}
    return {"action": "hold"}


def evaluate_credit_spread_exit(entry_credit: float, exit_debit: float, config: dict) -> dict:
    """Shared profit-target/stop-loss check for credit-spread strategies
    that enter for a net credit and close as a single unit (broken_wing_butterfly).

    Calibrated against earnings trading conventions: profit targets of 10% of
    credit collected, stop losses at 2.0-2.6x credit received. Unlike
    evaluate_debit_spread_exit which is for positions that PAY upfront,
    this is for positions that COLLECT upfront (credit butterflies, iron spreads
    that net credit).

    `entry_credit`: credit received upfront (stored negative per convention, e.g., -2.00)
    `exit_debit`: cost to close position (positive = costs money to close)

    The key insight: a credit-receiving position has built-in loss protection
    equal to the credit collected. Max loss = (far_strike - near_strike) - credit.
    """
    credit_received = abs(entry_credit)  # Convert from stored negative to positive
    profit = credit_received - exit_debit
    profit_target_pct = config.get("profit_target_pct", 0.10)
    stop_loss_credit_multiple = config.get("stop_loss_credit_multiple", 2.0)

    if profit >= credit_received * profit_target_pct:
        return {"action": "close_all", "reason": "profit_target"}
    if exit_debit >= credit_received * stop_loss_credit_multiple:
        return {"action": "close_all", "reason": "stop_loss"}
    return {"action": "hold"}


def select_side(symbol: str, front_expiration, price: float, config: dict) -> dict:
    """25-delta risk reversal (call IV - put IV at matched |delta|, the
    industry-standard skew measure) to pick which side to sell. Used by
    directional_credit_spread to choose call or put side via skew analysis.

    25-delta matched comparison avoids the structural put skew distortion
    that would appear if comparing raw IV at dollar-distance strikes (price
    +/- expected_move) with unequal deltas.
    """
    chain = call_tt([
        "get_option_chain", "--symbol", symbol, "--expiration", str(front_expiration),
        "--include_greeks", "--strike_count", "40", "--around_price", str(price),
    ])
    if not chain.get("ok"):
        return {"ok": False, "error": chain.get("error", "get_option_chain failed")}
    entries = chain["chain"].get(str(front_expiration), [])

    target_delta = config.get("skew_delta_target", 0.25)
    calls = [e for e in entries if str(e.get("option_type", "")).strip().lower().startswith("c") and e.get("delta") is not None]
    puts = [e for e in entries if str(e.get("option_type", "")).strip().lower().startswith("p") and e.get("delta") is not None]
    if not calls or not puts:
        return {"ok": False, "error": "no greeks/delta data for skew measurement"}

    call_ref = min(calls, key=lambda e: abs(abs(e["delta"]) - target_delta))
    put_ref = min(puts, key=lambda e: abs(abs(e["delta"]) - target_delta))
    if call_ref.get("iv") is None or put_ref.get("iv") is None:
        return {"ok": False, "error": "no iv data for risk-reversal candidates"}

    call_iv, put_iv = call_ref["iv"], put_ref["iv"]
    side = "call" if call_iv >= put_iv else "put"
    return {"ok": True, "side": side, "call_iv": call_iv, "put_iv": put_iv, "skew": call_iv - put_iv}


def rank_candidates(candidates: list[dict], config: dict) -> list[dict]:
    """Rank Tier 1/2 candidates from a strategy's get_candidates output by
    compute_composite_score, descending. Reject and Near Miss candidates
    are excluded entirely -- ranking within tiers they didn't clear would
    imply they're viable with enough of a score, which they aren't.
    """
    scored = []
    for c in candidates:
        if c.get("tier") not in ("Tier 1", "Tier 2"):
            continue
        score = compute_composite_score(c["criteria"], c.get("winrate_sample_size", 0))
        if score is None:
            continue
        scored.append({**c, "composite_score": score})
    scored.sort(key=lambda c: c["composite_score"], reverse=True)
    return scored


def select_positions(ranked: list[dict], config: dict) -> dict:
    """Walk a ranked candidate list applying max_concurrent_earnings_positions
    and correlation_block_list, selecting the top-scoring candidates that
    don't collide. Diversifies across names rather than concentrating in
    whichever single candidate scores highest -- earnings-move risk is
    idiosyncratic per name, so spreading a limited position budget across
    several qualifying names is sounder than betting it all on the top
    score, whose precision doesn't warrant that much confidence.
    """
    max_positions = config.get("max_concurrent_earnings_positions", 3)
    block_list = config.get("correlation_block_list", [])

    def _group_of(symbol: str) -> int | None:
        for i, group in enumerate(block_list):
            if symbol in group:
                return i
        return None

    selected: list[dict] = []
    skipped: list[dict] = []
    used_groups: set[int] = set()

    for c in ranked:
        if len(selected) >= max_positions:
            skipped.append({"symbol": c["symbol"], "reason": "max_positions_reached"})
            continue
        group = _group_of(c["symbol"])
        if group is not None and group in used_groups:
            skipped.append({"symbol": c["symbol"], "reason": "correlation_block"})
            continue
        selected.append(c)
        if group is not None:
            used_groups.add(group)

    return {"selected": selected, "skipped": skipped}


def run_candidate_scan(args_date: str, config: dict, fetch_criteria_fn, apply_tiering_fn, strategy_config: dict, extra_criteria_fn=None) -> dict:
    """Shared cmd_get_candidates body: calendar fetch, per-symbol criteria
    (strategy-specific fetch_criteria_fn plus the common avg_volume/
    iv_rv_ratio/winrate signals), tiering, ranking, and position selection.
    Every earnings strategy's cmd_get_candidates is a thin wrapper around
    this -- only the strategy-specific fetch/tiering functions differ.

    `extra_criteria_fn(symbol, config, lookback, criteria)`, if given, is
    called after the common signals are populated and may mutate `criteria`
    in place (e.g. double_calendar's realized_move_dispersion step) --
    the one genuine per-strategy addition inside an otherwise identical loop.
    """
    iso_date = datetime.strptime(args_date, "%m/%d/%Y").strftime("%Y-%m-%d")
    calendar = fetch_dolthub_calendar(iso_date, config)
    lookback = config.get("winrate_lookback_quarters", 8)

    candidates = []
    for entry in calendar:
        symbol = entry["symbol"]
        criteria: dict = {}

        broker = fetch_criteria_fn(symbol, entry["date"], entry["timing"], config)
        if broker.get("ok"):
            criteria.update(broker["criteria"])
        broker_error = None if broker.get("ok") else broker.get("error")

        criteria["avg_volume"] = fetch_avg_volume(symbol, config)

        ivrv = fetch_iv_rv_ratio(symbol, config)
        criteria["iv_rv_ratio"] = ivrv["iv_rv_ratio"] if ivrv.get("ok") else None

        winrate = compute_winrate(symbol, config, lookback)
        criteria["winrate"] = winrate["winrate"]
        winrate_sample_size = winrate["sample_size"]

        if extra_criteria_fn is not None:
            extra_criteria_fn(symbol, config, lookback, criteria)

        tiering = apply_tiering_fn(criteria, strategy_config)

        candidates.append({
            "symbol": symbol,
            "earnings_timing": entry["timing"],
            "tier": tiering["tier"],
            "hard_fail_reasons": tiering["hard_fail_reasons"],
            "near_miss_reasons": tiering["near_miss_reasons"],
            "criteria": criteria,
            "winrate_sample_size": winrate_sample_size,
            "broker_data_error": broker_error,
        })

    candidates.sort(key=lambda c: {"Tier 1": 0, "Tier 2": 1, "Near Miss": 2, "Reject": 3}[c["tier"]])

    ranked = rank_candidates(candidates, config)
    selection = select_positions(ranked, config)

    return {
        "ok": True,
        "date": args_date,
        "candidates": candidates,
        "ranked": ranked,
        "selected": selection["selected"],
        "skipped_for_selection": selection["skipped"],
    }


def run_strategy_main(cmd_get_candidates_fn, fetch_order_fn) -> None:
    """Shared main()/argparse/dispatch scaffolding, byte-identical across
    every strategy file before this consolidation. `fetch_order_fn` is the
    strategy's own order-builder (e.g. fetch_iron_fly_order), called
    directly here instead of each file keeping its own three-line
    cmd_get_order wrapper.
    """
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p_cand = sub.add_parser("get_candidates")
    p_cand.add_argument("--date", required=True)

    p_order = sub.add_parser("get_order")
    p_order.add_argument("--symbol", required=True)
    p_order.add_argument("--earnings_date", required=True)
    p_order.add_argument("--earnings_timing", required=True)

    args = parser.parse_args()
    if args.command == "get_candidates":
        result = cmd_get_candidates_fn(args)
    else:
        config = _load_config()
        result = fetch_order_fn(
            args.symbol.strip().upper(),
            _date.fromisoformat(args.earnings_date),
            args.earnings_timing,
            config,
        )
    json.dump(result, sys.stdout, default=str)


def cmd_get_winrate(args) -> dict:
    config = _load_config()
    return compute_winrate(args.symbol.strip().upper(), config, args.lookback_quarters)


def cmd_get_calendar(args) -> dict:
    config = _load_config()
    source = config.get("earnings_calendar_source", "dolthub")
    if source != "dolthub":
        raise NotImplementedError(f"calendar source '{source}' not implemented — only 'dolthub' is wired up")
    iso_date = datetime.strptime(args.date, "%m/%d/%Y").strftime("%Y-%m-%d")
    rows = fetch_dolthub_calendar(iso_date, config)
    for row in rows:
        row["date"] = str(row["date"])
    return {"ok": True, "date": args.date, "source": source, "tickers": rows}


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p_cal = sub.add_parser("get_calendar")
    p_cal.add_argument("--date", required=True)

    p_ivrv = sub.add_parser("get_iv_rv")
    p_ivrv.add_argument("--symbol", required=True)

    p_winrate = sub.add_parser("get_winrate")
    p_winrate.add_argument("--symbol", required=True)
    p_winrate.add_argument("--lookback_quarters", type=int, default=8)

    args = parser.parse_args()
    dispatch = {
        "get_calendar": cmd_get_calendar,
        "get_iv_rv": cmd_get_iv_rv,
        "get_winrate": cmd_get_winrate,
    }
    result = dispatch[args.command](args)
    json.dump(result, sys.stdout, default=str)


if __name__ == "__main__":
    main()
