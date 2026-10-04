"""What each delta bwb arm would have made at any credit floor, or none (2026-10-03).

    python scripts/flies_bwb_floor_replay.py                     # every bwb arm since 2026-09-30
    python scripts/flies_bwb_floor_replay.py --arm bwb-down --per-day
    python scripts/flies_bwb_floor_replay.py --since 2026-10-05 --until 2026-10-30

Read-only; runnable from anywhere; ledgers resolve off `$CHERRYPICK_HOME` (default `~/.cherrypick`).

**The question.** The delta-placed bwb arms (`bwb-up`/`-down`, `-w2`) are held almost entirely by
their own `min_bwb_credit_pct_of_tail` floor (experiment-log 2026-09-30). From 2026-09-30 every bwb
attempt row carries the structure it was offered in `proposed_legs` -- each leg's strike, sign,
quantity, bid, ask and delta -- so a refused bwb can be priced and settled against the session's
print without a chain. This replay asks what the arm would have made had the floor been lower, and
whether the market or the slippage model is what refuses it.

**How.** Per (arm, session), walk the attempt rows that carry `proposed_legs`, in tick order. Each
is re-priced from its own quotes through `fly.fly_debit` -- `modelled` at the arm's
`slippage_frac` (what `evaluate_bwb_entry` charged; it reproduces `would_be_credit` to the cent) and
`mid` at zero. An attempt is entered when the credit clears the floor under test and every gate
after the floor in `evaluate_bwb_entry` (the ceiling, `max_bwb_tail_dollars`, fees), and then
`engine.portfolio_gates` (cadence, duplicate, sign rule) against the positions the replay itself has
entered that day. Of the gates ahead of strike selection, those that read only the market (window,
delta, containment) were already applied: a row only carries legs if it got that far. The two that
count positions (`max_positions`, `max_positions_per_window`) are applied again here, against the
replay's own entries, because a lower floor changes what they count. Each entry settles at the
session's `fly_books` print through `fly.position_pnl` (`kind: bwb`, opening fee, and the settlement
fee that print triggers).

**What it is not.**
- **Unrolled.** A real bwb rolls its far wing in once the roll cheapens (`engine.evaluate_roll`), and
  the roll needs quotes at two strikes later in the session that the ledger does not keep. Every
  replayed entry is held to settlement as entered, so its tail is fully exposed. The `roll`
  block prices the arm's own real fills both ways, to show how far that moves the answer.
- **Not the arm's later gating.** A rolled position is a fly, with different legs for the sign and
  duplicate rules. Here every position stays a bwb all day.
- **Pre-floor refusals are fixed.** Floor-refused rows only exist where the window and delta gates
  passed, so a lower floor cannot add a tick the arm never priced.

**Validation, on every run, per arm.** Each must hold, or the arm's floor table is not printed and
the run exits 1:
- the credit re-priced from the stored quotes matches `would_be_credit` on every priced row;
- at the deployed floor, the replay enters exactly the real fills made before the session's first
  real roll. Until then the replay's book and the real one are the same, so they must agree. After
  it they may part, since a rolled position is a fly to the sign and duplicate rules and the replay
  cannot roll; that count is printed, never excused;
- every real fill that was never rolled settles through this path to the cent.

An arm with no real fill has nothing to check its gating against: its table prints, marked
UNVALIDATED.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from cherrypick.flies import cli, engine, fly

ARMS = ("bwb-up", "bwb-down", "bwb-up-w2", "bwb-down-w2", "bwb-atm")
FLOORS = (0.0, 0.025, 0.05, 0.075, 0.10, 0.125, 0.15)  # fractions of the tail (far_width - wing_width)


def _ledger(name: str) -> Path:
    home = os.environ.get("CHERRYPICK_HOME")
    root = (Path(home) if home else Path.home() / ".cherrypick") / "data" / "flies"
    return root / f"{name}_trades.db"


def _structure(legs: list[dict]) -> dict:
    """Side, centre and both widths from the three stored legs, checked against `fly.bwb_strikes`."""
    side = legs[0]["type"]
    center = next(leg["strike"] for leg in legs if leg["sign"] == -1)
    wings = sorted((leg["strike"] for leg in legs if leg["sign"] == 1), key=lambda k: abs(k - center))
    width, far_width = abs(wings[0] - center), abs(wings[1] - center)
    if fly.bwb_strikes(side, center, width, far_width) != (wings[0], center, wings[1]):
        raise ValueError(f"legs are not a {side} bwb: {legs}")
    return {"side": side, "center": center, "wing_width": width, "far_width": far_width}


def credit(legs: list[dict], slip: float) -> float:
    by = {leg["strike"]: leg for leg in legs}
    lo, mid_k, hi = sorted(by)
    return -fly.fly_debit(by[lo], by[mid_k], by[hi], slip)


def load_attempts(conn, arm: str, symbol: str, since: str, until: str | None) -> list[dict]:
    q = """select ts, trade_date, outcome, block_detail, proposed_legs, would_be_credit, position_id
           from fly_entry_attempts where arm=? and symbol=? and trade_date>=? and (? is null or trade_date<=?)
           and proposed_legs is not null order by trade_date, ts, id"""
    out = []
    for r in conn.execute(q, (arm, symbol, since, until, until)):
        row = dict(r)
        row["legs"] = json.loads(row["proposed_legs"])
        row.update(_structure(row["legs"]))
        t = datetime.fromisoformat(row["ts"])
        row["now_min"] = t.hour * 60 + t.minute
        out.append(row)
    return out


def replay_day(rows: list[dict], params: dict, floor_frac: float, slip: float, symbol: str) -> list[dict]:
    qty = params.get("quantity", 1)
    open_fee = fly.fly_open_fee(symbol, qty)
    roll_fee = fly.vertical_open_fee(symbol, qty)
    ceiling = params.get("max_bwb_credit_pct_of_tail", 0.6)
    tail_cap = params.get("max_bwb_tail_dollars", 150.0)
    taken: list[dict] = []
    for r in rows:
        # The two pre-strike gates that count positions, against the replay's own book (the engine
        # applies them before it records `proposed_legs`, so a row passed them only against the REAL
        # book). A skip has no side effect, so their place in the order does not change the result.
        if len(taken) >= params.get("max_positions", 4):
            continue
        _, window = engine.in_entry_window(r["now_min"], params.get("entry_windows", []))
        if engine._window_cap_reached(params, taken, window):
            continue
        tail = r["far_width"] - r["wing_width"]
        c = credit(r["legs"], slip)
        if c <= floor_frac * tail or c > ceiling * tail:
            continue
        if (tail - c) * fly.CONTRACT_MULTIPLIER * qty + open_fee > tail_cap:
            continue
        if c * fly.CONTRACT_MULTIPLIER * qty <= open_fee + roll_fee:
            continue
        pos = {
            "kind": "bwb",
            "side": r["side"],
            "center": r["center"],
            "wing_width": r["wing_width"],
            "far_width": r["far_width"],
            "trade_date": r["trade_date"],
            "entry_time_min": r["now_min"],
            "entry_window": window,
        }
        proposed = [(engine._EXPIRY, *leg[1:]) for leg in fly.position_legs(pos)]
        refusal, _ = engine.portfolio_gates(
            params,
            taken,
            r["now_min"],
            proposed_legs=proposed,
            structure=(float(r["center"]), float(r["wing_width"]), float(r["far_width"])),
        )
        if refusal:
            continue
        taken.append(
            {
                **pos,
                "ts": r["ts"],
                "credit": round(c, 4),
                "net": c,
                "quantity": qty,
                "fees": open_fee,
                "attempt": r,
            }
        )
    return taken


def settle(p: dict, price: float) -> float:
    return fly.position_pnl(
        {k: p[k] for k in ("kind", "side", "center", "wing_width", "far_width", "net", "quantity", "fees")},
        price,
    )


def before_roll(fills: list[tuple[str, str]], days: list[str], first_roll: dict[str, datetime]) -> set:
    """The (session, ts) fills made before that session's first real roll, on the replayed sessions:
    the stretch over which the replay's book and the real one are the same."""
    return {
        (d, t)
        for d, t in fills
        if d in days and (d not in first_roll or datetime.fromisoformat(t) < first_roll[d])
    }


def _days(per_day: dict[str, float]) -> str:
    vals = list(per_day.values())
    return (
        (f"losing {sum(v < 0 for v in vals)}/{len(vals)}  worst {min(vals):+9.2f}  best {max(vals):+9.2f}")
        if vals
        else ""
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arm", action="append", help=f"repeatable; default {', '.join(ARMS)}")
    ap.add_argument("--symbol", default="SPX")
    ap.add_argument("--since", default="2026-09-30", help="default: the first session with proposed_legs")
    ap.add_argument("--until")
    ap.add_argument("--per-day", dest="per_day", action="store_true")
    a = ap.parse_args()
    failed = []

    config = cli.load_config()
    conn = sqlite3.connect(f"file:{_ledger('paper').as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    prices = {
        r["trade_date"]: r["settlement_price"]
        for r in conn.execute(
            "select trade_date, max(settlement_price) settlement_price from fly_books where symbol=? "
            "and status='settled' group by trade_date",
            (a.symbol,),
        )
    }

    for arm in a.arm or ARMS:
        params = engine.merged_params(config, arm)
        slip = params.get("slippage_frac", fly.DEFAULT_SLIPPAGE_FRAC)
        deployed = params.get("min_bwb_credit_pct_of_tail", 0.15)  # the engine's own default
        attempts = load_attempts(conn, arm, a.symbol, a.since, a.until)
        by_day: dict[str, list[dict]] = defaultdict(list)
        for r in attempts:
            by_day[r["trade_date"]].append(r)
        days = sorted(d for d in by_day if prices.get(d) is not None)
        if not days:
            print(f"== {arm}: no settled session with proposed_legs\n")
            continue
        tail = attempts[0]["far_width"] - attempts[0]["wing_width"]
        print(
            f"== {arm}  {a.symbol}  {days[0]}..{days[-1]}  ({len(days)} sessions, {len(attempts)} priced "
            f"attempts; {attempts[0]['wing_width']:g}/{attempts[0]['far_width']:g}, tail {tail:g}, "
            f"deployed floor {deployed:g} = {deployed * tail:.2f})"
        )

        # Validation (see the module note): the stored credit, the deployed floor's entries against
        # the real fills up to each session's first real roll, and settlement on unrolled fills.
        priced = [r for r in attempts if r["would_be_credit"] is not None]
        worst = max((abs(credit(r["legs"], slip) - r["would_be_credit"]) for r in priced), default=0.0)
        real = [(r["trade_date"], r["ts"]) for r in attempts if r["outcome"] == "filled"]
        mine = [(d, p["ts"]) for d in days for p in replay_day(by_day[d], params, deployed, slip, a.symbol)]
        rows = [
            dict(r)
            for r in conn.execute(
                "select * from fly_positions where arm=? and symbol=? and status='settled' and trade_date>=? "
                "and (? is null or trade_date<=?) and entry_mode='bwb_roll' and void_reason is null",
                (arm, a.symbol, a.since, a.until, a.until),
            )
        ]
        held = [r for r in rows if r["kind"] == "bwb"]
        first_roll: dict[str, datetime] = {}
        for r in rows:
            if r["rolled_at"]:
                at = datetime.fromisoformat(r["rolled_at"])
                first_roll[r["trade_date"]] = min(first_roll.get(r["trade_date"], at), at)

        settled_ok = sum(
            abs(
                settle(
                    {**r, "net": r["credit"], "fees": fly.fly_open_fee(r["symbol"], r["quantity"])},
                    r["settlement_price"],
                )
                - r["pnl"]
            )
            < 0.005
            for r in held
        )
        real_early = before_roll(real, days, first_roll)
        mine_early = before_roll(mine, days, first_roll)
        problems = []
        if worst > 0.0005:
            problems.append(f"credit re-prices {worst:.4f} away from would_be_credit")
        if mine_early != real_early:
            problems.append(
                f"before the first roll the replay enters {len(mine_early)} vs {len(real_early)} real "
                f"(missing {sorted(real_early - mine_early)[:3]}, "
                f"extra {sorted(mine_early - real_early)[:3]})"
            )
        if settled_ok != len(held):
            problems.append(f"settlement reproduces only {settled_ok}/{len(held)} unrolled fills")
        status = "FAIL" if problems else ("UNVALIDATED" if not real else "PASS")
        print(
            f"  validation {status}: credit re-priced on {len(priced)} rows, worst gap {worst:.4f}; "
            f"before each session's first real roll the deployed floor enters {len(mine_early)} vs "
            f"{len(real_early)} real fills; after it {len(mine) - len(mine_early)} vs "
            f"{len(real) - len(real_early)} (the replay cannot roll)"
            + (
                f"; settlement reproduces {settled_ok}/{len(held)} real unrolled fills to the cent"
                if held
                else ""
            )
            + ("" if real else "; no real fill to check the gating against")
        )
        if problems:
            failed.append(arm)
            print("  " + "; ".join(problems) + " -- the floor table is not printed\n")
            continue

        # How far short of the floor the market was: the session's best offered credit.
        print(
            "  best credit offered per session (modelled / mid):  "
            + "  ".join(
                f"{d[5:]} {max(credit(r['legs'], slip) for r in by_day[d]):.2f}/"
                f"{max(credit(r['legs'], 0.0) for r in by_day[d]):.2f}"
                for d in days
            )
        )

        print(
            f"  {'floor':>13} {'pricing':8} {'entries':>7} {'net':>10} {'per entry':>9} "
            f"{'tail hit':>8}   sessions"
        )
        for frac in FLOORS:
            for name, s in (("modelled", slip), ("mid", 0.0)):
                per_day: dict[str, float] = {}
                n = tail_hits = 0
                for d in days:
                    taken = replay_day(by_day[d], params, frac, s, a.symbol)
                    pnls = [settle(p, prices[d]) for p in taken]
                    per_day[d] = round(sum(pnls), 2)
                    n += len(taken)
                    tail_hits += sum(
                        fly.bwb_payoff(p["side"], p["center"], p["wing_width"], p["far_width"], prices[d]) < 0
                        for p in taken
                    )
                net = sum(per_day.values())
                label = f"{frac:g} ({frac * tail:.2f})" + ("*" if frac == deployed else "")
                print(
                    f"  {label:>13} {name:8} {n:7d} {net:+10.2f} {(net / n if n else 0):+9.2f} "
                    f"{tail_hits:8d}   "
                    f"{_days(per_day) if n else ''}"
                )
                if a.per_day and n:
                    print("                " + "  ".join(f"{d[5:]} {v:+.2f}" for d, v in per_day.items()))
        print(
            "  (* deployed; every entry unrolled, held to the print"
            + ("; UNVALIDATED: no real fill to check the gating against)" if not real else ")")
        )

        # The roll's weight, on the arm's own real fills since --since: as traded vs never rolled.
        if rows:
            traded = sum(r["pnl"] for r in rows)
            unrolled = sum(
                settle(
                    {
                        **r,
                        "kind": "bwb",
                        "net": r["credit"],
                        "fees": fly.fly_open_fee(r["symbol"], r["quantity"]),
                    },
                    r["settlement_price"],
                )
                for r in rows
            )
            rolled = sum(r["kind"] == "fly" for r in rows)
            print(
                f"  roll: {len(rows)} real fills, {rolled} rolled; as traded {traded:+.2f}, "
                f"same entries never rolled {unrolled:+.2f}"
            )
        print()
    if failed:
        print(f"validation FAILED for {', '.join(failed)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
