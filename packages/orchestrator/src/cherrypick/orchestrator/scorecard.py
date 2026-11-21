"""The scorecard: one row per arm and automation in the suite, and the reason it needs a look.

Pure: it joins what the suite already decided, and decides nothing new.

- **The paper record**, its net, drawdown, sessions and trades, comes from `core.compare` over each
  module's current measurement era.
- **The vs-control reading** is `core.changes.reading` of the same comparison.
- **The vs-backtest reading** is the forward check (`forwardcheck.run`) where the register declares
  one.
- **The research verdict** comes from two places:
  - for a module arm, the status of the register row whose forward block names it;
  - for an automation, the module's own register label (`cli groups-view`).

`look` is the first of these that holds, worst first (`LOOKS`); none means nothing calls for a look:

1. its forward check reads kill;
2. its forward check reads drift;
3. a vs-control reading is ready (a decision is due);
4. its session net's interval sits wholly below zero over at least `core.changes.MIN_SESSIONS`
   sessions (losing beyond chance);
5. its research verdict failed while it still runs.

A forward state set against a licensed backtest reference is withheld while licensed results are
hidden (`is_licensed` names a reference file that is), and it then calls for no look. The state
`collecting` is never withheld, since it is the paper count alone.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from cherrypick.core import changes as core_changes

#: Each look in order of severity, with its words.
LOOKS = (
    ("forward_kill", "its forward check reads kill"),
    ("forward_drift", "its forward check reads drift: below its backtest"),
    ("decision_due", "a decision against its control is due"),
    ("losing", "its session net is below zero beyond chance"),
    ("failed_running", "its study failed and it still runs"),
)
_RANK = {k: i for i, (k, _) in enumerate(LOOKS)}
_WORDS = dict(LOOKS)

#: The automations module's states for an automation that trades now or will from a set session.
RUNNING_STATES = ("on", "waiting")


def _forward_specs(reg: Mapping[str, Any]) -> list[tuple[dict, dict]]:
    """(register row, forward block) for every block the register declares."""
    out = []
    for row in reg.get("hypotheses") or []:
        fwd = row.get("forward")
        if not fwd:
            continue
        for spec in fwd if isinstance(fwd, list) else [fwd]:
            if isinstance(spec, dict) and spec.get("module") and spec.get("arm"):
                out.append((row, spec))
    return out


def _reference_file(spec: Mapping[str, Any]) -> str | None:
    ref = spec.get("reference")
    return ref.get("file") if isinstance(ref, dict) else None


def _vs_backtest(check: Mapping[str, Any], spec: Mapping[str, Any] | None, is_licensed) -> dict:
    state = check.get("state")
    ref = _reference_file(spec or {})
    withheld = bool(
        is_licensed is not None and state != "collecting" and ref is not None and is_licensed(ref)
    )
    return {
        "id": check.get("id"),
        "study": check.get("study"),
        "state": None if withheld else state,
        "withheld": withheld,
        "n": check.get("n"),
        "min_obs": check.get("min_obs"),
    }


def _vs_control(arm: Mapping[str, Any]) -> dict | None:
    if not arm.get("control"):
        return None
    r = core_changes.reading(arm.get("vs_control"))
    return {"against": arm["control"], **r}


def _sample(vb: dict | None, vc: dict | None, sessions: int) -> dict:
    """How far the sample has come: the forward check's count against its declared minimum, else the
    vs-control reading's sessions against those it still needs, else the sessions alone."""
    if vb is not None and vb.get("min_obs"):
        return {"basis": "forward", "have": vb.get("n") or 0, "need": vb["min_obs"]}
    if vc is not None and vc.get("sessions_to_decision") is not None:
        have = vc.get("sessions") or 0
        return {"basis": "vs_control", "have": have, "need": have + vc["sessions_to_decision"]}
    return {"basis": "sessions", "have": sessions, "need": None}


def _look(row: dict) -> str | None:
    found = []
    vb, vc = row["vs_backtest"], row["vs_control"]
    if vb is not None and vb["state"] == "kill":
        found.append("forward_kill")
    if vb is not None and vb["state"] == "drift":
        found.append("forward_drift")
    if vc is not None and vc["reading"] == "ready":
        found.append("decision_due")
    if row["losing_beyond_chance"]:
        found.append("losing")
    if row["research"] is not None and row["research"].get("verdict") == "failed" and row["running"]:
        found.append("failed_running")
    return min(found, key=_RANK.__getitem__) if found else None


def build(
    comparisons: Iterable[Mapping[str, Any]],
    checks: Iterable[Mapping[str, Any]],
    register: Mapping[str, Any],
    automations: Iterable[Mapping[str, Any]] = (),
    *,
    is_licensed: Callable[[str], bool] | None = None,
) -> dict:
    """The scorecard from each module's `core.compare.module_comparison`, the forward checks, the
    register and the automations module's members (`groups-view`'s, each `{name, state, verdict}`)."""
    specs = {(s["module"], s["arm"]): (row, s) for row, s in _forward_specs(register)}
    checks_by = {(c.get("module"), c.get("arm")): c for c in checks}
    autos = {a["name"]: a for a in automations if a.get("name")}
    rows: dict[tuple[str, str], dict] = {}
    eras = {}

    def base(module: str, arm: str, kind: str) -> dict:
        return {
            "module": module,
            "arm": arm,
            "kind": kind,
            "research": None,
            "vs_backtest": None,
            "vs_control": None,
            "sessions": 0,
            "trades": 0,
            "net": None,
            "max_drawdown": None,
            "losing_beyond_chance": False,
            "running": True,
        }

    for comp in comparisons:
        module = comp.get("module")
        if not module:
            continue
        eras[module] = {"status": comp.get("status"), "start": (comp.get("era") or {}).get("start")}
        for arm in comp.get("arms") or []:
            name = arm.get("arm")
            if not name:
                continue
            row = base(module, name, "automation" if module == "automations" else "arm")
            mean = arm.get("mean_session_net")
            row.update(
                sessions=int(arm.get("sessions") or 0),
                trades=int(arm.get("trades") or 0),
                net=arm.get("net"),
                max_drawdown=arm.get("max_drawdown"),
                # An interval over a handful of sessions is not read (core.changes' floor), clear of
                # zero or not.
                losing_beyond_chance=int(arm.get("sessions") or 0) >= core_changes.MIN_SESSIONS
                and bool(arm.get("session_net_ci_excludes_zero"))
                and (mean or 0) < 0,
                vs_control=_vs_control(arm),
            )
            rows[(module, name)] = row

    for name, a in autos.items():
        row = rows.setdefault(("automations", name), base("automations", name, "automation"))
        row["running"] = a.get("state") in RUNNING_STATES
        row["state"] = a.get("state")
        verdict = a.get("verdict")
        row["research"] = {"id": None, "verdict": verdict} if verdict else None

    for key, check in checks_by.items():
        module, arm = key
        if not module or not arm:
            continue
        row = rows.setdefault(key, base(module, arm, "automation" if module == "automations" else "arm"))
        reg_row, spec = specs.get(key, (None, None))
        row["vs_backtest"] = _vs_backtest(check, spec, is_licensed)
        if row["research"] is None and reg_row is not None:
            row["research"] = {"id": reg_row.get("id"), "verdict": reg_row.get("status")}

    out = []
    for row in rows.values():
        row["sample"] = _sample(row["vs_backtest"], row["vs_control"], row["sessions"])
        look = _look(row)
        row["look"] = look
        row["look_words"] = _WORDS.get(look) if look else None
        out.append(row)
    # Worst look first; then rows with something to read (a record, a verdict, a forward check) before
    # those with none yet; then each module in the order its comparison came, and the arm.
    order = {m: i for i, m in enumerate(eras)}
    order.setdefault("automations", len(order))

    def blank(r: dict) -> bool:
        return r["sessions"] == 0 and r["research"] is None and r["vs_backtest"] is None

    out.sort(
        key=lambda r: (
            r["look"] is None,
            _RANK.get(r["look"] or "", 99),
            blank(r),
            order.get(r["module"], len(order) + 1),
            r["arm"],
        )
    )
    counts: dict[str, int] = {}
    for r in out:
        if r["look"]:
            counts[r["look"]] = counts.get(r["look"], 0) + 1
    return {
        "rows": out,
        "eras": eras,
        "looks": [{"key": k, "words": w} for k, w in LOOKS],
        "counts": counts,
        "min_sessions": core_changes.MIN_SESSIONS,
    }
