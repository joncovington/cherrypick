"""Move live-only settings from meic.json's top level into its `live` block (2026-10-14).

Before the `live` block could carry its own overrides (`paper.LIVE_OVERRIDE_KEYS`), the only way to
size live was to change the top level -- which every paper arm also inherits. This moves each such
key whose top-level value differs from the shipped `config.example.json` into the `live` block and
puts the shipped default back at the top level, and optionally marks the arms that should keep
measuring live as `follows_live: true`.

Dry run by default: it prints the plan, including every paper arm whose effective value of a moved
key would change -- read that list before applying, because an arm that left the key unset was
trading the old top-level value, and after the move it trades the shipped default. `--apply` writes
both files, each backed up beside itself first. The files are rewritten as JSON (two-space indent),
so hand formatting is not kept; values and key order are.

    python -m cherrypick.meic.live_migrate [--follows-live ARM ...] [--apply]
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

from cherrypick.meic import paper
from cherrypick.meic import paths as _paths

_MISSING = object()


def plan(config: dict, registry: dict, example: dict, follows_live: list[str]) -> dict:
    """The migration as data: the new config and registry, what moved, and which paper arms change.
    Pure -- nothing is read or written here."""
    new_cfg = copy.deepcopy(config)
    new_reg = copy.deepcopy(registry)
    live = new_cfg.setdefault("live", {})
    moved, conflicts = [], []
    for key in paper.LIVE_OVERRIDE_KEYS:
        if key not in config or config[key] == example.get(key):
            continue
        if live.get(key) is not None:
            conflicts.append(
                f"{key}: the live block already sets {live[key]!r}; top level {config[key]!r} left alone"
            )
            continue
        live[key] = config[key]
        default = example.get(key, _MISSING)
        if default is _MISSING:
            del new_cfg[key]
        else:
            new_cfg[key] = default
        moved.append(
            {"key": key, "live": config[key], "top_level_now": None if default is _MISSING else default}
        )

    profiles = new_reg.get("profiles", new_reg)
    marked, unknown = [], []
    for name in follows_live:
        if not isinstance(profiles.get(name), dict):
            unknown.append(name)
            continue
        profiles[name][paper.FOLLOWS_LIVE_KEY] = True
        marked.append(name)

    old_profiles = registry.get("profiles", registry)
    changes = []
    for name, pdef in profiles.items():
        if name.startswith("_") or not isinstance(pdef, dict) or pdef.get("enabled", True) is False:
            continue
        before = _effective(_resolve(config, old_profiles, old_profiles[name]))
        after = _effective(_resolve(new_cfg, profiles, pdef))
        for key in paper.LIVE_OVERRIDE_KEYS:
            if before.get(key) != after.get(key):
                changes.append({"arm": name, "key": key, "before": before.get(key), "after": after.get(key)})
    live_before = _effective(paper.live_params(config, old_profiles))
    live_after = _effective(paper.live_params(new_cfg, profiles))
    live_changes = [
        {"key": k, "before": live_before.get(k), "after": live_after.get(k)}
        for k in sorted(set(live_before) | set(live_after))
        if live_before.get(k) != live_after.get(k)
    ]
    return {
        "config": new_cfg,
        "registry": new_reg,
        "moved": moved,
        "conflicts": conflicts,
        "marked_follows_live": marked,
        "unknown_arms": unknown,
        "paper_arm_changes": changes,
        "live_changes": live_changes,
    }


def _effective(params: dict) -> dict:
    """Params as the engine reads them: the entry start paper actually uses (its own key, else the
    shared one) and no `live` block -- which moves by design and trades nothing itself."""
    out = {k: v for k, v in params.items() if k not in ("live", "paper_entry_window_start")}
    out["entry_window_start"] = params.get("paper_entry_window_start") or params.get(
        "entry_window_start", "10:00"
    )
    return out


def _resolve(config: dict, profiles: dict, pdef: dict) -> dict:
    """An arm's params, resolving follows_live against THIS registry (paper._merged_params would read
    the machine's registry from disk, which is the 'before' state during a dry run)."""
    if pdef.get(paper.FOLLOWS_LIVE_KEY):
        return paper._profiles.merge_profile(paper.live_params(config, profiles), pdef)
    return paper._merged_params(config, pdef)


def _backup_and_write(path: Path, doc: dict, stamp: str) -> Path:
    backup = path.with_name(f"{path.name}.bak-pre-live-migrate-{stamp}")
    shutil.copy2(path, backup)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return backup


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--follows-live", nargs="*", default=[], metavar="ARM", help="arms to mark follows_live: true"
    )
    ap.add_argument("--apply", action="store_true", help="write the change (default: print the plan only)")
    args = ap.parse_args(argv)

    cfg_path = _paths.config_path()
    reg_path = _paths.risk_profiles_path()
    example_path = _paths.example_config_path()
    if cfg_path.resolve() == example_path.resolve():
        print(json.dumps({"ok": False, "error": "no machine config: this install runs the shipped example"}))
        return 1
    config = json.loads(cfg_path.read_text(encoding="utf-8"))
    registry = json.loads(reg_path.read_text(encoding="utf-8"))
    example = json.loads(example_path.read_text(encoding="utf-8"))

    p = plan(config, registry, example, args.follows_live)
    report = {k: v for k, v in p.items() if k not in ("config", "registry")}
    report["config_path"], report["registry_path"] = str(cfg_path), str(reg_path)
    if p["unknown_arms"]:
        print(json.dumps({"ok": False, "error": f"unknown arms: {p['unknown_arms']}", **report}, indent=2))
        return 1
    nothing = not p["moved"] and not p["marked_follows_live"]
    if not args.apply or nothing:
        print(
            json.dumps(
                {"ok": True, "applied": False, "nothing_to_do": nothing, **report}, indent=2, default=str
            )
        )
        return 0

    shipped_registry = _paths._PKG_ROOT.joinpath("config.risk.example.json").resolve()
    if p["marked_follows_live"] and reg_path.resolve() == shipped_registry:
        print(json.dumps({"ok": False, "error": "the registry is the shipped example; copy it home first"}))
        return 1
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backups = [str(_backup_and_write(cfg_path, p["config"], stamp))]
    if p["marked_follows_live"]:
        backups.append(str(_backup_and_write(reg_path, p["registry"], stamp)))
    print(json.dumps({"ok": True, "applied": True, "backups": backups, **report}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
