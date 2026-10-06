"""The daily VIX/VIX3M read and the switch it drives. Pure functions; no I/O, no clock reads.

`ratio` = VIX / VIX3M. The switch has two thresholds and a memory:

- below `enter_below` -> hold the risk asset (contango: the roll yield is there to collect);
- at or above `exit_at_or_above` -> hold cash (backwardation, or close enough to it);
- between the two -> keep whatever is held. `control` declares the two equal, so its band is empty
  and it is a plain one-threshold switch; `flipexit` holds through 0.97-1.0 and leaves only on a
  real inversion.

Replayed through this function on SVXY 2018-03 to 2026-10 (SHV as cash, 2 bps a side,
`scripts/contango_replay.py`): the plain switch made 7.4% a year with a 39.8% drawdown, the band
9.8% with 41.6%, buy-and-hold 12.3% with 62.2%. The gate buys drawdown, not return; whether that
trade is worth its switching cost is what this module measures forward.

A reading carries the ages of the two prints it came from and refuses when either is stale or
missing, the curve rule: a frozen value must never pass for a measured one.
"""

from __future__ import annotations

REGIME_DEFAULTS = {"max_quote_age_seconds": 300}

RISK = "risk"
CASH = "cash"


def ratio(vix: float | None, vix3m: float | None) -> float | None:
    """VIX / VIX3M, or None on a missing or non-positive input (a torn read, never a divide-by-zero)."""
    if vix is None or vix3m is None or vix <= 0 or vix3m <= 0:
        return None
    return round(vix / vix3m, 6)


def reading(vix_quote: dict | None, vix3m_quote: dict | None, params: dict | None = None) -> dict:
    """One regime reading from `{"value", "age_seconds"}` quotes: `{"ok": True, "ratio", ...}` or a
    refusal naming the input that failed."""
    p = {**REGIME_DEFAULTS, **{k: v for k, v in (params or {}).items() if k in REGIME_DEFAULTS}}
    if vix_quote is None or vix_quote.get("value") is None:
        return {"ok": False, "reason": "no_vix_quote"}
    if vix3m_quote is None or vix3m_quote.get("value") is None:
        return {"ok": False, "reason": "no_vix3m_quote"}
    for name, q in (("vix", vix_quote), ("vix3m", vix3m_quote)):
        age = q.get("age_seconds")
        if age is not None and age > p["max_quote_age_seconds"]:
            return {"ok": False, "reason": f"stale_{name}", "age_seconds": age}
    r = ratio(vix_quote["value"], vix3m_quote["value"])
    if r is None:
        return {"ok": False, "reason": "non_positive_input"}
    return {
        "ok": True,
        "ratio": r,
        "vix": vix_quote["value"],
        "vix3m": vix3m_quote["value"],
        "vix_age_seconds": vix_quote.get("age_seconds"),
        "vix3m_age_seconds": vix3m_quote.get("age_seconds"),
    }


def thresholds(params: dict) -> tuple[float, float]:
    """(enter_below, exit_at_or_above) from an arm's merged params. Raises on an inverted pair: a
    band where the exit sits below the entry would switch in and out on the same ratio."""
    enter = float(params.get("enter_below", 0.97))
    exit_ = float(params.get("exit_at_or_above", enter))
    if exit_ < enter:
        raise ValueError(f"exit_at_or_above {exit_} is below enter_below {enter}")
    return enter, exit_


def target_state(r: float, current: str | None, params: dict) -> str:
    """`RISK` or `CASH` for a MEASURED ratio. `current` None (an arm holding nothing yet) counts as
    cash, so an arm born inside the band waits for a real contango read before buying."""
    enter, exit_ = thresholds(params)
    if r < enter:
        return RISK
    if r >= exit_:
        return CASH
    return current if current in (RISK, CASH) else CASH
