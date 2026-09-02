"""The daily VIX/VIX3M signal: ratio, regime classification, and the two-day-confirmed hook.

Pure functions over quotes/rows the caller already fetched. No I/O, no clock reads.

The advisor's GEX-counts lesson (2026-08-21) is the reason this module is RTH-gated and
basis-stamped from day one: a recorder that freezes on the last streamed value overnight
double-weights whatever sign the session ended on. VIX/VIX3M quotes freeze the same way once the
feed goes quiet, so every reading here carries the quote ages it was computed from and a
`freshness` verdict (`"live"` or `"stale"`), and a stale reading is refused (`ok: False`) rather
than handed to a caller as if it were measured.

Regime vocabulary:
- `ratio` = VIX / VIX3M.
- `"contango"` when ratio < `contango_max` (a buffer below 1.0, so a knife-edge 0.999 day is not
  mistaken for the harvest regime) — the everyday state (~84% of days historically).
- `"backwardation"` otherwise — stress; no new contango-gated entries.
- The **hook**: `ratio > hook_threshold` AND today's ratio is below YESTERDAY's — a deep
  backwardation spike that has started to mean-revert. Requires the PRIOR session's ratio (the
  two-day confirmation), never a same-day read alone; a hook with no prior ratio on file is simply
  not a hook (never a guess).
"""

from __future__ import annotations

REGIME_DEFAULTS = {
    "contango_max": 0.97,
    "hook_threshold": 1.10,
    "max_quote_age_seconds": 300,
}


def _params(params: dict | None) -> dict:
    return {**REGIME_DEFAULTS, **{k: v for k, v in (params or {}).items() if k in REGIME_DEFAULTS}}


def ratio(vix: float, vix3m: float) -> float | None:
    """VIX / VIX3M, or None on a non-positive denominator (a torn read, never a divide-by-zero)."""
    if vix3m is None or vix3m <= 0 or vix is None:
        return None
    return round(vix / vix3m, 6)


def classify(r: float, params: dict | None = None) -> str:
    """`"contango"` or `"backwardation"` for a MEASURED ratio. Callers must gate on `ok` first —
    this never sees an unmeasured ratio."""
    p = _params(params)
    return "contango" if r < p["contango_max"] else "backwardation"


def hook_signal(r: float, prior_ratio: float | None, params: dict | None = None) -> bool:
    """The two-day-confirmed hook: today's ratio clears `hook_threshold` AND sits below
    yesterday's — a spike that has started to mean-revert. False (never a guess) when no prior
    ratio is on file."""
    p = _params(params)
    if prior_ratio is None:
        return False
    return r > p["hook_threshold"] and r < prior_ratio


def reading(
    vix_quote: dict | None,
    vix3m_quote: dict | None,
    *,
    prior_ratio: float | None,
    params: dict | None = None,
) -> dict:
    """One regime reading from two quote dicts (`{"value": float, "age_seconds": float}` or
    `{"value": float}` for a stored close with no live age), with the module's own freshness gate.

    Returns `{"ok": True, "ratio", "regime", "hook", "basis", "vix", "vix3m", ...}` or
    `{"ok": False, "reason": ...}` — a stale or missing quote refuses rather than freezing the
    last value forward (the GEX-counts lesson: a frozen overnight value silently double-weights
    whatever sign the session ended on).
    """
    p = _params(params)
    if vix_quote is None or vix_quote.get("value") is None:
        return {"ok": False, "reason": "no_vix_quote"}
    if vix3m_quote is None or vix3m_quote.get("value") is None:
        return {"ok": False, "reason": "no_vix3m_quote"}
    max_age = p["max_quote_age_seconds"]
    for name, q in (("vix", vix_quote), ("vix3m", vix3m_quote)):
        age = q.get("age_seconds")
        if age is not None and age > max_age:
            return {"ok": False, "reason": f"stale_{name}", "age_seconds": age}
    r = ratio(vix_quote["value"], vix3m_quote["value"])
    if r is None:
        return {"ok": False, "reason": "non_positive_vix3m"}
    return {
        "ok": True,
        "ratio": r,
        "regime": classify(r, p),
        "hook": hook_signal(r, prior_ratio, p),
        "vix": vix_quote["value"],
        "vix3m": vix3m_quote["value"],
        "vix_age_seconds": vix_quote.get("age_seconds"),
        "vix3m_age_seconds": vix3m_quote.get("age_seconds"),
        "prior_ratio": prior_ratio,
    }
