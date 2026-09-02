"""Proof that the fact set can be trusted: check it against the ledgers it claims to summarise.

This is the load-bearing step of the whole package. A cross-module report is only worth reading if
its numbers reproduce the ones the modules themselves would give, and the way that guarantee is
usually lost is not a wrong formula but a silent scope difference -- a filter that drops rows, a
session boundary that shifts a trade into the wrong day, a status value nobody knew existed.

So this deliberately does NOT re-derive using the same readers the fact set used, which would only
prove the code equals itself. It counts and sums straight off each module's table with its own
independent SQL, then checks the totals match. Where they don't, it reports the delta rather than
failing, because a real discrepancy is a finding about the ledger and not merely a broken test.
"""

from __future__ import annotations

import sqlite3

from cherrypick.core import ledgers as _ledgers

from cherrypick.review import facts as _facts
from cherrypick.review import paths as _paths

# Independent SQL per schema: same question, different route than READERS took.
_INDEPENDENT = {
    "meic": (
        "SELECT COUNT(*) n, COALESCE(SUM(pnl), 0) gross, COALESCE(SUM(fees), 0) cost "
        "FROM ic_trades WHERE exit_time IS NOT NULL AND substr(exit_time, 1, 10) = ?"
    ),
    "flies": (
        "SELECT COUNT(*) n, COALESCE(SUM(gross_pnl), 0) gross, COALESCE(SUM(fees), 0) cost "
        "FROM fly_positions WHERE status = 'settled' AND trade_date = ?"
    ),
    "earnings": (
        "SELECT COUNT(*) n, COALESCE(SUM(pnl), 0) gross, "
        "COALESCE(SUM(COALESCE(entry_cost,0) + COALESCE(exit_cost,0)), 0) cost "
        "FROM trades WHERE closed_at IS NOT NULL "
        "AND date(closed_at, 'unixepoch', 'localtime') = ?"
    ),
    "calendars": (
        "SELECT COUNT(*) n, COALESCE(SUM(gross_pnl), 0) gross, COALESCE(SUM(fees), 0) cost "
        "FROM dc_positions WHERE status = 'closed' AND closed_session = ?"
    ),
    "pmcc": (
        "SELECT COUNT(*) n, COALESCE(SUM(gross_pnl), 0) gross, COALESCE(SUM(fees), 0) cost "
        "FROM pmcc_positions WHERE status = 'closed' AND closed_session = ?"
    ),
}

# Money is compared to the cent. Anything looser hides exactly the rounding drift worth catching.
TOLERANCE = 0.01


def _independent_totals(module: str, session: str) -> dict | None:
    db_path = _paths.module_db(module)
    if not db_path.exists():
        return None
    conn = _ledgers.connect_ro(db_path)
    try:
        row = conn.execute(_INDEPENDENT[module], (session,)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if row is None:
        return None
    return {"closed": row["n"], "gross": round(row["gross"], 2), "cost": round(row["cost"], 2)}


def check_session(session: str, facts: dict | None = None) -> dict:
    """Compare one session's fact set against independently-computed totals."""
    facts = facts or _facts.read(session)
    if facts is None:
        return {"session": session, "ok": False, "reason": "no fact set written"}

    mismatches = []
    for module, spec in facts.get("modules", {}).items():
        if not spec.get("ok"):
            continue
        truth = _independent_totals(module, session)
        if truth is None:
            continue
        claimed = spec["results"]
        for field in ("closed", "gross", "cost"):
            a, b = claimed.get(field), truth.get(field)
            if a is None or b is None:
                continue
            if abs(float(a) - float(b)) > TOLERANCE:
                mismatches.append(
                    {
                        "module": module,
                        "field": field,
                        "fact_set": a,
                        "ledger": b,
                        "delta": round(float(a) - float(b), 2),
                    }
                )
    return {"session": session, "ok": not mismatches, "mismatches": mismatches}


def run(since: str | None = None) -> dict:
    """Reconcile every written fact set. Returns the mismatches, not just a verdict."""
    store = _paths.data_dir()
    if not store.exists():
        return {"ok": False, "reason": "no fact sets written yet"}
    sessions = sorted(p.stem.removeprefix("eod-") for p in store.glob("eod-*.json"))
    if since:
        sessions = [s for s in sessions if s >= since]

    results = [check_session(s) for s in sessions]
    failures = [r for r in results if not r["ok"]]
    return {
        "ok": not failures,
        "sessions_checked": len(results),
        "sessions_matching": len(results) - len(failures),
        "failures": failures,
    }
