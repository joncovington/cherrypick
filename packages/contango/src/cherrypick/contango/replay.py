"""The switch replayed over daily closes -- the research this module was built from, rerun through
the loop's own rule (`regime.target_state`), so the replay and the paper loop cannot disagree about
what the rule is.

Pure: the caller passes the series (`scripts/contango_replay.py` reads them from the technicals
store and Cboe's index files). Each session's ratio is read at its close and the holding changes at
that close, as the loop's ten-minutes-before-the-close decision approximates; the held fund's
close-to-close total return is earned until the next switch. A switch costs `cost_bps` of NAV a
side, two sides per switch.
"""

from __future__ import annotations

import math
from datetime import date

from cherrypick.contango import regime


def run(
    days: list[str],
    ratio: dict[str, float],
    risk: dict[str, float],
    cash: dict[str, float],
    params: dict,
    *,
    cost_bps: float = 2.0,
) -> dict:
    """`{"navs": [(day, nav)], "switches", "days_in_risk", "stats"}` for one arm's params.
    `risk`/`cash` are total-return closes; a day missing any input is skipped, not filled."""
    usable = [d for d in days if d in ratio and d in risk and d in cash]
    nav, state, navs, switches, in_risk = 1.0, None, [], 0, 0
    for i, d in enumerate(usable):
        if i:
            prev = usable[i - 1]
            series = risk if state == regime.RISK else cash
            nav *= series[d] / series[prev]
            in_risk += state == regime.RISK
        target = regime.target_state(ratio[d], state, params)
        if target != state:
            nav *= 1 - (cost_bps / 10_000) * (2 if state is not None else 1)
            switches += state is not None
            state = target
        navs.append((d, nav))
    return {"navs": navs, "switches": switches, "days_in_risk": in_risk, "stats": stats(navs)}


def stats(navs: list[tuple[str, float]]) -> dict:
    if len(navs) < 2:
        return {}
    vals = [v for _, v in navs]
    years = (date.fromisoformat(navs[-1][0]) - date.fromisoformat(navs[0][0])).days / 365.25
    rets = [vals[i] / vals[i - 1] - 1 for i in range(1, len(vals))]
    peak, mdd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    mean = sum(rets) / len(rets)
    vol = math.sqrt(sum((r - mean) ** 2 for r in rets) / len(rets)) * math.sqrt(252)
    cagr = vals[-1] ** (1 / years) - 1 if years > 0 and vals[-1] > 0 else None
    return {
        "start": navs[0][0],
        "end": navs[-1][0],
        "cagr": cagr,
        "vol": vol,
        "max_drawdown": mdd,
        "worst_day": min(rets),
        "final_nav": vals[-1],
    }
