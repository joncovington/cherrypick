"""An arm's date-based gates, in words, for the sessions ahead (`core.ahead`).

What `paper.evaluate_entry` and `paper.force_close_active` do on a dated kind of day, read from the
arm's merged params (and the base config for the closes, which are base-wide), plus the gates that read
a value at the time. The labels are the shared calendar's (`core.ahead.calendar_on`). A test holds the
keys read here to `paper.py`'s source, so a gate that moves there cannot keep its old words here.
"""

from __future__ import annotations

from cherrypick.core import ahead as core_ahead

#: At or above these a VIX level or a VIX1D ratio never binds: VIX has never closed above 90.
OFF_VIX = 100
OFF_RATIO = 10


def _pct(x: float, nd: int = 1) -> str:
    return f"{x * 100:.{nd}f}%"


def date_gates(params: dict, base: dict) -> list[dict]:
    """The arm's gates that fire on a dated kind of day."""
    bs = params.get("fomc_blackout_start", "13:30")
    be = params.get("fomc_blackout_end", "14:30")
    close_fomc = base.get("fomc_blackout_start", "13:30")
    window_end = params.get("entry_window_end", "14:30")
    eod = base.get("physical_settlement_force_close_time", "15:30")
    settle = base.get("expiration_settlement_time", "16:00")
    return [
        {
            "label": "FOMC",
            "name": "Fed decision (FOMC)",
            "effect": f"No new entries {bs} to {be}. Open positions close at {close_fomc}.",
            "depends_on": (
                f"after {be}, IV rank at least {_pct(params.get('fomc_post_blackout_min_iv_rank', 0.40), 0)} "
                f"and the day's range under {_pct(params.get('fomc_post_blackout_max_intraday_range_pct', 0.005))}"
            ),
        },
        {
            "label": "TripleWitching",
            "name": "Triple witching",
            "effect": "No new entries after 12:30. Open positions close at 14:00.",
            "depends_on": "a volatile open skips the day",
        },
        {
            "label": "QuarterlyExpiry",
            "name": "Quarterly expiry",
            "effect": "Open positions close at 14:00.",
            "depends_on": (
                "a volatile open skips the day, and entries stop once the day's range passes "
                f"{_pct(params.get('quarterly_expiry_max_intraday_range_pct', 0.005))}"
            ),
        },
        {
            "label": "EarlyClose",
            "name": "Half day",
            "effect": (
                "The market closes at 13:00. MEIC does not move its times for it: entries to "
                f"{window_end}, closes from {eod}, settlement at {settle}."
            ),
            "depends_on": None,
        },
    ]


def value_gates(params: dict) -> list[dict]:
    """The arm's gates that read a value at the time, which no calendar answers ahead. A gate set
    where it cannot bind (an arm turns one off with 0 or 999) is left out."""
    out = []
    iv = params.get("min_iv_rank", 0.3)
    if iv and iv > 0:
        out.append(
            {
                "name": "IV rank",
                "effect": f"No entries below an IV rank of {_pct(iv, 0)}.",
                "depends_on": "IV rank at the time",
            }
        )
    vix = params.get("regime_vix_pause_threshold", 25)
    if vix is not None and vix < OFF_VIX:
        out.append(
            {
                "name": "VIX",
                "effect": f"No entries while VIX is above {vix:g}.",
                "depends_on": "VIX at the time",
            }
        )
    ratio = params.get("regime_vix1d_ratio_pause_threshold", 1.30)
    if ratio is not None and ratio < OFF_RATIO:
        out.append(
            {
                "name": "VIX1D ratio",
                "effect": f"No entries while VIX1D over VIX is above {ratio:.2f}.",
                "depends_on": "VIX1D and VIX at the time",
            }
        )
    atr = params.get("regime_atr_pause_threshold_pct")
    if atr and atr < 1:
        out.append(
            {
                "name": "ATR",
                "effect": f"No entries while the 5-day ATR is above {_pct(atr)} of the price.",
                "depends_on": "the last five sessions' ranges",
            }
        )
    if params.get("regime_gex_block_negative", True):
        out.append(
            {
                "name": "GEX",
                "effect": "No entries while net GEX is negative.",
                "depends_on": "dealer gamma at the time",
            }
        )
    if params.get("stabilization_entry"):
        out.append(
            {
                "name": "Stabilization",
                "effect": "Entries wait for the last two five-minute bars to read stable.",
                "depends_on": "the bars at the time",
            }
        )
    return out


def ahead(params: dict, base: dict, start, session_nets=None) -> dict:
    """`core.ahead.sessions_ahead` over this arm's gates and the shared calendar."""
    return core_ahead.sessions_ahead(
        start,
        date_gates(params, base),
        core_ahead.calendar_on,
        value_gates=value_gates(params),
        session_nets=session_nets,
    )
