"""At each live cap refusal, would freeing a slot have beaten holding? A smoke test, not a rule.

    python scripts/flies_cap_swap_replay.py                         # every refusal since 2026-09-25
    python scripts/flies_cap_swap_replay.py --session 2026-10-02 --detail

Read-only; runnable from anywhere; both ledgers resolve off `$CHERRYPICK_HOME` (default
`~/.cherrypick`) and open `?mode=ro`. Prints JSON.

**The question.** Live has one sizing gate, `live.max_open_margin_dollars` ($1,000, about three open
spreads), and it refuses entries in runs (`fly_decisions` reason `max_open_margin_reached`; the
journal collapses identical consecutive refusals into one row with a count). Paper has no cap. At
a tick t inside each run, free a slot by acting on the stalest open, uncompleted live spread (oldest
`entry_time`, entered by t, not completed by t), one of two ways:

- **C1, force-complete.** Buy the completing spread at its natural (`buy_ask - sell_bid`) from the
  live `fly_order_path` row for that position's completion order nearest at or before t. The
  position becomes a fly with the net debit as its worst case, and is settled at its own
  `settlement_price` by `fly.position_pnl`, with the completion priced by `fly.vertical_open_fee`.
  The print's fee is what the row really paid there plus the module's own fee for any strike the
  completion adds, so a row that did complete, replayed at its own debit, returns its recorded
  `pnl` (checked on every settled live fly whose settlement fees were split out). Mid is reported
  beside it.
- **C2, abort.** Buy the spread back at `fly_live_marks.structure_mid` nearest at or before t, plus
  slippage and a two-leg close fee (`cherrypick.core.fees.ic_close_fee`, 2 legs / 1 sell leg). The
  marks carry no bid-ask, so `slippage_frac` of a spread cannot be applied; the slippage charged is
  the position's own `slippage_dollars`, live's measured mid-to-fill concession opening the same
  two legs. Rule 5's one exception was measured negative; this is here for contrast.

The freed slot's value is the **paper twin**: paper's entry for the same arm and symbol during the
run (its first to last tick, each widened by `TWIN_TOLERANCE_SECONDS` because the two loops tick on
their own clocks), nearest the first tick, and its settled `pnl`. t is the twin's entry, held inside
the run: the tick the freed slot would have been filled on. Hold is the live position's settled
`pnl`. Per option, swap = twin + acted-on position under that option - held.

**Rules.** One action per refusal run, not per tick: a run is one blocked opportunity. A position
acted on is not acted on again by a later run that session, nor is a paper entry counted twice. A
run with no paper twin is `unmatched`, never valued. A quote or mark older than
`QUOTE_TOLERANCE_SECONDS` before t (or none at all) leaves that option `unmeasured`, never
interpolated and never zero. The replay does not re-run the cap after a swap: later refusals
are taken as recorded, and the twin's own exposure is not charged against the cap.

**What the ledgers can answer today (2026-10-04).** Every `fly_order_path` row through 2026-10-02
is a backfilled spot trail with no quotes (docs/fill-model.md, "Backfill": there is no quote history
to recover), so C1 at natural is unmeasured on every refusal so far. What can be said is a bound:
a completion limit that rested UNFILLED at t means the natural was above it, so C1 valued at the
resting limit (`c1_bound`) is an upper bound on C1, never a measurement of it.

**Caveats.** Three sessions is a smoke test. The twin is paper's modelled fill standing in for a
live fill that never happened; paper and live complete by different rules and the gap between them
is what packages/flies/docs/fill-model.md exists to measure. Neither option becomes a rule unless
it pays net on the sessions it acted on, and a live completion-gate change would be
measurement-affecting, with a declared break and a judging rule written first.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.core import fees as core_fees
from cherrypick.core.regimecuts import robustness
from cherrypick.flies import fly

ET = ZoneInfo("America/New_York")
CAP_REASON = "max_open_margin%"
# The live loop observes a working order about once a minute and marks once a minute, so two
# minutes is one missed reading. Past that the price describes a different market.
QUOTE_TOLERANCE_SECONDS = 120.0
# The paper and live loops tick on their own clocks; within a minute is the same tick.
TWIN_TOLERANCE_SECONDS = 60.0
DEFAULT_SINCE = "2026-09-25"  # the cap became live's only sizing gate
CAVEAT = (
    "Smoke test over few sessions. The freed slot is valued by paper's twin entry, a modelled fill "
    "standing in for a live fill that never happened (packages/flies/docs/fill-model.md). C1 at "
    "natural needs order-path quotes, which the backfilled history does not have; c1_bound is an "
    "upper bound on C1, not a measurement."
)


def _ledger(name: str) -> Path:
    home = os.environ.get("CHERRYPICK_HOME")
    root = (Path(home) if home else Path.home() / ".cherrypick") / "data" / "flies"
    return root / f"{name}_trades.db"


def _open(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def ts(value) -> datetime | None:
    """A stored timestamp as an aware datetime. Every one this module writes is ET with offset; a
    naive one (older rows) is read as ET rather than as machine-local."""
    if not value:
        return None
    t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=ET)


# --------------------------------------------------------------------------- reads
def refusal_runs(live, arm: str, since: str, until: str | None) -> list[dict]:
    q = """select id, trade_date, arm, symbol, reason, first_seen, last_seen, occurrences,
                  center_first, center_last, detail
           from fly_decisions where reason like ? and accepted = 0 and arm = ?
           and trade_date >= ? and (? is null or trade_date <= ?) order by first_seen, id"""
    return [dict(r) for r in live.execute(q, (CAP_REASON, arm, since, until, until))]


def positions(conn, arm: str, day: str, symbol: str) -> list[dict]:
    q = """select * from fly_positions where arm = ? and trade_date = ? and symbol = ?
           and void_reason is null and entry_mode = 'legged' order by entry_time"""
    return fly.held([dict(r) for r in conn.execute(q, (arm, day, symbol))])


def open_spreads_at(rows: list[dict], t: datetime) -> list[dict]:
    """Live spreads held at t that had not completed by t, stalest first."""
    out = []
    for p in rows:
        entered = ts(p["entry_time"])
        done = ts(p["completed_at"])
        if entered is None or entered > t or (done is not None and done <= t):
            continue
        if p["kind"] not in ("short_vertical", "fly"):
            continue
        out.append(p)
    return sorted(out, key=lambda p: ts(p["entry_time"]))


def stalest(candidates: list[dict]) -> dict | None:
    return candidates[0] if candidates else None


def latest_at_or_before(rows: list[dict], key: str, t: datetime, tolerance: float) -> dict | None:
    """The row whose `key` time is nearest at or before t, within `tolerance` seconds, else None."""
    best = None
    for r in rows:
        at = ts(r[key])
        if at is None or at > t or (t - at).total_seconds() > tolerance:
            continue
        if best is None or at > ts(best[key]):
            best = r
    return best


def completion_path(live, position_id: str) -> list[dict]:
    q = "select * from fly_order_path where position_id = ? and leg = 'completion' order by observed_at"
    return [dict(r) for r in live.execute(q, (position_id,))]


def marks(live, position_id: str) -> list[dict]:
    q = "select * from fly_live_marks where position_id = ? order by iteration_ts"
    return [dict(r) for r in live.execute(q, (position_id,))]


def paper_twin(paper_rows: list[dict], first: datetime, last: datetime, used: set) -> dict | None:
    """Paper's entry during the run: any refusal tick in it is the same blocked opportunity, so the
    window is the run's first to last tick, each widened by a minute. Nearest the first tick wins;
    a paper entry already standing in for an earlier run is not counted twice."""
    lo = first.timestamp() - TWIN_TOLERANCE_SECONDS
    hi = last.timestamp() + TWIN_TOLERANCE_SECONDS
    best = None
    for p in paper_rows:
        at = ts(p["entry_time"])
        if at is None or p["position_id"] in used or not lo <= at.timestamp() <= hi:
            continue
        if best is None or abs(at - first) < abs(ts(best["entry_time"]) - first):
            best = p
    return best


# --------------------------------------------------------------------------- the position at t
def _qty(p: dict) -> int:
    return int(p.get("quantity") or 1)


def open_cash(p: dict) -> float:
    """Per-contract cash the spread held at t: its opening credit. A row that completed later carries
    net = credit - debit, so the debit is put back."""
    return p["net"] + ((p["debit"] or 0.0) if p["kind"] == "fly" else 0.0)


def settlement_fees(p: dict) -> float:
    """What the row paid at the print: the broker's figure where it was split out, else the module's
    own assignment fee for this row's shape at its settlement price."""
    if p.get("settlement_fees") is not None:
        return p["settlement_fees"]
    return fly.assignment_fee(p, p["settlement_price"])


def entry_fees(p: dict) -> float:
    """Fees the spread had paid by t: the row's total less its settlement fees and, on a row that
    completed later, the completion's own two-leg fee."""
    paid = (p["fees"] or 0.0) - settlement_fees(p)
    if p["kind"] == "fly":
        paid -= fly.vertical_open_fee(p["symbol"] or "SPX", _qty(p))
    return paid


def force_completed_net(p: dict, debit: float) -> float:
    """Settled net of the spread completed at t for `debit` points, at its own settlement print.

    The print's fee starts from what the row really paid there and adds only the settlement events
    the completing leg brings (the module's table: the fly's ITM strikes less the row's own), so a
    row that did complete, completed here at its own debit, gives back its recorded `pnl`."""
    sym, qty = p["symbol"] or "SPX", _qty(p)
    completed = {
        "kind": "fly",
        "side": p["side"],
        "center": p["center"],
        "wing_width": p["wing_width"],
        "quantity": qty,
        "net": open_cash(p) - debit,
        "status": "settled",
        "settlement_price": p["settlement_price"],
    }
    sp = p["settlement_price"]
    added = fly.assignment_fee(completed, sp) - fly.assignment_fee(p, sp)
    completed["fees"] = entry_fees(p) + fly.vertical_open_fee(sym, qty) + settlement_fees(p) + added
    return fly.position_pnl(completed, p["settlement_price"])


def aborted_net(p: dict, structure_mid: float) -> float:
    """Net of the spread bought back at t at its mark, its own measured slippage and a close fee.
    `structure_mid` is signed like the ledger (a short vertical's is negative: what it costs back)."""
    sym, qty = p["symbol"] or "SPX", _qty(p)
    close_fee = core_fees.ic_close_fee(sym, qty, legs=2, sell_legs=1, ndigits=4)
    gross = (open_cash(p) + structure_mid) * fly.CONTRACT_MULTIPLIER * qty
    return gross - entry_fees(p) - close_fee - (p["slippage_dollars"] or 0.0)


# --------------------------------------------------------------------------- the replay
def value_run(run: dict, live_rows: list[dict], paper_rows: list[dict], acted: set, used: set, live) -> dict:
    """One refusal run. The action is taken at the twin's entry, held inside the run's span: that is
    the tick the freed slot would have been filled on."""
    first, last = ts(run["first_seen"]), ts(run["last_seen"]) or ts(run["first_seen"])
    out = {
        "trade_date": run["trade_date"],
        "first_seen": run["first_seen"],
        "last_seen": run["last_seen"],
        "occurrences": run["occurrences"],
        "center": run["center_first"],
        "status": None,
    }
    twin = paper_twin(paper_rows, first, last, used)
    t = min(max(ts(twin["entry_time"]), first), last) if twin else first
    out["acted_at"] = t.isoformat()
    target = stalest([p for p in open_spreads_at(live_rows, t) if p["position_id"] not in acted])
    if target is None:
        out["status"] = "nothing_to_free"
        return out
    out["position_id"] = target["position_id"]
    if twin is None:
        out["status"] = "unmatched"
        return out
    out["twin"] = {"position_id": twin["position_id"], "entry_time": twin["entry_time"], "pnl": twin["pnl"]}
    out["twin_same_center"] = twin["center"] in (run["center_first"], run["center_last"])
    if target["pnl"] is None or target["settlement_price"] is None:
        out["status"] = "position_unsettled"
        return out
    if twin["pnl"] is None:
        out["status"] = "twin_unsettled"
        return out
    used.add(twin["position_id"])
    acted.add(target["position_id"])
    out["status"] = "valued"
    held = target["pnl"]
    out["held_net"] = held

    path = completion_path(live, target["position_id"])
    quoted = [r for r in path if r["buy_ask"] is not None and r["sell_bid"] is not None]
    q = latest_at_or_before(quoted, "observed_at", t, QUOTE_TOLERANCE_SECONDS)
    if q is None:
        out["c1"] = None
        out["c1_unmeasured"] = "no completion quote within tolerance"
    else:
        natural = q["buy_ask"] - q["sell_bid"]
        c1 = force_completed_net(target, natural)
        out["c1"] = round(twin["pnl"] + c1 - held, 2)
        out["c1_natural_debit"] = round(natural, 4)
        if None not in (q["buy_bid"], q["sell_ask"]):
            mid = (q["buy_bid"] + q["buy_ask"]) / 2 - (q["sell_bid"] + q["sell_ask"]) / 2
            out["c1_mid_debit"] = round(mid, 4)
            out["c1_at_mid"] = round(twin["pnl"] + force_completed_net(target, mid) - held, 2)
    resting = latest_at_or_before(
        [r for r in path if r["limit_price"] is not None], "observed_at", t, QUOTE_TOLERANCE_SECONDS
    )
    if resting is not None:
        out["c1_resting_limit"] = resting["limit_price"]
        out["c1_bound"] = round(twin["pnl"] + force_completed_net(target, resting["limit_price"]) - held, 2)

    m = latest_at_or_before(marks(live, target["position_id"]), "iteration_ts", t, QUOTE_TOLERANCE_SECONDS)
    if m is None or m["structure_mid"] is None or m["kind"] != "short_vertical":
        out["c2"] = None
        out["c2_unmeasured"] = "no short-vertical mark within tolerance"
    elif target["slippage_dollars"] is None:
        out["c2"] = None
        out["c2_unmeasured"] = "no recorded slippage on the position"
    else:
        out["c2"] = round(twin["pnl"] + aborted_net(target, m["structure_mid"]) - held, 2)
        out["c2_structure_mid"] = m["structure_mid"]
    return out


def summarise(runs: list[dict], key: str) -> dict:
    """One option's swap values by session. An option valued nowhere reads `net: None`, never 0:
    unmeasured is not break-even."""
    by_day: dict[str, list[float]] = defaultdict(list)
    for r in runs:
        if r.get(key) is not None:
            by_day[r["trade_date"]].append(r[key])
    per_session = {d: round(sum(v), 2) for d, v in sorted(by_day.items())}
    worst = min(per_session.items(), key=lambda kv: kv[1]) if per_session else None
    return {
        "valued_runs": sum(len(v) for v in by_day.values()),
        "net": round(sum(per_session.values()), 2) if per_session else None,
        "per_session": per_session,
        "worst_session": {"trade_date": worst[0], "net": worst[1]} if worst else None,
        "robustness": robustness({d: (len(v), sum(v)) for d, v in by_day.items()}),
    }


def replay(live, paper, arm: str, since: str, until: str | None) -> dict:
    runs = refusal_runs(live, arm, since, until)
    valued: list[dict] = []
    acted: dict[str, set] = defaultdict(set)
    used: dict[str, set] = defaultdict(set)
    cache: dict[tuple, tuple[list, list]] = {}
    for run in runs:
        key = (run["trade_date"], run["symbol"])
        if key not in cache:
            cache[key] = (
                positions(live, arm, run["trade_date"], run["symbol"]),
                positions(paper, arm, run["trade_date"], run["symbol"]),
            )
        live_rows, paper_rows = cache[key]
        day = run["trade_date"]
        valued.append(value_run(run, live_rows, paper_rows, acted[day], used[day], live))
    status: dict[str, int] = defaultdict(int)
    for r in valued:
        status[r["status"]] += 1
    ok = [r for r in valued if r["status"] == "valued"]
    return {
        "caveat": CAVEAT,
        "arm": arm,
        "since": since,
        "until": until,
        "quote_tolerance_seconds": QUOTE_TOLERANCE_SECONDS,
        "twin_tolerance_seconds": TWIN_TOLERANCE_SECONDS,
        "refusal_runs": len(runs),
        "sessions": sorted({r["trade_date"] for r in runs}),
        "status": dict(status),
        "unmeasured": {
            "c1": sum(1 for r in ok if r.get("c1") is None),
            "c2": sum(1 for r in ok if r.get("c2") is None),
        },
        "twin_same_center": sum(1 for r in ok if r.get("twin_same_center")),
        "held_net": round(sum(r["held_net"] for r in ok), 2),
        "twin_net": round(sum(r["twin"]["pnl"] for r in ok), 2),
        "c1": summarise(ok, "c1"),
        "c1_at_mid": summarise(ok, "c1_at_mid"),
        "c1_bound": summarise(ok, "c1_bound"),
        "c2": summarise(ok, "c2"),
        "runs": valued,
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arm", default="control")
    ap.add_argument("--session", help="one trade date (YYYY-MM-DD)")
    ap.add_argument("--since", default=DEFAULT_SINCE, help=f"default {DEFAULT_SINCE}")
    ap.add_argument("--until")
    ap.add_argument("--detail", action="store_true", help="include every refusal run")
    a = ap.parse_args(argv)
    since, until = (a.session, a.session) if a.session else (a.since, a.until)
    out = replay(_open(_ledger("live")), _open(_ledger("paper")), a.arm, since, until)
    if not a.detail:
        out.pop("runs")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
