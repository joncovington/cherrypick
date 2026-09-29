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

A session count is not enough on its own, and the 2026-09-28 read showed it: MEIC control's
`deep_positive` GEX cell lost $37.5k over eleven sessions -- not thin -- and half of all its movement
was one session, whose arrival had flipped the cell from +$29k to -$30.6k between two nightly
snapshots. So the writer also stamps, from per-session totals the module hands in:

  * `fragile` on every cell -- one session dominates it or dropping any one flips its sign -- with
    the numbers behind it and a session-level bootstrap interval under `robustness`;
  * `paired` on every dimension: buckets compared on the SAME days, per trade, with a sign test,
    which is what separates "that kind of day was good" from "entering in that regime was good";
  * `history` on every cell, from the dated snapshots already on disk, so a cell whose sign has
    changed recently says so;
  * `multiplicity` on the document: how many intervals and paired tests it carries, and how many
    would clear the bar by chance alone.

All of it is deterministic -- the bootstrap is seeded -- so a re-cut of a past session reproduces
the stamps exactly.
"""

from __future__ import annotations

import json
import math
import os
import random
import re
import statistics
from pathlib import Path
from typing import Any

CUT_VERSION = 2
THIN_BELOW_SESSIONS = 3
DEFAULT_CROSS_TABS: tuple[tuple[str, str], ...] = (("gex", "trend"),)
LATEST_NAME = "regime_cuts.json"
_DATED_RE = re.compile(r"^regime_cuts-(\d{4}-\d{2}-\d{2})\.json$")

# Robustness stamps (2026-09-28); see the module docstring.
CONCENTRATED_SHARE = 0.40  # one session this share of a cell's absolute flow makes the cell fragile
INTERVAL_LEVEL = 0.90
BOOTSTRAP_RESAMPLES = 1000
BOOTSTRAP_SEED = 20260928
PAIRED_ALPHA = 0.10
STABILITY_WINDOW = 10  # prior dated snapshots a cell's history is read over
_UNREAD_BUCKETS = ("untagged", "unknown")

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


def session_totals(pairs) -> dict[str, list]:
    """`{session: [trades, net]}` from `(session, net)` pairs -- the per-session input a writer
    attaches to a bucket or cross-tab cell as `session_nets`. Each module decides what net means
    (flies' `pnl`, MEIC's `pnl - fees`); this only pools it by day. Undated rows are dropped."""
    out: dict[str, list] = {}
    for session, net in pairs:
        if not session:
            continue
        slot = out.setdefault(str(session), [0, 0.0])
        slot[0] += 1
        slot[1] += float(net or 0.0)
    return {k: [v[0], round(v[1], 2)] for k, v in sorted(out.items())}


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def _bootstrap_interval(nets: list[float]) -> tuple[float, float]:
    """Percentile interval of the cell's TOTAL net, resampling sessions (never trades: same-day
    entries share a market). Seeded, so the same sessions always give the same interval."""
    rng = random.Random(BOOTSTRAP_SEED)
    k = len(nets)
    totals = sorted(sum(rng.choices(nets, k=k)) for _ in range(BOOTSTRAP_RESAMPLES))
    tail = (1 - INTERVAL_LEVEL) / 2
    lo = totals[int(tail * BOOTSTRAP_RESAMPLES)]
    hi = totals[math.ceil((1 - tail) * BOOTSTRAP_RESAMPLES) - 1]
    return round(lo, 2), round(hi, 2)


def robustness(session_nets: dict) -> dict | None:
    """How much a cell's net rests on any one session. None below THIN_BELOW_SESSIONS, where the
    cell is already stamped thin and carries nothing more to read."""
    nets = [float(v[1]) for _, v in sorted(session_nets.items())]
    if len(nets) < THIN_BELOW_SESSIONS:
        return None
    total = sum(nets)
    flow = sum(abs(x) for x in nets)
    largest = max(nets, key=abs)
    flips = sum(1 for x in nets if _sign(total - x) != _sign(total))
    lo, hi = _bootstrap_interval(nets)
    return {
        "positive_sessions": sum(1 for x in nets if x > 0),
        "largest_session_net": round(largest, 2),
        "largest_session_share": round(abs(largest) / flow, 4) if flow else None,
        "sign_flips_dropping_one": flips,
        "net_interval": [lo, hi],
        "interval_level": INTERVAL_LEVEL,
        "interval_excludes_zero": lo > 0 or hi < 0,
    }


def _is_fragile(rob: dict | None) -> bool | None:
    if rob is None:
        return None
    share = rob["largest_session_share"]
    return rob["sign_flips_dropping_one"] > 0 or (share is not None and share >= CONCENTRATED_SHARE)


def sign_test_p(wins: int, losses: int) -> float | None:
    """Two-sided exact sign test; ties are dropped before the call. None with nothing to test."""
    n = wins + losses
    if n == 0:
        return None
    tail = sum(math.comb(n, i) for i in range(min(wins, losses) + 1)) / 2**n
    return round(min(1.0, 2 * tail), 4)


def paired_contrasts(buckets: list[dict]) -> list[dict]:
    """Every pair of read buckets compared on the sessions both traded: per-trade net in `a` minus
    per-trade net in `b`, day by day. A bucket that wins on the pooled table but not here is a kind
    of DAY, not a kind of entry -- MEIC's down_from_open was positive on 9 of 9 days and beat flat
    entries on the same day only 4 of 8 times. `buckets` are in document order (largest first), so
    `a` is the larger bucket of each pair. Pairs sharing fewer than THIN_BELOW_SESSIONS sessions
    are left out rather than listed thin: there is nothing in them to read."""
    read = [b for b in buckets if b["bucket"] not in _UNREAD_BUCKETS and b.get("session_nets")]
    out = []
    for i, a in enumerate(read):
        for b in read[i + 1 :]:
            sa, sb = a["session_nets"], b["session_nets"]
            shared = sorted(set(sa) & set(sb))
            if len(shared) < THIN_BELOW_SESSIONS:
                continue
            diffs = [sa[d][1] / sa[d][0] - sb[d][1] / sb[d][0] for d in shared]
            a_better = sum(1 for x in diffs if x > 0)
            b_better = sum(1 for x in diffs if x < 0)
            out.append(
                {
                    "a": a["bucket"],
                    "b": b["bucket"],
                    "sessions": len(shared),
                    "a_better_sessions": a_better,
                    "b_better_sessions": b_better,
                    "mean_diff_per_trade": round(statistics.fmean(diffs), 2),
                    "median_diff_per_trade": round(statistics.median(diffs), 2),
                    "sign_test_p": sign_test_p(a_better, b_better),
                }
            )
    return out


def _cell(row: dict) -> dict:
    sessions = int(row.get("sessions") or 0)
    out: dict[str, Any] = {"sessions": sessions}
    for k in _CELL_KEYS:
        out[k] = row.get(k)
    out["completed"] = row.get("completed")
    out["completion_rate"] = row.get("completion_rate")
    out["thin"] = sessions < THIN_BELOW_SESSIONS
    # Only when the writer handed in per-session totals: no key rather than a null that would
    # read as "checked and found sound".
    if row.get("session_nets") is not None:
        rob = robustness(row["session_nets"])
        out["fragile"] = _is_fragile(rob)
        out["robustness"] = rob
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
            ordered = sorted(rows, key=_bucket_sort_key)
            buckets = [
                {
                    "bucket": r["bucket"],
                    "value_min": r.get("value_min"),
                    "value_max": r.get("value_max"),
                    **_cell(r),
                }
                for r in ordered
            ]
            dims[dim] = {**cov, "buckets": buckets}
            if any(r.get("session_nets") is not None for r in rows):
                dims[dim]["paired"] = paired_contrasts(ordered)
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
    doc = {
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
    if any("fragile" in c for c in _iter_cells(doc)):
        doc["multiplicity"] = multiplicity(doc)
    return doc


def _iter_cells(doc: dict):
    """Every single-dimension bucket and cross-tab cell in the document."""
    for arm in doc.get("arms") or []:
        for d in (arm.get("dimensions") or {}).values():
            yield from d.get("buckets") or []
    for tab in doc.get("cross_tabs") or []:
        for arm in tab.get("arms") or []:
            yield from arm.get("cells") or []


def multiplicity(doc: dict) -> dict:
    """How much of what clears the bar would clear it by chance. A document this size carries
    hundreds of intervals: at a 90% level one in ten cells with no edge at all still excludes zero,
    and one in ten paired contrasts with no difference at all still reads p < 0.10."""
    intervals = [c["robustness"] for c in _iter_cells(doc) if c.get("robustness")]
    paired = [
        p
        for arm in doc.get("arms") or []
        for d in (arm.get("dimensions") or {}).values()
        for p in d.get("paired") or []
        if p.get("sign_test_p") is not None
    ]
    return {
        "alpha": round(1 - INTERVAL_LEVEL, 4),
        "intervals": len(intervals),
        "intervals_excluding_zero": sum(1 for r in intervals if r["interval_excludes_zero"]),
        "intervals_expected_by_chance": round(len(intervals) * (1 - INTERVAL_LEVEL), 1),
        "paired_alpha": PAIRED_ALPHA,
        "paired_tests": len(paired),
        "paired_below_alpha": sum(1 for p in paired if p["sign_test_p"] < PAIRED_ALPHA),
        "paired_expected_by_chance": round(len(paired) * PAIRED_ALPHA, 1),
    }


# --------------------------------------------------------------------------- history
def _arm_rows(container: dict) -> list:
    return container.get("arms") or container.get("books") or []


def _arm_key(row: dict) -> Any:
    return row.get("arm") or row.get("book")


def _cell_nets(doc: dict) -> dict[tuple, float | None]:
    """`{cell key: net}` over a document of either spelling (cut_version 1 wrote `books`)."""
    out: dict[tuple, float | None] = {}
    for arm in _arm_rows(doc):
        for dim, d in (arm.get("dimensions") or {}).items():
            for c in d.get("buckets") or []:
                out[("dim", _arm_key(arm), dim, str(c.get("bucket")))] = c.get("net_pnl")
    for tab in doc.get("cross_tabs") or []:
        dims = tuple(tab.get("dims") or ())
        for arm in _arm_rows(tab):
            for c in arm.get("cells") or []:
                out[("cross", _arm_key(arm), dims, tuple(c.get("buckets") or ()))] = c.get("net_pnl")
    return out


def stamp_history(doc: dict, priors: list[dict]) -> dict:
    """Stamp every non-thin cell with how its net read in the prior snapshots (oldest first, at
    most STABILITY_WINDOW): `{snapshots, first_net, sign_changes}`, where a sign change is any
    zero-crossing along prior..current. The priors are what readers were shown on those nights,
    and a later read-side fix does not rewrite them -- which is the point: a cell that moved
    because its definition moved has also changed what it told people."""
    priors = priors[-STABILITY_WINDOW:]
    prior_nets = [_cell_nets(p) for p in priors]
    doc["history"] = {"window": STABILITY_WINDOW, "sessions": [p.get("session") for p in priors]}

    def stamp(cell: dict, key: tuple) -> None:
        current = cell.get("net_pnl")
        if cell.get("thin") or current is None:
            return
        seq = [n[key] for n in prior_nets if n.get(key) is not None]
        signs = [x for x in (_sign(v) for v in [*seq, current]) if x != 0]
        cell["history"] = {
            "snapshots": len(seq),
            "first_net": seq[0] if seq else None,
            "sign_changes": sum(1 for x, y in zip(signs, signs[1:], strict=False) if x != y),
        }

    for arm in doc.get("arms") or []:
        for dim, d in (arm.get("dimensions") or {}).items():
            for c in d.get("buckets") or []:
                stamp(c, ("dim", arm["arm"], dim, str(c.get("bucket"))))
    for tab in doc.get("cross_tabs") or []:
        dims = tuple(tab.get("dims") or ())
        for arm in tab.get("arms") or []:
            for c in arm.get("cells") or []:
                stamp(c, ("cross", arm["arm"], dims, tuple(c.get("buckets") or ())))
    return doc


def load_priors(directory: Path | str, session: str, window: int = STABILITY_WINDOW) -> list[dict]:
    """The dated snapshots strictly before `session`, oldest first, at most `window` of them.
    An unreadable file is skipped: a history with a gap is still a history."""
    out = []
    for s in [d for d in dated_sessions(directory) if d < session][-window:]:
        try:
            with open(Path(directory) / dated_name(s), encoding="utf-8") as handle:
                doc = json.load(handle)
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict):
            out.append(doc)
    return out


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
    Returns `{"dated": path, "latest": path | None}`.

    Stamps `history` first, from the dated snapshots already in `directory`: this is the one place
    both writers pass through that knows where those snapshots live. A `--backfill` runs oldest
    first, so each session is stamped against the ones it has just rewritten."""
    directory = Path(directory)
    session = str(doc["session"])
    stamp_history(doc, load_priors(directory, session))
    dated = write_json_atomic(directory / dated_name(session), doc)
    current = latest_session(directory)
    latest = None
    if current is None or session >= current:
        latest = write_json_atomic(directory / LATEST_NAME, doc)
    return {"dated": str(dated), "latest": str(latest) if latest else None}


__all__ = [
    "BOOTSTRAP_RESAMPLES",
    "BOOTSTRAP_SEED",
    "CONCENTRATED_SHARE",
    "CUT_VERSION",
    "INTERVAL_LEVEL",
    "PAIRED_ALPHA",
    "STABILITY_WINDOW",
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
    "load_priors",
    "multiplicity",
    "paired_contrasts",
    "robustness",
    "session_totals",
    "sign_test_p",
    "stamp_history",
    "write_artifact",
    "write_json_atomic",
]
