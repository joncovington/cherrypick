"""Item 31, the earnings module's paper rule (docs/edge-hypotheses-plan.md): the event list, the
coverage pilot, the end-of-day request files, the study, and the replay check on paper entries.

    python scripts/earnings_item31.py plan [--host H] [--port P] [--out FILE]
    python scripts/earnings_item31.py pilot [--out-dir DIR]
    python scripts/earnings_item31.py requests [--host H] [--port P] [--out-dir DIR]
    python scripts/earnings_item31.py study [--host H] [--port P] [--out-dir DIR]
    python scripts/earnings_item31.py report [--out-dir DIR]
    python scripts/earnings_item31.py replay-check [--requests-only] [--out-dir DIR]

`requests` and `study` are described in the "The study" section below. The pilot chose the end-of-day
basis (2022 and 2023 were under 90% complete intraday), so the study is the declaration's "end-of-day
fallback" for the whole window.

`plan` reads Dolt's `earnings_calendar` for the names the suite's liquidity verdict calls `tradable`
and writes the event list: reports with a stated timing ('After market close' or 'Before market
open') from 2020-01-22 to 2026-07-14, each with its entry session (an after-close report enters that
day, a pre-market report the session before). Counts only are printed.

`pilot` is the coverage test the declaration runs before anything else: 30 events a year for 2020-2025
drawn by a seeded hash of name and date (never by outcome), and for each the entry window (15:35 to
15:50 ET at 5 minutes) and one later session (09:40 to 15:55). It writes the request file for the
research queue (`fetch-intraday-chains`, packages/backtest) and, once the store holds the answers,
the coverage report and the basis decision. It reads row counts and whether each required bar came
back with a quote, a delta, an implied volatility and the underlying's price present. **It never
computes a price into a result, a P&L or a return**: a quote is tested for being there, never read.

**The basis rule, as declared:** if at least 90% of the pilot's requests are complete in every year,
the intraday basis is used for the whole study; if not, the end-of-day fallback is, for the whole
study. A request is complete when the vendor answered with rows and every required bar has at least
one contract with a bid, an ask, a delta, a positive implied volatility and an underlying price.

Choices the declaration leaves open, made here and stated:

- *The expiry* of a pilot request is the Friday on or after the reaction date (the Thursday when that
  Friday is a holiday): the weekly the module's own rule reaches for. The universe is the names with
  weekly options, so a request that comes back empty is a coverage miss and counts as one.
- *The later session* is the trading day after the entry session (the reaction session for both
  timings), asked at 09:40 to 15:55 at 5 minutes, ending 5 minutes before an early close.
- *Required bars:* the entry request's 15:35, 15:40 (the entry bar) and 15:50; the later request's
  first and last bar of its window.
- *Early-close entry sessions* (13:00 ET) have no 15:40 bar and are left out of the draw, counted.
- *Names* are the verdict's `names` block (158 on 2026-10-30), not its `reference` funds.
- *Windows* are cut by the report's date.

Everything written goes under the backtest data dir (results/research/item31/), never into the repo:
the inputs and everything derived from them are licensed.
"""

from __future__ import annotations

import argparse
import bisect
import contextlib
import hashlib
import json
import math
import random
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

from cherrypick.core import bs, chaindelta, fees, robustness
from cherrypick.core import calendar as cal
from cherrypick.core import stats as core_stats
from cherrypick.core.clock import ET

FIRST_REPORT = date(2020, 1, 22)
LAST_REPORT = date(2026, 7, 14)
DESIGN_END = date(2023, 12, 31)
AFTER_CLOSE, BEFORE_OPEN = "After market close", "Before market open"
TIMINGS = {AFTER_CLOSE: "after_close", BEFORE_OPEN: "before_open"}

SEED = 31
PILOT_YEARS = tuple(range(2020, 2026))
PILOT_PER_YEAR = 30
THRESHOLD = 0.90
INTERVAL = "5m"
ENTRY_WINDOW = ("15:35", "15:50")
ENTRY_BARS = ("15:35", "15:40", "15:50")
LATER_START, LATER_END = "09:40", "15:55"
#: A refusal that says nothing about the vendor's coverage (the run could not ask): the request is
#: not answered, the decision waits for a re-run.
NOT_AN_ANSWER = ("unauthenticated", "session_in_use", "library_missing", "no_key", "transport_not_built",
                 "unknown_vendor")  # fmt: skip


# ------------------------------------------------------------------------------------------------
# Pure: the events.


def entry_session(report: date, timing: str) -> date:
    """After the close: that day. Before the open: the session before."""
    return report if timing == "after_close" else cal.previous_trading_day(report)


def reaction_session(report: date, timing: str) -> date:
    """The first session that trades the news: the next one after an after-close report, the report
    date itself before the open."""
    return cal.next_trading_day(report) if timing == "after_close" else report


def event_row(symbol: str, report: date, timing: str) -> dict:
    entry = entry_session(report, timing)
    return {
        "symbol": symbol,
        "report_date": report.isoformat(),
        "timing": timing,
        "entry_session": entry.isoformat(),
        "reaction_session": reaction_session(report, timing).isoformat(),
        "early_close_entry": cal.is_early_close(entry),
    }


def window_of(report: date) -> str:
    return "design" if report <= DESIGN_END else "holdout"


def summarise(events: list[dict], no_timing: int) -> dict:
    """Counts only: by window and timing, by year, and what is dropped or set aside."""
    by = Counter((window_of(date.fromisoformat(e["report_date"])), e["timing"]) for e in events)
    years = Counter(e["report_date"][:4] for e in events)
    return {
        "events": len(events),
        "names": len({e["symbol"] for e in events}),
        "design": {t: by[("design", t)] for t in TIMINGS.values()},
        "holdout": {t: by[("holdout", t)] for t in TIMINGS.values()},
        "by_year": dict(sorted(years.items())),
        "no_stated_timing_dropped": no_timing,
        "early_close_entry": sum(1 for e in events if e["early_close_entry"]),
    }


# ------------------------------------------------------------------------------------------------
# Pure: the pilot draw and its requests.


def draw_key(seed: int, symbol: str, report_date: str) -> str:
    """The rank of an event in the draw: a hash of the seed, the name and the date, nothing else.
    No price, outcome or later field reaches it."""
    return hashlib.sha256(f"{seed}|{symbol}|{report_date}".encode()).hexdigest()


def pilot_draw(events: list[dict], *, seed: int = SEED, per_year: int = PILOT_PER_YEAR,
               years: tuple[int, ...] = PILOT_YEARS) -> list[dict]:  # fmt: skip
    """`per_year` events from each year by report date: the lowest hashes of (seed, name, date).
    An early-close entry session is not in the pool (no bar at 15:40). A year with fewer events
    gives all it has."""
    pool: dict[int, list[dict]] = defaultdict(list)
    for e in events:
        y = int(e["report_date"][:4])
        if y in years and not e["early_close_entry"]:
            pool[y].append(e)
    out: list[dict] = []
    for y in sorted(pool):
        ranked = sorted(pool[y], key=lambda e: draw_key(seed, e["symbol"], e["report_date"]))
        out.extend(sorted(ranked[:per_year], key=lambda e: (e["report_date"], e["symbol"])))
    return out


def weekly_expiry(reaction: date) -> date:
    """The Friday on or after the reaction session, the Thursday when that Friday is a holiday."""
    fri = reaction + timedelta(days=(4 - reaction.weekday()) % 7)
    return fri if cal.is_trading_day(fri) else cal.previous_trading_day(fri)


def later_window(session: date) -> tuple[str, str]:
    """The later session's window: 09:40 to 15:55, ending 5 minutes before an early close."""
    if cal.is_early_close(session):
        hh, mm = cal.session_close_hhmm(session).split(":")
        end = datetime(2000, 1, 1, int(hh), int(mm)) - timedelta(minutes=5)
        return LATER_START, end.strftime("%H:%M")
    return LATER_START, LATER_END


def requests_for(event: dict) -> list[dict]:
    """The pilot's two requests for an event: the entry window, and the next session's day. Each
    carries the bars it must return to count as complete."""
    expiry = weekly_expiry(date.fromisoformat(event["reaction_session"])).isoformat()
    later = cal.next_trading_day(date.fromisoformat(event["entry_session"]))
    start, end = later_window(later)
    label = f"{event['symbol']} {event['report_date']}"
    base = {"symbol": event["symbol"], "expiration": expiry, "interval": INTERVAL,
            "event": label, "year": int(event["report_date"][:4])}  # fmt: skip
    return [
        {**base, "kind": "entry", "session": event["entry_session"], "start": ENTRY_WINDOW[0],
         "end": ENTRY_WINDOW[1], "required": list(ENTRY_BARS)},
        {**base, "kind": "later", "session": later.isoformat(), "start": start, "end": end,
         "required": [start, end]},
    ]  # fmt: skip


def pilot_requests(events: list[dict], **kw) -> list[dict]:
    return [r for e in pilot_draw(events, **kw) for r in requests_for(e)]


# ------------------------------------------------------------------------------------------------
# Pure: the basis rule.


def basis(per_year: dict, threshold: float = THRESHOLD) -> str:
    """`intraday` when every year has requests and at least `threshold` of them are complete, else
    `end_of_day`. `per_year[year]` is {"requests": n, "complete": k}. One basis for all years."""
    if not per_year:
        return "end_of_day"
    for counts in per_year.values():
        if counts["requests"] == 0 or counts["complete"] / counts["requests"] < threshold:
            return "end_of_day"
    return "intraday"


# ------------------------------------------------------------------------------------------------
# The coverage report: counts and presence, never a price.

_BAR_PRESENT = (
    "SELECT 1 FROM intraday_chain_quotes WHERE symbol = ? AND session = ? AND expiration = ? AND bar = ? "
    "AND bid IS NOT NULL AND ask IS NOT NULL AND delta IS NOT NULL AND iv IS NOT NULL AND iv > 0 "
    "AND underlying IS NOT NULL LIMIT 1"
)


def judge(conn: sqlite3.Connection, req: dict) -> dict:
    """What became of one request: its status, rows and bars as recorded, and for an answered one the
    required bars that did not come back whole. Presence only."""
    sym, day, exp = req["symbol"].upper(), req["session"], req["expiration"]
    row = conn.execute(
        "SELECT status, rows, bars, detail FROM intraday_chain_requests WHERE symbol = ? AND session = ? "
        'AND expiration = ? AND start = ? AND "end" = ? AND interval = ?',
        (sym, day, exp, req["start"], req["end"], req["interval"]),
    ).fetchone()
    if row is None:
        return {"state": "unanswered", "rows": 0, "bars": 0}
    status, rows, bars, detail = row
    if status == "refused":
        reason = (detail or "").split(":")[0] or "unknown"
        state = "unanswered" if reason in NOT_AN_ANSWER else "refused"
        return {"state": state, "rows": 0, "bars": 0, "reason": reason}
    if status == "empty":
        return {"state": "empty", "rows": 0, "bars": 0}
    missing = [
        b for b in req["required"] if conn.execute(_BAR_PRESENT, (sym, day, exp, b)).fetchone() is None
    ]
    return {"state": "incomplete" if missing else "complete", "rows": rows, "bars": bars, "missing": missing}


def coverage(conn: sqlite3.Connection, requests: list[dict]) -> dict:
    """The pilot's coverage by year and kind, the decision, and nothing priced."""
    years: dict[int, dict] = {}
    missing_bars: Counter = Counter()
    reasons: Counter = Counter()
    for req in requests:
        j = judge(conn, req)
        y = years.setdefault(
            req["year"],
            {"requests": 0, "complete": 0, "incomplete": 0, "empty": 0, "refused": 0, "unanswered": 0,
             "rows": 0, "bars": 0, "by_kind": {}},
        )  # fmt: skip
        y["requests"] += 1
        y[j["state"]] += 1
        y["rows"] += j["rows"]
        y["bars"] += j["bars"]
        k = y["by_kind"].setdefault(req["kind"], {"requests": 0, "complete": 0})
        k["requests"] += 1
        k["complete"] += j["state"] == "complete"
        for b in j.get("missing", []):
            missing_bars[f"{req['kind']} {b}"] += 1
        if j.get("reason"):
            reasons[j["reason"]] += 1
    for y in years.values():
        y["share_complete"] = round(y["complete"] / y["requests"], 4) if y["requests"] else None
    unanswered = sum(y["unanswered"] for y in years.values())
    out = {
        "requests": sum(y["requests"] for y in years.values()),
        "unanswered": unanswered,
        "years": {str(k): v for k, v in sorted(years.items())},
        "missing_required_bars": dict(missing_bars),
        "refusal_reasons": dict(reasons),
    }
    if unanswered:
        out.update(
            decided=False,
            basis=None,
            note=f"{unanswered} requests unanswered: fetch them, then run pilot again",
        )
    else:
        out.update(decided=True, basis=basis({y: v for y, v in years.items()}))
    return out


# ------------------------------------------------------------------------------------------------
# Dolt and the files.


def _out_dir(override: str | None = None) -> Path:
    if override:
        d = Path(override)
    else:
        from cherrypick.backtest import paths

        d = paths.results_dir() / "research" / "item31"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _spellings(symbol: str) -> set[str]:
    return {symbol, symbol.replace("/", "."), symbol.replace(".", "/")}


def universe_names() -> tuple[list[str], str]:
    """The `names` the liquidity verdict calls tradable, and the file's `updated_on`."""
    from cherrypick.earnings import universe

    verdict = universe.load()
    if verdict.get("error"):
        raise SystemExit(f"liquidity verdict unavailable: {verdict['error']}")
    names = sorted(s for s, v in verdict["names"].items() if (v or {}).get("verdict") == universe.TRADABLE)
    return names, str(verdict.get("updated_on"))


def dolt_rows(names: list[str], host: str, port: int) -> list[tuple[str, date, str | None]]:
    import mysql.connector

    spelled = {sp: n for n in names for sp in _spellings(n)}
    conn = mysql.connector.connect(
        host=host, port=port, user="root", database="earnings", connection_timeout=10
    )
    try:
        cur = conn.cursor()
        marks = ",".join(["%s"] * len(spelled))
        cur.execute(
            f"SELECT act_symbol, date, `when` FROM earnings_calendar WHERE date BETWEEN %s AND %s "
            f"AND act_symbol IN ({marks})",
            (FIRST_REPORT.isoformat(), LAST_REPORT.isoformat(), *spelled),
        )
        return [(spelled[s], d, w) for s, d, w in cur.fetchall()]
    finally:
        conn.close()


def build_events(rows: list[tuple[str, date, str | None]]) -> tuple[list[dict], int]:
    """(events, rows with no stated timing). One event per name and date."""
    seen: dict[tuple[str, date], str] = {}
    no_timing = 0
    for symbol, d, when in rows:
        timing = TIMINGS.get(when or "")
        if timing is None:
            no_timing += 1
            continue
        seen.setdefault((symbol, d), timing)
    events = [event_row(s, d, t) for (s, d), t in sorted(seen.items(), key=lambda kv: (kv[0][1], kv[0][0]))]
    return events, no_timing


def cmd_plan(args) -> int:
    names, updated = universe_names()
    events, no_timing = build_events(dolt_rows(names, args.host, args.port))
    counts = summarise(events, no_timing)
    counts["universe_names"] = len(names)
    counts["verdict_updated_on"] = updated
    out = Path(args.out) if args.out else _out_dir() / "events.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {"generated": datetime.now().isoformat(timespec="seconds"), "counts": counts, "events": events}
        ),
        encoding="utf-8",
    )
    print(json.dumps(counts, indent=2))
    print(f"wrote {out}")
    return 0


def cmd_pilot(args) -> int:
    from cherrypick.backtest import intradayfetch, settings

    d = _out_dir(args.out_dir)
    events = json.loads((d / "events.json").read_text(encoding="utf-8"))["events"]
    requests = pilot_requests(events)
    req_path = d / "pilot-requests.json"
    req_path.write_text(
        json.dumps({"seed": SEED, "per_year": PILOT_PER_YEAR, "requests": requests}), encoding="utf-8"
    )
    print(f"{len(requests)} pilot requests ({len(requests) // 2} events) in {req_path}")
    store = intradayfetch.db_path(settings.load(None))
    if not store.exists():
        print("no intraday store yet: queue the fetch, then run pilot again")
        return 0
    conn = sqlite3.connect(f"file:{store.as_posix()}?mode=ro", uri=True)
    try:
        report = coverage(conn, requests)
    finally:
        conn.close()
    report.update(
        rule=f"intraday if at least {THRESHOLD:.0%} complete in every year, else end_of_day",
        threshold=THRESHOLD,
    )
    (d / "pilot.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("requests", "unanswered", "decided", "basis")}))
    return 0


# ================================================================================================
# THE STUDY (end-of-day basis, chosen by the pilot's declared rule).
#
# What the declaration leaves open on this basis, and the reading taken (each is a limit, stated
# again in results.md):
#
# - *The underlying* is Dolt's `stocks.ohlcv` close of the session; the end-of-day chain rows carry
#   no underlying, no delta and no implied volatility. Forward (put-call parity at the strike whose
#   call and put mids are closest, zero rate), implied volatility and delta are computed from each
#   chain's own mids (`core.bs`, `core.chaindelta.forward_from_parity`), the quotes standing at the
#   close, so the module's screens read the numbers they were written for.
# - *The module's own builders run*, not copies: `fetch_price_and_term_structure`, `apply_tiering`,
#   `fetch_<strategy>_order`, `sizing.compute_position_size`, `management.evaluate`,
#   `management.execution_gate`, `scanner.compute_generic_exit_debit` (through `exit_debit_bounds`),
#   `costs`/`fees`. Only the readers that do I/O are replaced, by `FakeTT` over the store: `call_tt`,
#   `fetch_iv_rv_ratio`, `compute_winrate`, the exit-basis reader, and the strategies' `date` (so that
#   "today" is the entry session). Nothing in the module is edited.
# - *The front expiry* is the nearest weekly Friday (the Thursday when that Friday is a holiday) on or
#   after the reaction date, the pilot's reading; the store holds only the expiries asked for, so
#   Monday and Wednesday listings are not seen. *The back expiry* is the module's own rule over the
#   same weekly list (the nearest third-Friday monthly at least 21 days after the front).
# - *Open interest* is not in the end-of-day store, so the 2,000-contract floor is not reproduced
#   (the threshold is set to 0 in the study's copy of the file; the front chain's day volume is kept
#   on each trade). The combined option volume floor stays unreproduced, as declared. The weekly test
#   is "the front chain came back with rows".
# - *The realised-move dispersion ceiling* (the module's 0.20 / 0.25) is not in the declaration's
#   admission list, so it is not applied; each trade carries its dispersion and the result reports
#   the arm split by it.
# - *The 50-session dollar volume* is the median of close x volume over the 50 sessions before the
#   entry session; the *30-day average volume* is the mean over the 30 sessions before it (the entry
#   session's own volume is partial at 15:40 and is never read).
# - *The IV/RV ratio* is the module's own rule (the first non-null pair of the last five rows) over
#   rows dated strictly before the entry session.
# - *Marks* are one per later session, at 16:00 ET whatever the session (an early close is still the
#   day's last quote), so the expiry close (from 15:30) fires on an expiry day's mark and the PEAD gate
#   is the first and only mark of each later session. A close the spread gate refuses waits for the
#   next session's mark, except the expiry close; the module's 30-minute patience is within a session
#   and has no meaning here.
# - *A leg with no two-sided quote at a later mark* is marked at its last earlier quote in the trade
#   (counted); on its expiry day, at intrinsic against the stock's close. An expiry day with no rows at
#   all settles every leg at intrinsic ($5 a distinct in-the-money symbol, `fees.ic_expire_fee`).
# - *A split* (the close moving by a factor under 0.52 or over 1.9 between two sessions of the trade)
#   ends the trade at the session before, counted; a split on the first later session drops it.
# - *Readings* that change only the fill (mid with fees only, natural side, the stress) re-price the
#   managed path's own entry and exit; management itself is decided on the working price.

STRATEGIES = ("iron_fly", "iron_condor", "directional_credit_spread")
GATES = ("", "g1", "g2")


def arm_name(strategy: str, gate: str = "") -> str:
    return f"{strategy}+{gate}" if gate else strategy


ARMS = tuple(arm_name(s, g) for s in STRATEGIES for g in GATES)
TIMING_TEXT = {"after_close": AFTER_CLOSE, "before_open": BEFORE_OPEN}

DESIGN = (FIRST_REPORT, DESIGN_END)
HOLDOUT = (date(2024, 1, 2), LAST_REPORT)
HALF_END = date(2021, 12, 31)  # the first time-half ends here; the second starts 2022-01-03
COVID = (date(2020, 2, 1), date(2020, 6, 30))
MIN_TRADES = {"design": 150, "holdout": 100}
CONTROL_SHIFTS = (-28, 28, -35)  # calendar days: four weeks earlier, four weeks later, five weeks earlier
CONTROL_LEAD_SESSIONS = 3
HORIZON_DAYS = 130  # the weekly list the fake broker offers from an entry session
G1_MULTIPLE, G1_PRIOR, G1_MIN_PRIOR = 1.2, 8, 4
G2_SHARE = 0.25
T_BAR = 2.0
ALPHA = 0.05
RESAMPLES = 2000
BEST_REMOVED = 5
NAME_SHARE_CAP = 0.08
SPLIT_RATIO = (0.52, 1.9)
MIN_DOLLAR_VOLUME = 100e6  # technicals' tradable.MIN_DOLLAR_VOLUME (a test holds them equal)
PIT_MAX_SPREAD_PCT = 0.075
PIT_DOLLAR_SESSIONS, AVG_VOLUME_SESSIONS = 50, 30
STRESS = {"fees": 1.5, "slippage_frac_of_spread": 0.25}
REPLAY_MIN_EVENTS, REPLAY_MIN_SHARE, REPLAY_TICK = 20, 0.90, 0.01
PAPER_FIRST, PAPER_LAST = date(2026, 7, 15), date(2026, 10, 23)
EXIT_READINGS = ("managed", "first_morning", "held_to_expiry", "no_pead")
FILL_READINGS = ("working", "mid_fees", "natural", "stress")

# The deployed earnings.json at the declaration date (2026-10-30), copied so that a later edit cannot
# move the test. The open interest floor is the one value that differs (see above).
DEFAULTS = {
    "min_price": 10.0,
    "min_term_structure": -0.004,
    "require_weekly_options": True,
    "min_combined_open_interest": 0,  # deployed: 2000; not in the end-of-day store
    "max_bid_ask_spread_pct": 0.15,
    "min_market_cap": 2_000_000_000,
    "near_miss_min_market_cap": 1_000_000_000,
    "min_combined_option_volume": 500,
    "near_miss_min_combined_option_volume": 200,
    "min_avg_volume": 1_500_000,
    "near_miss_min_avg_volume": 1_000_000,
    "min_iv_rv_ratio": 1.25,
    "near_miss_min_iv_rv_ratio": 1.0,
    "min_winrate": 0.5,
    "near_miss_min_winrate": 0.4,
}
_WING = {
    "max_risk_per_trade_pct": 0.05,
    "max_front_expiration_days": 9,
    "wing_width_credit_multiple": 3.0,
    "wing_width_multiple_low": 2.5,
    "wing_width_multiple_mid": 3.0,
    "wing_width_multiple_high": 3.5,
    "wing_width_band_low_max": 1.25,
    "wing_width_band_mid_max": 1.75,
    "profit_target_pct": 0.5,
    "stop_loss_credit_multiple": 1.5,
    "exit_after_announcement_minutes": 240,
}
THRESHOLDS = {
    "iron_fly": {
        **_WING,
        "max_atm_delta_abs": 0.57,
        "min_expected_move_dollars": 0.9,
        "max_realized_move_dispersion_pct": 0.2,
    },  # fmt: skip
    "iron_condor": {**_WING, "min_expected_move_pct": 0.04, "max_realized_move_dispersion_pct": 0.25},
    "directional_credit_spread": {
        **_WING,
        "max_realized_move_dispersion_pct": 0.25,
        "min_expected_move_pct": 0.04,
        "min_skew_abs": 0.02,
        "skew_delta_target": 0.25,
    },
}
SCREEN = {  # the deployed symbol_screen, with the option volume floor off (declared limit)
    "avg_volume": "pass",
    "combined_option_volume": "off",
    "winrate": "off",
    "iv_rv_ratio": "off",
    "market_cap": "off",
}
COSTS = {
    "commission_open_per_contract": 1.0,
    "commission_close_per_contract": 0.0,
    "commission_cap_per_leg": 10.0,
    "clearing_fee_per_contract": 0.1,
    "regulatory_fee_per_contract": 0.04,
    "slippage_frac_of_spread": 0.125,
    "slippage_cap_frac_of_mid": 0.15,
}
CAPITAL = 100_000
MAX_CONTRACTS = 1


def study_config() -> dict:
    """The full config the module's functions are handed: each strategy's block is the deployed one
    merged over the defaults, with the screen levels riding on it as the module's loader does."""
    return {
        "available_capital_paper_mode": CAPITAL,
        "max_contracts_per_leg": MAX_CONTRACTS,
        "strat_test_portfolio": "per_strategy",
        "tastytrade_costs": dict(COSTS),
        "symbol_screen": dict(SCREEN),
        "winrate_lookback_quarters": 8,
        "management": {},
        "strategies": {
            name: {**DEFAULTS, **own, "_symbol_screen": dict(SCREEN)} for name, own in THRESHOLDS.items()
        },
    }


def stress_config(cfg: dict) -> dict:
    """The stress: fees at 1.5x (commission, its cap and the pass-throughs) and the haircut at 25% of
    the spread. The cap on a leg's slippage (15% of its mid) is left as it is."""
    c = dict(COSTS)
    for k in ("commission_open_per_contract", "commission_close_per_contract", "commission_cap_per_leg",
              "clearing_fee_per_contract", "regulatory_fee_per_contract"):  # fmt: skip
        c[k] = COSTS[k] * STRESS["fees"]
    c["slippage_frac_of_spread"] = STRESS["slippage_frac_of_spread"]
    return {**cfg, "tastytrade_costs": c}


# ------------------------------------------------------------------------------------------------
# Pure: expiries.


def reaction_date(report: date, timing: str) -> date:
    """The module's `scanner.reaction_date`: the next calendar day after an after-close report."""
    return report + timedelta(days=1) if timing == "after_close" else report


def weekly_candidates(start: date, days: int = HORIZON_DAYS) -> list[date]:
    """The weekly expiries from `start` on: each Friday, the Thursday when it is a holiday."""
    out: list[date] = []
    fri = start + timedelta(days=(4 - start.weekday()) % 7)
    while fri <= start + timedelta(days=days):
        d = fri if cal.is_trading_day(fri) else cal.previous_trading_day(fri)
        if d >= start and d not in out:
            out.append(d)
        fri += timedelta(days=7)
    return out


def front_expiry(report: date, timing: str, entry: date) -> date | None:
    """The nearest weekly on or after the reaction date (the module's `select_front_expiration`)."""
    r = reaction_date(report, timing)
    ok = [e for e in weekly_candidates(entry) if e >= r]
    return min(ok) if ok else None


def _is_monthly(d: date) -> bool:
    return d.weekday() == 4 and 15 <= d.day <= 21


def back_expiry(front: date, candidates: list[date], min_days: int = 21) -> date | None:
    """The module's `select_back_expiration`: the nearest monthly at least `min_days` after the front,
    else the nearest expiry at least that far."""
    far = [e for e in candidates if (e - front).days >= min_days]
    monthly = [e for e in far if _is_monthly(e)]
    pool = monthly or far
    return min(pool) if pool else None


def event_dates(event: dict) -> dict:
    """The dates an event needs chains for: the entry session, the front and back expiries."""
    entry = date.fromisoformat(event["entry_session"])
    report = date.fromisoformat(event["report_date"])
    front = front_expiry(report, event["timing"], entry)
    back = back_expiry(front, weekly_candidates(entry)) if front else None
    return {"entry": entry, "report": report, "front": front, "back": back}


# ------------------------------------------------------------------------------------------------
# Pure: the paired control.


def control_for(event: dict, reports: list[date], span: tuple[date, date]) -> dict | None:
    """The control session for an event: the same weekday four weeks earlier, kept only if the
    calendar lists no report for the name from three sessions before that session through the
    control's expiry; else four weeks later under the same test; else five weeks earlier; else None.

    `reports` is every report date the calendar lists for the name, whatever its timing (a blank
    timing is still a report). `span` is the calendar's first and last row: a window the calendar
    does not cover cannot be shown clear, so it is refused. The control's report date shifts with
    its session, so the expiry rule yields the event's days to expiry."""
    entry = date.fromisoformat(event["entry_session"])
    report = date.fromisoformat(event["report_date"])
    timing = event["timing"]
    for shift in CONTROL_SHIFTS:
        c_entry, c_report = entry + timedelta(days=shift), report + timedelta(days=shift)
        if not cal.is_trading_day(c_entry):
            continue
        if timing == "before_open" and not (
            cal.is_trading_day(c_report) and cal.previous_trading_day(c_report) == c_entry
        ):
            continue
        if timing == "after_close" and c_report != c_entry:
            continue
        c_front = front_expiry(c_report, timing, c_entry)
        if c_front is None:
            continue
        lead = c_entry
        for _ in range(CONTROL_LEAD_SESSIONS):
            lead = cal.previous_trading_day(lead)
        if lead < span[0] or c_front > span[1]:
            continue
        if any(lead <= r <= c_front for r in reports):
            continue
        return {
            "shift_days": shift,
            "entry_session": c_entry.isoformat(),
            "report_date": c_report.isoformat(),
            "timing": timing,
            "front_expiration": c_front.isoformat(),
        }
    return None


# ------------------------------------------------------------------------------------------------
# Pure: the request files.


def request_plan(
    events: list[dict], reports_by_symbol: dict[str, list[date]], span: tuple[date, date]
) -> dict:
    """The chains the study reads, before the store is looked at: for each event the front expiry's
    path from the entry session through the expiry (the entry chain, every later mark and the expiry
    day) and the back expiry's entry chain; for each event's control the front path. Returns
    {"singles": [...], "paths": [...], "no_control": n} with each request once."""
    singles: dict[tuple, None] = {}
    paths: dict[tuple, None] = {}
    no_control = 0
    for e in events:
        sym = e["symbol"].upper()
        d = event_dates(e)
        if d["front"] is None:
            continue
        paths.setdefault((sym, d["front"].isoformat(), d["entry"].isoformat(), d["front"].isoformat()), None)
        if d["back"] is not None:
            singles.setdefault((sym, d["entry"].isoformat(), d["back"].isoformat()), None)
        c = control_for(e, reports_by_symbol.get(e["symbol"], []), span)
        if c is None:
            no_control += 1
            continue
        paths.setdefault((sym, c["front_expiration"], c["entry_session"], c["front_expiration"]), None)
    return {"singles": list(singles), "paths": list(paths), "no_control": no_control}


def sessions_between(a: date, b: date) -> list[date]:
    out, d = [], a if cal.is_trading_day(a) else cal.next_trading_day(a)
    while d <= b:
        out.append(d)
        d = cal.next_trading_day(d)
    return out


def stored_requests(conn: sqlite3.Connection) -> dict:
    """What the end-of-day store already answers (complete or empty): singles by key, whole paths by
    key, and each name-expiry's answered sessions, so a path whose sessions are all answered one by
    one counts as stored."""
    singles = {
        tuple(r)
        for r in conn.execute(
            "SELECT symbol, session, expiration FROM eod_chain_requests WHERE status IN ('complete', 'empty')"
        )
    }
    paths = {
        tuple(r)
        for r in conn.execute(
            "SELECT symbol, expiration, start, through FROM eod_chain_paths "
            "WHERE status IN ('complete', 'empty')"
        )
    }
    return {"singles": singles, "paths": paths}


def minus_stored(plan: dict, stored: dict) -> dict:
    """The plan less what is stored. A path is stored when the store holds a path for the same name and
    expiry covering it, or answers every session of it one by one."""
    singles = [k for k in plan["singles"] if k not in stored["singles"]]
    todo = []
    for sym, exp, start, through in plan["paths"]:
        if any(p[0] == sym and p[1] == exp and p[2] <= start and p[3] >= through for p in stored["paths"]):
            continue
        days = [
            d.isoformat() for d in sessions_between(date.fromisoformat(start), date.fromisoformat(through))
        ]
        if days and all((sym, d, exp) in stored["singles"] for d in days):
            continue
        todo.append((sym, exp, start, through))
    return {"singles": singles, "paths": todo, "no_control": plan["no_control"]}


def request_files(todo: dict) -> tuple[dict, dict]:
    """The two files `fetch-chains` and `fetch-chain-paths` read."""
    chains = {"requests": [{"symbol": s, "session": d, "expiration": e} for s, d, e in todo["singles"]]}
    paths = {
        "requests": [{"symbol": s, "expiration": e, "start": a, "through": b} for s, e, a, b in todo["paths"]]
    }
    return chains, paths


def cmd_requests(args) -> int:
    from cherrypick.backtest import chainfetch, settings

    d = _out_dir(args.out_dir)
    events = json.loads((d / "events.json").read_text(encoding="utf-8"))["events"]
    names = sorted({e["symbol"] for e in events})
    rows = dolt_all_reports(names, args.host, args.port)
    cal_doc = calendar_document(rows)
    (d / "calendar.json").write_text(json.dumps(cal_doc), encoding="utf-8")
    reports, span = reports_from_document(cal_doc)
    plan = request_plan(events, reports, span)
    store = chainfetch.db_path(settings.load(None))
    stored = {"singles": set(), "paths": set()}
    if store.exists():
        conn = sqlite3.connect(f"file:{store.as_posix()}?mode=ro", uri=True)
        try:
            stored = stored_requests(conn)
        finally:
            conn.close()
    todo = minus_stored(plan, stored)
    chains, paths = request_files(todo)
    (d / "eod-chain-requests.json").write_text(json.dumps(chains), encoding="utf-8")
    (d / "eod-path-requests.json").write_text(json.dumps(paths), encoding="utf-8")
    out = {
        "events": len(events),
        "planned_singles": len(plan["singles"]),
        "planned_paths": len(plan["paths"]),
        "stored_singles_skipped": len(plan["singles"]) - len(todo["singles"]),
        "stored_paths_skipped": len(plan["paths"]) - len(todo["paths"]),
        "chain_requests": len(todo["singles"]),
        "path_requests": len(todo["paths"]),
        "total_requests": len(todo["singles"]) + len(todo["paths"]),
        "events_without_a_control": plan["no_control"],
        "files": [str(d / "eod-chain-requests.json"), str(d / "eod-path-requests.json")],
    }
    print(json.dumps(out, indent=2))
    return 0


def dolt_all_reports(names: list[str], host: str, port: int) -> list[tuple[str, date, str | None]]:
    """Every calendar row for the names, whatever its timing, from the first row on: the control's
    test needs a blank-timing report to count as a report."""
    import mysql.connector

    spelled = {sp: n for n in names for sp in _spellings(n)}
    conn = mysql.connector.connect(
        host=host, port=port, user="root", database="earnings", connection_timeout=10
    )
    try:
        cur = conn.cursor()
        marks = ",".join(["%s"] * len(spelled))
        cur.execute(
            f"SELECT act_symbol, date, `when` FROM earnings_calendar WHERE act_symbol IN ({marks})",
            tuple(spelled),
        )
        return [(spelled[s], d, w) for s, d, w in cur.fetchall()]
    finally:
        conn.close()


def calendar_document(rows: list[tuple[str, date, str | None]]) -> dict:
    """calendar.json: every report row per name, and the span the calendar covers."""
    by: dict[str, list] = defaultdict(list)
    for s, d, w in rows:
        by[s].append([d.isoformat(), w])
    for v in by.values():
        v.sort()
    firsts = [v[0][0] for v in by.values() if v]
    lasts = [v[-1][0] for v in by.values() if v]
    return {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "span": [min(firsts), max(lasts)] if firsts else None,
        "reports": dict(by),
    }


def reports_from_document(doc: dict) -> tuple[dict[str, list[date]], tuple[date, date]]:
    reports = {s: [date.fromisoformat(d) for d, _ in v] for s, v in doc["reports"].items()}
    span = (date.fromisoformat(doc["span"][0]), date.fromisoformat(doc["span"][1]))
    return reports, span


# ------------------------------------------------------------------------------------------------
# Inputs: Dolt's bars and volatility history, and the end-of-day chain store.


class MarketData:
    """Dolt's `stocks.ohlcv` (close, volume) and `options.volatility_history` for the names, held in
    memory. A test builds one from dicts; `load_market_data` fills it from Dolt."""

    def __init__(self, bars: dict[str, dict[date, tuple[float, float]]], ivrv: dict[str, list] | None = None):
        self.bars = bars
        self.ivrv = ivrv or {}
        self._days: dict[str, list[date]] = {s: sorted(b) for s, b in bars.items()}

    def close(self, symbol: str, day: date) -> float | None:
        row = self.bars.get(symbol, {}).get(day)
        return row[0] if row and row[0] else None

    def before(self, symbol: str, day: date, n: int) -> list[tuple[float, float]]:
        """(close, volume) of the n sessions strictly before `day`, oldest first."""
        days = self._days.get(symbol, [])
        i = bisect.bisect_left(days, day)
        return [self.bars[symbol][d] for d in days[max(0, i - n) : i]]

    def avg_volume(self, symbol: str, day: date) -> float | None:
        rows = self.before(symbol, day, AVG_VOLUME_SESSIONS)
        return statistics.fmean(v for _, v in rows) if len(rows) == AVG_VOLUME_SESSIONS else None

    def median_dollar_volume(self, symbol: str, day: date) -> float | None:
        rows = self.before(symbol, day, PIT_DOLLAR_SESSIONS)
        return statistics.median(c * v for c, v in rows) if len(rows) == PIT_DOLLAR_SESSIONS else None

    def iv_rv(self, symbol: str, day: date) -> dict:
        """The module's `fetch_iv_rv_ratio` over rows dated strictly before `day`: the first non-null,
        non-zero-hv pair among the last five."""
        rows = [r for r in self.ivrv.get(symbol, []) if r[0] < day][-5:]
        for _, hv, iv in reversed(rows):
            if hv is not None and iv is not None and float(hv) != 0:
                return {"ok": True, "iv_rv_ratio": float(iv) / float(hv)}
        return {"ok": False, "error": "no non-null iv/hv pair in the last 5 trading days"}


def load_market_data(names: list[str], host: str, port: int) -> MarketData:
    """One query each for the bars and the volatility history. `ohlcv` leads its key with the date, so
    a name-by-name query walks the whole table; one `IN` query over a bounded date range does not."""
    import mysql.connector

    spelled = {sp: n for n in names for sp in _spellings(n)}
    marks = ",".join(["%s"] * len(spelled))
    start = (FIRST_REPORT - timedelta(days=150)).isoformat()
    bars: dict[str, dict] = defaultdict(dict)
    ivrv: dict[str, list] = defaultdict(list)
    where = f"WHERE date >= %s AND act_symbol IN ({marks})"
    for database, sql, sink in (
        ("stocks", f"SELECT act_symbol, date, close, volume FROM ohlcv {where}", "b"),
        ("options", f"SELECT act_symbol, date, hv_current, iv_current FROM volatility_history {where}", "v"),
    ):
        conn = mysql.connector.connect(
            host=host, port=port, user="root", database=database, connection_timeout=10
        )
        try:
            cur = conn.cursor()
            cur.execute(sql, (start, *spelled))
            for sym, day, a, b in cur.fetchall():
                if sink == "b":
                    bars[spelled[sym]][day] = (
                        float(a) if a is not None else 0.0,
                        float(b) if b is not None else 0.0,
                    )
                else:
                    ivrv[spelled[sym]].append((day, a, b))
        finally:
            conn.close()
    for rows in ivrv.values():
        rows.sort(key=lambda r: r[0])
    return MarketData(dict(bars), dict(ivrv))


def _volume(row: dict) -> float | None:
    """A row's day volume, None when the store has none or holds a non-number."""
    try:
        v = float(row.get("volume"))
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def usable_quote(bid, ask) -> bool:
    """A two-sided quote: both sides present and finite, a positive ask, not crossed."""
    try:
        b, a = float(bid), float(ask)
    except (TypeError, ValueError):
        return False
    return math.isfinite(b) and math.isfinite(a) and a > 0 and b >= 0 and a >= b


class Chains:
    """The end-of-day chain store read as rows {strike, right, bid, ask, volume}, once each."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._cache: dict[tuple, list[dict]] = {}

    def rows(self, symbol: str, session: date, expiration: date) -> list[dict]:
        key = (symbol.upper(), session.isoformat(), expiration.isoformat())
        if key not in self._cache:
            self._cache[key] = [
                {"strike": k, "right": r[0].upper(), "bid": b, "ask": a, "volume": v}
                for k, r, b, a, v in self.conn.execute(
                    "SELECT strike, right, bid, ask, volume FROM eod_chain_quotes "
                    "WHERE symbol = ? AND session = ? AND expiration = ? ORDER BY strike, right",
                    key,
                )
            ]
        return self._cache[key]

    def clear(self) -> None:
        """Forget the rows read so far (a run keeps one event's chains in memory, not all of them)."""
        self._cache.clear()


def occ_symbol(symbol: str, expiration: date, right: str, strike: float) -> str:
    """The OCC option symbol the module's parsers read: the root padded to six, yymmdd, C or P, and
    the strike in thousandths over eight digits."""
    root = symbol.replace(".", "").replace("/", "").upper()
    return f"{root:<6}{expiration:%y%m%d}{right}{int(round(strike * 1000)):08d}"


# ------------------------------------------------------------------------------------------------
# The fake broker: tt.py's three commands the module's code calls, answered from the store.


def _window(strikes: list[float], count: int, around: float | None) -> set[float]:
    """`core.broker.atm_window` over strikes: `count` each side of the strike nearest `around`."""
    if not strikes:
        return set()
    center = around if around is not None else strikes[len(strikes) // 2]
    nearest = min(range(len(strikes)), key=lambda i: abs(strikes[i] - center))
    return set(strikes[max(0, nearest - count) : min(len(strikes), nearest + count + 1)])


class FakeTT:
    """`scanner.call_tt` over the end-of-day store: `get_quote` (the stock's close),
    `get_option_chain` (every expiry, or one expiry with the quotes, greeks and volume asked for,
    windowed on `--strike_count` and `--around_price`) and `get_market_metrics` (not answered). The
    delta and implied volatility are computed from each chain's own mids at the close."""

    def __init__(self, chains: Chains, symbol: str, session: date, spot: float, expirations: list[date]):
        self.chains, self.symbol, self.session, self.spot = chains, symbol, session, spot
        self.expirations = expirations
        self._forward: dict[date, float] = {}
        self._greeks: dict[tuple, tuple] = {}

    def forward(self, exp: date) -> float:
        if exp not in self._forward:
            rows = [
                {"strike_price": r["strike"], "option_type": r["right"], "bid": r["bid"], "ask": r["ask"]}
                for r in self.chains.rows(self.symbol, self.session, exp)
                if usable_quote(r["bid"], r["ask"])
            ]
            self._forward[exp] = chaindelta.forward_from_parity(rows) or self.spot
        return self._forward[exp]

    def greeks(self, exp: date, row: dict) -> tuple[float | None, float | None]:
        """(delta, implied vol) from the row's mid; None where no vol prices it or no time is left."""
        key = (exp, row["right"], row["strike"])
        if key not in self._greeks:
            years = (exp - self.session).days / 365.0
            out: tuple = (None, None)
            if years > 0 and usable_quote(row["bid"], row["ask"]):
                mid = (float(row["bid"]) + float(row["ask"])) / 2.0
                f = self.forward(exp)
                iv = bs.implied_vol(f, row["strike"], years, mid, row["right"], 0.0)
                if iv is not None and iv > 0.0001:
                    out = (bs.delta(f, row["strike"], years, iv, row["right"], 0.0), iv)
            self._greeks[key] = out
        return self._greeks[key]

    def __call__(self, args: list[str]) -> dict:
        cmd, opts, flags = args[0], {}, set()
        i = 1
        while i < len(args):
            tok = args[i]
            if tok in ("--include_greeks", "--include_quotes", "--include_oi", "--include_volume"):
                flags.add(tok[2:])
                i += 1
            else:
                opts[tok[2:]] = args[i + 1]
                i += 2
        if cmd == "get_quote":
            return {"ok": True, "symbol": self.symbol, "price": self.spot}
        if cmd == "get_option_chain":
            return self._chain(opts, flags)
        return {"ok": False, "error": f"{cmd} is not answered in the replay"}

    def _chain(self, opts: dict, flags: set) -> dict:
        exp_text = opts.get("expiration")
        if exp_text is None:
            if flags:
                return {"ok": False, "error": "the replay answers a chain with data for one expiry only"}
            return {"ok": True, "symbol": self.symbol, "chain": {e.isoformat(): [] for e in self.expirations}}
        exp = date.fromisoformat(exp_text)
        rows = self.chains.rows(self.symbol, self.session, exp)
        if not rows:
            return {"ok": False, "error": f"No {exp_text} expiration for {self.symbol} (not stored)."}
        strikes = sorted({r["strike"] for r in rows})
        count = int(opts["strike_count"]) if "strike_count" in opts else 15
        around = float(opts["around_price"]) if "around_price" in opts else None
        keep = _window(strikes, count, around)
        out = []
        for r in rows:
            if r["strike"] not in keep:
                continue
            e = {
                "symbol": occ_symbol(self.symbol, exp, r["right"], r["strike"]),
                "strike_price": r["strike"],
                "option_type": r["right"],
                "expiration_date": exp.isoformat(),
            }
            if "include_quotes" in flags:
                ok = usable_quote(r["bid"], r["ask"])
                e["bid"] = float(r["bid"]) if ok else None
                e["ask"] = float(r["ask"]) if ok else None
                e["mid"] = round((e["bid"] + e["ask"]) / 2, 4) if ok else None
            if "include_greeks" in flags:
                e["delta"], e["iv"] = self.greeks(exp, r)
            if "include_volume" in flags and _volume(r) is not None:
                e["day_volume"] = int(_volume(r))
            out.append(e)
        return {"ok": True, "symbol": self.symbol, "chain": {exp_text: out}}


def _fixed_date(today: date):
    class FixedDate(date):
        @classmethod
        def today(cls):
            return today

    return FixedDate


@contextlib.contextmanager
def replay_env(tt, ivrv: dict, basis: str, today: date):
    """The module's I/O readers swapped for the replay's, and put back: `call_tt`,
    `fetch_iv_rv_ratio`, `compute_winrate` (the dispersion is not applied), `exit_basis` (which reads
    the clock of the row; the replay says which fill it is judging) and each strategy's `date`."""
    from cherrypick.earnings import scanner
    from cherrypick.earnings.strategies import directional_credit_spread, iron_condor, iron_fly

    mods = (iron_fly, iron_condor, directional_credit_spread)
    saved = {
        "call_tt": scanner.call_tt,
        "fetch_iv_rv_ratio": scanner.fetch_iv_rv_ratio,
        "compute_winrate": scanner.compute_winrate,
        "exit_basis": scanner.exit_basis,
    }
    saved_dates = [m.date for m in mods]
    scanner.call_tt = tt
    scanner.fetch_iv_rv_ratio = lambda symbol, config: ivrv
    scanner.compute_winrate = lambda symbol, config, lookback_quarters=8: {"ok": False, "error": "not read"}
    scanner.exit_basis = lambda trade: basis
    for m in mods:
        m.date = _fixed_date(today)
    try:
        yield
    finally:
        for k, v in saved.items():
            setattr(scanner, k, v)
        for m, d in zip(mods, saved_dates, strict=True):
            m.date = d


# ------------------------------------------------------------------------------------------------
# Pure: the reaction-move table (G1 and the dispersion), from Dolt's bars.


def reaction_moves(
    symbol: str, rows: list[tuple[date, str | None]], data: MarketData
) -> list[tuple[date, float]]:
    """(report date, absolute reaction move) for each stated-timing report: the reaction session's
    close against the last close before the report (the entry session's), both from the bars. A report
    without both closes, or whose ratio looks like a split, is left out."""
    out = []
    for d, when in sorted(rows):
        timing = TIMINGS.get(when or "")
        if timing is None:
            continue
        a = data.close(symbol, entry_session(d, timing))
        b = data.close(symbol, reaction_session(d, timing))
        if not a or not b or not (SPLIT_RATIO[0] < b / a < SPLIT_RATIO[1]):
            continue
        out.append((d, abs(b / a - 1.0)))
    return out


def prior_moves(moves: list[tuple[date, float]], report: date, n: int = G1_PRIOR) -> list[float]:
    return [m for d, m in moves if d < report][-n:]


def g1_status(moves: list[tuple[date, float]], report: date, em_pct: float | None) -> bool | None:
    """G1: the entry's expected move (a share of the price) is at least 1.2 times the name's mean
    absolute reaction move over its previous eight stated-timing reports. None when the name has
    fewer than four earlier ones (no G1 trade) or no expected move was read."""
    prior = prior_moves(moves, report)
    if len(prior) < G1_MIN_PRIOR or em_pct is None:
        return None
    return em_pct >= G1_MULTIPLE * statistics.fmean(prior)


def dispersion(moves: list[tuple[date, float]], report: date) -> float | None:
    """The module's realised-move dispersion: the population standard deviation of the previous eight
    absolute moves. Recorded for the report; not applied."""
    prior = prior_moves(moves, report)
    return statistics.pstdev(prior) if len(prior) >= 2 else None


def crossing_cost(quotes: list[dict]) -> float:
    """The entry's round-trip crossing cost in dollars: every leg's full quoted spread, summed, x 100."""
    return sum(float(q["ask"]) - float(q["bid"]) for q in quotes) * 100.0


def g2_status(quotes: list[dict], credit: float) -> bool:
    """G2: the crossing cost is at most 25% of the credit received (x 100). A credit of zero or less
    cannot pay any cost."""
    return credit > 0 and crossing_cost(quotes) <= G2_SHARE * credit * 100.0 + 1e-9


def _quote(bid, ask) -> dict:
    b, a = float(bid), float(ask)
    return {"bid": b, "ask": a, "mid": round((b + a) / 2, 4)}


def et_mark(session: date) -> datetime:
    """The clock a session's end-of-day mark is evaluated at: 16:00 ET, whatever the close."""
    return datetime.combine(session, time(16, 0), ET)


# ------------------------------------------------------------------------------------------------
# One trade: admission, the structure the module builds, its management, and its fills.


class Dropped(Exception):
    """The trade cannot be built or priced; the reason is the drop's name."""


def _modules():
    from cherrypick.earnings import costs, management, scanner, sizing
    from cherrypick.earnings.strategies import directional_credit_spread, iron_condor, iron_fly

    mods = {
        "iron_fly": iron_fly,
        "iron_condor": iron_condor,
        "directional_credit_spread": directional_credit_spread,
    }
    orders = {
        "iron_fly": iron_fly.fetch_iron_fly_order,
        "iron_condor": iron_condor.fetch_iron_condor_order,
        "directional_credit_spread": directional_credit_spread.fetch_directional_credit_spread_order,
    }
    return costs, management, scanner, sizing, mods, orders


class Study:
    """The replay over the stores: `trade` builds and manages one structure on one name and session."""

    def __init__(self, data: MarketData, chains: Chains, cfg: dict, moves: dict[str, list]):
        self.data, self.chains, self.cfg, self.moves = data, chains, cfg, moves
        self.stress_cfg = stress_config(cfg)

    # -- admission ---------------------------------------------------------------------------
    def _criteria(self, mods, strategy, symbol, report, timing_text, session, front_rows) -> dict:
        got = mods[strategy].fetch_price_and_term_structure(symbol, report, timing_text, self.cfg)
        if not got.get("ok"):
            raise Dropped("criteria: " + str(got.get("error"))[:60])
        crit = got["criteria"]
        crit["combined_open_interest"] = 0  # the store holds none; the floor is 0 in the study's copy
        crit["has_weekly_options"] = bool(front_rows)
        crit["avg_volume"] = self.data.avg_volume(symbol, session)
        ivrv = self.data.iv_rv(symbol, session)
        crit["iv_rv_ratio"] = ivrv.get("iv_rv_ratio")
        return crit

    def _pit_reasons(self, symbol, session, crit) -> list[str]:
        out = []
        dv = self.data.median_dollar_volume(symbol, session)
        if dv is None:
            out.append("pit_dollar_volume_unverified")
        elif dv < MIN_DOLLAR_VOLUME:
            out.append("pit_dollar_volume")
        spread = crit.get("bid_ask_spread_pct")
        if spread is None or spread > PIT_MAX_SPREAD_PCT:
            out.append("pit_spread")
        return out

    def _em_pct(self, scanner, symbol, front, spot) -> float | None:
        atm = scanner.fetch_front_back_atm_entries(symbol, front, front, spot)
        if not atm.get("ok"):
            return None
        from cherrypick.core import structures

        return structures.expected_move(atm["front_call"]["mid"], atm["front_put"]["mid"]) / spot

    # -- one trade ---------------------------------------------------------------------------
    def stub(self, strategy: str, ev: dict, role: str, drop: str) -> dict:
        """A record for a trade that was never tried, with its reason."""
        rec = self._skeleton(strategy, ev, role, ev)
        rec["drop"] = drop
        return rec

    def _skeleton(self, strategy: str, ev: dict, role: str, src: dict) -> dict:
        symbol = ev["symbol"]
        return {
            "symbol": symbol,
            "strategy": strategy,
            "role": role,
            "event_report": ev["report_date"],
            "report_date": src["report_date"],
            "entry_session": src["entry_session"],
            "timing": ev["timing"],
            "year": int(ev["report_date"][:4]),
            "window": window_of(date.fromisoformat(ev["report_date"])),
            "time_half": 0 if date.fromisoformat(ev["report_date"]) <= HALF_END else 1,
            "name_half": robustness.name_half(symbol),
            "ok": False,
            "drop": None,
            "admitted": None,
            "reject": [],
        }

    def trade(
        self, strategy: str, ev: dict, role: str, control: dict | None = None, *, entry_only: bool = False
    ) -> dict:
        """The record of one structure: `ev` is the event row; for role 'control' `control` carries the
        control's session, report date and expiry. Any role but 'event' builds without the screens.
        `entry_only` stops once the order is built and sized (the replay check). The record always says
        what became of the trade."""
        src = control or ev
        symbol = ev["symbol"]
        report = date.fromisoformat(src["report_date"])
        session = date.fromisoformat(src["entry_session"])
        timing = ev["timing"]
        rec = self._skeleton(strategy, ev, role, src)
        try:
            self._trade(rec, strategy, symbol, report, timing, session, role, entry_only)
        except Dropped as why:
            rec["drop"] = str(why)
        return rec

    def _trade(self, rec, strategy, symbol, report, timing, session, role, entry_only=False) -> None:
        costs, management, scanner, sizing, mods, orders = _modules()
        spot = self.data.close(symbol, session)
        if spot is None:
            raise Dropped("no_underlying_bar")
        timing_text = TIMING_TEXT[timing]
        candidates = weekly_candidates(session)
        tt = FakeTT(self.chains, symbol, session, spot, candidates)
        with replay_env(tt, self.data.iv_rv(symbol, session), "mid", session):
            front, _ = scanner.select_front_expiration(candidates, report, timing_text)
            if front is None:
                raise Dropped("no_front_expiry")
            front_rows = self.chains.rows(symbol, session, front)
            if not front_rows:
                raise Dropped("no_front_chain")
            rec["front_expiration"] = front.isoformat()
            rec["front_volume"] = sum(_volume(r) or 0.0 for r in front_rows)
            moves = self.moves.get(symbol, [])
            rec["dispersion"] = dispersion(
                moves, report if role == "event" else date.fromisoformat(rec["event_report"])
            )
            if role == "event":
                back = scanner.select_back_expiration(candidates, front, 21)
                if back is None or not self.chains.rows(symbol, session, back):
                    raise Dropped("no_back_chain")
                crit = self._criteria(mods, strategy, symbol, report, timing_text, session, front_rows)
                reject = list(
                    mods[strategy].apply_tiering(crit, self.cfg["strategies"][strategy])["reject_reasons"]
                )
                reject += self._pit_reasons(symbol, session, crit)
                em = crit.get("expected_move_pct")
                if em is None and crit.get("expected_move_dollars") is not None:
                    em = crit["expected_move_dollars"] / spot
                rec.update(admitted=not reject, reject=reject, term=crit.get("term_structure"), em_pct=em,
                           atm_spread_pct=crit.get("bid_ask_spread_pct"), spot=spot)  # fmt: skip
                rec["g1"] = g1_status(moves, report, em)
                if reject:
                    return
            else:
                rec["em_pct"] = self._em_pct(scanner, symbol, front, spot)
                rec["spot"] = spot
            order = orders[strategy](symbol, report, timing_text, self.cfg)
            if not order.get("ok"):
                raise Dropped("order: " + str(order.get("error"))[:60])
            size = sizing.compute_position_size(order, self.cfg["strategies"][strategy], self.cfg)
            if not size["ok"]:
                raise Dropped("over_budget" if size["reason"] == "risk_cap_exceeded" else size["reason"])
            if entry_only:
                self._legs(order, front_rows)  # every leg must carry a two-sided quote
                rec["built"] = {"legs": order["order"]["legs"], "credit": float(order["credit"])}
                rec["ok"] = True
                return
            self._manage(rec, strategy, symbol, session, front, order, size, spot, front_rows)

    # -- management and fills ----------------------------------------------------------------
    def _legs(self, order: dict, front_rows: list[dict]) -> tuple[list[dict], list[dict]]:
        from cherrypick.core.livepositions import parse_occ

        legs = [dict(leg) for leg in order["order"]["legs"]]
        info, by = [], {(r["right"], round(r["strike"], 3)): r for r in front_rows}
        for leg in legs:
            root, exp, right, strike = parse_occ(leg["symbol"])
            row = by.get((right, round(strike, 3)))
            if row is None or not usable_quote(row["bid"], row["ask"]):
                raise Dropped("leg_quote_missing")
            info.append({"symbol": leg["symbol"], "right": right, "strike": strike, "action": leg["action"],
                         "quote": _quote(row["bid"], row["ask"])})  # fmt: skip
        return legs, info

    def _marks(self, symbol, info, front, session, spot) -> tuple[list[dict], bool]:
        """One mark per later session through the front expiry: each leg's quote at the close, its last
        earlier quote when it has none, its intrinsic value on its expiry day. Stops before a split."""
        marks, last, prev = [], {i["symbol"]: i["quote"] for i in info}, spot
        for s in sessions_between(cal.next_trading_day(session), front):
            close = self.data.close(symbol, s)
            if close and prev and not (SPLIT_RATIO[0] < close / prev < SPLIT_RATIO[1]):
                return marks, True
            rows = self.chains.rows(symbol, s, front)
            by = {(r["right"], round(r["strike"], 3)): r for r in rows}
            quotes, stale, intrinsic = {}, 0, 0
            for i in info:
                r = by.get((i["right"], round(i["strike"], 3)))
                if r is not None and usable_quote(r["bid"], r["ask"]):
                    q = _quote(r["bid"], r["ask"])
                elif s == front and close:
                    v = bs.intrinsic(close, i["strike"], i["right"])
                    q, intrinsic = {"bid": v, "ask": v, "mid": v}, intrinsic + 1
                else:
                    q, stale = last[i["symbol"]], stale + 1
                quotes[i["symbol"]] = q
                last[i["symbol"]] = q
            settled = s == front and not rows
            if settled and not close:
                raise Dropped("no_settlement_price")
            marks.append(
                {
                    "session": s,
                    "quotes": quotes,
                    "spot": close,
                    "stale": stale,
                    "intrinsic": intrinsic,
                    "settled": settled,
                }
            )
            if close:
                prev = close
        return marks, False

    def _manage(self, rec, strategy, symbol, session, front, order, size, spot, front_rows) -> None:
        costs, management, scanner, _, _, _ = _modules()
        legs, info = self._legs(order, front_rows)
        marks, split = self._marks(symbol, info, front, session, spot)
        if not marks:
            raise Dropped("split_on_first_session" if split else "no_later_session")
        credit = float(order["credit"])
        quotes_in = [i["quote"] for i in info]
        credit_nat = sum(
            q["bid"] if i["action"] == "Sell to Open" else -q["ask"]
            for i, q in zip(info, quotes_in, strict=True)
        )
        trade = {
            "strategy": strategy, "symbol": symbol, "expiration": front.isoformat(),
            "legs_json": json.dumps(legs), "entry_credit": credit, "quantity": 1,
            "opened_at": et_mark(session).timestamp(),
        }  # fmt: skip
        pseudo = {"order": {"legs": legs}}
        entry = {
            "credit_mid": credit,
            "credit_nat": credit_nat,
            "cost": costs.apply_entry_costs(pseudo, quotes_in, 1, self.cfg),
            "cost_stress": costs.apply_entry_costs(pseudo, quotes_in, 1, self.stress_cfg),
        }
        rec["credit"] = credit
        rec["capital_at_risk"] = float(size["per_contract_loss"])
        rec["crossing"] = crossing_cost(quotes_in)
        rec["g2"] = g2_status(quotes_in, credit)
        rec["expiry_days"] = (front - session).days
        rec["strikes"] = {i["symbol"][-15:]: i["action"][0] for i in info}
        nopead_cfg = {**self.cfg, "management": {"close_losers_first_morning": False}}
        picks = {
            "managed": self._walk(trade, marks, self.cfg, strategy, split),
            "no_pead": self._walk(trade, marks, nopead_cfg, strategy, split),
            "first_morning": (0, "first_morning"),
            "held_to_expiry": (len(marks) - 1, "split" if split else "expiry_close"),
        }
        exits = {}
        for reading, (idx, reason) in picks.items():
            exits[reading] = self._price(rec, order, legs, entry, marks, idx, reason)
        rec["exits"] = exits
        rec["ok"] = True

    def _walk(self, trade, marks, cfg, strategy, split) -> tuple[int, str]:
        """The module's management on each later session's mark: (index of the mark it closes at, why).
        A close the spread gate refuses waits for the next mark, except the expiry close."""
        _, management, _, _, _, _ = _modules()
        for k, m in enumerate(marks, start=1):
            snap = {"ok": True, "quotes": m["quotes"], "spot": m["spot"]}
            now = et_mark(m["session"])
            dec = management.evaluate(trade, snap, cfg, now=now, sessions_held=k, is_first_check_of_day=True)
            if (
                dec.closes
                and management.execution_gate(snap, cfg, strategy, now=now, reason=dec.reason) is None
            ):
                return k - 1, dec.reason
        return len(marks) - 1, "split"

    def _price(self, rec, order, legs, entry, marks, idx, reason) -> dict:
        """One exit: the debit on the working price and on the natural side, the costs on both sides in
        the working and the stress readings, and the nets that add up."""
        costs, _, scanner, _, _, _ = _modules()
        m = marks[idx]
        quotes = m["quotes"]
        debit_mid = scanner.compute_generic_exit_debit(legs, quotes, "mid")
        debit_nat = scanner.compute_generic_exit_debit(legs, quotes, "natural")
        if debit_mid is None or debit_nat is None:
            raise Dropped("unpriceable_exit")
        stale = sum(x["stale"] for x in marks[: idx + 1])
        pseudo = {"order": {"legs": legs}}
        if m["settled"]:
            itm = sum(1 for leg in legs if quotes[leg["symbol"]]["mid"] > 0)
            settlement = fees.ic_expire_fee(itm)
            zero = {"commission": 0.0, "pass_through_fees": 0.0, "slippage": 0.0, "total_cost": 0.0}
            exit_c = exit_s = zero
            reason = "settled"
        else:
            settlement = 0.0
            leg_quotes = [quotes[leg["symbol"]] for leg in legs]
            exit_c = costs.exit_costs("mid", pseudo, leg_quotes, 1, self.cfg)
            exit_s = costs.exit_costs("mid", pseudo, leg_quotes, 1, self.stress_cfg)
        risk = rec["capital_at_risk"]
        gross = (entry["credit_mid"] - debit_mid) * 100.0
        fees_in = entry["cost"]["commission"] + entry["cost"]["pass_through_fees"]
        fees_out = exit_c["commission"] + exit_c["pass_through_fees"]
        nets = {
            "working": gross - entry["cost"]["total_cost"] - exit_c["total_cost"] - settlement,
            "mid_fees": gross - fees_in - fees_out - settlement,
            "natural": (entry["credit_nat"] - debit_nat) * 100.0 - fees_in - fees_out - settlement,
            "stress": gross - entry["cost_stress"]["total_cost"] - exit_s["total_cost"] - settlement,
        }
        return {
            "session": m["session"].isoformat(),
            "reason": reason,
            "hold_sessions": idx + 1,
            "stale_marks": stale,
            "gross": gross,
            "entry_fees": fees_in,
            "entry_slippage": entry["cost"]["slippage"],
            "exit_fees": fees_out,
            "exit_slippage": exit_c["slippage"],
            "settlement": settlement,
            "net": nets["working"],
            "share": nets["working"] / risk,
            "nets": nets,
            "shares": {k: v / risk for k, v in nets.items()},
        }


# ------------------------------------------------------------------------------------------------
# The whole replay: every event, its three structures, their controls.


def run_study(
    events: list[dict], reports: dict[str, list[date]], span: tuple[date, date], study: Study
) -> list[dict]:
    """A record for each event, strategy and role. The control is built only for an event whose
    structure was built, from the control session `control_for` names."""
    out: list[dict] = []
    for ev in sorted(events, key=lambda e: (e["report_date"], e["symbol"])):
        study.chains.clear()
        control = control_for(ev, reports.get(ev["symbol"], []), span)
        for strategy in STRATEGIES:
            rec = study.trade(strategy, ev, "event")
            out.append(rec)
            if not rec["ok"]:
                continue
            if control is None:
                out.append(study.stub(strategy, ev, "control", "no_control_session"))
            else:
                out.append(study.trade(strategy, ev, "control", control))
    return out


# ------------------------------------------------------------------------------------------------
# Pure: the readings. A record is the dict `Study.trade` returns.


def index_records(records: list[dict]) -> dict[tuple, dict]:
    """{(name, event's report date, strategy): {"event": rec, "control": rec}}."""
    idx: dict[tuple, dict] = defaultdict(dict)
    for r in records:
        idx[(r["symbol"], r["event_report"], r["strategy"])][r["role"]] = r
    return dict(idx)


def share(rec: dict, reading: str = "managed", fill: str = "working") -> float:
    return rec["exits"][reading]["shares"][fill]


def arm_sample(
    idx: dict, strategy: str, gate: str, window: str
) -> tuple[list[dict], list[tuple[dict, dict]]]:
    """An arm's closed event trades in a window, and those with a closed control. G1 keeps the events
    whose expected move clears it, and their controls keep the event's status. G2 keeps the events
    whose credit pays its crossing cost, and pairs them only with a control whose own quotes do."""
    events, pairs = [], []
    for (_, _, strat), pr in idx.items():
        e = pr.get("event")
        if strat != strategy or not e or not e["ok"] or e["window"] != window:
            continue
        if gate == "g1" and e.get("g1") is not True:
            continue
        if gate == "g2" and not e.get("g2"):
            continue
        events.append(e)
        c = pr.get("control")
        if c and c["ok"] and (gate != "g2" or c.get("g2")):
            pairs.append((e, c))
    events.sort(key=lambda r: (r["entry_session"], r["symbol"]))
    pairs.sort(key=lambda p: (p[0]["entry_session"], p[0]["symbol"]))
    return events, pairs


def cluster_t(
    values: list[float], clusters: list[str], *, resamples: int | None = None, seed: int = SEED
) -> dict:
    """The mean, its t with the standard error clustered by entry session (CR0), and the one-sided
    bootstrap p that the mean is not above zero: sessions resampled with replacement (2,000 times),
    the bootstrap t centred on the observed mean. Seeded."""
    resamples = RESAMPLES if resamples is None else resamples
    groups: dict[str, list[float]] = defaultdict(list)
    for v, c in zip(values, clusters, strict=True):
        groups[c].append(v)
    sums = [sum(g) for g in groups.values()]
    sizes = [len(g) for g in groups.values()]
    n, k = sum(sizes), len(sums)
    if n == 0:
        return {"n": 0, "sessions": 0, "mean": None, "t": None, "p": 1.0}

    def stat(idx: list[int]) -> tuple[float, float]:
        total = sum(sizes[i] for i in idx)
        mean = sum(sums[i] for i in idx) / total
        se = math.sqrt(sum((sums[i] - mean * sizes[i]) ** 2 for i in idx)) / total
        return mean, se

    mean, se = stat(list(range(k)))
    t = (math.inf if mean > 0 else -math.inf if mean < 0 else 0.0) if se < 1e-12 else mean / se
    rng = random.Random(seed)
    hits = 0
    for _ in range(resamples):
        m, s = stat([rng.randrange(k) for _ in range(k)])
        tb = 0.0 if s < 1e-12 else (m - mean) / s
        hits += tb >= t
    return {"n": n, "sessions": k, "mean": mean, "t": t, "p": (1 + hits) / (resamples + 1)}


def _mean(xs) -> float | None:
    xs = list(xs)
    return statistics.fmean(xs) if xs else None


def drop_best(values: list[float], k: int = BEST_REMOVED) -> list[float]:
    """The values without the k largest."""
    return sorted(values)[:-k] if len(values) > k else []


def window_stats(events: list[dict], pairs: list[tuple[dict, dict]], *, bootstrap: bool = True) -> dict:
    """One arm in one window: the clustered tests of the net share and of the paired difference, each
    time-half and name-half, the stress, and the net share without the best five trades. The holdout
    is read for its means, not tested: `bootstrap=False` skips the resampling (p = 1)."""
    nets = [share(e) for e in events]
    diffs = [share(e) - share(c) for e, c in pairs]
    n = None if bootstrap else 0
    return {
        "events": len(events),
        "pairs": len(pairs),
        "net": cluster_t(nets, [e["entry_session"] for e in events], resamples=n),
        "pair": cluster_t(diffs, [e["entry_session"] for e, _ in pairs], resamples=n),
        "net_by_time_half": [_mean(share(e) for e in events if e["time_half"] == h) for h in (0, 1)],
        "net_by_name_half": [_mean(share(e) for e in events if e["name_half"] == h) for h in (0, 1)],
        "pair_by_time_half": [
            _mean(share(e) - share(c) for e, c in pairs if e["time_half"] == h) for h in (0, 1)
        ],
        "stress_mean": _mean(share(e, fill="stress") for e in events),
        "without_best_five": _mean(drop_best(nets)),
    }


def _pos(x) -> bool:
    return x is not None and x > 0


def design_checks(w: dict, holm_net: bool, holm_pair: bool) -> dict:
    """The declared design-window conditions, each a bool."""
    return {
        "net_t_holm": _pos(w["net"]["mean"]) and w["net"]["t"] > T_BAR and holm_net,
        "pair_t_holm": _pos(w["pair"]["mean"]) and w["pair"]["t"] > T_BAR and holm_pair,
        "net_each_time_half": all(_pos(x) for x in w["net_by_time_half"]),
        "net_each_name_half": all(_pos(x) for x in w["net_by_name_half"]),
        "pair_each_time_half": all(_pos(x) for x in w["pair_by_time_half"]),
        "stress_positive": _pos(w["stress_mean"]),
        "best_five_removed_positive": _pos(w["without_best_five"]),
    }


def judge_arm(design: dict, holdout: dict, holm_net: bool, holm_pair: bool) -> dict:
    """The arm's verdict. No verdict under 150 paired trades in the design window (or 100 in the
    holdout when the design passed). A design pass needs every check; then the holdout's mean net and
    paired difference both above zero. A fail is read as 'pays, not for the report' when the net
    clears and the pair does not, and 'costs eat the report' in the other order."""
    checks = design_checks(design, holm_net, holm_pair)
    enough = design["pairs"] >= MIN_TRADES["design"]
    passed = enough and all(checks.values())
    hold_ok = _pos(holdout["net"]["mean"]) and _pos(holdout["pair"]["mean"])
    hold_enough = holdout["pairs"] >= MIN_TRADES["holdout"]
    if not enough:
        word = "NO VERDICT"
    elif not passed:
        word = "FAIL"
    elif not hold_enough:
        word = "NO VERDICT"
    else:
        word = "PASS" if hold_ok else "FAIL"
    reading = None
    if enough and not passed:
        if checks["net_t_holm"] and not checks["pair_t_holm"]:
            reading = "pays, not for the report"
        elif checks["pair_t_holm"] and not checks["net_t_holm"]:
            reading = "the report is worth something but the costs eat it"
    return {"verdict": word, "design_pass": passed, "enough_design": enough, "enough_holdout": hold_enough,
            "holdout_ok": hold_ok, "checks": checks, "reading": reading}  # fmt: skip


def evaluate_arms(idx: dict) -> dict:
    """Every arm's design and holdout statistics and verdict, Holm across the nine arms (a family each
    for the net test and the paired test; an arm under the minimum counts p = 1)."""
    stats: dict[str, dict] = {}
    for strategy in STRATEGIES:
        for gate in GATES:
            samples = {w: arm_sample(idx, strategy, gate, w) for w in ("design", "holdout")}
            stats[arm_name(strategy, gate)] = {
                w: window_stats(*samples[w], bootstrap=w == "design") for w in samples
            }
    p_net, p_pair = {}, {}
    for arm, s in stats.items():
        enough = s["design"]["pairs"] >= MIN_TRADES["design"]
        p_net[arm] = s["design"]["net"]["p"] if enough else 1.0
        p_pair[arm] = s["design"]["pair"]["p"] if enough else 1.0
    h_net, h_pair = core_stats.holm(p_net, alpha=ALPHA), core_stats.holm(p_pair, alpha=ALPHA)
    out = {}
    for arm, s in stats.items():
        out[arm] = {
            **s,
            **judge_arm(s["design"], s["holdout"], h_net[arm], h_pair[arm]),
            "p_net": p_net[arm],
            "p_pair": p_pair[arm],
        }
    for strategy in STRATEGIES:
        base = out[strategy]["verdict"]
        for gate in ("g1", "g2"):
            a = out[arm_name(strategy, gate)]
            a["gate_passes_ungated_fails"] = a["verdict"] == "PASS" and base != "PASS"
    return out


# ------------------------------------------------------------------------------------------------
# Pure: the reported readings (not judged).


def tercile_means(rows: list[tuple[float | None, float]]) -> list[float | None]:
    """The mean share in the lowest, middle and highest third of a measure, by rank."""
    ok = sorted((m, s) for m, s in rows if m is not None)
    if len(ok) < 3:
        return [None, None, None]
    n = len(ok)
    cuts = [0, n // 3, 2 * n // 3, n]
    return [_mean(s for _, s in ok[cuts[i] : cuts[i + 1]]) for i in range(3)]


def concentration(events: list[dict]) -> dict:
    """The names' shares of an arm's trades: the largest, how many are above 8%, the top five's share
    and the mean net share without the top five names."""
    if not events:
        return {}
    counts = Counter(e["symbol"] for e in events)
    n = len(events)
    top5 = [s for s, _ in counts.most_common(5)]
    top = counts.most_common(1)[0]
    return {
        "largest_name": top[0],
        "largest_share": top[1] / n,
        "names_above_cap": sum(1 for c in counts.values() if c / n > NAME_SHARE_CAP),
        "top5_share": sum(counts[s] for s in top5) / n,
        "mean_without_top5": _mean(share(e) for e in events if e["symbol"] not in top5),
    }


def macro_week(report: date, fomc: set[date], cpi: set[date] | None) -> dict:
    """Whether the report's Monday-to-Sunday week holds a scheduled FOMC decision or a CPI release
    (None for CPI when no CPI calendar was supplied)."""
    monday = report - timedelta(days=report.weekday())
    week = {monday + timedelta(days=i) for i in range(7)}
    return {"fomc": bool(week & fomc), "cpi": None if cpi is None else bool(week & cpi)}


def macro_split(events: list[dict], fomc: set[date], cpi: set[date] | None) -> dict:
    out = {}
    for label in ("fomc", "cpi"):
        if label == "cpi" and cpi is None:
            out[label] = None
            continue
        yes, no = [], []
        for e in events:
            flag = macro_week(date.fromisoformat(e["event_report"]), fomc, cpi)[label]
            (yes if flag else no).append(share(e))
        out[label] = {
            "in_week": {"n": len(yes), "mean": _mean(yes)},
            "other_weeks": {"n": len(no), "mean": _mean(no)},
        }
    return out


def reported_readings(
    events: list[dict], pairs: list[tuple[dict, dict]], fomc: set[date], cpi: set[date] | None
) -> dict:
    """Everything the declaration reports and does not judge, for one arm and window."""
    if not events:
        return {}
    ceiling = THRESHOLDS[events[0]["strategy"]]["max_realized_move_dispersion_pct"]
    covid = [e for e in events if not (COVID[0] <= date.fromisoformat(e["entry_session"]) <= COVID[1])]
    wins = [share(e) > 0 for e in events]
    return {
        "fills": {f: _mean(share(e, fill=f) for e in events) for f in FILL_READINGS},
        "exits": {x: _mean(share(e, reading=x) for e in events) for x in EXIT_READINGS},
        "without_2020_02_to_06": _mean(share(e) for e in covid),
        "win_rate": sum(wins) / len(wins),
        "mean_net_dollars": _mean(e["exits"]["managed"]["net"] for e in events),
        "mean_hold_sessions": _mean(e["exits"]["managed"]["hold_sessions"] for e in events),
        "exit_reasons": dict(Counter(e["exits"]["managed"]["reason"] for e in events)),
        "stale_marks": sum(e["exits"]["managed"]["stale_marks"] for e in events),
        "concentration": concentration(events),
        "by_term_structure_tercile": tercile_means([(e.get("term"), share(e)) for e in events]),
        "by_atm_spread_tercile": tercile_means([(e.get("atm_spread_pct"), share(e)) for e in events]),
        "by_dispersion": {
            "above_ceiling": _mean(share(e) for e in events if (e.get("dispersion") or 0) > ceiling),
            "within": _mean(
                share(e) for e in events if e.get("dispersion") is not None and e["dispersion"] <= ceiling
            ),
        },
        "macro": macro_split(events, fomc, cpi),
        "event_not_richer_than_control": _event_premium(pairs),
        "by_year": {
            str(y): {
                "n": c,
                "mean": _mean(share(e) for e in events if e["year"] == y),
                "mean_atm_spread_pct": _mean(
                    e["atm_spread_pct"]
                    for e in events
                    if e["year"] == y and e.get("atm_spread_pct") is not None
                ),
            }
            for y, c in sorted(Counter(e["year"] for e in events).items())
        },
        "events_per_session": _per_session(events),
        "worst_five": [
            {"symbol": e["symbol"], "report": e["event_report"], "net": e["exits"]["managed"]["net"]}
            for e in sorted(events, key=lambda r: r["exits"]["managed"]["net"])[:5]
        ],
    }


def _event_premium(pairs: list[tuple[dict, dict]]) -> float | None:
    """The share of pairs whose event straddle was not richer than its control's (the report not
    priced: a mis-dated report would show here)."""
    both = [(e.get("em_pct"), c.get("em_pct")) for e, c in pairs]
    both = [(a, b) for a, b in both if a is not None and b is not None]
    return sum(1 for a, b in both if a <= b) / len(both) if both else None


def _per_session(events: list[dict]) -> dict:
    c = Counter(e["entry_session"] for e in events)
    return {"sessions": len(c), "max": max(c.values()), "mean": statistics.fmean(c.values())}


def drop_summary(records: list[dict]) -> dict:
    """What fell out and why: the not-admitted and the dropped, by strategy and role, and year, and the
    drop rate of admitted event trades in each design time-half (a gap above 5 points is flagged)."""
    by_reason: Counter = Counter()
    by_year: Counter = Counter()
    admitted = Counter()
    dropped = Counter()
    for r in records:
        key = f"{r['strategy']}/{r['role']}"
        if r["role"] == "event":
            if r["admitted"]:
                admitted[(r["strategy"], r["window"], r["time_half"])] += 1
                if r["drop"]:
                    dropped[(r["strategy"], r["window"], r["time_half"])] += 1
            elif r["drop"] is None:
                by_reason[f"{key}: not admitted"] += 1
        if r["drop"]:
            reason = r["drop"].split(":")[0]
            by_reason[f"{key}: {reason}"] += 1
            by_year[f"{key} {r['year']}"] += 1
    gaps = {}
    for s in STRATEGIES:
        rates = [
            dropped[(s, "design", h)] / admitted[(s, "design", h)] if admitted[(s, "design", h)] else None
            for h in (0, 1)
        ]
        gap = None if None in rates else abs(rates[0] - rates[1])
        gaps[s] = {"rate_by_half": rates, "gap": gap, "flag": gap is not None and gap > 0.05}
    return {
        "by_reason": dict(sorted(by_reason.items())),
        "by_year": dict(sorted(by_year.items())),
        "paired_halves": gaps,
    }


def mde_line(events: list[dict]) -> dict | None:
    """B12 from the arm's own design spread: the smallest mean (in shares of capital at risk) its
    trade count could detect at the Holm-adjusted one-sided bar."""
    vals = [share(e) for e in events]
    if len(vals) < 2:
        return None
    return robustness.planned_mde(vals, len(vals), paired=False)


def macro_calendar(path: Path) -> tuple[set[date], set[date] | None]:
    """The public FOMC and CPI dates for the macro-week split: `macro_calendar.json` beside the other
    inputs ({"fomc": [...], "cpi": [...]}), else the suite's curated FOMC years and no CPI. The file is
    put there by hand; nothing here fetches it."""
    fomc: set[date] = set()
    for y in range(2020, 2028):
        if cal.fomc_year_known(y):
            fomc.update(cal.fomc_dates(y))
    cpi = None
    if path.exists():
        doc = json.loads(path.read_text(encoding="utf-8"))
        fomc.update(date.fromisoformat(x) for x in doc.get("fomc", []))
        if doc.get("cpi"):
            cpi = {date.fromisoformat(x) for x in doc["cpi"]}
    return fomc, cpi


# ------------------------------------------------------------------------------------------------
# The replay check on paper entries: entries only, no outcome is read.

#: The columns of the paper ledger the check reads. Every one describes the entry; a column that
#: describes an outcome (pnl, exit, closed_at, status, ...) is refused by `assert_entry_only`.
ENTRY_COLUMNS = ("symbol", "strategy", "expiration", "legs_json", "entry_credit", "opened_at", "arm")
OUTCOME_COLUMNS = ("pnl", "exit_debit", "closed_at", "exit_cost", "exit_iv", "exit_reason", "status",
                   "hold_days", "max_unrealized_pnl", "min_unrealized_pnl")  # fmt: skip


def assert_entry_only(columns) -> None:
    bad = [c for c in columns if c in OUTCOME_COLUMNS]
    if bad:
        raise ValueError(f"the replay check reads entries only; refused outcome columns: {bad}")


def paper_entries(db: Path, first: date = PAPER_FIRST, last: date = PAPER_LAST) -> list[dict]:
    """The strat_test control arms' entries of the three judged strategies from `first` to `last`
    (the advised twins are not the module's rule), as {symbol, strategy, session, expiration, legs,
    credit}. Only `ENTRY_COLUMNS` are selected."""
    assert_entry_only(ENTRY_COLUMNS)
    arms = tuple(f"strat_test:{s}" for s in STRATEGIES)
    lo = datetime.combine(first, time(0, 0), ET).timestamp()
    hi = datetime.combine(last + timedelta(days=1), time(0, 0), ET).timestamp()
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            f"SELECT {', '.join(ENTRY_COLUMNS)} FROM trades WHERE arm IN ({','.join('?' * len(arms))}) "
            "AND opened_at >= ? AND opened_at < ? ORDER BY opened_at",
            (*arms, lo, hi),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for symbol, strategy, expiration, legs_json, credit, opened, _arm in rows:
        out.append(
            {
                "symbol": symbol,
                "strategy": strategy,
                "expiration": expiration,
                "session": datetime.fromtimestamp(opened, ET).date().isoformat(),
                "legs": json.loads(legs_json or "[]"),
                "credit": credit,
            }
        )
    return out


def leg_signature(legs: list[dict]) -> frozenset:
    from cherrypick.core.livepositions import parse_occ

    sig = set()
    for leg in legs:
        p = parse_occ(leg["symbol"])
        if p:
            sig.add((p[1], p[2], p[3], leg["action"]))
    return frozenset(sig)


def compare_entry(paper: dict, built: dict | None) -> str | None:
    """None when the replay built the paper entry (the same expiry, strikes and actions, the credit
    within a tick); else why not."""
    if built is None:
        return "not_built"
    if leg_signature(paper["legs"]) != leg_signature(built["legs"]):
        return "structure"
    if abs(float(paper["credit"]) - float(built["credit"])) > REPLAY_TICK + 1e-9:
        return "credit"
    return None


def evaluate_replay_check(results: list[dict]) -> dict:
    """The share of replayed paper entries that agree. Stops the study when fewer than 20 events
    could be replayed or fewer than 90% agree."""
    n = len(results)
    agree = sum(1 for r in results if r["disagreement"] is None)
    share_ = agree / n if n else None
    return {
        "events": n,
        "agree": agree,
        "share": share_,
        "disagreements": dict(Counter(r["disagreement"] for r in results if r["disagreement"])),
        "ok": n >= REPLAY_MIN_EVENTS and share_ is not None and share_ >= REPLAY_MIN_SHARE,
        "rule": f"at least {REPLAY_MIN_EVENTS} events and {REPLAY_MIN_SHARE:.0%} agreeing, "
        "else the study stops and the replay is fixed first",
    }


def replay_events(
    entries: list[dict], reports: dict[str, list[tuple[date, str | None]]]
) -> list[tuple[dict, dict]]:
    """(paper entry, event row) for each entry whose name and entry session match a stated-timing
    report in the calendar."""
    out = []
    for p in entries:
        for d, when in reports.get(p["symbol"], []):
            timing = TIMINGS.get(when or "")
            if timing and entry_session(d, timing).isoformat() == p["session"]:
                out.append((p, event_row(p["symbol"], d, timing)))
                break
    return out


def cmd_replay_check(args) -> int:
    from cherrypick.backtest import chainfetch, settings
    from cherrypick.earnings import paths as epaths

    d = _out_dir(args.out_dir)
    doc = json.loads((d / "calendar.json").read_text(encoding="utf-8"))
    rows = {s: [(date.fromisoformat(x), w) for x, w in v] for s, v in doc["reports"].items()}
    entries = paper_entries(epaths.data_path("paper_trades.db"))
    pairs = replay_events(entries, rows)
    store = chainfetch.db_path(settings.load(None))
    if args.requests_only or not store.exists():
        singles = {}
        for p, ev in pairs:
            dt = event_dates(ev)
            for exp in (dt["front"], dt["back"]):
                if exp:
                    singles.setdefault((p["symbol"].upper(), dt["entry"].isoformat(), exp.isoformat()), None)
        stored = {"singles": set(), "paths": set()}
        if store.exists():
            conn = sqlite3.connect(f"file:{store.as_posix()}?mode=ro", uri=True)
            try:
                stored = stored_requests(conn)
            finally:
                conn.close()
        todo = [k for k in singles if k not in stored["singles"]]
        out = d / "eod-replay-check-requests.json"
        out.write_text(json.dumps(request_files({"singles": todo, "paths": []})[0]), encoding="utf-8")
        print(
            json.dumps(
                {
                    "paper_entries": len(entries),
                    "replayable_events": len(pairs),
                    "chain_requests": len(todo),
                    "file": str(out),
                }
            )
        )
        return 0
    names = sorted({p["symbol"] for p, _ in pairs})
    data = load_market_data(names, args.host, args.port)
    conn = sqlite3.connect(f"file:{store.as_posix()}?mode=ro", uri=True)
    try:
        study = Study(data, Chains(conn), study_config(), {})
        results = []
        for p, ev in pairs:
            rec = study.trade(p["strategy"], ev, "replay", None, entry_only=True)
            results.append({"symbol": p["symbol"], "session": p["session"], "strategy": p["strategy"],
                            "disagreement": compare_entry(p, rec.get("built"))})  # fmt: skip
    finally:
        conn.close()
    report_ = evaluate_replay_check(results)
    (d / "replay-check.json").write_text(json.dumps({**report_, "rows": results}, indent=1), encoding="utf-8")
    print(json.dumps(report_, indent=2))
    return 0 if report_["ok"] else 2


# ------------------------------------------------------------------------------------------------
# The study command, its files and results.md.


def clean(obj):
    """JSON-safe: infinities and NaN become None."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    return obj


WINDOW_TEXT = f"design {DESIGN[0]}..{DESIGN[1]}; holdout {HOLDOUT[0]}..{HOLDOUT[1]}"


def summarise_results(records: list[dict], fomc: set[date], cpi: set[date] | None) -> dict:
    """results.json: `verdict` per arm and `window`, as the studies board reads them, the arms'
    statistics and reported readings, and what dropped out."""
    idx = index_records(records)
    arms = evaluate_arms(idx)
    out_arms = {}
    for arm, a in arms.items():
        strategy, _, gate = arm.partition("+")
        reported = {}
        for w in ("design", "holdout"):
            events, pairs = arm_sample(idx, strategy, gate, w)
            reported[w] = reported_readings(events, pairs, fomc, cpi)
            if w == "design":
                a["mde"] = mde_line(events)
        out_arms[arm] = {**a, "reported": reported}
    return {
        "verdict": {arm: {"verdict": a["verdict"], "reading": a["reading"]} for arm, a in arms.items()},
        "window": WINDOW_TEXT,
        "run_id": f"item31-{date.today().isoformat()}",
        "basis": "end_of_day",
        "arms": out_arms,
        "drops": drop_summary(records),
        "counts": {
            "records": len(records),
            "event_trades_closed": sum(1 for r in records if r["role"] == "event" and r["ok"]),
            "control_trades_closed": sum(1 for r in records if r["role"] == "control" and r["ok"]),
        },
        "macro_calendar": {"fomc_dates": len(fomc), "cpi": None if cpi is None else len(cpi)},
        "declared_limits": LIMITS,
    }


LIMITS = (
    "End-of-day basis: entries and marks are the day's closing quotes, so a target or stop is seen only "
    "at a close and an expiry day closes at its end-of-day mark; the direction of the bias is unknown.",
    "The universe is today's liquid list (hindsight), with the point-in-time dollar-volume, weekly and "
    "spread tests applied.",
    "The open interest floor and the option volume floor are not reproduced (the store holds no open "
    "interest); the realised-move dispersion ceiling is not applied (it is not in the declaration).",
    "The front expiry is the weekly Friday; Monday and Wednesday listings are not seen.",
    "Early assignment is not modelled; a split ends the trade at the session before.",
    "A leg with no two-sided quote at a mark is carried at its last earlier quote (counted).",
    "The wing multiple's IV/RV band uses Dolt's volatility history, as the module's own fallback.",
)


def cmd_study(args) -> int:
    from cherrypick.backtest import chainfetch, settings

    d = _out_dir(args.out_dir)
    pilot = json.loads((d / "pilot.json").read_text(encoding="utf-8"))
    if not pilot.get("decided") or pilot.get("basis") != "end_of_day":
        raise SystemExit("the pilot did not choose the end-of-day basis; this study is that basis only")
    events = json.loads((d / "events.json").read_text(encoding="utf-8"))["events"]
    doc = json.loads((d / "calendar.json").read_text(encoding="utf-8"))
    reports, span = reports_from_document(doc)
    names = sorted({e["symbol"] for e in events})
    data = load_market_data(names, args.host, args.port)
    moves = {
        s: reaction_moves(s, [(date.fromisoformat(x), w) for x, w in v], data)
        for s, v in doc["reports"].items()
    }
    store = chainfetch.db_path(settings.load(None))
    conn = sqlite3.connect(f"file:{store.as_posix()}?mode=ro", uri=True)
    try:
        records = run_study(events, reports, span, Study(data, Chains(conn), study_config(), moves))
    finally:
        conn.close()
    fomc, cpi = macro_calendar(d / "macro_calendar.json")
    result = summarise_results(records, fomc, cpi)
    (d / "trades.json").write_text(json.dumps(clean(records)), encoding="utf-8")
    path = d / "results.json"
    path.write_text(json.dumps(clean(result), indent=1, default=str), encoding="utf-8")
    path.with_suffix(".md").write_text(report(clean(result)), encoding="utf-8")
    print(json.dumps({"verdict": {k: v["verdict"] for k, v in result["verdict"].items()}, "file": str(path)}))
    return 0


def cmd_report(args) -> int:
    d = _out_dir(args.out_dir)
    src = d / "results.json"
    src.with_suffix(".md").write_text(report(json.loads(src.read_text(encoding="utf-8"))), encoding="utf-8")
    return 0


CHECK_LABELS = {
    "net_t_holm": "Mean net above zero, t over 2, Holm",
    "pair_t_holm": "Paired difference above zero, t over 2, Holm",
    "net_each_time_half": "Net positive in each time-half",
    "net_each_name_half": "Net positive in each name-half",
    "pair_each_time_half": "Paired positive in each time-half",
    "stress_positive": "Positive at 1.5x fees and a 25% haircut",
    "best_five_removed_positive": "Positive without the best five trades",
}


def _pts(x) -> str:
    """A share of capital at risk as signed points: 0.0123 -> +1.2."""
    if x is None:
        return ""
    v = round(100 * x, 1)
    return f"{'-' if v < 0 else '+' if v > 0 else ''}{abs(v):.1f}"


def _num(x, nd: int = 2) -> str:
    return "" if x is None else f"{x:.{nd}f}"


def findings(result: dict) -> list[str]:
    """The plain-English reading, worked out from the figures each run."""
    out = []
    arms = result["arms"]
    passing = [a for a, v in arms.items() if v["verdict"] == "PASS"]
    nov = [a for a, v in arms.items() if v["verdict"] == "NO VERDICT"]
    out.append(
        f"**Arms passing:** {', '.join(passing) if passing else 'none'}. "
        f"**No verdict:** {', '.join(nov) if nov else 'none'}."
    )
    for a, v in arms.items():
        if v["reading"]:
            out.append(f"**{a}:** {v['reading']}.")
        if v.get("gate_passes_ungated_fails"):
            out.append(
                f"**{a}:** passes while its ungated arm does not; "
                "recorded as the gate's pass, never as the strategy's."
            )
    for a, v in arms.items():
        m = v.get("mde")
        if m and m.get("mde") is not None:
            out.append(
                f"**{a} sensitivity (B12):** no edge larger than about {_pts(m['mde'])} points of "
                "capital at risk per trade can be ruled out."
            )
            break
    gaps = [s for s, g in result["drops"]["paired_halves"].items() if g["flag"]]
    if gaps:
        out.append(
            f"**Drop rates** differ by more than 5 points between the design halves for: {', '.join(gaps)}."
        )
    return out


def report(result: dict) -> str:
    """results.md beside results.json, for the console's studies tab."""
    arms = result["arms"]
    lines = [
        "# Item 31: the earnings module's paper rule",
        "",
        f"End-of-day basis, {result['window']}. Net and paired figures are points of capital at risk "
        "(the widest wing less the credit): +1.0 means one point of the money at risk earned per trade.",
        "",
        "## Verdicts",
        "",
        "| arm | verdict | design pairs | mean net | t | mean paired | t | holdout net | holdout paired |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for arm, a in arms.items():
        d, h = a["design"], a["holdout"]
        lines.append(
            f"| {arm} | {a['verdict'].lower()} | {d['pairs']} | {_pts(d['net']['mean'])}"
            f" | {_num(d['net']['t'])} | {_pts(d['pair']['mean'])} | {_num(d['pair']['t'])}"
            f" | {_pts(h['net']['mean'])} | {_pts(h['pair']['mean'])} |"
        )
    lines += [
        "",
        "## Design checks",
        "",
        "| arm | " + " | ".join(CHECK_LABELS.values()) + " |",
        "|---|" + "---|" * len(CHECK_LABELS),
    ]
    for arm, a in arms.items():
        cells = " | ".join("pass" if a["checks"][k] else "fail" for k in CHECK_LABELS)
        lines.append(f"| {arm} | {cells} |")
    lines += ["", "## What it says", "", *[f"- {x}" for x in findings(result)]]
    lines += [
        "",
        "## Reported, not judged (design window)",
        "",
        "| arm | mid + fees | natural | stress | first morning | to expiry | no PEAD gate"
        " | without 2020-02..06 | win rate |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for arm, a in arms.items():
        r = a["reported"].get("design") or {}
        if not r:
            continue
        f, x = r["fills"], r["exits"]
        lines.append(
            f"| {arm} | {_pts(f['mid_fees'])} | {_pts(f['natural'])} | {_pts(f['stress'])}"
            f" | {_pts(x['first_morning'])} | {_pts(x['held_to_expiry'])} | {_pts(x['no_pead'])}"
            f" | {_pts(r['without_2020_02_to_06'])} | {_num(r['win_rate'], 3)} |"
        )
    lines += ["", "## Where the trades fall out", ""]
    for k, v in result["drops"]["by_reason"].items():
        lines.append(f"- {k}: {v}")
    lines += ["", "## Declared limits", "", *[f"- {x}" for x in result["declared_limits"]]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("plan", help="the event list from Dolt")
    pl.add_argument("--host", default="127.0.0.1")
    pl.add_argument("--port", type=int, default=3306)
    pl.add_argument("--out")
    pl.set_defaults(fn=cmd_plan)
    pi = sub.add_parser("pilot", help="the pilot's request file, and its coverage report once fetched")
    pi.add_argument("--out-dir")
    pi.set_defaults(fn=cmd_pilot)
    for name, fn, text in (
        ("requests", cmd_requests, "the end-of-day chain and path request files, less what is stored"),
        ("study", cmd_study, "the declared replay on end-of-day quotes; writes results.json and results.md"),
    ):
        sp = sub.add_parser(name, help=text)
        sp.add_argument("--host", default="127.0.0.1")
        sp.add_argument("--port", type=int, default=3306)
        sp.add_argument("--out-dir")
        sp.set_defaults(fn=fn)
    rp = sub.add_parser("report", help="results.md from an existing results.json (local)")
    rp.add_argument("--out-dir")
    rp.set_defaults(fn=cmd_report)
    rc = sub.add_parser("replay-check", help="the entries-only check against the paper ledger")
    rc.add_argument("--host", default="127.0.0.1")
    rc.add_argument("--port", type=int, default=3306)
    rc.add_argument("--out-dir")
    rc.add_argument("--requests-only", action="store_true")
    rc.set_defaults(fn=cmd_replay_check)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
