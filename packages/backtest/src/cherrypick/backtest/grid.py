"""A grid study's settings side by side: one result per tested setting, and whether a setting sits on a
plateau or is a lone peak.

A grid study (items 23, 24, 43) runs the same rule at several values of one parameter. Its result
file already holds each arm's own figures; `block` lines them up by setting so the studies board can
draw the result against the parameter, and labels the live setting, and every passing setting, with
`core.robustness.neighbourhood` (B13). Two cases come first, because the core's two words would
misdescribe them: `not_positive` where the setting's own mean is zero or below, and
`neighbour_better` where a nearest neighbour's mean is higher (a slope, not a peak). The label is
description: it never re-chooses a setting, and a lone peak does not undo a pass.

`block` is a pure function over a result document. `annotate` adds the block to result files a study
already wrote, so a finished study gains it without a replay (and a holdout is never spent twice).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from cherrypick.core import robustness

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
) -> dict:
    """The grid block for one result document. `settings` maps each arm in the grid to its value of
    `parameter`; `declared` is the arm holding the live setting. `labels` names a setting where the
    number reads badly (an entry slot's minutes as "12:30")."""
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
    return {
        "parameter": parameter,
        "measure": MEASURE,
        "declared": declared,
        "rows": rows,
        "neighbourhood": around,
    }


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
