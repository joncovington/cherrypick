"""The stage lists: our six relative-strength stage lists, limited to names we would trade options
on -- the vendor's Early Breakout ... Early Breakdown watchlists, rebuilt so they outlive the
subscription (it ends 2026-10-24).

**From what.** The stage rule (`stage.py`, the one the editions score at 99% same side) over every
stock the store holds, on one session's close, against SPY. A list is one side and stage:

    Early Breakout             leader / early       Early Breakdown           laggard / early
    Building Outperformance    leader / building    Building Underperformance laggard / building
    Confirmed Outperformers    leader / confirmed   Confirmed Underperformers laggard / confirmed

Leaders are ordered by their one-month excess return, strongest first; laggards weakest first.

**Only options-tradable names** (the liquidity verdict, `liquidity.py`: weekly options and $100M a
day; a name it never judged is left out, never assumed). That is the difference from the vendor's,
whose lists are mostly names we would not trade: on 2026-10-16, 34 of its 98 Early Breakouts passed.
Early Breakdown is the list the user reads first: leaders that have outperformed for a while and are
rolling over (its names' median 1-10 score was 10 on that day).

**Scored while both exist.** The vendor's watchlists, saved nightly by the collector
(`market-report/vendor-watchlists/<session>.json`), carry no date of their own; each is filed under
the chart session of the run that saved it. So each of our sessions is compared with the vendor's
lists filed under that session AND the one before, on the vendor's names we hold bars for, all of
them (not only the tradable ones): whichever agrees better says which close the vendor's evening
lists describe. On the 2026-10-16 lists (captured the next day): 100% same side, 96% same stage
(99% and 76% with the excess rule before the 2026-10-27 refit).

    python -m cherrypick.technicals stage-lists [--session YYYY-MM-DD]
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cherrypick.core import jsonio as _jsonio

from . import liquidity, paths, stage, store

LISTS: tuple[tuple[str, str, str], ...] = (
    ("Early Breakout", "leader", "early"),
    ("Building Outperformance", "leader", "building"),
    ("Confirmed Outperformers", "leader", "confirmed"),
    ("Confirmed Underperformers", "laggard", "confirmed"),
    ("Building Underperformance", "laggard", "building"),
    ("Early Breakdown", "laggard", "early"),
)


def out_dir() -> Path:
    return paths.data_dir() / "stage-lists"


# ------------------------------------------------------------------------------------------------
# Pure.


def lists_from(
    stages: dict[str, stage.Stage], one_month: dict[str, float], tradable: set[str]
) -> dict[str, list[str]]:
    """{list name: symbols} from each name's stage and one-month excess return, tradable names only.
    Leaders strongest first, laggards weakest first; a tie by symbol."""
    out = {}
    for name, side, st in LISTS:
        names = [s for s, v in stages.items() if (v.side, v.stage) == (side, st) and s in tradable]
        sign = -1.0 if side == "leader" else 1.0
        out[name] = sorted(names, key=lambda s: (sign * one_month.get(s, 0.0), s))
    return out


def score(stages: dict[str, stage.Stage], held: set[str], vendor_lists: dict[str, list[str]]) -> dict:
    """Our stage for every name on the vendor's six lists that we hold bars for: same side and same
    stage, per list and in total."""
    per, total = {}, {"held": 0, "same_side": 0, "same_stage": 0}
    for name, side, st in LISTS:
        names = [s for s in vendor_lists.get(name) or [] if s in held]
        same_side = sum(1 for s in names if s in stages and stages[s].side == side)
        same = sum(1 for s in names if s in stages and (stages[s].side, stages[s].stage) == (side, st))
        per[name] = {"vendor": len(vendor_lists.get(name) or []), "held": len(names), "same_side": same_side,
                     "same_stage": same}  # fmt: skip
        total["held"] += len(names)
        total["same_side"] += same_side
        total["same_stage"] += same
    h = total["held"]
    total["side_rate"] = round(total["same_side"] / h, 3) if h else None
    total["stage_rate"] = round(total["same_stage"] / h, 3) if h else None
    return {"lists": per, "total": total}


# ------------------------------------------------------------------------------------------------
# Readers and the build.


def vendor_lists(session: str) -> dict[str, list[str]] | None:
    """The vendor's watchlists filed under `session`, {name: symbols}, or None."""
    path = paths.market_report_dir() / "vendor-watchlists" / f"{session}.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return {w["name"]: [x["symbol"] for x in w.get("symbols") or []] for w in doc.get("lists") or []}


def build(session: str | None = None) -> dict[str, Any]:
    """Our six lists on `session` (default the newest close the store holds), written to
    `stage-lists/<session>.json`, scored against the vendor's where saved."""
    conn = store.connect()
    try:
        rule = stage.DEFAULT_RULE
        bench = {b.date: b.close for b in store.adjusted_bars(conn, rule.benchmark)}
        if not bench:
            return {"ok": False, "reason": f"no {rule.benchmark} bars"}
        day = session or max(bench)
        held = [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM bars")]
        stocks = store.stocks(conn, held)
        closes = {s: {b.date: b.close for b in store.adjusted_bars(conn, s, day)} for s in stocks}
    finally:
        conn.close()
    closes = {s: c for s, c in closes.items() if c.get(day)}
    stages = stage.stages_on(day, closes, bench, rule)
    sessions = sorted(bench)
    one_month = {s: stage.excess(closes[s], bench, sessions, day, rule.windows[0]) or 0.0 for s in stages}
    tradable = {s for s, v in liquidity.load().items() if v.get("verdict") == liquidity.TRADABLE}
    lists = lists_from(stages, one_month, tradable)
    i = sessions.index(day) if day in sessions else None
    before = sessions[i - 1] if i else None
    against = {}
    for label, d in (("same_session", day), ("session_before", before)):
        v = vendor_lists(d) if d else None
        if v is not None:
            against[label] = {"vendor_session": d, **score(stages, set(closes), v)}
    doc = {
        "session": day,
        "built_at": datetime.now(UTC).isoformat(),
        "rule": rule.name,
        "benchmark": rule.benchmark,
        "filter": "liquidity verdict: tradable",
        "staged": len(stages),
        "lists": {
            name: [{"symbol": s, "one_month_excess": round(one_month[s], 4)} for s in syms]
            for name, syms in lists.items()
        },  # fmt: skip
        "against_vendor": against or None,
    }
    path = _jsonio.write_json_atomic(out_dir() / f"{day}.json", doc, indent=1, default=None)
    return {
        "ok": True,
        "session": day,
        "staged": len(stages),
        "lists": {name: len(syms) for name, syms in lists.items()},
        "against_vendor": {
            k: v["total"] | {"vendor_session": v["vendor_session"]} for k, v in against.items()
        },
        "file": str(path),
    }


def latest() -> dict | None:
    """The newest stage-lists file, or None."""
    files = sorted(out_dir().glob("????-??-??.json"))
    if not files:
        return None
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
