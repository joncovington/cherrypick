"""The paper loop's entry point into the engine: scaffolding for the PR that wires the loop (orchestrator
job, stream requests, ledger, console page). It holds no loop and no I/O. `decide_all` is what the loop
calls on each tick with a snapshot built from the stream cache; a recorded decision input
(`Snapshot.to_dict`) replays through `replay`, so a paper decision can be re-run later on exactly what it saw.
"""

from __future__ import annotations

from .engine import decide
from .model import Decision, Snapshot


def decide_all(arms: dict[str, dict], snap: Snapshot, *, costs: dict | None = None) -> list[Decision]:
    """Every enabled arm on `snap` for its symbol, in arm-name order."""
    return [
        decide(arm, snap, costs=costs)
        for name, arm in sorted(arms.items())
        if arm.get("enabled", True) and arm["symbol"] == snap.symbol
    ]


def replay(
    arms: dict[str, dict], recorded: dict, events=None, *, costs: dict | None = None
) -> list[Decision]:
    """Decide again from a recorded snapshot dict."""
    return decide_all(arms, Snapshot.from_dict(recorded, events), costs=costs)
