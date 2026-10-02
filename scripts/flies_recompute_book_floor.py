"""Recompute every settled flies book's floor under the corrected expiry-fee rule (2026-09-24).

    python scripts/flies_recompute_book_floor.py            # dry run -- prints, changes nothing
    python scripts/flies_recompute_book_floor.py --apply    # backs up each ledger, then rewrites

Runnable from anywhere; ledgers resolve off `$CHERRYPICK_HOME` (default `~/.cherrypick`).

**What was wrong.** Settlement folds the expiry fee its real price charged into each position's
`fees`, and `fly.position_pnl` trusted that figure at every price, so a settled book's `worst` paired
a hypothetical price with the real settlement's fees. 2026-09-24's live control recorded -$224.11,
a combination no single price produces; the book's true worst is -$223.11. Fixed in d6f814a8, which
only reached books settled after it. This brings the rest into line.

**What it rewrites, and what it leaves.** Only the floor columns of settled `fly_books` rows --
`worst`, `worst_at`, `floor_holds`, `band_low`, `band_high`, `unbounded_below` -- recomputed exactly
as `book.settle_book` computes them now: `fly.book_floor` over `fly.held(...)` of the book's rows,
each marked settled. No position row is touched, and `pnl` cannot move: the correction cancels at
the settlement price, so the book pnl under the old and the new rule must agree, which this script
checks per book and refuses to write through if it fails.

**Measured on the 2026-09-24 ledgers.** Paper: 248 settled books; recomputing under the OLD rule
reproduces every stored `worst` exactly, so each change is this fix alone -- 196 books move, three
flip `floor_holds` (2026-08-04 control by $240: a many-position book's worst price leaves dozens of
strikes in the money at $5 each, where the old rule reused the few its real settlement charged).
Live: 6 of 9 books were stored BEFORE broker fee reconciliation rewrote their positions' fees, so
their recompute absorbs that too; the stored book `pnl` on such rows is stale against its own
positions, reported here and not rewritten.

Run it with the flies loops idle (after the session's settlement); each ledger is copied through
SQLite's backup API to `<ledger>.bak-pre-floor-recompute-<stamp>` before anything is written.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from cherrypick.flies import book as bookmod
from cherrypick.flies import cli as climod
from cherrypick.flies import db as dbmod
from cherrypick.flies import engine, fly

COLUMNS = ("worst", "worst_at", "floor_holds", "band_low", "band_high", "unbounded_below")


def _ledgers() -> list[Path]:
    home = os.environ.get("CHERRYPICK_HOME")
    flies = (Path(home) if home else Path.home() / ".cherrypick") / "data" / "flies"
    return [p for p in (flies / "paper_trades.db", flies / "live_trades.db") if p.exists()]


def _recompute(conn: sqlite3.Connection, book: sqlite3.Row, config: dict) -> tuple[dict, float, float]:
    """The floor columns `settle_book` would write for this book today, and its book pnl at the
    settlement price under the new and the old fee rule (which must agree)."""
    final = [bookmod._to_position(r) for r in fly.held(dbmod.book_positions(conn, book["book_id"]))]
    for p in final:
        p["status"] = "settled"
    params = engine.merged_params(config, book["arm"])
    floor = fly.book_floor(final, step=params.get("book_scan_step", 1.0))
    band = floor["band"] or (None, None)
    new = {
        "worst": floor["worst"],
        "worst_at": floor["worst_at"],
        "floor_holds": int(floor["floor_holds"]),
        "band_low": band[0],
        "band_high": band[1],
        "unbounded_below": int(floor["unbounded_below"]),
    }
    old_rule = [{**p, "settlement_price": None} for p in final]
    sp = book["settlement_price"]
    return new, round(fly.book_pnl(final, sp), 2), round(fly.book_pnl(old_rule, sp), 2)


def _same(a, b) -> bool:
    if a is None or b is None:
        return a is b
    return abs(float(a) - float(b)) < 0.005


def run(path: Path, config: dict, apply: bool) -> dict:
    conn = dbmod.connect(str(path))
    conn.row_factory = sqlite3.Row
    books = conn.execute(
        "SELECT * FROM fly_books WHERE status = 'settled' AND settlement_price IS NOT NULL"
        " ORDER BY trade_date, arm"
    ).fetchall()
    changes, pnl_drift, stale_pnl, holds_flips, band_moves = [], [], [], [], []
    for b in books:
        new, pnl, pnl_old_rule = _recompute(conn, b, config)
        if not _same(pnl, pnl_old_rule):
            pnl_drift.append((b["book_id"], pnl_old_rule, pnl))
        if b["pnl"] is not None and not _same(pnl, b["pnl"]):
            stale_pnl.append((b["book_id"], b["pnl"], pnl))
        diff = {c: (b[c], new[c]) for c in COLUMNS if not _same(b[c], new[c])}
        if diff:
            changes.append((b["book_id"], diff))
            if "floor_holds" in diff:
                holds_flips.append(b["book_id"])
            if "band_low" in diff or "band_high" in diff:
                band_moves.append(b["book_id"])
            if apply:
                sets = ", ".join(f"{c} = ?" for c in diff)
                conn.execute(
                    f"UPDATE fly_books SET {sets} WHERE book_id = ?", [new[c] for c in diff] + [b["book_id"]]
                )
    if apply:
        conn.commit()
    conn.close()
    return {
        "books": len(books),
        "changes": changes,
        "pnl_drift": pnl_drift,
        "stale_pnl": stale_pnl,
        "holds_flips": holds_flips,
        "band_moves": band_moves,
    }


def _backup(path: Path) -> Path:
    dest = path.with_name(f"{path.name}.bak-pre-floor-recompute-{datetime.now():%Y%m%d-%H%M%S}")
    src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    out = sqlite3.connect(dest)
    src.backup(out)
    out.close()
    src.close()
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--apply", action="store_true", help="back up each ledger, then rewrite (default: dry run)"
    )
    args = ap.parse_args()
    config = climod.load_config(None)
    ledgers = _ledgers()
    if not ledgers:
        print("no flies ledgers found")
        return 1

    # Dry-run first, always: a book whose pnl would move means the rule is not what this script
    # assumes, and nothing is written for any ledger in that case.
    plans = {p: run(p, config, apply=False) for p in ledgers}
    for path, r in plans.items():
        worst_moves = [d["worst"] for _, d in r["changes"] if "worst" in d]
        print(f"{path.name}: {r['books']} settled books, {len(r['changes'])} change")
        print(
            f"  worst moves {len(worst_moves)}; floor_holds flips {len(r['holds_flips'])};"
            f" bands move {len(r['band_moves'])}"
        )
        if worst_moves:
            deltas = sorted(float(new) - float(old) for old, new in worst_moves)
            print(
                f"  worst delta: min {deltas[0]:+.2f}  median {deltas[len(deltas) // 2]:+.2f}"
                f"  max {deltas[-1]:+.2f}"
            )
        for bid in r["holds_flips"]:
            d = dict(r["changes"])[bid]
            print(
                f"  floor_holds flip {bid}: {d['floor_holds'][0]} -> {d['floor_holds'][1]}"
                f"  worst {d.get('worst')}"
            )
        for bid, stored, positions in r["stale_pnl"]:
            print(f"  note: {bid} stored book pnl {stored} vs its positions {positions} (not rewritten)")
        if r["pnl_drift"]:
            print(
                f"  REFUSING: {len(r['pnl_drift'])} book(s) whose pnl would move, e.g. {r['pnl_drift'][:3]}"
            )
    if any(r["pnl_drift"] for r in plans.values()):
        return 2
    if not args.apply:
        print("\ndry run -- nothing written. Re-run with --apply.")
        return 0
    for path in ledgers:
        print(f"backup {path.name} -> {_backup(path).name}")
        r = run(path, config, apply=True)
        print(f"  wrote {len(r['changes'])} book(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
