"""The module's config: where it is read, the loop's defaults, the arms and the symbols they trade.

The file is `~/.cherrypick/config/automations.json` (`ensemble.json`, its old name, while only that
exists), copied from `config.example.json`, which is what a checkout without one reads. Three blocks
matter:

- `automations`: changes laid over the shipped automations (`arms.load_arms`):
  `{"iron-fly-1000": {"enabled": false}}` switches one off, a whole entry under a new name adds one. A
  misspelt key is refused with its path and the loop does not start, so an edit never runs as a different
  automation. The block's older spellings (`arms`, and the suite's `books` and `profiles`) still resolve,
  so none is withdrawn; `automations` is read first when a file holds two.
- `symbols`: per underlying, the OCC `root` a chain must carry (SPXW keeps the AM-settled SPX monthly
  out), and the `window_strikes` the stream request asks
  for on the entry minutes. A symbol the arms use and this block does not name falls back to
  `DEFAULT_ROOTS` and the symbol itself, so a new arm on a known index needs no code and no config here.
- `defaults`: the loop's knobs (`DEFAULTS`).

Resident loops read config at start (the orchestrator ticks `--once`, so each tick does).
"""

from __future__ import annotations

import os
import pathlib
from datetime import date, datetime
from zoneinfo import ZoneInfo

from cherrypick.core import config as _cfg
from cherrypick.core import home as _home
from cherrypick.core import signallib

from . import arms as arms_mod
from . import enrolment

_PKG_ROOT = str(pathlib.Path(__file__).resolve().parents[3])

#: The loop's knobs. Every one is overridable under `defaults`.
DEFAULTS = {
    #: A quote older than this is not priced against (the suite's usable-quote rule).
    "max_quote_age_seconds": 90,
    #: The spot print a decision or a trail sample is built on must be this fresh.
    "spot_max_age_seconds": 90,
    #: Open positions are marked this often, and on every tick of an exit check.
    "mark_interval_minutes": 15,
    #: The stream request widens for an arm this many minutes before its entry window opens.
    "stream_lead_minutes": 10,
    #: A trail that began later than this many minutes after the open cannot give the opening range or
    #: the five-minute bars: an arm that reads them is refused rather than fed a partial day.
    "trail_grace_minutes": 3,
    #: A close still unposted this many sessions on is logged for `--settle`.
    "settle_alert_sessions": 2,
    #: Daily bars to keep subscribed beyond what an arm's moving average needs.
    "history_margin_days": 15,
}

#: OCC roots by index symbol; any other symbol's root is the symbol itself.
DEFAULT_ROOTS = {"SPX": "SPXW", "NDX": "NDXP", "RUT": "RUTW", "XSP": "XSP"}
DEFAULT_WINDOW_STRIKES = 60


def _old_env_config() -> str | None:
    """`ENSEMBLE_CONFIG`, the override's old name, read only when `AUTOMATIONS_CONFIG` is unset."""
    return None if os.environ.get("AUTOMATIONS_CONFIG") else os.environ.get("ENSEMBLE_CONFIG")


def load_config(path: str | None = None) -> dict:
    return _home.load_module_config("automations", _PKG_ROOT, path or _old_env_config())


def defaults(config: dict) -> dict:
    return {**DEFAULTS, **_cfg.section(config, "defaults", label="automations")}


#: The automations block's spellings, the new one first.
REGISTRY_KEYS = ("automations", *_cfg.ARM_REGISTRY_KEYS)


def arm_overrides(config: dict) -> dict:
    """The `automations` block (or an older spelling: `arms`, `books`, `profiles`) as `load_arms` takes
    it."""
    return dict(_cfg.registry(config, *REGISTRY_KEYS, label="automations") or {})


def load_arms(config: dict, *, library: dict | None = None) -> dict[str, dict]:
    """The shipped arms with this machine's changes over them, each signal reference
    (`{"library": name, "version": hash}`) replaced by its spec from the signal library
    (`core.signallib`, read only when an arm holds one). Raises ValueError, naming every problem."""
    overrides = arm_overrides(config)
    if signallib.refs(overrides):
        overrides, _ = signallib.resolve(overrides, library if library is not None else signallib.load())
    return apply_groups(arms_mod.load_arms(overrides), config)


#: The keys a group may carry in the file. `budget_changes` is written by the Groups page's save alone.
GROUP_KEYS = {"enabled", "allocation", "max_risk", "shares", "start", "budget_changes"}
#: A group's budget: what `budget` hands `sizing`, and what a dated change replaces whole.
BUDGET_KEYS = ("allocation", "max_risk", "shares")


def _dollars(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0


def today_et() -> date:
    return datetime.now(ZoneInfo("America/New_York")).date()


def _budget(where: str, spec: dict) -> dict:
    """The budget keys of `spec` (a group, or one of its dated changes), checked."""
    row: dict = {}
    for k in ("allocation", "max_risk"):
        if k in spec:
            if not _dollars(spec[k]):
                raise ValueError(f"{where}.{k}: dollars, more than 0")
            row[k] = float(spec[k])
    if "shares" in spec:
        raw_shares = spec["shares"]
        shares = (
            {m: v for m, v in raw_shares.items() if not str(m).startswith("_")}
            if isinstance(raw_shares, dict)
            else {}
        )
        if not shares:
            raise ValueError(f"{where}.shares: an object of {{member: percent}}")
        for m, v in shares.items():
            if not (_dollars(v) and v <= 100):
                raise ValueError(f"{where}.shares.{m}: a percent, more than 0 and at most 100")
        total = sum(shares.values())
        if total > 100 + 1e-9:
            raise ValueError(f"{where}.shares: {total:g} percent in all, more than 100")
        if not ("allocation" in row or "max_risk" in row):
            raise ValueError(f"{where}.shares: a share of nothing (no allocation or max_risk)")
        row["shares"] = {m: float(v) for m, v in shares.items()}
    return row


def _changes(where: str, raw) -> list[dict]:
    """A group's `budget_changes`: [{"from": session, **budget}], each a whole budget, dates rising."""
    if not isinstance(raw, list):
        raise ValueError(f"{where}: a list of {{from, allocation?, max_risk?, shares?}}")
    out, last = [], ""
    for i, c in enumerate(raw):
        at = f"{where}[{i}]"
        if not isinstance(c, dict):
            raise ValueError(f"{at}: an object with from")
        extra = {k for k in c if not k.startswith("_")} - {"from", *BUDGET_KEYS}
        if extra:
            raise ValueError(f"{at}: unknown key {sorted(extra)[0]!r}")
        if not enrolment._iso(c.get("from")):
            raise ValueError(f"{at}.from: an ISO date, YYYY-MM-DD")
        if c["from"] <= last:
            raise ValueError(f"{at}.from: after the change before it")
        last = c["from"]
        out.append({"from": c["from"], **_budget(at, c)})
    return out


def history(row: dict) -> list[dict]:
    """A group's budgets in date order: [{"from": None, **first}, {"from": session, **change}, ...]. The
    first is the one the group has carried since it began; a group that never changed has that one alone."""
    return row.get("budget_history") or [{"from": None, **{k: row[k] for k in BUDGET_KEYS if k in row}}]


def budget_at(row: dict, day: str) -> dict:
    """The budget a group (a `groups` row) carries on session `day`: the latest change from on or before it,
    else the first."""
    out = {}
    for b in history(row):
        if b["from"] is None or b["from"] <= day:
            out = {k: v for k, v in b.items() if k != "from"}
    return out


def groups(config: dict, today: date | None = None) -> dict[str, dict]:
    """The `groups` block, `_`-prefixed notes dropped, each group with the budget it carries `today`
    (Eastern, unless given). A group not named is on, with no budget.

    {group: {"enabled": bool,
             "allocation": dollars,      the group's capital; an `allocation_pct` size reads its share
             "max_risk": dollars,        a cap on the group's open risk at once
             "shares": {member: pct},    each member's slice of both (at most 100 in all); a member
                                         not listed has no slice of its own, only the group's cap
             "budget_history": [...]}}   the budget from each date (`history`), when it has changed

    A budget changes on a date (`budget_changes`, each `{"from": session, **budget}`, the budget whole), so
    a change made while a group trades starts at a session boundary and the group's record can be read
    against the budget each session carried (`groupread`)."""
    day = (today or today_et()).isoformat()
    raw = config.get("groups") or {}
    if not isinstance(raw, dict):
        raise ValueError("groups: an object of {group: {enabled: true|false}}")
    out = {}
    for name, spec in raw.items():
        if name.startswith("_"):
            continue
        if not isinstance(spec, dict):
            raise ValueError(f"groups.{name}: an object with enabled")
        keys = {k for k in spec if not k.startswith("_")}
        if keys - GROUP_KEYS:
            raise ValueError(f"groups.{name}: unknown key {sorted(keys - GROUP_KEYS)[0]!r}")
        if not isinstance(spec.get("enabled"), bool):
            raise ValueError(f"groups.{name}.enabled: true or false")
        row = {"enabled": spec["enabled"]}
        if "start" in spec:
            if not enrolment._iso(spec["start"]):
                raise ValueError(f"groups.{name}.start: an ISO date, YYYY-MM-DD")
            row["start"] = spec["start"]
        first = _budget(f"groups.{name}", spec)
        changes = (
            _changes(f"groups.{name}.budget_changes", spec["budget_changes"])
            if "budget_changes" in spec
            else []
        )
        if changes:
            row["budget_history"] = [{"from": None, **first}, *changes]
            row.update(budget_at(row, day))
        else:
            row.update(first)
        out[name] = row
    return out


def budget(group: str, spec: dict, member: str, members: list[str]) -> dict | None:
    """What `sizing` reads of a member's group (`sizing.BUDGET_KEYS`), None when the group sets no
    allocation or cap."""
    if "allocation" not in spec and "max_risk" not in spec:
        return None
    share = spec.get("shares", {}).get(member)
    out = {"group": group, "members": sorted(members)}
    if "allocation" in spec:
        out["allocation"] = round(spec["allocation"] * (share / 100.0 if share is not None else 1.0), 2)
    if "max_risk" in spec:
        out["group_max_risk"] = spec["max_risk"]
        if share is not None:
            out["member_max_risk"] = round(spec["max_risk"] * share / 100.0, 2)
    return out


def apply_groups(arms: dict[str, dict], config: dict) -> dict[str, dict]:
    """Each arm whose group is switched off, switched off with it: no new entries, while what it holds
    is still managed and settled. A group no arm belongs to may be kept, switched off (one being set up),
    and is refused switched on; it is refused too while an arm names a group the block does not, the
    misspelling that would leave that arm trading: `"ensembel": off` beside arms in `ensemble`. So is a
    share for an arm outside the group. Each member
    of a group with an allocation or cap carries its `budget` (`budget`), which `sizing` reads; an
    `allocation_pct` size with neither its own allocation nor a group's is refused."""
    switches = groups(config)
    known = {a.get("group") for a in arms.values()}
    empty = sorted(set(switches) - known)
    for g in empty:
        if switches[g]["enabled"]:
            raise ValueError(f"groups.{g}: no automation belongs to this group, so it cannot be on")
    unnamed = sorted(g for g in known if g is not None and g not in switches)
    if empty and unnamed:
        raise ValueError(
            f"groups.{empty[0]}: no automation belongs to this group, and automations name {unnamed[0]!r},"
            " which the groups block does not: a misspelling?"
        )
    members: dict[str, list[str]] = {}
    for name, arm in arms.items():
        if arm.get("group") is not None:
            members.setdefault(arm["group"], []).append(name)
    for g, spec in switches.items():
        outside = sorted(set(spec.get("shares", {})) - set(members.get(g, [])))
        if outside:
            raise ValueError(f"groups.{g}.shares.{outside[0]}: not a member of this group")
    out = {}
    for name, arm in arms.items():
        g = arm.get("group")
        spec = switches.get(g, {"enabled": True}) if g is not None else {"enabled": True}
        if not spec["enabled"]:
            arm = {**arm, "enabled": False}
        if "start" in spec and spec["start"] > arm.get("start", ""):
            arm = {**arm, "start": spec["start"]}  # a member joins at its group's boundary
        b = budget(g, spec, name, members[g]) if g is not None else None
        if b is not None:
            arm = {**arm, "budget": b}
        size = arm["size"]
        if "allocation_pct" in size and "allocation" not in size and "allocation" not in (b or {}):
            raise ValueError(f"{name}.size.allocation_pct: no allocation, its own or its group's")
        out[name] = arm
    return out


def group_status(arms: dict[str, dict], config: dict) -> dict[str, dict]:
    """{group: {"enabled", "arms": [{"name", "enabled", "verdict"}]}} for the status line, with the
    group's `allocation`, `max_risk` and `shares` when it sets them."""
    switches = groups(config)
    out: dict[str, dict] = {}
    for name, arm in sorted(arms.items()):
        g = arm.get("group")
        if g is None:
            continue
        spec = switches.get(g, {"enabled": True})
        row = out.setdefault(g, {**spec, "arms": []})
        row["arms"].append(
            {
                "name": name,
                "enabled": bool(arm.get("enabled", True)),
                "verdict": (arm.get("register") or {}).get("verdict"),
            }
        )
    return out


def signal_versions(config: dict, names) -> dict[str, list[dict]]:
    """The library signals each named arm reads, {arm: [{"name", "version"}]}, for a run card; an arm
    with none is left out."""
    overrides = arm_overrides(config)
    out = {}
    for n in names:
        pairs = signallib.refs(overrides.get(n) or {})
        if pairs:
            out[n] = [{"name": a, "version": v} for a, v in pairs]
    return out


def enabled(arms: dict[str, dict]) -> dict[str, dict]:
    return {n: a for n, a in arms.items() if a.get("enabled", True)}


def volume_symbols(arms: dict[str, dict]) -> set[str]:
    """The symbols whose volume an enabled arm's signals read (`arms.volume_symbols`)."""
    return {s for a in enabled(arms).values() for s in arms_mod.volume_symbols(a)}


def symbols(config: dict, arms: dict[str, dict]) -> dict[str, dict]:
    """{symbol: {root, window_strikes}} for every symbol an enabled arm trades."""
    declared = {
        str(k).upper(): v for k, v in (config.get("symbols") or {}).items() if not str(k).startswith("_")
    }
    out = {}
    for sym in sorted({a["symbol"].upper() for a in enabled(arms).values()}):
        block = declared.get(sym) or {}
        out[sym] = {
            "root": str(block.get("root") or DEFAULT_ROOTS.get(sym, sym)).upper(),
            "window_strikes": int(block.get("window_strikes") or DEFAULT_WINDOW_STRIKES),
        }
    return out
