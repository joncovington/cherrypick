"""A passing item's paper arm read against its backtest (#1080).

A pass becomes a paper arm, and nothing compared that arm's paper record with what the backtest
predicted. A register row that passed declares, before any paper is read, its `forward` check:

    "forward": {
        "module": "weeklyput", "schema": "weeklyput_put", "arm": "unhedged",
        "measure": "cycle_mean",          # or "session_sum"
        "field": "net_bp",                # the per-trade field the measure averages or sums
        "since": "2026-11-09",            # the first session read
        "min_obs": 30,                    # cycles or sessions before any state but collecting
        "reference": {"file": "{home}/data/backtest/results/research/item57/results.json",
                      "mean": "arms.unhedged.mean_bp", "t": "arms.unhedged.t", "n": "arms.unhedged.cycles"},
        "kill_z": -2.0, "drift_z": -1.0,
        "beside": ["pm1400-n1"]           # optional arms reported with it, never judged
    }

A row with more than one arm to judge declares a list of such blocks, one per arm. Two optional keys:

- `per`: a per-trade field the measured one is divided by before the measure is taken
  (`"per": "capital"` reads each trade's net over its capital, a return that does not depend on
  the size paper trades at);
- `reference: null` declares that the paper arm is judged on its own record (`paper_alone`) from
  the start: for a study whose result holds no reference in paper's units, or whose reference is
  purged with the licensed data before paper could reach `min_obs`.

The measure turns the arm's closed paper trades (`core.ledgers`) into one value per observation:
`cycle_mean` averages `field` over the trades settling in a session (item 57's equal-weight cycle);
`session_sum` sums it per session (item 73's session net). The reference is the backtest's own
mean per observation, with its spread taken from the study's t and count (sd = mean x sqrt(n) / t),
read from the local result file, which is derived from licensed data and never leaves the machine.

The state, from z = (paper mean - reference mean) / (reference sd / sqrt(paper n)):

- `collecting` until `min_obs` observations;
- `kill` at z <= `kill_z`: paper is running clearly below what the backtest predicted;
- `drift` at z <= `drift_z`, or a paper mean at or below zero;
- `confirm` otherwise.

A kill is a recommendation to the user, never a config change. Deterministic; reads only.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

MEASURES = ("cycle_mean", "session_sum")
NEEDED = ("module", "schema", "arm", "measure", "field", "since", "min_obs", "reference", "kill_z", "drift_z")
COLLECTING, KILL, DRIFT, CONFIRM = "collecting", "kill", "drift", "confirm"


def specs(forward: Any) -> list:
    """A row's forward blocks: one object, or a list of them (one per judged arm)."""
    return list(forward) if isinstance(forward, list) else [forward]


def problems(forward: Any) -> list[str]:
    """What is wrong with a row's `forward` declaration (one block or a list); empty when sound."""
    blocks = specs(forward)
    if not blocks:
        return ["forward must declare at least one block"]
    if len(blocks) == 1:
        return _block_problems(blocks[0])
    out = [f"forward[{i}]: {p}" for i, b in enumerate(blocks) for p in _block_problems(b)]
    arms = [b.get("arm") for b in blocks if isinstance(b, dict)]
    if len(set(arms)) != len(arms):
        out.append("forward: each block judges a different arm")
    return out


def _block_problems(spec: Any) -> list[str]:
    if not isinstance(spec, dict):
        return ["forward must be an object"]
    out = [f"forward needs {k}" for k in NEEDED if k not in spec]
    if spec.get("measure") not in MEASURES:
        out.append(f"forward.measure is one of {', '.join(MEASURES)}")
    if not isinstance(spec.get("min_obs"), int) or spec.get("min_obs", 0) < 1:
        out.append("forward.min_obs is a whole number, 1 or more")
    k, d = spec.get("kill_z"), spec.get("drift_z")
    if not (isinstance(k, (int, float)) and isinstance(d, (int, float)) and k < d <= 0):
        out.append("forward.kill_z < drift_z <= 0")
    ref = spec.get("reference")
    if ref is not None and (not isinstance(ref, dict) or not {"file", "mean", "t", "n"} <= set(ref)):
        out.append("forward.reference needs file, mean, t and n, or is null (judged on paper alone)")
    if "per" in spec and not (isinstance(spec["per"], str) and spec["per"]):
        out.append("forward.per names a per-trade field")
    return out


def _at(doc: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        doc = doc[part]
    return doc


def reference(spec: dict, read) -> dict | None:
    """The backtest's mean and spread per observation, or None when the result cannot be read.
    `read(path)` returns the parsed result file (a `{home}/...` path) or None."""
    ref = spec["reference"]
    if ref is None:
        return None
    doc = read(ref["file"])
    if doc is None:
        return None
    try:
        mean, t, n = float(_at(doc, ref["mean"])), float(_at(doc, ref["t"])), int(_at(doc, ref["n"]))
    except (KeyError, TypeError, ValueError):
        return None
    if not t or n < 2:
        return None
    return {"mean": mean, "sd": abs(mean * math.sqrt(n) / t), "n": n}


def observations(trades: list[dict], spec: dict, arm: str | None = None) -> dict[str, float]:
    """One value per session for `arm` (default the spec's) from its closed trades since `since`."""
    arm = arm or spec["arm"]
    per = spec.get("per")
    by: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        v = t.get(spec["field"])
        if t.get("arm") != arm or v is None or (t.get("session") or "") < spec["since"]:
            continue
        if per:
            d = t.get(per)
            if not d:
                continue  # no divisor (a missing or zero capital): the trade cannot be measured
            v = float(v) / float(d)
        by[t["session"]].append(float(v))
    agg = statistics.fmean if spec["measure"] == "cycle_mean" else sum
    return {s: agg(vs) for s, vs in sorted(by.items())}


def judge(spec: dict, obs: dict[str, float], ref: dict | None, *, licence_ended: bool = False) -> dict:
    n = len(obs)
    out: dict[str, Any] = {"n": n, "min_obs": spec["min_obs"]}
    declared_alone = spec.get("reference", ...) is None
    if ref is None and not licence_ended and not declared_alone:
        return {**out, "state": COLLECTING, "why": "the backtest reference cannot be read here"}
    if n < spec["min_obs"]:
        return {**out, "state": COLLECTING}
    if ref is None:
        alone = paper_alone(spec, obs)
        if declared_alone:
            alone["basis"] = "paper alone: declared, no backtest reference in paper's units"
        return {**out, **alone}
    mean = statistics.fmean(obs.values())
    z = (mean - ref["mean"]) / (ref["sd"] / math.sqrt(n)) if ref["sd"] else 0.0
    if z <= spec["kill_z"]:
        state = KILL
    elif z <= spec["drift_z"] or mean <= 0:
        state = DRIFT
    else:
        state = CONFIRM
    return {**out, "state": state, "paper_mean": mean, "reference_mean": ref["mean"], "z": round(z, 2)}


def paper_alone(spec: dict, obs: dict[str, float]) -> dict:
    """Once the licence has ended and the backtest reference was purged with its data (#1091), the
    paper arm is judged on its own record: its mean in its own standard errors (t), `kill` at t at
    or under `kill_z`, `drift` at a mean at or under zero, `confirm` otherwise. Said so (`basis`)."""
    values = list(obs.values())
    mean = statistics.fmean(values)
    sd = statistics.stdev(values) if len(values) > 1 else 0.0
    t = mean / (sd / math.sqrt(len(values))) if sd else 0.0
    if t <= spec["kill_z"]:
        state = KILL
    elif mean <= 0:
        state = DRIFT
    else:
        state = CONFIRM
    return {
        "state": state,
        "paper_mean": mean,
        "t": round(t, 2),
        "basis": "paper alone: the reference was purged",
    }


def check(
    row: dict, trades: list[dict], read, *, licence_ended: bool = False, spec: dict | None = None
) -> dict:
    """One forward block's check (the row's only block by default): its state, and any arms
    reported beside it."""
    spec = spec if spec is not None else specs(row["forward"])[0]
    out = judge(spec, observations(trades, spec), reference(spec, read), licence_ended=licence_ended)
    beside = {a: len(observations(trades, spec, a)) for a in spec.get("beside", [])}
    return {"id": row["id"], "study": row["study"], "module": spec["module"], "arm": spec["arm"],
            **out, "beside_obs": beside}  # fmt: skip


def run(reg: dict, trades_for, read, *, licence_ended: bool = False) -> list[dict]:
    """Every row that declares a forward check. `trades_for(module, schema)` returns that module's
    closed paper trades (`core.ledgers`), or [] when it cannot be read. With `licence_ended`, a check
    whose reference is gone judges the paper arm alone (`paper_alone`)."""
    out = []
    for row in reg.get("hypotheses", []):
        if not row.get("forward") or problems(row["forward"]):
            continue
        for spec in specs(row["forward"]):
            trades = trades_for(spec["module"], spec["schema"])
            out.append(check(row, trades, read, licence_ended=licence_ended, spec=spec))
    return out


def read_result(home: Path):
    """A `read` for `run`: a `{home}/...` result file parsed, or None."""

    def read(p: str):
        try:
            return json.loads((home / p.removeprefix("{home}/")).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    return read
