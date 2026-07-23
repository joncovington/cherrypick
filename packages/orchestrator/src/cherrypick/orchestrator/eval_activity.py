"""Per-module eval-activity health — is a continuous-loop module actually *evaluating* candidates, and
what is it deciding, not merely "did the loop write a file recently" (that's the freshness check).

This distinguishes a **healthily gate-blocked** quiet day — e.g. MEIC evaluating every symbol every
iteration and rejecting them all on `regime_gex_negative` (a negative-gamma regime it deliberately won't
sell condors into) — from a loop that is **iterating but not evaluating**, **erroring on its data**
(e.g. RUT "no price"), or that **stopped evaluating mid-session**. On 2026-07-23 "MEIC opened 0 trades"
looked alarming but was healthy; only the loop_log proved it, which is what this surfaces.

Schema-keyed like report.py (`paper.trade_schema` -> reader). Only the continuous-loop modules are
wired: `meic_ic` (loop_log) and `fly_book` (fly_snapshots). Earnings is an event-driven daily scan whose
"did it run" is already the entry-SLA check, so it has no eval-activity reader.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config as cfgmod

OK, WARN = "OK", "WARN"

# Reject reasons that mean "the strategy looked and correctly abstained" — a quiet day, not a fault.
_BENIGN_REASON = (
    "regime_gex", "below_near_miss", "iv_rank", "credit", "delta", "otm", "skip",
    "no_0dte", "correlation", "cap", "window", "no_fresh_quotes", "no_spot_price",
)


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _age_min(ts_iso: str | None) -> float | None:
    """Minutes since an ISO timestamp (tz-aware or naive), or None if unparseable/absent."""
    if not ts_iso:
        return None
    try:
        t = datetime.fromisoformat(str(ts_iso))
    except ValueError:
        return None
    now = datetime.now(t.tzinfo) if t.tzinfo else datetime.now()
    return max(0.0, (now - t).total_seconds() / 60.0)


def _in_window(ts_iso: str | None, window_min: int) -> bool:
    age = _age_min(ts_iso)
    return age is not None and age <= window_min


def _empty() -> dict[str, Any]:
    return {"iterations": 0, "evaluated": 0, "errors": 0, "entries": 0,
            "last_age_min": None, "top_reason": None}


# --------------------------------------------------------------------------- meic_ic (loop_log)
def _meic_activity(conn, day: str, window_min: int) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT loop_time, reasoning, mcp_errors FROM loop_log WHERE loop_date = ? ORDER BY id", (day,)
    ).fetchall()
    if not rows:
        return _empty()
    last_age = _age_min(rows[-1]["loop_time"])
    recent = [r for r in rows if _in_window(r["loop_time"], window_min)]
    evaluated = errors = 0
    reasons: dict[str, int] = {}
    for r in recent:
        txt = r["reasoning"] or ""
        # Each evaluated symbol prints "SYM(ivr X): ..."; each errored one prints "SYM: ERROR ...".
        evaluated += len(re.findall(r"\(ivr", txt))
        errors += len(re.findall(r"ERROR", txt))
        mcp = str(r["mcp_errors"] or "").strip()  # may be "[]" (JSON), "0", or an error blob
        if mcp and mcp not in ("[]", "0", "null", "{}", "None"):
            errors += 1
        for reason in re.findall(r"all ([a-z_]+)", txt):  # "SYM: all regime_gex_negative"
            reasons[reason] = reasons.get(reason, 0) + 1
    ent = conn.execute("SELECT entry_time FROM ic_trades WHERE trade_date = ?", (day,)).fetchall()
    entries = sum(1 for e in ent if _in_window(e["entry_time"], window_min))
    top = max(reasons, key=reasons.get) if reasons else None
    return {"iterations": len(recent), "evaluated": evaluated, "errors": errors,
            "entries": entries, "last_age_min": last_age, "top_reason": top}


# --------------------------------------------------------------------------- fly_book (fly_snapshots)
def _flies_activity(conn, day: str, window_min: int) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT iteration_ts, status FROM fly_snapshots WHERE trade_date = ? ORDER BY id", (day,)
    ).fetchall()
    if not rows:
        return _empty()
    last_age = _age_min(rows[-1]["iteration_ts"])
    recent = [r for r in rows if _in_window(r["iteration_ts"], window_min)]
    evaluated = sum(1 for r in recent if r["status"] == "ok")
    refused = [r["status"] for r in recent if r["status"] != "ok"]  # no_fresh_quotes / no_spot_price
    errors = len(refused)
    top = None
    if refused:
        top = max(set(refused), key=refused.count)
    ent = conn.execute("SELECT entry_time FROM fly_positions WHERE trade_date = ?", (day,)).fetchall()
    entries = sum(1 for e in ent if _in_window(e["entry_time"], window_min))
    return {"iterations": len(recent), "evaluated": evaluated, "errors": errors,
            "entries": entries, "last_age_min": last_age, "top_reason": top}


_READERS = {"meic_ic": _meic_activity, "fly_book": _flies_activity}


def assess(act: dict[str, Any], *, window_min: int, eval_stale_min: float,
           error_frac_warn: float) -> tuple[str, str]:
    """Apply the four health triggers to an activity snapshot -> (status, detail). Rejecting-all is
    HEALTHY (legit gates), so the alarms are: stopped evaluating, iterating-but-not-evaluating, evals
    dominated by errors, and 0-entries for a non-benign reason."""
    last, iters = act["last_age_min"], act["iterations"]
    ev, err, ent, top = act["evaluated"], act["errors"], act["entries"], act["top_reason"]
    if last is None:
        return OK, "no iterations recorded today"  # 'not running at all' is the freshness check's job
    if last > eval_stale_min:  # (3) stopped evaluating mid-session
        return WARN, f"stopped evaluating — last iteration {last:.0f} min ago"
    if iters == 0:  # window empty but last_age <= stale would be contradictory; guard anyway
        return WARN, f"no iterations in the last {window_min} min"
    if ev == 0:  # (1) iterating but nothing evaluated (all refused/errored)
        return WARN, f"iterating ({iters}x) but evaluated nothing — {top or 'all refused/errored'}"
    total = ev + err
    if total and err / total >= error_frac_warn:  # (2) evaluations erroring
        return WARN, f"evaluations erroring ({err}/{total}) — {top or 'error'}"
    if ent == 0:  # (4) never enters for an unusual (non-benign-gate) reason
        if top and not any(b in top for b in _BENIGN_REASON):
            return WARN, f"no entries; dominant reason not a known gate: {top}"
        return OK, f"{ev} evals, 0 entries (all {top or 'rejected'})"
    return OK, f"{ev} evals, {ent} entries"


def for_module(mcfg: dict[str, Any], name: str, day: str, window_min: int) -> dict[str, Any] | None:
    """Activity snapshot for one module, or None if its schema has no eval-activity reader (earnings)
    or its paper DB is absent. Read-only, files only."""
    paper = mcfg.get("paper", {})
    reader = _READERS.get(paper.get("trade_schema", "meic_ic"))
    if reader is None:
        return None
    db = cfgmod.paper_db_path(mcfg, name)
    if not db.exists():
        return None
    conn = _connect_ro(db)
    try:
        return reader(conn, day, window_min)
    except sqlite3.Error:
        return None
    finally:
        conn.close()
