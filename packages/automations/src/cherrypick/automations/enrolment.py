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

**Backtest suggestion.** An automation with a block not backtested gets `suggestion`: what its blocks
read (`needs`: the chain at its expiry class and the entry's cadence, the greeks when a leg is placed by
delta, the spot trail for an intraday trigger) and, from the source registry's inventory
(`core.sources.coverage`), whether each is covered over the `SUGGEST_DAYS` up to the last finished
session. Our own stores are asked first; a licensed (vendor) store only where they fall short and the
caller says the licence allows new work (`licensed_ok`, which `paper_loop.backtest_suggestions` reads
through `licence.allows_new_work`, the driver's own test). A need a licensed store covers is marked
`licensed`, and so is the suggestion, since its result will be. Where every need is covered the suite
says the backtest can run; it never runs one.

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

from cherrypick.core import calendar as cal

from .model import hhmm

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


SUGGEST_DAYS = 365


def _expiry_class(arm: dict) -> str:
    e = arm["expiry"]
    if e.get("kind") in ("dte", "market_days") and e.get("days") in (0, 1) and not e.get("tolerance"):
        return "0dte" if e["days"] == 0 else "1dte"
    return "all"  # a nearer or farther listed expiry: a weekly or a monthly, which only `all` holds both of


CLOSE_TICK = 15 * 60 + 59  # the end-of-day chains' one tick on a full session (`DailyChainHistory.tick`)


def _cadence(arm: dict) -> str:
    """1d when the end-of-day chains can price the entry: a daily trigger, or a time entry whose window
    holds the 15:59 tick by the engine's own test (`entry_due`); else 1m."""
    e = arm["entry"]
    daily = e.get("kind") == "trigger" and (e.get("trigger") or {}).get("daily")
    at_close = False
    if e.get("kind") == "time":
        at = hhmm(e["at"])
        at_close = at <= CLOSE_TICK < at + e.get("window", 5)
    return "1d" if daily or at_close else "1m"


def needs(arm: dict) -> list[dict]:
    """What a backtest of `arm` reads: [{"field", "expiry"?, "cadence"}]."""
    expiry, cadence = _expiry_class(arm), _cadence(arm)
    out = [{"field": "chain", "expiry": expiry, "cadence": cadence}]
    if any((leg.get("strike") or {}).get("rule") == "delta" for leg in arm["legs"]):
        out.append({"field": "greeks", "expiry": expiry, "cadence": cadence})
    e = arm["entry"]
    intraday_trigger = e.get("kind") == "orb" or (
        e.get("kind") == "trigger" and not (e.get("trigger") or {}).get("daily")
    )
    if intraday_trigger:
        out.append({"field": "spot", "cadence": "1m"})
    return out


def suggestion(
    arm: dict, inventory: dict, today: date, coverage, *, licensed_ok: bool = False
) -> dict | None:
    """None when every block is backtested; else {"blocks", "window", "needs": [{..., "covered",
    "source", "licensed"}], "possible", "licensed"}. `coverage` is `core.sources.coverage` (passed in,
    so this stays pure). Our own stores are asked first for each need; only when they fall short and
    `licensed_ok` (the licence allows new work) is a licensed store asked, and a need it covers is
    marked `licensed`. The suggestion is `licensed` when any need is, so its result will be too."""
    lab = labels(arm)
    open_blocks = [b for b, r in lab.items() if r["state"] != BACKTESTED]
    if not open_blocks:
        return None
    last = cal.previous_trading_day(today)  # the last finished session; today's is not in a store yet
    end = last.isoformat()
    start = date.fromordinal(last.toordinal() - SUGGEST_DAYS).isoformat()
    rows = []
    for need in needs(arm):

        def ask(licensed: bool, need=need) -> dict:
            return coverage(inventory, need["field"], arm["symbol"], start, end, cadence=need["cadence"],
                            expiry=need.get("expiry"), licensed=licensed)  # fmt: skip

        got, licensed = ask(False), False
        if not got.get("covered") and licensed_ok:
            vendor = ask(True)
            if vendor.get("covered"):
                got, licensed = vendor, True
        row = {**need, "covered": bool(got.get("covered")), "source": got.get("best"), "licensed": licensed}
        if not row["covered"]:
            # The nearest our stores come: the first source holding anything, with its span and why it
            # falls short (own recordings that began recently, a source without the expiry).
            near = next((s for s in got.get("sources") or [] if s.get("first")), None)
            if near is not None:
                row["nearest"] = {k: near.get(k) for k in ("source", "first", "last", "why")}
        rows.append(row)
    return {
        "blocks": open_blocks,
        "window": [start, end],
        "needs": rows,
        "possible": all(r["covered"] for r in rows),
        "licensed": any(r["licensed"] for r in rows),
    }


def builder_view(arm: dict, suggestion_row: dict | None) -> dict:
    """One automation as the Builder page shows it: its identity, each block with its keys and label,
    its start and forward rule, and the backtest suggestion when it has one."""
    lab = labels(arm)
    reg = arm.get("register") or {}
    return {
        "name": arm["name"],
        "group": arm.get("group"),
        "symbol": arm["symbol"],
        "enabled": bool(arm.get("enabled", True)),
        "note": arm.get("note"),
        "start": arm.get("start"),
        "register": {"item": reg["item"], "verdict": reg["verdict"]} if reg else None,
        "summary": summary(arm),
        "blocks": [
            {"block": b, "state": lab[b]["state"], "keys": {k: arm[k] for k in keys if k in arm}}
            for b, keys in BLOCKS.items()
        ],
        "forward": arm.get("forward"),
        "suggestion": suggestion_row,
    }


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
