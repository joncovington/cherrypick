"""The paper loop's entry into the engine. It holds no loop and no I/O: `decide_all` is what the loop calls
on each tick with a snapshot built from the stream cache, and the decision input is recorded
(`compact`, one line of JSON) so a paper decision can be run again later on exactly what it saw
(`replay`) and compared with the backtest's path.
"""

from __future__ import annotations

import copy
from dataclasses import replace

from .engine import decide
from .model import Decision, OpenPosition, Snapshot
from .snapshot import gex_series


def decide_all(arms: dict[str, dict], snap: Snapshot, *, costs: dict | None = None) -> list[Decision]:
    """Every enabled arm on `snap` for its symbol, in arm-name order. An arm that enters is visible to the
    arms after it (`no_position_today` and the limits read what is held), exactly as the backtest's
    driver stacks the arms; the snapshot the caller passed is not changed."""
    work = replace(
        snap,
        entries=copy.deepcopy(snap.entries),
        open_positions=list(snap.open_positions),
    )
    out: list[Decision] = []
    for name, arm in sorted(arms.items()):
        if not arm.get("enabled", True) or arm["symbol"] != snap.symbol:
            continue
        d = decide(arm, work, costs=costs)
        out.append(d)
        if d.enter and d.entry is not None:
            work.entries.setdefault(name, []).append(work.session)
            work.open_positions.append(OpenPosition(name, work.session, d.entry.expiry))
    return out


def compact(snap: Snapshot) -> dict:
    """The recorded decision input: `Snapshot.to_dict` without the GEX series, which `replay` rebuilds
    from the quote rows' gamma and open interest (the rows are what the series is made from), and
    without the rows' empty fields."""
    d = snap.to_dict()
    d.pop("gex", None)
    d["gex_from_rows"] = True
    for c in d["chains"].values():
        c["rows"] = [{k: v for k, v in r.items() if v is not None} for r in c["rows"]]
    return d


def replay(
    arms: dict[str, dict], recorded: dict, events=None, *, costs: dict | None = None
) -> list[Decision]:
    """Decide again from a recorded snapshot dict (`Snapshot.to_dict` or `compact`)."""
    d = dict(recorded)
    if d.pop("gex_from_rows", False):
        d["gex"] = {e: gex_series(c["rows"], e, d["spot"]) for e, c in d["chains"].items()}
    return decide_all(arms, Snapshot.from_dict(d, events), costs=costs)
