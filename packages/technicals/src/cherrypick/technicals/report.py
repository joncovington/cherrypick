"""One session's market-report readings, written once so every surface reads the same artifact.

Phase 7's source for the console: the stage table by sector, the breadth history, the rotation
states, the scan-rule signals and the relative-strength leaders -- every one computed by this
package's own engines, gathered here and written to
`~/.cherrypick/data/technicals/report-<session>.json`. Nothing downstream recomputes any of it, the
same posture as overview's fact pack: a surface that re-derived a stage would be a second opinion
waiting to drift from the one the pack was written against.

Record-only, like every market-report reading: it feeds no gate, no sizing and no order.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from . import levels, paths, rotation, signals, stage, store, symbols, trend

REPORT_VERSION = 1
BREADTH_SESSIONS = 10
TOP_LEADERS = 10


def _sectors() -> dict[str, str]:
    try:
        doc = json.loads(
            (paths.market_report_dir() / "universe" / "sectors.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return {}
    return {s: row.get("sector") for s, row in (doc.get("sectors") or {}).items() if row.get("sector")}


def build(session: str | None = None, conn=None) -> dict[str, Any]:
    own = conn is None
    conn = conn or store.connect()
    bench = {b.date: b.close for b in store.adjusted_bars(conn, stage.DEFAULT_RULE.benchmark)}
    if not bench:
        return {"ok": False, "reason": "no benchmark bars in the store"}
    day = session or max(bench)
    stocks = store.stocks(conn, symbols.candidates())
    bars = {s: [b for b in store.adjusted_bars(conn, s) if b.date <= day] for s in stocks}
    bars = {s: b for s, b in bars.items() if b}
    closes = {s: {b.date: b.close for b in bs} for s, bs in bars.items()}

    # Breadth, and the stage table for the session itself.
    history = []
    for d in [x for x in sorted(bench) if x <= day][-BREADTH_SESSIONS:]:
        st = stage.stages_on(d, closes, bench)
        lead = sum(v.side == "leader" for v in st.values())
        lag = sum(v.side == "laggard" for v in st.values())
        history.append(
            {"session": d, "leaders": lead, "laggards": lag, "net": lead - lag,
             "bullish_share": round(lead / (lead + lag), 3) if lead + lag else None}
        )  # fmt: skip
    today = stage.stages_on(day, closes, bench)
    sector_of = _sectors()
    by_sector: dict[str, dict[str, list]] = {}
    for sym, v in sorted(today.items()):
        row = by_sector.setdefault(sector_of.get(sym, "Unassigned"), {"leaders": [], "laggards": []})
        row["leaders" if v.side == "leader" else "laggards"].append({"symbol": sym, "stage": v.stage})
    stages = [
        {"sector": sec, "net": len(r["leaders"]) - len(r["laggards"]), **r}
        for sec, r in sorted(
            by_sector.items(), key=lambda kv: -(len(kv[1]["leaders"]) - len(kv[1]["laggards"]))
        )
    ]

    # Rotation.
    rrule = rotation.DEFAULT_RULE
    funds = sorted({*symbols.ROTATION_ETFS, rrule.benchmark, rrule.asset_benchmark})
    fund_closes = {s: {b.date: b.close for b in store.adjusted_bars(conn, s) if b.date <= day} for s in funds}
    states = rotation.states_on(day, fund_closes, symbols.ASSET_ETFS, rrule)
    rot = {st: sorted(f for f, v in states.items() if v == st) for st in rotation.STATES}
    rot["none"] = sorted(f for f, v in states.items() if v is None)

    # Signals and the relative-strength leaders.
    sig: dict[str, list[str]] = {r: [] for r in signals.RULES}
    returns = {}
    for sym, bs in bars.items():
        if bs[-1].date != day:
            continue
        r = signals.readings([b.high for b in bs], [b.low for b in bs], [b.close for b in bs])
        if r is not None:
            for rule in signals.matches(r):
                sig[rule].append(sym)
        if len(bs) > levels.RANK_SESSIONS:
            returns[sym] = bs[-1].close / bs[-1 - levels.RANK_SESSIONS].close - 1
    ranks = levels.rank(returns)
    top = sorted(returns, key=lambda s: -returns[s])[:TOP_LEADERS]
    leaders = []
    for sym in top:
        c = [b.close for b in bars[sym]]
        leaders.append(
            {
                "symbol": sym,
                "sector": sector_of.get(sym),
                "rank": ranks[sym],
                "return_6m_pct": round(100 * returns[sym], 1),
                "trend_short": trend.label(trend.scores(c, trend.SHORT_TERM)[-1]),
                "trend_long": trend.label(trend.scores(c, trend.LONG_TERM)[-1]),
                "stage": f"{today[sym].side}/{today[sym].stage}" if sym in today else None,
            }
        )
    if own:
        conn.close()
    return {
        "ok": True,
        "report_version": REPORT_VERSION,
        "session": day,
        "generated_at": datetime.now(UTC).isoformat(),
        "universe": len(bars),
        "rules": {"stage": stage.DEFAULT_RULE.name, "rotation": rrule.name},
        "breadth": history,
        "stages": stages,
        "rotation": rot,
        "signals": sig,
        "leaders": leaders,
        "record_only": True,
    }


def write(doc: dict) -> str:
    path = paths.data_dir() / f"report-{doc['session']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    tmp.replace(path)  # write-then-rename: a reader never sees half a report
    return str(path)
