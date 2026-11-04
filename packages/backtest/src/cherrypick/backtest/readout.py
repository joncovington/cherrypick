"""The read surface the console bridges (`python -m cherrypick.backtest read ...`). Read-only.

Everything here is derived from licensed data and goes only to the operator's own console on this
machine (127.0.0.1); nothing is published. The console hides the page unless the orchestrator's
`features.backtest` is true, and its server routes refuse on their own, so this verb is never the
gate: it answers whoever asks on this machine, as every other read verb does.

`overview` -> {ok, license, windows, stores, validation, runs, trials, provisional_looks}
`run --id X [--capital N]` -> {ok, report, cards} (the run's own report, as written, and its cards:
cards.py, each arm on its own peak session risk unless one capital is chosen for all)
`stores` -> {ok, dir, files} (where the declared vendor stores live; the research queue's check)
`summaries --ids A,B` -> {ok, runs, missing} (each run's arms in brief, for the studies tiles: the
report's own mean, sessions and trades per arm, and the running net thinned to SUMMARY_POINTS)
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

from . import licensing, paths, results, store

RUNS_LISTED = 50
#: Points on a tile's running-net line: enough for its shape, small enough for every tile at once.
SUMMARY_POINTS = 48
#: Runs one `summaries` read answers for: a board's worth of studies.
SUMMARY_RUNS = 80
RUN_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")


def _ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _stores(settings: dict) -> list[dict]:
    d = paths.vendor_dir(settings)
    out = []
    for f in sorted(d.glob("*-*s.db")) if d.exists() else []:
        symbol, cadence = f.stem.rsplit("-", 1)
        conn = _ro(f)
        try:
            rows = conn.execute(
                "SELECT trade_date, status, reason FROM vendor_sessions WHERE symbol = ?", (symbol,)
            ).fetchall()
        finally:
            conn.close()
        complete = sorted(r["trade_date"] for r in rows if r["status"] == store.COMPLETE)
        refused = [r for r in rows if r["status"] != store.COMPLETE]
        out.append(
            {
                "symbol": symbol,
                "cadence": int(cadence.rstrip("s")),
                "file": f.name,
                "complete": len(complete),
                "span": [complete[0], complete[-1]] if complete else None,
                "refused": len(refused),
                "refused_reasons": dict(Counter(r["reason"] or "unknown" for r in refused)),
            }
        )
    return out


def _validation(settings: dict) -> list[dict]:
    vdir = paths.results_dir() / "validation"
    latest: dict[tuple[str, str], dict] = {}
    for p in sorted(vdir.glob("*.json"), key=lambda p: p.stat().st_mtime) if vdir.exists() else []:
        doc = json.loads(p.read_text(encoding="utf-8"))
        latest[(doc.get("module", "flies"), doc.get("vendor_db", p.name))] = {**doc, "file": p.name}
    out = []
    for doc in latest.values():
        dec = doc.get("decisions") or {}
        cadence = None
        stem = Path(str(doc.get("vendor_db", ""))).stem
        if "-" in stem:
            cadence = int(stem.rsplit("-", 1)[1].rstrip("s") or 0) or None
        out.append(
            {
                "module": doc.get("module", "flies"),
                "cadence": cadence,
                "file": doc["file"],
                "dates": doc.get("dates") or [],
                "unlocks_history": bool(dec.get("unlocks_history")),
                "sessions_compared": dec.get("sessions_compared"),
                "vendor_at_least_recorder": dec.get("vendor_at_least_recorder") or {},
                # The bar since 2026-10-25 (validate.SHARE_TOLERANCE): empty on an older report.
                "vendor_within_tolerance": dec.get("vendor_within_tolerance") or {},
                "recorder": {k: (dec.get("recorder") or {}).get(k) for k in ("measures", "checks")},
                "vendor": {k: (dec.get("vendor") or {}).get(k) for k in ("measures", "checks")},
                # From the 2026-10-17 amendment: the bar's basis, and the thinned recording when it is.
                "data_basis": dec.get("data_basis") or "recorder",
                "recorder_at_vendor_cadence": {
                    k: (dec.get("recorder_at_vendor_cadence") or {}).get(k) for k in ("measures", "checks")
                }
                if dec.get("recorder_at_vendor_cadence")
                else None,
                "cadence_cost": dec.get("cadence_cost"),
                "sessions": doc.get("sessions") or {},
            }
        )
    return sorted(out, key=lambda v: (v["module"], v["cadence"] or 0))


def _runs() -> tuple[list[dict], dict, dict]:
    if not paths.results_db().exists():
        return [], {}, {}
    conn = results.connect()
    try:
        runs = [
            {
                "run_id": r["run_id"],
                "created_at": r["created_at"],
                "module": r["module"],
                "window": r["window_name"],
                "provisional": r["status"] == "provisional",
                "arms": json.loads(r["arms"]),
                "span": [r["start"], r["end"]],
                "cadence": r["cadence"],
                "sessions": r["sessions"],
                "refused": len(json.loads(r["refused"] or "{}")),
            }
            for r in conn.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (RUNS_LISTED,))
        ]
        trials = {r[0]: r[1] for r in conn.execute("SELECT module, COUNT(*) FROM trials GROUP BY module")}
        # A provisional run records no trial (runner._summarise: a look, not an evaluation), so the
        # trial count can read zero after many looks at the same history. The distinct specs those
        # looks ran are counted here, for the reader only; no statistic is deflated by it.
        looks = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT r.module, COUNT(DISTINCT s.spec_hash) FROM session_results s "
                "JOIN runs r ON r.run_id = s.run_id WHERE r.status = 'provisional' GROUP BY r.module"
            )
        }
    finally:
        conn.close()
    return runs, trials, looks


def _stores_inventory(settings: dict) -> dict:
    from . import stores

    try:
        # Never counts: the bridge's 30 s cannot hold a cold count (`stores.inventory`).
        return stores.inventory(settings, recount=False)
    except Exception as exc:  # noqa: BLE001 -- the inventory never breaks the overview
        return {"error": str(exc), "stores": [], "undeclared": []}


def overview(settings: dict) -> dict:
    runs, trials, looks = _runs()
    return {
        "ok": True,
        "license": licensing.status(settings),
        "windows": settings["windows"],
        "stores": _stores(settings),
        # Every vendor store against the declared registry (stores.py): counts only, and any undeclared file.
        "inventory": _stores_inventory(settings),
        "validation": _validation(settings),
        "runs": runs,
        "trials": trials,
        "provisional_looks": looks,
    }


def store_locations(settings: dict) -> dict:
    """Where every declared vendor store lives (`stores.STORES` in `paths.vendor_dir`): names and the
    directory only, no file is opened."""
    from . import stores

    return {"ok": True, "dir": str(paths.vendor_dir(settings)), "files": [e["file"] for e in stores.STORES]}


def run_report(
    run_id: str,
    capital: float | None = None,
    risk_pct: float | None = None,
    risk_unit: str = "max_loss",
    dd_pct: float | None = None,
) -> dict:
    path = paths.results_dir() / "runs" / f"{run_id}.json"
    if not run_id.replace("-", "").isalnum() or not path.exists():
        return {"ok": False, "error": f"no run {run_id!r}"}
    report = json.loads(path.read_text(encoding="utf-8"))
    return {
        "ok": True,
        "report": report,
        "cards": _cards(run_id, report, capital, risk_pct, risk_unit, dd_pct),
    }


def thin(values: list[float], points: int = SUMMARY_POINTS) -> list[float]:
    """`values` at `points` evenly spaced places, the first and last always kept; all of them when
    there are no more than `points`. For a sparkline's shape only: no figure is read from it."""
    if len(values) <= points:
        return list(values)
    last = len(values) - 1
    return [values[round(i * last / (points - 1))] for i in range(points)]


def run_summaries(run_ids: list[str]) -> dict:
    """Each run's arms in brief, for the studies board's tiles: the mean net a session, sessions and
    trades as the run's own report states them (never recomputed), and the running net over its
    sessions from `session_results`, thinned to SUMMARY_POINTS. An id that is not a run id or has no
    report is listed in `missing`; one read answers for at most SUMMARY_RUNS ids."""
    wanted = list(dict.fromkeys(i for i in run_ids if i))[:SUMMARY_RUNS]
    reports: dict[str, dict] = {}
    missing: list[str] = []
    for rid in wanted:
        path = paths.results_dir() / "runs" / f"{rid}.json"
        if not RUN_ID.match(rid) or not path.exists():
            missing.append(rid)
            continue
        try:
            reports[rid] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            missing.append(rid)
    cums: dict[str, dict[str, list[float]]] = {rid: {} for rid in reports}
    if reports and paths.results_db().exists():
        conn = _ro(paths.results_db())
        try:
            marks = ",".join("?" * len(reports))
            for r in conn.execute(
                f"SELECT run_id, arm, net FROM session_results WHERE run_id IN ({marks}) "
                "ORDER BY run_id, arm, session",
                list(reports),
            ):
                line = cums[r["run_id"]].setdefault(r["arm"], [])
                line.append((line[-1] if line else 0.0) + (r["net"] or 0.0))
        finally:
            conn.close()
    runs = {}
    for rid, report in reports.items():
        arms = {}
        for arm, entry in (report.get("arms") or {}).items():
            summary = (entry or {}).get("summary") or {}
            arms[arm] = {
                "mean": summary.get("mean"),
                "sessions": summary.get("sessions"),
                "trades": (entry or {}).get("trades"),
                "path": [round(v, 2) for v in thin(cums[rid].get(arm, []))],
            }
        runs[rid] = {
            "module": report.get("module"),
            "window": report.get("window"),
            "provisional": bool(report.get("provisional")),
            "span": report.get("span"),
            "arms": arms,
        }
    return {"ok": True, "runs": runs, "missing": missing}


def run_trades(run_id: str, arm: str) -> dict:
    """One arm's trade log from a run (cards.trades_for_run): every trade with its own exit reason."""
    from . import cards, runner

    path = paths.results_dir() / "runs" / f"{run_id}.json"
    if not run_id.replace("-", "").isalnum() or not path.exists():
        return {"ok": False, "error": f"no run {run_id!r}"}
    report = json.loads(path.read_text(encoding="utf-8"))
    if arm not in (report.get("arms") or {}):
        return {"ok": False, "error": f"run {run_id} has no arm {arm!r}"}
    mod = runner.MODULES[report.get("module") or runner.MODULE]
    return {"ok": True, "run_id": run_id, "arm": arm, "trades": cards.trades_for_run(run_id, mod.schema, arm)}


def _cards(
    run_id: str,
    report: dict,
    capital: float | None,
    risk_pct: float | None = None,
    risk_unit: str = "max_loss",
    dd_pct: float | None = None,
) -> dict:
    """The run's cards (cards.py). A failure here is the cards' own error, never the report's."""
    from . import cards, runner

    try:
        mod = runner.MODULES[report.get("module") or runner.MODULE]
        arms = list(report.get("arms") or {})
        pct = cards.DEFAULT_RISK_PCT if risk_pct is None else risk_pct
        dd = cards.DEFAULT_DD_PCT if dd_pct is None else dd_pct
        out = {"ok": True, **cards.for_run(run_id, mod.schema, arms, capital, pct, risk_unit, dd)}
        strips = cards.arm_settings(_arm_specs(report, mod.name))
        for arm, c in (out.get("arms") or {}).items():
            c["settings"] = strips.get(arm, {"recorded": False})
        return out
    except Exception as exc:  # noqa: BLE001 -- the report still reads without its cards
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _arm_specs(report: dict, module: str) -> dict[str, dict | None]:
    """Each arm's spec: the report's own (runs from 2026-11-01), else the trial row its hash
    recorded (a design run that was not provisional), else None."""
    specs: dict[str, dict | None] = {}
    wanted: dict[str, str] = {}
    for arm, entry in (report.get("arms") or {}).items():
        spec = (entry or {}).get("spec")
        specs[arm] = spec if isinstance(spec, dict) else None
        if specs[arm] is None and (entry or {}).get("spec_hash"):
            wanted[arm] = entry["spec_hash"]
    if wanted:
        conn = results.connect()
        try:
            for arm, h in wanted.items():
                row = conn.execute(
                    "SELECT spec FROM trials WHERE module = ? AND spec_hash = ?", (module, h)
                ).fetchone()
                specs[arm] = json.loads(row[0]) if row else None
        finally:
            conn.close()
    return specs
