"""The daily fact set -- deterministic, versioned, and the only thing any surface reads.

Every EOD surface in this suite used to derive its own numbers from each module's ledger, which is
how the orchestrator's report and the console's TypeScript port came to disagree about flies (one
reads `fly_positions`, the other `fly_books`). So nothing here is re-derived downstream: this module
writes one JSON artifact per session and the markdown render, the console page and the narrative all
read *that*. If they disagree, one of them has a bug, rather than one of them having a different
opinion.

Three rules the shape enforces, each of which this suite has already been bitten by:

**`None` is not zero.** A module with no row for a field reports null. A cost of zero and a cost
that was never recorded are different facts, and the paper book already contains 46 trades whose
slippage predates the column -- averaging those as zero flatters the cost model by ~90%.

**Effective sample sits beside raw N.** Earnings' 64 trades are ~14 independent earnings events;
trades sharing a symbol and session share one event and are not independent observations. Reporting
64 invites a conclusion the data cannot carry -- a mistake made against this very book.

**Measurement breaks travel with the numbers.** Results either side of a break must never be
pooled. Earnings and MEIC record breaks; flies has no such table, and the fact set says so rather
than implying a continuity it cannot verify.

Status: a session is `provisional` at the close and `final` once the overnight module has settled.
MEIC and flies are 0DTE and complete at the close; earnings opens before the close and settles the
next morning, so its realised P&L for session D only exists on session D+1. The narrative runs on
`final` sets only, which is what lets it be written once and frozen.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
from datetime import UTC, date, datetime

from cherrypick.core import ledgers as _ledgers
from cherrypick.core.profiles import compare_profiles as _compare_profiles

from cherrypick.review import paths as _paths

FACT_VERSION = 3

STATUS_PROVISIONAL = "provisional"
STATUS_FINAL = "final"

# Module -> (ledger schema, does it settle within the session?). The 0DTE modules are complete at
# the close; earnings carries overnight, which is the whole reason a session has two passes.
MODULES = {
    "meic": {"schema": "meic_ic", "settles_intraday": True},
    "flies": {"schema": "fly_book", "settles_intraday": True},
    "earnings": {"schema": "earnings", "settles_intraday": False},
}


def _rows(conn, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    """Query, or [] if the table doesn't exist in this module's ledger. A module that has never
    written a table is not an error -- it is a module that has not done that thing yet."""
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []


def _scalar(conn, sql: str, params: tuple = ()):
    rows = _rows(conn, sql, params)
    return rows[0][0] if rows else None


# --------------------------------------------------------------------------- health
# "Did it run" is a different question from "what did it earn", and today proved they can look
# identical: the earnings entry scan reported a quiet book for three sessions while a decayed
# calendar column was silently starving it of candidates. A health line is what separates a module
# that chose not to trade from one that could not.


def _meic_health(conn, session: str) -> dict:
    attempts = _rows(
        conn,
        "SELECT outcome, COUNT(*) n FROM entry_attempts WHERE trade_date = ? GROUP BY outcome",
        (session,),
    )
    return {
        "loop_ticked": bool(_scalar(conn, "SELECT COUNT(*) FROM loop_log WHERE loop_date = ?", (session,))),
        "iterations": _scalar(conn, "SELECT COUNT(*) FROM loop_log WHERE loop_date = ?", (session,)),
        "entry_attempts": {r["outcome"] or "unknown": r["n"] for r in attempts} or None,
    }


def _flies_health(conn, session: str) -> dict:
    attempts = _rows(
        conn,
        "SELECT outcome, COUNT(*) n FROM fly_entry_attempts WHERE trade_date = ? GROUP BY outcome",
        (session,),
    )
    iterations = _scalar(conn, "SELECT COUNT(*) FROM fly_iterations WHERE trade_date = ?", (session,))
    return {
        "loop_ticked": bool(iterations),
        "iterations": iterations,
        "entry_attempts": {r["outcome"] or "unknown": r["n"] for r in attempts} or None,
    }


def _earnings_health(conn, session: str) -> dict:
    phases = _rows(
        conn,
        "SELECT phase, status, COUNT(*) n FROM loop_iterations WHERE session_date = ? GROUP BY phase, status",
        (session,),
    )
    iterations = sum(r["n"] for r in phases)
    return {
        "loop_ticked": bool(iterations),
        "iterations": iterations or None,
        # Phase coverage is the tell for this module: an entry phase that never ran means no
        # candidate could have been taken regardless of what the screen thought.
        "phases": {f"{r['phase']}:{r['status']}": r["n"] for r in phases} or None,
        "errors": sum(r["n"] for r in phases if r["status"] != "ok") or None,
    }


HEALTH_READERS = {"meic": _meic_health, "flies": _flies_health, "earnings": _earnings_health}


# --------------------------------------------------------------------------- expected vs observed
# Deliberately module-native. Each module already records an expectation, in its own terms: flies
# models a book's P&L before the day resolves, MEIC proposes entries it may or may not fill,
# earnings prices an implied move. Forcing one common definition would mean inventing a model where
# a module has none, which produces a number that looks rigorous and is not.


def _meic_expected(conn, session: str) -> dict:
    row = _rows(
        conn,
        "SELECT SUM(total_entries) proposed, SUM(entries_filled) filled, SUM(gross_credit) credit,"
        " SUM(net_pnl) net FROM daily_summary WHERE summary_date = ?",
        (session,),
    )
    r = row[0] if row else None
    if not r or r["proposed"] is None:
        return {"basis": "entries_proposed_vs_filled", "expected": None, "observed": None}
    return {
        "basis": "entries_proposed_vs_filled",
        "expected": r["proposed"],
        "observed": r["filled"],
        "credit_collected": r["credit"],
        "net": r["net"],
    }


def _flies_expected(conn, session: str) -> dict:
    """flies is the one module with a true modelled counterfactual: every book carries the P&L its
    own model expected alongside what it made, plus whether its floor held and over what band."""
    rows = _rows(
        conn,
        "SELECT SUM(modeled_pnl) modeled, SUM(pnl) actual, SUM(floor_holds) held, COUNT(*) books,"
        " AVG(completion_rate) completion FROM fly_books WHERE trade_date = ?",
        (session,),
    )
    r = rows[0] if rows else None
    if not r or r["books"] in (None, 0):
        return {"basis": "modeled_pnl", "expected": None, "observed": None}
    return {
        "basis": "modeled_pnl",
        "expected": r["modeled"],
        "observed": r["actual"],
        "books": r["books"],
        "floor_held": r["held"],
        "completion_rate": r["completion"],
    }


def _earnings_expected(conn, session: str) -> dict:
    """Earnings' expectation is the implied move priced at entry. It only became recoverable once
    entry_context started carrying it (2026-08-12); older positions report null rather than a
    reconstructed guess."""
    rows = _rows(
        conn,
        "SELECT entry_context FROM trades WHERE entry_context IS NOT NULL"
        " AND date(opened_at, 'unixepoch', 'localtime') = ?",
        (session,),
    )
    moves = []
    for r in rows:
        try:
            ctx = json.loads(r["entry_context"])
        except (TypeError, ValueError):
            continue
        if ctx.get("expected_move_pct") is not None:
            moves.append(ctx["expected_move_pct"])
    return {
        "basis": "implied_move_at_entry",
        "expected": (sum(moves) / len(moves)) if moves else None,
        "observed": None,  # realised move lands with the close, in the next session's fact set
        "positions_with_expectation": len(moves) or None,
    }


EXPECTED_READERS = {"meic": _meic_expected, "flies": _flies_expected, "earnings": _earnings_expected}


# --------------------------------------------------------------------------- sample and breaks


def _measurement_breaks(conn) -> list[str] | None:
    """Dates results must never be pooled across, or None where the module has no such table.

    None and [] mean different things: [] is a module that tracks breaks and has none, None is a
    module that does not track them at all -- and a trend line through the second is only as
    trustworthy as the assumption that nothing changed.
    """
    rows = _rows(conn, "SELECT break_date FROM measurement_breaks ORDER BY break_date")
    if not rows:
        # Distinguish "table missing" from "table empty".
        exists = _rows(
            conn, "SELECT name FROM sqlite_master WHERE type='table' AND name='measurement_breaks'"
        )
        return [] if exists else None
    # Deduped: a break is a date results must not be pooled across, and one date recorded under
    # three keys is still one break.
    return sorted({r["break_date"] for r in rows})


def _suspected_break(
    all_records: list[dict],
    session: str,
    lookback: int = 10,
    factor: float = 3.0,
    min_trades: int = 10,
) -> dict | None:
    """A session whose scale departs sharply from the recent past, where no break is journaled.

    MEIC's trade count and capital both stepped up roughly tenfold on 2026-08-07 when the
    four-stream forward test launched, and its `measurement_breaks` records 2026-08-11 — so the
    largest regime change in the book is not journaled, and a trend drawn through it pools a
    20-trade-a-day book with a 700-trade-a-day one.

    This detects that shape and reports it with the numbers behind the suspicion. It never writes a
    break: journaling one is a judgement about what the module did and belongs to the module. The
    trigger is deliberately crude — `factor`x the trailing median — because a detector nobody can
    reason about produces flags nobody trusts.
    """
    by_session: dict[str, list[dict]] = {}
    for r in all_records:
        if r.get("session"):
            by_session.setdefault(r["session"], []).append(r)
    if session not in by_session:
        return None

    earlier = sorted(s for s in by_session if s < session)
    prior = earlier[-lookback:]
    if len(prior) < 3:  # too little history to call anything a departure from it
        return None

    def _scale(rows: list[dict]) -> float:
        return float(len(rows))

    today_scale = _scale(by_session[session])
    baseline = statistics.median(_scale(by_session[s]) for s in prior)
    if baseline <= 0 or today_scale <= 0:
        return None

    # An absolute floor before any ratio is believed. Earnings going from 6 trades to 2 is a 0.33x
    # "departure" and means nothing; at these counts the ratio is arithmetic, not evidence.
    if max(today_scale, baseline) < min_trades:
        return None

    ratio = today_scale / baseline
    if (1 / factor) < ratio < factor:
        return None

    # Flag the step, not the plateau after it. The trailing median takes many sessions to catch up
    # to a tenfold change, so comparing only against it re-reports one regime change every session
    # until it does -- MEIC's launch flagged on 08-07, 08-10 and 08-12 for a single event. A shift
    # that already happened yesterday is not news today.
    previous = _scale(by_session[earlier[-1]])
    if previous > 0:
        step = today_scale / previous
        if (1 / factor) < step < factor:
            return None

    return {
        "session": session,
        "trades": int(today_scale),
        "trailing_median_trades": baseline,
        "ratio": round(ratio, 2),
        "basis": f"trade count vs median of prior {len(prior)} sessions, and vs the session before",
        "note": "suspected regime change with no journaled measurement break",
    }


def _sample(records: list[dict]) -> dict:
    """Raw count beside the count of independent events.

    Trades sharing a symbol and a session share one market event; treating them as independent is
    how 64 earnings trades get read as 64 observations when they represent ~14.
    """
    events = {(r.get("session") or "", r.get("symbol") or "") for r in records}
    return {"n": len(records), "effective_n": len(events)}


# --------------------------------------------------------------------------- assembly


def _returns(records: list[dict]) -> dict:
    """Return on risk, both ways.

    `on_max_risk` divides by the worst case defined at entry. `on_deployed` is left null for now
    and deliberately not aliased to the same number: for a defined-risk spread the broker's margin
    IS the max loss, so the two only diverge under portfolio margin or an undefined-risk position,
    and publishing one figure under two headings would manufacture a distinction that isn't there.
    """
    net = sum(r.get("net_pnl") or 0.0 for r in records)
    capitals = [r["capital"] for r in records if r.get("capital") is not None]
    total_capital = sum(capitals) if capitals else None
    return {
        "net": round(net, 2),
        "capital_at_risk": round(total_capital, 2) if total_capital else None,
        "on_max_risk": round(net / total_capital, 6) if total_capital else None,
        "on_deployed": None,
        "capital_coverage": f"{len(capitals)}/{len(records)}" if records else None,
    }


def _summarize(records: list[dict]) -> dict:
    """The per-group figures. Shared by the module total and each of its arms so the parts always
    add up to the whole."""
    gross = sum(r.get("gross_pnl") or 0.0 for r in records)
    cost = sum(r.get("cost") or 0.0 for r in records)
    return {
        "closed": len(records),
        "gross": round(gross, 2),
        "cost": round(cost, 2),
        "net": round(gross - cost, 2),
        "wins": sum(1 for r in records if (r.get("net_pnl") or 0) > 0),
        "return": _returns(records),
        "sample": _sample(records),
    }


def _by_profile(records: list[dict]) -> dict:
    """Split a module's session by its attribution tag — MEIC's risk_profile, flies' arm, earnings'
    book. `cherrypick.core.ledgers` normalises all three onto `profile`.

    Collapsing these away loses the experiment. MEIC currently runs `open`, `width-5` and `width-10`
    against the same underlying on the same sessions, which is a paired comparison and the entire
    reason three profiles exist; a single module row reports their average and hides that `open`
    takes no stops at all while the other two stop 70-90% of trades on a moving day. Flies runs its
    arms for exactly the same reason.

    Grouped through `cherrypick.core.profiles.compare_profiles`, the helper the orchestrator's own
    per-profile reporting already uses, rather than a fourth hand-rolled grouping.
    """
    if not records:
        return {}
    return _compare_profiles(records, tag_key="profile", summarize=_summarize)


def build_module_facts(module: str, session: str, db_path=None) -> dict:
    """One module's slice of a session, or a structured reason it could not be read."""
    spec = MODULES[module]
    db_path = db_path or _paths.module_db(module)
    if not db_path.exists():
        return {"ok": False, "reason": "ledger not found", "db": str(db_path)}

    conn = _ledgers.connect_ro(db_path)
    try:
        # The bounds are a pushdown hint, NOT a guarantee: the earnings reader deliberately does no
        # SQL date filtering, because closed_at is epoch seconds while the session is a local
        # calendar day and SQLite's date(...,'unixepoch') is UTC -- a SQL bound there would shift
        # evening closes into the wrong session. The orchestrator filters in Python afterwards and
        # so must we. Filtering on the returned `session` field is schema-agnostic and cannot drift
        # from whatever each reader decided a session was. (Caught by reconcile on its first run,
        # which is the entire reason that step exists.)
        closed = [
            r
            for r in _ledgers.READERS[spec["schema"]](conn, start=session, end=session)
            if r.get("session") == session
        ]
        open_reader = _ledgers.OPEN_READERS[spec["schema"]]
        try:
            carried = [r for r in open_reader(conn) if r.get("session") == session]
        except sqlite3.Error:
            carried = []
        health = HEALTH_READERS[module](conn, session)
        expected = EXPECTED_READERS[module](conn, session)
        breaks = _measurement_breaks(conn)
        # Unbounded read: the suspicion is about how this session compares with the recent past,
        # so it needs the past. Cheap at these table sizes (MEIC's is the largest at ~2.6k rows).
        history = _ledgers.READERS[spec["schema"]](conn)
    finally:
        conn.close()

    suspected = _suspected_break(history, session)
    if suspected and breaks and session in breaks:
        suspected = None  # already journaled; nothing to flag

    totals = _summarize(closed)
    return {
        "ok": True,
        "book": "paper",
        "settles_intraday": spec["settles_intraday"],
        "health": health,
        "results": {k: totals[k] for k in ("closed", "gross", "cost", "net", "wins")},
        # The arms, kept because for MEIC and flies the comparison between them IS the experiment.
        "by_profile": _by_profile(closed),
        "carried_overnight": {
            "positions": len(carried),
            "capital_at_risk": round(sum(r.get("capital_at_risk") or 0.0 for r in carried), 2)
            if carried
            else None,
        },
        "return": _returns(closed),
        "expected_vs_observed": expected,
        "sample": {**_sample(closed), "breaks": breaks, "suspected_break": suspected},
    }


def build(session: str, status: str = STATUS_PROVISIONAL, modules=None) -> dict:
    """The whole fact set for one session."""
    names = modules or list(MODULES)
    per_module = {name: build_module_facts(name, session) for name in names}
    readable = [m for m in per_module.values() if m.get("ok")]
    return {
        "session": session,
        "status": status,
        "fact_version": FACT_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        # Deliberately no suite net. Three modules at these scales do not sum into anything
        # meaningful: on 2026-08-12 the combined figure was $82,629 of which MEIC was $80,102, so
        # the total tracked one module with noise attached and read as though it described three.
        "suite": {
            "closed": sum(m["results"]["closed"] for m in readable),
            "net_by_module": {n: m["results"]["net"] for n, m in per_module.items() if m.get("ok")},
            "modules_read": len(readable),
            "modules_unreadable": [n for n, m in per_module.items() if not m.get("ok")],
        },
        "modules": per_module,
    }


def write(facts: dict) -> object:
    """Write the fact set atomically. A reader must never see a half-written artifact."""
    target = _paths.facts_path(facts["session"])
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(facts, indent=2, default=str), encoding="utf-8")
    tmp.replace(target)
    return target


def read(session: str) -> dict | None:
    try:
        return json.loads(_paths.facts_path(session).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def sessions_with_activity(module: str, db_path=None) -> list[str]:
    """Every session this module has a closed trade for -- the backfill's work list."""
    spec = MODULES[module]
    db_path = db_path or _paths.module_db(module)
    if not db_path.exists():
        return []
    conn = _ledgers.connect_ro(db_path)
    try:
        records = _ledgers.READERS[spec["schema"]](conn)
    finally:
        conn.close()
    return sorted({r["session"] for r in records if r.get("session")})


def today() -> str:
    return date.today().isoformat()
