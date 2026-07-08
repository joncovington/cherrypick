#!/usr/bin/env python3
"""
Test: Strategy Fallback System

Demonstrates how undefined-risk strategies fall back to wide-wing
defined-risk alternatives when allow_naked_strategies is disabled.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from strategy_fallback import (
    get_fallback_strategy,
    suggest_alternative_strategy_to_user,
    is_naked_strategy,
    WIDE_WING_PROFILES,
)


def test_fallback_system():
    """Test fallback strategy selection."""

    print("\n" + "="*80)
    print("STRATEGY FALLBACK SYSTEM TEST")
    print("Testing: Naked -> Wide-Wing Defined-Risk Fallbacks")
    print("="*80)

    # Test cases: (symbol, strategy, allow_naked)
    test_cases = [
        ("AAPL", "SHORT_STRADDLE", True),   # Naked allowed → use as-is
        ("AAPL", "SHORT_STRADDLE", False),  # Naked not allowed → fallback
        ("JPM", "IRON_FLY", False),         # Already defined → no fallback
        ("MSFT", "SHORT_STRANGLE", False),  # Naked not allowed → fallback
        ("UNH", "JADE_LIZARD", False),      # Naked not allowed → fallback
    ]

    for symbol, original_strategy, allow_naked in test_cases:
        print(f"\n{'-'*80}")
        print(f"Test: {symbol} | {original_strategy} | allow_naked={allow_naked}")
        print(f"{'-'*80}")

        # Get fallback decision
        result = get_fallback_strategy(original_strategy, allow_naked, {})

        print(f"Is naked strategy: {is_naked_strategy(original_strategy)}")
        print(f"Final strategy: {result['strategy']}")
        print(f"Is fallback: {result['is_fallback']}")
        print(f"Risk profile: {result['risk_profile']}")

        if result["is_fallback"]:
            print(f"Wing multiple: {result['wing_multiple']:.1f}x")
            print(f"Reason: {result['reason']}")
            print(f"Description: {result['description']}")

            # Show suggestion to user
            suggestion = suggest_alternative_strategy_to_user(
                symbol, original_strategy, result
            )
            print(suggestion)

    # Show wide-wing profiles
    print("\n" + "="*80)
    print("WIDE-WING PROFILES FOR FALLBACK STRATEGIES")
    print("="*80)

    for profile_name, config in WIDE_WING_PROFILES.items():
        print(f"\n{profile_name}:")
        print(f"  Wing Multiple: {config['wing_multiple']:.1f}x credit width")
        print(f"  Profit Target: {config['profit_target_pct']*100:.0f}% of credit")
        print(f"  Use Case: {config['description']}")

    # Example comparison
    print("\n" + "="*80)
    print("COMPARISON: Naked vs Wide-Wing Fallback")
    print("="*80)

    print("\nScenario: AAPL earnings, entry credit = $3.50")
    print("\nOPTION 1: Short Straddle (if allow_naked_strategies=true)")
    print("  Entry: Sell ATM 150 call + put for $3.50 credit")
    print("  Max Loss: UNLIMITED (stock could gap 20%+)")
    print("  Max Profit: $1.75 (50% of $3.50)")
    print("  Risk: Unmanageable tail risk")

    print("\nOPTION 2: Iron Fly with MODERATE wide wings (allow_naked_strategies=false)")
    print("  Entry: Sell 150 call/put, buy 152.50 wings for $3.50 credit")
    print("  Wing Multiple: 6.0x credit width = $21 wide")
    print("  Max Loss: $21.00 - $3.50 = $17.50 per contract")
    print("  Max Profit: $1.75 (50% of $3.50)")
    print("  Risk: DEFINED at $17.50 (manageable)")
    print("  Edge: Captures same 50% profit on credit but with risk constraint")

    print("\nOPTION 3: Iron Fly with AGGRESSIVE wide wings")
    print("  Entry: Same structure, but wings at 8.0x = $28 wide")
    print("  Max Loss: $28.00 - $3.50 = $24.50")
    print("  Profit Zone: Very wide (stock must stay within 3% of ATM)")
    print("  Risk: Still DEFINED but higher (trades safety for easier wins)")

    print("\n" + "="*80)
    print("KEY INSIGHT: Wide Wings Make Iron Fly Act Like Naked Shorts")
    print("="*80)

    print("""
Naked Short Straddle:
  - Sell ATM, unlimited profit zone, unlimited loss
  - Returns depend on how far stock drifts ITM

Wide-Wing Iron Fly:
  - Sell ATM, very wide wings, defined loss
  - Similar profit zone as narrow-wing Iron Fly
  - But max loss is now PREDICTABLE

Benefits of Fallback:
  ✓ Risk is DEFINED (portfolio constraint satisfied)
  ✓ Still captures the edge (wide profit zone)
  ✓ Easier to size and manage positions
  ✓ Better for portfolios with risk limits
  ✓ Can scale up position size since risk is known

Trade-off:
  • Wider wings = larger max loss
  • But loss is KNOWN upfront
  • Can size accordingly
""")


if __name__ == "__main__":
    test_fallback_system()
