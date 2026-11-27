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
        "FROM fly_positions WHERE status = 'settled' AND trade_date = ?{void}"
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
    "bwb": (
        "SELECT COUNT(*) n, COALESCE(SUM(gross_pnl), 0) gross, COALESCE(SUM(fees), 0) cost "
        "FROM bwb_positions WHERE status = 'closed' AND closed_session = ?"
    ),
    "curve": (
        "SELECT COUNT(*) n, COALESCE(SUM(gross_pnl), 0) gross, COALESCE(SUM(fees), 0) cost "
        "FROM curve_positions WHERE status = 'closed' AND closed_session = ?"
    ),
    "contango": (
        "SELECT COUNT(*) n, COALESCE(SUM(gross_pnl), 0) gross, "
        "COALESCE(SUM(COALESCE(fees, 0) + COALESCE(slippage, 0)), 0) cost "
        "FROM contango_positions WHERE status = 'closed' AND exit_session = ?"
    ),
    "weeklyput": (
        "SELECT COUNT(*) n, COALESCE(SUM(gross_pnl), 0) gross, "
        "COALESCE(SUM(COALESCE(fees, 0) + COALESCE(settlement, 0) + COALESCE(slippage, 0)), 0) cost "
        "FROM weeklyput_positions WHERE status = 'closed' AND settle_session = ?"
    ),
    "automations": (
        "SELECT COUNT(*) n, COALESCE(SUM(gross_pnl), 0) gross, "
        "COALESCE(SUM(COALESCE(fees, 0) + COALESCE(settlement, 0) + COALESCE(slippage, 0)), 0) cost "
        "FROM automations_positions WHERE status = 'closed' AND exit_session = ?"
    ),
    "ladder": (
        "SELECT COUNT(*) n, COALESCE(SUM(gross_pnl), 0) gross, "
        "COALESCE(SUM(COALESCE(fees, 0) + COALESCE(settlement, 0) + COALESCE(slippage, 0)), 0) cost "
        "FROM ladder_positions WHERE status = 'closed' AND sell_session = ?"
    ),
}

# Rows a module has voided: still `settled` with their P&L, but not evidence, so the fact set leaves
# them out (flies stamps `void_reason`). Applied only where the ledger has the column -- an older
# ledger has nothing voided -- and phrased here rather than borrowed from the reader, so it stays an
# independent route to the same scope.
_VOID = {"flies": ("fly_positions", "void_reason", " AND void_reason IS NULL")}

# Money is compared to the cent. Anything looser hides exactly the rounding drift worth catching.
TOLERANCE = 0.01


def _independent_totals(module: str, session: str) -> dict | None:
    if module not in _INDEPENDENT:
        # A module the fact set covers but this check has no independent route for yet: unchecked,
        # never a crash that hides every other module's reconciliation.
        return None
    db_path = _paths.module_db(module)
    if not db_path.exists():
        return None
    conn = _ledgers.connect_ro(db_path)
    try:
        void = ""
        if module in _VOID:
            table, column, clause = _VOID[module]
            if column in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
                void = clause
        row = conn.execute(_INDEPENDENT[module].replace("{void}", void), (session,)).fetchone()
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


#: The most sets one pass rebuilds. A real correction touches a few sessions; a count past this is a
#: scope bug in the check or the fact set, which a rewrite of every file cannot fix.
REBUILD_LIMIT = 30


def rebuild_session(session: str) -> None:
    """Rebuild one session's final set and its render, as `build --session <day> --final` does. The
    stored `carried_overnight` is kept (`facts._keep_carried_overnight`), so a rebuild cannot wipe it."""
    from cherrypick.review import render as _render

    _facts.write(_facts.build(session, status=_facts.STATUS_FINAL))
    _render.write(session)


def run(since: str | None = None, today: str | None = None, rebuild: bool = False) -> dict:
    """Reconcile every written fact set. Returns the mismatches, not just a verdict.

    A session still trading is left out and named in `skipped`. Its provisional fact set is a
    snapshot (the status digest refreshes it hourly from 10:00 ET) and the ledger keeps moving, so
    the two disagree whenever a trade closes between the snapshot and the 10:15 final pass -- on
    2026-10-14 two earnings closes did, and the suite was told its review was wrong.

    With `rebuild`, a FINAL set that disagrees is rebuilt and checked again, so a ledger correction to
    a past session (the 11-19 ladder backfill, the 11-20 fee rewrite) is absorbed instead of warning
    every morning until someone rebuilds it by hand. Only what still disagrees afterwards is a
    failure; each rebuild is listed in `rebuilt`, with its error when it raised. A provisional set is
    never rebuilt here (it is a snapshot, and stamping it final would be a claim about its session),
    nor are more than `REBUILD_LIMIT` in one pass. The notes (`eod-<day>.note.md`) are written once and
    stay as they were: a rebuilt set can move under a note that was written from the old one.
    """
    store = _paths.data_dir()
    if not store.exists():
        return {"ok": False, "reason": "no fact sets written yet"}
    sessions = sorted(p.stem.removeprefix("eod-") for p in store.glob("eod-*.json"))
    if since:
        sessions = [s for s in sessions if s >= since]
    today = today or _facts.today()
    skipped = [s for s in sessions if s >= today]
    sessions = [s for s in sessions if s < today]

    results = [check_session(s) for s in sessions]
    rebuilt: list[dict] = []
    if rebuild:
        todo = [
            r["session"]
            for r in results
            if not r["ok"] and (_facts.read(r["session"]) or {}).get("status") == _facts.STATUS_FINAL
        ]
        for session in todo[:REBUILD_LIMIT]:
            try:
                rebuild_session(session)
                rebuilt.append({"session": session, "ok": True})
            except Exception as exc:  # noqa: BLE001 -- one bad session must not stop the rest
                rebuilt.append({"session": session, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
        again = {r["session"]: check_session(r["session"]) for r in rebuilt if r["ok"]}
        results = [again.get(r["session"], r) for r in results]
    failures = [r for r in results if not r["ok"]]
    out = {
        "ok": not failures,
        "sessions_checked": len(results),
        "sessions_matching": len(results) - len(failures),
        "skipped": skipped,
        "failures": failures,
    }
    if rebuild:
        out["rebuilt"] = rebuilt
    return out
