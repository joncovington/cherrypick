"""The intraday agent's deterministic half: when to ask, what a valid answer is, the decision file a
loop reads, and the record of every pack (docs/intraday-agent-plan.md).

**No model is called from here.** The suite keeps AI outside the packages (root CLAUDE.md): the call
lives in `scripts/flies_intraday_agent.py`, which hands `run_tick` an `ask(prompt, pack_json)`
function. Everything this module decides -- whether a tick is worth a call, whether a reply is
admissible, what a loop may read and for how long -- is a pure rule, so a test drives a whole tick
with a fake `ask`, and the replay drives the same tick over past sessions.

**A decision is advice with an expiry.** It is written to one file per target (`paper`, `live`)
with `expires_at`; `read_decision` returns None for a missing, stale, other-session or invalid file,
and a None means the arm runs the fixed rule for that tick. A failure here costs a tick's advice,
never a fill.

**Every pack is kept** (`data/flies/intraday_agent/<session>.jsonl`), with its decision, reason,
model and cost, whether or not a call was made: the material the deterministic rule is later fitted
from.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from cherrypick.core import home as _home

from cherrypick.flies import engine, intraday_pack

DEFAULTS = {
    "enabled": False,
    "model": "opus",
    "paper": True,
    "paper_arm": "intraday-agent",
    "live_mode_max": "shadow",
    "trigger_band_points": 10.0,
    "decision_ttl_minutes": 10,
    # 100, not 40 (2026-10-06): a vertical is open most of the session, so at a 4-minute gap the
    # agent is asked ~95 times; 40 ran out by ~12:20 on all 29 replayed sessions, leaving every
    # afternoon -- when verticals strand -- to the rule.
    "max_calls_per_session": 100,
    "min_minutes_between_calls": 4,
}
LIVE_MODES = ("off", "shadow", "gates", "gates_and_closures")
#: No live closing-order path exists yet. Until one is built and this flips, `gates_and_closures` is
#: never offered and never in force on live: a record naming it runs as `gates`. Closes stay tags
#: (close_tags.py), on live as on paper, so the shadow's would-be closes are still scored.
LIVE_CLOSES_BUILT = False
TARGETS = ("paper", "live")
REASON_MAX_WORDS = 40


def agent_config(cfg: dict) -> dict:
    """The `intraday_agent` block over its defaults. Off unless the config says otherwise."""
    block = cfg.get("intraday_agent") or {}
    out = {**DEFAULTS, **{k: v for k, v in block.items() if not str(k).startswith("_")}}
    if out["live_mode_max"] not in LIVE_MODES:
        out["live_mode_max"] = "off"  # an unknown mode never widens what live may do
    return out


_LIVE_RANK = {m: i for i, m in enumerate(LIVE_MODES)}


def live_mode_today(acfg: dict, arm_record: dict | None, session: str) -> str:
    """What the agent may do on live today: the mode chosen in /live-flies-start and written on
    today's arm record, CAPPED by `live_mode_max`. "off" when the agent is disabled, there is no
    record, the record is for another day, or no mode was chosen. The cap is the guard: config can
    only narrow the day's choice, never widen it (mutant `flies-intraday-live-mode-cap`)."""
    if not acfg.get("enabled") or not arm_record:
        return "off"
    if session not in (arm_record.get("date"), arm_record.get("armed_for")):
        return "off"
    chosen = (arm_record.get("intraday_agent") or {}).get("mode") or "off"
    if chosen not in _LIVE_RANK:
        return "off"
    return built_mode(_capped(chosen, acfg["live_mode_max"]))


def built_mode(mode: str) -> str:
    """`mode`, narrowed to what the live loop can actually do (LIVE_CLOSES_BUILT)."""
    return "gates" if mode == "gates_and_closures" and not LIVE_CLOSES_BUILT else mode


def live_tick(
    params: dict, acfg: dict, arm_record: dict | None, session: str, now: float
) -> tuple[dict, dict]:
    """The live arm's params this tick, and the agent context to stamp on what it enters.

    `shadow` never changes a param: the decision is read so each entry can carry what the agent's
    gate would have done. From `gates` up, a fresh live decision sets the arm's trend gate; with no
    fresh decision the arm keeps its own declared gate, so a missing agent costs advice, never a
    change to the arm. Never mutates `params`."""
    mode = live_mode_today(acfg, arm_record, session)
    ctx = {"mode": mode, "decision": None}
    if mode == "off":
        return params, ctx
    decision = read_decision("live", session=session, now=now)
    ctx["decision"] = decision
    if decision is not None and _LIVE_RANK[mode] >= _LIVE_RANK["gates"]:
        params = {**params, "refuse_completion_against_trend": decision["trend_gate"] == "on"}
    return params, ctx


def entry_stamp(ctx: dict, snapshot: dict, params: dict, side: str) -> dict:
    """The columns a live entry carries about the agent: the day's mode, the gate a fresh decision
    held, and whether that gate would have refused THIS entry (`engine.completion_opposes_drift`,
    the gate's own test). The live shadow is scored on the last one. Empty with the agent off."""
    if ctx.get("mode", "off") == "off":
        return {}
    decision = ctx.get("decision")
    if decision is None:
        return {"agent_mode": ctx["mode"], "agent_gate": None, "agent_would_refuse": None}
    opposes, _drift = engine.completion_opposes_drift(snapshot, params, side)
    return {
        "agent_mode": ctx["mode"],
        "agent_gate": decision["trend_gate"],
        "agent_would_refuse": int(decision["trend_gate"] == "on" and opposes),
    }


def _capped(chosen: str, ceiling: str) -> str:
    return min(chosen, ceiling, key=lambda m: _LIVE_RANK[m])


# --------------------------------------------------------------------------- when to ask
def trigger(pack: dict, acfg: dict) -> str | None:
    """Why this instant is worth a call, or None. Only two things are decisions: an open vertical
    (close it or not, and is the trend still on) and spot near the trend band (is it becoming or
    failing a trend). Anything else would spend a call restating the rule."""
    flies = pack.get("flies") or {}
    if flies.get("open_verticals"):
        return "open_vertical"
    spx = pack.get("spx") or {}
    if spx.get("vs_open_points") is None:
        return None
    distance = abs(abs(spx["vs_open_points"]) - intraday_pack.TREND_BAND_POINTS)
    return "near_trend_band" if distance <= float(acfg["trigger_band_points"]) else None


# --------------------------------------------------------------------------- the reply
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def validate(reply: str | None, pack: dict) -> dict:
    """`{"ok": True, "decision": {...}}` or `{"ok": False, "error": ...}` for one model reply.

    Admissible means: one JSON object; `trend_gate` on/off; `close_stranded` naming only labels the
    pack showed as open verticals (mapped back to real position ids here, never by the model);
    `confidence` in [0, 1]; a non-empty `reason`.

    A reason over REASON_MAX_WORDS words is CLIPPED to that length and flagged `reason_clipped`, not
    refused (2026-10-06). The reason is a note for people; the gate and the closes are what an arm
    acts on. Refusing the whole reply over its length threw away 11 of 43 Opus decisions on the
    replay's first session, every one an open-vertical call, where the model has the most to say,
    and each one cost its arm a tick of the fixed rule."""
    if not reply:
        return {"ok": False, "error": "empty reply"}
    text = _FENCE.sub("", reply.strip()).strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return {"ok": False, "error": "not one JSON object"}
    if not isinstance(obj, dict):
        return {"ok": False, "error": "not one JSON object"}
    gate = obj.get("trend_gate")
    if gate not in ("on", "off"):
        return {"ok": False, "error": f"trend_gate must be on or off, got {gate!r}"}
    flies = pack.get("flies") or {}
    ids = flies.get("_position_ids") or {}
    open_labels = {p["id"] for p in flies.get("positions", []) if p.get("state") == "open_vertical"}
    labels = obj.get("close_stranded", [])
    if not isinstance(labels, list) or not all(isinstance(x, str) for x in labels):
        return {"ok": False, "error": "close_stranded must be a list of position labels"}
    bad = [x for x in labels if x not in open_labels]
    if bad:
        return {"ok": False, "error": f"close_stranded names positions that are not open verticals: {bad}"}
    # A bound, not a refusal: a close named for a vertical already at or past its wing width saves
    # almost nothing (the loss is capped at the width), pays fees and gives up any reversal. The
    # agent named exactly those on its first real run (2026-09-21 12:30) and reasoned it backwards,
    # so they are dropped here and recorded as dropped, and the rest of the decision stands.
    by_label = {p["id"]: p for p in flies.get("positions", [])}
    dropped = [
        x
        for x in labels
        if (by_label[x].get("spot_past_short_points") or 0) >= float(by_label[x].get("wing") or 0) > 0
    ]
    labels = [x for x in labels if x not in dropped]
    conf = obj.get("confidence")
    if not isinstance(conf, (int, float)) or not 0 <= float(conf) <= 1:
        return {"ok": False, "error": "confidence must be a number in [0, 1]"}
    reason = obj.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return {"ok": False, "error": "reason is required"}
    words = reason.split()
    clipped = len(words) > REASON_MAX_WORDS
    return {
        "ok": True,
        "decision": {
            "trend_gate": gate,
            "close_stranded": [ids[x] for x in labels if x in ids],
            "close_labels": labels,
            "dropped_closes": [{"label": x, "why": "past_wing_width"} for x in dropped],
            "confidence": round(float(conf), 3),
            "reason": " ".join(words[:REASON_MAX_WORDS]) if clipped else reason.strip(),
            "reason_clipped": clipped,
        },
    }


# --------------------------------------------------------------------------- the decision file
def decision_path(target: str) -> Path:
    return _home.state_dir() / f"flies-intraday-advice-{target}.json"


def write_decision(target: str, record: dict) -> Path:
    path = decision_path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return path


def read_decision(target: str, *, session: str, now: float) -> dict | None:
    """The decision a loop may act on now, or None (missing, unreadable, another session, expired,
    or not admissible): None means the fixed rule this tick."""
    try:
        rec = json.loads(decision_path(target).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if rec.get("session") != session or rec.get("ok") is not True:
        return None
    if not isinstance(rec.get("expires_at"), (int, float)) or now > rec["expires_at"]:
        return None
    return rec.get("decision")


# --------------------------------------------------------------------------- the record of packs
#: The historical replay's own store (intraday_replay.py), beside the forward records and never
#: mixed with them: a replayed call must not count toward a live session's call cap, and the forward
#: readers glob only the top folder.
REPLAY_STORE = "replay"


def record_path(session: str, store: str = "") -> Path:
    folder = _home.data_dir("flies") / "intraday_agent"
    return (folder / store if store else folder) / f"{session}.jsonl"


def records(session: str, store: str = "") -> list[dict]:
    try:
        lines = record_path(session, store).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _append(session: str, rec: dict, store: str = "") -> None:
    path = record_path(session, store)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, default=str) + "\n")


# --------------------------------------------------------------------------- one tick
def run_tick(
    *,
    cfg: dict,
    target: str,
    session: str,
    as_of: float,
    arm: str,
    gex_conn,
    ledger_conn,
    ask,
    prompt: str,
    write_file: bool = True,
    store: str = "",
    pace: bool = True,
) -> dict:
    """One check: build the pack, decide whether it is worth a call, ask, validate, record.

    `ask(prompt, pack_json)` returns `{"reply", "model", "cost_usd", "error"}`; it is the script's
    model call, or a fake in a test or the replay. `write_file=False` (the replay) records the pack
    and decision but never writes the decision file a loop reads; `store` (the replay's
    REPLAY_STORE) keeps its records, and so its call cap, apart from the forward record. `pace=False`
    (the targeted replay, which samples only the minutes that can change a score) skips the cap and
    the gap, which pace the forward job's cadence; the trigger still applies."""
    if target not in TARGETS:
        raise ValueError(f"target must be one of {TARGETS}")
    acfg = agent_config(cfg)
    raw = intraday_pack.build_pack(
        gex_conn=gex_conn, ledger_conn=ledger_conn, session=session, as_of=as_of, arm=arm
    )
    base = {
        "target": target,
        "session": session,
        "as_of": as_of,
        "arm": arm,
        "pack_version": raw["pack_version"],
        "pack_blocks": intraday_pack.completeness(raw),
    }
    why = trigger(raw, acfg)
    if why is None:
        return {**base, "called": False, "skipped": "no_trigger"}
    mine = (
        [r for r in records(session, store) if r.get("target") == target and r.get("called")] if pace else []
    )
    if len(mine) >= int(acfg["max_calls_per_session"]):
        return {**base, "called": False, "skipped": "call_cap"}
    if mine and as_of - float(mine[-1]["as_of"]) < float(acfg["min_minutes_between_calls"]) * 60:
        return {**base, "called": False, "skipped": "too_soon"}

    started = time.time()
    answer = ask(prompt, json.dumps(intraday_pack.for_model(raw)))
    verdict = (
        validate(answer.get("reply"), raw)
        if not answer.get("error")
        else {"ok": False, "error": answer["error"]}
    )
    rec = {
        **base,
        "called": True,
        "trigger": why,
        "model": answer.get("model"),
        "cost_usd": answer.get("cost_usd"),
        "seconds": round(time.time() - started, 1),
        "ok": verdict["ok"],
        "decision": verdict.get("decision"),
        "error": verdict.get("error"),
        "pack": raw,
    }
    _append(session, rec, store)
    if write_file:
        write_decision(
            target,
            {
                "session": session,
                "as_of": as_of,
                "expires_at": as_of + float(acfg["decision_ttl_minutes"]) * 60,
                "ok": verdict["ok"],
                "decision": verdict.get("decision"),
                "error": verdict.get("error"),
                "model": answer.get("model"),
                "arm": arm,
            },
        )
    return {k: v for k, v in rec.items() if k != "pack"}
