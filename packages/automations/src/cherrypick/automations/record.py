"""The replay record: every decision tick, with the snapshot it was made from.

`paper.decide_all` runs on a snapshot built from the stream cache, and the cache keeps only the latest
quote, so a decision cannot be re-made from the cache afterwards. The loop therefore writes one line of
JSON per symbol decision tick to `~/.cherrypick/data/automations/replay/<session>.jsonl`: the compact snapshot
(`paper.compact`), the arms it was decided for (as they stood), and the decisions made. `replay_file`
runs the same snapshots through `paper.replay` and says where the decisions differ, which is how the paper
path is compared with the backtest's later. `paper.replay` is `paper.decide_all`, the call the backtest
driver makes on each stored minute, so a replayed day is the backtest's decision on paper's own market.

`compare_day` is the nightly check (`cli replay-day`): every tick of a session again, and each place
the decision or the fill differs, one row each: whether it entered, the first failing check, and for an
entry its expiry, count, legs, prices, slippage and fees (`FILL_FIELDS`).

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
    return _home.data_dir("automations") / "replay"


def decision_summary(d: Decision) -> dict:
    out = {"arm": d.arm, "enter": d.enter, "reason": d.reason, "checks": [list(c) for c in d.checks]}
    if d.entry is not None:
        e = d.entry
        out["entry"] = {
            "expiry": e.expiry,
            "quantity": e.quantity,
            # A far leg carries its expiry fifth; a one-expiry record is as it always was.
            "legs": [[g.name, g.right, g.side, g.strike, *([g.expiry] if g.expiry else [])] for g in e.legs],
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


def as_they_ran(arms: dict[str, dict]) -> dict[str, dict]:
    """Recorded arms with what they ran under made explicit: a record from before `limits.per_day` was a
    stated field ran one entry a day (the engine refused a second), so that is filled in, never anything
    the record does not imply."""
    out = {}
    for name, arm in arms.items():
        lim = arm.get("limits") or {}
        if "per_day" not in lim:
            arm = {**arm, "limits": {"max_open": 1, **lim, "per_day": 1}}
        out[name] = arm
    return out


FILL_FIELDS = ("expiry", "quantity", "legs", "prices", "slippage", "fees_open")


def _differences(recorded: dict, replayed: dict) -> list[tuple[str, object, object]]:
    """(field, recorded, replayed) for each place two decision summaries differ."""
    out = []
    for k in ("enter", "reason"):
        if recorded.get(k) != replayed.get(k):
            out.append((k, recorded.get(k), replayed.get(k)))
    a, b = recorded.get("entry") or {}, replayed.get("entry") or {}
    if a or b:
        for k in FILL_FIELDS:
            if json.dumps(a.get(k), sort_keys=True) != json.dumps(b.get(k), sort_keys=True):
                out.append((f"entry.{k}", a.get(k), b.get(k)))
    return out


def compare_day(path: Path, events=None, *, costs: dict | None = None) -> dict:
    """A recorded session run again on its own snapshots: {"session", "ticks", "decisions", "differ":
    [{"minute", "symbol", "arm", "field", "recorded", "replayed"}]}. An arm the record decided and the
    replay did not (or the other way) is a difference too."""
    recs = read(path)
    differ: list[dict] = []
    n = 0
    for rec in recs:
        again = {
            d.arm: decision_summary(d)
            for d in paper.replay(as_they_ran(rec["arms"]), rec["snapshot"], events, costs=costs)
        }
        was = {d["arm"]: d for d in rec["decisions"]}
        for arm in sorted(set(was) | set(again)):
            n += 1
            where = {"minute": rec["minute"], "symbol": rec["symbol"], "arm": arm}
            if arm not in again or arm not in was:
                differ.append({**where, "field": "decided", "recorded": arm in was, "replayed": arm in again})
                continue
            for field, a, b in _differences(was[arm], again[arm]):
                differ.append({**where, "field": field, "recorded": a, "replayed": b})
    session = recs[0]["session"] if recs else path.stem
    return {"session": session, "ticks": len(recs), "decisions": n, "differ": differ}


def replay_file(
    path: Path, events=None, *, costs: dict | None = None, arms: dict | None = None
) -> list[dict]:
    """Run every recorded tick again. Each result: the tick, and the recorded and the replayed decisions as
    (arm, enter, reason) so a difference is one comparison. `arms` replaces the recorded definitions (to see
    what a changed arm would have done on the same market)."""
    out = []
    for rec in read(path):
        use = arms if arms is not None else as_they_ran(rec["arms"])
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
