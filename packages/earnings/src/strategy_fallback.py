"""
Strategy Fallback System

When undefined-risk strategies (naked shorts) are disabled in config,
this system suggests defined-risk alternatives using Iron Fly with
wide wings to simulate unlimited-risk behavior.

The wider the wings, the closer Iron Fly behaves to a naked strategy:
- Normal wings (3.0x credit width): defined risk, tight profit zone
- Wide wings (5.0x credit width): defined risk, wider profit zone (closer to naked)
- Very wide wings (8.0x credit width): defined risk, very wide zone (approximates naked)
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Mapping of naked strategies to their fallback alternatives
NAKED_STRATEGY_FALLBACKS = {
    "SHORT_STRADDLE": {
        "fallback": "IRON_FLY_WIDE",
        "reason": "Naked ATM straddle → Iron Fly with very wide wings (8x credit multiple)",
        "wing_multiple": 8.0,
        "risk_level": "Simulated Unlimited",
        "description": "Captures similar edge to naked short, but with defined maximum loss",
    },
    "SHORT_STRANGLE": {
        "fallback": "IRON_CONDOR_WIDE",
        "reason": "Naked OTM strangle → Iron Condor with very wide wings (8x credit multiple)",
        "wing_multiple": 8.0,
        "risk_level": "Simulated Unlimited",
        "description": "Captures OTM strangle edge with defined risk via wings",
    },
    "JADE_LIZARD": {
        "fallback": "BROKEN_WING_BUTTERFLY_WIDE",
        "reason": "Asymmetric naked → Asymmetric with wide wings on naked side",
        "wing_multiple": 6.0,  # Slightly tighter since one side was already hedged
        "risk_level": "Simulated Partial Unlimited",
        "description": "Maintains directional hedge but defines maximum loss",
    },
}


def is_naked_strategy(strategy: str) -> bool:
    """Check if a strategy has unlimited risk potential."""
    return strategy in NAKED_STRATEGY_FALLBACKS


def get_fallback_strategy(
    original_strategy: str,
    allow_naked: bool,
    config: dict,
) -> dict:
    """
    Get fallback strategy if naked strategies are disabled.

    Args:
        original_strategy: The originally recommended strategy
        allow_naked: Whether naked strategies are allowed
        config: Full config dict for wing width calculations

    Returns:
        Dict with:
        - strategy: Final strategy to use
        - is_fallback: Whether this is a fallback
        - reason: Explanation of choice
        - wing_multiple: Recommended wing width (if fallback)
        - risk_profile: Risk characterization
    """

    if allow_naked or not is_naked_strategy(original_strategy):
        # No fallback needed; use original strategy
        return {
            "strategy": original_strategy,
            "is_fallback": False,
            "reason": "Strategy allowed or not naked",
            "wing_multiple": None,
            "risk_profile": "As configured",
        }

    # Fallback to defined-risk alternative
    fallback_info = NAKED_STRATEGY_FALLBACKS[original_strategy]

    return {
        "strategy": fallback_info["fallback"],
        "is_fallback": True,
        "original_strategy": original_strategy,
        "reason": fallback_info["reason"],
        "wing_multiple": fallback_info["wing_multiple"],
        "risk_profile": fallback_info["risk_level"],
        "description": fallback_info["description"],
    }


def log_fallback_warning(fallback_result: dict, candidate_symbol: str) -> None:
    """Log a warning when fallback strategy is used."""

    if not fallback_result["is_fallback"]:
        return

    warning_msg = (
        f"[{candidate_symbol}] Strategy fallback applied:\n"
        f"  Original: {fallback_result.get('original_strategy')}\n"
        f"  Fallback: {fallback_result['strategy']}\n"
        f"  Reason: {fallback_result['reason']}\n"
        f"  Wing Multiple: {fallback_result['wing_multiple']:.1f}x credit width\n"
        f"  Risk Profile: {fallback_result['risk_profile']}\n"
        f"  Note: {fallback_result['description']}"
    )

    logger.warning(warning_msg)
    print(f"\n⚠️  WARNING: {warning_msg}\n")


def apply_wide_wings_config(strategy_config: dict, wing_multiple: float) -> dict:
    """
    Modify strategy config to use wide wings.

    For wide-wing fallback, we increase wing width and adjust profit target.
    """

    modified_config = strategy_config.copy()

    # Increase wing width multiple (makes wider wings, more like naked)
    modified_config["wing_width_credit_multiple"] = wing_multiple

    # Adjust profit target percentage for wider wings
    # Wider wings = more credit collected, but also larger max risk
    # Keep 50% profit target but it represents a larger $ amount with wider wings
    # (already in config as "profit_target_pct": 0.50)

    # Add flag to track that this is a wide-wing fallback
    modified_config["_fallback_wide_wings"] = True
    modified_config["_original_wing_multiple"] = strategy_config.get(
        "wing_width_credit_multiple", 3.0
    )
    modified_config["_fallback_wing_multiple"] = wing_multiple

    return modified_config


def suggest_alternative_strategy_to_user(
    candidate_symbol: str,
    original_strategy: str,
    fallback_info: dict,
) -> str:
    """Generate human-readable suggestion for alternative strategy."""

    original = original_strategy
    fallback = fallback_info["fallback"]
    wing_multiple = fallback_info["wing_multiple"]

    suggestion = f"""
╔═══════════════════════════════════════════════════════════════════════╗
║ STRATEGY SUBSTITUTION: Undefined Risk Not Allowed                     ║
╠═══════════════════════════════════════════════════════════════════════╣
║                                                                       ║
║  Symbol: {candidate_symbol}
║  Original Strategy: {original}
║  Risk Profile: Unlimited (DISABLED)                                   ║
║                                                                       ║
║  SUGGESTED ALTERNATIVE: {fallback}
║  Wing Multiple: {wing_multiple:.1f}x credit width
║  Risk Profile: {fallback_info['risk_level']}
║                                                                       ║
║  Why: {fallback_info['description']}
║                                                                       ║
║  How to Enable Original: Set "allow_naked_strategies": true in config ║
║                                                                       ║
╚═══════════════════════════════════════════════════════════════════════╝
"""

    return suggestion


# Configuration for wide-wing behavior mapping
WIDE_WING_PROFILES = {
    "CONSERVATIVE": {
        "wing_multiple": 4.0,
        "profit_target_pct": 0.50,
        "description": "Moderate width - balances risk/reward",
    },
    "MODERATE": {
        "wing_multiple": 6.0,
        "profit_target_pct": 0.50,
        "description": "Wide wings - closer to naked behavior",
    },
    "AGGRESSIVE": {
        "wing_multiple": 8.0,
        "profit_target_pct": 0.50,
        "description": "Very wide wings - closely simulates unlimited risk",
    },
}


def get_wide_wing_profile(risk_level: str = "MODERATE") -> dict:
    """Get recommended wing configuration for risk level."""
    return WIDE_WING_PROFILES.get(
        risk_level.upper(),
        WIDE_WING_PROFILES["MODERATE"],
    )


# Example usage documentation
USAGE_EXAMPLE = """
# Example 1: Short Straddle → Iron Fly (Wide Wings)

Candidate: AAPL
Original Strategy: SHORT_STRADDLE
allow_naked_strategies: false

Action:
1. Detect naked strategy is disabled
2. Look up fallback: IRON_FLY_WIDE (8.0x wing multiple)
3. Use Iron Fly config with wing_width_credit_multiple = 8.0
4. Log warning with substitution details
5. Proceed with wide-wing Iron Fly instead

Result:
- Risk is now DEFINED (no unlimited loss)
- Wings are very wide (8x credit width ≈ simulates naked)
- Profit target still 50% of credit collected
- Max loss = wing width - credit (predictable, manageable)


# Example 2: How Wide Wings Approximate Naked Shorts

Normal Iron Fly (3.0x wings):
- ATM short strike: 100
- Wing strike: 103 (3% OTM)
- Max loss: $3 per contract - credit collected
- Effective only for small moves

Wide-Wing Iron Fly (8.0x wings):
- ATM short strike: 100
- Wing strike: 108 (8% OTM)
- Max loss: $8 per contract - credit collected
- Captures move out to 8%, similar to naked short
- But max loss is DEFINED (unlike naked)


# Example 3: Risk Comparison

Short Straddle (Undefined Risk):
- Entry: Sell 100 call + put
- Max Loss: UNLIMITED (stock can gap 20%+)
- Max Profit: $0.50 (half of credit collected)
- Return: Can be 100%+ if stock holds ATM

Iron Fly (Wide Wings, Defined Risk):
- Entry: Sell 100 call + put, buy 108 call + put
- Max Loss: $7.50 per contract (DEFINED)
- Max Profit: $0.50 (same as naked, half of credit)
- Return: 6.7% on $7.50 max risk = similar return %

→ Both capture similar edge, but Iron Fly has manageable risk
"""
