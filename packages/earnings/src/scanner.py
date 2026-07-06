"""Internal earnings-candidate scanner (Tier A).

Replaces reliance on the external EarningsEdgeDetection project for the
signals that are cheap to compute from data we already have via tt.py:
term structure and expected move, both derived purely from a live option
chain (front-month vs. a later expiration's ATM implied vol).

Tier B (winrate, IV/RV ratio) needs a historical daily-price source and an
earnings-calendar source and is not implemented here yet — see
`cmd_get_candidates`'s NotImplementedError paths below.

Intended commands (see CLAUDE.md's Tool Reference):
  get_calendar --date MM/DD/YYYY          (stub — needs a calendar source)
  get_candidates --date MM/DD/YYYY        (Tier A signals only, per symbol)
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
    """Query a locally-running `dolt sql-server` for dolthub/earnings.

    Requires `dolt clone dolthub/earnings && cd earnings && dolt sql-server`
    running separately (see README's DoltHub setup notes) and
    `pip install mysql-connector-python`.

    NOTE: verify the earnings-repo's actual table/column names against your
    checked-out copy (`dolt sql -q "show tables"` / `describe <table>`)
    before relying on this query — schema below is unverified against a live
    instance and may need adjusting.
    """
    import mysql.connector

    conn = mysql.connector.connect(
        host=config.get("dolthub_host", "127.0.0.1"),
        port=config.get("dolthub_port", 3306),
        database=config.get("dolthub_database", "earnings"),
    )
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT act_symbol AS symbol, date, time "
            "FROM earnings_calendar WHERE date = %s",
            (date,),
        )
        return cur.fetchall()
    finally:
        conn.close()


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
        "filter against config's min_term_structure/min_expected_move_pct. "
        "Tier 2 (IV/RV ratio, winrate) requires a historical price/RV source "
        "and is not implemented yet — candidates here are Tier A only."
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p_cal = sub.add_parser("get_calendar")
    p_cal.add_argument("--date", required=True)

    p_cand = sub.add_parser("get_candidates")
    p_cand.add_argument("--date", required=True)

    args = parser.parse_args()
    dispatch = {
        "get_calendar": cmd_get_calendar,
        "get_candidates": cmd_get_candidates,
    }
    result = dispatch[args.command](args)
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    main()
