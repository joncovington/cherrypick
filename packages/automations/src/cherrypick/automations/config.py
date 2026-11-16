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

from cherrypick.core import config as _cfg
from cherrypick.core import home as _home
from cherrypick.core import signallib

from . import arms as arms_mod

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


GROUP_KEYS = {"enabled", "allocation", "max_risk", "shares"}


def _dollars(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0


def groups(config: dict) -> dict[str, dict]:
    """The `groups` block, `_`-prefixed notes dropped. A group not named is on, with no budget.

    {group: {"enabled": bool,
             "allocation": dollars,      the group's capital; an `allocation_pct` size reads its share
             "max_risk": dollars,        a cap on the group's open risk at once
             "shares": {member: pct}}}   each member's slice of both (at most 100 in all); a member
                                         not listed has no slice of its own, only the group's cap
    """
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
        for k in ("allocation", "max_risk"):
            if k in spec:
                if not _dollars(spec[k]):
                    raise ValueError(f"groups.{name}.{k}: dollars, more than 0")
                row[k] = float(spec[k])
        if "shares" in spec:
            raw_shares = spec["shares"]
            shares = (
                {m: v for m, v in raw_shares.items() if not str(m).startswith("_")}
                if isinstance(raw_shares, dict)
                else {}
            )
            if not shares:
                raise ValueError(f"groups.{name}.shares: an object of {{member: percent}}")
            for m, v in shares.items():
                if not (_dollars(v) and v <= 100):
                    raise ValueError(f"groups.{name}.shares.{m}: a percent, more than 0 and at most 100")
            total = sum(shares.values())
            if total > 100 + 1e-9:
                raise ValueError(f"groups.{name}.shares: {total:g} percent in all, more than 100")
            if not ("allocation" in row or "max_risk" in row):
                raise ValueError(f"groups.{name}.shares: a share of nothing (no allocation or max_risk)")
            row["shares"] = {m: float(v) for m, v in shares.items()}
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
    is still managed and settled. A group the config names but no arm belongs to is refused, so a
    misspelt group never leaves its arms trading; so is a share for an arm outside the group. Each member
    of a group with an allocation or cap carries its `budget` (`budget`), which `sizing` reads; an
    `allocation_pct` size with neither its own allocation nor a group's is refused."""
    switches = groups(config)
    known = {a.get("group") for a in arms.values()}
    unknown = sorted(set(switches) - known)
    if unknown:
        raise ValueError(f"groups.{unknown[0]}: no arm belongs to this group")
    members: dict[str, list[str]] = {}
    for name, arm in arms.items():
        if arm.get("group") is not None:
            members.setdefault(arm["group"], []).append(name)
    for g, spec in switches.items():
        outside = sorted(set(spec.get("shares", {})) - set(members[g]))
        if outside:
            raise ValueError(f"groups.{g}.shares.{outside[0]}: not a member of this group")
    out = {}
    for name, arm in arms.items():
        g = arm.get("group")
        spec = switches.get(g, {"enabled": True}) if g is not None else {"enabled": True}
        if not spec["enabled"]:
            arm = {**arm, "enabled": False}
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
