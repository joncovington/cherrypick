#!/usr/bin/env python3
"""
Paper trading analysis: real earnings candidates for tonight's/tomorrow-
morning's entry window, ranked by strategy conviction. Thin CLI wrapper
around rank_strategies.py get_ranked_symbols (cross-strategy ranking) and
each winning strategy's own get_order (concrete tradeable order) -- no
mock/fixture data, this is the live production path (see CLAUDE.md's
Loop Step 4b).

Usage:
    python late_day_earnings_ranked.py
    python late_day_earnings_ranked.py --count 2
    python late_day_earnings_ranked.py --config conservative

Note: rank_strategies.py's calendar fetch is always "today's AMC +
tomorrow's BMO" (scanner.fetch_entry_window_calendar) -- --date is passed
through for logging only and does not change which calendar day is
scanned. Use scanner.py get_calendar directly for historical dates.
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

TIER_FLOOR_BY_PROFILE = {
    "conservative": ("Tier 1",),
    "moderate": ("Tier 1", "Tier 2"),
    "aggressive": ("Tier 1", "Tier 2"),  # Tier 3 isn't surfaced by rank_strategies.py (viable-only filter)
}


def _run_python(args: list[str]) -> dict:
    result = subprocess.run(
        [sys.executable, *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed: {result.stderr.strip().splitlines()[-1] if result.stderr else 'unknown error'}")
    return json.loads(result.stdout)


class StrategyRanker:
    """Fetches real ranked candidates and builds tradeable orders."""

    def __init__(self, config: dict):
        self.config = config
        self.log_dir = Path("logs/paper")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.src_dir = Path(__file__).resolve().parent

    def get_ranked_candidates(self, date_str: str) -> dict:
        """Calls rank_strategies.py get_ranked_symbols for the real,
        live-scanned candidate universe."""
        return _run_python([
            str(self.src_dir / "rank_strategies.py"), "get_ranked_symbols",
            "--date", date_str,
        ])

    def filter_by_conviction(self, symbols: list[dict]) -> list[dict]:
        """Keep only symbols whose winning strategy tiered within this
        profile's floor. rank_strategies.py already restricts "viable" to
        Tier 1/2, so conservative further narrows to Tier 1 only."""
        allowed_tiers = TIER_FLOOR_BY_PROFILE.get(self.config["min_conviction"], TIER_FLOOR_BY_PROFILE["moderate"])
        kept = []
        for s in symbols:
            if s.get("outcome") != "selected" or not s.get("best_strategy"):
                continue
            winner = s["viable"][0] if s.get("viable") else None
            if winner and winner["tier"] in allowed_tiers:
                kept.append(s)
        return kept

    def build_orders(self, candidates: list[dict]) -> list[dict]:
        """For each selected symbol, build the concrete tradeable order via
        its winning strategy's own get_order."""
        trades = []
        for c in candidates[: self.config["max_positions_per_day"]]:
            strategy = c["best_strategy"]
            order_script = self.src_dir / "strategies" / f"{strategy}.py"
            try:
                order = _run_python([
                    str(order_script), "get_order",
                    "--symbol", c["symbol"],
                    "--earnings_date", c["earnings_date"],
                    "--earnings_timing", c["earnings_timing"],
                ])
            except Exception as exc:
                order = {"ok": False, "error": str(exc)}

            trades.append({
                "symbol": c["symbol"],
                "earnings_date": c["earnings_date"],
                "earnings_timing": c["earnings_timing"],
                "strategy": strategy,
                "score": c["best_score"],
                "tier": c["viable"][0]["tier"] if c.get("viable") else None,
                "order_ok": order.get("ok", False),
                "order": order if order.get("ok") else None,
                "order_error": None if order.get("ok") else order.get("error"),
                "entry_price": order.get("credit") or order.get("price"),
            })
        return trades

    def analyze(self, date_str: str) -> dict:
        """Run the real ranking + order-building pipeline."""
        ranked = self.get_ranked_candidates(date_str)
        if not ranked.get("ok"):
            return {
                "ok": False,
                "error": ranked.get("error", "get_ranked_symbols failed"),
                "date": date_str,
            }

        all_symbols = ranked["symbols"]
        selected = self.filter_by_conviction(all_symbols)
        rejected = [s for s in all_symbols if s not in selected]
        trades = self.build_orders(selected)

        return {
            "ok": True,
            "timestamp": datetime.now().isoformat(),
            "date": date_str,
            "total_candidates": len(all_symbols),
            "selected": trades,
            "rejected": rejected,
            "average_score": (
                sum(t["score"] for t in trades if t["score"] is not None) / len(trades)
                if trades
                else 0
            ),
        }

    def log_analysis(self, analysis: dict) -> None:
        """Append this run to the monthly log file."""
        log_file = self.log_dir / f"runs_{datetime.now().strftime('%Y_%m')}.json"

        runs = []
        if log_file.exists():
            with open(log_file) as f:
                runs = json.load(f)

        runs.append({
            "date": analysis["date"],
            "timestamp": analysis["timestamp"],
            "total_candidates": analysis["total_candidates"],
            "selected": len(analysis["selected"]),
            "trades": analysis["selected"],
        })

        with open(log_file, "w") as f:
            json.dump(runs, f, indent=2, default=str)

    def print_report(self, analysis: dict) -> None:
        """Print analysis report."""
        if not analysis.get("ok"):
            print(f"ERROR: {analysis.get('error')}")
            return

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
                print(f"  {i}. {trade['symbol']:<8} Score: {trade.get('score', 0):.4f}" if trade.get("score") is not None else f"  {i}. {trade['symbol']:<8} Score: N/A")
                print(f"     Strategy: {trade['strategy']}")
                print(f"     Tier: {trade.get('tier', 'N/A')}")
                if trade["order_ok"]:
                    print(f"     Entry: ${trade.get('entry_price', 0):.2f}")
                else:
                    print(f"     Order build FAILED: {trade.get('order_error')}")
                print()

        if analysis["rejected"]:
            print(f"REJECTED/WAITLISTED ({len(analysis['rejected'])})")
            for s in analysis["rejected"]:
                print(f"  - {s['symbol']}: {s.get('outcome', 'rejected')} ({s.get('reason', 'n/a')})")
            print()

        print("READY FOR 3:50 PM ENTRY WINDOW")
        print(f"Entry orders logged to: logs/paper/runs_{datetime.now().strftime('%Y_%m')}.json")
        print("=" * 80)


def main():
    """Run analysis and log results."""
    date_str = datetime.now().strftime("%m/%d/%Y")
    count = 3
    config_profile = "moderate"

    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--date" and i + 1 < len(args):
            date_str = args[i + 1]
        elif arg == "--count" and i + 1 < len(args):
            count = int(args[i + 1])
        elif arg == "--config" and i + 1 < len(args):
            config_profile = args[i + 1]

    config = {
        "min_conviction": config_profile if config_profile in TIER_FLOOR_BY_PROFILE else "moderate",
        "max_positions_per_day": count,
    }

    ranker = StrategyRanker(config)
    analysis = ranker.analyze(date_str)

    if analysis.get("ok"):
        ranker.log_analysis(analysis)
    ranker.print_report(analysis)


if __name__ == "__main__":
    main()
