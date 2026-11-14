"""The replay record: every decision tick, with the snapshot it was made from.

`paper.decide_all` runs on a snapshot built from the stream cache, and the cache keeps only the latest
quote, so a decision cannot be re-made from the cache afterwards. The loop therefore writes one line of
JSON per symbol decision tick to `~/.cherrypick/data/ensemble/replay/<session>.jsonl`: the compact snapshot
(`paper.compact`), the arms it was decided for (as they stood), and the decisions made. `replay_file`
runs the same snapshots through `paper.replay` and says where the decisions differ, which is how the paper
path is compared with the backtest's later.

Local data only (a market snapshot is quote data): the directory sits under the managed home and is not
part of the repository.
"""

from __future__ import annotations

import json
from pathlib import Path

from cherrypick.core import home as _home

from . import paper
from .model import Decision


def replay_dir() -> Path:
    return _home.data_dir("ensemble") / "replay"


def decision_summary(d: Decision) -> dict:
    out = {"arm": d.arm, "enter": d.enter, "reason": d.reason, "checks": [list(c) for c in d.checks]}
    if d.entry is not None:
        e = d.entry
        out["entry"] = {
            "expiry": e.expiry,
            "quantity": e.quantity,
            "legs": [[g.name, g.right, g.side, g.strike] for g in e.legs],
            "prices": e.prices,
            "slippage": e.slippage,
            "fees_open": e.fees_open,
        }
    return out


def line(snap, arms: dict[str, dict], decisions: list[Decision], ts: str) -> str:
    return json.dumps(
        {
            "ts": ts,
            "session": snap.session,
            "minute": snap.minute,
            "symbol": snap.symbol,
            "snapshot": paper.compact(snap),
            "arms": arms,
            "decisions": [decision_summary(d) for d in decisions],
        },
        separators=(",", ":"),
        default=str,
    )


def append(
    snap, arms: dict[str, dict], decisions: list[Decision], ts: str, *, directory: Path | None = None
) -> Path:
    directory = directory or replay_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{snap.session}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line(snap, arms, decisions, ts) + "\n")
    return path


def read(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def replay_file(
    path: Path, events=None, *, costs: dict | None = None, arms: dict | None = None
) -> list[dict]:
    """Run every recorded tick again. Each result: the tick, and the recorded and the replayed decisions as
    (arm, enter, reason) so a difference is one comparison. `arms` replaces the recorded definitions (to see
    what a changed arm would have done on the same market)."""
    out = []
    for rec in read(path):
        use = arms if arms is not None else rec["arms"]
        again = paper.replay(use, rec["snapshot"], events, costs=costs)
        out.append(
            {
                "session": rec["session"],
                "minute": rec["minute"],
                "symbol": rec["symbol"],
                "recorded": [(d["arm"], d["enter"], d["reason"]) for d in rec["decisions"]],
                "replayed": [(d.arm, d.enter, d.reason) for d in again],
            }
        )
    return out
