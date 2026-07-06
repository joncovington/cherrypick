"""Internal earnings-candidate scanner.

Implements the hard filters and tiering in docs/screening-criteria.md.
Term structure and expected move (criteria #4/#6) are computed live from
tastytrade option chains via tt.py. The earnings calendar and IV/RV ratio
(criteria #10) are queried from DoltHub's post-no-preference/earnings and
post-no-preference/options datasets respectively, via a locally-running
`dolt sql-server`. Winrate (#9) still needs a historical realized-move
backtest against post-no-preference/options's option_chain table and is
not implemented yet — see `cmd_get_candidates`'s NotImplementedError.

Intended commands (see CLAUDE.md's Tool Reference):
  get_calendar --date MM/DD/YYYY
  get_iv_rv --symbol X
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
        "call fetch_iv_rv_ratio() for criterion #10, filter all against "
        "docs/screening-criteria.md's thresholds. Winrate (#9) still needs a "
        "historical realized-move backtest against option_chain and is not "
        "implemented — every candidate caps at Tier 2 until it is."
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p_cal = sub.add_parser("get_calendar")
    p_cal.add_argument("--date", required=True)

    p_ivrv = sub.add_parser("get_iv_rv")
    p_ivrv.add_argument("--symbol", required=True)

    p_cand = sub.add_parser("get_candidates")
    p_cand.add_argument("--date", required=True)

    args = parser.parse_args()
    dispatch = {
        "get_calendar": cmd_get_calendar,
        "get_iv_rv": cmd_get_iv_rv,
        "get_candidates": cmd_get_candidates,
    }
    result = dispatch[args.command](args)
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    main()
