# Configuration Guide: config.json Parameters

Complete reference for all configurable parameters.

---

## Quick Start

Copy `config.example.json` to `config.json` and customize. All 10 strategies have configurable entry conditions.

---

## Global Settings

### Risk Configuration

```json
{
  "allow_naked_strategies": false,
  "win_rate_target": 0.65,
  "max_concurrent_earnings_positions": 3,
  "max_daily_earnings_trades": 5
}
```

**allow_naked_strategies**  
Enable/disable undefined-risk strategies (SHORT_STRADDLE, SHORT_STRANGLE, JADE_LIZARD).  
- `false` (default): Fallback to wide-wing alternatives  
- `true`: Use naked strategies directly  

**win_rate_target**  
Target win rate (0.0-1.0). Used to adjust entry thresholds. Lower target = relaxed gates.

**max_concurrent_earnings_positions**  
Maximum simultaneous overnight earnings plays. Default 3.

**max_daily_earnings_trades**  
Maximum new entries per day. Default 5.

---

### Exit Defaults

```json
{
  "profit_target_pct": 0.50,
  "profit_target_pct_calendar": 0.25,
  "stop_loss_credit_multiple": 2.0,
  "exit_after_announcement_minutes": 240,
  "per_leg_delta_stop": 0.60
}
```

**profit_target_pct**  
Profit target for credit spreads. Default 50%.

**profit_target_pct_calendar**  
Profit target for calendar spreads. Default 25%.

**stop_loss_credit_multiple**  
Maximum loss as multiple of entry credit. 2.0 = lose 2x entry credit.

**exit_after_announcement_minutes**  
Backstop exit time (4 hours = 240 min).

**per_leg_delta_stop**  
Delta threshold for protective stops. Default 0.60 for naked, 0.45 for spreads.

---

## Entry Condition Gates

```json
{
  "entry_condition_gates": {
    "max_realized_move_dispersion_pct": 0.15,
    "min_iv_rank": 0.15,
    "min_credit_dollars": 0.10,
    "min_dte": 30,
    "max_dte": 60
  }
}
```

**max_realized_move_dispersion_pct**  
Reject if σ > 15%. Stocks too unpredictable.

**min_iv_rank**  
Reject if IV Rank < 0.15. Insufficient premium.

**min_credit_dollars**  
Reject if entry credit < $0.10. Uneconomical.

**min_dte / max_dte**  
Entry window: 30-60 days to expiration.

---

## Strategy-Specific Configurations

### 1. Short Straddle

```json
{
  "strategies": {
    "short_straddle": {
      "max_realized_move_dispersion_pct": 0.08,
      "min_iv_rank": 1.20,
      "min_entry_credit_dollars": 3.00,
      "profit_target_pct": 0.50,
      "leg_stop_delta_abs": 0.60,
      "stop_loss_credit_multiple": 2.0,
      "exit_after_announcement_minutes": 240
    }
  }
}
```

**When:** High IV (>1.20) + Ultra-predictable (σ<0.08)  
**Dispersion gate:** 0.08 (tighter than global 0.15)  
**Entry credit:** $3-5 typical  
**Exit:** 50% target, delta stop 0.60, 4-hour backstop  

---

### 2. Iron Fly

```json
{
  "strategies": {
    "iron_fly": {
      "max_realized_move_dispersion_pct": 0.20,
      "min_iv_rank": 0.75,
      "min_entry_credit_dollars": 0.80,
      "profit_target_pct": 0.50,
      "wing_width_credit_multiple": 3.0,
      "leg_stop_delta_abs": 0.45,
      "exit_after_announcement_minutes": 240
    }
  }
}
```

**When:** Medium IV (0.75-1.00) + Normal dispersion (0.08-0.20)  
**Entry credit:** $0.80-1.50  
**Wing width:** 3.0x default (8.0x for fallback)  
**Exit:** 50% target, delta stop 0.45, 4-hour backstop  

---

### 3. Iron Condor

```json
{
  "strategies": {
    "iron_condor": {
      "max_realized_move_dispersion_pct": 0.25,
      "min_iv_rank": 0.60,
      "min_entry_credit_dollars": 0.50,
      "profit_target_pct": 0.50,
      "wing_width_credit_multiple": 3.0,
      "leg_stop_delta_abs": 0.45,
      "exit_after_announcement_minutes": 240
    }
  }
}
```

**When:** Wide range expected + No directional bias  
**Entry credit:** $0.50-1.00  
**Exit:** 50% target, delta stop 0.45, 4-hour backstop  

---

### 4. Short Strangle

```json
{
  "strategies": {
    "short_strangle": {
      "max_realized_move_dispersion_pct": 0.15,
      "min_iv_rank": 1.00,
      "min_entry_credit_dollars": 0.50,
      "profit_target_pct": 0.50,
      "leg_stop_delta_abs": 0.60,
      "stop_loss_credit_multiple": 2.0,
      "exit_after_announcement_minutes": 240
    }
  }
}
```

**When:** OTM strikes preferred, lower capital  
**Entry credit:** $0.50-1.20  
**Exit:** 50% target, delta stop 0.60, 4-hour backstop  

---

### 5. Reverse Fly

```json
{
  "strategies": {
    "reverse_fly": {
      "min_realized_move_ratio": 1.10,
      "max_realized_move_dispersion_pct": 0.30,
      "min_entry_credit_dollars": 1.50,
      "profit_target_pct": 0.50,
      "wing_width_pct": 0.10,
      "exit_after_announcement_minutes": 240
    }
  }
}
```

**When:** Gap premium detected (ratio > 1.10)  
**Entry:** Long ATM + short wings  
**Entry credit:** $1.50-3.00  
**Exit:** 50% target or max defined loss  

---

### 6. Jade Lizard

```json
{
  "strategies": {
    "jade_lizard": {
      "max_realized_move_dispersion_pct": 0.20,
      "min_iv_rank": 0.80,
      "min_entry_credit_dollars": 1.00,
      "profit_target_pct": 0.50,
      "directional_bias_required": true,
      "leg_stop_delta_abs": 0.60,
      "exit_after_announcement_minutes": 240
    }
  }
}
```

**When:** Clear directional bias + IV skew  
**Entry credit:** $1.00-2.00  
**Exit:** 50% target, delta stop 0.60, 4-hour backstop  

---

### 7. Directional Spread

```json
{
  "strategies": {
    "directional_credit_spread": {
      "max_realized_move_dispersion_pct": 0.25,
      "min_iv_rank": 0.70,
      "min_entry_credit_dollars": 0.40,
      "profit_target_pct": 0.50,
      "directional_bias_required": true,
      "leg_stop_delta_abs": 0.45,
      "exit_after_announcement_minutes": 240
    }
  }
}
```

**When:** One-sided move expected + IV skew  
**Entry credit:** $0.40-1.50  
**Exit:** 50% target, delta stop 0.45, 4-hour backstop  

---

### 8. Broken Wing Butterfly

```json
{
  "strategies": {
    "broken_wing_butterfly": {
      "max_realized_move_dispersion_pct": 0.20,
      "min_iv_rank": 0.75,
      "min_entry_credit_dollars": 0.30,
      "profit_target_pct": 0.50,
      "iv_skew_required": true,
      "leg_stop_delta_abs": 0.45,
      "exit_after_announcement_minutes": 240
    }
  }
}
```

**When:** IV skew favors asymmetric structure  
**Entry credit:** $0.30-0.80  
**Exit:** 50% target, delta stop 0.45, 4-hour backstop  

---

### 9. ATM Calendar

```json
{
  "strategies": {
    "atm_calendar": {
      "max_realized_move_dispersion_pct": 0.10,
      "max_iv_rank": 0.60,
      "max_entry_debit_dollars": 0.30,
      "profit_target_pct": 0.25,
      "exit_before_expiration_days": 5,
      "directional_bias_required": false
    }
  }
}
```

**When:** Low IV + Ultra-predictable  
**Entry debit:** $0.20-0.30 (pay, not collect)  
**Exit:** 25% profit target or 5 days before exp  

---

### 10. Double Calendar

```json
{
  "strategies": {
    "double_calendar": {
      "max_realized_move_dispersion_pct": 0.10,
      "max_iv_rank": 0.60,
      "max_entry_debit_dollars": 0.50,
      "profit_target_pct": 0.25,
      "exit_before_expiration_days": 5,
      "directional_bias_required": false
    }
  }
}
```

**When:** Low IV + Symmetric, boring stock  
**Entry debit:** $0.40-0.60 (both calendars)  
**Exit:** 25% profit target or 5 days before exp  

---

## Decision Matrix Thresholds

```json
{
  "decision_matrix": {
    "primary": {
      "realized_move_ratio_threshold": 1.10,
      "gap_premium_min_ratio": 1.10,
      "normal_iv_crush_max_ratio": 1.10
    },
    "secondary": {
      "ultra_predictable_dispersion": 0.08,
      "normal_dispersion_floor": 0.08,
      "normal_dispersion_ceiling": 0.20
    },
    "tertiary": {
      "iv_rank_thin": 0.50,
      "iv_rank_light": 0.75,
      "iv_rank_medium": 1.00
    }
  }
}
```

**primary.realized_move_ratio_threshold**  
Above 1.10 = gap premium route.

**secondary thresholds**  
σ < 0.08 = ultra-predictable (naked OK).  
0.08-0.20 = normal (spreads).  
> 0.20 = wide wings or calendar only.

**tertiary IV thresholds**  
IV < 0.75 = calendar spreads.  
IV 0.75-1.00 = Iron Fly/Condor.  
IV > 1.00 = SHORT_STRADDLE/STRANGLE.

---

## Fallback Profiles

```json
{
  "fallback_profiles": {
    "conservative": {
      "wing_multiple": 4.0,
      "profit_target_pct": 0.50,
      "use_case": "Tight risk, capital limited"
    },
    "moderate": {
      "wing_multiple": 6.0,
      "profit_target_pct": 0.50,
      "use_case": "Balanced (DEFAULT)"
    },
    "aggressive": {
      "wing_multiple": 8.0,
      "profit_target_pct": 0.50,
      "use_case": "Wide zone, capital available"
    }
  }
}
```

Used when `allow_naked_strategies: false`. Selects fallback wing width.

---

## Example: Custom Conservative Config

For risk-averse portfolio:

```json
{
  "allow_naked_strategies": false,
  "win_rate_target": 0.65,
  "max_concurrent_earnings_positions": 2,
  "max_daily_earnings_trades": 2,
  
  "entry_condition_gates": {
    "max_realized_move_dispersion_pct": 0.12,
    "min_iv_rank": 0.20,
    "min_credit_dollars": 0.15
  },
  
  "strategies": {
    "short_straddle": {
      "max_realized_move_dispersion_pct": 0.06,
      "min_iv_rank": 1.30
    },
    "iron_fly": {
      "wing_width_credit_multiple": 4.0
    }
  },
  
  "fallback_profiles": {
    "active": "conservative"
  }
}
```

Changes:
- No naked strategies allowed
- Stricter dispersion gates (0.12 vs 0.15)
- Conservative wing profile (4.0x)
- Max 2 positions, 2 trades/day
- Only ultra-tight SHORT_STRADDLE (σ < 0.06)

---

## Example: Aggressive Config

For experienced trader with capital:

```json
{
  "allow_naked_strategies": true,
  "win_rate_target": 0.60,
  "max_concurrent_earnings_positions": 5,
  "max_daily_earnings_trades": 8,
  
  "entry_condition_gates": {
    "max_realized_move_dispersion_pct": 0.18,
    "min_iv_rank": 0.10,
    "min_credit_dollars": 0.05
  },
  
  "strategies": {
    "short_straddle": {
      "max_realized_move_dispersion_pct": 0.12,
      "min_iv_rank": 1.00
    },
    "short_strangle": {
      "min_iv_rank": 0.80
    }
  }
}
```

Changes:
- Naked strategies allowed (SHORT_STRADDLE, SHORT_STRANGLE)
- Looser dispersion gates (0.18 vs 0.15)
- Lower IV requirements
- Max 5 positions, 8 trades/day
- Wider acceptable dispersion ranges

---

## Validation Checklist

After editing config.json:

```bash
# Check JSON syntax
python -c "import json; json.load(open('config.json')); print('Valid')"

# Verify all strategies defined
grep -c '"iron_fly"' config.json  # Should find entry

# Check critical thresholds
# - max_realized_move_dispersion_pct < 0.30?
# - min_iv_rank > 0.05?
# - profit_target_pct < 0.75?

# Test with dry run
python get_candidates.py --date 2026-07-15 --dry_run
```

---

## Reset to Defaults

```bash
cp config.example.json config.json
```

---

## Navigation

**← Previous:** [Quick Reference](./02-quick-reference.md)  
**Next →** [Entry Conditions Framework](./04-entry-conditions.md)
