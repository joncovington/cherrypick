"""A grid study's settings side by side: one result per tested setting, and whether a setting sits on a
plateau or is a lone peak.

A grid study (items 23, 24, 43) runs the same rule at several values of one parameter. Its result
file already holds each arm's own figures; `block` lines them up by setting so the studies board can
draw the result against the parameter, and labels the live setting, and every passing setting, with
`core.robustness.neighbourhood` (B13). Two cases come first, because the core's two words would
misdescribe them: `not_positive` where the setting's own mean is zero or below, and
`neighbour_better` where a nearest neighbour's mean is higher (a slope, not a peak). The label is
description: it never re-chooses a setting, and a lone peak does not undo a pass.

A plateau says neighbouring settings earned alike, not that they earned on different days. Given
each arm's session nets, the block also reports every labelled setting's best sessions
(`core.robustness.best_sessions`) and how many of them each neighbour shares. `same_days` marks a
setting whose every neighbour shares most of its best sessions: settings that close together are
one bet on a few days, however flat the means look.

`block` is a pure function over a result document. `run_sessions` reads a run's session nets from
the results store for it. `annotate` adds the block to result files a study already wrote, so a
finished study gains it without a replay (and a holdout is never spent twice).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from cherrypick.core import robustness

from . import results

#: What every grid block measures: the arm's own mean net per session, the figure each verdict reads.
MEASURE = "mean net per session"
#: A setting whose own mean is zero or below: neither a plateau nor a peak worth the name.
NOT_POSITIVE = "not_positive"
#: A setting a nearest neighbour beats: on a slope, which the core would call a lone peak.
NEIGHBOUR_BETTER = "neighbour_better"


def _own(doc: dict, arm: str) -> dict | None:
    """An arm's own figures, from `arms` (tested) or `reference` (the base and reported arms). A
    reference entry is either the figures themselves or `{"own": ...}`."""
    for section in ("arms", "reference"):
        x = (doc.get(section) or {}).get(arm)
        if isinstance(x, dict):
            own = x.get("own", x)
            return own if isinstance(own, dict) else None
    return None


def block(
    doc: dict,
    parameter: str,
    settings: Mapping[str, float],
    declared: str,
    *,
    labels: Mapping[str, str] | None = None,
    sessions: Mapping[str, Mapping[str, float]] | None = None,
) -> dict:
    """The grid block for one result document. `settings` maps each arm in the grid to its value of
    `parameter`; `declared` is the arm holding the live setting. `labels` names a setting where the
    number reads badly (an entry slot's minutes as "12:30"). `sessions` maps each arm to its
    `{session: net}`; without it the block carries no `best_days`, as files written before it."""
    if declared not in settings:
        raise ValueError(f"the declared arm {declared!r} is not in the grid")
    rows = []
    for arm, setting in sorted(settings.items(), key=lambda kv: kv[1]):
        own = _own(doc, arm) or {}
        word = ((doc.get("arms") or {}).get(arm) or {}).get("verdict")
        rows.append({
            "arm": arm,
            "setting": setting,
            "label": (labels or {}).get(arm, f"{setting:g}"),
            "mean": own.get("mean_net"),
            "n": own.get("n"),
            "verdict": word,
            "declared": arm == declared,
        })  # fmt: skip
    means = {r["setting"]: r["mean"] for r in rows}

    def label(setting: float) -> str:
        # The core asks only whether each neighbour keeps half the mean, so a losing setting, or one a
        # neighbour beats, would read as a peak.
        nb = robustness.neighbourhood(means, setting)
        m = means[setting]
        if m is not None and m <= 0:
            return NOT_POSITIVE
        if m is not None and any(means[k] > m for k in nb.get("neighbours", [])):
            return NEIGHBOUR_BETTER
        return nb["verdict"]

    around = {declared: label(settings[declared])}
    for r in rows:
        if r["verdict"] == "pass":
            around[r["arm"]] = label(r["setting"])
    out = {
        "parameter": parameter,
        "measure": MEASURE,
        "declared": declared,
        "rows": rows,
        "neighbourhood": around,
    }
    if sessions is not None:
        out["best_days"] = _best_days(around, settings, means, sessions)
    return out


def _best_days(
    labelled: Iterable[str],
    settings: Mapping[str, float],
    means: Mapping[float, float | None],
    sessions: Mapping[str, Mapping[str, float]],
) -> dict:
    """Each labelled arm's best sessions, and how many of them each nearest neighbour also counts
    among its own best. An arm with too few sessions reads `insufficient` and shares nothing."""
    arm_at = {v: a for a, v in settings.items()}
    best = {a: robustness.best_sessions(sessions.get(a) or {}) for a in settings}
    out = {}
    for arm in labelled:
        own = best[arm]
        if own.get("insufficient"):
            out[arm] = own
            continue
        mine = set(own["sessions"])
        shared = {}
        for k in robustness.neighbourhood(means, settings[arm]).get("neighbours", []):
            theirs = best[arm_at[k]]
            if not theirs.get("insufficient"):
                shared[arm_at[k]] = len(mine & set(theirs["sessions"]))
        out[arm] = {
            **own,
            "shared_with_neighbours": shared,
            "same_days": bool(shared) and all(2 * n > own["k"] for n in shared.values()),
        }
    return out


def run_sessions(run_id: str | None) -> dict[str, dict[str, float]] | None:
    """`{arm: {session: net}}` for one run from the results store; None without a run id, so a
    document that names no run gets a block without `best_days`."""
    if not run_id:
        return None
    conn = results.connect()
    try:
        out: dict[str, dict[str, float]] = {}
        for arm, session, net in conn.execute(
            "SELECT arm, session, net FROM session_results WHERE run_id = ?", (run_id,)
        ):
            out.setdefault(arm, {})[session] = float(net or 0.0)
        return out
    finally:
        conn.close()


def annotate(files: Iterable[str | Path], build: Callable[[dict], dict]) -> list[str]:
    """Each result file rewritten with `grid` = `build(doc)`; every other key is kept as written."""
    done = []
    for f in files:
        p = Path(f)
        doc = json.loads(p.read_text(encoding="utf-8"))
        doc["grid"] = build(doc)
        p.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
        done.append(str(p))
    return done
