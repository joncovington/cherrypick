"""Every modification under test in one module, as one list: where a change is decided.

Pure over three inputs, each read by the code that owns it:

- **The module's comparison** (`core.compare.module_comparison`): every arm beside its control, paired
  by session. An arm stamped `advised` is an advisor experiment's twin of its base; any other arm with a
  control is a configured variant tested against it.
- **The advisor's experiments** (`experiments`, its store read-only): each experiment's own name and
  hypothesis, for a twin's plain name, and its status.
- **Declared ideas and settings** from the backtest (`declares`, its `read declared`): a Builder run's
  surviving idea or setting whose register row was drafted, with its one held-out test or none yet.

Each row says what is tested, against what, how (`kind`), the gap per trade and per session with its
interval, a reading and the sessions to a decision. **The reading** is declared here, once, on the
per-trade pairing where `core.compare` has one (each session's net over its entries, so a variant that
enters more is not read as better for its size), else the per-session one (`basis`):

- `closed`: an advisor experiment expired or killed, or a declared idea or setting that has had its one
  held-out test (its verdict beside it).
- `waiting`: a declared idea or setting whose held-out test has not run.
- `too_early`: fewer than `MIN_SESSIONS` paired sessions, whatever the interval says.
- `ready`: the paired interval excludes zero.
- `too_early`: no count of sessions would exclude zero.
- `leaning`: the sessions still needed (`sessions_to_exclude_zero` less those paired) are no more than
  those already paired: at its size, the gap would decide within as many sessions again.
- otherwise `too_early`.

`direction` is the sign of the paired mean (`better`, `worse`, `even`). A reading is a forward read of
paired paper sessions, never a verdict on the rule; the module's own register says which arms are
declared tests. A declared item's held-out figures come from a backtest run, licensed when the run
was, and the console withholds them as it withholds that run.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from cherrypick.core import home as _home
from cherrypick.core import ledgers as _ledgers

#: Under this many paired sessions a reading is too early, whatever the interval says.
MIN_SESSIONS = 10
CLOSED_STATUSES = frozenset({"expired", "killed"})
READINGS = ("ready", "leaning", "too_early", "waiting", "closed")


def experiments(path: Path | None = None) -> dict[str, dict]:
    """`{experiment id: {name, hypothesis, status, sessions_run, expires_after_sessions}}` from the
    advisor's store, read-only. An absent or unreadable store is empty."""
    path = Path(path or (_home.data_dir("advisor") / "advisor.db"))
    if not path.exists():
        return {}
    try:
        conn = _ledgers.connect_ro(path)
    except sqlite3.Error:
        return {}
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(experiments)")}
        want = ["id", "name", "hypothesis", "status", "sessions_run", "expires_after_sessions"]
        if "id" not in cols:
            return {}
        sel = ", ".join(c if c in cols else f"NULL AS {c}" for c in want)
        return {
            r["id"]: {k: r[k] for k in want if k != "id"}
            for r in conn.execute(f"SELECT {sel} FROM experiments")
        }
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


def first_sentence(text: str | None, limit: int = 160) -> str | None:
    """The first sentence of `text`, cut at `limit` characters; None for none."""
    if not text:
        return None
    s = " ".join(str(text).split())
    for stop in (". ", "; "):
        i = s.find(stop)
        if 0 < i < limit:
            return s[: i + 1].rstrip(";") if stop == ". " else s[:i]
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def _direction(mean: float | None) -> str | None:
    if mean is None:
        return None
    return "better" if mean > 0 else "worse" if mean < 0 else "even"


def basis(vs: Mapping[str, Any] | None) -> tuple[str, Mapping[str, Any] | None]:
    """The pairing a reading is read on: per trade (`per_entry`, each session's net over its entries)
    where `core.compare` has it, so a variant that enters more is not read as better for its size; else
    per session."""
    entry = (vs or {}).get("per_entry")
    if entry and entry.get("sessions"):
        return "per_trade", entry
    return "per_session", vs


def reading(vs: Mapping[str, Any] | None, *, closed: bool = False) -> dict:
    """The reading of one paired comparison (`core.compare`'s `vs_control`), on its `basis`."""
    on, block = basis(vs)
    sessions = int((block or {}).get("sessions") or 0)
    needed = (block or {}).get("sessions_to_exclude_zero")
    to_go = None if needed is None else max(0, int(needed) - sessions)
    if to_go is not None and sessions < MIN_SESSIONS:
        to_go = max(to_go, MIN_SESSIONS - sessions)  # the minimum is still to come, whatever the size
    direction = _direction((block or {}).get("mean_diff")) if sessions else None
    vs = block
    if closed:
        state = "closed"
    elif sessions < MIN_SESSIONS:
        state = "too_early"  # an interval over a handful of sessions is not read, even one clear of zero
    elif (vs or {}).get("ci_excludes_zero"):
        state = "ready"
    elif to_go is None:
        state = "too_early"
    elif to_go <= sessions:
        state = "leaning"
    else:
        state = "too_early"
    return {
        "reading": state,
        "basis": on,
        "direction": direction,
        "sessions": sessions,
        "sessions_to_decision": to_go,
    }


def _gap(vs: Mapping[str, Any] | None) -> dict | None:
    if not vs or not vs.get("sessions"):
        return None
    entry = vs.get("per_entry") or {}
    return {
        "per_session": vs.get("mean_diff"),
        "per_session_ci": vs.get("ci"),
        "per_trade": entry.get("mean_diff") if entry.get("sessions") else None,
        "per_trade_ci": entry.get("ci") if entry.get("sessions") else None,
        "level": vs.get("ci_level"),
        "first": vs.get("first"),
        "last": vs.get("last"),
    }


def _arm_row(arm: Mapping[str, Any], details: Mapping[str, dict]) -> dict | None:
    vs = arm.get("vs_control")
    control = arm.get("control")
    if not control:
        return None
    if arm.get("advised"):
        exp_id = arm.get("experiment_id")
        exp = details.get(exp_id or "") or {}
        status = exp.get("status") or arm.get("experiment_status")
        tag = str(arm["arm"]).removeprefix("advised:")
        name = exp.get("name") or tag
        # One experiment can run a twin per strategy (earnings: `advised:<name>:<strategy>`).
        part = tag[len(name) + 1 :] if tag.startswith(f"{name}:") else ""
        if part:
            name = f"{name} ({part})"
        return {
            "kind": "advised",
            "name": name,
            "description": first_sentence(exp.get("hypothesis")),
            "id": exp_id,
            "arm": arm["arm"],
            "against": control,
            "status": status,
            "gap": _gap(vs),
            **reading(vs, closed=status in CLOSED_STATUSES),
        }
    return {
        "kind": "arm",
        "name": arm["arm"],
        "description": None,
        "id": None,
        "arm": arm["arm"],
        "against": control,
        "status": None,
        "gap": _gap(vs),
        **reading(vs),
    }


def _declared_row(d: Mapping[str, Any]) -> dict:
    held = d.get("held_out")
    closed = held is not None
    return {
        "kind": "declared",
        "name": d.get("title") or f"{d.get('automation')}: {d.get('item')}",
        "description": d.get("measure"),
        "id": d.get("item"),
        "arm": d.get("automation"),
        "against": "its held-out sessions",
        "status": "tested" if closed else "declared",
        "source": {
            "kind": d.get("source_kind"),
            "result": d.get("result"),
            "licensed": bool(d.get("licensed")),
        },
        "gap": None,
        "held_out": held,
        "reading": "closed" if closed else "waiting",
        "basis": None,
        "direction": None,
        "sessions": None,
        "sessions_to_decision": None,
    }


ORDER = {r: i for i, r in enumerate(("ready", "leaning", "waiting", "too_early", "closed"))}


def rows(
    comparison: Mapping[str, Any], details: Mapping[str, dict] | None = None, declares: Iterable[Mapping] = ()
) -> list[dict]:
    """Every change under test in one module's `comparison`, with any `declares` for it, the open ones
    first (ready, leaning, waiting, too early) and the closed last."""
    out = []
    for arm in comparison.get("arms") or []:
        row = _arm_row(arm, details or {})
        if row is not None:
            out.append(row)
    out += [_declared_row(d) for d in declares]
    return sorted(out, key=lambda r: (ORDER[r["reading"]], r["kind"] != "advised", str(r["name"])))


def changes(
    module: str,
    *,
    declares: Iterable[Mapping] = (),
    session: str | None = None,
    db_path: Path | None = None,
    advisor_db: Path | None = None,
) -> dict:
    """The module's changes document: its comparison's status and era, and its rows."""
    from cherrypick.core import compare as _compare

    comp = _compare.module_comparison(
        module, db_path=db_path, session=session, experiments=_compare.experiments_index(advisor_db)
    )
    listed = rows(comp, experiments(advisor_db), declares)
    counts = {r: sum(1 for x in listed if x["reading"] == r) for r in READINGS}
    return {
        "module": module,
        "status": comp.get("status"),
        "reason": comp.get("reason"),
        "era": comp.get("era"),
        "min_sessions": MIN_SESSIONS,
        "counts": counts,
        "rows": listed,
    }


def load_declares(text: str | None) -> list[dict]:
    """The backtest's `read declared` items, as JSON text; none for none."""
    if not text:
        return []
    doc = json.loads(text)
    items = doc.get("items") if isinstance(doc, dict) else doc
    if not isinstance(items, list):
        raise ValueError("declares: a list of declared items, or {items: [...]}")
    return [d for d in items if isinstance(d, dict)]


__all__ = [
    "MIN_SESSIONS",
    "READINGS",
    "changes",
    "experiments",
    "first_sentence",
    "load_declares",
    "reading",
    "rows",
]
