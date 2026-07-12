#!/usr/bin/env python3
"""Per-strategy text report for the strategy-testing program (see
docs/strategy-testing-plan.md) -- thin CLI over strategy_metrics.py for
quick weekly review. All numbers come from strategy_metrics.py, the same
module strategy_dashboard.py reads, so the two can never disagree.

Usage:
    python strategy_report.py
    python strategy_report.py --since 2026-07-01
    python strategy_report.py --profile strat_test --strategy iron_fly
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import scanner
import strategy_metrics as sm

STRATEGY_NAMES = [
    "iron_fly", "double_calendar", "iron_condor", "atm_calendar",
    "directional_credit_spread", "broken_wing_butterfly", "reverse_fly",
]


def _fmt(value, digits=2, pct=False, dollar=False):
    if value is None:
        return "n/a"
    if pct:
        return f"{value * 100:.{digits}f}%"
    if dollar:
        return f"${value:,.{digits}f}"
    return f"{value:.{digits}f}"


def _pass_glyph(passed):
    if passed is None:
        return "- n/a"
    return "PASS" if passed else "FAIL"


def print_strategy_block(name: str, trades: list[dict], capital_basis: float | None) -> None:
    summary = sm.strategy_summary(trades, capital_basis)
    sample = summary["sample"]
    cf = summary["core_five"]

    print(f"--- {name} " + "-" * max(1, 60 - len(name)))
    print(f"  Trades: {sample['count']:>4}  "
          f"(directional target 30: {'met' if sample['directional_met'] else 'not yet'}, "
          f"significant target 100: {'met' if sample['significant_met'] else 'not yet'})")
    if sample["count"] == 0:
        print("  No closed trades yet.")
        print()
        return

    print(f"  Win rate:       {_fmt(cf['win_rate']['value'], pct=True)}")
    print(f"  Profit factor:  {_fmt(cf['profit_factor']['value'])}"
          f"   [{_pass_glyph(cf['profit_factor']['pass'])}, need > {sm.BENCHMARKS['profit_factor_min']}]")
    print(f"  Expectancy:     {_fmt(cf['expectancy']['value'], dollar=True)}"
          f"   [{_pass_glyph(cf['expectancy']['pass'])}, need > {sm.BENCHMARKS['expectancy_cost_multiple_min']}x avg cost "
          f"({_fmt(cf['expectancy']['avg_cost'], dollar=True)})]")
    print(f"  Sharpe (trade): {_fmt(cf['sharpe']['value'])}"
          f"   [{_pass_glyph(cf['sharpe']['pass'])}, need > {sm.BENCHMARKS['sharpe_min']}]")
    mdd = cf["max_drawdown"]["value"]
    print(f"  Max drawdown:   {_fmt(mdd['absolute'], dollar=True)} ({_fmt(mdd['pct'], pct=True)})"
          f"   [{_pass_glyph(cf['max_drawdown']['pass'])}, need < {sm.BENCHMARKS['max_drawdown_max_pct']*100:.0f}%]")
    if summary["avg_hold_seconds"]:
        hours = summary["avg_hold_seconds"] / 3600
        print(f"  Avg hold:       {hours:.1f}h")

    iv = summary["iv_crush"]
    if iv["avg_crush"] is not None:
        direction = "crush" if iv["avg_crush"] >= 0 else "expansion"
        print(f"  Avg IV {direction}:  {abs(iv['avg_crush'])*100:.1f} vol pts  (n={iv['sample_count']}/{sample['count']})")
    else:
        print("  Avg IV crush:   n/a (no trades with both entry/exit IV captured)")

    if summary["regime_buckets"]:
        print("  Regime coverage:")
        for bucket, count in sorted(summary["regime_buckets"].items(), key=lambda x: -x[1]):
            print(f"    {bucket:<35} {count}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["live", "paper"], default="paper",
                        help="'live' reads data/earnings_trades.db; 'paper' (default) reads "
                             "data/paper_trades.db.")
    parser.add_argument("--db", default=None, help="Overrides the mode-based default DB path.")
    parser.add_argument("--profile", default=None,
                        help="Book to report on. Defaults to 'strat_test' in paper mode, "
                             "'default' in live mode.")
    parser.add_argument("--strategy", default=None, help="limit to one strategy")
    parser.add_argument("--since", default=None, help="YYYY-MM-DD, only trades opened on/after this date")
    args = parser.parse_args()

    sm.DB_PATH = sm.db_path_for_mode(args.mode, args.db)
    profile = args.profile or ("strat_test" if args.mode == "paper" else "default")

    config = scanner._load_config()
    capital_basis = config.get("available_capital_paper_mode")

    names = [args.strategy] if args.strategy else STRATEGY_NAMES

    print("=" * 80)
    print(f"STRATEGY TEST REPORT -- profile={profile}" + (f" since={args.since}" if args.since else ""))
    print(f"MODE: {args.mode.upper()} ({sm.DB_PATH})")
    print("=" * 80)
    print()
    print("Caveats: forward-only sample (no historical backfill); trades sharing a")
    print("symbol/night are correlated (one earnings event), not fully independent;")
    print("paper fills are cost-adjusted but still lack real queue position/slippage")
    print("depth -- expect live drawdown 1.5-2x paper. <100 trades isn't statistically")
    print("significant; <30 isn't even directional. (Drawdown % uses")
    print("available_capital_paper_mode as its reference basis in both modes.)")
    print()

    for name in names:
        trades = sm.load_closed_trades(profile=profile, strategy=name, since=args.since)
        print_strategy_block(name, trades, capital_basis)


if __name__ == "__main__":
    main()
