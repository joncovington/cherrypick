"""Pair each session's base book with the first expected-move debit-spread pair (2026-09-29).

    python scripts/flies_em_pair_replay.py            # control vs debit-first-up/-down since 2026-09-21
    python scripts/flies_em_pair_replay.py --base no-entry-on-up-trend
    python scripts/flies_em_pair_replay.py --since 2026-10-01 --until 2026-10-31

Read-only; runnable from anywhere; ledgers resolve off `$CHERRYPICK_HOME` (default `~/.cherrypick`).

**The question.** Proposed 2026-09-29: just before the entry window, buy a call and a put debit
spread at the expected move, as a hedge on the base book's breakout days and as first legs the
module can complete into flies. `debit-first-up`/`-down` already buy those spreads at the 0.15-delta
centre (about one standard deviation), so no arm is needed to read it -- each session's FIRST entry
on each side is the pair, and it is scored two ways:

- `traded` -- as the arm recorded it, completion included.
- `held`   -- the same debit spread held to settlement and never completed, repriced at the real
              print through `fly.position_pnl` (`long_vertical`, the settlement fee that print
              triggers). An uncompleted row IS the held case and keeps its recorded P&L; a completed
              row is charged its own entry's share of its fees only (`analysis_rows.entry_fees`).
              A counterfactual, not a fill.

The two diverge exactly where the proposal needs them to agree: completion caps the spread at a
fly's payoff, so on the day spot runs through it the hedge is gone (2026-09-21: the call spread
completed at +$2; held, it was worth +$339 on a -$836 control day). The read that matters is the
`base losing days` block -- does the pair pay when the base loses, and what does it cost when it
does not -- and it needs several losing days before it says anything.
"""

from __future__ import annotations

import argparse
import statistics as st

from cherrypick.flies import analysis_rows, fly


def _session_stats(per_day: dict[str, float]) -> tuple[float, float, float, int]:
    vals = [per_day[d] for d in sorted(per_day)]
    cum = peak = dd = 0.0
    for v in vals:
        cum += v
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    return sum(vals), (min(vals) if vals else 0.0), dd, sum(v < 0 for v in vals)


def held_pnl(row: dict) -> float:
    """The row held as its opening debit spread to the print: its recorded P&L if it was never
    completed, else repriced with only its entry's share of the fees (`analysis_rows`)."""
    if row["kind"] == "long_vertical":
        return row["pnl"]
    spread = {
        "kind": "long_vertical",
        "side": row["side"],
        "center": row["center"],
        "wing_width": row["wing_width"],
        "net": -row["debit"],
        "quantity": row["quantity"],
        "fees": analysis_rows.entry_fees(row),
    }
    return fly.position_pnl(spread, row["settlement_price"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ledger", choices=("paper", "live"), default="paper")
    ap.add_argument("--base", default="control", help="the book the pair hedges")
    ap.add_argument("--up", default="debit-first-up")
    ap.add_argument("--down", default="debit-first-down")
    ap.add_argument("--symbol", default="SPX")
    ap.add_argument("--since", default="2026-09-21", help="default: the debit-first pair's first session")
    ap.add_argument("--until")
    a = ap.parse_args()

    conn = analysis_rows.open_ledger(a.ledger)
    base, up, dn = (
        analysis_rows.settled_positions(conn, arm=arm, symbol=a.symbol, since=a.since, until=a.until)
        for arm in (a.base, a.up, a.down)
    )
    if not base:
        print("nothing to pair: no base rows")
        return

    base_day: dict[str, float] = {}
    for r in base:
        base_day[r["trade_date"]] = base_day.get(r["trade_date"], 0.0) + r["pnl"]
    first: dict[tuple[str, str], dict] = {}
    for side, rows in (("up", up), ("dn", dn)):
        for r in rows:  # entry_time order, so the first seen is the session's first entry
            first.setdefault((side, r["trade_date"]), r)
    days = sorted(d for d in base_day if ("up", d) in first and ("dn", d) in first)
    if not days:
        print("no session has the base and both sides of the pair")
        return

    print(
        f"== {a.ledger} {a.base} + first {a.up}/{a.down}  {days[0]}..{days[-1]}  "
        f"({len(days)} sessions; a completed row held is charged its own entry fee)"
    )
    print(
        f"  {'session':10} {'base':>9} {'traded':>8} {'held':>8}   "
        "first pair (entry, centre, how it ended, traded P&L)"
    )
    traded: dict[str, float] = {}
    held: dict[str, float] = {}
    for d in days:
        u, w = first[("up", d)], first[("dn", d)]
        traded[d] = u["pnl"] + w["pnl"]
        held[d] = held_pnl(u) + held_pnl(w)
        legs = " / ".join(
            f"{s} {r['entry_time'][11:16]} K{r['center']:.0f} "
            f"{'fly' if r['kind'] == 'fly' else 'open'} {r['pnl']:+.0f}"
            for s, r in (("up", u), ("dn", w))
        )
        print(
            f"  {d:10} {base_day[d]:+9.2f} {traded[d]:+8.2f} {held[d]:+8.2f}   {legs}  "
            f"spot {u['underlying_at_entry']:.0f}->{u['settlement_price']:.0f}"
        )

    total = sum(base_day[d] for d in days)
    print(f"  {'total':10} {total:+9.2f} {sum(traded.values()):+8.2f} {sum(held.values()):+8.2f}")
    losing = [d for d in days if base_day[d] < 0]
    print(
        f"\n  base losing days: {len(losing)} of {len(days)}"
        + ("  -- too few to read the hedge; re-run later" if len(losing) < 5 else "")
    )
    for name, pair in (("traded", traded), ("held", held)):
        on = [pair[d] for d in losing]
        off = [pair[d] for d in days if d not in losing]
        print(
            f"  {name:7} on losing days {sum(on):+9.2f} (avg {st.mean(on) if on else 0:+7.2f})   "
            f"other days {sum(off):+9.2f} (avg {st.mean(off) if off else 0:+7.2f})"
        )

    b = _session_stats({d: base_day[d] for d in days})
    print(f"\n  {'book':16} {'net':>9} {'worst day':>10} {'max DD':>9} {'losing days':>12}")
    for name, s in (
        (a.base, b),
        ("+ pair traded", _session_stats({d: base_day[d] + traded[d] for d in days})),
        ("+ pair held", _session_stats({d: base_day[d] + held[d] for d in days})),
    ):
        print(f"  {name:16} {s[0]:+9.2f} {s[1]:+10.2f} {s[2]:+9.2f} {s[3]:12d}")


if __name__ == "__main__":
    main()
