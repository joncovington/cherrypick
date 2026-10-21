"""The management ruleset: the shipped default and the operator's overrides.

The default is the rule set digested from the vendor's education library on 2026-10-13 (credit
trades take profit at 50% of the credit, stop when the loss equals the credit, close at 21 DTE; debit
trades stop at -50% and take profit at +75%; never roll a loser; never hold a physically-settled
option into expiry). The evidence behind each number, and where outside research disagrees, is in
this package's README; each rule's `confidence` is how much that evidence supports it, and is what an
evaluation reports beside the rule's verdict.

Overrides live in ``~/.cherrypick/config/manage.json`` and are merged key by key over the default, so
a file holding one changed threshold keeps every other rule. A key the default does not have is
reported, never silently accepted: a misspelt threshold would otherwise leave the default in force
while the operator believes it changed.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from cherrypick.core import home as _home
from cherrypick.core import incomerules as _income

from cherrypick.manage import shapes as _shapes

RULESET_NAME = "manage-2026-10"

DEFAULT_RULES: dict[str, Any] = {
    "ruleset": RULESET_NAME,
    # How an underlying's legs become trades (each trade is sized, stopped and closed as one):
    # "order_chain" follows the broker's order chains (the opening order and every roll), and takes the
    # chain's net cash as the basis -- falling back to "expiry_chain" for any leg the history cannot
    # explain; "expiry_chain" splits by expiry, keeping a diagonal's short with the long it is sold
    # against; "underlying" makes every underlying one trade. Both fallbacks price the open legs only.
    "trade_grouping": "order_chain",
    # A trade where no rule fired: how far the ruleset supports holding it, less a penalty when an
    # earnings report falls inside its short legs' life.
    "hold": {"confidence": 70, "earnings_penalty": 10},
    "risk": {
        "enabled": True,
        # Max loss budget per trade as a share of net liquidating value (the vendor's 2% rule).
        "max_risk_pct": 0.02,
        # A trade whose modelled remaining downside exceeds budget x this multiple is flagged.
        "oversize_multiple": 3.0,
        "oversize_action": "REVIEW",
        "confidence": 60,
    },
    "credit": {
        "take_profit": {
            "enabled": True,
            "capture": 0.50,
            "confidence": 80,
            # A shape's own target, in place of `capture` and `confidence` (shapes.py names the shapes).
            # One short strike of these is nearly always in the money, so they rarely keep half: the
            # tastylive strategy guide (2023) takes them off at 25%. Outside tests found 25% loses after
            # commissions on OTM credit trades; none tested these shapes, hence the lower confidence.
            "by_shape": {
                "short straddle": {"capture": 0.25, "confidence": 55},
                "iron butterfly": {"capture": 0.25, "confidence": 55},
                "big lizard": {"capture": 0.25, "confidence": 55},
                "reverse big lizard": {"capture": 0.25, "confidence": 55},
            },
        },
        "stop": {
            "enabled": True,
            # Stop when the loss reaches this multiple of the credit (1.0 = the spread costs 2x the credit).
            "loss_multiple_of_credit": 1.0,
            # Where that stop cannot fire (the credit exceeds what is left to lose), stop at this share of
            # the modelled max loss instead.
            "fallback_max_loss_fraction": 0.50,
            # 70 until 2026-10-20; lowered after a declared local study of strangle management on
            # licensed history (its plan and results stay under ~/.cherrypick, by the data licence).
            "confidence": 50,
        },
        "time_exit": {"enabled": True, "dte": 21, "roll_if_profitable": True, "confidence": 75},
        # Thesis rule: a short strike is in the money and what is left to win, against what is left to
        # lose, is below the entry standard (1/3 of width = 0.5 reward per unit of risk).
        "breached": {"enabled": True, "min_reward_to_risk": 0.50, "confidence": 70},
        # Off by default (the rule set holds credit trades through earnings); on = close before it.
        "close_before_earnings": {"enabled": False, "confidence": 55},
    },
    "debit": {
        "stop": {"enabled": True, "loss_fraction": 0.50, "confidence": 65},
        "take_profit": {
            "enabled": True,
            "gain_fraction": 0.75,
            "confidence": 65,
            # A shape's own target, in place of `gain_fraction` and `confidence`. A calendar trades only
            # the time value between its expiries and cannot run far either way, so the strategy guide
            # takes it off at 10-25% of the debit; a zebra starts with no time value and takes 25%.
            "by_shape": {
                "calendar": {"gain_fraction": 0.25, "confidence": 55},
                "call zebra": {"gain_fraction": 0.25, "confidence": 55},
                "put zebra": {"gain_fraction": 0.25, "confidence": 55},
            },
        },
        # A diagonal (a PMCC or its put mirror) whose net debit is over this share of the distance
        # between its strikes: a fast move through the short takes both legs to intrinsic, where the
        # trade is worth about that distance, so a debit near it has little left to make and one past
        # it loses. The strategy guide's entry rule, applied while the trade is held (the chain's net
        # debit, so the short calls rolled for credit count).
        "diagonal_width": {"enabled": True, "max_share_of_width": 0.75, "confidence": 60},
        "time_exit": {
            "enabled": True,
            # A debit trade carrying short legs (a diagonal) is managed when its nearest short is this close.
            "short_leg_dte": 21,
            # A trade of long options only is out before the final weeks' decay.
            "long_only_dte": 14,
            "roll_if_profitable": True,
            "confidence": 70,
        },
    },
    "legs": {
        # A short leg that has given up this share of its premium is bought back, whatever the trade does.
        "short_take_profit": {"enabled": True, "capture": 0.80, "confidence": 85},
        # A butterfly's (broken-wing, broken-heart) long vertical worth this share of its width is sold,
        # keeping the short spread: its value is banked rather than given back if the stock comes back
        # through the body. The tastylive strategy guide's defence (2023); no outside test.
        "long_vertical": {"enabled": True, "share_of_width": 0.90, "confidence": 50},
    },
    "roll": {
        # A roll is only ever for a net credit; one that would cost a debit is a close instead.
        "credit_only": True,
        # A naked short put that a rule says to close may instead roll out in time at the same strike,
        # to the first monthly at least `out_days` past its expiry, when that is modelled to take in at
        # least `min_credit` per share. The credit is MODELLED (the put's own IV, flat term structure):
        # confirm it on the live quote before sending.
        # A roll that would carry the put through an earnings report is never offered: close it, and
        # re-open after the report if the trade is still wanted.
        "naked_put": {
            "enabled": True,
            "out_days": 28,
            "min_credit": 0.10,
            "skip_through_earnings": True,
            "confidence": 60,
        },
        # A diagonal (shorts expiring before a long they are sold against) rolls its SHORT STRUCTURE --
        # the shorts and any wings in their expiry -- out to the first monthly at least `out_days` later,
        # same strikes, when the short reaches the time exit or has given up most of its premium. Only
        # when the trade is not stopped out (rolling against a failed long is repairing a loser), the
        # long outlives the new short by `min_long_days`, the roll is modelled to take in `min_credit` per
        # unit, and every new short stays beyond the long's breakeven (long strike +/- the chain's net
        # debit per share) so an assignment cannot lock in a loss. Earnings inside the new short's life
        # are flagged, not blocked: the long covers the short through a report.
        "diagonal_short": {
            "enabled": True,
            "out_days": 28,
            "min_credit": 0.10,
            "min_long_days": 45,
            "skip_through_earnings": False,
            "confidence": 60,
        },
        # A broken-wing butterfly opened for a credit "flies off" its risk: buy the wing that makes it a
        # plain butterfly and sell the wide one, when that costs no more than `max_share_of_credit` of
        # the net credit taken in. What is left cannot lose (fees aside) and still pays most at the body.
        # The new wing is MODELLED (its IV read off the body's and the old wing's): re-quote first. The
        # tastylive strategy guide's (2023) first goal for a BWB; no outside test.
        "fly_off": {"enabled": True, "max_share_of_credit": 1.0, "confidence": 55},
        # A short strangle or straddle is TESTED when one short reaches `tested_delta` while the other
        # is under `target_delta`: roll the untested short toward the money, in the same expiry, to the
        # listed strike nearest `target_delta`, when that takes in `min_credit` a share at the quoted
        # mids (the snapshot's `roll_chains`, v11). It adds credit and cuts the trade's delta; it is
        # REPAIR, which this ruleset otherwise avoids, so it is a review and never replaces a stop. It
        # never passes the tested strike: an inverted strangle always ranks below closing or rolling out
        # in time without the inversion (the user's rule, 2026-10-19), so a straddle's untested side is
        # never rolled in its own expiry. The tastylive strategy guide's (2023) strangle defence,
        # without its inversion. OFF by default since 2026-10-20, after the same declared local study
        # (results under ~/.cherrypick, by the data licence); kept as an option an override turns on.
        "untested_side": {
            "enabled": False,
            "tested_delta": 0.40,
            "target_delta": 0.30,
            "min_credit": 0.10,
            "confidence": 50,
        },
    },
    # The wheel (wheel.py, docs/skate-plan.md step 5): rules on the short options and shares of a wheel
    # cycle that reconciles. They rank with every other rule by action (a close or a stop still
    # outranks a roll); an unreconciled cycle gets none of them, only a review saying so.
    "wheel": {
        # A short whose mark is at or under its buy-to-close: the resting order's price where one is
        # working, else `btc_price` ($0.05 `btc_price_illiquid` on a wide market). The skate method's
        # exit: a GTC buy-to-close at $0.03 placed right after the fill.
        "take_profit": {"enabled": True, "btc_price": 0.03, "btc_price_illiquid": 0.05, "confidence": 80},
        # A short at or through its strike: roll or close, with the net-zero and premium-gambit rolls
        # out in time priced on the snapshot's quoted chain and each judged by check-roll. Repair, which
        # this ruleset otherwise avoids, so it never outranks a close.
        "tested": {"enabled": True, "confidence": 50},
        # Shares delivered by an assignment and not yet written against: name the covered call at the
        # skate call band (`band`: "fixed" or "by_expiry"). Never a strike without the band.
        "next_leg": {"enabled": True, "band": "fixed", "confidence": 50},
    },
    # The PMCC view (pmcc.py, docs/skate-plan.md step 6): a call diagonal is a PMCC only when its long
    # was entered at `entry_min_delta` or more, or `positions.<SYM>.intent` is "pmcc"; "diagonal"
    # keeps it out. Its rules fire only on a recognised PMCC whose orders reconcile (an unreconciled
    # one gets a review saying so), into the trade holding the short, ranked by action with the
    # diagonal rules it keeps: a close or stop outranks them, and the diagonal's credit roll is offered
    # in place of a roll-or-close, as it is for any close.
    "pmcc": {
        # The long's days are judged on the day it was bought (it is rolled at 90-120 days left).
        "recognize": {"min_long_days": 180, "max_short_days": 60, "entry_min_delta": 0.80},
        # The short strike less the long strike must exceed the long's net cost per share after every
        # premium collected, or an assignment (the long exercised to deliver) locks in a loss.
        "width": {"enabled": True, "confidence": 65},
        # The short in the money with less time value than the next dividend before an ex-date inside
        # its life, or under `near_zero_extrinsic` at any time (the pmcc package's flag).
        "assignment": {"enabled": True, "near_zero_extrinsic": 0.05, "confidence": 65},
        # The long at `days_left` or fewer (the guides' 90-120) or under `min_delta`: review a roll of
        # the long down and out.
        "roll_long": {"enabled": True, "days_left": 120, "min_delta": 0.70, "confidence": 45},
        # The short's roll candidates for a roll-or-close: one per quoted expiry after the short and
        # before the long, the next `expiries`, at most `max_delta`, costing at most `max_debit` a share.
        "roll": {"max_delta": 0.40, "max_debit": 0.10, "expiries": 5},
    },
    "expiry": {
        "enabled": True,
        # Physically-settled legs are closed at or inside this many calendar days to expiry.
        "close_by_dte": 3,
        "cash_settled_roots": [
            "SPX",
            "SPXW",
            "XSP",
            "NDX",
            "NDXP",
            "RUT",
            "RUTW",
            "MRUT",
            "VIX",
            "VIXW",
            "DJX",
        ],
        "confidence": 90,
    },
    "earnings": {"enabled": True, "warn_days": 14},
    # A trade put on to hold through an earnings report: opened `min_days_before`..`max_days_before`
    # days before the call (the user's practice, 2026-10-18: 1 to 7; the report's own day counts for
    # an after-the-close call since 2026-10-20), its nearest short expiring no more than
    # `max_days_expiry_after` days after it. Before the call its close, take-profit and roll rules
    # wait, and the expiry close when the trade comes off before its legs expire; after it, close,
    # unless losing with a leg in a later expiry (the user's call, 2026-10-20: it can recover).
    # Other safety rules never wait.
    "earnings_trade": {
        "enabled": True,
        "min_days_before": 0,
        "max_days_before": 7,
        "max_days_expiry_after": 14,
        "confidence": 75,
    },
    "ex_dividend": {"enabled": True, "warn_days": 10, "confidence": 80},
    # A trade whose net premium is under `max_premium_share` of its modelled max loss (a super bear or
    # bull, a risk reversal) earns from direction, not premium, so the credit and debit rules -- each a
    # percentage of that small premium -- give way to these, judged on what it can win and lose.
    "directional": {
        "enabled": True,
        "max_premium_share": 0.25,
        "take_profit": {
            "enabled": True,
            "share_of_max_gain": 0.50,
            "max_reward_multiple": 1.0,
            "confidence": 65,
        },
        "stop": {"enabled": True, "share_of_max_loss": 0.50, "confidence": 65},
    },
    "conflict_penalty": 10,
    # Legs left out of the evaluation entirely. SPX 0DTE is the flies live loop's own book: it is
    # managed by that loop and settles in cash at the close, so judging it here only adds noise.
    "exclude": {"zero_dte_underlyings": ["SPX"]},
    "pricing": {"rate": 0.04, "grid_low": 0.01, "grid_high": 3.0, "grid_steps": 600},
    # The account as one book (portfolio.py, docs/portfolio-guidance-plan.md). Every band is a retail
    # heuristic with no published out-of-sample evidence, hence the low confidences.
    "portfolio": {
        "enabled": True,
        # Theta per calendar day as a share of net liq. Above the ceiling the book is selling more
        # gamma than the band allows; the floor only informs (theta is not edge).
        "theta": {"enabled": True, "min_pct_nlv": 0.001, "max_pct_nlv": 0.003, "confidence": 40},
        # Beta-weighted delta band in benchmark shares: -short_per_theta x theta to +long_per_theta x
        # theta (leaning slightly long). With no theta to scale by, +/- the BWD whose 1% move costs
        # `fallback_pct_nlv_per_1pct` of net liq.
        "delta": {
            "enabled": True,
            "short_per_theta": 0.5,
            "long_per_theta": 0.75,
            "fallback_pct_nlv_per_1pct": 0.005,
            "confidence": 40,
        },
        # A short-gamma book whose break-even move is under this multiple of the benchmark's implied
        # one-day move is losing the race between theta and gamma.
        "breakeven": {"enabled": True, "min_multiple_of_expected_move": 1.0, "confidence": 40},
        # One underlying over this share of the book's gross theta or gross beta-weighted delta.
        "concentration": {"enabled": True, "max_share": 0.25, "confidence": 40},
        # When the broker's beta and its three-month correlation disagree enough to doubt the beta.
        "beta_doubt": {"min_abs_corr": 0.3, "near_zero_beta": 0.1},
        # Benchmark shocks the stress line reprices the book at, each underlying moved by beta x shock.
        "stress": {"shocks": [-0.10, 0.05]},
        # Buying power used against a ceiling that rises with VIX: [VIX under, cap], the last bound
        # null for "anything above". The common retail table (more room when premium is rich).
        "buying_power": {
            "enabled": True,
            "vix_steps": [[15, 0.25], [20, 0.30], [30, 0.35], [40, 0.40], [None, 0.50]],
            "confidence": 40,
        },
        # The risk matrix (benchmark moves x IV shifts in vol points, time held still) and the risk
        # graph (`graph` = [from, to, step] of benchmark moves, now and at the nearest expiry).
        "scenarios": {
            "moves": [-0.10, -0.05, -0.02, -0.01, 0.0, 0.01, 0.02, 0.05, 0.10],
            "iv_shifts": [-0.05, 0.0, 0.05],
            # "term": each leg's shift scaled by sqrt(30 / DTE), front months moving most; "flat": alike.
            "iv_shift_weighting": "term",
            "graph": [-0.15, 0.15, 0.01],
        },
        # When `portfolio.delta` fires, size an XSP vertical (hedge.py) toward a target `inset` of the
        # band's width inside the breached edge: a credit spread with its short at
        # `credit_short_delta`, and a debit spread with its long at `debit_long_delta`, each `width`
        # points wide, at most `max_units` of either.
        "hedge": {
            "enabled": True,
            "inset": 0.25,
            "credit_short_delta": 0.30,
            "debit_long_delta": 0.40,
            "width": 5,
            "max_units": 10,
            # A credit spread never takes buying power used past this share of net liq. A debit spread
            # is exempt: it only lowers the book's worst case, so it is offered past the cap and says so.
            "max_buying_power_pct": 0.50,
        },
        # Income trades from our own universe, sized to the account (candidates.py). The structures
        # are the screener rules on the cycle monthly; the caps scale with net liq. The short put and
        # covered call deltas are the Screeners lists' (`cherrypick.core.incomerules`, 16, 2026-10-19:
        # the delta studies found no edge at the vendor's 0.40 and 0.165, and the least loss at 16).
        # Buying power is held under the VIX-scaled cap the reading states, else `max_buying_power_pct`.
        "candidates": {
            "enabled": True,
            "short_put_delta": _income.SHORT_PUT_DELTA,
            "spread_long_delta": 0.25,
            "covered_call_delta": _income.COVERED_CALL_DELTA,
            # A short put or covered call whose nearest strike misses its delta by more is not built:
            # the Screeners lists' tolerance, the history replay's (`cherrypick.core.incomerules`).
            "delta_tolerance": _income.DELTA_TOLERANCE,
            "spread_min_credit": 0.331,
            "per_trade_risk_pct": 0.03,
            "notional_per_name_pct": {"cash": 0.25, "margin": 0.50},
            "max_buying_power_pct": 0.50,
            "max_name_theta_share": 0.25,
            "max_units": 10,
            "show": 15,
            "show_too_big": 10,
            # A waiting candidate (stopped by buying power or the theta ceiling) is graded by the
            # buying power it would leave: under the VIX cap ok, within this much over it `over`,
            # beyond that `far_over`.
            "bp_warn_over": 0.10,
        },
    },
    # Per-underlying overrides: {"SPY": {"intent": "hedge", "disable": ["debit.stop"], "note": "..."}}.
    # `intent: "pmcc"` recognises a call diagonal as a PMCC; `"diagonal"` keeps it out (pmcc.py).
    "positions": {},
}

# Every rule id an override's `disable` list may name.
RULE_IDS = (
    "expiry",
    "credit.take_profit",
    "credit.stop",
    "credit.time_exit",
    "credit.breached",
    "credit.close_before_earnings",
    "debit.stop",
    "debit.take_profit",
    "debit.diagonal_width",
    "debit.time_exit",
    "legs.short_take_profit",
    "legs.long_vertical",
    "roll.naked_put",
    "roll.diagonal_short",
    "roll.fly_off",
    "roll.untested_side",
    "wheel.take_profit",
    "wheel.tested",
    "wheel.next_leg",
    "pmcc.width",
    "pmcc.assignment",
    "pmcc.roll_long",
    "directional.take_profit",
    "directional.stop",
    "ex_dividend",
    "risk.oversize",
    "earnings_trade",
    "portfolio.theta",
    "portfolio.delta",
    "portfolio.breakeven",
    "portfolio.concentration",
    "portfolio.buying_power",
)


def overrides_path() -> Path:
    return _home.config_path("manage")


def _merge(base: Any, over: Any, path: str, warnings: list[str]) -> Any:
    if not isinstance(base, dict) or not isinstance(over, dict):
        if isinstance(base, dict) != isinstance(over, dict):
            warnings.append(
                f"{path or '(root)'}: expected {'an object' if isinstance(base, dict) else 'a value'}"
            )
            return base
        return copy.deepcopy(over)
    out = copy.deepcopy(base)
    for key, value in over.items():
        if key.startswith("_"):
            continue  # comments
        where = f"{path}.{key}" if path else key
        if key not in base:
            warnings.append(f"{where}: unknown key, ignored")
            continue
        if key == "positions":
            out[key] = copy.deepcopy(value) if isinstance(value, dict) else {}
            continue
        if key == "by_shape" and isinstance(value, dict):
            out[key] = _merge_by_shape(base, value, where, warnings)
            continue
        out[key] = _merge(base[key], value, where, warnings)
    return out


def _merge_by_shape(rule: dict[str, Any], over: dict[str, Any], path: str, warnings: list[str]) -> dict:
    """A rule's per-shape targets with `over` merged in. Any shape `shapes.classify` names may be added
    (a ratio butterfly by its quantities, "1-3-2"), carrying only the rule's own fields; a shape it
    never names is reported, as a misspelt key is."""
    out = copy.deepcopy(rule["by_shape"])
    fields = {k for k in rule if k not in ("enabled", "by_shape")}
    for shape, entry in over.items():
        if shape.startswith("_"):
            continue
        where = f"{path}.{shape}"
        if shape not in _shapes.NAMES and not _shapes.is_ratio(shape):
            warnings.append(f"{where}: unknown shape, ignored")
            continue
        if not isinstance(entry, dict):
            warnings.append(f"{where}: expected an object")
            continue
        if shape in out:
            out[shape] = _merge(out[shape], entry, where, warnings)
            continue
        kept = {}
        for name, value in entry.items():
            if name.startswith("_"):
                continue
            if name in fields:
                kept[name] = copy.deepcopy(value)
            else:
                warnings.append(f"{where}.{name}: unknown key, ignored")
        out[shape] = kept
    return out


def for_shape(rule: dict[str, Any], shape: str | None, field: str) -> tuple[float, Any, bool]:
    """A rule's threshold and confidence for a trade of `shape`: its `by_shape` entry where it has one
    (a field the entry leaves out is the rule's own), else the rule's. The flag is True when the shape's
    own entry set it."""
    entry = (rule.get("by_shape") or {}).get(shape or "") or {}
    return float(entry.get(field, rule[field])), entry.get("confidence", rule["confidence"]), bool(entry)


def _check_positions(rules: dict[str, Any], warnings: list[str]) -> None:
    for sym, entry in (rules.get("positions") or {}).items():
        if not isinstance(entry, dict):
            warnings.append(f"positions.{sym}: expected an object")
            continue
        for rule in entry.get("disable") or []:
            if rule not in RULE_IDS:
                warnings.append(f"positions.{sym}.disable: unknown rule '{rule}'")


def effective(overrides: dict[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
    """The default with `overrides` merged over it, and every override that could not be applied."""
    warnings: list[str] = []
    rules = _merge(DEFAULT_RULES, overrides or {}, "", warnings)
    _check_positions(rules, warnings)
    return rules, warnings


def load(path: Path | None = None) -> tuple[dict[str, Any], list[str], str | None]:
    """The effective ruleset, its warnings, and the override file used (None when there is none)."""
    target = path or overrides_path()
    if not target.exists():
        return effective(None) + (None,)
    try:
        doc = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        rules, warnings = effective(None)
        return (
            rules,
            [f"{target.name}: unreadable ({exc}); the default ruleset is in force", *warnings],
            str(target),
        )
    rules, warnings = effective(doc if isinstance(doc, dict) else {})
    return rules, warnings, str(target)


def disabled(rules: dict[str, Any], underlying: str, rule_id: str) -> bool:
    entry = (rules.get("positions") or {}).get(underlying) or {}
    if rule_id in (entry.get("disable") or []):
        return True
    section, _, name = rule_id.partition(".")
    node = rules.get(section) or {}
    # `risk.oversize` and the like are switched by their section's own `enabled`.
    node = node.get(name, node) if name else node
    return isinstance(node, dict) and node.get("enabled") is False
