"""The MEIC replay's validation against MEIC paper (docs/quote-history-replay-plan.md, Phase 2).

A path replay drifts from paper the moment one decision differs, and for an arm with caps or spacing
(`bp-*`) every later decision differs with it. That says little about whether the replay reproduces
paper's RULES on paper's DATA. So, as flies' check does, each test hands the replay paper's own book as
it stood at an instant and runs one engine tick:

- **state as of t** (`as_of`): paper's condors entered before t, every one open (nothing settles
  intraday), exit, settlement and path-telemetry fields cleared, fees the entry's share. No SPX arm
  ran a per-side stop in the recorder era (control holds to settlement, the `bp-*` arms too), so no
  side is ever stopped by t; an arm that stops is checked on entries only until that changes.
- **entries**: a paper entry is matched if the replay, at a recorded sample within `WINDOW_SECONDS`
  of it, enters the same arm on the same short put, short call and width. Credit difference on the
  matched ones.
- **replay-only entries**: on the replay's own tick grid, with the book as of each tick, an entry
  paper made nowhere within `TICK_SECONDS` on that arm and strikes; consecutive ticks of one structure
  are one episode.
- **path agreement**: a full-day replay of the arms (independent books), matched to paper's condors by
  arm, strikes and width within a tick of entry, compares each side's recorded worst cost
  (`put_max_cost`/`call_max_cost`), the path every stop threshold is derived from
  (`stop_policies`). No SPX arm stopped in the recorder era, so this replaces the plan's
  stops-matched check, which would have nothing to match.

The thresholds were declared in the plan (2026-10-18) before any number here was read.
"""

from __future__ import annotations

import shutil
import sqlite3
import statistics
import tempfile
from datetime import datetime
from pathlib import Path

from cherrypick.core.clock import ET

from cherrypick.meic import db as meic_db
from cherrypick.meic import paper, paper_loop, replay
from cherrypick.meic import paths as _paths

WINDOW_SECONDS = 30.0
TICK_SECONDS = replay.DEFAULT_TICK_SECONDS
#: Declared 2026-10-18 in docs/quote-history-replay-plan.md, before any MEIC replay number was read.
THRESHOLDS = {
    "entries_matched_min": 0.90,
    "extra_entry_episodes_max": 0.10,
    "credit_abs_median_max": 0.10,
    "credit_abs_p90_max": 0.30,
    "max_cost_abs_median_max": 0.10,
    "max_cost_abs_p90_max": 0.30,
}
_CLEARED = (
    "exit_time", "exit_price", "exit_reason", "exit_analysis", "pnl", "settle_underlying", "put_settle_value",
    "call_settle_value", "put_stop_cost", "call_stop_cost", "put_max_cost", "call_max_cost", "put_touch_time",
    "put_touch_spot", "call_touch_time", "call_touch_spot", "put_mae_spot", "put_mae_time", "call_mae_spot",
    "call_mae_time", "put_stop_fill_status", "call_stop_fill_status", "pending_exit_json", "put_exit_fill_source",
    "call_exit_fill_source", "settlement_fees",
)  # fmt: skip


def _ts(value) -> float | None:
    try:
        return datetime.fromisoformat(str(value)).timestamp() if value else None
    except ValueError:
        return None


def as_of(row: dict, t: float) -> dict | None:
    """Paper's condor `row` as it stood at instant `t`, or None if it was not yet entered."""
    entered = _ts(row.get("entry_time"))
    if entered is None or entered >= t:
        return None
    out = dict(row)
    out["status"] = "open"
    out["fees"] = float(out.get("fees") or 0.0) - float(out.get("settlement_fees") or 0.0)
    for k in _CLEARED:
        if k in out:
            out[k] = None
    out["unmarked_iterations"] = 0
    return out


def _key(row: dict) -> tuple:
    """The identity two entries must share to be the same condor: arm, both short strikes, and the
    wing on each side. Per-side widths (`equalize_credit` pairs) fall back to the scalar when NULL,
    so a pre-migration row keys on `wing_width` exactly as before -- and a pair never collides with
    its single-width sibling, which differ only in one wing."""
    w = float(row["wing_width"])
    put_wing = row.get("put_wing")
    call_wing = row.get("call_wing")
    return (
        row["arm"],
        float(row["put_strike"]),
        float(row["call_strike"]),
        w if put_wing is None else float(put_wing),
        w if call_wing is None else float(call_wing),
    )


class _Template:
    """A migrated, empty scratch ledger, copied to a fresh file for each tested tick."""

    def __init__(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = Path(self._dir.name) / "template.db"
        with meic_db.scratch_writes():
            meic_db.call("init_db", str(self.path))
        conn = sqlite3.connect(self.path)
        self.columns = [r[1] for r in conn.execute("PRAGMA table_info(ic_trades)")]
        conn.close()
        self._n = 0

    def fresh(self, rows: list[dict]) -> Path:
        self._n += 1
        path = Path(self._dir.name) / f"tick-{self._n}.db"
        shutil.copyfile(self.path, path)
        conn = sqlite3.connect(path)
        for r in rows:
            cols = [c for c in self.columns if c in r and c != "id"]
            conn.execute(
                f"INSERT INTO ic_trades ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                [r[c] for c in cols],
            )
        conn.commit()
        conn.close()
        return path

    def entries(self, path: Path) -> list[dict]:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM ic_trades")]
        finally:
            conn.close()

    def close(self):
        self._dir.cleanup()


def _tick(
    template: _Template, rday: replay.ReplayDay, sample: dict, book: list[dict], arms: list[str]
) -> list[dict]:
    """One engine tick at `sample` on `book`; the condors it entered."""
    when = datetime.fromtimestamp(sample["ts"], ET)
    snap = rday.snapshot(sample, when)
    if not snap.get("ok"):
        return []
    snap.pop("ok")
    path = template.fresh(book)
    before = {r["ic_order_id"] for r in book}
    with meic_db.scratch_writes(), paper.pinned_clock(when):
        paper.process_symbol(snap, str(path), "paper", profiles_filter=arms)
    new = [r for r in template.entries(path) if r["ic_order_id"] not in before and r["status"] != "cancelled"]
    path.unlink(missing_ok=True)
    return new


def _episodes(hits: list[tuple[float, tuple]], gap: float) -> list[dict]:
    """Consecutive (time, key) hits of the same key, no more than `gap` apart, as one episode each."""
    out: list[dict] = []
    for t, key in sorted(hits):
        last = next((e for e in reversed(out) if e["key"] == key), None)
        if last is not None and t - last["last"] <= gap:
            last["last"] = t
            last["ticks"] += 1
            continue
        out.append({"key": key, "first": t, "last": t, "ticks": 1})
    return out


def decision_check(
    day: str, arms: list[str], *, history_db=None, paper_db=None, settle_price=None, delta_source=None
) -> dict:
    """The replay's decisions against paper's on `day`, each from paper's own book (module docstring).
    `delta_source` replays on that delta whatever the session ran with: backtest's validation passes
    "computed" to both its sources, since the feed's lag is in the recording and not in a vendor's
    history, and one shared source is what makes the two replays comparable."""
    cfg, profiles = replay.replay_profiles(arms)
    if delta_source is not None and delta_source not in paper_loop.DELTA_SOURCES:
        raise ValueError(f"delta_source must be one of {paper_loop.DELTA_SOURCES}, not {delta_source!r}")
    # By default the delta that session ran with: the check asks whether the replay reproduces
    # paper's own decisions, so a session before `delta_source_since` is replayed on the feed's delta.
    as_of = delta_source or paper_loop.delta_source_for(cfg, day)
    rday = replay.ReplayDay(day, cfg, profiles, history_db=history_db, delta_source=as_of)
    if not rday.recorded.samples:
        return {"ok": False, "day": day, "error": "no recorded samples"}
    if not rday.vol:
        return {"ok": False, "day": day, "error": "no VIX open"}
    paper_db = Path(paper_db) if paper_db else _paths.paper_db_path()
    src = sqlite3.connect(f"file:{paper_db}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    marks = ",".join("?" * len(arms))
    rows = [
        dict(r)
        for r in src.execute(
            f"SELECT * FROM ic_trades WHERE trade_date = ? AND symbol = ? AND arm IN ({marks}) "
            "AND status != 'cancelled' ORDER BY entry_time",
            (day, replay.SYMBOL, *arms),
        )
    ]
    src.close()
    if settle_price is None:
        settle_price = next((r["settle_underlying"] for r in rows if r.get("settle_underlying")), None)
    samples = rday.recorded.samples
    stamps = [s["ts"] for s in samples]
    template = _Template()
    try:
        # Paper's entries, each at its own moment, on paper's book as of that moment.
        matched, unmatched = [], []
        for p in rows:
            tp = _ts(p["entry_time"])
            book = [b for b in (as_of(r, tp) for r in rows) if b is not None]
            near = sorted(
                (i for i, t in enumerate(stamps) if abs(t - tp) <= WINDOW_SECONDS),
                key=lambda i: abs(stamps[i] - tp),
            )
            hit = None
            for i in near:
                twin = next(
                    (e for e in _tick(template, rday, samples[i], book, [p["arm"]]) if _key(e) == _key(p)),
                    None,
                )
                if twin is not None:
                    hit = (stamps[i], twin)
                    break
            if hit:
                matched.append({"paper": p["ic_order_id"], "arm": p["arm"], "offset_s": round(hit[0] - tp, 1),
                                "credit_diff": round(float(hit[1]["net_credit"]) - float(p["net_credit"]), 4)})  # fmt: skip
            else:
                unmatched.append(
                    {"paper": p["ic_order_id"], "arm": p["arm"], "at": p["entry_time"], "key": _key(p)}
                )
        # Replay entries paper never made, on the replay's own tick grid.
        paper_entries = [(_ts(r["entry_time"]), _key(r)) for r in rows]
        extra = []
        for s in replay.ticks(samples, TICK_SECONDS):
            book = [b for b in (as_of(r, s["ts"]) for r in rows) if b is not None]
            for e in _tick(template, rday, s, book, arms):
                k = _key(e)
                if not any(pk == k and abs(pt - s["ts"]) <= TICK_SECONDS for pt, pk in paper_entries):
                    extra.append((s["ts"], k))
    finally:
        template.close()
    episodes = _episodes(extra, TICK_SECONDS + 5)
    # Path agreement: a full-day replay of the arms, matched to paper's condors.
    max_cost_diffs, path_pairs = [], 0
    with tempfile.TemporaryDirectory() as tmp:
        ledger = Path(tmp) / "day.db"
        full = replay.replay_session(
            day, ledger, arms=arms, history_db=history_db, settle_price=settle_price, delta_source=as_of
        )
        if full.get("ok"):
            conn = sqlite3.connect(ledger)
            conn.row_factory = sqlite3.Row
            mine = [dict(r) for r in conn.execute("SELECT * FROM ic_trades WHERE status != 'cancelled'")]
            conn.close()
            used = set()
            for p in rows:
                tp = _ts(p["entry_time"])
                twin = next(
                    (m for m in mine if m["ic_order_id"] not in used and _key(m) == _key(p)
                     and abs(_ts(m["entry_time"]) - tp) <= TICK_SECONDS),
                    None,
                )  # fmt: skip
                if twin is None:
                    continue
                used.add(twin["ic_order_id"])
                path_pairs += 1
                for side in ("put", "call"):
                    a, b = p.get(f"{side}_max_cost"), twin.get(f"{side}_max_cost")
                    if a is not None and b is not None:
                        max_cost_diffs.append(round(float(b) - float(a), 4))
    credit = sorted(abs(m["credit_diff"]) for m in matched)
    cost = sorted(abs(d) for d in max_cost_diffs)
    return {
        "ok": True,
        "day": day,
        "arms": arms,
        "paper_entries": len(rows),
        "entries_matched": len(matched),
        "unmatched_entries": unmatched,
        "extra_entry_episodes": [
            {
                "arm": e["key"][0],
                "put": e["key"][1],
                "call": e["key"][2],
                "width": e["key"][3],
                "from": datetime.fromtimestamp(e["first"], ET).isoformat(timespec="seconds"),
                "ticks": e["ticks"],
            }
            for e in episodes
        ],  # fmt: skip
        "path_pairs": path_pairs,
        "max_cost_diffs": max_cost_diffs,
        "credit_abs_median": round(statistics.median(credit), 4) if credit else None,
        "credit_abs_p90": round(credit[int(0.9 * (len(credit) - 1))], 4) if credit else None,
        "max_cost_abs_median": round(statistics.median(cost), 4) if cost else None,
        "max_cost_abs_p90": round(cost[int(0.9 * (len(cost) - 1))], 4) if cost else None,
        "matched_entries": matched,
    }


def summarise(sessions: list[dict]) -> dict:
    """The declared pass test over sessions (`THRESHOLDS`), with each measure's value, in the envelope
    `packages/backtest`'s validation reads."""
    ok = [s for s in sessions if s.get("ok")]
    n_e = sum(s["paper_entries"] for s in ok)
    credit = sorted(abs(m["credit_diff"]) for s in ok for m in s["matched_entries"])
    cost = sorted(abs(d) for s in ok for d in s["max_cost_diffs"])
    pairs = sum(s["path_pairs"] for s in ok)

    def pct(v):
        return round(v[int(0.9 * (len(v) - 1))], 4) if v else None

    measures = {
        "sessions": len(ok),
        "entries_matched": round(sum(s["entries_matched"] for s in ok) / n_e, 4) if n_e else None,
        "extra_entry_episodes": round(sum(len(s["extra_entry_episodes"]) for s in ok) / n_e, 4)
        if n_e
        else None,
        "credit_abs_median": round(statistics.median(credit), 4) if credit else None,
        "credit_abs_p90": pct(credit),
        "max_cost_abs_median": round(statistics.median(cost), 4) if cost else None,
        "max_cost_abs_p90": pct(cost),
    }
    th = THRESHOLDS

    def ge(v, x):
        return v is not None and v >= x

    def le(v, x):
        return v is not None and v <= x

    checks = {
        "entries_matched": ge(measures["entries_matched"], th["entries_matched_min"]),
        "extra_entry_episodes": le(measures["extra_entry_episodes"], th["extra_entry_episodes_max"]),
        "credit_abs_median": le(measures["credit_abs_median"], th["credit_abs_median_max"]),
        "credit_abs_p90": le(measures["credit_abs_p90"], th["credit_abs_p90_max"]),
        "max_cost_abs_median": le(measures["max_cost_abs_median"], th["max_cost_abs_median_max"]),
        "max_cost_abs_p90": le(measures["max_cost_abs_p90"], th["max_cost_abs_p90_max"]),
    }
    return {
        "measures": measures,
        "thresholds": th,
        "checks": checks,
        "enough_sessions": len(ok) >= 10,
        "passes": all(checks.values()) and len(ok) >= 10,
        # Reported beside the test: how many of paper's condors the full-day replay paired, the base
        # the path measures stand on.
        "path_pairs": {"pairs": pairs, "paper_entries": n_e, "share": round(pairs / n_e, 4) if n_e else None},
    }
