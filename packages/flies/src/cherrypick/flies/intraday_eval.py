"""The intraday agent's qualification: what the evidence so far lets `/live-flies-start` offer.

docs/intraday-agent-plan.md, "Unlocking gates and closures". A deterministic evaluation over the
paper arms (`trend-rule` against `intraday-agent`, paired by session) and the agent's own records,
never written by the agent. It writes `data/flies/intraday_agent_qualification.json`, which the
console's agent page reads and step 4's live selection will read, so the two can never disagree about
what is unlocked.

Every criterion records the number it judged on, so an unlock can always be checked. A criterion
the suite cannot score yet (the live shadow's sign agreement and its re-priced closes, which arrive
with step 4) is `pass: null`, and null never unlocks: a mode stays locked until every one of its
criteria has actually passed.

The paired test uses each session's SETTLED net for the gates (closes are tags, so a settled net is
the gate's effect alone) and the net with closes at the 2x haircut for the closes. The one-sided 95%
bound uses Student's t, rounded toward caution past 30 degrees of freedom.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path

from cherrypick.core import home as _home

from cherrypick.flies import analytics, engine, intraday_advice, intraday_pack

SCHEMA = 1
RULE_ARM = "trend-rule"
CONTROL_ARM = "control"
MODES = intraday_advice.LIVE_MODES

MIN_DECISION_SESSIONS = 20
MIN_SHADOW_SESSIONS = 5
MIN_CLOSE_EPISODES = 60
#: Live-shadow sessions in which the agent's gate had an effect, live or paper, and the share of
#: them whose live effect must agree in sign with paper's.
MIN_SHADOW_AGREEMENT = 0.8
#: Live tagged closes before the shadow's closes are judged at all.
MIN_SHADOW_CLOSES = 10
#: The plan's working guess for the edge to detect, per session; `sessions_needed` re-derives the
#: sample from the observed spread of the paired difference at this edge.
TARGET_EDGE = 100.0

# One-sided 95% Student's t by degrees of freedom; past the table, the value at the band's lower end
# (larger, so the bound is never more generous than the exact one).
_T95 = (
    6.314, 2.920, 2.353, 2.132, 2.015, 1.943, 1.895, 1.860, 1.833, 1.812,
    1.796, 1.782, 1.771, 1.761, 1.753, 1.746, 1.740, 1.734, 1.729, 1.725,
    1.721, 1.717, 1.714, 1.711, 1.708, 1.706, 1.703, 1.701, 1.699, 1.697,
)  # fmt: skip


def t95(df: int) -> float:
    if df < 1:
        raise ValueError("needs at least one degree of freedom")
    if df <= len(_T95):
        return _T95[df - 1]
    if df <= 40:
        return 1.697
    if df <= 60:
        return 1.684
    if df <= 120:
        return 1.671
    return 1.658


def qualification_path() -> Path:
    return _home.data_dir("flies") / "intraday_agent_qualification.json"


def read_replay() -> dict | None:
    """The historical replay's scored sessions (intraday_replay.py), or None before it has run."""
    from cherrypick.flies import intraday_replay

    try:
        r = json.loads(intraday_replay.result_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return r if isinstance(r, dict) and isinstance(r.get("sessions"), dict) else None


def merge_replay(values: dict, decided: set, replay: dict | None) -> list[str]:
    """Fold the replay's sessions into the forward arms' values and the decided set, in place. The
    plan counts the replay toward the gate criteria (its pack has a look-ahead test). A session the
    forward arms also traded keeps the forward figures; the replay never overwrites a real one.
    Returns the sessions taken from the replay."""
    taken = []
    for day, s in sorted(((replay or {}).get("sessions") or {}).items()):
        if day in values["rule"] or day in values["agent"]:
            continue
        for k in ("control", "rule", "agent"):
            if s.get(k) is not None:
                values[k][day] = s[k]
        if s.get("decided"):
            decided.add(day)
        taken.append(day)
    return taken


def live_db_path() -> Path:
    from cherrypick.flies import db as dbmod

    return Path(dbmod.live_db_path())


def read_qualification() -> dict | None:
    try:
        q = json.loads(qualification_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return q if isinstance(q, dict) else None


def offered_live_modes(cfg: dict, qualification: dict | None) -> list[str]:
    """What /live-flies-start may offer today. The qualification file's `offered_modes` (or, with
    no file yet, no evidence: off and shadow), narrowed again by config's `live_mode_max` as it is
    NOW and by what the live loop can do. "off" always; nothing with the agent disabled."""
    acfg = intraday_advice.agent_config(cfg)
    if not acfg["enabled"]:
        return ["off"]
    evidence = (qualification or {}).get("offered_modes") or ["off", "shadow"]
    cap = MODES.index(acfg["live_mode_max"])
    out = [
        m for i, m in enumerate(MODES) if i <= cap and m in evidence and intraday_advice.built_mode(m) == m
    ]
    return out if "off" in out else ["off", *out]


# --------------------------------------------------------------------------- inputs
def session_values(conn, arm: str, start=None, end=None) -> dict[str, dict]:
    """Per session: entries, stranded verticals, settled net, and net with closes (natural, 2x)."""
    where, params = analytics._period_clause(start, end, arm)
    rows = conn.execute(
        f"SELECT {analytics._CLOSE_COLUMNS} FROM fly_positions WHERE {where} "
        "AND kind IN ('fly', 'short_vertical')",
        params,
    ).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        v = analytics.close_valued(r)
        s = out.setdefault(
            r["trade_date"],
            {
                "entries": 0,
                "stranded": 0,
                "tagged": 0,
                "settled_net": 0.0,
                "net_closes": 0.0,
                "net_closes_2x": 0.0,
            },
        )
        s["entries"] += 1
        s["stranded"] += r["kind"] == "short_vertical"
        s["tagged"] += v["tagged"]
        s["settled_net"] += v["settled_net"]
        s["net_closes"] += v["closed_net"]
        s["net_closes_2x"] += v["closed_net_2x"]
    for s in out.values():
        for k in ("settled_net", "net_closes", "net_closes_2x"):
            s[k] = round(s[k], 2)
    return out


def record_summary(records_by_session: dict[str, list[dict]]) -> dict:
    """From the agent's records: the sessions with an admissible paper decision, the sessions with a
    live-shadow decision, and the stranded-vertical episodes (distinct open verticals) a paper
    decision was made over."""
    paper, shadow, episodes = set(), set(), set()
    for session, recs in records_by_session.items():
        for r in recs:
            if not (r.get("called") and r.get("ok")):
                continue
            if r.get("target") == "live":
                shadow.add(session)
                continue
            if r.get("target") != "paper":
                continue
            paper.add(session)
            flies = (r.get("pack") or {}).get("flies") or {}
            ids = flies.get("_position_ids") or {}
            for p in flies.get("positions") or []:
                if p.get("state") == "open_vertical" and p.get("id") in ids:
                    episodes.add(ids[p["id"]])
    return {"paper_sessions": sorted(paper), "shadow_sessions": sorted(shadow), "episodes": len(episodes)}


def spend(records_by_session: dict[str, list[dict]]) -> list[dict]:
    """Per session and target: checks made, model calls, admissible replies, and cost, by model."""
    out = []
    for session, recs in sorted(records_by_session.items()):
        for target in intraday_advice.TARGETS:
            mine = [r for r in recs if r.get("target") == target]
            if not mine:
                continue
            called = [r for r in mine if r.get("called")]
            by_model: dict[str, dict] = {}
            for r in called:
                m = by_model.setdefault(str(r.get("model") or "unknown"), {"calls": 0, "cost_usd": 0.0})
                m["calls"] += 1
                m["cost_usd"] = round(m["cost_usd"] + float(r.get("cost_usd") or 0.0), 4)
            out.append(
                {
                    "session": session,
                    "target": target,
                    "checks": len(mine),
                    "calls": len(called),
                    "ok": sum(1 for r in called if r.get("ok")),
                    "cost_usd": round(sum(float(r.get("cost_usd") or 0.0) for r in called), 4),
                    "by_model": by_model,
                }
            )
    return out


def load_records() -> dict[str, list[dict]]:
    folder = intraday_advice.record_path("2000-01-01").parent
    if not folder.is_dir():
        return {}
    return {p.stem: intraday_advice.records(p.stem) for p in sorted(folder.glob("*.jsonl"))}


def _sign(v: float) -> int:
    return (v > 0) - (v < 0)


def shadow_scoring(live_rows: list, paper_agent: dict, paper_control: dict, live_closes: dict) -> dict:
    """The live shadow, scored (pure).

    Per session run in `shadow` mode, the agent gate's live effect is what refusing the entries it
    would have refused was worth: minus their settled net (a refused loser is a saving). Paper's is
    the agent arm against control on the same session: the same gate, applied. A session in which
    neither moved is no evidence either way and is not counted; the rest agree when the two have the
    same sign. `live_closes` is `analytics.close_tag_result` over the live arm: the shadow's named
    closes, priced at live natural when named."""
    by_session: dict[str, dict] = {}
    for r in live_rows:
        if r["agent_mode"] != "shadow":
            continue
        s = by_session.setdefault(r["trade_date"], {"entries": 0, "would_refuse": 0, "live_effect": 0.0})
        s["entries"] += 1
        if r["agent_would_refuse"]:
            s["would_refuse"] += 1
            s["live_effect"] -= float(r["pnl"] if r["pnl"] is not None else 0.0)
    sessions = []
    for day, s in sorted(by_session.items()):
        paper = None
        if day in paper_agent and day in paper_control:
            paper = round(paper_agent[day]["settled_net"] - paper_control[day]["settled_net"], 2)
        live_effect = round(s["live_effect"], 2)
        moved = paper is not None and (live_effect != 0 or paper != 0)
        sessions.append(
            {
                "session": day,
                **s,
                "live_effect": live_effect,
                "paper_effect": paper,
                "counted": moved,
                "agree": (_sign(live_effect) == _sign(paper)) if moved else None,
            }
        )
    counted = [x for x in sessions if x["counted"]]
    agree = sum(1 for x in counted if x["agree"])
    return {
        "sessions": sessions,
        "counted": len(counted),
        "agree": agree,
        "agreement": round(agree / len(counted), 4) if counted else None,
        "closes": {
            "tagged": int(live_closes.get("tagged") or 0),
            "saved": live_closes.get("saved"),
            "saved_2x": live_closes.get("saved_2x"),
        },
    }


def _shadow_inputs(live_db: Path, live_arm: str) -> tuple[list, dict]:
    """Settled live rows with the agent's stamp, and the live arm's tagged-close valuation. Empty
    when there is no live ledger, or one that predates the stamp."""
    import sqlite3

    if not live_db.exists():
        return [], {}
    conn = sqlite3.connect(f"file:{live_db.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(fly_positions)")}
        if not {"agent_mode", "agent_would_refuse", "close_tag_natural", "close_tag_fees"} <= cols:
            return [], {}
        where, params = analytics._period_clause(None, None, live_arm)
        rows = conn.execute(
            f"SELECT trade_date, agent_mode, agent_would_refuse, pnl FROM fly_positions WHERE {where} "
            "AND agent_mode IS NOT NULL",
            params,
        ).fetchall()
        return list(rows), analytics.close_tag_result(conn, live_arm)
    finally:
        conn.close()


# --------------------------------------------------------------------------- the evaluation
def paired_stats(diffs: list[float]) -> dict:
    """Mean, spread and one-sided 95% lower bound of a paired difference, and the sessions the
    observed spread needs to detect TARGET_EDGE at 80% power."""
    n = len(diffs)
    out = {"n": n, "mean": None, "sd": None, "lower_95": None, "sessions_needed": None}
    if n == 0:
        return out
    mean = sum(diffs) / n
    out["mean"] = round(mean, 2)
    if n < 2:
        return out
    sd = math.sqrt(sum((d - mean) ** 2 for d in diffs) / (n - 1))
    out["sd"] = round(sd, 2)
    out["lower_95"] = round(mean - t95(n - 1) * sd / math.sqrt(n), 2)
    out["sessions_needed"] = math.ceil(((1.645 + 0.842) * sd / TARGET_EDGE) ** 2) if sd > 0 else None
    return out


def _criterion(cid, mode, label, value, threshold, passed):
    return {"id": cid, "mode": mode, "label": label, "value": value, "threshold": threshold, "pass": passed}


def evaluate(
    rule: dict,
    agent: dict,
    summary: dict,
    *,
    live_mode_max: str,
    generated_at: str,
    arms: dict,
    shadow: dict | None = None,
) -> dict:
    """Pure: the qualification from per-session arm values, the record summary, and the scored live
    shadow (`shadow_scoring`; None scores nothing)."""
    decided = set(summary["paper_sessions"])
    paired = sorted(set(rule) & set(agent) & decided)
    gate_diffs = [agent[s]["settled_net"] - rule[s]["settled_net"] for s in paired]
    close_diffs = [agent[s]["net_closes_2x"] - rule[s]["net_closes_2x"] for s in paired]
    gates = paired_stats(gate_diffs)
    closes = paired_stats(close_diffs)

    def rate(values, key):
        entries = sum(values[s]["entries"] for s in paired)
        return round(sum(values[s][key] for s in paired) / entries, 4) if entries else None

    strand_rule, strand_agent = rate(rule, "stranded"), rate(agent, "stranded")
    saved_2x = round(sum(agent[s]["net_closes_2x"] - agent[s]["settled_net"] for s in paired), 2)
    shadow = shadow or {"sessions": [], "counted": 0, "agree": 0, "agreement": None, "closes": {}}
    counted, agreement = shadow["counted"], shadow["agreement"]
    shadow_closes = shadow.get("closes") or {}
    live_tagged = int(shadow_closes.get("tagged") or 0)

    criteria = [
        _criterion(
            "decision_sessions",
            "gates",
            "sessions with the agent's decisions recorded",
            len(decided),
            MIN_DECISION_SESSIONS,
            len(decided) >= MIN_DECISION_SESSIONS,
        ),
        _criterion(
            "beats_rule",
            "gates",
            "agent beats trend-rule on settled net per session (one-sided 95%)",
            gates["lower_95"],
            0,
            None if gates["lower_95"] is None else gates["lower_95"] > 0,
        ),
        _criterion(
            "strands_no_more",
            "gates",
            "agent's strand rate no higher than trend-rule's",
            strand_agent,
            strand_rule,
            None if strand_agent is None or strand_rule is None else strand_agent <= strand_rule,
        ),
        _criterion(
            "live_shadow",
            "gates",
            f"live-shadow sessions where the gate moved, {int(MIN_SHADOW_AGREEMENT * 100)}% agreeing in sign with paper",
            counted,
            MIN_SHADOW_SESSIONS,
            None if counted == 0 else (counted >= MIN_SHADOW_SESSIONS and agreement >= MIN_SHADOW_AGREEMENT),
        ),
        _criterion(
            "close_episodes",
            "gates_and_closures",
            "stranded-vertical episodes with a close decision",
            summary["episodes"],
            MIN_CLOSE_EPISODES,
            summary["episodes"] >= MIN_CLOSE_EPISODES,
        ),
        _criterion(
            "closes_survive_2x",
            "gates_and_closures",
            "the agent's closes still save money at the 2x haircut",
            saved_2x,
            0,
            None if not summary["episodes"] else saved_2x > 0,
        ),
        _criterion(
            "shadow_closes_live",
            "gates_and_closures",
            f"the shadow's closes, at live natural, still save money (at least {MIN_SHADOW_CLOSES})",
            shadow_closes.get("saved"),
            0,
            None
            if live_tagged == 0
            else (live_tagged >= MIN_SHADOW_CLOSES and (shadow_closes.get("saved") or 0) > 0),
        ),
    ]
    gates_ok = all(c["pass"] is True for c in criteria if c["mode"] == "gates")
    closes_ok = gates_ok and all(c["pass"] is True for c in criteria if c["mode"] == "gates_and_closures")
    cap = MODES.index(live_mode_max) if live_mode_max in MODES else MODES.index("shadow")
    unlocked = {"off": True, "shadow": True, "gates": gates_ok, "gates_and_closures": closes_ok}
    # Evidence decides `unlocked`; what live can DO narrows `offered` (no live closing path yet).
    offered = [
        m for i, m in enumerate(MODES) if i <= cap and unlocked[m] and intraday_advice.built_mode(m) == m
    ]
    return {
        "schema": SCHEMA,
        "generated_at": generated_at,
        "arms": arms,
        "live_mode_max": live_mode_max,
        "sessions": {"decided": len(decided), "paired": paired, "shadow": summary["shadow_sessions"]},
        "gates": {**gates, "target_edge": TARGET_EDGE},
        "closes": {**closes, "episodes": summary["episodes"], "saved_2x": saved_2x},
        "strand_rate": {"rule": strand_rule, "agent": strand_agent},
        "shadow": shadow,
        "live_closes_built": intraday_advice.LIVE_CLOSES_BUILT,
        "criteria": criteria,
        "unlocked": unlocked,
        "offered_modes": offered,
    }


def run(conn, cfg: dict, *, write: bool = False, now: datetime | None = None) -> dict:
    """Gather, evaluate, and (with `write`) replace the qualification file atomically."""
    acfg = intraday_advice.agent_config(cfg)
    agent_arm = acfg["paper_arm"]
    records = load_records()
    summary = record_summary(records)
    values = {"control": session_values(conn, CONTROL_ARM), "rule": session_values(conn, RULE_ARM)}
    values["agent"] = session_values(conn, agent_arm)
    decided_set = set(summary["paper_sessions"])
    replayed = merge_replay(values, decided_set, read_replay())
    summary = {**summary, "paper_sessions": sorted(decided_set)}
    live_arm = (cfg.get("live") or {}).get("arm", "control")
    live_rows, live_closes = _shadow_inputs(live_db_path(), live_arm)
    result = evaluate(
        values["rule"],
        values["agent"],
        summary,
        live_mode_max=acfg["live_mode_max"],
        generated_at=(now or datetime.now(UTC)).isoformat(timespec="seconds"),
        arms={"control": CONTROL_ARM, "rule": RULE_ARM, "agent": agent_arm, "live": live_arm},
        shadow=shadow_scoring(live_rows, values["agent"], values["control"], live_closes),
    )
    # For the console's agent page, so it shows history without re-deriving any of it: each session's
    # three arms (only sessions where the rule or agent arm traded), the settled tagged closes, spend.
    days = sorted(set(values["rule"]) | set(values["agent"]))
    decided = set(summary["paper_sessions"])
    result["per_session"] = [
        {
            "session": d,
            "decided": d in decided,
            "source": "replay" if d in replayed else "forward",
            **{k: values[k].get(d) for k in ("control", "rule", "agent")},
        }
        for d in days
    ]
    result["replayed_sessions"] = replayed
    result["tagged_closes"] = [
        {"arm": arm, **c}
        for arm in (RULE_ARM, agent_arm)
        for c in analytics.close_tag_result(conn, arm)["closes"]
    ]
    result["spend"] = spend(records)
    # The band the agent arm's gate resolves to, so a chart draws the band the gate uses.
    band = engine.merged_params(cfg, agent_arm).get("regime_trend_points", intraday_pack.TREND_BAND_POINTS)
    result["trend_band_points"] = float(band)
    if write:
        path = qualification_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(result, indent=2), encoding="utf-8")
        tmp.replace(path)
    return result
