"""Replay the flies live pilot's entry admission over paper control's recorded ticks, varying the start.

    python scripts/flies_live_start_replay.py                      # 10:00 / 10:15 / 10:30 over the era
    python scripts/flies_live_start_replay.py --start 10:15 --start 10:45
    python scripts/flies_live_start_replay.py --validate --since 2026-09-29 --start 10:15
    python scripts/flies_live_start_replay.py --calibrate          # score the pricing models

Read-only; runnable from anywhere; ledgers resolve off `$CHERRYPICK_HOME` (default `~/.cherrypick`).

**The question.** `live.no_entry_before` moved to 10:30 on 2026-09-17 because live then held ONE
incomplete position at a time. That rule was removed on 2026-09-25, leaving
`live.max_open_margin_dollars` as the only sizing gate, and the start moved to 10:15 for 2026-09-29
on this script's first answer (10:15 +$4,758 against 10:30's +$2,735 and 10:00's +$3,614).

**Why that answer's model drifted (2026-10-12).** It took paper control's settled rows and dropped
the ones the start or the cap refused. Over live's sessions from 09-29 it admitted 41 structures to
live's 59, and 36 of live's 59 were structures (centre and side) that NO paper arm held that day.
Dropping rows cannot produce them. Paper's 10:00-10:14 entries and its cap-free book occupy centres
(the duplicate-structure rule), run the six-minute cadence clock and hold legs (the sign rule), so
paper's later ticks were refused for reasons live's smaller book never had, and live entered
elsewhere. The cap arithmetic was not the drift; it admits every one of live's own entries.

**The model: the engine's own gates over the recorded ticks.** Paper control records each tick's
wanted centre and spot (`fly_entry_attempts`, every ~15 s, 10:00-14:30). Each tick, at most one per
`--tick-seconds` (live's loop runs about once a minute), is put to:
- the start, and the quarter-end session refusal (live-only, from 2026-10-01);
- `engine.portfolio_gates` (cadence, duplicate structure, sign rule) against the REPLAY's own book,
  the arm's whole day as live hands it, with `engine.choose_side` picking the side from the spot;
- `live_loop.margin_cap_exceeded`'s arithmetic: the book's worst case at that instant (an uncompleted
  vertical at its entry credit and entry fee, a completed fly at its floor after the fees paid by
  then) plus the proposal's (width - credit) x100 must fit under the cap.
Which second of the minute live's tick lands on decides which centre it sees, and from there the day
diverges. So every session is replayed at `--phases` tick phases and the figures are their mean;
`phase_nets` is each phase's total. The spread between phases is the model's own noise floor.

**Pricing.** Most admitted structures were never held by paper, so each is priced by two models
(`--calibrate` scores them):
- credit: the median credit of that day's paper entries (any legged 5-wide SPX arm) within 15 minutes
  and at a similar distance from their centre (`credit_estimate`);
- completion: the first recorded tick after entry at which spot has moved `COMPLETE_MOVE_POINTS`
  through the centre toward the completing side, before live's 15:30 cutoff. It classifies 79 of 84
  live rows (completed or stranded) with a 0.6-minute median timing error, and 241 of 251 paper
  control rows;
- the completion pays the live limit (`engine.completion_limit`), and the P&L is `fly.position_pnl`
  at the session's print with modelled fees.
`--donors` prices a structure paper did hold (same day, centre and side within `DONOR_MINUTES`) from
that row instead; donor pricing reproduces every paper control row's recorded `pnl` within a cent. It
is off by default because the model prices a structure $15 below its own row on average, and the
earlier a start, the more of its entries paper held, so mixing the two biases the comparison toward
10:00.

`--validate` replays live's own sessions at live's start. The declared tolerance: each session's mean
structure count within `VALIDATE_SESSION_TOLERANCE` of live's, and the total within
`VALIDATE_TOTAL_PCT`. It exits 1 outside it. What live does and the replay does not: an entry order
that does not fill is cancelled and re-placed a tick later, and a fresh-quote check skips a tick, so
live's book drifts from any replay of it within minutes. Fills are paper's modelled ones, so the
money compares starts with one another and never forecasts live P&L.
"""

from __future__ import annotations

import argparse
import bisect
import statistics as st
from collections import defaultdict
from datetime import datetime, timedelta

from cherrypick.core import calendar as _cal
from cherrypick.core.regimecuts import sign_test_p
from cherrypick.flies import analysis_rows, engine, fly

DEFAULT_STARTS = ("10:00", "10:15", "10:30")
SYMBOL = "SPX"
WIDTH = 5.0
#: Paper attempt outcomes that reached a centre inside the arm's window: the ticks live could enter on.
WANT_OUTCOMES = ("filled", "cadence_blocked", "duplicate_blocked", "sign_rule_blocked", "gate_blocked")
#: A donor row must be this close in time to the replayed entry (minutes).
DONOR_MINUTES = 2.0
#: Points spot must move through the centre, toward the completing side, for a modelled completion.
COMPLETE_MOVE_POINTS = 6.0
#: live.completion_cutoff: no completion after this (the resting order is cancelled there).
COMPLETION_CUTOFF = "15:30"
#: The live quarter-end refusal (live_loop._quarter_end) landed after 2026-09-30's session.
QUARTER_END_FROM = "2026-10-01"
#: --validate: each session's mean structure count within this many of live's ...
VALIDATE_SESSION_TOLERANCE = 2
#: ... and the total within this fraction of live's.
VALIDATE_TOTAL_PCT = 0.10
CREDIT_WINDOW_MIN = 15.0
CREDIT_MONEYNESS_POINTS = 2.5


# --------------------------------------------------------------------------- inputs
class Inputs:
    """Everything the replay reads, loaded once: ticks, spot paths, the donor pool, the prints."""

    def __init__(self, since: str, until: str | None, arm: str = "control", use_donors: bool = False):
        self.use_donors = use_donors
        conn = analysis_rows.open_ledger("paper")
        hi = until or "9999-12-31"
        self.ticks: dict[str, list[tuple[datetime, float, float]]] = defaultdict(list)
        marks = ",".join("?" * len(WANT_OUTCOMES))
        for ts, center, spot in conn.execute(
            "SELECT ts, center, spot FROM fly_entry_attempts "
            "WHERE arm = ? AND symbol = ? AND mode = 'legged' "
            f"AND trade_date BETWEEN ? AND ? AND outcome IN ({marks}) AND center IS NOT NULL "
            "AND spot IS NOT NULL ORDER BY ts",
            (arm, SYMBOL, since, hi, *WANT_OUTCOMES),
        ):
            self.ticks[ts[:10]].append((datetime.fromisoformat(ts), float(center), float(spot)))
        self.paths: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
        for ts, px in conn.execute(
            "SELECT iteration_ts, underlying_price FROM fly_iterations WHERE arm = ? AND symbol = ? "
            "AND trade_date BETWEEN ? AND ? AND underlying_price IS NOT NULL ORDER BY iteration_ts",
            (arm, SYMBOL, since, hi),
        ):
            self.paths[ts[:10]].append((datetime.fromisoformat(ts), float(px)))
        self.path_times = {d: [t for t, _ in v] for d, v in self.paths.items()}
        pool = analysis_rows.settled_positions(
            conn, arm=None, symbol=SYMBOL, since=since, until=until, entry_mode="legged"
        )
        self.pool: dict[str, list[dict]] = defaultdict(list)
        self.prints: dict[str, float] = {}
        for p in pool:
            if p["wing_width"] != WIDTH or p["kind"] not in ("fly", "short_vertical"):
                continue
            p["_t"] = datetime.fromisoformat(p["entry_time"])
            self.pool[p["trade_date"]].append(p)
            if p.get("settlement_price") is not None:
                self.prints.setdefault(p["trade_date"], float(p["settlement_price"]))
        self.paper_control = [p for d in self.pool.values() for p in d if p["arm"] == arm]

    def days(self) -> list[str]:
        return sorted(d for d in self.ticks if d in self.prints and d in self.paths)


# --------------------------------------------------------------------------- the two models
def _toward(side: str, center: float, spot: float) -> float:
    """Spot's distance from the centre in the completing direction (negative before it gets there)."""
    up = fly.completing_side_direction(side) == "up"
    return (spot - center) if up else (center - spot)


def modelled_completion(inp: Inputs, day: str, side: str, center: float, t0: datetime) -> datetime | None:
    """The first recorded tick after `t0`, up to the cutoff, at which spot has moved
    COMPLETE_MOVE_POINTS through the centre toward the completing side; None if it never does."""
    times, path = inp.path_times.get(day, []), inp.paths.get(day, [])
    for t, px in path[bisect.bisect_right(times, t0) :]:
        if t.strftime("%H:%M") >= COMPLETION_CUTOFF:
            return None
        if _toward(side, center, px) >= COMPLETE_MOVE_POINTS:
            return t
    return None


def credit_estimate(
    inp: Inputs, day: str, t: datetime, side: str, center: float, spot: float
) -> float | None:
    """The median credit of that day's pool entries within CREDIT_WINDOW_MIN of `t` whose spot sat
    about as far from their centre (toward the completing side); the nearest-in-time entry's when
    none is that close."""
    rows = inp.pool.get(day, [])
    if not rows:
        return None
    here = _toward(side, center, spot)
    near = [
        p["credit"]
        for p in rows
        if abs((p["_t"] - t).total_seconds()) <= CREDIT_WINDOW_MIN * 60
        and p.get("underlying_at_entry") is not None
        and abs(_toward(p["side"], p["center"], p["underlying_at_entry"]) - here) <= CREDIT_MONEYNESS_POINTS
    ]
    if near:
        return float(st.median(near))
    return float(min(rows, key=lambda p: abs((p["_t"] - t).total_seconds()))["credit"])


def donor(inp: Inputs, day: str, t: datetime, side: str, center: float) -> dict | None:
    cands = [
        p
        for p in inp.pool.get(day, [])
        if p["center"] == center
        and p["side"] == side
        and abs((p["_t"] - t).total_seconds()) <= DONOR_MINUTES * 60
    ]
    return min(cands, key=lambda p: abs((p["_t"] - t).total_seconds())) if cands else None


def structure(inp: Inputs, day: str, t: datetime, side: str, center: float, spot: float) -> dict | None:
    """One replayed entry: its credit, completion (time and debit) and settled P&L, from a donor
    when one exists and from the two models otherwise."""
    d = donor(inp, day, t, side, center) if inp.use_donors else None
    params = {"min_floor_dollars": 1.0, "fee_buffer": 0.10}
    entry_fee = fly.vertical_open_fee(SYMBOL)
    if d is not None:
        credit, source = float(d["credit"]), "donor"
        done = d["completed_at"] if d["kind"] == "fly" else None
        completed_at = datetime.fromisoformat(done) if done else None
        if completed_at is not None and completed_at.strftime("%H:%M") >= COMPLETION_CUTOFF:
            completed_at = None
        debit = float(d["debit"]) if completed_at is not None else None
    else:
        credit = credit_estimate(inp, day, t, side, center, spot)
        if credit is None:
            return None
        source = "modelled"
        completed_at = modelled_completion(inp, day, side, center, t)
        debit = None
        if completed_at is not None:
            limit, _ = engine.completion_limit(
                {"net": credit, "fees": entry_fee, "quantity": 1, "symbol": SYMBOL}, params
            )
            debit = float(limit)
    pos = {
        "trade_date": day,
        "side": side,
        "center": center,
        "wing_width": WIDTH,
        "far_width": None,
        "quantity": 1,
        "credit": credit,
        "debit": debit,
        "entry_fee": entry_fee,
        "completed_at": completed_at,
        "t": t,
        "entry_time_min": t.hour * 60 + t.minute,
        "source": source,
    }
    final = state_at(pos, None)
    pos["pnl"] = round(fly.position_pnl(final, inp.prints[day]), 2)
    pos["stranded"] = completed_at is None
    return pos


def state_at(pos: dict, t: datetime | None) -> dict:
    """The position as the ledger would hold it at `t` (None: after the close)."""
    done = pos["completed_at"] is not None and (t is None or pos["completed_at"] <= t)
    if done:
        return {
            **pos,
            "kind": "fly",
            "net": pos["credit"] - pos["debit"],
            "fees": pos["entry_fee"] + fly.vertical_open_fee(SYMBOL),
        }
    return {**pos, "kind": "short_vertical", "net": pos["credit"], "fees": pos["entry_fee"]}


def exposure_at(pos: dict, t: datetime) -> float:
    """live_loop.open_margin_dollars for one position: its worst case at `t`, net of the fees paid by
    then; the floor reserves the print's own fee."""
    return max(0.0, -fly.position_floor(state_at(pos, t)))


# --------------------------------------------------------------------------- the replay
def replay_day(
    inp: Inputs, day: str, start: str, cap: float, cadence: int, tick_seconds: int, phase: float = 0.0
) -> dict:
    """One session under live's rules. Ticks are taken at most one per `tick_seconds`, the first at
    or after `start` + `phase` seconds: live's loop runs about once a minute at an arbitrary second,
    and which second it lands on moves which centre a tick sees."""
    book: list[dict] = []
    cap_refused = 0
    if day >= QUARTER_END_FROM and _cal.is_quarterly_expiry(_cal.date.fromisoformat(day)):
        return {"book": book, "cap_refused": 0, "quarter_end": True}
    gate_params = {"min_seconds_between_entries": cadence}
    ticks = inp.ticks.get(day, [])
    if not ticks:
        return {"book": book, "cap_refused": 0, "quarter_end": False}
    hh, mm = (int(x) for x in start.split(":"))
    first = ticks[0][0].replace(hour=hh, minute=mm, second=0, microsecond=0) + timedelta(seconds=phase)
    last = first - timedelta(seconds=tick_seconds)
    for t, center, spot in ticks:
        if t < first or (t - last) < timedelta(seconds=tick_seconds):
            continue
        last = t
        side = engine.choose_side({"underlying_price": spot}, center)
        long_strike = center - WIDTH if side == engine.PUT else center + WIDTH
        held = [state_at(p, t) for p in book]
        reason, _ = engine.portfolio_gates(
            gate_params,
            held,
            t.hour * 60 + t.minute,
            proposed_legs=[("0dte", side, center, -1), ("0dte", side, long_strike, 1)],
            structure=(float(center), WIDTH, None),
        )
        if reason:
            continue
        pos = structure(inp, day, t, side, center, spot)
        if pos is None:
            continue
        proposed = max(0.0, (WIDTH - pos["credit"]) * fly.CONTRACT_MULTIPLIER)
        if sum(exposure_at(p, t) for p in book) + proposed > cap:
            cap_refused += 1
            continue
        book.append(pos)
    return {"book": book, "cap_refused": cap_refused, "quarter_end": False}


def replay(
    inp: Inputs, days: list[str], start: str, cap: float, cadence: int, tick_seconds: int, phases: int = 4
) -> dict:
    """Every session at `phases` evenly spaced tick phases. Per-day figures are the mean over the
    phases; `phase_nets` keeps each phase's total, the spread a single phase would hide."""
    offsets = [tick_seconds * i / phases for i in range(phases)]
    per_day, kept_per_day = defaultdict(float), defaultdict(float)
    books: dict[float, dict[str, list[dict]]] = {}
    phase_nets = []
    kept = modelled = stranded = cap_refused = 0
    for off in offsets:
        books[off] = {}
        net = 0.0
        for day in days:
            out = replay_day(inp, day, start, cap, cadence, tick_seconds, off)
            b = out["book"]
            books[off][day] = b
            kept += len(b)
            modelled += sum(1 for p in b if p["source"] == "modelled")
            stranded += sum(1 for p in b if p["stranded"])
            cap_refused += out["cap_refused"]
            kept_per_day[day] += len(b) / phases
            day_net = sum(p["pnl"] for p in b)
            per_day[day] += day_net / phases
            net += day_net
        phase_nets.append(round(net, 2))
    per_day = {d: round(v, 2) for d, v in per_day.items()}
    vals = list(per_day.values())
    return {
        "kept": round(kept / phases, 1),
        "modelled": round(modelled / phases, 1),
        "stranded": round(stranded / phases, 1),
        "cap_refused_ticks": round(cap_refused / phases, 1),
        "net": round(sum(vals), 2),
        "phase_nets": phase_nets,
        "days": len(vals),
        "losing_days": sum(1 for v in vals if v < 0),
        "worst_day": min(vals) if vals else None,
        "median_day": round(st.median(vals), 2) if vals else None,
        "per_day": per_day,
        "kept_per_day": dict(kept_per_day),
        "books": books,
    }


# --------------------------------------------------------------------------- reads
def _matched(live_rows: list[dict], book: list[dict]) -> int:
    """Live entries the replay also made: same centre and side, within ten minutes."""
    return sum(
        1
        for r in live_rows
        if any(
            p["center"] == r["center"]
            and p["side"] == r["side"]
            and abs((p["t"] - datetime.fromisoformat(r["entry_time"])).total_seconds()) <= 600
            for p in book
        )
    )


def validate(inp: Inputs, a) -> bool:
    live = analysis_rows.settled_positions(
        analysis_rows.open_ledger("live"),
        arm=a.arm,
        symbol=SYMBOL,
        since=a.since,
        until=a.until,
        entry_mode="legged",
    )
    live_by: dict[str, list[dict]] = defaultdict(list)
    for r in live:
        live_by[r["trade_date"]].append(r)
    days = [d for d in inp.days() if d in live_by]
    missing = sorted(set(live_by) - set(days))
    rep = replay(inp, days, a.start[0], a.cap, a.cadence, a.tick_seconds, a.phases)
    rows = [r for d in days for r in live_by[d]]
    print(
        f"live actual: {len(rows)} structures, net {sum(r['pnl'] for r in rows):+.2f}, "
        f"{sum(1 for r in rows if r['kind'] != 'fly')} stranded"
    )
    print(
        f"replay {a.start[0]} (mean of {a.phases} tick phases): {rep['kept']} structures "
        f"({rep['modelled']} modelled), net {rep['net']:+.2f} (phases {rep['phase_nets']}), "
        f"{rep['stranded']} stranded"
    )
    ok, total_matched = True, 0.0
    for d in days:
        lv = live_by[d]
        counts = [len(b[d]) for b in rep["books"].values()]
        hit = st.mean(_matched(lv, b[d]) for b in rep["books"].values())
        total_matched += hit
        mean = rep["kept_per_day"][d]
        bad = abs(mean - len(lv)) > VALIDATE_SESSION_TOLERANCE
        ok = ok and not bad
        print(
            f"  {d}  live {len(lv):>3} / {sum(r['pnl'] for r in lv):+9.2f}"
            f"   replay {mean:>4.1f} {counts} / {rep['per_day'][d]:+9.2f}"
            f"   same structure {hit:.1f}{'  <-- outside tolerance' if bad else ''}"
        )
    print(
        "live structures the replay also made (same centre and side within 10 min): "
        f"{total_matched:.1f} of {len(rows)}"
    )
    if missing:
        print(f"not replayable (paper's ticks or print missing): {', '.join(missing)}")
    total_ok = bool(rows) and abs(rep["kept"] - len(rows)) <= VALIDATE_TOTAL_PCT * len(rows)
    ok = ok and total_ok
    print(
        f"tolerance: every session's mean within +/-{VALIDATE_SESSION_TOLERANCE} of live, total within "
        f"{VALIDATE_TOTAL_PCT:.0%} ({rep['kept']} against {len(rows)}): {'PASS' if ok else 'FAIL'}"
    )
    return ok


def _score_completion(inp: Inputs, rows: list[dict]) -> str:
    right = total = 0
    errs = []
    for p in rows:
        d = p["trade_date"]
        if d not in inp.paths:
            continue
        total += 1
        pred = modelled_completion(inp, d, p["side"], p["center"], datetime.fromisoformat(p["entry_time"]))
        done = p["completed_at"] if p["kind"] == "fly" else None
        actual = datetime.fromisoformat(done) if done else None
        if actual is not None and actual.strftime("%H:%M") >= COMPLETION_CUTOFF:
            actual = None
        if (pred is None) == (actual is None):
            right += 1
            if pred is not None:
                errs.append(abs((pred - actual).total_seconds()) / 60)
    return (
        f"{right} of {total} classified right (completed or stranded); median timing error "
        f"{st.median(errs):.1f} min, 80th pct {st.quantiles(errs, n=5)[-1]:.1f} min"
    )


def calibrate(inp: Inputs, a) -> None:
    """Score the completion model against live's and paper control's own rows, and the credit
    estimate against paper control's (each row's own structure left out of the pool)."""
    live = analysis_rows.settled_positions(
        analysis_rows.open_ledger("live"),
        arm=None,
        symbol=SYMBOL,
        since=a.since,
        until=a.until,
        entry_mode="legged",
    )
    live = [p for p in live if p["wing_width"] == WIDTH and p["kind"] in ("fly", "short_vertical")]
    print(f"completion model (move {COMPLETE_MOVE_POINTS} pts, cutoff {COMPLETION_CUTOFF}):")
    print(f"  live:  {_score_completion(inp, live)}")
    print(f"  paper: {_score_completion(inp, inp.paper_control)}")
    cerr = []
    for p in inp.paper_control:
        d = p["trade_date"]
        if p.get("underlying_at_entry") is None:
            continue
        others = inp.pool[d]
        inp.pool[d] = [q for q in others if q["center"] != p["center"] or q["_t"] != p["_t"]]
        est = credit_estimate(inp, d, p["_t"], p["side"], p["center"], p["underlying_at_entry"])
        inp.pool[d] = others
        if est is not None:
            cerr.append(est - p["credit"])
    print(
        f"credit estimate: median abs error {st.median(abs(e) for e in cerr):.3f}, "
        f"mean error {st.mean(cerr):+.3f} over {len(cerr)} rows"
    )
    # Donor pricing against the recorded row (live's cutoff left out: it is not paper's rule), and
    # the whole modelled pricing against donor pricing: the bias a modelled entry carries.
    diffs, exact, checked = [], 0, 0
    keep = inp.use_donors
    for p in inp.paper_control:
        d = p["trade_date"]
        if d not in inp.paths or d not in inp.prints or p.get("underlying_at_entry") is None:
            continue
        inp.use_donors = True
        own = structure(inp, d, p["_t"], p["side"], p["center"], p["underlying_at_entry"])
        if own is not None and not (p["completed_at"] and p["completed_at"][11:16] >= COMPLETION_CUTOFF):
            checked += 1
            exact += abs(own["pnl"] - p["pnl"]) <= 0.011
        inp.use_donors = False
        others = inp.pool[d]
        inp.pool[d] = [q for q in others if q["center"] != p["center"] or q["_t"] != p["_t"]]
        mod = structure(inp, d, p["_t"], p["side"], p["center"], p["underlying_at_entry"])
        inp.pool[d] = others
        if own is not None and mod is not None:
            diffs.append(mod["pnl"] - own["pnl"])
    inp.use_donors = keep
    print(f"donor pricing reproduces the recorded pnl within a cent on {exact} of {checked} rows")
    print(
        f"modelled P&L less the row's own, per structure: mean {st.mean(diffs):+.2f}, "
        f"median {st.median(diffs):+.2f}, total {sum(diffs):+.2f} over {len(diffs)} rows"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arm", default="control")
    ap.add_argument("--start", action="append", help="HH:MM, repeatable; default 10:00, 10:15, 10:30")
    ap.add_argument("--baseline", default="10:30", help="the start every other one is compared against")
    ap.add_argument("--cap", type=float, default=1000.0, help="live.max_open_margin_dollars")
    ap.add_argument("--cadence", type=int, default=360, help="min_seconds_between_entries")
    ap.add_argument("--tick-seconds", type=int, default=60, help="live's loop period")
    ap.add_argument("--phases", type=int, default=4, help="tick phases averaged over")
    ap.add_argument("--since", default="2026-08-21", help="default: the advisor-era cutover")
    ap.add_argument("--until")
    ap.add_argument(
        "--validate", action="store_true", help="live's actual sessions beside the replay of them"
    )
    ap.add_argument("--calibrate", action="store_true", help="score the completion and credit models")
    ap.add_argument(
        "--donors", action="store_true", help="price a structure paper held from its row (biased; see above)"
    )
    a = ap.parse_args()
    a.start = a.start or list(DEFAULT_STARTS)
    inp = Inputs(a.since, a.until, a.arm, use_donors=a.donors)

    if a.calibrate:
        calibrate(inp, a)
        return
    if a.validate:
        raise SystemExit(0 if validate(inp, a) else 1)

    days = inp.days()
    base = replay(inp, days, a.baseline, a.cap, a.cadence, a.tick_seconds, a.phases)
    for start in sorted(set(a.start) | {a.baseline}):
        out = (
            base
            if start == a.baseline
            else replay(inp, days, start, a.cap, a.cadence, a.tick_seconds, a.phases)
        )
        line = {k: v for k, v in out.items() if k not in ("per_day", "kept_per_day", "books")}
        if start != a.baseline:
            diffs = [out["per_day"][d] - base["per_day"][d] for d in out["per_day"]]
            better, worse = sum(1 for x in diffs if x > 0.01), sum(1 for x in diffs if x < -0.01)
            phases_won = sum(1 for x, y in zip(out["phase_nets"], base["phase_nets"], strict=True) if x > y)
            line["vs_baseline"] = (
                f"{better}-{worse} days, p {sign_test_p(better, worse)}, net {sum(diffs):+.0f}, "
                f"ahead in {phases_won} of {len(base['phase_nets'])} phases"
            )
        print(start, line)


if __name__ == "__main__":
    main()
