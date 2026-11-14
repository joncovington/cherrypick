"""Reads over the ledger: each arm's closed trades, net and drawdown, and the arms together.

Pure over rows. Net is net at the suite's slippage model, the way the ledger stores it; the stress and mid
nets are context. Drawdown is the largest fall of the running net from its peak, over closed trades in the
order they ended (the exit session, then the entry session), in dollars. The combined line stacks every
arm as sized.
"""

from __future__ import annotations


def drawdown(nets: list[float]) -> float:
    """The largest peak-to-trough fall of the running sum, as a positive number (0 when it never falls)."""
    peak = run = worst = 0.0
    for n in nets:
        run += n
        peak = max(peak, run)
        worst = max(worst, peak - run)
    return round(worst, 2)


def _line(rows: list[dict]) -> dict:
    ordered = sorted(rows, key=lambda r: (r["exit_session"] or "", r["entry_session"], r["arm"]))
    nets = [r["net_pnl"] for r in ordered]
    n = len(nets)
    return {
        "trades": n,
        "net": round(sum(nets), 2),
        "gross": round(sum(r["gross_pnl"] for r in ordered), 2),
        "fees": round(sum(r["fees"] for r in ordered), 2),
        "settlement": round(sum(r["settlement"] for r in ordered), 2),
        "slippage": round(sum(r["slippage"] for r in ordered), 2),
        "net_stress": round(sum(r["net_stress"] for r in ordered), 2),
        "net_mid": round(sum(r["net_mid"] for r in ordered), 2),
        "wins": sum(1 for x in nets if x > 0),
        "mean": round(sum(nets) / n, 2) if n else None,
        "worst": min(nets) if nets else None,
        "drawdown": drawdown(nets),
    }


def closed(conn) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM ensemble_positions WHERE status = 'closed' AND net_pnl IS NOT NULL "
            "ORDER BY exit_session, entry_session, arm"
        )
    ]


def summary(conn) -> dict:
    """{"arms": {arm: line}, "combined": line} over every closed position."""
    rows = closed(conn)
    by_arm: dict[str, list[dict]] = {}
    for r in rows:
        by_arm.setdefault(r["arm"], []).append(r)
    return {"arms": {a: _line(rs) for a, rs in sorted(by_arm.items())}, "combined": _line(rows)}
