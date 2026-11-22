"""Today's market conditions in one automation's record (`cli conditions`). Pure.

Each closed trade's entry session is labelled by the daily-bar market regime of its own underlying
(`core.marketregime`: trend and volatility, each session's label from the bars strictly before it, so
no label knows the session it describes). The trades are cut by those labels and the document is
assembled by `core.regimecuts`, which stamps every cell's `thin` and `fragile` the way it does for
MEIC's and flies' cuts, then `stamp_today` sets today's label against each cut: the automation's
record in today's kind of market, readable only where the dimension has enough sessions and the cell
is not thin.

The automations ledger records no regime at entry, so this is the one place an automation's trades
meet a market label, and it is derived each time, never stored. It is context, not a forecast and
not a verdict.
"""

from __future__ import annotations

from cherrypick.core import marketregime
from cherrypick.core import regimecuts as rc
from cherrypick.core.metrics import MIN_EFFECTIVE_N

#: The market dimensions an automation is cut by, each a key of a `marketregime.classify` label.
DIMENSIONS = ("trend", "vol")
UNTAGGED = "untagged"


def labels(bars: list[dict], today: str) -> tuple[dict[str, dict], dict]:
    """`({session: label}, today's label)` from one symbol's daily bars (every bar before `today`).

    Each past session's label is from the bars before it (`marketregime.series`); today's is from
    every bar given, all of which precede today. A label is `{trend, vol}`; an unmeasured today is
    `{"status": "unmeasured", "reason": ...}`."""
    past = {row["session"]: {d: row[d] for d in DIMENSIONS} for row in marketregime.series(bars)}
    now = marketregime.classify(bars) if bars else {"status": "unmeasured", "reason": "no daily bars"}
    if now.get("status") == "measured":
        past[today] = {d: now[d] for d in DIMENSIONS}
    return past, now


def _summary(rows: list[dict]) -> dict:
    """A cell's figures over closed trades (`net_pnl`, `gross_pnl` and `cost` as the ledger states
    them, so gross less cost is net), counted by entry session."""
    nets = [float(r.get("net_pnl") or 0.0) for r in rows]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]
    gross = sum(float(r.get("gross_pnl") or 0.0) for r in rows)
    cost = sum(float(r.get("cost") or 0.0) for r in rows)
    won, lost = sum(wins), abs(sum(losses))
    return {
        "trades": len(rows),
        "sessions": len({r["entry_session"] for r in rows if r.get("entry_session")}),
        "gross_pnl": round(gross, 2),
        "fees": round(cost, 2),
        "net_pnl": round(sum(nets), 2),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / (len(wins) + len(losses)), 4) if wins or losses else None,
        "avg_pnl": round(sum(nets) / len(nets), 2) if nets else None,
        "avg_win": round(won / len(wins), 2) if wins else None,
        "avg_loss": round(-lost / len(losses), 2) if losses else None,
        "fee_drag_pct": round(cost / gross * 100, 2) if gross > 0 else None,
        "profit_factor": round(won / lost, 2) if lost > 0 else None,
    }


def _coverage(tags: list[str], rows: list[dict]) -> dict:
    """One dimension's coverage in `regime_coverage`'s shape: tagged, untagged, sessions (the
    entry sessions tagged: a daily label repeats across a session's trades), `degenerate` (one
    bucket only) and `underpowered` (under `MIN_EFFECTIVE_N` sessions)."""
    tagged = [r for t, r in zip(tags, rows, strict=True) if t != UNTAGGED]
    sessions = len({r["entry_session"] for r in tagged})
    kinds = {t for t in tags if t != UNTAGGED}
    return {
        "tagged": len(tagged),
        "untagged": len(rows) - len(tagged),
        "coverage_pct": round(len(tagged) / len(rows) * 100, 2) if rows else None,
        "sessions": sessions,
        "daily_scale": True,
        "effective_n": sessions,
        "degenerate": bool(tagged) and len(kinds) == 1,
        "underpowered": bool(tagged) and sessions < MIN_EFFECTIVE_N,
        "sessions_needed": max(0, MIN_EFFECTIVE_N - sessions) if tagged else None,
    }


def cut(
    name: str,
    symbol: str | None,
    trades: list[dict],
    by_symbol: dict[str, dict[str, dict]],
    today: dict[str, dict],
    session: str,
    era_start: str | None,
    generated_at: str,
) -> dict:
    """The automation's conditions document.

    `trades` are its closed trades (`entry_session`, `symbol`, `net_pnl`, `gross_pnl`, `cost`);
    `by_symbol` each symbol's `{session: {trend, vol}}` labels; `today` each symbol's label for
    `session` (`labels`' second value). A trade whose entry session has no label is `untagged`."""
    rows = [
        t for t in trades if t.get("entry_session") and (era_start is None or t["entry_session"] >= era_start)
    ]
    regimes: dict[str, list[dict]] = {}
    coverage: dict[str, dict] = {}
    for dim in DIMENSIONS:
        tags = [
            ((by_symbol.get(str(t.get("symbol") or "")) or {}).get(t["entry_session"]) or {}).get(dim)
            or UNTAGGED
            for t in rows
        ]
        grouped: dict[str, list[dict]] = {}
        for tag, t in zip(tags, rows, strict=True):
            grouped.setdefault(tag, []).append(t)
        regimes[dim] = [
            {
                "bucket": tag,
                **_summary(rs),
                "session_nets": rc.session_totals((r["entry_session"], r.get("net_pnl")) for r in rs),
            }
            for tag, rs in grouped.items()
        ]
        coverage[dim] = _coverage(tags, rows)
    era = {"start": era_start, "bounding_break": None, "binding": [], "ignored_future": [], "caveats": []}
    doc = rc.assemble(
        module="automations",
        session=session,
        symbol=symbol,
        arm_column="arm",
        entry_modes=None,
        phase="entry",
        era=era,
        arms=[
            {
                "arm": name,
                "era_start": era_start,
                "era_break": None,
                "summary": _summary(rows),
                "coverage": coverage,
                "regimes": regimes,
            }
        ],
        cross_tabs=[],
        generated_at=generated_at,
        min_effective_n=MIN_EFFECTIVE_N,
    )
    measured = {s: {d: v[d] for d in DIMENSIONS} for s, v in today.items() if v.get("status") == "measured"}
    rc.stamp_today(doc, {"at": session, "symbols": measured})
    doc["today"]["labels"] = {
        s: (
            {
                **{d: v.get(d) for d in (*DIMENSIONS, "atr_percentile", "sma_slope")},
                "through": v.get("session"),
            }
            if v.get("status") == "measured"
            else {"status": "unmeasured", "reason": v.get("reason")}
        )
        for s, v in today.items()
    }
    return doc
