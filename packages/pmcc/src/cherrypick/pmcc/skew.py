"""The skew sampler: once a session per symbol, what the market charged where the replay can only
model (2026-10-04).

`scripts/pmcc_shield_replay.py` prices every option off a vol index with one skew term, `b`,
borrowed from 21-DTE calls and flat past 30 days. Its most sensitive inputs sit exactly there:
- the shield's 0.70-delta weekly short sits on the skew (XSP's `shield_hold` alpha runs from -0.9
  to +5.7 a year as `b` goes from 0 to 0.40);
- the put benchmark's 20-delta year put sits on the year-long skew, which nothing has calibrated.

So the loop records, once a session per symbol from `sample_offset` minutes before the close:
- at the weekly short's expiry: the ATM call and the call nearest 0.70 delta;
- at the held-long arms' year expiry: the ATM call and the puts nearest 0.30, 0.20 and 0.10 delta.

The replay's `--skew-check` reads them back against the model.

Telemetry only: nothing here decides a trade. Pure apart from the clock's calendar.
"""

from __future__ import annotations

from datetime import date

from cherrypick.pmcc import clock

DEFAULTS = {"enabled": False, "sample_offset": 60}

# (target, tenor, right, |delta| or None for the strike nearest spot)
TARGETS = (
    ("week_atm_call", "week", "call", None),
    ("week_call_70", "week", "call", 0.70),
    ("year_atm_call", "year", "call", None),
    ("year_put_30", "year", "put", 0.30),
    ("year_put_20", "year", "put", 0.20),
    ("year_put_10", "year", "put", 0.10),
)


def settings(config: dict) -> dict:
    return {**DEFAULTS, **(config.get("skew_samples") or {})}


def dates(listed: list[str], today: date) -> dict | None:
    """`{"week", "year"}`: the expirations an entry today would use -- `clock.short_expiration` and
    `clock.leap_expiration` at their defaults -- or None when the listing has no year-long date."""
    year = clock.leap_expiration(listed, today)
    week = clock.short_expiration(today)
    if year is None or week is None:
        return None
    return {"week": week["short_expiration"], "year": year["long_expiration"]}


def select(snapshot: dict, today: date) -> list[dict]:
    """One row per target from `provider.skew_snapshot`'s entries. A target with nothing to pick is
    still a row, with its refusal, so a missing day reads as missing rather than as quiet. A delta
    target picks only among strikes with a delta on file; the ATM targets take the strike nearest
    spot whatever is on file for it."""
    spot = snapshot["spot"]
    rows = []
    for target, tenor, right, want in TARGETS:
        exp = snapshot["dates"][tenor]
        entries = [e for e in snapshot["entries"].get(exp, []) if e["option_type"] == right]
        if want is None:
            pick = min(entries, key=lambda e: (abs(e["strike"] - spot), -e["strike"]), default=None)
        else:
            greeked = [e for e in entries if e["delta"] is not None]
            pick = min(greeked, key=lambda e: (abs(abs(e["delta"]) - want), e["strike"]), default=None)
        row = {
            "target": target,
            "expiration": exp,
            "dte": (date.fromisoformat(exp) - today).days,
            "option_type": right,
        }
        if pick is None:
            row.update(usable=0, refusal="no_greeks" if entries else "no_quotes")
        else:
            row.update(
                {k: pick[k] for k in ("strike", "delta", "iv", "bid", "ask", "mid", "quote_age_seconds")}
            )
            row.update(usable=int(bool(pick["usable"])), refusal=pick["refusal"])
        rows.append(row)
    return rows
