"""Internal earnings-candidate scanner.

Implements the hard filters and tiering in docs/screening-criteria.md.
Term structure and expected move (criteria #4/#6) are computed live from
tastytrade option chains via tt.py. The earnings calendar, IV/RV ratio
(#10), and winrate backtest (#9) are all queried from three DoltHub
datasets served by one locally-running `dolt sql-server --data-dir`:
post-no-preference/earnings, post-no-preference/options, and
post-no-preference/stocks. get_candidates (tying every signal together
into a tiered scan across a day's calendar) is not implemented yet.

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
    """
    term_structure = (front_atm_iv - back_atm_iv) / back_atm_iv
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
    raise NotImplementedError(
        "for each symbol from get_calendar: pull front/back option chains via "
        "tt.py get_option_chain --include_greeks, call compute_term_structure(), "
        "call fetch_iv_rv_ratio() for criterion #10 and compute_winrate() for "
        "criterion #9, filter all against docs/screening-criteria.md's "
        "thresholds. Not implemented yet -- each signal works standalone "
        "(see get_iv_rv/get_winrate) but nothing ties them together into a "
        "tiered scan across the day's calendar."
    )


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
