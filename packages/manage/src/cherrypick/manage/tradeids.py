"""Stable trade IDs: one per trade for its whole life, whatever else happens on the name.

The evaluation used to label a name's trades `XYZ`, `XYZ#1`, `XYZ#2` by their place in that day's list,
so a new trade on the name renumbered the rest and nothing could be keyed on a label across days. This
assigns each trade an ID once and keeps it:

- **Format** `<UNDERLYING>-<YYMMDD>-<n>`: the name, the day the trade's order chain opened, and `n` to
  tell apart trades opened on the name that day (`XYZ-260401-1`).
- **Kept by its orders.** The store (`trade-ids.json` in manage's data folder) maps each ID to every
  order its chain has held. A trade takes the ID of a stored one its orders overlap, so a roll (a new
  order on the same chain) keeps the ID, and the new order is added to it.
- **Kept by its legs when its orders cannot be read.** The broker history manage reads is bounded
  (400 days a name), and a chain whose opening has left it no longer reconciles. A trade without a
  chain takes the ID of the stored one whose last-seen legs are exactly its legs.
- **Merged, never split.** A trade whose orders overlap two stored IDs (a join made one trade of two)
  keeps the older and records the other as merged into it; the evaluation says so on the trade.
- **Provisional otherwise.** A trade with neither a chain nor a match gets `<UNDERLYING>-open-<hash>`
  of its legs, marked provisional and never stored, until its orders are known.

**Deterministic.** Each snapshot carries the store as it stood (`snapshot.trade_ids`), and new IDs are
numbered in the evaluation's own order, so a re-run or a minute's re-mark of a snapshot assigns the
same IDs, and the console's live marks join the evaluation by them. Only the full-snapshot run writes
the store, adding to it, never rewriting an ID.

Pure apart from `load` and `record`, which read and write the one store file.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from cherrypick.core.jsonio import write_json_atomic

ID_RE = re.compile(r"^[A-Z0-9./]+-\d{6}-\d+$")


def is_trade_id(value: Any) -> bool:
    return bool(ID_RE.match(str(value or "")))


def _legs_key(legs: list[dict[str, Any]]) -> list[str]:
    return sorted({" ".join(str(leg.get("symbol") or "").split()) for leg in legs if leg.get("symbol")})


def _order_ids(chain: dict[str, Any] | None) -> list[str]:
    return [str(o["order_id"]) for o in ((chain or {}).get("orders") or []) if o.get("order_id") is not None]


class Assigner:
    """Assigns one evaluation's trade IDs against the store as the snapshot carried it."""

    def __init__(self, known: dict[str, Any] | None):
        self.known: dict[str, dict[str, Any]] = {
            k: v
            for k, v in ((known or {}).get("ids") or {}).items()
            if isinstance(v, dict) and is_trade_id(k)
        }
        self.by_order: dict[str, str] = {}
        for tid, entry in self.known.items():
            for o in entry.get("orders") or []:
                self.by_order.setdefault(str(o), tid)
        self.taken: set[str] = set(self.known)
        self.assigned: set[str] = set()

    def _live(self, tid: str) -> str:
        """Follow merges to the ID that holds them now."""
        seen = set()
        while tid in self.known and self.known[tid].get("merged_into") and tid not in seen:
            seen.add(tid)
            tid = str(self.known[tid]["merged_into"])
        return tid

    def _older(self, ids: list[str]) -> list[str]:
        return sorted(ids, key=lambda t: (str((self.known.get(t) or {}).get("opened") or "9999"), t))

    def assign(
        self, underlying: str, legs: list[dict[str, Any]], chain: dict[str, Any] | None
    ) -> dict[str, Any]:
        """{trade_id, provisional, merged (IDs folded into this one), orders, opened, legs} for one trade."""
        orders = _order_ids(chain)
        legs_key = _legs_key(legs)
        opened = str(((chain or {}).get("orders") or [{}])[0].get("executed_at") or "")[:10] or None
        hits = sorted({self._live(self.by_order[o]) for o in orders if o in self.by_order} - self.assigned)
        if not hits and not orders:
            same = [
                t
                for t, e in self.known.items()
                if not e.get("merged_into")
                and e.get("underlying") == underlying
                and (e.get("legs") or []) == legs_key
                and t not in self.assigned
            ]
            hits = self._older(same)[:1]
        if hits:
            ordered = self._older(hits)
            tid, merged = ordered[0], ordered[1:]
            opened = (self.known.get(tid) or {}).get("opened") or opened
            provisional = False
        elif orders and opened:
            stem = f"{underlying.upper()}-{opened[2:4]}{opened[5:7]}{opened[8:10]}"
            n = 1
            while f"{stem}-{n}" in self.taken:
                n += 1
            tid, merged, provisional = f"{stem}-{n}", [], False
        else:
            digest = hashlib.sha1("|".join(legs_key).encode("utf-8")).hexdigest()[:6]
            tid, merged, provisional = f"{underlying.upper()}-open-{digest}", [], True
        self.taken.add(tid)
        self.assigned.add(tid)
        for o in orders:
            self.by_order[o] = tid
        return {
            "trade_id": tid,
            "provisional": provisional,
            "merged": merged,
            "orders": orders,
            "opened": opened,
            "legs": legs_key,
        }


def updated(known: dict[str, Any] | None, trades: list[dict[str, Any]], session: str) -> dict[str, Any]:
    """The store after one full evaluation: every non-provisional trade's ID with its orders added and its
    legs as seen today, and every ID a trade merged marked so. Never removes or renames an ID."""
    ids = {k: dict(v) for k, v in ((known or {}).get("ids") or {}).items() if isinstance(v, dict)}
    for t in trades:
        ident = t.get("trade_ident") or {}
        tid = t.get("trade_id")
        if not tid or ident.get("provisional") or not is_trade_id(tid):
            continue
        entry = ids.get(tid) or {
            "underlying": t.get("underlying"),
            "opened": ident.get("opened"),
            "first_seen": session,
        }
        entry["orders"] = sorted(set(entry.get("orders") or []) | set(ident.get("orders") or []))
        entry["legs"] = ident.get("legs") or entry.get("legs") or []
        entry["last_seen"] = session
        ids[tid] = entry
        for old in ident.get("merged") or []:
            if old in ids and not ids[old].get("merged_into"):
                ids[old] = {**ids[old], "merged_into": tid, "merged_on": session}
    return {"ids": ids}


def load(path: Path) -> tuple[dict[str, Any], bool]:
    """(the store, readable). A missing store is empty; one that cannot be read is empty and unreadable."""
    if not path.exists():
        return {"ids": {}}, True
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ids": {}}, False
    return (doc if isinstance(doc, dict) else {"ids": {}}), isinstance(doc, dict)


def record(path: Path, known: dict[str, Any], trades: list[dict[str, Any]], session: str) -> bool:
    """Write the updated store; False (and nothing written) when the store on disk cannot be read, so a
    damaged file never loses the IDs it holds."""
    on_disk, readable = load(path)
    if not readable:
        return False
    # The file as it stands is the base, so an ID it holds is never lost; the snapshot's copy only
    # decided which IDs this evaluation took.
    base = {"ids": {**((known or {}).get("ids") or {}), **(on_disk.get("ids") or {})}}
    new = updated(base, trades, session)
    if new != base:
        write_json_atomic(path, new)
    return True
