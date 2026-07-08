#!/usr/bin/env python3
"""
10-Day Earnings Scan Test Harness

Tests the entry condition framework by:
1. Iterating through next 10 market days
2. Running earnings scan for each day
3. Analyzing candidates with decision matrix
4. Logging optimal strategy selection with reasoning
"""

import json
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from src import scanner


def get_next_market_days(start_date_str: str, num_days: int = 10) -> list[str]:
    """Generate next N market days (excluding weekends)."""
    start = datetime.strptime(start_date_str, "%m/%d/%Y")
    market_days = []
    current = start

    while len(market_days) < num_days:
        # Skip weekends (5 = Saturday, 6 = Sunday)
        if current.weekday() < 5:
            market_days.append(current.strftime("%m/%d/%Y"))
        current += timedelta(days=1)

    return market_days


def analyze_candidate(symbol: str, criteria: dict) -> dict:
    """Analyze single candidate against decision matrix."""

    analysis = {
        "symbol": symbol,
        "metrics": {
            "dispersion_pct": criteria.get("realized_move_dispersion_pct"),
            "expected_move_pct": criteria.get("expected_move_pct"),
            "iv_rank": criteria.get("iv_rv_ratio"),  # Proxy for IV quality
            "winrate": criteria.get("winrate"),
            "market_cap": criteria.get("market_cap"),
        },
        "decision_path": [],
        "recommended_strategy": None,
        "reasoning": "",
    }

    # Extract key metrics
    dispersion = criteria.get("realized_move_dispersion_pct")
    expected_move = criteria.get("expected_move_pct", 0)
    iv_rank = criteria.get("iv_rv_ratio", 0)

    # Decision 1: Check dispersion
    if dispersion is None:
        analysis["decision_path"].append("❌ Dispersion unverified (skip)")
        analysis["reasoning"] = "No realized move history available"
        return analysis

    analysis["decision_path"].append(f"✓ Dispersion: {dispersion:.4f}")

    # Decision 2: Evaluate realized vs expected (estimate)
    if expected_move > 0:
        ratio_estimate = "≈ Expected" if dispersion < 0.20 else ">> Expected (high var)"
    else:
        ratio_estimate = "Unknown"

    # Route through decision matrix
    if dispersion < 0.15:
        analysis["decision_path"].append("✓ Low dispersion (σ < 0.15)")

        if iv_rank > 1.20:
            analysis["recommended_strategy"] = "SHORT_STRADDLE"
            analysis["reasoning"] = f"Low dispersion + high IV rank ({iv_rank:.2f}). Naked ATM straddle justified by predictability and premium."
        elif iv_rank > 1.00:
            analysis["recommended_strategy"] = "IRON_FLY"
            analysis["reasoning"] = f"Low dispersion + medium IV ({iv_rank:.2f}). ATM hedged straddle offers predictable risk."
        else:
            analysis["recommended_strategy"] = "ATM_CALENDAR"
            analysis["reasoning"] = f"Low dispersion + low IV ({iv_rank:.2f}). Calendar spread captures term structure edge."

    elif dispersion < 0.20:
        analysis["decision_path"].append("✓ Medium-low dispersion (0.15 ≤ σ < 0.20)")

        if iv_rank > 1.20:
            analysis["recommended_strategy"] = "BROKEN_WING_BUTTERFLY"
            analysis["reasoning"] = f"Medium dispersion + high IV ({iv_rank:.2f}). Asymmetric straddle for skew edge."
        else:
            analysis["recommended_strategy"] = "IRON_FLY"
            analysis["reasoning"] = f"Medium dispersion + medium IV ({iv_rank:.2f}). Hedged straddle with wing protection."

    elif dispersion < 0.25:
        analysis["decision_path"].append("✓ Medium dispersion (0.20 ≤ σ < 0.25)")

        if iv_rank > 1.15:
            analysis["recommended_strategy"] = "DIRECTIONAL_SPREAD"
            analysis["reasoning"] = f"Medium dispersion + good IV ({iv_rank:.2f}). Directional 2-leg spread for skew arbitrage."
        else:
            analysis["recommended_strategy"] = "IRON_CONDOR"
            analysis["reasoning"] = f"Medium dispersion + modest IV ({iv_rank:.2f}). Wide-range strangle at expected-move boundaries."

    elif dispersion < 0.30:
        analysis["decision_path"].append("✓ Medium-high dispersion (0.25 ≤ σ < 0.30)")
        analysis["recommended_strategy"] = "REVERSE_FLY"
        analysis["reasoning"] = f"High dispersion ({dispersion:.4f}) suggests historical gap premium. Long straddle hedge with defined risk."

    else:
        analysis["decision_path"].append("❌ High dispersion (σ ≥ 0.30)")
        analysis["reasoning"] = f"Dispersion too high ({dispersion:.4f}). Stock is unpredictable; no viable strategy."
        analysis["recommended_strategy"] = "REJECT"

    return analysis


def log_daily_scan(date_str: str, candidates: list[dict]) -> dict:
    """Log results for one day's scan."""

    daily_log = {
        "date": date_str,
        "timestamp": datetime.now().isoformat(),
        "candidates_found": len(candidates),
        "analyses": [],
        "strategy_summary": {},
    }

    # Analyze each candidate
    strategy_counts = {}
    for candidate in candidates:
        symbol = candidate.get("symbol", "UNKNOWN")
        criteria = candidate.get("criteria", {})

        analysis = analyze_candidate(symbol, criteria)
        daily_log["analyses"].append(analysis)

        strategy = analysis.get("recommended_strategy")
        if strategy:
            strategy_counts[strategy] = strategy_counts.get(strategy, 0) + 1

    daily_log["strategy_summary"] = strategy_counts

    return daily_log


def print_day_report(daily_log: dict) -> None:
    """Pretty-print daily scan results."""

    print("\n" + "="*80)
    print(f"EARNINGS SCAN: {daily_log['date']}")
    print("="*80)
    print(f"Candidates found: {daily_log['candidates_found']}")

    if not daily_log['analyses']:
        print("⚠️  No candidates available for analysis")
        return

    print("\nSTRATEGY RECOMMENDATIONS:")
    print("-"*80)

    for analysis in daily_log['analyses']:
        symbol = analysis['symbol']
        strategy = analysis['recommended_strategy']
        reasoning = analysis['reasoning']
        dispersion = analysis['metrics']['dispersion_pct']

        # Format output
        status = "✓" if strategy != "REJECT" else "❌"
        print(f"\n{status} {symbol}")
        print(f"   Dispersion: {dispersion:.4f if dispersion else 'N/A'}")
        print(f"   Strategy: {strategy or 'UNKNOWN'}")
        print(f"   Reasoning: {reasoning}")

    print("\nSTRATEGY DISTRIBUTION:")
    print("-"*80)
    for strategy, count in daily_log['strategy_summary'].items():
        print(f"  {strategy}: {count} candidate(s)")

    print("\n")


def run_10day_sweep():
    """Run earnings scan across next 10 market days."""

    config = scanner._load_config()

    # Get next 10 market days
    start_date = "07/08/2026"  # Today
    market_days = get_next_market_days(start_date, num_days=10)

    print("\n" + "="*80)
    print("10-DAY EARNINGS SCAN & ENTRY ANALYSIS TEST HARNESS")
    print("="*80)
    print(f"Starting: {start_date}")
    print(f"Market days: {', '.join(market_days)}")
    print("="*80)

    all_daily_logs = []
    strategies_by_day = {}
    total_candidates = 0
    strategy_totals = {}

    # Scan each market day
    for date_str in market_days:
        print(f"\n[{date_str}] Running earnings scan...", end="", flush=True)

        try:
            # Try to fetch earnings calendar and run scans
            # For now, simulate with empty result if database unavailable
            result = {"ok": True, "date": date_str, "candidates": [], "error": None}

            try:
                entry_window = scanner.fetch_entry_window_calendar(date_str, config)
                if entry_window.get("ok"):
                    candidates = entry_window.get("candidates", [])
                    result["candidates"] = candidates
            except Exception as db_err:
                result["error"] = f"Database unavailable: {str(db_err)}"

            if not result.get("ok"):
                print(f" ⚠️  Error: {result.get('error', 'Unknown error')}")
                daily_log = {
                    "date": date_str,
                    "timestamp": datetime.now().isoformat(),
                    "candidates_found": 0,
                    "analyses": [],
                    "strategy_summary": {},
                    "error": result.get("error"),
                }
            else:
                candidates = result.get("candidates", [])
                daily_log = log_daily_scan(date_str, candidates)
                print(f" ✓ {daily_log['candidates_found']} candidates")

            # Print daily report
            print_day_report(daily_log)

            # Aggregate results
            all_daily_logs.append(daily_log)
            total_candidates += daily_log['candidates_found']
            strategies_by_day[date_str] = daily_log['strategy_summary']

            for strategy, count in daily_log['strategy_summary'].items():
                strategy_totals[strategy] = strategy_totals.get(strategy, 0) + count

        except Exception as e:
            print(f" ❌ Exception: {str(e)}")
            daily_log = {
                "date": date_str,
                "timestamp": datetime.now().isoformat(),
                "candidates_found": 0,
                "analyses": [],
                "strategy_summary": {},
                "error": str(e),
            }
            all_daily_logs.append(daily_log)

    # Summary report
    print("\n" + "="*80)
    print("10-DAY SUMMARY")
    print("="*80)
    print(f"Total candidates analyzed: {total_candidates}")
    print(f"Date range: {market_days[0]} to {market_days[-1]}")
    print("\nStrategy distribution (10 days):")
    print("-"*80)
    for strategy in sorted(strategy_totals.keys()):
        count = strategy_totals[strategy]
        print(f"  {strategy}: {count}")

    print("\n" + "="*80)
    print("TEST HARNESS COMPLETE")
    print("="*80)

    # Save full report
    report_file = Path(__file__).parent / "earnings_scan_report.json"
    with open(report_file, "w") as f:
        json.dump({
            "summary": {
                "start_date": market_days[0],
                "end_date": market_days[-1],
                "num_days": len(market_days),
                "total_candidates": total_candidates,
                "strategy_totals": strategy_totals,
            },
            "daily_logs": all_daily_logs,
        }, f, indent=2)

    print(f"\nDetailed report saved to: {report_file}")


if __name__ == "__main__":
    run_10day_sweep()
