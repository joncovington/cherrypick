"""The read surface the console bridges (`python -m cherrypick.backtest read ...`). Read-only.

Everything here is derived from licensed data and goes only to the operator's own console on this
machine (127.0.0.1); nothing is published. The console hides the page unless the orchestrator's
`features.backtest` is true, and its server routes refuse on their own, so this verb is never the
gate: it answers whoever asks on this machine, as every other read verb does.

`overview` -> {ok, license, windows, stores, validation, runs, trials}
`run --id X` -> {ok, report} (the run's own report, as written)
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path

from . import licensing, paths, results, store

RUNS_LISTED = 50


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


def _runs() -> tuple[list[dict], dict]:
    if not paths.results_db().exists():
        return [], {}
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
    finally:
        conn.close()
    return runs, trials


def _stores_inventory(settings: dict) -> dict:
    from . import stores

    try:
        return stores.inventory(settings)
    except Exception as exc:  # noqa: BLE001 -- the inventory never breaks the overview
        return {"error": str(exc), "stores": [], "undeclared": []}


def overview(settings: dict) -> dict:
    runs, trials = _runs()
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
    }


def run_report(run_id: str) -> dict:
    path = paths.results_dir() / "runs" / f"{run_id}.json"
    if not run_id.replace("-", "").isalnum() or not path.exists():
        return {"ok": False, "error": f"no run {run_id!r}"}
    return {"ok": True, "report": json.loads(path.read_text(encoding="utf-8"))}
