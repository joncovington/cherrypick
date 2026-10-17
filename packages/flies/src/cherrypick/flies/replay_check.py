"""The replay's decision-level validation (docs/quote-history-replay-plan.md, Phase 1 stage 2).

A path replay drifts from paper the moment one decision differs (control centres at the money, so a
half-point of spot between two quote readers flips the side), and every later decision then differs
too. That says little about whether the replay reproduces paper's RULES on paper's DATA. So at every
recorded sample this hands the replay paper's own book as it stood at that instant and runs one
tick, and compares the decision with what paper did:

- **state as of t** (`as_of`): paper's positions entered before t. One completed after t is still
  its opening vertical (completion fields cleared, fees its entry share); one completed by t is a
  fly. Nothing settles intraday, so every position is open. The running-best fields are telemetry
  the tick recomputes and are cleared. This is everything `book.process_snapshot` reads from the
  ledger (`dbmod.book_positions`).
- **matching**: a paper entry is matched if the replay entered on the same side and centre in the
  `WINDOW_SECONDS` before it; a paper completion if the replay completed that position in the window
  before it. Replay decisions paper never made are grouped into episodes (consecutive samples), so
  one missed chance counts once.

The pass thresholds were declared in the plan before any number here was read.
"""

from __future__ import annotations

import sqlite3
import statistics
import tempfile
from datetime import datetime
from pathlib import Path

from cherrypick.flies import book, clock, replay
from cherrypick.flies import db as dbmod

WINDOW_SECONDS = 30.0
#: Declared 2026-10-17 (the plan, stage 2), before any decision-level number was read.
THRESHOLDS = {
    "entries_matched_min": 0.90,
    "extra_entry_episodes_max": 0.10,
    "completions_matched_min": 0.85,
    "early_completion_episodes_max": 0.10,
    "credit_abs_median_max": 0.10,
    "credit_abs_p90_max": 0.30,
}

_COMPLETION_FIELDS = (
    "debit", "completed_at", "completion_latency_min", "spot_at_completion", "completion_order_id",
    "completion_fill_status", "completion_mode", "completion_vol_bucket", "completion_gex_bucket",
    "completion_time_bucket", "completion_skew_bucket", "completion_vol_value", "completion_gex_concentration",
    "completion_time_value", "completion_skew_value", "completion_net_gex", "completion_gamma_flip",
    "completion_gex_spot", "completion_gex_strikes", "completion_gex_input_age", "completion_center_offset_bucket",
    "completion_center_offset_value", "completion_trend_bucket", "completion_trend_value",
    "completion_event_bucket", "completion_event_value", "completion_event_labels", "hedge_mid_at_completion",
    "hedge_mid_at_completion_at",
)  # fmt: skip
_TELEMETRY_FIELDS = (
    "best_completing_debit", "best_debit_at", "best_completing_credit", "best_credit_at",
    "post_best_completing_debit", "post_best_debit_at", "post_best_completing_credit", "post_best_credit_at",
    "best_roll_debit", "best_roll_debit_at", "hedge_best_mid", "hedge_best_mid_at",
)  # fmt: skip
_SETTLEMENT_FIELDS = (
    "settlement_price",
    "expiry_payoff",
    "gross_pnl",
    "pnl",
    "exit_time",
    "settlement_source",
)


def _ts(value) -> float | None:
    try:
        return datetime.fromisoformat(str(value)).timestamp() if value else None
    except ValueError:
        return None


def as_of(row: dict, t: float) -> dict | None:
    """Paper's position `row` as it stood at instant `t`, or None if it was not yet entered."""
    entered = _ts(row.get("entry_time"))
    if entered is None or entered >= t:
        return None
    out = dict(row)
    out["status"] = "open"
    for k in _SETTLEMENT_FIELDS:
        if k in out:
            out[k] = None
    out["pinned"] = 0
    settle_fees = float(out.get("settlement_fees") or 0.0)
    total_fees = float(out.get("fees") or 0.0) - settle_fees
    if "settlement_fees" in out:
        out["settlement_fees"] = None
    done = _ts(row.get("completed_at"))
    if done is not None and done <= t:
        out["fees"] = total_fees
        return out
    # Not yet completed at t: the opening vertical, with its entry share of the fees.
    legged = row.get("entry_mode") == "legged"
    out["kind"] = "short_vertical" if legged else "long_vertical"
    out["net"] = float(row["credit"]) if legged else -float(row.get("debit") or 0.0)
    out["fees"] = total_fees / 2.0 if done is not None else total_fees
    for k in _COMPLETION_FIELDS + _TELEMETRY_FIELDS:
        if k in out:
            out[k] = None
    return out


class _Template:
    """A migrated, empty ledger copied into memory for each sample's tick."""

    def __init__(self):
        self._dir = tempfile.TemporaryDirectory()
        path = Path(self._dir.name) / "template.db"
        conn = dbmod.connect(str(path))
        self.columns = [r[1] for r in conn.execute("PRAGMA table_info(fly_positions)")]
        conn.close()
        self.source = sqlite3.connect(path)

    def fresh(self, rows: list[dict]) -> sqlite3.Connection:
        mem = sqlite3.connect(":memory:")
        self.source.backup(mem)
        mem.row_factory = sqlite3.Row
        for r in rows:
            cols = [c for c in self.columns if c in r and c != "id"]
            mem.execute(
                f"INSERT INTO fly_positions ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                [r[c] for c in cols],
            )
        return mem

    def close(self):
        self.source.close()
        self._dir.cleanup()


def _episodes(hits: list[tuple[float, tuple]], gap: float) -> list[dict]:
    """Consecutive (time, key) hits of the same key, no more than `gap` apart, as one episode each."""
    out: list[dict] = []
    for t, key in sorted(hits):
        last = next((e for e in reversed(out) if e["key"] == key), None)
        if last is not None and t - last["last"] <= gap:
            last["last"] = t
            last["samples"] += 1
            continue
        out.append({"key": key, "first": t, "last": t, "samples": 1})
    return out


def decision_check(
    config: dict,
    day: str,
    paper_conn,
    *,
    arm: str = "control",
    history_db=None,
    window_seconds: float = WINDOW_SECONDS,
) -> dict:
    """The replay's decisions against paper `arm`'s on `day`, each from paper's own book (module
    docstring). Paper and the recorder tick on separate ~15 s clocks, so each of paper's decisions is
    tested at every recorded sample within `window_seconds` of it, on paper's book as of that
    decision; the scan for decisions paper never made uses paper's book as of each sample."""
    cfg = replay.replay_config(config, [arm])
    symbol = (cfg.get("symbols") or ["SPX"])[0]
    session = replay.RecordedSession(symbol, day, history_db=history_db)
    if not session.samples:
        return {"ok": False, "error": f"no recorded samples for {symbol} on {day}"}
    paper_conn.row_factory = sqlite3.Row
    paper_rows = [
        dict(r)
        for r in paper_conn.execute(
            "SELECT * FROM fly_positions WHERE arm = ? AND trade_date = ? AND entry_mode = 'legged' "
            "AND status != 'cancelled' AND void_reason IS NULL ORDER BY entry_time",
            (arm, day),
        )
    ]
    kwargs = {
        k: v for k, v in replay.provider.snapshot_kwargs(cfg).items() if k != "max_gex_input_age_seconds"
    }
    instants = session.instants()
    stamps = [w.timestamp() for w in instants]
    template = _Template()

    def tick(when, book_at: float, *, keep_open: str | None = None) -> dict:
        """One replay tick at `when` on paper's book as of `book_at` (`keep_open` held uncompleted)."""
        state = []
        for r in paper_rows:
            if r["position_id"] == keep_open:
                # Held open: as it stood just before paper completed it, whatever the sample time.
                done = _ts(r.get("completed_at"))
                s = as_of(r, min(book_at, done) - 1e-3 if done is not None else book_at)
            else:
                s = as_of(r, book_at)
            if s is not None:
                state.append(s)
        snap = session.snapshot("", symbol, when=when, **kwargs)
        if not snap.get("ok"):
            return {"entries": [], "completed": [], "refusal": snap.get("reason", "refused")}
        mem = template.fresh(state)
        before = {r["position_id"]: r["kind"] for r in state}
        with clock.pinned(when):
            book.process_snapshot(snap, cfg, mem, arm, experiment_id=None)
        out = {"entries": [], "completed": [], "refusal": None}
        for r in mem.execute("SELECT position_id, kind, side, center, credit FROM fly_positions"):
            if r["position_id"] not in before:
                out["entries"].append({"side": r["side"], "center": r["center"], "credit": r["credit"]})
            elif before[r["position_id"]] != "fly" and r["kind"] == "fly":
                out["completed"].append(r["position_id"])
        att = mem.execute(
            "SELECT outcome, block_detail FROM fly_entry_attempts ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if att is not None and att["outcome"] != "filled":
            out["refusal"] = att["outcome"] + (f": {att['block_detail']}" if att["block_detail"] else "")
        mem.close()
        return out

    def near(t: float) -> list[int]:
        return [i for i, s in enumerate(stamps) if abs(s - t) <= window_seconds]

    try:
        # Paper's entries, each at its own moment.
        matched_entries, unmatched_entries = [], []
        for p in paper_rows:
            tp = _ts(p["entry_time"])
            hit, said = None, None
            for i in near(tp):
                res = tick(instants[i], tp)
                said = said or res["refusal"]
                twin = next(
                    (e for e in res["entries"] if (e["side"], e["center"]) == (p["side"], p["center"])), None
                )
                if twin is not None:
                    hit = (stamps[i], twin)
                    break
                if res["entries"]:
                    said = f"entered {res['entries'][0]['side']} {res['entries'][0]['center']:g} instead"
            if hit is not None:
                diff = round(float(hit[1]["credit"]) - float(p["credit"]), 4)
                matched_entries.append(
                    {
                        "paper": p["position_id"],
                        "at": p["entry_time"],
                        "offset_s": round(hit[0] - tp, 1),
                        "credit_diff": diff,
                    }  # fmt: skip
                )
            else:
                unmatched_entries.append(
                    {
                        "paper": p["position_id"],
                        "at": p["entry_time"],
                        "side": p["side"],
                        "center": p["center"],
                        "replay_said": said,
                    }  # fmt: skip
                )

        # Paper's completions, each at its own moment, the position still open.
        paper_done = {p["position_id"]: _ts(p["completed_at"]) for p in paper_rows if p.get("completed_at")}
        matched_c, unmatched_c = [], []
        for pid, tc in paper_done.items():
            if any(pid in tick(instants[i], tc, keep_open=pid)["completed"] for i in near(tc)):
                matched_c.append(pid)
                continue
            # A completion is a touch, and two readers catch it at different instants: carry on with
            # the position held open and say how much later the replay completes it, or that it does
            # not. Reported, not counted: the declared measure is the window.
            later = None
            for i, t in enumerate(stamps):
                if t > tc + window_seconds and pid in tick(instants[i], t, keep_open=pid)["completed"]:
                    later = round((t - tc) / 60.0, 1)
                    break
            unmatched_c.append({"paper": pid, "completed_at": _iso(tc), "replay_completed_min_later": later})

        # Decisions paper never made: every sample, on paper's book as of that sample.
        extra, early = [], []
        paper_entries = [(_ts(p["entry_time"]), (p["side"], p["center"])) for p in paper_rows]
        for when, t in zip(instants, stamps, strict=True):
            res = tick(when, t)
            for e in res["entries"]:
                key = (e["side"], e["center"])
                if not any(k == key and abs(tp - t) <= window_seconds for tp, k in paper_entries):
                    extra.append((t, key))
            for q in res["completed"]:
                if not (q in paper_done and abs(paper_done[q] - t) <= window_seconds):
                    early.append((t, (q,)))
    finally:
        template.close()

    extra_episodes = _episodes(extra, gap=window_seconds)
    early_episodes = _episodes(early, gap=window_seconds)
    n_e, n_c = len(paper_rows), len(paper_done)
    # The completion OUTCOME, beside the declared timing measure: did the replay complete paper's
    # completed positions at all that session, and leave paper's strands uncompleted? Under paper's
    # live-limit rule the debit paid is the position's own limit, so this, not the minute, is what
    # moves P&L. Found after the four first sessions were read (plan, stage 2), so a report there;
    # declared as the completion test for sessions from 2026-10-19.
    strands = [p["position_id"] for p in paper_rows if not p.get("completed_at")]
    completed_by_replay = {e["key"][0] for e in early_episodes}
    outcome = {
        "paper_completed": n_c,
        "replay_also_completed": len(matched_c)
        + sum(1 for u in unmatched_c if u.get("replay_completed_min_later") is not None),
        "paper_stranded": len(strands),
        "replay_also_stranded": sum(1 for pid in strands if pid not in completed_by_replay),
    }
    credit_sorted = sorted(abs(m["credit_diff"]) for m in matched_entries)
    return {
        "ok": True,
        "day": day,
        "arm": arm,
        "samples": len(session.samples),
        "paper_entries": n_e,
        "entries_matched": len(matched_entries),
        "unmatched_entries": unmatched_entries,
        "extra_entry_episodes": [
            {
                "side": e["key"][0],
                "center": e["key"][1],
                "from": _iso(e["first"]),
                "to": _iso(e["last"]),
                "samples": e["samples"],
                "paper_said": _paper_reason(paper_conn, arm, day, e["first"]),
            }
            for e in extra_episodes
        ],  # fmt: skip
        "paper_completions": n_c,
        "completions_matched": len(matched_c),
        "unmatched_completions": unmatched_c,
        "early_completion_episodes": [
            {
                "paper": e["key"][0],
                "from": _iso(e["first"]),
                "samples": e["samples"],
                "paper_completed_at": _iso(paper_done[e["key"][0]]) if paper_done.get(e["key"][0]) else None,
            }
            for e in early_episodes
        ],  # fmt: skip
        "completion_outcome": outcome,
        "credit_abs_median": round(statistics.median(credit_sorted), 4) if credit_sorted else None,
        "credit_abs_p90": round(credit_sorted[int(0.9 * (len(credit_sorted) - 1))], 4)
        if credit_sorted
        else None,
        "matched_entries": matched_entries,
    }


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, replay.ET).isoformat(timespec="seconds")


def _paper_reason(paper_conn, arm: str, day: str, t: float) -> str | None:
    """Paper's own attempt outcome nearest at or before `t`: why paper did not enter then."""
    r = paper_conn.execute(
        "SELECT outcome, block_detail FROM fly_entry_attempts WHERE arm = ? AND trade_date = ? AND ts <= ? "
        "ORDER BY ts DESC LIMIT 1",
        (arm, day, _iso(t)),
    ).fetchone()
    if r is None:
        return None
    return r["outcome"] + (f": {r['block_detail']}" if r["block_detail"] else "")


def _outcome_share(sessions: list[dict]) -> dict:
    agree = sum(s["completion_outcome"]["replay_also_completed"] + s["completion_outcome"]["replay_also_stranded"]
                for s in sessions)  # fmt: skip
    total = sum(
        s["completion_outcome"]["paper_completed"] + s["completion_outcome"]["paper_stranded"]
        for s in sessions
    )
    return {"agree": agree, "positions": total, "share": round(agree / total, 4) if total else None}


def summarise(sessions: list[dict]) -> dict:
    """The declared pass test over sessions (`THRESHOLDS`), with each measure's value."""
    ok = [s for s in sessions if s.get("ok")]
    n_e = sum(s["paper_entries"] for s in ok)
    n_c = sum(s["paper_completions"] for s in ok)
    diffs = sorted(abs(m["credit_diff"]) for s in ok for m in s["matched_entries"])
    measures = {
        "sessions": len(ok),
        "entries_matched": round(sum(s["entries_matched"] for s in ok) / n_e, 4) if n_e else None,
        "extra_entry_episodes": round(sum(len(s["extra_entry_episodes"]) for s in ok) / n_e, 4)
        if n_e
        else None,
        "completions_matched": round(sum(s["completions_matched"] for s in ok) / n_c, 4) if n_c else None,
        "early_completion_episodes": round(sum(len(s["early_completion_episodes"]) for s in ok) / n_c, 4)
        if n_c
        else None,
        "credit_abs_median": round(statistics.median(diffs), 4) if diffs else None,
        "credit_abs_p90": round(diffs[int(0.9 * (len(diffs) - 1))], 4) if diffs else None,
    }
    th = THRESHOLDS

    def ge(v, x):
        return v is not None and v >= x

    def le(v, x):
        return v is not None and v <= x

    checks = {
        "entries_matched": ge(measures["entries_matched"], th["entries_matched_min"]),
        "extra_entry_episodes": le(measures["extra_entry_episodes"], th["extra_entry_episodes_max"]),
        "completions_matched": ge(measures["completions_matched"], th["completions_matched_min"]),
        "early_completion_episodes": le(
            measures["early_completion_episodes"], th["early_completion_episodes_max"]
        ),
        "credit_abs_median": le(measures["credit_abs_median"], th["credit_abs_median_max"]),
        "credit_abs_p90": le(measures["credit_abs_p90"], th["credit_abs_p90_max"]),
    }
    return {
        "measures": measures,
        "thresholds": th,
        "checks": checks,
        "enough_sessions": len(ok) >= 10,
        "passes": all(checks.values()) and len(ok) >= 10,
        # Reported beside the declared test (see `decision_check`): positions whose completed-or-
        # stranded outcome the replay reproduced, out of paper's.
        "completion_outcome_agreement": _outcome_share(ok),
    }
