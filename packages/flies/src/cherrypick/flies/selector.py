"""The selector arm: on each tick, take the source arm whose trade has paid best in this regime.

Every other arm is its own portfolio testing one variable. The selector holds one portfolio and,
on each tick, asks each source arm what it would do against THAT portfolio -- the source's own
entry function and merged params, gated by the selector's own open positions, so cadence, the sign
rule and the duplicate check describe the book the selector actually holds. It then scores each
proposal from a model fitted on the source arms' own settled rows and frozen for the session, and
books at most one.

Pure throughout: nothing here touches the database or the clock. `book.process_selector` does the
writing, `cli selector-fit` writes the model, and `selector_replay` walks the same functions
forward over history.

**With no model, the selector is control.** It books the default source's plan whenever that
source proposes one and nothing else, so the arm starts as a byte-identical twin of control and
departs from it only on evidence -- the posture curve's `noflip` has against its control.

**Identical plans merge, never double-count.** `vol-floor` proposes control's own plan on every
tick its straddle floor clears, and nothing otherwise. The two proposals collapse into one
candidate carrying both labels, scored on the strictest proposer's evidence first (the declared
`sources` order runs least to most strict within one structure). Their rows are never pooled:
vol-floor's trades are a subset of control's tape, so pooling them counts one trade twice.

The procedure is declared and versioned (`PROCEDURE_VERSION`). Refitting it nightly moves its
parameters, not its meaning; changing it is a measurement break. Version 1 reads two features, the
volatility and trend-from-open buckets, because the regime cuts so far show one paired contrast in
46 under p 0.10 against about 4.6 by chance -- more features would be fitting noise.
"""

from __future__ import annotations

from cherrypick.core import regimecuts as _rc

from cherrypick.flies import engine

PROCEDURE_VERSION = 1
FEATURES = ("vol_bucket", "trend_bucket")
DEFAULT_SOURCE = "control"
DEFAULT_MIN_SESSIONS = 5
DEFAULT_MARGIN = 0.0

# The entry modes the selector can book, each through the engine function its own arm calls.
ENTRY_FUNCTIONS = {
    "legged": engine.evaluate_credit_spread_entry,
    "debit_first": engine.evaluate_debit_vertical_entry,
}


def settings(params: dict) -> dict:
    """The selector's own keys, read off its merged params with their declared defaults."""
    cfg = params.get("selector") or {}
    return {
        "sources": list(cfg.get("sources", [DEFAULT_SOURCE])),
        "default": cfg.get("default", DEFAULT_SOURCE),
        "min_sessions": int(cfg.get("min_sessions", DEFAULT_MIN_SESSIONS)),
        "margin": float(cfg.get("margin", DEFAULT_MARGIN)),
    }


def source_mode(params: dict) -> str | None:
    """The one entry mode a source arm trades, or None when it trades none the selector can book
    (or several, which would make "this source's plan" ambiguous)."""
    modes = [m for m in params.get("entry_modes", ["legged"]) if m in ENTRY_FUNCTIONS]
    return modes[0] if len(modes) == 1 else None


# --------------------------------------------------------------------------- candidates
def candidates(
    snapshot: dict, config: dict, sources: list, open_positions: list, day_positions: list
) -> list:
    """What each source would do on this snapshot against the SELECTOR's book. One entry per
    source, entered or refused; a refusal keeps its reason, so the choice record can say why a
    source offered nothing."""
    out = []
    for source in sources:
        params = engine.merged_params(config, source)
        mode = source_mode(params)
        if mode is None:
            out.append(
                {"source": source, "mode": None, "enter": False, "reason": "no_bookable_mode", "plan": None}
            )
            continue
        detail: dict = {}
        enter, reason, plan = ENTRY_FUNCTIONS[mode](snapshot, params, open_positions, day_positions, detail)
        out.append(
            {
                "source": source,
                "mode": mode,
                "enter": enter,
                "reason": reason,
                "plan": plan if enter else None,
            }
        )
    return out


def plan_key(mode: str, plan: dict) -> tuple:
    """Two proposals are one trade when mode, side and geometry all match. `engine.structure_key`
    alone is deliberately blind to kind and side (a legged and a debit-first entry at one centre
    converge on one fly), which is right for the duplicate rule and wrong here: those two are
    different trades at different prices."""
    return (mode, plan["side"], *engine.structure_key(plan))


def merge(cands: list) -> list:
    """Collapse identical entered proposals into one candidate carrying every source that offered
    it, in `sources` order. The plan kept is the first proposer's; identical keys mean identical
    geometry, and every source quotes the same snapshot."""
    merged: dict = {}
    for c in cands:
        if not c["enter"]:
            continue
        key = plan_key(c["mode"], c["plan"])
        if key not in merged:
            merged[key] = {"sources": [], "mode": c["mode"], "plan": c["plan"]}
        merged[key]["sources"].append(c["source"])
    return list(merged.values())


def label(candidate: dict) -> str:
    return "+".join(candidate["sources"])


def booking_source(selected_from: str | None) -> str | None:
    """The source whose params a booked position completes under: the first label of the merge,
    the least strict proposer, which for a merge is the plan's own owner."""
    if not selected_from:
        return None
    return selected_from.split("+")[0]


# --------------------------------------------------------------------------- scoring
def cell_key(regime: dict) -> str:
    return "|".join(str(regime.get(f)) for f in FEATURES)


def score(candidate: dict, regime: dict, model: dict | None) -> dict:
    """The candidate's evidence in this regime. Proposers are tried strictest first: the first with
    a cell of at least `min_sessions` sessions scores it; failing that, the first with that many
    sessions overall scores it, flagged `shrunk`; failing both it is ineligible, and an ineligible
    candidate is never preferred over the default."""
    out = {"label": label(candidate), "cell": cell_key(regime), "eligible": False}
    if not model:
        return out
    min_sessions = model.get("min_sessions", DEFAULT_MIN_SESSIONS)
    strictest_first = list(reversed(candidate["sources"]))
    for source in strictest_first:
        stats = (model.get("cells", {}).get(out["cell"]) or {}).get(source)
        if stats and stats["sessions"] >= min_sessions:
            return {**out, **stats, "evidence": source, "eligible": True, "shrunk": False}
    for source in strictest_first:
        stats = model.get("overall", {}).get(source)
        if stats and stats["sessions"] >= min_sessions:
            return {**out, **stats, "evidence": source, "eligible": True, "shrunk": True}
    return out


def choose(scored: list, model: dict | None, default: str = DEFAULT_SOURCE) -> tuple:
    """Pick one candidate or none. `scored` is [(candidate, score), ...]. Returns
    (candidate | None, reason).

    The default source's candidate is taken unless the model has a reason to depart from it: a
    different candidate with positive evidence that beats the default's by `margin`, or the
    default's own cell (not shrunk) saying negative, which skips. No model means no departure."""
    ctrl = next(((c, s) for c, s in scored if default in c["sources"]), None)
    if not model:
        return (ctrl[0], "no_model_default") if ctrl else (None, "default_not_offered")

    margin = model.get("margin", DEFAULT_MARGIN)
    eligible = [(c, s) for c, s in scored if s["eligible"]]
    best = max(eligible, key=lambda cs: cs[1]["net_per_entry"], default=None)
    ctrl_net = ctrl[1]["net_per_entry"] if ctrl and ctrl[1]["eligible"] else 0.0

    if best is not None and (ctrl is None or best[0] is not ctrl[0]):
        if best[1]["net_per_entry"] > 0 and best[1]["net_per_entry"] >= ctrl_net + margin:
            return best[0], "model_preferred"
    if ctrl is None:
        return None, "default_not_offered"
    s = ctrl[1]
    if s["eligible"] and not s["shrunk"] and s["net_per_entry"] < 0:
        return None, "default_cell_negative"
    if not s["eligible"]:
        return ctrl[0], "thin_default"
    return ctrl[0], "default"


# --------------------------------------------------------------------------- fitting
def _stats(rows: list) -> dict:
    by_session: dict = {}
    for r in rows:
        by_session[r["trade_date"]] = by_session.get(r["trade_date"], 0.0) + (r["pnl"] or 0.0)
    net = sum(by_session.values())
    return {
        "sessions": len(by_session),
        "entries": len(rows),
        "net_per_entry": round(net / len(rows), 2),
        "completion_rate": round(sum(1 for r in rows if r["kind"] in ("fly", "iron_fly")) / len(rows), 4),
        "worst_session": round(min(by_session.values()), 2),
    }


def fit(
    rows: list,
    *,
    through: str,
    sources: list,
    arm_starts: dict | None = None,
    min_sessions: int = DEFAULT_MIN_SESSIONS,
    margin: float = DEFAULT_MARGIN,
) -> dict:
    """The model for the session `through`, from settled rows strictly BEFORE it.

    `rows` are settled, unvoided positions: arm, trade_date, pnl, kind and the entry tags the
    features name (`entry_vol_bucket`, `entry_trend_bucket`). `arm_starts` scopes each arm to its
    era (from `core.regimecuts.era_bounds`); rows before an arm's start are dropped, so no break is
    ever pooled across. Anything on or after `through` is ignored, which is what makes the fit
    truncation-invariant: adding tomorrow's rows cannot change today's model."""
    arm_starts = arm_starts or {}
    kept = [
        r
        for r in rows
        if r["arm"] in sources
        and r["trade_date"] < through
        and r["trade_date"] >= (arm_starts.get(r["arm"]) or "")
        and r.get("pnl") is not None
    ]
    cells: dict = {}
    overall: dict = {}
    for source in sources:
        mine = [r for r in kept if r["arm"] == source]
        if not mine:
            continue
        overall[source] = _stats(mine)
        grouped: dict = {}
        for r in mine:
            key = cell_key({f: r.get(f"entry_{f}") for f in FEATURES})
            grouped.setdefault(key, []).append(r)
        for key, group in grouped.items():
            cells.setdefault(key, {})[source] = _stats(group)
    return {
        "module": "flies",
        "session": through,
        "fitted_through": max((r["trade_date"] for r in kept), default=None),
        "procedure_version": PROCEDURE_VERSION,
        "features": list(FEATURES),
        "sources": list(sources),
        "arm_starts": dict(arm_starts),
        "min_sessions": min_sessions,
        "margin": margin,
        "default": DEFAULT_SOURCE,
        "overall": overall,
        "cells": cells,
    }


def arm_starts(breaks: list, session: str, sources: list) -> dict:
    """Each source's era start as of `session`, from the ledger's own `measurement_breaks`
    (`core.regimecuts.era_bounds`): the latest book-wide break, or the arm's own later one. A break
    dated on the session itself starts a fresh era there, so that session's model learns from
    nothing before it -- which is the point."""
    era = _rc.era_bounds(breaks, session)
    return {s: _rc.arm_start(era, s)[0] for s in sources}


def model_id(model: dict) -> str:
    return f"selector-{model['session']}-v{model['procedure_version']}"


def validate_model(doc, session: str) -> tuple[bool, str]:
    """(ok, reason). Each way a model can be unusable has its own reason, because the choice record
    must say which one sent the selector back to the default."""
    if doc is None:
        return False, "model_absent"
    if (
        not isinstance(doc, dict)
        or not isinstance(doc.get("cells"), dict)
        or not isinstance(doc.get("overall"), dict)
    ):
        return False, "model_malformed"
    if doc.get("module") != "flies":
        return False, "model_malformed"
    if doc.get("procedure_version") != PROCEDURE_VERSION:
        return False, "model_wrong_procedure"
    if doc.get("session") != session:
        return False, "model_stale"
    return True, "ok"
