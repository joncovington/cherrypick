"""Black-Scholes gamma from an implied volatility. Pure.

The vendor's Standard tier carries first-order greeks and IV but not gamma (a Professional field), and
the GEX surface the replay rebuilds needs gamma per strike. So gamma is computed here, from the
vendor's own IV and underlying price at the same instant, with no rate or dividend: over hours to a
0DTE expiry both move gamma by far less than the IV's own error. Validation (`validate`) measures the
result against the gamma the recorder took from the live feed; nothing assumes they agree.

Time to expiry is wall-clock time to the session's close (16:00 ET, 13:00 on an early close) over a
365-day year, floored at one minute so the last ticks of a session do not divide by zero.
"""

from __future__ import annotations

import math

YEAR_SECONDS = 365.0 * 86400.0
MIN_T_SECONDS = 60.0


def year_fraction(seconds_to_expiry: float) -> float:
    return max(float(seconds_to_expiry), MIN_T_SECONDS) / YEAR_SECONDS


def bs_gamma(spot: float, strike: float, iv: float | None, t_years: float) -> float | None:
    """Gamma per one point of the underlying, or None where it is undefined: no IV (the vendor
    reports 0 when its solver failed), a non-positive input, or a degenerate time."""
    if iv is None or not (iv > 0 and spot > 0 and strike > 0 and t_years > 0):
        return None
    vol_t = iv * math.sqrt(t_years)
    d1 = (math.log(spot / strike) + 0.5 * iv * iv * t_years) / vol_t
    return math.exp(-0.5 * d1 * d1) / (math.sqrt(2.0 * math.pi) * spot * vol_t)
