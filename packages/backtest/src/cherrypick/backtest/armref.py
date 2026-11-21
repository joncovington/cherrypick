"""A module arm's backtest reference: the stored run whose arm has the paper arm's settings, and the
paper record set against it. `read arm-reading --module M --name ARM`.

**The match is by settings, never by name.** A run's report keeps each arm's spec (`runner.spec_for`,
`runner.meic_spec_for`: the params merged over the base, the cadence and the fill model). A study names
its arms for the study (`n1`, `w35`), so the arm's own name is left out of both sides and a run arm
matches the paper arm whose current spec is the same in every other key. A paper arm whose settings no
stored run has tried has no reference, and says so: no stand-in run, no nearest spec. Where several runs
match, the one with the most sessions answers, then the newest; the others are listed.

**The unit is the session's net.** A module arm can enter many times a session (MEIC's control takes
hundreds), so a band over the next twenty trades would cover minutes of one session. Both sides are read
per session: the run's from its stored per-session results (`session_results`, one row per arm and
session), paper's from `core.compare`, which reads the module's ledger over its current measurement era
(the changes tab's window), so the record is the arm as it is set now. The reading is then
`automationref.reading` with a session in the place of a trade.

Derived from licensed data where the run read the vendor's store (every module run does): it goes only
to the operator's own console, withheld there while licensed results are hidden. Nothing here is a trial
or a verdict, and nothing goes to the advisor.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from . import automationref, paths, runner

#: The modules whose paper arms can be matched to replay runs, and each one's paper cadence. Flies is retired.
CADENCE = {"meic": 60}


def _without_name(spec: dict) -> dict:
    return {k: v for k, v in spec.items() if k != "arm"}


def settings_key(spec: dict) -> str:
    """The hash of a spec with the arm's name left out: what decides its trades, whatever it is called."""
    return runner.spec_hash(_without_name(spec))


def paper_keys(module: str, config: dict) -> dict[str, str]:
    """`{paper arm: settings_key}` for every arm the module's current config declares."""
    mod = runner.MODULES[module]
    registry = config["registry"].get("profiles", config["registry"])
    out = {}
    for arm in registry:
        if str(arm).startswith("_"):
            continue
        try:
            out[arm] = settings_key(mod.spec_for(config, arm, cadence=CADENCE[module]))
        except runner.RunRefused:
            continue
    return out


def run_arms(conn: sqlite3.Connection, module: str) -> list[dict]:
    """Every stored run arm of `module` with a recorded spec: run, arm, window, span, sessions, key."""
    out = []
    for r in conn.execute(
        "SELECT run_id, window_name, start, end, created_at, report FROM runs "
        "WHERE module = ? ORDER BY created_at",
        (module,),
    ):
        try:
            doc = json.loads(r["report"] or "{}")
        except ValueError:
            continue
        arms = doc.get("arms") if isinstance(doc, dict) else None
        for name, a in (arms or {}).items() if isinstance(arms, dict) else []:
            spec = a.get("spec") if isinstance(a, dict) else None
            if not isinstance(spec, dict):
                continue
            sessions = conn.execute(
                "SELECT COUNT(*) FROM session_results WHERE run_id = ? AND arm = ?", (r["run_id"], name)
            ).fetchone()[0]
            out.append(
                {
                    "run": r["run_id"],
                    "run_arm": name,
                    "window": r["window_name"],
                    "first": r["start"],
                    "last": r["end"],
                    "created_at": r["created_at"],
                    "sessions": sessions,
                    "key": settings_key(spec),
                }
            )
    return out


def matches(key: str, candidates: list[dict]) -> list[dict]:
    """The run arms with this settings key, the one that answers first: most sessions, then newest."""
    found = [c for c in candidates if c["key"] == key and c["sessions"] > 0]
    return sorted(found, key=lambda c: (-c["sessions"], -float(c["created_at"] or 0)))


def session_doc(conn: sqlite3.Connection, run: str, run_arm: str, name: str) -> dict:
    """The run arm's per-session nets as a reference document (`automationref.reference`'s shape), each
    session a unit, under the paper arm's `name`."""
    rows = conn.execute(
        "SELECT session, net FROM session_results WHERE run_id = ? AND arm = ? ORDER BY session",
        (run, run_arm),
    ).fetchall()
    sessions = [r["session"] for r in rows]
    return {
        "name": run,
        "sessions": sessions,
        "trades": [
            {
                "arm": name,
                "session": r["session"],
                "exit_session": r["session"],
                "net": float(r["net"] or 0.0),
            }
            for r in rows
        ],
        "summary": {name: {"sessions": len(rows), "reasons": {}}},
    }


def paper_sessions(module: str, arm: str, *, session: str | None = None) -> tuple[list[dict], str | None]:
    """The paper arm's net per session over the module's current measurement era (`core.compare`), and
    where that era starts."""
    from cherrypick.core import compare

    comp = compare.module_comparison(module, session=session, experiments={})
    era = (comp.get("era") or {}).get("start")
    row = next((a for a in comp.get("arms") or [] if a.get("arm") == arm), None)
    nets = (row or {}).get("session_nets") or []
    return [{"session": s, "net": float(n)} for s, n in nets], era


def arm_reading(
    module: str, arm: str, *, session: str | None = None, resamples: int = automationref.RESAMPLES
) -> dict[str, Any]:
    """`read arm-reading`: the paper arm's matching runs and, against the one that answers, its reading."""
    if module not in CADENCE:
        return {"ok": False, "error": f"no replay runs for {module!r}"}
    try:
        config, _ = runner.MODULES[module].load()
    except runner.RunRefused as exc:
        return {"ok": False, "error": str(exc)}
    keys = paper_keys(module, config)
    if arm not in keys:
        return {"ok": False, "error": f"{module} declares no arm {arm!r}"}
    conn = (
        sqlite3.connect(f"file:{paths.results_db()}?mode=ro", uri=True)
        if paths.results_db().exists()
        else None
    )
    if conn is None:
        return {
            "ok": True,
            "module": module,
            "arm": arm,
            "unit": "session",
            "licensed": True,
            "matches": [],
            "chosen": None,
            "reading": None,
        }
    conn.row_factory = sqlite3.Row
    try:
        found = matches(keys[arm], run_arms(conn, module))
        brief = [{k: m[k] for k in ("run", "run_arm", "window", "first", "last", "sessions")} for m in found]
        if not found:
            return {
                "ok": True,
                "module": module,
                "arm": arm,
                "unit": "session",
                "licensed": True,
                "matches": [],
                "chosen": None,
                "reading": None,
            }
        chosen = found[0]
        doc = session_doc(conn, chosen["run"], chosen["run_arm"], arm)
    finally:
        conn.close()
    paper, era = paper_sessions(module, arm, session=session)
    reading = automationref.reading(doc, arm, paper, resamples=resamples)
    return {
        "ok": True,
        "module": module,
        "arm": arm,
        "unit": "session",
        # Every module run replays the vendor's store.
        "licensed": True,
        "matches": brief,
        "chosen": brief[0],
        "paper_from": era,
        "reading": reading,
    }
