"""Internal earnings-candidate scanner.

Implements the hard filters and tiering in docs/screening-criteria.md.
get_candidates ties every signal together into a full tiered scan across
a day's calendar. The earnings calendar, average volume (#8), IV/RV
ratio (#10), and winrate backtest (#9) are all queried from three
DoltHub datasets served by one locally-running `dolt sql-server
--data-dir`: post-no-preference/earnings, post-no-preference/options,
and post-no-preference/stocks -- all real and tested live. Price, term
structure, expected move, OI, ATM delta, and expiration window (#1-#3,
#5-#7) depend on tt.py's broker calls, not implemented yet -- every
candidate currently rejects on those criteria specifically (see
fetch_price_and_term_structure, apply_tiering).

Intended commands (see CLAUDE.md's Tool Reference):
  get_calendar --date MM/DD/YYYY
  get_iv_rv --symbol X
  get_winrate --symbol X [--lookback_quarters N]
  get_candidates --date MM/DD/YYYY
"""

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"


@dataclass
class TermStructureResult:
    symbol: str
    front_expiration: str
    back_expiration: str
    front_atm_iv: float
    back_atm_iv: float
    term_structure: float  # (front_iv - back_iv) / back_iv; negative = front richer
    expected_move: float   # front-month ATM straddle price
    expected_move_pct: float  # expected_move / underlying_price


def compute_term_structure(
    symbol: str,
    underlying_price: float,
    front_expiration: str,
    front_atm_call_mid: float,
    front_atm_put_mid: float,
    front_atm_iv: float,
    back_expiration: str,
    back_atm_iv: float,
) -> TermStructureResult:
    """Pure calculation — no network calls. Caller supplies ATM strike data
    already pulled from `tt.py get_option_chain --include_greeks` for both
    the front (post-earnings) and a later back-month expiration.

    Term structure mirrors EarningsEdgeDetection's convention: negative
    values mean the front-month is richer than the back-month (the
    earnings-event IV premium the trade is designed to capture).

    NOTE: the sign here is (back - front) / back, not (front - back) / back
    -- caught live during testing, a real bug: the naive (front - back)/back
    formula is POSITIVE when front is richer, which is backwards from this
    function's own documented convention and from the -0.004 threshold in
    docs/screening-criteria.md (which expects negative-is-good). With the
    original sign, a real earnings candidate (EPAC, front IV ~64% richer
    than back) would have been *rejected* as term_structure_insufficient --
    exactly the opposite of the intended behavior.
    """
    term_structure = (back_atm_iv - front_atm_iv) / back_atm_iv
    expected_move = front_atm_call_mid + front_atm_put_mid
    return TermStructureResult(
        symbol=symbol,
        front_expiration=front_expiration,
        back_expiration=back_expiration,
        front_atm_iv=front_atm_iv,
        back_atm_iv=back_atm_iv,
        term_structure=term_structure,
        expected_move=expected_move,
        expected_move_pct=expected_move / underlying_price,
    )


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


def fetch_iv_rv_ratio(symbol: str, config: dict) -> dict:
    """Query post-no-preference/options's volatility_history for IV/RV (screening
    criterion #10). Requires a second locally-cloned Dolt repo alongside the
    earnings one, served by the same `dolt sql-server` (run it with
    `--data-dir` pointing at a parent directory containing both `earnings/`
    and `options/` clones — Dolt serves every repo under that directory as
    its own database on one server).

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


def _pre_and_reaction_closes(symbol: str, earnings_date, timing: str, config: dict) -> tuple[dict, dict] | None:
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
    """Backtest screening criterion #9: over the last `lookback_quarters` earnings
    dates, what fraction had the option-implied move (ATM straddle price) exceed
    the actual realized move? A high winrate means this name's options have
    historically overpriced its earnings moves — exactly the edge an iron fly
    seller wants. Quarters with any data gap (missing chain, ambiguous timing,
    no ohlcv row) are skipped and counted separately, not silently treated as
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
        closes = _pre_and_reaction_closes(symbol, row["date"], row["timing"], config)
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
    """30-day average daily volume from stocks.ohlcv (screening criterion #8).
    No broker dependency -- this is real trailing exchange volume, computable
    entirely from the DoltHub stocks dataset already used for the winrate
    backtest.
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


def _call_tt(args_list: list[str]) -> dict:
    """Shell out to tt.py, matching this project's documented CLI-tool
    architecture (see CLAUDE.md's Tool Reference) rather than importing it,
    so scanner.py stays decoupled from tt.py's broker/credential setup.
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


def _atm_entry(entries: list[dict], option_type: str, underlying_price: float) -> dict | None:
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


def fetch_price_and_term_structure(symbol: str, earnings_date: date, earnings_timing: str, config: dict) -> dict:
    """Live price + term structure/expected move (criteria #1, #4, #6) via tt.py.

    `earnings_date`/`earnings_timing` pick the correct front-month: the
    nearest expiration on or after the earnings *reaction* date (the day
    the market can actually trade on the news -- the next trading day for
    an "After market close" report, the report day itself for "Before
    market open"). This must NOT be "nearest expiration to today" -- a
    same-day 0DTE expiration having nothing to do with a multi-day-out
    earnings event was caught live during testing (AAPL: front_exp
    defaulted to today's 0DTE chain, producing a nonsensical positive
    term-structure reading for a symbol that wasn't even an earnings
    candidate on that date).

    Returns {"ok": False, "error": ...} on any missing data (no credentials,
    no chain, incomplete ATM strikes, no suitable back-month expiration)
    rather than raising -- callers (cmd_get_candidates) treat that as
    "insufficient data to verify these hard filters," not as a reason to
    crash the whole scan. On success, returns {"ok": True, "criteria":
    {"price": ..., "term_structure": ..., "expected_move_dollars": ...}}.
    """
    try:
        quote = _call_tt(["get_quote", "--symbol", symbol])
        if not quote.get("ok"):
            return {"ok": False, "error": quote.get("error", "get_quote failed")}
        price = quote.get("price")
        if price is None:
            return {"ok": False, "error": "get_quote returned no price"}

        chain_all = _call_tt(["get_option_chain", "--symbol", symbol])
        if not chain_all.get("ok"):
            return {"ok": False, "error": chain_all.get("error", "get_option_chain failed")}
        expirations = sorted(date.fromisoformat(e) for e in chain_all["chain"].keys())
        if not expirations:
            return {"ok": False, "error": "no expirations in chain"}

        # Weekly-options detection: a name with only monthly cycles can still
        # incidentally have its nearest monthly expiration fall inside the
        # max_front_expiration_days window some weeks (by luck of the
        # calendar), which would otherwise pass the generic expiration-window
        # check without actually being a liquid, weekly-optioned name. Check
        # the real cadence instead: at least one gap between consecutive
        # expirations of <=10 days indicates weeklies exist.
        has_weekly_options = any(
            (expirations[i + 1] - expirations[i]).days <= 10
            for i in range(len(expirations) - 1)
        )

        from datetime import timedelta
        if earnings_timing == "After market close":
            reaction_date = earnings_date + timedelta(days=1)
        else:
            reaction_date = earnings_date

        eligible = [e for e in expirations if e >= reaction_date]
        if not eligible:
            return {"ok": False, "error": f"no expiration on/after reaction date {reaction_date}"}
        front_exp = min(eligible)
        back_candidates = [e for e in expirations if e > front_exp]
        if not back_candidates:
            return {"ok": False, "error": "no back-month expiration available for term structure"}
        back_exp = min(back_candidates, key=lambda e: abs((e - front_exp).days - 30))

        front_chain = _call_tt([
            "get_option_chain", "--symbol", symbol, "--expiration", str(front_exp),
            "--include_greeks", "--include_quotes", "--strike_count", "3",
            "--around_price", str(price),
        ])
        back_chain = _call_tt([
            "get_option_chain", "--symbol", symbol, "--expiration", str(back_exp),
            "--include_greeks", "--strike_count", "3", "--around_price", str(price),
        ])
        if not front_chain.get("ok") or not back_chain.get("ok"):
            return {"ok": False, "error": "front/back chain fetch failed"}

        front_entries = front_chain["chain"][str(front_exp)]
        back_entries = back_chain["chain"][str(back_exp)]
        front_call = _atm_entry(front_entries, "call", price)
        front_put = _atm_entry(front_entries, "put", price)
        back_call = _atm_entry(back_entries, "call", price)
        if front_call is None or front_put is None or back_call is None:
            return {"ok": False, "error": "incomplete ATM strikes in front/back chain"}
        if front_call.get("mid") is None or front_put.get("mid") is None:
            return {"ok": False, "error": "no quote data for front-month ATM strikes"}
        if front_call.get("iv") is None or back_call.get("iv") is None:
            return {"ok": False, "error": "no greeks/iv data for front/back ATM strikes"}

        # Combined OI (criterion #3) needs the whole front-month chain, not
        # just the ATM window used for the straddle/IV calc above.
        front_chain_oi = _call_tt([
            "get_option_chain", "--symbol", symbol, "--expiration", str(front_exp),
            "--include_oi", "--strike_count", "999",
        ])
        combined_oi = None
        if front_chain_oi.get("ok"):
            oi_entries = front_chain_oi["chain"].get(str(front_exp), [])
            ois = [e["open_interest"] for e in oi_entries if e.get("open_interest") is not None]
            if ois:
                combined_oi = sum(ois)

        ts = compute_term_structure(
            symbol=symbol,
            underlying_price=price,
            front_expiration=str(front_exp),
            front_atm_call_mid=front_call["mid"],
            front_atm_put_mid=front_put["mid"],
            front_atm_iv=front_call["iv"],
            back_expiration=str(back_exp),
            back_atm_iv=back_call["iv"],
        )
        return {
            "ok": True,
            "criteria": {
                "price": price,
                "term_structure": ts.term_structure,
                "expected_move_dollars": ts.expected_move,
                "combined_open_interest": combined_oi,
                "atm_delta_abs": abs(front_call["delta"]) if front_call.get("delta") is not None else None,
                "front_expiration_days": (front_exp - date.today()).days,
                "chain_complete": True,
                "has_weekly_options": has_weekly_options,
            },
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def apply_tiering(criteria: dict, config: dict) -> dict:
    """Pure function -- no I/O. Applies docs/screening-criteria.md's hard
    filters and near-miss bands to an already-computed criteria dict.
    `criteria` keys are all optional; a missing/None value for any
    criterion is treated as an unverified hard-filter failure, not a
    silent pass -- see the winrate/IV-RV precedent of never defaulting an
    unknown to "pass." A present-but-out-of-range value for
    combined_open_interest/atm_delta_abs/front_expiration_days is a
    distinct, correctly-labeled rejection reason, not just "_unverified"
    (a real gap caught during testing: this function originally only
    checked these three for None, never against their actual thresholds
    in config, so an out-of-range chain would have passed silently).
    """
    hard_fail: list[str] = []

    if criteria.get("price") is None:
        hard_fail.append("price_unverified")
    elif criteria["price"] < config["min_price"]:
        hard_fail.append("price_below_minimum")

    if criteria.get("term_structure") is None:
        hard_fail.append("term_structure_unverified")
    elif criteria["term_structure"] > config["min_term_structure"]:
        hard_fail.append("term_structure_insufficient")

    if criteria.get("expected_move_dollars") is None:
        hard_fail.append("expected_move_unverified")
    elif criteria["expected_move_dollars"] < config["min_expected_move_dollars"]:
        hard_fail.append("expected_move_below_minimum")

    if criteria.get("combined_open_interest") is None:
        hard_fail.append("combined_open_interest_unverified")
    elif criteria["combined_open_interest"] < config["min_combined_open_interest"]:
        hard_fail.append("combined_open_interest_below_minimum")

    if criteria.get("atm_delta_abs") is None:
        hard_fail.append("atm_delta_abs_unverified")
    elif criteria["atm_delta_abs"] > config["max_atm_delta_abs"]:
        hard_fail.append("atm_delta_abs_above_maximum")

    if criteria.get("front_expiration_days") is None:
        hard_fail.append("front_expiration_days_unverified")
    elif criteria["front_expiration_days"] > config["max_front_expiration_days"]:
        hard_fail.append("front_expiration_days_too_far_out")

    if not criteria.get("chain_complete"):
        hard_fail.append("chain_complete_unverified")

    if config.get("require_weekly_options", True):
        if criteria.get("has_weekly_options") is None:
            hard_fail.append("has_weekly_options_unverified")
        elif not criteria["has_weekly_options"]:
            hard_fail.append("no_weekly_options")

    near_miss: list[str] = []

    def _band(value, min_pass, min_near_miss, name):
        if value is None:
            near_miss.append(f"{name}_unknown")
            return
        if value >= min_pass:
            return
        if value >= min_near_miss:
            near_miss.append(name)
            return
        hard_fail.append(f"{name}_below_near_miss")

    _band(criteria.get("avg_volume"), config["min_avg_volume"], config["near_miss_min_avg_volume"], "avg_volume")
    _band(criteria.get("iv_rv_ratio"), config["min_iv_rv_ratio"], config["near_miss_min_iv_rv_ratio"], "iv_rv_ratio")
    _band(criteria.get("winrate"), config["min_winrate"], config["near_miss_min_winrate"], "winrate")

    if hard_fail:
        tier = "Reject"
    elif not near_miss:
        tier = "Tier 1"
    elif len(near_miss) == 1:
        tier = "Tier 2"
    else:
        tier = "Near Miss"

    return {"tier": tier, "hard_fail_reasons": hard_fail, "near_miss_reasons": near_miss}


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
    """Composite ranking score for a Tier 1/2 candidate, built entirely from
    signals apply_tiering already required to be present to reach that tier
    -- no new data, just combining what's already computed.

    Returns None if term_structure (the core signal) is missing; a
    candidate can't be ranked without it. IV/RV ratio and winrate are
    secondary confirmations of the same "is IV overpriced" question as
    term structure, not independent signals, so they're applied as
    multiplicative adjustments rather than summed as separate scores --
    summing would let a strong term structure and a merely-neutral IV/RV
    ratio look identical to two moderate signals combined, which isn't
    the intent (a strong core signal should still rank higher than two
    average ones).
    """
    ts = criteria.get("term_structure")
    if ts is None:
        return None
    iv_rv = criteria.get("iv_rv_ratio") or 1.0
    wr = _shrunk_winrate(criteria.get("winrate"), winrate_sample_size)
    return abs(ts) * iv_rv * wr


def rank_candidates(candidates: list[dict], config: dict) -> list[dict]:
    """Rank Tier 1/2 candidates from get_candidates' output by
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


def cmd_get_winrate(args) -> dict:
    config = _load_config()
    return compute_winrate(args.symbol.strip().upper(), config, args.lookback_quarters)


def cmd_get_calendar(args) -> dict:
    config = _load_config()
    source = config.get("earnings_calendar_source", "dolthub")
    if source != "dolthub":
        raise NotImplementedError(f"calendar source '{source}' not implemented — only 'dolthub' is wired up")
    rows = fetch_dolthub_calendar(args.date, config)
    return {"ok": True, "date": args.date, "source": source, "tickers": rows}


def cmd_get_candidates(args) -> dict:
    """Full tiered scan for a date: pulls the calendar, then for each symbol
    computes every criterion in docs/screening-criteria.md and tiers it via
    apply_tiering(). Price/term-structure/expected-move (criteria #1/#4/#6)
    require a live tastytrade session via tt.py (see `tt.py secrets_set`);
    OI/ATM-delta/expiration-window (part of #2/#3/#5/#7) are not computed by
    fetch_price_and_term_structure yet and always show up as `_unverified`.
    Volume, IV/RV, and winrate (#8/#10/#9) are real, live DoltHub-backed
    signals regardless of tt.py's credential status.
    """
    config = _load_config()
    calendar = fetch_dolthub_calendar(args.date, config)
    lookback = config.get("winrate_lookback_quarters", 8)

    candidates = []
    for entry in calendar:
        symbol = entry["symbol"]
        criteria: dict = {}

        broker = fetch_price_and_term_structure(symbol, entry["date"], entry["timing"], config)
        if broker.get("ok"):
            criteria.update(broker["criteria"])
        broker_error = None if broker.get("ok") else broker.get("error")

        criteria["avg_volume"] = fetch_avg_volume(symbol, config)

        ivrv = fetch_iv_rv_ratio(symbol, config)
        criteria["iv_rv_ratio"] = ivrv["iv_rv_ratio"] if ivrv.get("ok") else None

        winrate = compute_winrate(symbol, config, lookback)
        criteria["winrate"] = winrate["winrate"]
        winrate_sample_size = winrate["sample_size"]

        tiering = apply_tiering(criteria, config)

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
        "date": args.date,
        "candidates": candidates,
        "ranked": ranked,
        "selected": selection["selected"],
        "skipped_for_selection": selection["skipped"],
    }


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

    p_cand = sub.add_parser("get_candidates")
    p_cand.add_argument("--date", required=True)

    args = parser.parse_args()
    dispatch = {
        "get_calendar": cmd_get_calendar,
        "get_iv_rv": cmd_get_iv_rv,
        "get_winrate": cmd_get_winrate,
        "get_candidates": cmd_get_candidates,
    }
    result = dispatch[args.command](args)
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    main()
