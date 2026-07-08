#!/usr/bin/env python3
"""
Paper trading runner: Log entries and exits, track performance.

Usage:
    python paper_trading_runner.py --log-entry --symbol AAPL --price 5.10 --quantity 3
    python paper_trading_runner.py --log-exit --symbol AAPL --exit-price 2.50 --quantity 3
    python paper_trading_runner.py --summary
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional


class PaperTradingLogger:
    """Logs paper trading entries and exits."""

    def __init__(self, log_dir="paper_trading_logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        self.run_log = self.log_dir / f"runs_{datetime.now().strftime('%Y_%m')}.json"
        self.perf_log = self.log_dir / f"performance_{datetime.now().strftime('%Y_%m')}.json"

    def log_entry(
        self,
        symbol: str,
        strategy: str,
        entry_price: float,
        quantity: int,
        notes: str = "",
    ) -> Dict:
        """Log trade entry."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "date": datetime.now().strftime("%Y-%m-%d"),
            "symbol": symbol,
            "strategy": strategy,
            "entry_price": entry_price,
            "quantity": quantity,
            "entry_time": datetime.now().strftime("%H:%M:%S"),
            "notes": notes,
            "status": "ENTRY",
        }

        print(f"[ENTRY] {symbol} {strategy} @ ${entry_price:.2f} x {quantity}")
        print(f"  Time: {entry['entry_time']}")
        print(f"  Total credit: ${entry_price * 100 * quantity:.2f}")

        return entry

    def log_exit(
        self,
        symbol: str,
        entry_price: float,
        exit_price: float,
        quantity: int,
        reason: str = "manual",
        notes: str = "",
    ) -> Dict:
        """Log trade exit."""
        entry_credit = entry_price * 100 * quantity
        exit_cost = exit_price * 100 * quantity
        profit = entry_credit - exit_cost

        exit_record = {
            "timestamp": datetime.now().isoformat(),
            "date": datetime.now().strftime("%Y-%m-%d"),
            "symbol": symbol,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "quantity": quantity,
            "exit_time": datetime.now().strftime("%H:%M:%S"),
            "exit_reason": reason,
            "p_l": profit,
            "win": profit > 0,
            "notes": notes,
            "status": "EXIT",
        }

        return_pct = (profit / entry_credit * 100) if entry_credit > 0 else 0

        print(f"[EXIT] {symbol}")
        print(f"  Entry: ${entry_price:.2f}, Exit: ${exit_price:.2f}")
        print(f"  Profit/Loss: ${profit:.2f} ({return_pct:.1f}%)")
        print(f"  Reason: {reason}")

        return exit_record

    def append_performance(self, exit_record: Dict) -> None:
        """Append exit record to performance log."""
        records = []
        if self.perf_log.exists():
            with open(self.perf_log) as f:
                records = json.load(f)

        records.append(exit_record)

        with open(self.perf_log, "w") as f:
            json.dump(records, f, indent=2)

    def print_summary(self) -> None:
        """Print performance summary."""
        if not self.perf_log.exists():
            print("No performance data yet")
            return

        with open(self.perf_log) as f:
            records = json.load(f)

        if not records:
            print("No trades logged")
            return

        wins = len([r for r in records if r.get("win", False)])
        losses = len(records) - wins
        total_pl = sum(r.get("p_l", 0) for r in records)
        avg_pl = total_pl / len(records) if records else 0
        win_rate = (wins / len(records) * 100) if records else 0

        print("=" * 60)
        print("PAPER TRADING SUMMARY")
        print("=" * 60)
        print(f"Total trades: {len(records)}")
        print(f"Wins: {wins}")
        print(f"Losses: {losses}")
        print(f"Win rate: {win_rate:.1f}%")
        print(f"Total P&L: ${total_pl:.2f}")
        print(f"Avg P&L/trade: ${avg_pl:.2f}")
        print("=" * 60)


def main():
    """Parse arguments and execute logging."""
    logger = PaperTradingLogger()

    if "--log-entry" in sys.argv:
        # Parse entry arguments
        idx = sys.argv.index("--log-entry")
        symbol = "UNKNOWN"
        strategy = "UNKNOWN"
        entry_price = 0.0
        quantity = 1
        notes = ""

        for i, arg in enumerate(sys.argv[idx:]):
            if arg == "--symbol" and i + 1 < len(sys.argv[idx:]):
                symbol = sys.argv[idx + i + 1]
            elif arg == "--strategy" and i + 1 < len(sys.argv[idx:]):
                strategy = sys.argv[idx + i + 1]
            elif arg == "--price" and i + 1 < len(sys.argv[idx:]):
                entry_price = float(sys.argv[idx + i + 1])
            elif arg == "--quantity" and i + 1 < len(sys.argv[idx:]):
                quantity = int(sys.argv[idx + i + 1])
            elif arg == "--notes" and i + 1 < len(sys.argv[idx:]):
                notes = sys.argv[idx + i + 1]

        entry = logger.log_entry(symbol, strategy, entry_price, quantity, notes)
        print()

    elif "--log-exit" in sys.argv:
        # Parse exit arguments
        idx = sys.argv.index("--log-exit")
        symbol = "UNKNOWN"
        entry_price = 0.0
        exit_price = 0.0
        quantity = 1
        reason = "manual"
        notes = ""

        for i, arg in enumerate(sys.argv[idx:]):
            if arg == "--symbol" and i + 1 < len(sys.argv[idx:]):
                symbol = sys.argv[idx + i + 1]
            elif arg == "--price" and i + 1 < len(sys.argv[idx:]):
                entry_price = float(sys.argv[idx + i + 1])
            elif arg == "--exit-price" and i + 1 < len(sys.argv[idx:]):
                exit_price = float(sys.argv[idx + i + 1])
            elif arg == "--quantity" and i + 1 < len(sys.argv[idx:]):
                quantity = int(sys.argv[idx + i + 1])
            elif arg == "--reason" and i + 1 < len(sys.argv[idx:]):
                reason = sys.argv[idx + i + 1]
            elif arg == "--notes" and i + 1 < len(sys.argv[idx:]):
                notes = sys.argv[idx + i + 1]

        exit_record = logger.log_exit(symbol, entry_price, exit_price, quantity, reason, notes)
        logger.append_performance(exit_record)
        print()

    elif "--summary" in sys.argv:
        logger.print_summary()

    else:
        print("Usage:")
        print("  python paper_trading_runner.py --log-entry --symbol AAPL --price 5.10 --quantity 3")
        print("  python paper_trading_runner.py --log-exit --symbol AAPL --exit-price 2.50 --quantity 3")
        print("  python paper_trading_runner.py --summary")


if __name__ == "__main__":
    main()
