#!/usr/bin/env python3
"""
Generate end-of-day paper trading report.

Usage:
    python generate_eod_report.py
    python generate_eod_report.py --date 2026-07-15
    python generate_eod_report.py --week
    python generate_eod_report.py --detailed
    python generate_eod_report.py --format json
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional


class EODReportGenerator:
    """Generates end-of-day paper trading reports."""

    def __init__(self, log_dir="paper_trading_logs"):
        self.log_dir = Path(log_dir)
        self.run_log = self.log_dir / f"runs_{datetime.now().strftime('%Y_%m')}.json"
        self.perf_log = self.log_dir / f"performance_{datetime.now().strftime('%Y_%m')}.json"
        self.runs = []
        self.performance = []
        self.load_logs()

    def load_logs(self):
        """Load existing logs."""
        if self.run_log.exists():
            with open(self.run_log) as f:
                self.runs = json.load(f)

        if self.perf_log.exists():
            with open(self.perf_log) as f:
                self.performance = json.load(f)

    def generate_daily_report(self, date: str, detailed: bool = False) -> str:
        """Generate report for specific date."""

        # Find runs for this date
        day_runs = [r for r in self.runs if r.get("date") == date]

        if not day_runs:
            return f"No data for {date}"

        latest_run = day_runs[-1]
        trades = latest_run.get("trades", [])
        selected = [t for t in trades if t.get("status") != "REJECTED"]
        rejected = [t for t in trades if t.get("status") == "REJECTED"]

        report = []
        report.append("=" * 80)
        report.append(f"PAPER TRADING EOD REPORT: {date}")
        report.append("=" * 80)
        report.append("")

        # Analysis Summary
        report.append("ANALYSIS SUMMARY")
        report.append(f"  Total candidates: {latest_run.get('total_candidates', 0)}")
        report.append(f"  Selected for trading: {len(selected)}")
        report.append(f"  Rejected/waitlisted: {len(rejected)}")
        report.append("")

        # Selected Trades
        if selected:
            report.append("SELECTED TRADES (Ranked by Score)")
            for i, trade in enumerate(selected, 1):
                report.append(f"  {i}. {trade.get('symbol', 'N/A'):<8} - {trade.get('strategy', 'N/A')}")
                report.append(f"     Entry: ${trade.get('entry_price', 0):.2f}")
                report.append(f"     Quantity: {trade.get('entry_quantity', 0)}")
                report.append(f"     Target: 50% of entry credit")
                report.append(f"     Backstop: 4 hours post-announcement")
                report.append("")

        # Next-Day Monitoring
        report.append("NEXT-DAY EXIT MONITORING")
        report.append("  9:30 AM ET: Market opens, IV crush complete")
        report.append("  Check each position:")
        report.append("    1. Profit target hit? -> Close")
        report.append("    2. Delta stop triggered? -> Close leg")
        report.append("    3. Approaching backstop? -> Force close")
        report.append("")

        # Key Metrics
        if selected:
            avg_score = sum(t.get("score", 0) for t in selected) / len(selected)
            report.append("KEY METRICS")
            report.append(f"  Average score: {avg_score:.1f}/100")
            report.append(f"  Tier 1 count: {len([t for t in selected if 'TIER 1' in t.get('strategy', '')])}")
            report.append(f"  Tier 2 count: {len([t for t in selected if 'TIER 2' in t.get('strategy', '')])}")
            report.append("")

        # Observations
        report.append("OBSERVATIONS")
        report.append(f"  [OK] {len(selected)} high-quality candidates selected")
        if len(selected) > 0:
            report.append("  [OK] Ready for entry at 3:50 PM ET")
        if len(rejected) > 0:
            report.append(f"  [--] {len(rejected)} candidates rejected (capital/conviction limits)")
        report.append("")

        # Tomorrow Action Items
        report.append("TOMORROW ACTION ITEMS")
        report.append("  [ ] 9:30 AM: Market open, begin exit monitoring")
        report.append("  [ ] 9:35 AM: Check profit targets and stops")
        report.append("  [ ] 10:30 AM: Apply 4-hour backstop rule")
        report.append("  [ ] EOD: Log exits and P&L")
        report.append("")

        # Recommendations for config and risk adjustments
        report.append("RECOMMENDATIONS FOR NEXT WEEK")
        if len(selected) < 2:
            report.append("  CONFIG: Low candidate count today - relax conviction threshold?")
            report.append("          Try: --config moderate instead of conservative")
        if len(selected) > 5:
            report.append("  CONFIG: High candidate count - capital may be stretched")
            report.append("          Try: Increase max_positions_per_day or reduce available_capital")

        if len(selected) > 0:
            avg_score = sum(t.get("score", 0) for t in selected) / len(selected)
            if avg_score > 85:
                report.append("  CONFIG: Very high-quality candidates today (avg 85+)")
                report.append("          Opportunity: Consider increasing position size")
            elif avg_score < 75:
                report.append("  CONFIG: Medium-quality candidates today (avg < 75)")
                report.append("          Note: Be ready for wider exit ranges tomorrow")

        report.append("")
        report.append("RISK TOLERANCE ASSESSMENT")

        # Analyze overnight holds
        overnight_positions = len([t for t in selected if "pre-market" in t.get("strategy", "").lower()])
        if overnight_positions > 2:
            report.append("  RISK: Multiple overnight holds tonight")
            report.append("        Gap risk: Monitor pre-market quotes")
            report.append("        Tolerance: Consider --config conservative if gap risk concerns you")
        elif overnight_positions == 1:
            report.append("  RISK: One overnight hold - manageable gap exposure")
            report.append("        Set alerts for 6:30 AM announcement")
        else:
            report.append("  RISK: No overnight holds - lower overnight gap risk")

        # Capital utilization
        if len(selected) > 0:
            capital_per_trade = 5000
            total_capital_used = capital_per_trade * len(selected)
            capital_pct = (total_capital_used / 50000) * 100 if total_capital_used > 0 else 0

            if capital_pct > 80:
                report.append("  RISK: High capital utilization (>80% of available)")
                report.append("        Tight: Limited room for adverse moves")
                report.append("        Tolerance: Consider reducing position size or --max_positions")
            elif capital_pct > 50:
                report.append("  RISK: Medium capital utilization (50-80%)")
                report.append("        Balanced: Reasonable risk buffer available")
            else:
                report.append("  RISK: Low capital utilization (<50%)")
                report.append("        Conservative: Large buffer for adverse moves")

        # Strategy risk mix
        naked_count = len([t for t in selected if "STRADDLE" in t.get("strategy", "") or "STRANGLE" in t.get("strategy", "")])
        spread_count = len([t for t in selected if "CONDOR" in t.get("strategy", "") or "FLY" in t.get("strategy", "")])

        if naked_count > 0:
            report.append("  RISK: Naked short strategies in portfolio")
            report.append("        Unlimited: SHORT_STRADDLE/STRANGLE have undefined max loss")
            report.append("        Tolerance: If uncomfortable, use --allow_naked_strategies false")

        if spread_count > 0:
            report.append("  RISK: Defined-risk spreads in portfolio")
            report.append("        Managed: Max loss known and limited")
            report.append("        Tolerance: Good for risk-averse traders")

        report.append("")
        report.append("SUGGESTED RISK ADJUSTMENTS")
        report.append("  If uncomfortable with current risk:")
        report.append("    1. Reduce: max_positions_per_day (e.g., 3 -> 2)")
        report.append("    2. Lower: available_capital (triggers smaller position sizes)")
        report.append("    3. Tighten: min_conviction to 'high' (Tier 1 only, score 80+)")
        report.append("    4. Avoid: naked strategies (--allow_naked_strategies false)")
        report.append("")
        report.append("  If confident with current risk:")
        report.append("    1. Increase: max_positions_per_day to 4-5")
        report.append("    2. Raise: available_capital for larger positions")
        report.append("    3. Loosen: min_conviction to 'medium' (Tier 1-2 allowed)")
        report.append("    4. Embrace: naked strategies for max edge (SHORT_STRADDLE)")
        report.append("")

        report.append("=" * 80)

        return "\n".join(report)

    def generate_weekly_report(self, days: int = 7) -> str:
        """Generate weekly performance report."""

        cutoff = datetime.now() - timedelta(days=days)
        relevant_perf = [
            p for p in self.performance
            if datetime.fromisoformat(p.get("timestamp", "")) > cutoff
        ]

        if not relevant_perf:
            return f"No performance data for past {days} days"

        wins = len([p for p in relevant_perf if p.get("p_l", 0) > 0])
        losses = len(relevant_perf) - wins
        total_pl = sum(p.get("p_l", 0) for p in relevant_perf)
        avg_pl = total_pl / len(relevant_perf) if relevant_perf else 0
        win_rate = (wins / len(relevant_perf) * 100) if relevant_perf else 0

        report = []
        report.append("=" * 80)
        report.append(f"WEEKLY PERFORMANCE REPORT: Past {days} Days")
        report.append("=" * 80)
        report.append("")

        report.append("SUMMARY")
        report.append(f"  Trades executed: {len(relevant_perf)}")
        report.append(f"  Wins: {wins}")
        report.append(f"  Losses: {losses}")
        report.append(f"  Win rate: {win_rate:.1f}%")
        report.append("")

        report.append("P&L METRICS")
        report.append(f"  Total P&L: ${total_pl:.2f}")
        report.append(f"  Avg P&L per trade: ${avg_pl:.2f}")
        if total_pl > 0:
            report.append(f"  Status: POSITIVE (+{total_pl:.2f})")
        elif total_pl < 0:
            report.append(f"  Status: NEGATIVE ({total_pl:.2f})")
        else:
            report.append("  Status: BREAKEVEN")
        report.append("")

        report.append("INSIGHTS")
        if win_rate >= 60:
            report.append("  [OK] Strong performance (60%+ win rate)")
            report.append("  [OK] Ready to consider live trading")
        elif win_rate >= 50:
            report.append("  [~] Acceptable performance (50%+ win rate)")
            report.append("  [~] Continue paper trading, refine scoring")
        else:
            report.append("  [FAIL] Below target win rate (< 50%)")
            report.append("  [FAIL] Review and adjust scoring weights")
        report.append("")

        report.append("=" * 80)

        return "\n".join(report)

    def save_report(self, report: str, date: Optional[str] = None, report_type: str = "daily"):
        """Save report to file."""
        if date is None:
            date = datetime.now().strftime("%Y_%m_%d")

        filename = self.log_dir / f"{report_type}_report_{date}.txt"
        with open(filename, "w") as f:
            f.write(report)

        return filename


def main():
    """Generate EOD report."""

    # Parse arguments
    date = datetime.now().strftime("%Y-%m-%d")
    detailed = False
    weekly = False
    fmt = "text"

    for arg in sys.argv[1:]:
        if arg.startswith("--date"):
            date = arg.split("=")[1] if "=" in arg else sys.argv[sys.argv.index(arg) + 1]
        elif arg == "--detailed":
            detailed = True
        elif arg == "--week":
            weekly = True
        elif arg.startswith("--format"):
            fmt = arg.split("=")[1] if "=" in arg else "text"

    # Generate report
    generator = EODReportGenerator()

    # Create logs directory if needed
    generator.log_dir.mkdir(exist_ok=True)

    if weekly:
        report = generator.generate_weekly_report()
        report_type = "weekly"
    else:
        report = generator.generate_daily_report(date, detailed=detailed)
        report_type = "daily"

    # Display and save
    print(report)

    if fmt == "text":
        filename = generator.save_report(report, date=date.replace("-", "_"), report_type=report_type)
        print(f"\n[SAVED] {filename}")


if __name__ == "__main__":
    main()
