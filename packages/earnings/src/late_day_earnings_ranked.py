#!/usr/bin/env python3
"""
Paper trading analysis: Scan earnings candidates and rank by strategy conviction.

Usage:
    python late_day_earnings_ranked.py
    python late_day_earnings_ranked.py --mode auto --count 2
    python late_day_earnings_ranked.py --config conservative
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

# Configuration (edit these values to customize)
CONFIG = {
    "max_positions_per_day": 3,
    "available_capital": 50000,
    "min_conviction": "medium",  # conservative, moderate, aggressive
    "allow_naked_strategies": False,
    "auto_submit": False,
}


class StrategyRanker:
    """Ranks earnings candidates by strategy conviction."""

    def __init__(self, config: Dict):
        self.config = config
        self.log_dir = Path("logs/paper")
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def get_mock_candidates(self) -> List[Dict]:
        """Get mock candidates for demonstration."""
        return [
            {
                "symbol": "BMO",
                "earnings_time": "Pre-market",
                "expected_move": 3.2,
                "iv_rank": 1.35,
                "realized_vol_dispersion": 0.0125,
                "strategy": "SHORT_STRADDLE",
                "score": 84,
                "tier": "TIER 1",
                "entry_price": 5.10,
                "entry_quantity": 3,
            },
            {
                "symbol": "AMC",
                "earnings_time": "After close",
                "expected_move": 8.5,
                "iv_rank": 1.05,
                "realized_vol_dispersion": 0.0850,
                "strategy": "IRON_CONDOR",
                "score": 77,
                "tier": "TIER 2",
                "entry_price": 0.80,
                "entry_quantity": 6,
            },
            {
                "symbol": "JPM",
                "earnings_time": "Before open",
                "expected_move": 2.8,
                "iv_rank": 1.18,
                "realized_vol_dispersion": 0.0450,
                "strategy": "IRON_FLY",
                "score": 75,
                "tier": "TIER 2",
                "entry_price": 0.90,
                "entry_quantity": 5,
            },
            {
                "symbol": "XOM",
                "earnings_time": "After close",
                "expected_move": 2.1,
                "iv_rank": 0.95,
                "realized_vol_dispersion": 0.0350,
                "strategy": "IRON_FLY",
                "score": 68,
                "tier": "TIER 3",
                "entry_price": 0.75,
                "entry_quantity": 4,
            },
            {
                "symbol": "BAC",
                "earnings_time": "Before open",
                "expected_move": 3.5,
                "iv_rank": 0.85,
                "realized_vol_dispersion": 0.0420,
                "strategy": "IRON_CONDOR",
                "score": 62,
                "tier": "TIER 3",
                "entry_price": 0.65,
                "entry_quantity": 5,
            },
        ]

    def filter_by_conviction(self, candidates: List[Dict]) -> List[Dict]:
        """Filter candidates by conviction level."""
        min_score_map = {
            "conservative": 80,
            "moderate": 70,
            "aggressive": 60,
        }
        min_score = min_score_map.get(self.config["min_conviction"], 70)

        return [c for c in candidates if c.get("score", 0) >= min_score]

    def filter_by_capital(self, candidates: List[Dict]) -> List[Dict]:
        """Ensure sufficient capital for positions."""
        selected = []
        total_capital_used = 0

        for candidate in candidates:
            # Rough estimate: $1500 per spread
            capital_needed = 1500 * candidate.get("entry_quantity", 1)

            if (
                total_capital_used + capital_needed
                <= self.config["available_capital"]
            ):
                selected.append(candidate)
                total_capital_used += capital_needed

                if len(selected) >= self.config["max_positions_per_day"]:
                    break

        return selected

    def rank_candidates(self, candidates: List[Dict]) -> List[Dict]:
        """Rank candidates by conviction score."""
        return sorted(candidates, key=lambda x: x.get("score", 0), reverse=True)

    def analyze(self) -> Dict:
        """Run complete analysis."""
        candidates = self.get_mock_candidates()
        total_candidates = len(candidates)

        # Filter by conviction
        conviction_filtered = self.filter_by_conviction(candidates)

        # Filter by capital
        selected = self.filter_by_capital(conviction_filtered)
        selected = self.rank_candidates(selected)

        rejected = [c for c in candidates if c not in selected]

        return {
            "timestamp": datetime.now().isoformat(),
            "date": datetime.now().strftime("%Y-%m-%d"),
            "total_candidates": total_candidates,
            "passed_conviction": len(conviction_filtered),
            "selected": selected,
            "rejected": rejected,
            "average_score": (
                sum(c.get("score", 0) for c in selected) / len(selected)
                if selected
                else 0
            ),
        }

    def log_analysis(self, analysis: Dict) -> None:
        """Log analysis to file."""
        log_file = self.log_dir / f"runs_{datetime.now().strftime('%Y_%m')}.json"

        runs = []
        if log_file.exists():
            with open(log_file) as f:
                runs = json.load(f)

        runs.append(
            {
                "date": analysis["date"],
                "timestamp": analysis["timestamp"],
                "total_candidates": analysis["total_candidates"],
                "selected": len(analysis["selected"]),
                "trades": analysis["selected"],
            }
        )

        with open(log_file, "w") as f:
            json.dump(runs, f, indent=2)

    def print_report(self, analysis: Dict) -> None:
        """Print analysis report."""
        print("=" * 80)
        print(f"PAPER TRADING ANALYSIS - {analysis['date']}")
        print("=" * 80)
        print()

        print("ANALYSIS SUMMARY")
        print(f"  Total candidates: {analysis['total_candidates']}")
        print(f"  Selected for trading: {len(analysis['selected'])}")
        print(f"  Rejected/waitlisted: {len(analysis['rejected'])}")
        print()

        if analysis["selected"]:
            print("SELECTED TRADES (Ranked by Score)")
            for i, trade in enumerate(analysis["selected"], 1):
                print(f"  {i}. {trade['symbol']:<8} Score: {trade.get('score', 0)}")
                print(f"     Strategy: {trade['strategy']}")
                print(f"     Entry: ${trade.get('entry_price', 0):.2f}")
                print(f"     Quantity: {trade.get('entry_quantity', 0)}")
                print(f"     Tier: {trade.get('tier', 'N/A')}")
                print()

        if analysis["rejected"]:
            print(f"REJECTED ({len(analysis['rejected'])})")
            for trade in analysis["rejected"]:
                print(f"  - {trade['symbol']}: Score {trade.get('score', 0)} (below threshold)")
            print()

        print("READY FOR 3:50 PM ENTRY WINDOW")
        print(f"Entry orders logged to: logs/paper/runs_{datetime.now().strftime('%Y_%m')}.json")
        print("=" * 80)


def main():
    """Run analysis and log results."""
    # Parse arguments
    mode = "manual"
    count = 3
    config_profile = "moderate"

    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--mode" and i + 1 < len(sys.argv) - 1:
            mode = sys.argv[i + 2]
        elif arg == "--count" and i + 1 < len(sys.argv) - 1:
            count = int(sys.argv[i + 2])
        elif arg == "--config" and i + 1 < len(sys.argv) - 1:
            config_profile = sys.argv[i + 2]

    # Update config
    CONFIG["min_conviction"] = (
        "conservative" if config_profile == "conservative" else
        "aggressive" if config_profile == "aggressive" else
        "moderate"
    )
    CONFIG["max_positions_per_day"] = count

    # Run analysis
    ranker = StrategyRanker(CONFIG)
    analysis = ranker.analyze()

    # Log results
    ranker.log_analysis(analysis)

    # Print report
    ranker.print_report(analysis)


if __name__ == "__main__":
    main()
