"""Walk the selector forward over recorded sessions, before it ever trades.

For each session d, `selector.fit` is given every settled row from before d, and the frozen model it
returns decides d's entries; then the window steps one session on. Nothing on or after d reaches
d's model (`fit` is truncation-invariant and a test pins it), so every decision here is out of
sample.

Two modes, labelled as what they are:

* **take/skip (exact).** control's own rows, each kept or dropped by the model's verdict on its
  cell. Exact for the reason `replay_gates` states: structures are independent, so dropping an entry
  the selector would have skipped leaves the others' outcomes untouched. One caveat it shares with
  every gate replay: a skipped entry would have freed a cadence slot control never used.
* **two-structure (an upper bound, a smoke test).** control and debit-first-atm over the sessions
  both traded, choosing between entries filled on the same tick. It can only choose among trades
  that were actually filled, and it ignores the cross-construction sign-rule and cadence
  interactions a single book would meet. With the handful of sessions that exist it checks the
  machinery, not the strategy.

Benchmarks travel with every answer, because a selector is only worth what it beats: control as
traded, debit-first-atm as traded, the hindsight oracle (the best of the tick's trades or nothing),
every gate `replay_gates.sweep` replays, vol-floor's gate applied to control's rows from the stamped
straddle ratio, and the advised twins' real books over the same sessions.

Read-only over the paper ledger. Never writes, never reaches a broker.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from cherrypick.core import regimecuts as _rc

from cherrypick.flies import replay_gates, selector

VOL_FLOOR_PCT = 0.0022  # vol-floor's declared `min_entry_straddle_pct`
PAIR_SECONDS = 10  # entries of two arms on one tick land within this of each other

_ROW_COLUMNS = (
    "position_id, arm, trade_date, kind, entry_time, completed_at, pnl, entry_trend_bucket, "
    "entry_vol_bucket, entry_vol_value, void_reason"
)


def load_rows(conn, *, start: str, end: str | None = None, arms=None, symbol: str = "SPX") -> list[dict]:
    """Settled rows for the arms named (all arms when None), in session and fill order."""
    q = f"SELECT {_ROW_COLUMNS} FROM fly_positions WHERE symbol = ? AND status = 'settled' AND trade_date >= ?"
    args: list = [symbol, start]
    if end:
        q += " AND trade_date <= ?"
        args.append(end)
    if arms:
        q += f" AND arm IN ({', '.join('?' for _ in arms)})"
        args.extend(arms)
    q += " ORDER BY trade_date, entry_time"
    return [dict(r) for r in conn.execute(q, args)]


def _fit_rows(rows: list[dict]) -> list[dict]:
    """The rows a model may learn from: voided rows never count."""
    return [r for r in rows if not r.get("void_reason")]


def _regime(r: dict) -> dict:
    return {"vol_bucket": r.get("entry_vol_bucket"), "trend_bucket": r.get("entry_trend_bucket")}


def _session_diff(a: dict, b: dict) -> dict:
    """`{session: [1, a - b]}` from two `per_day` maps, the shape `regimecuts.robustness` reads."""
    return {d: [1, round(a.get(d, 0.0) - b.get(d, 0.0), 2)] for d in sorted(set(a) | set(b))}


# --------------------------------------------------------------------------- take/skip
def take_skip(
    base_rows: list[dict], history: list[dict], *, breaks: list, min_sessions: int, margin: float
) -> dict:
    """Control's rows kept or dropped, session by session, by a model fitted on everything before.
    `base_rows` are what replay-gates reads (its `base` is reproduced exactly); `history` is what
    the model learns from, which may reach further back than the window replayed."""
    sources = [selector.DEFAULT_SOURCE]
    kept, folds = [], {}
    for d in sorted({r["trade_date"] for r in base_rows}):
        starts = selector.arm_starts(breaks, d, sources)
        m = selector.fit(
            _fit_rows(history),
            through=d,
            sources=sources,
            arm_starts=starts,
            min_sessions=min_sessions,
            margin=margin,
        )
        reasons: Counter = Counter()
        for r in (x for x in base_rows if x["trade_date"] == d):
            cand = {"sources": sources, "mode": "legged", "plan": {}}
            choice, why = selector.choose([(cand, selector.score(cand, _regime(r), m))], m)
            reasons[why] += 1
            if choice is not None:
                kept.append(r)
        folds[d] = dict(reasons)
    out = replay_gates.summarize(base_rows, kept)
    out["folds"] = folds
    return out


# --------------------------------------------------------------------------- two structures
def _t(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def ticks(rows_a: list[dict], rows_b: list[dict]) -> list[list[dict]]:
    """Group two arms' entries into ticks: an entry of each within PAIR_SECONDS of the other is one
    tick (each used once, nearest first); an unpaired entry is a tick of its own."""
    used, out = set(), []
    for a in rows_a:
        ta = _t(a["entry_time"])
        best = None
        for b in rows_b:
            if b["position_id"] in used or b["trade_date"] != a["trade_date"]:
                continue
            gap = abs((_t(b["entry_time"]) - ta).total_seconds())
            if gap <= PAIR_SECONDS and (best is None or gap < best[0]):
                best = (gap, b)
        if best:
            used.add(best[1]["position_id"])
            out.append([a, best[1]])
        else:
            out.append([a])
    out.extend([b] for b in rows_b if b["position_id"] not in used)
    return sorted(out, key=lambda t: (t[0]["trade_date"], t[0]["entry_time"]))


def two_structure(rows: list[dict], *, sources: list, breaks: list, min_sessions: int, margin: float) -> dict:
    """Choose per tick between the sources' filled entries; also the hindsight oracle over the
    same ticks. Sessions are those where every source traded."""
    by_arm = {s: [r for r in rows if r["arm"] == s] for s in sources}
    sessions = sorted(set.intersection(*({r["trade_date"] for r in by_arm[s]} for s in sources)))
    window = [r for r in rows if r["trade_date"] in sessions and r["arm"] in sources]
    kept, oracle, folds = [], [], {}
    for d in sessions:
        starts = selector.arm_starts(breaks, d, sources)
        m = selector.fit(
            _fit_rows(rows),
            through=d,
            sources=sources,
            arm_starts=starts,
            min_sessions=min_sessions,
            margin=margin,
        )
        reasons: Counter = Counter()
        day = [[r for r in by_arm[s] if r["trade_date"] == d] for s in sources]
        for tick in ticks(day[0], day[1]):
            scored = [({"sources": [r["arm"]], "mode": "legged", "plan": {}, "row": r}, None) for r in tick]
            scored = [(c, selector.score(c, _regime(c["row"]), m)) for c, _ in scored]
            choice, why = selector.choose(scored, m)
            reasons[why] += 1
            if choice is not None:
                kept.append(choice["row"])
            best = max(tick, key=lambda r: r["pnl"] or 0.0)
            if (best["pnl"] or 0.0) > 0:
                oracle.append(best)
        folds[d] = dict(reasons)
    out = {
        "sessions": sessions,
        "selector": {**replay_gates.summarize(window, kept), "folds": folds},
        "oracle": replay_gates.summarize(window, oracle),
    }
    for s in sources:
        mine = [r for r in window if r["arm"] == s]
        out[s] = replay_gates.summarize(mine, mine)
    return out


# --------------------------------------------------------------------------- benchmarks
def vol_floor_gate(rows: list[dict], floor: float = VOL_FLOOR_PCT) -> dict:
    """Vol-floor's gate replayed on control's rows from each row's stamped straddle ratio -- the
    same measure `engine.low_vol_refusal` reads. A row with no ratio is kept: the live gate fails
    open on an unquoted ATM pair, and so does this."""
    return replay_gates.summarize(
        rows, [r for r in rows if r.get("entry_vol_value") is None or r["entry_vol_value"] >= floor]
    )


def advised_books(conn, *, sessions: list, symbol: str = "SPX") -> dict:
    """The advised twins' REAL books over the given sessions -- measured, not replayed."""
    if not sessions:
        return {}
    marks = ", ".join("?" for _ in sessions)
    rows = [
        dict(r)
        for r in conn.execute(
            f"SELECT {_ROW_COLUMNS} FROM fly_positions WHERE symbol = ? AND status = 'settled' "
            f"AND arm LIKE 'advised:%' AND trade_date IN ({marks}) ORDER BY trade_date, entry_time",
            [symbol, *sessions],
        )
    ]
    out = {}
    for arm in sorted({r["arm"] for r in rows}):
        mine = [r for r in rows if r["arm"] == arm]
        out[arm] = {k: v for k, v in replay_gates.summarize(mine, mine).items() if k != "per_day"}
    return out


def run(
    conn,
    *,
    start: str,
    end: str | None = None,
    symbol: str = "SPX",
    history_start: str = "2026-08-21",
    min_sessions: int = selector.DEFAULT_MIN_SESSIONS,
    margin: float = selector.DEFAULT_MARGIN,
    two_structure_sources=("control", "debit-first-atm"),
) -> dict:
    """Both modes and every benchmark over one window. `history_start` bounds what the models may
    learn from (the advisor-era cutover by default); within it each fold is further scoped to the
    era its session sits in, by the ledger's own breaks."""
    from cherrypick.flies import db as dbmod

    breaks = dbmod.measurement_breaks(conn)
    # The same rows replay-gates reads (same filters, same order -- its `base` is reproduced
    # exactly), plus the regime columns the model scores on, which its loader does not select.
    base_rows = load_rows(conn, start=start, end=end, arms=["control"], symbol=symbol)
    history = load_rows(conn, start=history_start, end=end, arms=["control"], symbol=symbol)
    ts = take_skip(base_rows, history, breaks=breaks, min_sessions=min_sessions, margin=margin)
    base = replay_gates.summarize(base_rows, base_rows)

    gates = replay_gates.without_per_day(replay_gates.sweep(base_rows))
    gates["vol_floor"] = {k: v for k, v in vol_floor_gate(base_rows).items() if k != "per_day"}

    pair_rows = load_rows(conn, start=history_start, end=end, arms=list(two_structure_sources), symbol=symbol)
    two = two_structure(
        pair_rows,
        sources=list(two_structure_sources),
        breaks=breaks,
        min_sessions=min_sessions,
        margin=margin,
    )

    strip = lambda b: {k: v for k, v in b.items() if k not in ("per_day",)}  # noqa: E731
    return {
        "ok": True,
        "procedure_version": selector.PROCEDURE_VERSION,
        "window": {"start": start, "end": end, "history_start": history_start, "symbol": symbol},
        "min_sessions": min_sessions,
        "margin": margin,
        "take_skip": {
            "selector": strip(ts),
            "control": strip(base),
            "selector_minus_control": _rc.robustness(_session_diff(ts["per_day"], base["per_day"])),
        },
        "fixed_gates": gates,
        "advised_twins": advised_books(conn, sessions=sorted(base["per_day"]), symbol=symbol),
        "two_structure": {
            "label": "upper bound: chooses only among filled trades; a smoke test at this sample",
            "sessions": two["sessions"],
            **{k: strip(v) for k, v in two.items() if k != "sessions"},
        },
    }
