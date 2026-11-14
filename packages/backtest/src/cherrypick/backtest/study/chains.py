"""One expiry's end-of-day chain: quotes by right and strike, its parity forward and deltas.

Copied from item 59's `Chain` (which items 63, 64 and 74 load), with #1062's guard: a chain with no
parity forward offers no strike. Declared limits it carries into every study that uses it: deltas are
Black-76 at r = 0 off each leg's mid and the chain's own parity forward, and a quote counts only when
it is two-sided (a bid above zero, an ask at or above it).
"""

from __future__ import annotations

import statistics

from cherrypick.core import bs, legbars

from .calendars import days_between

#: Below this implied volatility a mid is taken as unpriced (item 59's floor).
MIN_IV = 0.0002


def _mid(q: dict) -> float:
    return (q["bid"] + q["ask"]) / 2


def parity_forward(calls: dict, puts: dict) -> float | None:
    """r = 0 parity: the median of K + C_mid - P_mid over the three strikes nearest the strike where
    the call's and put's mids are closest, every one two-sided both sides. None without one."""
    both = [k for k in calls if k in puts and legbars.two_sided(calls[k]) and legbars.two_sided(puts[k])]
    if not both:
        return None
    seed = min(both, key=lambda k: (abs(_mid(calls[k]) - _mid(puts[k])), k))
    near = sorted(both, key=lambda k: (abs(k - seed), k))[:3]
    return statistics.median(k + _mid(calls[k]) - _mid(puts[k]) for k in near)


class Chain:
    """One end-of-day chain for one expiry. `rows` are (strike, right, bid, ask), the right spelt
    C/P or CALL/PUT in any case (the end-of-day store spells it CALL/PUT)."""

    def __init__(self, rows, session: str, expiry: str):
        self.session, self.expiry = session, expiry
        self.years = max(days_between(session, expiry), 1) / 365.0
        self.by: dict[str, dict[float, dict]] = {"C": {}, "P": {}}
        for k, r, b, a in rows:
            r = "C" if str(r).upper() in ("C", "CALL") else "P"
            self.by[r][float(k)] = {"strike": float(k), "right": r, "bid": b, "ask": a}
        self.forward = parity_forward(self.by["C"], self.by["P"])

    def iv(self, q: dict) -> float | None:
        """The leg's Black-76 implied volatility at r = 0 off its mid, or None (no forward, a
        one-sided quote, or a mid below `MIN_IV`)."""
        if not self.forward or not legbars.two_sided(q):
            return None
        iv = bs.implied_vol(self.forward, q["strike"], self.years, _mid(q), q["right"], 0.0)
        return iv if iv and iv > MIN_IV else None

    def delta(self, q: dict) -> float | None:
        iv = self.iv(q)
        if iv is None:
            return None
        return bs.delta(self.forward, q["strike"], self.years, iv, q["right"], 0.0)

    def otm(self, right: str) -> list[dict]:
        """Out-of-the-money quotes on `right`, nearest the money first. Empty without a forward."""
        if not self.forward:
            return []
        if right == "P":
            ks = sorted((k for k in self.by["P"] if k < self.forward), reverse=True)
        else:
            ks = sorted(k for k in self.by["C"] if k > self.forward)
        return [self.by[right][k] for k in ks]

    def nearest(self, right: str, target: float, beyond: float | None = None) -> dict | None:
        """The OTM strike on `right` whose |delta| is nearest `target`, strictly beyond `beyond`
        (below it for a put, above it for a call); the nearer the money on a tie."""
        best = None
        for q in self.otm(right):
            if beyond is not None and (q["strike"] >= beyond if right == "P" else q["strike"] <= beyond):
                continue
            d = self.delta(q)
            if d is None:
                continue
            gap = abs(abs(d) - target)
            if best is None or gap < best[0]:
                best = (gap, q)
        return None if best is None else best[1]
