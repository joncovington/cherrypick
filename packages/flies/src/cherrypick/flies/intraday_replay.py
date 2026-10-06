"""The intraday agent's historical replay: the trend gate, replayed over recorded sessions
(docs/intraday-agent-plan.md, "Answering in weeks").

The model is asked at each minute of a past session through the same `run_tick` the forward job uses,
on packs built as of that minute (`intraday_pack`, whose look-ahead test is mutant
`flies-intraday-pack-lookahead`). Its records go to their own store (`REPLAY_STORE`). Everything
here is the deterministic half: which sessions, which minutes, and the scoring. The model call is
`scripts/flies_intraday_replay.py`.

**What is scored: the gate, exactly.** Paper `control` stamped every entry with its drift from the
open (`entry_trend_value`) and the direction it needed to complete, so whether the trend gate would
have refused it is a pure function of the row (`engine.completion_opposes_drift`, the gate's own
test). Structures are independent, so a session under a rule is its entries less the ones the rule
refuses (the `replay_gates.py` pattern; a freed cadence slot is not modelled):
- `trend-rule` refuses every entry that opposes a committed drift;
- `intraday-agent` refuses those only while its gate is on: a decision made before the entry and
  within `decision_ttl_minutes` of it, or, with none, the rule's gate, as the forward arm does.

**What is not: the closes.** Past paper sessions recorded no intraday quotes for a vertical, so a
close cannot be priced at natural after the fact. The model's close decisions are recorded with
their packs, and closes are valued only by the forward arms, at real quotes.

The agent sees `control`'s positions in its packs: the arm whose entries the replay scores, not the
agent arm's own (which did not exist). A pack therefore shows the agent entries its own gate would
have refused; the forward arm is the one that sees its own book.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.core import home as _home

from cherrypick.flies import engine, intraday_advice, intraday_pack

ET = ZoneInfo("America/New_York")
#: The first session with every pack block recorded (minute market readings began 2026-08-24).
REPLAY_FROM = "2026-08-24"
#: Before the forward arms: a replayed session must never be one the forward arms also traded.
REPLAY_TO = "2026-10-05"
REPLAY_ARM = "control"
SCHEMA = 1
DONE = "replay_done"


def result_path() -> Path:
    return _home.data_dir("flies") / "intraday_agent_replay.json"


def sessions(ledger_conn, *, start: str = REPLAY_FROM, end: str = REPLAY_TO) -> list[str]:
    """The sessions `control` settled in the window, the only ones with outcomes to score."""
    return [
        r[0]
        for r in ledger_conn.execute(
            "SELECT DISTINCT trade_date FROM fly_positions WHERE arm = ? AND symbol = 'SPX' AND status = 'settled' "
            "AND void_reason IS NULL AND trade_date >= ? AND trade_date <= ? ORDER BY trade_date",
            (REPLAY_ARM, start, end),
        )
    ]


def minutes(session: str) -> list[float]:
    """The forward job's cadence over a past session: each minute from 09:30 to 15:59 ET, as epochs."""
    day = datetime.fromisoformat(session).date()
    return [
        datetime.combine(day, time(h, m), tzinfo=ET).timestamp()
        for h in range(9, 16)
        for m in range(60)
        if (h, m) >= (9, 30)
    ]


def is_done(recs: list[dict]) -> bool:
    return any(r.get(DONE) for r in recs)


def resume_from(recs: list[dict]) -> float | None:
    """The last minute already checked, so an interrupted session resumes after it."""
    checked = [float(r["as_of"]) for r in recs if isinstance(r.get("as_of"), (int, float))]
    return max(checked) if checked else None


# --------------------------------------------------------------------------- scoring
def _epoch(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=ET)).timestamp()


def gate_at(decisions: list[dict], at: float, ttl_seconds: float) -> str | None:
    """The agent's gate in force at `at`: the latest admissible decision made before it and still
    fresh, else None (the arm runs the rule's gate)."""
    live = [d for d in decisions if d["as_of"] < at <= d["as_of"] + ttl_seconds]
    return max(live, key=lambda d: d["as_of"])["trend_gate"] if live else None


def opposes(row: dict, band: float) -> bool:
    """Would the trend gate refuse this entry? The gate's own test, on the drift the row stamped."""
    drift = row.get("entry_trend_value")
    if drift is None:
        return False  # the gate fails open on a missing open, and so does its replay
    snapshot = {"underlying_price": float(drift), "session": {"day_open": 0.0}}
    return engine.completion_opposes_drift(snapshot, {"regime_trend_points": band}, row["side"])[0]


def _arm_day(rows: list[dict]) -> dict:
    net = round(sum(float(r["pnl"] or 0.0) for r in rows), 2)
    return {
        "entries": len(rows),
        "stranded": sum(1 for r in rows if r["kind"] == "short_vertical"),
        "tagged": 0,
        "settled_net": net,
        # No close is valued in the replay (no quotes): with closes is the settled net.
        "net_closes": net,
        "net_closes_2x": net,
    }


def score_session(rows: list[dict], decisions: list[dict], *, band: float, ttl_seconds: float) -> dict:
    """One session under each rule: `control` as recorded, `trend-rule` and `intraday-agent`."""
    rule, agent, admitted = [], [], 0
    for r in rows:
        against = opposes(r, band)
        if not against:
            rule.append(r)
            agent.append(r)
            continue
        at = _epoch(r["entry_time"])
        gate = gate_at(decisions, at, ttl_seconds) if at is not None else None
        if gate == "off":
            agent.append(r)
            admitted += 1
    return {
        "control": _arm_day(rows),
        "rule": _arm_day(rule),
        "agent": _arm_day(agent),
        "refused_by_rule": len(rows) - len(rule),
        "admitted_by_agent": admitted,
    }


def decisions_of(recs: list[dict]) -> list[dict]:
    return [
        {"as_of": float(r["as_of"]), "trend_gate": r["decision"]["trend_gate"]}
        for r in recs
        if r.get("called") and r.get("ok") and isinstance(r.get("decision"), dict)
    ]


def load_rows(ledger_conn, session: str) -> list[dict]:
    return [
        dict(r)
        for r in ledger_conn.execute(
            "SELECT position_id, kind, side, entry_time, entry_trend_value, pnl FROM fly_positions "
            "WHERE arm = ? AND symbol = 'SPX' AND status = 'settled' AND void_reason IS NULL AND trade_date = ? "
            "AND kind IN ('fly', 'short_vertical') ORDER BY entry_time",
            (REPLAY_ARM, session),
        )
    ]


def score(ledger_conn, cfg: dict, *, now: datetime | None = None, write: bool = False) -> dict:
    """Every finished replayed session, scored, and (with `write`) the result file replaced. A
    session counts only once its replay ran to the bell (`DONE`)."""
    acfg = intraday_advice.agent_config(cfg)
    band = float(
        engine.merged_params(cfg, acfg["paper_arm"]).get(
            "regime_trend_points", intraday_pack.TREND_BAND_POINTS
        )
    )
    ttl = float(acfg["decision_ttl_minutes"]) * 60
    out: dict[str, dict] = {}
    calls = cost = 0.0
    models: set[str] = set()
    for session in sessions(ledger_conn):
        recs = intraday_advice.records(session, intraday_advice.REPLAY_STORE)
        if not is_done(recs):
            continue
        decisions = decisions_of(recs)
        called = [r for r in recs if r.get("called")]
        calls += len(called)
        cost += sum(float(r.get("cost_usd") or 0.0) for r in called)
        models |= {str(r["model"]) for r in called if r.get("model")}
        out[session] = {
            **score_session(load_rows(ledger_conn, session), decisions, band=band, ttl_seconds=ttl),
            "decided": bool(decisions),
            "calls": len(called),
            "ok": len(decisions),
        }
    result = {
        "schema": SCHEMA,
        "generated_at": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
        "window": [REPLAY_FROM, REPLAY_TO],
        "arm_replayed": REPLAY_ARM,
        "models": sorted(models),
        "band_points": band,
        "decision_ttl_minutes": acfg["decision_ttl_minutes"],
        "calls": int(calls),
        "cost_usd": round(cost, 2),
        "scores_closes": False,
        "sessions": out,
    }
    if write:
        path = result_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(result, indent=2), encoding="utf-8")
        tmp.replace(path)
    return result
