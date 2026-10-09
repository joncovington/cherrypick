"""The market's implied variance for a position's own expiration, recorded at entry (2026-10-09).

`cherrypick.core.impliedvar` -- Cboe's model-free VIX arithmetic for any expiration, validated on
the suite's history (SPY 30-day vs the published VIX, 2020-2026: correlation 0.996, mean gap 0.51
vol points) -- read from the stream cache at the same instant the entry was planned. A broken-wing
fly carries its risk in the wings, and this is the one number that prices them, where the
at-the-money expected move does not. With the realised variance to expiry it gives the premium each
trade actually sold.

RECORDING only: nothing in the entry decision reads it, so it changes nothing bwb does or measures.
A strip the cache's strike window cut short reads `entry_iv_complete = 0` and is a LOWER bound
(the wings it misses are where the variance it lacks lives); a reading that cannot be taken stores
its reason and no number, never a guess.
"""

from __future__ import annotations

import time
from typing import Any

# Columns this writes on the position row (declared in db._ADDED_COLUMNS).
COLUMNS = (
    "entry_iv_vol",
    "entry_iv_var",
    "entry_iv_complete",
    "entry_iv_quotes",
    "entry_iv_missing",
    "entry_iv_years",
    "entry_iv_rate",
    "entry_iv_reason",
)
DEFAULT_RATE = 0.04  # Cboe uses Treasury yields; the suite carries no curve. Second order, and stored.


def measure(
    cache_path: str,
    symbol: str,
    root: str,
    expiration: str,
    *,
    rate: float = DEFAULT_RATE,
    max_age_seconds: float = 300,
    now_ts: float | None = None,
) -> dict[str, Any]:
    """The `entry_iv_*` columns for one expiration, read from the cache now. Never raises."""
    from cherrypick.core import impliedvar as iv
    from cherrypick.core import streamcache

    now_ts = time.time() if now_ts is None else now_ts
    out: dict[str, Any] = {c: None for c in COLUMNS}
    out["entry_iv_rate"] = rate
    try:
        conn = streamcache.connect(cache_path)
        try:
            strip = iv.strip_from_cache(
                conn, symbol, expiration, root, now_ts=now_ts, max_age_seconds=max_age_seconds
            )
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 -- a reading not taken is recorded as such
        out["entry_iv_reason"] = f"cache: {type(exc).__name__}: {exc}"[:200]
        return out
    out["entry_iv_quotes"] = len(strip["quotes"])
    out["entry_iv_missing"] = strip["missing"]
    if not strip.get("expires_at"):
        out["entry_iv_reason"] = "no_expiry_in_cache"
        return out
    years = iv.years_between(now_ts, strip["expires_at"])
    out["entry_iv_years"] = round(years, 6)
    term = iv.single_term(strip["quotes"], years=years, rate=rate)
    if not term.get("ok"):
        out["entry_iv_reason"] = str(term.get("reason"))
        return out
    out.update(
        {
            "entry_iv_vol": round(term["vol"], 3),
            "entry_iv_var": term["variance"],
            "entry_iv_complete": 1 if term["complete"] else 0,
        }
    )
    return out
