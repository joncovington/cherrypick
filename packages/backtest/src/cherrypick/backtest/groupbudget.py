"""A group's budget read from its members' pinned backtest runs: a max risk that would have changed nothing
the runs did, each member's share of it, and the worst the group fell, in dollars. Pure over the runs'
trades; the console shows it beside the group's budget as a suggestion a person may take.

Each member's trades come from its own pinned Builder run (`automation_backtest.py`'s result), each trade
carrying its `risk` (the structure's maximum loss at the mid, whole-position dollars, the measure a group's
cap reads), its entry `session`, its `exit_session` and its net at the verdict fill.

- **Laid over the same sessions.** The members' runs are read only where every run covers the session
  (`overlap`: the latest first session to the earliest last one); a session one run did not cover would
  read that member as holding nothing. No common session is no suggestion, with why.
- **Risk at once** on a session is the sum of `risk` over the trades open on it, from the session each
  entered through the session it exited (the paper ledger's group reading, `groupread.peak_risk`, at the
  same grain: an upper bound, since two trades closed and opened inside one session count together).
- **Max risk** is the most the members held at once over the overlap, rounded up to `ROUND` dollars: a cap
  that would have refused nothing the runs entered.
- **Each member's share** is its own most at once as a percent of that max risk, rounded up to a tenth. If
  the shares add to more than 100 the members' peaks fell on different sessions and they competed for the
  room: each is scaled down to fit and `cut` counts, per member, the sessions its own risk would have gone
  over its scaled slice.
- **Drawdown** is the largest peak-to-trough fall of the members' net added by exit session, in dollars:
  what an allocation would have had to absorb.

A run that is licensed makes the whole reading licensed: the caller withholds it while licensed results
are not shown. Nothing here writes; the figures are a suggestion, never applied by themselves.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

#: The max risk is rounded up to this many dollars.
ROUND = 50.0


def _sessions(trades: Sequence[Mapping]) -> list[str]:
    days = {str(t["session"]) for t in trades if t.get("session")}
    days |= {str(t["exit_session"]) for t in trades if t.get("exit_session")}
    return sorted(days)


def open_risk(trades: Sequence[Mapping], days: Sequence[str]) -> dict[str, float]:
    """{session: the risk of the trades open on it} over `days`."""
    out = {}
    for d in days:
        out[d] = sum(
            float(t.get("risk") or 0.0)
            for t in trades
            if str(t.get("session") or "") <= d <= str(t.get("exit_session") or t.get("session") or "")
        )
    return out


def _span(trades: Sequence[Mapping], span: Sequence[str] | None) -> tuple[str, str] | None:
    """A run's first and last session: its declared span when it gives one, else its trades' own."""
    if span and len(span) == 2 and all(isinstance(s, str) and s for s in span):
        return str(span[0]), str(span[1])
    days = _sessions(trades)
    return (days[0], days[-1]) if days else None


def read(members: Sequence[Mapping]) -> dict:
    """`members`: [{"name", "run", "trades", "span" ([first, last] or None), "licensed"}], one per member
    with a pinned run. Returns {"ok", "why"?, "overlap", "sessions", "max_risk", "shares", "scaled", "cut",
    "drawdown", "members", "licensed"} (module docstring)."""
    licensed = any(bool(m.get("licensed")) for m in members)
    spans = {m["name"]: _span(m.get("trades") or [], m.get("span")) for m in members}
    rows = [
        {"name": m["name"], "run": m["run"], "first": (spans[m["name"]] or (None, None))[0],
         "last": (spans[m["name"]] or (None, None))[1], "trades": len(m.get("trades") or [])}
        for m in members
    ]  # fmt: skip
    base = {"licensed": licensed, "members": rows}
    if not members:
        return {"ok": True, "why": "no member has a pinned run", **base}
    if any(s is None for s in spans.values()):
        empty = sorted(n for n, s in spans.items() if s is None)
        return {"ok": True, "why": f"{', '.join(empty)}: the run entered nothing", **base}
    first = max(s[0] for s in spans.values() if s)
    last = min(s[1] for s in spans.values() if s)
    if first > last:
        return {"ok": True, "why": "the runs share no session", **base}
    inside = {
        m["name"]: [t for t in m.get("trades") or [] if first <= str(t.get("session") or "") <= last]
        for m in members
    }
    days = sorted({d for ts in inside.values() for d in _sessions(ts) if first <= d <= last})
    if not days:
        return {
            "ok": True,
            "why": "no trade inside the sessions the runs share",
            "overlap": [first, last],
            **base,
        }
    each = {n: open_risk(ts, days) for n, ts in inside.items()}
    together = {d: sum(each[n][d] for n in each) for d in days}
    peak = max(together.values())
    if peak <= 0:
        return {"ok": True, "why": "the runs held no risk", "overlap": [first, last], **base}
    max_risk = math.ceil(peak / ROUND) * ROUND
    own = {n: max(r.values()) for n, r in each.items()}
    raw = {n: math.ceil(own[n] / max_risk * 1000) / 10 for n in own}
    total = sum(raw.values())
    scaled = total > 100 + 1e-9
    shares = {n: (math.floor(v * 100 / total * 10) / 10 if scaled else v) for n, v in raw.items()}
    cut = (
        {n: sum(1 for d in days if each[n][d] > max_risk * shares[n] / 100 + 1e-9) for n in each}
        if scaled
        else {}
    )
    nets: dict[str, float] = {}
    for ts in inside.values():
        for t in ts:
            d = str(t.get("exit_session") or t.get("session"))
            nets[d] = nets.get(d, 0.0) + float((t.get("net") or {}).get("verdict") or 0.0)
    run = high = worst = 0.0
    for d in sorted(nets):
        run += nets[d]
        high = max(high, run)
        worst = max(worst, high - run)
    for r in rows:
        r["peak"] = round(own[r["name"]], 2)
    return {
        "ok": True,
        "overlap": [first, last],
        "sessions": len(days),
        "max_risk": max_risk,
        "shares": shares,
        "scaled": scaled,
        "cut": {n: c for n, c in cut.items() if c},
        "drawdown": round(worst, 2),
        **base,
    }
