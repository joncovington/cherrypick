"""Replay a raised completion limit on the live order path's own quotes (`flies.completion_escalation`).

    python scripts/flies_completion_replay.py                    # every quoted settled live session
    python scripts/flies_completion_replay.py --since 2026-10-05
    python scripts/flies_completion_replay.py --json out.json    # also write the full result

Read-only over the live ledger. Asked 2026-10-07: may an unfilled completion pay up to +0.25 or
+0.50 above its live limit once it looks stranded? Each trigger is shown at both caps side by side.
The answer is expected to firm up only as quoted sessions accumulate (from 2026-10-02 on), so this
is meant to be re-run, not read once.

**The baseline must reproduce live before anything is reported.** At the live limit, every position
must complete or strand as it really did, and a completed one must pay the debit live paid. A
mismatch means the fill rule or the path is wrong for that row, and the script stops rather than
compare policies against a baseline that is not live.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

from cherrypick.core.db import connect_ro
from cherrypick.flies import completion_escalation as ce
from cherrypick.flies import db as dbmod


def load(conn, since: str | None) -> list[dict]:
    """Settled live positions with a quoted completion path, each with its path and live timing."""
    clause = "AND p.trade_date >= ?" if since else ""
    params = [since] if since else []
    positions = [
        dict(r)
        for r in conn.execute(
            "SELECT p.* FROM fly_positions p WHERE p.status = 'settled' "
            "AND p.kind IN ('fly', 'short_vertical') "
            f"AND p.settlement_price IS NOT NULL {clause} AND EXISTS (SELECT 1 FROM fly_order_path o "
            "WHERE o.position_id = p.position_id AND o.leg = 'completion' AND o.buy_bid IS NOT NULL) "
            "ORDER BY p.trade_date, p.entry_time",
            params,
        ).fetchall()
    ]
    out = []
    for p in positions:
        path = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM fly_order_path WHERE position_id = ? AND leg = 'completion' "
                "ORDER BY observed_at",
                (p["position_id"],),
            ).fetchall()
        ]
        orders = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM fly_live_orders WHERE position_id = ? AND leg = 'completion' "
                "ORDER BY placed_at",
                (p["position_id"],),
            ).fetchall()
        ]
        filled = next((o for o in orders if o.get("outcome") == "filled"), None)
        out.append(
            {
                "position": p,
                "path": path,
                "placed_at": orders[0]["placed_at"] if orders else path[0]["observed_at"],
                "live_filled_at": (filled.get("broker_filled_at") or filled.get("resolved_at"))
                if filled
                else None,
                "live_paid": (filled.get("broker_fill_price") or filled.get("limit_price"))
                if filled
                else None,
            }
        )
    return out


_REFUSAL = re.compile(r"\$([0-9.]+) > cap \$([0-9.]+)")


def load_admissions(conn, paper_conn, items: list[dict]) -> dict:
    """Per session: the margin refusals live logged, paper control's entries, the centres live traded,
    and whether every live position that session is in the replay (a session missing one cannot
    re-total its exposure, so its refusals are not judged)."""
    out = {}
    for day in sorted({it["position"]["trade_date"] for it in items}):
        live_ids = {
            r["position_id"]
            for r in conn.execute(
                "SELECT position_id FROM fly_positions WHERE trade_date = ? AND status = 'settled' "
                "AND kind IN ('fly', 'short_vertical')",
                (day,),
            ).fetchall()
        }
        replayed = {it["position"]["position_id"] for it in items if it["position"]["trade_date"] == day}
        blocks = []
        for r in conn.execute(
            "SELECT first_seen, last_seen, center_first, detail FROM fly_decisions "
            "WHERE trade_date = ? AND reason = 'max_open_margin_reached' ORDER BY first_seen",
            (day,),
        ).fetchall():
            m = _REFUSAL.search(r["detail"] or "")
            if m:
                blocks.append(
                    {
                        "first_seen": r["first_seen"],
                        "last_seen": r["last_seen"],
                        "center": r["center_first"],
                        "would_be": float(m.group(1)),
                        "cap": float(m.group(2)),
                    }
                )
        paper = [
            dict(r)
            for r in paper_conn.execute(
                "SELECT * FROM fly_positions WHERE trade_date = ? AND arm = 'control' AND status = 'settled'",
                (day,),
            ).fetchall()
        ]
        taken = {it["position"]["center"] for it in items if it["position"]["trade_date"] == day}
        out[day] = {"complete": live_ids == replayed, "blocks": blocks, "paper": paper, "taken": taken}
    return out


def admissions(items: list[dict], base_rows: list[dict], pol_rows: list[dict], ctx: dict) -> dict:
    """One policy's admitted entries over every judged session."""
    admitted, skipped_sessions = [], []
    for day, c in ctx.items():
        if not c["blocks"]:
            continue
        if not c["complete"]:
            skipped_sessions.append(day)
            continue
        day_rows = [
            {"position": it["position"], "base": b["outcome"], "policy": p["outcome"]}
            for it, b, p in zip(items, base_rows, pol_rows, strict=True)
            if it["position"]["trade_date"] == day
        ]
        for a in ce.admit_blocked(c["blocks"], day_rows, c["paper"], taken_centers=c["taken"]):
            admitted.append({"trade_date": day, **{k: v for k, v in a.items() if k != "paper"}})
    scored = [a for a in admitted if a["paper_pnl"] is not None]
    return {
        "admitted": len(admitted),
        "paper_scored": len(scored),
        "unscored": len(admitted) - len(scored),
        "paper_pnl": round(sum(a["paper_pnl"] for a in scored), 2),
        "entries": admitted,
        "sessions_not_judged": skipped_sessions,
    }


def run(items: list[dict], ctx: dict | None = None) -> dict:
    grid = ce.policies()
    results: dict[str, list[dict]] = {}
    for pol in grid:
        rows = []
        for it in items:
            outcome = ce.simulate(
                it["position"],
                it["path"],
                pol,
                placed_at=it["placed_at"],
                live_filled_at=it["live_filled_at"],
            )
            rows.append(
                {
                    "trade_date": it["position"]["trade_date"],
                    "position_id": it["position"]["position_id"],
                    "outcome": outcome,
                    "settled": ce.settle(it["position"], outcome),
                }
            )
        results[pol["name"]] = rows

    mismatches = []
    for it, b in zip(items, results["baseline"], strict=True):
        live_completed = it["position"]["kind"] == "fly"
        problem = None
        if b["outcome"]["completed"] != live_completed:
            problem = f"baseline {'completed' if b['outcome']['completed'] else 'stranded'}, live did not"
        elif live_completed and abs((b["outcome"]["paid"] or 0) - (it["live_paid"] or 0)) > 0.005:
            problem = f"baseline paid {b['outcome']['paid']}, live paid {it['live_paid']}"
        if problem:
            mismatches.append({"position_id": it["position"]["position_id"], "problem": problem})

    summary = [ce.compare(results[p["name"]], p, results["baseline"]) for p in grid]
    if ctx is not None:
        for s, pol in zip(summary, grid, strict=True):
            s["admissions"] = admissions(items, results["baseline"], results[pol["name"]], ctx)
        if summary[0]["admissions"]["admitted"]:
            mismatches.append(
                {
                    "position_id": "-",
                    "problem": "the baseline let in a refused entry; the margin re-total is wrong",
                }
            )
    return {
        "positions": len(items),
        "sessions": sorted({it["position"]["trade_date"] for it in items}),
        "strands_live": sum(1 for it in items if it["position"]["kind"] == "short_vertical"),
        "baseline_mismatches": mismatches,
        "via_live_fill": sum(1 for b in results["baseline"] if b["outcome"]["via"] == "live_fill"),
        "summary": summary,
        "rows": results,
    }


def _cell(s: dict) -> str:
    return f"{s['delta']:+9.2f}  {s['rescued']:2d} / {s['overpaid']:2d}  {s['negative_floors']:2d}"


def _admit_cell(s: dict) -> str:
    a = s.get("admissions")
    if not a or not a["admitted"]:
        return "-"
    extra = f" ({a['unscored']} unscored)" if a["unscored"] else ""
    return f"+{a['admitted']} entries, paper {a['paper_pnl']:+.2f}{extra}"


def report(res: dict) -> None:
    print(
        f"{res['positions']} positions, {res['strands_live']} live strands, "
        f"sessions {', '.join(res['sessions'])}"
    )
    base = res["summary"][0]
    print(
        f"baseline P&L {base['pnl']:+.2f} (modelled fees); "
        f"{res['via_live_fill']} baseline fills fell back to live's moment"
    )
    print("\ncell: P&L change vs baseline   rescued / overpaid   fills with floor < 0")
    caps = sorted({s["cap"] for s in res["summary"] if s["cap"]})
    print(f"{'trigger':16s}" + "".join(f"   cap +{c:.2f}{'':18s}" for c in caps))
    families = []
    for s in res["summary"][1:]:
        if s["family"] not in families:
            families.append(s["family"])
    for fam in families:
        cells = [next(s for s in res["summary"] if s["family"] == fam and s["cap"] == c) for c in caps]
        print(f"{fam:16s}" + "".join(f"   {_cell(c):28s}" for c in cells))

    if "admissions" not in base:
        return
    print("\nentries the freed margin would have let in -- PAPER-SCORED, optimistic, never added above")
    print(f"{'trigger':16s}" + "".join(f"   cap +{c:.2f}{'':26s}" for c in caps))
    for fam in families:
        cells = [next(s for s in res["summary"] if s["family"] == fam and s["cap"] == c) for c in caps]
        print(f"{fam:16s}" + "".join(f"   {_admit_cell(c):34s}" for c in cells))
    skipped = sorted({d for s in res["summary"] for d in s["admissions"]["sessions_not_judged"]})
    if skipped:
        print(f"refusals not judged (a live position there has no quoted path): {', '.join(skipped)}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--since", help="first session (YYYY-MM-DD)")
    ap.add_argument("--json", help="also write the full result here")
    args = ap.parse_args(argv)

    conn = connect_ro(dbmod.live_db_path())
    items = load(conn, args.since)
    if not items:
        print("no settled live positions with a quoted completion path")
        return 1
    paper_conn = connect_ro(dbmod.default_db_path())
    res = run(items, load_admissions(conn, paper_conn, items))
    if res["baseline_mismatches"]:
        print("REFUSED: the baseline does not reproduce live, so no policy is compared against it")
        for m in res["baseline_mismatches"]:
            print(f"  {m['position_id']}: {m['problem']}")
        return 2
    report(res)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(res, fh, indent=2, default=str)
        print(f"\nwritten: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
