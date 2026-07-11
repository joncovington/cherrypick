"""`cherrypick init` — scaffold + validate a machine-local config.json (Part 12 Concept E).

Safe-by-default onboarding: writes config.json only when absent (or with --force), preferring the
repo's rich config.example.json and falling back to a compact embedded template for an installed copy
that ships no example file. Then it validates the result and points the user at the next steps
(secrets-set → doctor → install).

`validate_config` is a pure structural check (no filesystem/broker) so it's unit-tested; the CLI adds
filesystem path-resolution on top.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import config as cfgmod

KNOWN_CHANNELS = {"log", "desktop", "slack", "discord"}

# Minimal-but-valid fallback when config.example.json isn't on disk (e.g. a pip-installed copy). Kept
# intentionally small — the user fills in modules; `validate_config` guides them.
_MINIMAL_TEMPLATE: dict[str, Any] = {
    "_comment": "Cherrypick config scaffolded by `cherrypick init`. Add modules by repo or path.",
    "timezone": "America/New_York",
    "modules": {},
    "watchdog": {"task_name": "Cherrypick-Watchdog", "interval_minutes": 10, "renotify_minutes": 60},
    "notify": {"channels": ["log", "desktop"], "trade_channels": ["log"], "desktop_app_name": "Cherrypick"},
}


def _template_text() -> tuple[str, str]:
    """(text, source) for a fresh config — the repo example when present, else the embedded minimal."""
    example = cfgmod.ROOT / "config.example.json"
    if example.exists():
        return example.read_text(encoding="utf-8"), str(example)
    return json.dumps(_MINIMAL_TEMPLATE, indent=2) + "\n", "embedded minimal template"


def scaffold(target: Path | None = None, force: bool = False) -> dict[str, Any]:
    """Write a config.json if absent (or force). Never clobbers an existing config unless force."""
    target = target or cfgmod.CONFIG_PATH
    if target.exists() and not force:
        return {"created": False, "path": str(target), "reason": "already exists (not overwritten)"}
    text, source = _template_text()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return {"created": True, "path": str(target), "source": source}


def validate_config(cfg: dict[str, Any]) -> list[tuple[str, str]]:
    """Structural validation. Returns (level, message) issues; level in {error, warn}. No filesystem."""
    issues: list[tuple[str, str]] = []
    modules = cfg.get("modules")
    if not isinstance(modules, dict):
        return [("error", "'modules' section is missing or not an object")]

    enabled = [n for n, m in modules.items() if isinstance(m, dict) and m.get("enabled")]
    if not enabled:
        issues.append(("warn", "no modules are enabled — add a module and set enabled=true"))

    for name, m in modules.items():
        if not isinstance(m, dict):
            issues.append(("error", f"module '{name}' is not an object"))
            continue
        if not (m.get("repo") or m.get("path")):
            issues.append(("error", f"module '{name}' needs a 'repo' URL or a 'path'"))
        paper = m.get("paper")
        if not isinstance(paper, dict) or not paper.get("trade_schema"):
            issues.append(("warn", f"module '{name}' has no paper.trade_schema (report/notify skip it)"))

    if not cfg.get("timezone"):
        issues.append(("warn", "no 'timezone' set — defaulting to America/New_York"))

    channels = (cfg.get("notify") or {}).get("channels") or []
    unknown = [c for c in channels if c not in KNOWN_CHANNELS]
    if unknown:
        issues.append(("warn", f"unknown notify channel(s): {unknown} (known: {sorted(KNOWN_CHANNELS)})"))

    return issues


def check_module_paths(cfg: dict[str, Any]) -> list[tuple[str, str]]:
    """Filesystem check: is each enabled module's checkout present? (warn → run `cherrypick install`)."""
    issues: list[tuple[str, str]] = []
    for name, m in cfgmod.enabled_modules(cfg).items():
        try:
            root = cfgmod.module_root(m, name)
        except ValueError as exc:
            issues.append(("error", f"module '{name}': {exc}"))
            continue
        if not root.exists():
            where = "path" if m.get("path") else "managed location"
            issues.append(
                ("warn", f"module '{name}' not present at its {where} ({root}) — run: cherrypick install")
            )
    return issues


def run(cfg: dict[str, Any] | None = None, *, force: bool = False) -> dict[str, Any]:
    """Scaffold (if needed) + validate. Returns a JSON-safe result for the CLI to print."""
    scaffold_result = scaffold(force=force)
    try:
        cfg = cfgmod.load_config()
    except Exception as exc:
        return {"ok": False, "scaffold": scaffold_result, "error": f"could not load config: {exc}"}

    issues = validate_config(cfg) + check_module_paths(cfg)
    errors = [m for lvl, m in issues if lvl == "error"]
    warns = [m for lvl, m in issues if lvl == "warn"]
    next_steps = [
        "Edit config.json: set each module's repo/path and enabled=true.",
        "Store push-channel webhooks: cherrypick secrets-set --channel slack|discord",
        "Check readiness: cherrypick doctor",
        "Register tasks + fetch modules: cherrypick install",
    ]
    return {
        "ok": not errors,
        "scaffold": scaffold_result,
        "config": scaffold_result["path"],
        "errors": errors,
        "warnings": warns,
        "next_steps": next_steps,
    }
