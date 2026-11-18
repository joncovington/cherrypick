"""How far the full set (`fullset`) has got: overall, per part and per underlying, against the request
files `packages/backtest/scripts/fetch_full_set.py plan` wrote. Read by that script's `status` and by
the console's Data tab (`read fullset`).

Counts only, read-only: each store's own request ledger is read through a read-only connection and
compared with the plan. A request is finished once its store records it complete or empty (the vendor
held nothing); a refused one is asked again by the chain's second pass. Nothing is fetched or written.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from . import fullset, paths

STUDY = "fullset"
FINISHED = ("complete", "empty")
PARTS = ("zero", "eod", "oi", "near", "mid")
INTRADAY_LEDGER = (
    'SELECT symbol, session, expiration, start, "end", interval, status FROM intraday_chain_requests'
)


def folder() -> Path:
    """Where `plan` writes the request files (the set's result folder)."""
    return paths.results_dir() / "research" / STUDY


def _rows(path: Path, sql: str) -> list[tuple]:
    """A read-only query of a store; nothing when it is absent or not yet that shape."""
    if not path.exists():
        return []
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return conn.execute(sql).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def part(done: int, total: int, refused: int = 0) -> dict:
    return {"done": done, "total": total, "refused": refused,
            "share": round(done / total, 4) if total else None}  # fmt: skip


def tally(planned: list[tuple], ledger: list[tuple]) -> dict:
    """How many planned keys the ledger records finished, and refused. `ledger` rows are the key's
    fields then the status. Pure."""
    status = {tuple(r[:-1]): r[-1] for r in ledger}
    done = sum(status.get(k) in FINISHED for k in planned)
    refused = sum(status.get(k) == "refused" for k in planned)
    return part(done, len(planned), refused)


def roll_up(parts: dict[str, dict]) -> dict:
    """One total over several parts (each counted by its own requests or sessions). Pure."""
    done = sum(p["done"] for p in parts.values())
    total = sum(p["total"] for p in parts.values())
    return part(done, total, sum(p["refused"] for p in parts.values()))


def _load(where: Path, name: str) -> dict | None:
    p = where / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _added(where: Path) -> dict:
    """The additions recorded in the set's folder (`fullsetadd`'s file), read without importing it."""
    return _load(where, "additions.json") or {}


def progress(vendor: Path, where: Path | None = None) -> dict:
    """Overall, per underlying, and per part within each, against the planned files in `where` (the
    set's folder by default). `planned` is false until `plan` has written them. Counts only.
    The same-day part has no request file (the fetch lists the sessions an expiry fell on), so its
    total is the sessions in the span, a ceiling for underlyings without an expiry every day."""
    where = folder() if where is None else where
    span = _load(where, "oi_plan.json")
    planned = span is not None
    days = sorted((span or {"sessions": {}})["sessions"])
    paths_doc = _load(where, "paths.json") or {"requests": []}
    eod_ledger = _rows(vendor / "eod_chains.db",
                       "SELECT symbol, expiration, start, through, status FROM eod_chain_paths")  # fmt: skip
    oi_ledger = _rows(vendor / "open_interest.db", "SELECT symbol, session, status FROM oi_days")
    added = sorted(
        s
        for s in _added(where)
        if fullset.SYMBOL.match(s) and s not in fullset.SPEC and (where / f"oi_{s}.json").exists()
    )
    per: dict[str, dict] = {}
    for sym in (*fullset.SYMBOLS, *added):
        spec = fullset.spec_for(sym)
        own_days, own_paths = days, paths_doc
        if sym not in fullset.SPEC:  # an addition plans its own span and paths (`fullset.plan_added`)
            own_days = sorted((_load(where, f"oi_{sym}.json") or {"sessions": {}})["sessions"])
            own_paths = _load(where, f"paths_{sym}.json") or {"requests": []}
        parts: dict[str, dict] = {}
        # One file per underlying of the seven; the shared added store keys each row by its symbol.
        only = "" if sym in fullset.SPEC else f" WHERE symbol = '{sym}'"
        zero = _rows(vendor / fullset.zero_store(sym), f"SELECT trade_date, status FROM vendor_sessions{only}")
        parts["zero"] = tally([(d,) for d in own_days], zero)
        planned_eod = [(r["symbol"], r["expiration"], r["start"], r["through"])
                       for r in own_paths["requests"] if r["symbol"] in spec["eod"]]  # fmt: skip
        parts["eod"] = tally(planned_eod, eod_ledger)
        parts["oi"] = tally([(spec["root"], d) for d in own_days], oi_ledger)
        for name, store in (("near", fullset.near_store(sym)), ("mid", fullset.mid_store(sym))):
            doc = _load(where, f"{name}_{sym}.json") or {"requests": []}
            keys = [(r["symbol"], r["session"], r["expiration"], r["start"], r["end"], r["interval"])
                    for r in doc["requests"]]  # fmt: skip
            parts[name] = tally(keys, _rows(vendor / store, INTRADAY_LEDGER))
        per[sym] = {"all": roll_up(parts), **parts}
    by_part = {p: roll_up({s: per[s][p] for s in per}) for p in PARTS}
    return {"planned": planned, "overall": roll_up(by_part), "by_part": by_part, "by_symbol": per,
            "from": days[0] if days else None, "through": days[-1] if days else None}  # fmt: skip
