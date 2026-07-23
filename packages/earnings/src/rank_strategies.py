"""Cross-strategy, cross-symbol ranking for the entry-window evaluation.

Every strategy's own get_candidates only ever considers itself --
iron_fly.py get_candidates only ranks iron fly candidates, double_calendar.py
only double calendar, and so on. With six strategies now, there's no single
answer to "what should we actually trade tonight" without running all six
per symbol and comparing. This tool closes that gap: for each symbol on
tonight's/tomorrow-morning's earnings calendar (scanner.fetch_entry_window_calendar's
merge), every registered strategy is evaluated, the single best-ranked
viable strategy per symbol is picked (a second strategy on the same name
would concentrate correlated same-name gap risk, not diversify it), and the
existing scanner.select_positions() caps/correlation-blocks across the
resulting cross-symbol ranked list exactly as it already does for any
single strategy's candidates.

Score comparison reuses scanner.compute_composite_score's existing
signal-strength formula as-is (term_structure, or skew_abs for
expected_move_butterfly, times iv_rv_ratio times shrunk_winrate) -- not
risk-adjusted for how differently these strategies pay off (defined vs.
undefined risk, credit vs. debit), deferred until there's real trade data
to calibrate a risk adjustment against.

NOT wired into the live/paper trading loop's Step 4b in this pass --
get_ranked_symbols is a standalone tool, fully usable on its own, same
state double_calendar/expected_move_butterfly's entries are already in
(built, not loop-wired) until that's explicitly asked for separately.

Every run also writes an audit trail via db.py/db_paper.py's existing
scan_log (per config's paper_mode) -- one row per (symbol, strategy)
evaluated, plus one summary row per symbol (strategy = "_ranked", a
reserved name no real strategy uses) capturing both why the winning
strategy beat its within-symbol runner-up and where the symbol landed
relative to every other candidate symbol that day.

Commands (see CLAUDE.md's Tool Reference):
  get_ranked_symbols --date MM/DD/YYYY
"""

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

import scanner
from strategies import (
    atm_calendar,
    broken_wing_butterfly,
    directional_credit_spread,
    double_calendar,
    iron_condor,
    iron_fly,
)


def _dolt_port_open(host="127.0.0.1", port=3306):
    try:
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False


def _dolt_query_ready(databases=("earnings", "options", "stocks"), overall_timeout=30):
    """Poll ``SELECT 1`` against each needed Dolt database until it answers or the budget runs out.

    The TCP port binds before Dolt can serve its larger databases (the ``options`` chain table is
    big), so a plain port check can pass while the first real query still blocks. Gating on an actual
    query -- not just an open socket -- is what stops the entry scan from firing hundreds of heavy
    queries at a server that has not finished starting: the cold-start path behind the 30-minute
    hangs on 2026-07-14 and 2026-07-22.
    """
    import mysql.connector

    deadline = time.monotonic() + overall_timeout
    for db in databases:
        while True:
            try:
                conn = mysql.connector.connect(
                    host="127.0.0.1", port=3306, user="root", database=db, connection_timeout=5,
                )
                cur = conn.cursor()
                cur.execute("SELECT 1")
                cur.fetchall()
                conn.close()
                break
            except Exception:
                if time.monotonic() >= deadline:
                    return False
                time.sleep(1)
    return True


def _ensure_dolt_running():
    """Ensure the dolt SQL server is up AND actually serving queries before analysis."""
    if not _dolt_port_open():
        print("Starting dolt SQL server...", file=sys.stderr)
        try:
            # CREATE_NO_WINDOW (0 off-Windows): don't flash a console window when launched from a
            # windowless parent (e.g. the scheduled paper runs). dolt is a console app.
            subprocess.Popen(["dolt", "sql-server"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            print(f"Failed to start dolt: {e}", file=sys.stderr)
            return False

    # A bound socket is not proof Dolt can serve queries -- poll real queries with a deadline instead
    # of the old flat sleep(2), which returned before a cold server could answer and let the scan fire
    # heavy queries into a server that then blocked them.
    if not _dolt_query_ready():
        print("dolt sql-server did not become query-ready in time", file=sys.stderr)
        return False
    return True


def _verify_tastytrade_connection():
    """Verify tastytrade connection is active."""
    try:
        result = subprocess.run(
            [sys.executable, "src/tt.py", "get_connection_status"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            if data.get("ok") and data.get("connected"):
                return True
    except Exception as e:
        print(f"Connection check error: {e}", file=sys.stderr)
    return False


STRATEGY_REGISTRY = [
    {
        "name": "iron_fly",
        "fetch_criteria_fn": iron_fly.fetch_price_and_term_structure,
        "apply_tiering_fn": iron_fly.apply_tiering,
        "strategy_config_fn": iron_fly._strategy_config,
    },
    {
        "name": "double_calendar",
        "fetch_criteria_fn": double_calendar.fetch_price_and_expected_move,
        "apply_tiering_fn": double_calendar.apply_tiering,
        "strategy_config_fn": double_calendar._strategy_config,
        "extra_criteria_fn": double_calendar._add_dispersion,
    },
    {
        "name": "iron_condor",
        "fetch_criteria_fn": iron_condor.fetch_price_and_term_structure,
        "apply_tiering_fn": iron_condor.apply_tiering,
        "strategy_config_fn": iron_condor._strategy_config,
    },
    {
        "name": "atm_calendar",
        "fetch_criteria_fn": atm_calendar.fetch_price_and_term_structure,
        "apply_tiering_fn": atm_calendar.apply_tiering,
        "strategy_config_fn": atm_calendar._strategy_config,
    },
    {
        "name": "directional_credit_spread",
        "fetch_criteria_fn": directional_credit_spread.fetch_price_and_term_structure,
        "apply_tiering_fn": directional_credit_spread.apply_tiering,
        "strategy_config_fn": directional_credit_spread._strategy_config,
    },
    {
        "name": "broken_wing_butterfly",
        "fetch_criteria_fn": broken_wing_butterfly.fetch_price_and_expected_move,
        "apply_tiering_fn": broken_wing_butterfly.apply_tiering,
        "strategy_config_fn": broken_wing_butterfly._strategy_config,
    },
]


def _call_db(args_list: list[str], paper_mode: bool) -> dict:
    """Shells out to db_paper.py/db.py, matching scanner.call_tt's
    documented CLI-tool architecture -- this tool stays decoupled from
    either database module's own setup, same reasoning as scanner.py's
    relationship to tt.py.
    """
    db_script = "db_paper.py" if paper_mode else "db.py"
    db_path = Path(__file__).resolve().parent / db_script
    result = subprocess.run(
        [sys.executable, str(db_path), *args_list],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{db_script} {' '.join(args_list)} failed: {result.stderr.strip().splitlines()[-1] if result.stderr else 'unknown error'}")
    return json.loads(result.stdout)


def _has_listed_options(symbol: str) -> bool | None:
    """Cheap REST-only gate: does `symbol` have any listed option expirations?

    A bare `get_option_chain` (no expiration, no --include_* flags) returns just the
    chain structure over REST and skips every slow DXLink collector, so this costs one
    quick round-trip. Strategies make the identical bare call, so with the per-symbol
    cache active this is reused for free by names that pass.

    Returns True (has options), False (definitively none -- safe to skip), or None
    (ambiguous: a transient/timeout/connection error -- caller should run the full
    evaluation rather than wrongly discard a tradeable name).
    """
    try:
        chain = scanner.call_tt(["get_option_chain", "--symbol", symbol])
    except Exception:
        return None
    if chain.get("ok"):
        return bool(chain.get("chain"))
    # Only the explicit "no chain" response is definitive; anything else (tt_timeout,
    # connection) stays ambiguous so a message change degrades to the slow path, never
    # to a wrong skip.
    if "no option chain" in (chain.get("error") or "").lower():
        return False
    return None


def _no_options_result(name: str) -> dict:
    """A uniform Reject result for a symbol pre-filtered out for having no options,
    shaped like a normal `evaluate_symbol` entry so downstream logging/ranking is
    unchanged (one `no_listed_options` scan_log row per strategy)."""
    return {
        "name": name,
        "accepted": False,
        "reject_reasons": ["no_listed_options"],
        "criteria": {},
        "composite_score": 0.0,
        "broker_data_error": None,
    }


def _dolt_only_hard_fails(strategy_config: dict, avg_volume, iv_rv_ratio, winrate) -> list[str]:
    """The Dolt-computed soft criteria (avg_volume, iv_rv_ratio, winrate) that a strategy would
    reject on, each evaluated at its configured symbol_screen level -- the exact condition
    scanner._soft_gate turns into a `<name>_below_minimum` reject. Only a definite below-threshold
    value counts: a missing (None) or "off" criterion cannot be decided from the Dolt signals
    alone, so it must NOT gate out the broker fetch here."""
    levels = scanner._screen_levels(strategy_config)
    fails = []
    for value, name in ((avg_volume, "avg_volume"), (iv_rv_ratio, "iv_rv_ratio"), (winrate, "winrate")):
        level = levels[name]
        if level == "off" or value is None:
            continue
        key = f"near_miss_min_{name}" if level == "near_miss" else f"min_{name}"
        threshold = strategy_config.get(key)
        if threshold is not None and value < threshold:
            fails.append(f"{name}_below_minimum")
    return fails


def _dolt_reject_result(name: str, reject_reasons: list[str], criteria: dict) -> dict:
    """A rejected result carrying the Dolt reject reasons, shaped like a normal evaluate_symbol
    entry so downstream logging/ranking is unchanged (mirrors _no_options_result)."""
    return {
        "name": name,
        "accepted": False,
        "reject_reasons": reject_reasons,
        "criteria": criteria,
        "composite_score": 0.0,
        "broker_data_error": None,
    }


def evaluate_symbol(symbol: str, earnings_date, earnings_timing: str, config: dict) -> list[dict]:
    """Evaluate every registered strategy for one symbol. Common signals
    (avg_volume, iv_rv_ratio, winrate) are fetched once here, not once per
    strategy, since none of them depend on strategy specifics. Returns one
    result per strategy: {"name", "accepted", "reject_reasons", "criteria",
    "composite_score", "broker_data_error"}.
    """
    avg_volume = scanner.fetch_avg_volume(symbol, config)
    ivrv = scanner.fetch_iv_rv_ratio(symbol, config)
    iv_rv_ratio = ivrv["iv_rv_ratio"] if ivrv.get("ok") else None
    lookback = config.get("winrate_lookback_quarters", 8)
    winrate_result = scanner.compute_winrate(symbol, config, lookback)
    winrate = winrate_result["winrate"]
    winrate_sample_size = winrate_result["sample_size"]

    # Cheap pre-gate: skip the ~30s of live broker chain fetches when EVERY strategy would Reject
    # on a Dolt-only signal (avg_volume / iv_rv_ratio / winrate below its near-miss floor). No live
    # chain data can rescue a symbol already hard-failing a Dolt band, and the three Dolt signals
    # are already in hand -- so this turns a full broker scan into nothing for names that cannot
    # trade tonight regardless. A missing (None) signal is a near-miss, not a hard fail, so it never
    # triggers the skip: this can only reject symbols the full path would have rejected too.
    dolt_criteria = {
        "avg_volume": avg_volume,
        "iv_rv_ratio": iv_rv_ratio,
        "winrate": winrate,
        "winrate_sample_size": winrate_sample_size,
    }
    per_strategy_dolt_fails = {
        entry["name"]: _dolt_only_hard_fails(entry["strategy_config_fn"](config), avg_volume, iv_rv_ratio, winrate)
        for entry in STRATEGY_REGISTRY
    }
    if all(per_strategy_dolt_fails[entry["name"]] for entry in STRATEGY_REGISTRY):
        return [
            _dolt_reject_result(entry["name"], per_strategy_dolt_fails[entry["name"]], dict(dolt_criteria))
            for entry in STRATEGY_REGISTRY
        ]

    # Every strategy's fetch re-requests the same option chain for this symbol; a
    # per-symbol cache collapses those ~9 identical broker round-trips to one each.
    # Scoped to this symbol only (chain data is live) and always torn down.
    scanner.begin_tt_cache()
    try:
        # A name with no listed options can't trade any strategy -- skip the 7 live
        # chain fetches and reject it outright. Only a definitive "no chain" skips;
        # an ambiguous error falls through to the full evaluation below.
        if _has_listed_options(symbol) is False:
            return [_no_options_result(entry["name"]) for entry in STRATEGY_REGISTRY]

        results = []
        for entry in STRATEGY_REGISTRY:
            strategy_config = entry["strategy_config_fn"](config)
            criteria: dict = {}

            broker = entry["fetch_criteria_fn"](symbol, earnings_date, earnings_timing, config)
            if broker.get("ok"):
                criteria.update(broker["criteria"])
            broker_error = None if broker.get("ok") else broker.get("error")

            criteria["avg_volume"] = avg_volume
            criteria["iv_rv_ratio"] = iv_rv_ratio
            criteria["winrate"] = winrate
            criteria["winrate_sample_size"] = winrate_sample_size

            extra_fn = entry.get("extra_criteria_fn")
            if extra_fn is not None:
                extra_fn(symbol, config, lookback, criteria)

            tiering = entry["apply_tiering_fn"](criteria, strategy_config)
            score = scanner.compute_composite_score(criteria, winrate_sample_size)

            results.append({
                "name": entry["name"],
                "accepted": tiering["accepted"],
                "reject_reasons": tiering["reject_reasons"],
                "criteria": criteria,
                "composite_score": score,
                "broker_data_error": broker_error,
            })
    finally:
        scanner.end_tt_cache()
    return results


_REGISTRY_BY_NAME = {entry["name"]: entry for entry in STRATEGY_REGISTRY}


def reverify_symbol(symbol: str, strategy_name: str, earnings_date, earnings_timing: str, config: dict) -> dict:
    """Re-runs a single strategy's own fetch/tiering (fully fresh -- avg_volume/
    iv_rv_ratio/winrate are re-fetched here too, not reused from an earlier
    scan) and confirms it is still accepted. Used by the loop's entry-time
    re-verification step (CLAUDE.md Step 4b) instead of hand-rolled per-
    criterion prose -- the strategy's own apply_tiering already knows its own
    thresholds, so re-verification is just "run the same check again, right
    now," not a second, separately-maintained description of what to check.

    Returns {"ok": True} if still accepted, or {"ok": False, "reason":
    "reverify_failed_<top reject reason>"} otherwise (including if
    `strategy_name` isn't a registered strategy at all, or its fetch
    step itself failed).
    """
    entry = _REGISTRY_BY_NAME.get(strategy_name)
    if entry is None:
        return {"ok": False, "reason": f"reverify_failed_unknown_strategy_{strategy_name}"}

    strategy_config = entry["strategy_config_fn"](config)
    criteria: dict = {}

    broker = entry["fetch_criteria_fn"](symbol, earnings_date, earnings_timing, config)
    if broker.get("ok"):
        criteria.update(broker["criteria"])
    else:
        return {"ok": False, "reason": f"reverify_failed_{broker.get('error', 'fetch_error')}"}

    criteria["avg_volume"] = scanner.fetch_avg_volume(symbol, config)
    ivrv = scanner.fetch_iv_rv_ratio(symbol, config)
    criteria["iv_rv_ratio"] = ivrv["iv_rv_ratio"] if ivrv.get("ok") else None
    lookback = config.get("winrate_lookback_quarters", 8)
    winrate_result = scanner.compute_winrate(symbol, config, lookback)
    criteria["winrate"] = winrate_result["winrate"]

    extra_fn = entry.get("extra_criteria_fn")
    if extra_fn is not None:
        extra_fn(symbol, config, lookback, criteria)

    tiering = entry["apply_tiering_fn"](criteria, strategy_config)
    if not tiering["accepted"]:
        top_reason = (tiering["reject_reasons"] or ["screen_failed"])[0]
        return {"ok": False, "reason": f"reverify_failed_{top_reason}"}
    return {"ok": True, "criteria": criteria}


def _log_symbol_decision(scan_date: str, symbol_result: dict, paper_mode: bool) -> None:
    """Writes one scan_log row per (symbol, strategy) evaluated plus one
    "_ranked" summary row per symbol, via the existing log_scan command --
    no schema change, just persisting what get_candidates already computes
    per strategy (today only ever returned as JSON, never written) plus
    the cross-strategy decision itself.
    """
    symbol = symbol_result["symbol"]
    for r in symbol_result["strategies"]:
        reasons = r["reject_reasons"]
        decision = "accepted" if r["accepted"] else "rejected"
        _call_db([
            "log_scan", "--data", json.dumps({
                "scan_date": scan_date,
                "strategy": r["name"],
                "symbol": symbol,
                "tier": decision,
                "outcome": decision,
                "reason": "; ".join(reasons) if reasons else None,
                "logged_at": time.time(),
            }),
        ], paper_mode)

    _call_db([
        "log_scan", "--data", json.dumps({
            "scan_date": scan_date,
            "strategy": "_ranked",
            "symbol": symbol,
            "tier": None,
            "outcome": symbol_result["outcome"],
            "reason": symbol_result["reason"],
            "logged_at": time.time(),
        }),
    ], paper_mode)


def _evaluate_and_rank_symbol(symbol: str, entry_date, earnings_timing: str, config: dict) -> dict:
    """Evaluate a symbol's strategies and return ranking result."""
    strategy_results = evaluate_symbol(symbol, entry_date, earnings_timing, config)
    viable = sorted(
        (r for r in strategy_results if r["accepted"] and r["composite_score"] is not None),
        key=lambda r: r["composite_score"], reverse=True,
    )
    return {
        "symbol": symbol,
        "earnings_date": str(entry_date),
        "earnings_timing": earnings_timing,
        "strategies": strategy_results,
        "viable": viable,
        "best_strategy": viable[0]["name"] if viable else None,
        "best_score": viable[0]["composite_score"] if viable else None,
    }


def cmd_get_ranked_symbols(args) -> dict:
    _ensure_dolt_running()
    if not _verify_tastytrade_connection():
        return {"ok": False, "error": "tastytrade connection failed"}

    config = scanner._load_config()
    paper_mode = not config.get("enable_live_trading", False)
    calendar = scanner.fetch_entry_window_calendar(config)

    per_symbol = []
    max_workers = min(4, len(calendar))
    if max_workers > 1 and len(calendar) > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_evaluate_and_rank_symbol, entry["symbol"], entry["date"], entry["timing"], config): entry
                for entry in calendar
            }
            for future in as_completed(futures):
                try:
                    result = future.result()
                    per_symbol.append(result)
                except Exception as e:
                    entry = futures[future]
                    print(f"Error evaluating {entry['symbol']}: {e}", file=sys.stderr)
                    per_symbol.append({
                        "symbol": entry["symbol"],
                        "earnings_date": str(entry["date"]),
                        "earnings_timing": entry["timing"],
                        "strategies": [],
                        "viable": [],
                        "best_strategy": None,
                        "best_score": None,
                    })
    else:
        for entry in calendar:
            result = _evaluate_and_rank_symbol(entry["symbol"], entry["date"], entry["timing"], config)
            per_symbol.append(result)

    rankable = [s for s in per_symbol if s["best_strategy"] is not None]
    rankable.sort(key=lambda s: s["best_score"], reverse=True)

    ranked_for_selection = [{"symbol": s["symbol"], "composite_score": s["best_score"]} for s in rankable]
    selection = scanner.select_positions(ranked_for_selection, config)
    skip_reason_by_symbol = {s["symbol"]: s["reason"] for s in selection["skipped"]}
    selected_symbols = {s["symbol"] for s in selection["selected"]}

    total = len(rankable)
    for i, s in enumerate(rankable):
        rank = i + 1
        winner = s["viable"][0]
        runner_up = s["viable"][1] if len(s["viable"]) > 1 else None
        within_symbol = f"selected {winner['name']} (score {winner['composite_score']:.4f})"
        if runner_up is not None:
            within_symbol += f" over {runner_up['name']} (score {runner_up['composite_score']:.4f})"
        within_symbol += " within this symbol"

        neighbors = f"ranked {rank}/{total} across today's universe"
        if rank < total:
            lower = rankable[rank]
            neighbors += f", ahead of {lower['symbol']} (score {lower['best_score']:.4f})"
        if rank > 1:
            higher = rankable[rank - 2]
            neighbors += f", behind {higher['symbol']} (score {higher['best_score']:.4f})"

        if s["symbol"] in selected_symbols:
            s["outcome"] = "selected"
        else:
            s["outcome"] = skip_reason_by_symbol.get(s["symbol"], "rejected_unknown")
        s["reason"] = f"{within_symbol}; {neighbors}"

    for s in per_symbol:
        if s["best_strategy"] is None:
            top_reasons = []
            for r in s["strategies"]:
                if r["reject_reasons"]:
                    top_reasons.append(f"{r['name']}: {r['reject_reasons'][0]}")
            s["outcome"] = "rejected_no_viable_strategy"
            s["reason"] = "; ".join(top_reasons) if top_reasons else "no strategy produced a reject reason"

    scan_date = str(scanner._date.today())
    for s in per_symbol:
        _log_symbol_decision(scan_date, s, paper_mode)

    return {
        "ok": True,
        "date": args.date,
        "symbols": [
            {
                "symbol": s["symbol"],
                "earnings_date": s["earnings_date"],
                "earnings_timing": s["earnings_timing"],
                "outcome": s["outcome"],
                "reason": s["reason"],
                "best_strategy": s["best_strategy"],
                "best_score": s["best_score"],
                "strategies": s["strategies"],
            }
            for s in per_symbol
        ],
        "ranked": rankable,
        "selected": list(selected_symbols),
    }


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p_ranked = sub.add_parser("get_ranked_symbols")
    p_ranked.add_argument("--date", required=True)

    args = parser.parse_args()
    dispatch = {
        "get_ranked_symbols": cmd_get_ranked_symbols,
    }
    result = dispatch[args.command](args)
    json.dump(result, sys.stdout, default=str)


if __name__ == "__main__":
    main()
