"""Taking an automation to paper: its start date, what of it a backtest has judged, and the forward-check
rule its paper record is read by. Pure functions over an arm.

**Start.** `start` (ISO date) is the first session the paper loop may enter it; a backtest ignores it,
since a backtest is not a forward test. A member of a group whose `groups.<name>.start` is later joins
then (`config.apply_groups`): a group is judged from one boundary.

**Blocks and labels.** An automation is five blocks (`BLOCKS`): the structure (symbol, expiry, legs),
the entry (entry, weekdays, event skips), the gates, the exit, and the size (size and limits). A
register label may carry `blocks`, each block's hash (`block_hash`) as it was when the study ran. A
block is `backtested` while its hash still matches, `changed` once it does not (the verdict no longer
speaks for it), and `not_backtested` with no hash at all. So a paper result is labelled for what it is,
block by block: "entry backtested, exit not".

**Forward rule.** `forward` declares, at enrolment and before any paper is read, how the paper record
is judged, in the shape the orchestrator's `forward-checks` reads (`forwardcheck` there):
`measure`, `field`, `min_obs`, `kill_z`, `drift_z`, optionally `per`, `reference` (default null: paper
judged on its own) and `beside`. `forward_rows` fills in the module, the ledger schema, the arm and
`since` (the start), so the paper results become evidence without a separate declaration.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date

BLOCKS: dict[str, tuple[str, ...]] = {
    "structure": ("symbol", "expiry", "legs"),
    "entry": ("entry", "weekdays", "skip_events"),
    "gates": ("gates",),
    "exit": ("exit",),
    "size": ("size", "limits"),
}
BACKTESTED, CHANGED, NOT_BACKTESTED = "backtested", "changed", "not_backtested"

FORWARD_KEYS = frozenset({"measure", "field", "per", "min_obs", "kill_z", "drift_z", "reference", "beside"})
FORWARD_NEEDED = ("measure", "field", "min_obs", "kill_z", "drift_z")
MEASURES = ("cycle_mean", "session_sum")
MODULE, SCHEMA = "automations", "ensemble_trade"


def block_hash(arm: dict, block: str) -> str:
    """The first 12 hex digits of sha256 over the block's keys as canonical JSON (a key the arm leaves
    out is absent, not null)."""
    part = {k: arm[k] for k in BLOCKS[block] if k in arm}
    text = json.dumps(part, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def block_hashes(arm: dict) -> dict[str, str]:
    return {b: block_hash(arm, b) for b in BLOCKS}


def labels(arm: dict) -> dict[str, dict]:
    """{block: {"state": backtested|changed|not_backtested, "item", "verdict"}}."""
    reg = arm.get("register") or {}
    recorded = reg.get("blocks") or {}
    out = {}
    for b in BLOCKS:
        if b not in recorded:
            state = NOT_BACKTESTED
        else:
            state = BACKTESTED if recorded[b] == block_hash(arm, b) else CHANGED
        row = {"state": state}
        if state != NOT_BACKTESTED:
            row.update(item=reg.get("item"), verdict=reg.get("verdict"))
        out[b] = row
    return out


def summary(arm: dict) -> str:
    """One line: "structure, entry backtested (item 75, passed); exit changed; size not backtested"."""
    by: dict[str, list[str]] = {}
    for b, row in labels(arm).items():
        by.setdefault(row["state"], []).append(b)
    reg = arm.get("register") or {}
    parts = []
    if by.get(BACKTESTED):
        parts.append(f"{', '.join(by[BACKTESTED])} backtested (item {reg.get('item')}, {reg.get('verdict')})")
    if by.get(CHANGED):
        parts.append(f"{', '.join(by[CHANGED])} changed since its backtest")
    if by.get(NOT_BACKTESTED):
        parts.append(f"{', '.join(by[NOT_BACKTESTED])} not backtested")
    return "; ".join(parts)


def _iso(v) -> bool:
    if not isinstance(v, str):
        return False
    try:
        date.fromisoformat(v)
    except ValueError:
        return False
    return len(v) == 10


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def problems(arm: dict) -> list[str]:
    """The arm's `start`, `forward` and `register.blocks` problems, each with its path."""
    name = arm.get("name", "?")
    p = []
    if "start" in arm and not _iso(arm["start"]):
        p.append(f"{name}.start: an ISO date, YYYY-MM-DD")
    blocks = (arm.get("register") or {}).get("blocks")
    if blocks is not None:
        if not isinstance(blocks, dict) or set(blocks) - set(BLOCKS):
            p.append(f"{name}.register.blocks: {{block: hash}} for blocks {sorted(BLOCKS)}")
        elif not all(isinstance(v, str) and len(v) == 12 for v in blocks.values()):
            p.append(f"{name}.register.blocks: each a 12-digit hash (`enrolment.block_hash`)")
    fw = arm.get("forward")
    if fw is None:
        return p
    where = f"{name}.forward"
    if not isinstance(fw, dict):
        return p + [f"{where}: an object"]
    extra = set(fw) - FORWARD_KEYS
    if extra:
        return p + [f"{where}: unknown key {sorted(extra)[0]!r}"]
    p += [f"{where}: needs {k!r}" for k in FORWARD_NEEDED if k not in fw]
    if "start" not in arm:
        p.append(f"{where}: a forward rule reads from the start date, and there is none")
    if "measure" in fw and fw["measure"] not in MEASURES:
        p.append(f"{where}.measure: one of {list(MEASURES)}")
    if "min_obs" in fw and not (
        isinstance(fw["min_obs"], int) and not isinstance(fw["min_obs"], bool) and fw["min_obs"] >= 1
    ):
        p.append(f"{where}.min_obs: a whole number of at least 1")
    for k in ("kill_z", "drift_z"):
        if k in fw and not (_num(fw[k]) and fw[k] < 0):
            p.append(f"{where}.{k}: a negative number")
    if _num(fw.get("kill_z")) and _num(fw.get("drift_z")) and not fw["kill_z"] < fw["drift_z"]:
        p.append(f"{where}.kill_z: below drift_z")
    for k in ("field", "per"):
        if k in fw and not (isinstance(fw[k], str) and fw[k]):
            p.append(f"{where}.{k}: a ledger field name")
    if "beside" in fw and not (
        isinstance(fw["beside"], list) and all(isinstance(x, str) for x in fw["beside"])
    ):
        p.append(f"{where}.beside: a list of automation names")
    return p


def joins(arm: dict, today: date) -> bool:
    """Whether the paper loop may enter `arm` on `today`: on or after its start, when it has one."""
    return "start" not in arm or today.isoformat() >= arm["start"]


def forward_rows(arms: dict[str, dict]) -> list[dict]:
    """Each enrolled automation's forward check as a register-shaped row: {"id", "study", "forward"}.
    The orchestrator's `forward-checks` reads these beside the register's own."""
    out = []
    for name, arm in sorted(arms.items()):
        fw = arm.get("forward")
        if not fw or "start" not in arm:
            continue
        block = {
            "module": MODULE,
            "schema": SCHEMA,
            "arm": name,
            "since": arm["start"],
            "reference": None,
            **fw,
        }
        out.append({"id": f"automation:{name}", "study": f"{name}, forward test", "forward": block})
    return out
