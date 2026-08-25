"""The deployment score: five macro signals blended 0-100, recorded beside the phase.

This is a MEASUREMENT, not a gate. The five-gate GREEN/YELLOW/RED phase in ``gates.py`` stays the
operative morning verdict; the score is written into the same fact pack so weeks of it can be held
against outcomes before anything is allowed to act on it. Nothing in the suite sizes, skips, or
trades off this number today, and the pack says so on the block itself.

The five signals, each 0-100 where high means "deploy" and low means "defend":

- **vix_level** -- current VIX percentile-ranked against its trailing year of closes; a low-vol
  tape scores high. A bonus below 15 and a penalty above 30 keep the absolute level from being
  laundered entirely into a relative rank.
- **term_structure** -- VIX/VIX3M, the same ratio the contango gate reads, rescaled: 0.85 (steep
  contango, calm) -> 100, 1.15 (backwardation, stress) -> 0.
- **breadth** -- the share of the eleven SPDR sector ETFs above their own 200-day SMA, mapped
  30% -> 0, 80% -> 100. A labeled proxy: eleven sector ETFs are not five hundred stocks, but they
  are breadth the suite already streams, and a narrow mega-cap rally still shows up as most
  sectors under their averages.
- **credit** -- the HYG/TLT ratio's z-score against its trailing year: -2 (risk appetite) -> 100,
  +2 (credit stress) -> 0. A labeled proxy for spreads, not an OAS.
- **vix_roc** -- VIX's 20-session rate of change, the fear-of-fear proxy standing in for put/call
  flow the suite does not record: -30% -> 100, +50% -> 0.

A sixth signal from the reference design -- factor-crowding correlation -- is deferred, not
dropped silently: it needs ~100 single-name daily histories the streamer has no reason to carry
yet, and its weight is simply absent from the blend until it earns the data cost.

The honesty rules are the gates' rules: a signal whose inputs nobody measured is UNKNOWN, never a
guess; the blend renormalizes its declared weights over the measured signals and records that it
did; fewer than MIN_MEASURED_FOR_SCORE measured signals means NO score, because two readings do
not pretend to summarize a market. Thresholds and weights are constants versioned in git, on
purpose -- same reasoning as gates.py.
"""

from __future__ import annotations

from statistics import fmean, pstdev
from typing import Any

# Declared blend weights. Deliberately sum to 0.90: the missing 0.10 is the deferred
# factor-crowding signal's seat, kept visible here rather than quietly redistributed.
WEIGHTS = {
    "vix_level": 0.25,
    "term_structure": 0.20,
    "breadth": 0.20,
    "credit": 0.15,
    "vix_roc": 0.10,
}

MEASURED = "measured"
UNKNOWN = "unknown"

MIN_MEASURED_FOR_SCORE = 4

# vix_level: percentile lookback and the absolute-level adjustments.
PERCENTILE_LOOKBACK = 252
MIN_HISTORY_FOR_RANK = 200
VIX_CALM_LEVEL = 15.0
VIX_CALM_BONUS = 5.0
VIX_STRESS_LEVEL = 30.0
VIX_STRESS_PENALTY = 10.0

# term_structure: VIX/VIX3M ratio endpoints.
TERM_CALM_RATIO = 0.85    # -> 100
TERM_STRESS_RATIO = 1.15  # -> 0

# breadth: sector ETFs vs their own long SMA.
BREADTH_SMA_SESSIONS = 200
MIN_SECTORS_MEASURED = 8
BREADTH_FLOOR_PCT = 30.0  # -> 0
BREADTH_CEIL_PCT = 80.0   # -> 100

# credit: HYG/TLT ratio z-score endpoints and lookback.
#
# NOTE THE SIGN, which is the opposite of the spread convention it is easy to copy by mistake. This
# measures a RATIO, not a spread: high-yield falling against Treasuries means credit stress, so the
# ratio moves DOWN exactly when a spread would move UP. A ratio z of -2 is therefore the stressed
# end and +2 the risk-appetite end.
CREDIT_LOOKBACK = 252
MIN_HISTORY_FOR_Z = 200
CREDIT_STRESS_Z = -2.0  # ratio depressed vs its year -> 0
CREDIT_CALM_Z = 2.0     # ratio elevated vs its year -> 100

# vix_roc: 20-session VIX rate of change endpoints.
ROC_SESSIONS = 20
ROC_CALM_PCT = -30.0    # -> 100
ROC_STRESS_PCT = 50.0   # -> 0

# Zones. Cut-offs from the reference design; record-only until the backtest and weeks of recorded
# scores say they mean something.
ZONE_FULL_MIN = 70.0
ZONE_REDUCED_MIN = 40.0


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


def _linear(value: float, at_zero: float, at_hundred: float) -> float:
    """Linear map with clamping; works whichever direction the scale runs."""
    if at_zero == at_hundred:
        return 50.0
    return _clamp((value - at_zero) / (at_hundred - at_zero) * 100.0)


def _signal(signal_id: str, label: str, status: str, detail: str,
            score: float | None = None, value: float | None = None) -> dict:
    return {
        "id": signal_id,
        "label": label,
        "status": status,
        "score": round(score, 1) if score is not None else None,
        "value": value,
        "weight": WEIGHTS[signal_id],
        "detail": detail,
    }


def _closes(history: dict[str, list[dict]], symbol: str) -> list[tuple[str, float]]:
    """(session, close) pairs for one symbol, in date order, junk dropped."""
    out = []
    for row in history.get(symbol) or []:
        session, close = row.get("session"), row.get("close")
        if isinstance(session, str) and isinstance(close, (int, float)):
            out.append((session, float(close)))
    return sorted(out)


def _current(readings: dict[str, Any], name: str) -> float | None:
    value = (readings.get(name) or {}).get("value")
    return float(value) if isinstance(value, (int, float)) else None


def percentile_rank(closes: list[float], value: float) -> float:
    """Where `value` sits in `closes`, 0-100, by the fraction STRICTLY below it.

    Public and shared: `facts._vol_regime` renders percentiles beside this score's, on the same page,
    for the same readings. Two implementations of "where does today sit" put two different numbers in
    front of one reader -- which is not a rounding difference but a contradiction, and the kind that
    erodes trust in every other number on the page. The strict-below convention is this function's,
    and callers inherit it rather than choosing their own.
    """
    if not closes:
        return 0.0
    return sum(1 for c in closes if c < value) / len(closes) * 100.0


def _vix_level(readings: dict[str, Any], history: dict[str, list[dict]]) -> dict:
    label = f"VIX percentile ({PERCENTILE_LOOKBACK}-session)"
    vix = _current(readings, "vix")
    closes = [c for _, c in _closes(history, "VIX")][-PERCENTILE_LOOKBACK:]
    if vix is None:
        return _signal("vix_level", label, UNKNOWN, "VIX not measured")
    if len(closes) < MIN_HISTORY_FOR_RANK:
        return _signal("vix_level", label, UNKNOWN,
                       f"only {len(closes)} of {MIN_HISTORY_FOR_RANK} history sessions held")
    rank = percentile_rank(closes, vix)
    score = 100.0 - rank
    adjustment = ""
    if vix < VIX_CALM_LEVEL:
        score += VIX_CALM_BONUS
        adjustment = f", +{VIX_CALM_BONUS:.0f} calm bonus (VIX < {VIX_CALM_LEVEL:.0f})"
    elif vix > VIX_STRESS_LEVEL:
        score -= VIX_STRESS_PENALTY
        adjustment = f", -{VIX_STRESS_PENALTY:.0f} stress penalty (VIX > {VIX_STRESS_LEVEL:.0f})"
    return _signal("vix_level", label, MEASURED,
                   f"VIX {vix:.2f} at the {rank:.0f}th percentile of {len(closes)} sessions"
                   + adjustment,
                   score=_clamp(score), value=vix)


def _term_structure(readings: dict[str, Any]) -> dict:
    label = "Vol term structure (VIX/VIX3M)"
    vix, vix3m = _current(readings, "vix"), _current(readings, "vix3m")
    if vix is None or vix3m is None or vix3m == 0:
        return _signal("term_structure", label, UNKNOWN, "VIX or VIX3M not measured")
    ratio = vix / vix3m
    return _signal("term_structure", label, MEASURED,
                   f"ratio {ratio:.3f} (VIX {vix:.2f} / VIX3M {vix3m:.2f}); "
                   f"{TERM_CALM_RATIO} -> 100, {TERM_STRESS_RATIO} -> 0",
                   score=_linear(ratio, TERM_STRESS_RATIO, TERM_CALM_RATIO), value=round(ratio, 4))


def _breadth(history: dict[str, list[dict]], sector_symbols) -> dict:
    label = f"Sector breadth vs {BREADTH_SMA_SESSIONS}-day SMA (proxy)"
    above = total = 0
    for symbol in sector_symbols:
        closes = [c for _, c in _closes(history, symbol)]
        if len(closes) < BREADTH_SMA_SESSIONS:
            continue
        total += 1
        sma = fmean(closes[-BREADTH_SMA_SESSIONS:])
        if closes[-1] > sma:
            above += 1
    if total < MIN_SECTORS_MEASURED:
        return _signal("breadth", label, UNKNOWN,
                       f"only {total} of 11 sector ETFs hold {BREADTH_SMA_SESSIONS} sessions "
                       f"(need {MIN_SECTORS_MEASURED})")
    pct = above / total * 100.0
    return _signal("breadth", label, MEASURED,
                   f"{above} of {total} sector ETFs above their SMA ({pct:.0f}%); "
                   f"{BREADTH_FLOOR_PCT:.0f}% -> 0, {BREADTH_CEIL_PCT:.0f}% -> 100 -- "
                   "eleven sector ETFs standing proxy for full index breadth",
                   score=_linear(pct, BREADTH_FLOOR_PCT, BREADTH_CEIL_PCT), value=round(pct, 1))


def _credit(history: dict[str, list[dict]]) -> dict:
    label = f"Credit proxy (HYG/TLT z-score, {CREDIT_LOOKBACK}-session)"
    hyg = dict(_closes(history, "HYG"))
    tlt = dict(_closes(history, "TLT"))
    ratios = [hyg[d] / tlt[d] for d in sorted(hyg.keys() & tlt.keys()) if tlt[d]]
    window = ratios[-CREDIT_LOOKBACK:]
    if len(window) < MIN_HISTORY_FOR_Z:
        return _signal("credit", label, UNKNOWN,
                       f"only {len(window)} of {MIN_HISTORY_FOR_Z} aligned HYG/TLT sessions held")
    mean, sd = fmean(window), pstdev(window)
    if sd == 0:
        return _signal("credit", label, UNKNOWN, "flat ratio series (zero variance)")
    z = (window[-1] - mean) / sd
    return _signal("credit", label, MEASURED,
                   f"HYG/TLT ratio {window[-1]:.4f}, z {z:+.2f} vs {len(window)} sessions; "
                   f"{CREDIT_STRESS_Z:+.0f} (stress) -> 0, {CREDIT_CALM_Z:+.0f} (calm) -> 100 -- "
                   "ETF ratio standing proxy for cash credit spreads, and it moves opposite a "
                   "spread: high yield falling against Treasuries pushes the ratio DOWN",
                   score=_linear(z, CREDIT_STRESS_Z, CREDIT_CALM_Z), value=round(z, 2))


def _vix_roc(readings: dict[str, Any], history: dict[str, list[dict]]) -> dict:
    label = f"VIX {ROC_SESSIONS}-session rate of change (put/call proxy)"
    vix = _current(readings, "vix")
    closes = [c for _, c in _closes(history, "VIX")]
    if vix is None:
        return _signal("vix_roc", label, UNKNOWN, "VIX not measured")
    if len(closes) < ROC_SESSIONS + 1 or closes[-(ROC_SESSIONS + 1)] == 0:
        return _signal("vix_roc", label, UNKNOWN,
                       f"fewer than {ROC_SESSIONS + 1} VIX history sessions held")
    base = closes[-(ROC_SESSIONS + 1)]
    roc = (vix / base - 1.0) * 100.0
    return _signal("vix_roc", label, MEASURED,
                   f"VIX {vix:.2f} vs {base:.2f} {ROC_SESSIONS} sessions ago ({roc:+.1f}%); "
                   f"{ROC_CALM_PCT:.0f}% -> 100, {ROC_STRESS_PCT:.0f}% -> 0 -- "
                   "VIX momentum standing proxy for put/call flow the suite does not record",
                   score=_linear(roc, ROC_STRESS_PCT, ROC_CALM_PCT), value=round(roc, 1))


def _zone(score: float) -> str:
    if score >= ZONE_FULL_MIN:
        return "full"
    if score >= ZONE_REDUCED_MIN:
        return "reduced"
    return "defensive"


def evaluate(readings: dict[str, Any], history: dict[str, list[dict]], sector_symbols) -> dict:
    """The deployment block for the fact pack. ``history`` is facts.build's per-symbol close
    series ({symbol: [{"session", "close"}, ...]}, date order); ``sector_symbols`` the declared
    sector-ETF set, passed in so this module never imports the symbol registry."""
    signals = [
        _vix_level(readings, history),
        _term_structure(readings),
        _breadth(history, sector_symbols),
        _credit(history),
        _vix_roc(readings, history),
    ]
    measured = [s for s in signals if s["status"] == MEASURED]
    block: dict[str, Any] = {
        "signals": signals,
        "signals_measured": len(measured),
        "signals_total": len(signals),
        "deferred": ["factor_crowding"],
        "record_only": True,
        "note": "a recorded measurement -- feeds no gate, no phase, no sizing",
    }
    if len(measured) < MIN_MEASURED_FOR_SCORE:
        block.update({
            "score": None, "zone": None, "weights_renormalized": False,
            "reason": f"only {len(measured)} of {len(signals)} signals measured "
                      f"(need {MIN_MEASURED_FOR_SCORE})",
        })
        return block
    weight_sum = sum(s["weight"] for s in measured)
    score = round(sum(s["score"] * s["weight"] for s in measured) / weight_sum, 1)
    block.update({
        "score": score,
        "zone": _zone(score),
        "weights_renormalized": len(measured) < len(signals),
        "reason": None,
    })
    return block
