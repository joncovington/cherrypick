"""The regime-cuts artifact contract: per book x per regime dimension x bucket outcomes, era-scoped
by a module's `measurement_breaks` journal, written nightly by the module that owns the rows.

Pure functions over rows a module has already computed. No database, no clock, no filesystem
except the two writers at the bottom. flies and MEIC each keep their own `by_regime` (the query
layer is theirs, and their ledgers differ), and hand the results here so the document has ONE
shape and one set of rules whichever module wrote it. The console renders that document and the
advisor's deep pack reads it thinned; neither recomputes a cell.

Why this exists (2026-09-19): a by-hand cut of flies control's era rows made net-GEX sign look
predictive of completion until it was crossed with the trend bucket -- the whole effect sat in
one cell, positive gamma on up-from-open sessions, seven sessions wide. Two rules follow, and
both live here rather than in a renderer:

  * every cell carries `sessions`, and `thin` (fewer than THIN_BELOW_SESSIONS) is stamped by the
    WRITER, so no reader ever re-derives the threshold or reads a seven-session net as evidence;
  * nothing pools across a journaled measurement break: the era starts at the latest book-wide
    break on or before the session being cut, a book added later starts at its own `arm_added`
    break, and a break dated in the future (a gate's first binding session) is listed as declared
    but ignored until it passes.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

CUT_VERSION = 2
THIN_BELOW_SESSIONS = 3
DEFAULT_CROSS_TABS: tuple[tuple[str, str], ...] = (("gex", "trend"),)
LATEST_NAME = "regime_cuts.json"
_DATED_RE = re.compile(r"^regime_cuts-(\d{4}-\d{2}-\d{2})\.json$")

# Breaks of this kind journal one damaged session (a provider outage, a lost morning), not a change
# in what the numbers mean. They never bound an era; those inside it are listed as caveats.
NON_BOUNDING_KINDS = frozenset({"partial_session"})


def dated_name(session: str) -> str:
    return f"regime_cuts-{session}.json"


# --------------------------------------------------------------------------- era
def era_bounds(breaks: list[dict], session: str) -> dict:
    """Which break bounds the era as of `session`, and where each book's own window starts.

    `breaks` are `measurement_breaks` rows in the flies/MEIC schema (`break_date`, `scope`,
    `kind`, `reason`, `detail`). Only rows dated on or before `session` bind: a break journaled at
    its first BINDING session (the early-close and triple-witching gates, dated months ahead) is
    reported under `ignored_future` so the artifact says it is declared, and nothing more.

    Returns `{start, bounding_break, arm_starts: {book: (date, break)}, binding, ignored_future,
    caveats}`. `start` is None when no book-wide break has passed -- the cut then runs from the
    first row, and the document says so by carrying that None.
    """
    binding: list[dict] = []
    future: list[dict] = []
    for b in breaks:
        row = {k: b.get(k) for k in ("break_date", "scope", "kind", "reason")}
        row["scope"] = row["scope"] or "*"
        (binding if str(row["break_date"]) <= session else future).append(row)
    binding.sort(key=lambda r: (str(r["break_date"]), r["scope"], str(r["kind"])))
    future.sort(key=lambda r: (str(r["break_date"]), r["scope"], str(r["kind"])))

    book_wide = [r for r in binding if r["scope"] == "*" and r["kind"] not in NON_BOUNDING_KINDS]
    bounding = book_wide[-1] if book_wide else None
    start = str(bounding["break_date"]) if bounding else None

    arm_starts: dict[str, tuple[str, dict]] = {}
    for r in binding:
        if r["scope"] == "*" or r["kind"] in NON_BOUNDING_KINDS:
            continue
        date = str(r["break_date"])
        if start is not None and date < start:
            continue  # superseded by the book-wide boundary
        current = arm_starts.get(r["scope"])
        if current is None or date >= current[0]:
            arm_starts[r["scope"]] = (date, r)

    caveats = [
        r
        for r in binding
        if r["kind"] in NON_BOUNDING_KINDS and (start is None or str(r["break_date"]) >= start)
    ]
    for r in binding:
        r["binding"] = r is bounding or (
            r["scope"] != "*" and arm_starts.get(r["scope"], (None, None))[1] is r
        )
    return {
        "start": start,
        "bounding_break": bounding,
        "arm_starts": arm_starts,
        "binding": binding,
        "ignored_future": [{k: r[k] for k in ("break_date", "scope", "kind")} for r in future],
        "caveats": [{k: r[k] for k in ("break_date", "kind", "reason")} for r in caveats],
    }


def arm_start(era: dict, arm: str) -> tuple[str | None, dict | None]:
    """`(era_start, era_break)` for one arm: its own later break if it has one, else the era's."""
    own = era["arm_starts"].get(arm)
    if own is not None:
        return own[0], own[1]
    return era["start"], None


# --------------------------------------------------------------------------- assembly
_CELL_KEYS = (
    "trades",
    "gross_pnl",
    "fees",
    "net_pnl",
    "wins",
    "losses",
    "win_rate",
    "avg_pnl",
    "avg_win",
    "avg_loss",
    "fee_drag_pct",
    "profit_factor",
)


_OUTCOME_KEYS = ("completion_latency_min", "miss_gap")


def _cell(row: dict) -> dict:
    sessions = int(row.get("sessions") or 0)
    out: dict[str, Any] = {"sessions": sessions}
    for k in _CELL_KEYS:
        out[k] = row.get(k)
    out["completed"] = row.get("completed")
    out["completion_rate"] = row.get("completion_rate")
    out["thin"] = sessions < THIN_BELOW_SESSIONS
    return out


def _bucket_sort_key(b: dict) -> tuple:
    last = b["bucket"] in ("untagged", "unknown")
    return (last, -int(b.get("trades") or 0), str(b["bucket"]))


def _arm_sort_key(name: str) -> tuple:
    return (name != "control", name.startswith("advised:"), name)


def assemble(
    *,
    module: str,
    session: str,
    symbol: str | None,
    arm_column: str,
    entry_modes: list[str] | tuple[str, ...] | None,
    phase: str,
    era: dict,
    arms: list[dict],
    cross_tabs: list[dict],
    generated_at: str,
    min_effective_n: int,
) -> dict:
    """The document. `arms` are `{arm, era_start, era_break, summary, coverage, regimes}` where
    `summary` is the arm's own `_summarize`-shaped dict plus `sessions` (and `completed` /
    `completion_rate` when the module has the concept), `coverage` is the module's
    `regime_coverage(...)["dimensions"]`, and `regimes` is `{dimension: [by_regime rows]}`.
    `cross_tabs` are `{dims, arms: [{arm, cells: [{buckets, ...summary, sessions}]}]}`.

    `arm_column` names the SQL column the module's own rows use -- `arm` in every ledger since the
    2026-09-24 rename; artifacts written before it may carry `risk_profile` or `book` here.
    Everything is re-ordered and `thin`-stamped here so two writers cannot disagree.

    A summary may also carry the outcome distributions in `_OUTCOME_KEYS`; they are copied only
    when the writer set them, so a module without the concept emits no key rather than a null
    that reads as "measured and found nothing". Additive keys like these do not bump
    `CUT_VERSION`: every reader takes them with `.get`, and a bump would have the advisor refuse
    the other writer's artifact until its next nightly run."""
    out_arms = []
    for b in sorted(arms, key=lambda x: _arm_sort_key(x["arm"])):
        summary = b["summary"]
        dims: dict[str, dict] = {}
        for dim, rows in b["regimes"].items():
            cov = dict(b["coverage"].get(dim) or {})
            cov.pop("buckets", None)  # the per-bucket rows below carry the counts
            buckets = [
                {
                    "bucket": r["bucket"],
                    "value_min": r.get("value_min"),
                    "value_max": r.get("value_max"),
                    **_cell(r),
                }
                for r in rows
            ]
            buckets.sort(key=_bucket_sort_key)
            dims[dim] = {**cov, "buckets": buckets}
        arm = {
            "arm": b["arm"],
            "era_start": b.get("era_start"),
            "era_break": b.get("era_break"),
            "sessions": int(summary.get("sessions") or 0),
            # Stamped here like every cell's, so the console's era table stops comparing an arm's
            # sessions against the threshold itself (2026-09-24). Additive: no CUT_VERSION bump.
            "thin": int(summary.get("sessions") or 0) < THIN_BELOW_SESSIONS,
            "trades": summary.get("trades"),
            "net_pnl": summary.get("net_pnl"),
            "win_rate": summary.get("win_rate"),
            "completed": summary.get("completed"),
            "completion_rate": summary.get("completion_rate"),
        }
        for k in _OUTCOME_KEYS:
            if k in summary:
                arm[k] = summary[k]
        arm["dimensions"] = dims
        out_arms.append(arm)
    out_cross = []
    for ct in cross_tabs:
        entries = []
        for b in sorted(ct["arms"], key=lambda x: _arm_sort_key(x["arm"])):
            cells = [{"buckets": list(c["buckets"]), **_cell(c)} for c in b["cells"]]
            cells.sort(
                key=lambda c: (
                    any(x in ("untagged", "unknown") for x in c["buckets"]),
                    -int(c["trades"] or 0),
                    c["buckets"],
                )
            )
            entries.append({"arm": b["arm"], "cells": cells})
        out_cross.append({"dims": list(ct["dims"]), "arms": entries})
    return {
        "cut_version": CUT_VERSION,
        "module": module,
        "generated_at": generated_at,
        "session": session,
        "symbol": symbol,
        "arm_column": arm_column,
        "entry_modes": list(entry_modes) if entry_modes else None,
        "phase": phase,
        "thin_below_sessions": THIN_BELOW_SESSIONS,
        "min_effective_n": min_effective_n,
        "era": {
            "start": era["start"],
            "bounding_break": era["bounding_break"],
            "breaks": era["binding"],
            "ignored_future": era["ignored_future"],
            "caveats": era["caveats"],
        },
        "arms": out_arms,
        "cross_tabs": out_cross,
    }


# --------------------------------------------------------------------------- files
def write_json_atomic(path: Path | str, payload: Any, *, indent: int = 2) -> Path:
    """Write `payload` as JSON via a temp file beside the target and `os.replace`, so a reader never
    sees a half-written document. The fifth copy of this three-line pattern in the suite would
    have been in a module; this is the first shared one (the other four are noted in flies'
    docs/backlog.md as a measured, not yet folded, duplication)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=indent, default=str)
    os.replace(tmp, target)
    return target


def latest_session(directory: Path | str) -> str | None:
    """The `session` recorded in the directory's `regime_cuts.json`, or None."""
    path = Path(directory) / LATEST_NAME
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle).get("session")
    except (OSError, ValueError, AttributeError):
        return None


def dated_sessions(directory: Path | str) -> list[str]:
    """Every session with a dated artifact in `directory`, oldest first."""
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    return sorted(m.group(1) for n in names if (m := _DATED_RE.match(n)))


def write_artifact(directory: Path | str, doc: dict) -> dict:
    """Write the dated file always, and the latest copy only when `doc["session"]` is at or after
    the session already there -- a retroactive run lands beside a newer cut, never over it.
    Returns `{"dated": path, "latest": path | None}`."""
    directory = Path(directory)
    session = str(doc["session"])
    dated = write_json_atomic(directory / dated_name(session), doc)
    current = latest_session(directory)
    latest = None
    if current is None or session >= current:
        latest = write_json_atomic(directory / LATEST_NAME, doc)
    return {"dated": str(dated), "latest": str(latest) if latest else None}


__all__ = [
    "CUT_VERSION",
    "DEFAULT_CROSS_TABS",
    "LATEST_NAME",
    "NON_BOUNDING_KINDS",
    "THIN_BELOW_SESSIONS",
    "arm_start",
    "assemble",
    "dated_name",
    "dated_sessions",
    "era_bounds",
    "latest_session",
    "write_artifact",
    "write_json_atomic",
]
