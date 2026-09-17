#!/usr/bin/env python3
"""Move every historical advised row onto its experiment's own book tag (2026-09-17).

Until 2026-09-17 each module wrote every advisor experiment's rows to ONE book, `advised:<base>`
(`advised:control`, `advised:width-5`, earnings `advised:strat_test:<strategy>`), and experiments
on the same base reused that tag in turn, told apart only by the `experiment_id` stamp that
rows carry since 2026-09-16. From that date each experiment has its own book, `advised:<name>`
(the advisor's `experiments.tag`), so this script re-keys history to match: a row stamped with an
experiment moves to that experiment's tag exactly; an unstamped row is attributed to the one
experiment the advisor issued an artifact for on that module and session with that base -- read
off the advisor's own `enacted` journal, which names the target session of every artifact it
ever wrote -- and gets the stamp as well. A row two experiments could claim is left alone and
reported: an inference that could be wrong is worse than a legacy tag the readers still accept.

Lives here, outside every package, because it WRITES seven other packages' ledgers and the
advisor package is read-only over all of them by contract. One-off: idempotent (a row already on
its experiment's tag is not touched), dry-run by default, `--apply` backs each ledger up
(`<db>.bak-pre-retag-<stamp>`) before the first write to it. Run it with the paper loops idle --
outside RTH -- so no writer holds the ledger while a tag is rewritten.

    python scripts/retag_advised_books.py            # report only
    python scripts/retag_advised_books.py --apply    # rewrite, with backups
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.core import advice as _advice
from cherrypick.core import home as _home

ET = ZoneInfo("America/New_York")
ADVISED = "advised:"

# (module, table, tag column, how to read the row's session, has experiment_id column?) -- the
# tables the survey of every paper ledger on 2026-09-17 found carrying an advised tag. A table not
# listed here is reported by `--survey` rather than silently skipped.
TABLES: dict[str, list[dict]] = {
    "meic": [
        {"table": "ic_trades", "tag": "risk_profile", "session": "trade_date"},
        {"table": "entry_attempts", "tag": "risk_profile", "session": "trade_date"},
    ],
    "flies": [
        {"table": "fly_positions", "tag": "arm", "session": "trade_date", "book_id": True},
        {"table": "fly_books", "tag": "arm", "session": "trade_date", "book_id": True},
        {"table": "fly_decisions", "tag": "arm", "session": "trade_date"},
        {"table": "fly_iterations", "tag": "arm", "session": "trade_date"},
        {"table": "fly_entry_attempts", "tag": "arm", "session": "trade_date"},
    ],
    "earnings": [
        {"table": "trades", "tag": "profile", "session": "opened_at:epoch"},
        {"table": "management_events", "tag": "profile", "session": "order_id:trades"},
    ],
    "calendars": [{"table": "dc_positions", "tag": "book", "session": "entry_session"}],
    "pmcc": [
        {"table": "pmcc_positions", "tag": "book", "session": "entry_session"},
        {"table": "pmcc_decisions", "tag": "book", "session": "trade_date"},
        {"table": "pmcc_entry_attempts", "tag": "book", "session": "trade_date"},
    ],
    "curve": [
        {"table": "curve_positions", "tag": "book", "session": "entry_session"},
        {"table": "curve_decisions", "tag": "book", "session": "trade_date"},
        {"table": "curve_entry_attempts", "tag": "book", "session": "trade_date"},
    ],
    "bwb": [
        {"table": "bwb_positions", "tag": "book", "session": "entry_session"},
        {"table": "bwb_decisions", "tag": "book", "session": "trade_date"},
        {"table": "bwb_entry_attempts", "tag": "book", "session": "trade_date"},
    ],
}


def _paper_db(module: str) -> Path:
    return _home.data_dir() / module / "paper_trades.db"


def _advisor_db() -> Path:
    return _home.data_dir() / "advisor" / "advisor.db"


def load_mapping(conn: sqlite3.Connection) -> tuple[dict, dict]:
    """experiments by id, and {(module, session): [experiment ids issued for it, in issue order]}."""
    conn.row_factory = sqlite3.Row
    experiments = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM experiments")}
    issued: dict[tuple[str, str], list[str]] = defaultdict(list)
    rows = conn.execute(
        "SELECT experiment_id, event, detail_json FROM experiment_events"
        " WHERE event IN ('enacted', 'retracted') ORDER BY id"
    )
    for r in rows:
        detail = json.loads(r["detail_json"] or "{}")
        target = detail.get("target")
        exp = experiments.get(r["experiment_id"])
        if not target or exp is None:
            continue
        key = (exp["module"], target)
        if r["event"] == "enacted" and detail.get("written"):
            if r["experiment_id"] not in issued[key]:
                issued[key].append(r["experiment_id"])
        elif r["event"] == "retracted" and r["experiment_id"] in issued[key]:
            issued[key].remove(r["experiment_id"])
    return experiments, issued


def legacy_base(tag: str, module: str) -> tuple[str, str | None]:
    """`advised:control` -> ('control', None); earnings `advised:strat_test:iron_fly` ->
    ('strat_test', 'iron_fly')."""
    rest = tag[len(ADVISED) :]
    if module == "earnings" and ":" in rest:
        base, _, strategy = rest.partition(":")
        return base, strategy
    return rest, None


def new_tag(exp: dict, strategy: str | None) -> str:
    tag = exp.get("tag") or (
        _advice.advised_tag(exp["name"]) if exp.get("name") else f"{ADVISED}{exp['base_profile']}"
    )
    return f"{tag}:{strategy}" if strategy else tag


def resolve(module: str, tag: str, session: str | None, stamped: str | None, experiments: dict, issued: dict):
    """(experiment, strategy, how) or (None, strategy, why-not)."""
    base, strategy = legacy_base(tag, module)
    if stamped and stamped in experiments:
        return experiments[stamped], strategy, "stamped"
    if not session:
        return None, strategy, "no session"
    candidates = [
        experiments[e]
        for e in issued.get((module, session), [])
        if (experiments[e].get("base_profile") or "control") == base
    ]
    if len(candidates) == 1:
        return candidates[0], strategy, "inferred"
    if not candidates:
        return None, strategy, "no experiment issued for that session and base"
    # A handoff day: the old experiment's artifact was issued in the evening pass, then a kill
    # re-issued the successor's for the same session over it. One file, last writer wins -- the
    # loop read the successor's, and every row that IS stamped on such a day confirms it.
    return (
        candidates[-1],
        strategy,
        "inferred (last issued of " + ", ".join(c["id"] for c in candidates) + ")",
    )


def _session_of(row: sqlite3.Row, spec: dict, lookup: dict | None) -> str | None:
    how = spec["session"]
    if how == "opened_at:epoch":
        v = row["opened_at"]
        try:
            return datetime.fromtimestamp(float(v), ET).date().isoformat()
        except (TypeError, ValueError):
            return None
    if how == "order_id:trades":
        return (lookup or {}).get(row["order_id"])
    v = row[how]
    return str(v)[:10] if v else None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def plan_module(module: str, experiments: dict, issued: dict) -> list[dict]:
    db = _paper_db(module)
    if not db.exists():
        return []
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    changes: list[dict] = []
    trades_session: dict | None = None
    if module == "earnings":
        # management_events carry no date: the trade they belong to does.
        trades_session = {}
        for r in conn.execute("SELECT order_id, opened_at FROM trades WHERE profile LIKE 'advised:%'"):
            try:
                trades_session[r["order_id"]] = (
                    datetime.fromtimestamp(float(r["opened_at"]), ET).date().isoformat()
                )
            except (TypeError, ValueError):
                pass
    for spec in TABLES.get(module, []):
        table, col = spec["table"], spec["tag"]
        cols = _columns(conn, table)
        if not cols or col not in cols:
            continue
        has_stamp = "experiment_id" in cols
        key = "id" if "id" in cols else "rowid"
        sel = (
            f"SELECT {key} AS _k, {col} AS _tag"
            + (", experiment_id" if has_stamp else "")
            + ", * FROM {t} WHERE {c} LIKE 'advised:%'"
        )
        for row in conn.execute(sel.format(t=table, c=col)):
            tag = row["_tag"]
            session = _session_of(row, spec, trades_session)
            exp, strategy, how = resolve(
                module, tag, session, row["experiment_id"] if has_stamp else None, experiments, issued
            )
            if exp is None:
                changes.append(
                    {
                        "module": module,
                        "table": table,
                        "key": row["_k"],
                        "old": tag,
                        "new": None,
                        "why": how,
                        "session": session,
                    }
                )
                continue
            target = new_tag(exp, strategy)
            if target == tag and (not has_stamp or row["experiment_id"]):
                continue  # already on its book and stamped
            change = {
                "module": module,
                "table": table,
                "key": row["_k"],
                "keycol": key,
                "col": col,
                "old": tag,
                "new": target,
                "why": how,
                "session": session,
                "experiment_id": exp["id"],
                "stamp": has_stamp and not row["experiment_id"],
            }
            if spec.get("book_id") and "book_id" in cols and row["book_id"]:
                change["book_id"] = (
                    row["book_id"],
                    str(row["book_id"]).replace(f":{tag}:", f":{target}:", 1),
                )
            changes.append(change)
    conn.close()
    return changes


def apply_module(module: str, changes: list[dict], stamp: str) -> int:
    db = _paper_db(module)
    todo = [c for c in changes if c.get("new")]
    if not todo:
        return 0
    backup = db.with_name(db.name + f".bak-pre-retag-{stamp}")
    if not backup.exists():
        shutil.copy2(db, backup)
        for suffix in ("-wal", "-shm"):
            side = db.with_name(db.name + suffix)
            if side.exists():
                shutil.copy2(side, backup.with_name(backup.name + suffix))
    conn = sqlite3.connect(db, timeout=30)
    try:
        conn.execute("BEGIN")
        for c in todo:
            sets = [f"{c['col']} = ?"]
            args: list = [c["new"]]
            if c.get("stamp"):
                sets.append("experiment_id = ?")
                args.append(c["experiment_id"])
            if c.get("book_id"):
                sets.append("book_id = ?")
                args.append(c["book_id"][1])
            args.append(c["key"])
            conn.execute(f"UPDATE {c['table']} SET {', '.join(sets)} WHERE {c['keycol']} = ?", args)
        conn.commit()
    finally:
        conn.close()
    return len(todo)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="rewrite the ledgers (default: report only)")
    ap.add_argument("--module", action="append", help="limit to one or more modules")
    ap.add_argument("--verbose", action="store_true", help="list every unresolved row")
    args = ap.parse_args(argv)

    adv = sqlite3.connect(f"file:{_advisor_db()}?mode=ro", uri=True)
    experiments, issued = load_mapping(adv)
    adv.close()
    missing = [e["id"] for e in experiments.values() if not e.get("tag")]
    if missing:
        print(
            f"refusing: {len(missing)} experiment(s) carry no tag yet -- open advisor.db once (store.connect backfills)"
        )
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    summary = {}
    for module in args.module or list(TABLES):
        changes = plan_module(module, experiments, issued)
        per_table: dict[str, dict] = defaultdict(
            lambda: {"retag": 0, "stamp": 0, "unresolved": 0, "book_id": 0}
        )
        by_target: dict[str, int] = defaultdict(int)
        for c in changes:
            t = per_table[c["table"]]
            if c.get("new"):
                t["retag"] += 1
                by_target[f"{c['old']} -> {c['new']}"] += 1
                if c.get("stamp"):
                    t["stamp"] += 1
                if c.get("book_id"):
                    t["book_id"] += 1
            else:
                t["unresolved"] += 1
                if args.verbose:
                    print(
                        f"  unresolved {module}.{c['table']}#{c['key']} {c['old']} session={c['session']}: {c['why']}"
                    )
        applied = apply_module(module, changes, stamp) if args.apply else 0
        summary[module] = {"tables": dict(per_table), "moves": dict(by_target), "applied": applied}
    print(json.dumps({"ok": True, "applied": args.apply, "modules": summary}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
