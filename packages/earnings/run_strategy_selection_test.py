#!/usr/bin/env python3
"""
10-Day Earnings Entry Condition Framework Test
Demonstrates strategy selection across market days
"""

import json
from datetime import datetime
from dataclasses import dataclass


@dataclass
class Candidate:
    symbol: str
    dispersion: float
    iv_rank: float
    description: str


def select_strategy(c: Candidate) -> dict:
    """Route candidate to optimal strategy using entry condition matrix."""

    d = c.dispersion
    iv = c.iv_rank

    if d < 0.15:
        if iv > 1.20:
            strategy = "SHORT_STRADDLE"
            reason = "Low dispersion + High IV - Naked ATM justified"
        elif iv > 1.00:
            strategy = "IRON_FLY"
            reason = "Low dispersion + Medium IV - Hedged straddle"
        else:
            strategy = "ATM_CALENDAR"
            reason = "Low dispersion + Low IV - Term structure edge"
    elif d < 0.20:
        strategy = "BROKEN_WING_BUTTERFLY" if iv > 1.20 else "IRON_FLY"
        reason = "Medium-low dispersion - Mostly predictable"
    elif d < 0.25:
        strategy = "DIRECTIONAL_SPREAD" if iv > 1.15 else "IRON_CONDOR"
        reason = "Medium dispersion - Some variance accepted"
    elif d < 0.30:
        strategy = "REVERSE_FLY"
        reason = "Medium-high dispersion - Gap premium environment"
    else:
        strategy = "REJECT"
        reason = "Dispersion too high - Skip candidate"

    return {
        "symbol": c.symbol,
        "strategy": strategy,
        "reason": reason,
        "metrics": {"dispersion": d, "iv_rank": iv}
    }


# Simulated 10 market days
MARKET_DATA = {
    "07/08/2026": [
        Candidate("AAPL", 0.0125, 1.25, "Tech mega-cap"),
        Candidate("BIO", 0.087, 0.95, "Biotech volatile"),
        Candidate("JPM", 0.045, 1.18, "Financial stable"),
    ],
    "07/09/2026": [
        Candidate("MSFT", 0.015, 1.35, "Tech consistent"),
        Candidate("GS", 0.038, 1.40, "Investment bank"),
        Candidate("BABA", 0.092, 1.30, "Chinese tech"),
    ],
    "07/10/2026": [
        Candidate("QUIET", 0.0065, 0.78, "Blue chip flat"),
        Candidate("PG", 0.012, 0.85, "Consumer staples"),
    ],
    "07/11/2026": [
        Candidate("NVDA", 0.062, 1.42, "GPU semiconductor"),
        Candidate("AMD", 0.055, 1.38, "Semiconductor"),
        Candidate("QCOM", 0.048, 1.32, "Semiconductor"),
    ],
    "07/12/2026": [
        Candidate("XOM", 0.035, 1.05, "Energy moderate"),
        Candidate("CVX", 0.032, 1.08, "Energy"),
    ],
    "07/15/2026": [
        Candidate("BAC", 0.042, 1.12, "Bank"),
        Candidate("WFC", 0.048, 1.10, "Bank volatile"),
        Candidate("C", 0.055, 1.15, "Citigroup"),
    ],
    "07/16/2026": [
        Candidate("JNJ", 0.013, 1.18, "Healthcare"),
        Candidate("UNH", 0.018, 1.22, "Healthcare leader"),
        Candidate("BIIB", 0.075, 1.25, "Biotech risky"),
    ],
    "07/17/2026": [
        Candidate("WMT", 0.025, 0.95, "Retail stable"),
        Candidate("TGT", 0.038, 1.02, "Target"),
    ],
    "07/18/2026": [
        Candidate("GE", 0.052, 1.08, "Industrial"),
        Candidate("BA", 0.065, 1.12, "Boeing risky"),
        Candidate("CAT", 0.035, 1.10, "Caterpillar"),
    ],
    "07/19/2026": [
        Candidate("TSLA", 0.085, 1.50, "Tesla volatile"),
        Candidate("F", 0.048, 1.05, "Ford"),
    ],
}


def main():
    print("\n" + "="*90)
    print("10-DAY EARNINGS ENTRY CONDITION FRAMEWORK TEST")
    print("="*90)

    all_results = []
    strategy_totals = {}

    for date_str in sorted(MARKET_DATA.keys()):
        candidates = MARKET_DATA[date_str]

        print(f"\n[{date_str}] Analyzing {len(candidates)} candidates")
        print("-"*90)

        day_results = []
        for c in candidates:
            result = select_strategy(c)
            day_results.append(result)
            all_results.append({**result, "date": date_str})

            strategy = result["strategy"]
            strategy_totals[strategy] = strategy_totals.get(strategy, 0) + 1

            # Print result
            status = "[OK]" if strategy != "REJECT" else "[SKIP]"
            print(f"  {status} {c.symbol:6} s={c.dispersion:.4f} IV={c.iv_rank:.2f} -> {strategy}")
            print(f"       {result['reason']}")

    # Summary
    print("\n" + "="*90)
    print("10-DAY SUMMARY")
    print("="*90)

    total_candidates = len(all_results)
    print(f"Total candidates: {total_candidates}")
    print("\nStrategy distribution:")
    for strategy in sorted(strategy_totals.keys(), key=lambda s: strategy_totals[s], reverse=True):
        count = strategy_totals[strategy]
        pct = 100 * count / total_candidates
        print(f"  {strategy:30} {count:2} ({pct:5.1f}%)")

    # Save results
    with open("strategy_selection_results.json", "w") as f:
        json.dump({
            "summary": {
                "dates": len(MARKET_DATA),
                "total_candidates": total_candidates,
                "strategies": strategy_totals,
            },
            "results": all_results,
        }, f, indent=2)

    print("\nResults saved to strategy_selection_results.json")
    print("="*90)


if __name__ == "__main__":
    main()
