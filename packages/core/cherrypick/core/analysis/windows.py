"""The window each arm's analysis is read over: its own era where the ledger can say, else the suite's.

docs/strategy-analysis-plan.md, decision of 2026-10-17. A figure means something only over trades
that were made under the same rules. The suite's evidence window (its `data_epoch`) is one bound;
an arm's own measurement breaks are a tighter one: an arm added, a gate changed, an arm retired.
The regime cuts already scope each arm this way, and the analysis now does too, through the same
code (`core.regimecuts.era_bounds` and `arm_start`), so the two can never disagree about where an
arm's era starts.

Two `measurement_breaks` schemas exist, and only one can say which arm a break belongs to:

- **scoped** (flies, MEIC: `break_date, scope, kind, reason`): a break names its arm or the whole
  book (`scope` empty or "*"). Each arm reads from its own latest break, else the latest book-wide
  one. Breaks dated after the latest session are declared but do not bind yet, and kinds that
  caveat rather than bound (`regimecuts.NON_BOUNDING_KINDS`: the 10-08 outage) never start an era.
- **keyed** (bwb, curve, pmcc, calendars, earnings, contango: `break_date, key, old_value,
  new_value, note`): module-wide config changes with no arm. Which key resets which arm is not
  something this module can know -- bwb's 10-15 `arms` entry retired two arms and changed nothing
  for control -- so these arms read the suite window, and their reading says why rather than
  guessing.

The suite window stays a floor either way: no arm's window opens before it.
"""

from __future__ import annotations

from collections.abc import Iterable

from cherrypick.core import regimecuts

SCOPE_ARM = "arm_era"
SCOPE_SUITE = "suite"


def read_breaks(conn) -> tuple[list[dict], str]:
    """The ledger's breaks and which schema they are in: "scoped", "keyed", or "none"."""
    has = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='measurement_breaks'"
    ).fetchone()
    if not has:
        return [], "none"
    cols = {r[1] for r in conn.execute("PRAGMA table_info(measurement_breaks)")}
    if {"break_date", "scope", "kind"} <= cols:
        rows = conn.execute("SELECT break_date, scope, kind, reason FROM measurement_breaks").fetchall()
        return [
            dict(zip(("break_date", "scope", "kind", "reason"), tuple(r), strict=True)) for r in rows
        ], "scoped"
    return [], "keyed"


_SUITE_REASONS = {
    "keyed": "this module journals module-wide changes with no arm named, so its arms read the suite window",
    "none": "this ledger journals no measurement breaks, so its arms read the suite window",
}


def arm_windows(
    breaks: list[dict],
    schema: str,
    arms: Iterable[str],
    as_of: str | None,
    floor: str | None,
) -> dict[str, dict]:
    """{arm: {"scope", "start", "break", "reason"}} -- where each arm's reading starts, and why.

    `as_of` is the latest session the reading holds (so a re-run of the same rows gives the same
    windows, not one that moves with the clock); `floor` is the suite window's start, or None."""
    out: dict[str, dict] = {}
    if schema != "scoped" or as_of is None:
        reason = _SUITE_REASONS.get(schema, "no session to bound an era by")
        for arm in arms:
            out[arm] = {"scope": SCOPE_SUITE, "start": floor, "break": None, "reason": reason}
        return out
    era = regimecuts.era_bounds(breaks, as_of)
    for arm in arms:
        start, brk = regimecuts.arm_start(era, arm)
        bounding = brk if brk is not None else era["bounding_break"]
        if start is None or (floor is not None and start <= floor):
            # No break of its own or the book's after the suite window opened: the suite bounds it.
            out[arm] = {
                "scope": SCOPE_SUITE,
                "start": floor,
                "break": None,
                "reason": "no measurement break since the suite window opened",
            }
            continue
        out[arm] = {
            "scope": SCOPE_ARM,
            "start": start,
            "break": {k: (bounding or {}).get(k) for k in ("break_date", "scope", "kind", "reason")},
            "reason": "its own break" if brk is not None else "the module's latest book-wide break",
        }
    return out
