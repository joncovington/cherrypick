"""The Builder's pin of a backtest reference: the automations module's check, then the config editor's write.

An automation's reference is the one Builder run its paper is set against (`automations.references`). The
module owns what a pin may be, and this package never imports it: a save runs the module's
`cli reference-check` by subprocess, refuses on any problem it names, asks the backtest package that the
run exists (`backtest read automation-runs`, by subprocess), and writes only the `references` block the
check returned, replaced whole or added at the document root, in one `configedit.apply_raw_save` (backup,
mtime check, guarded pointers, atomic write) as every other config save does.

A pin changes nothing an automation trades: no automation's entry is touched, so no block hash, backtest
label or replay moves. Unpinning (`run` null) needs no backtest read.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import automationedit, automationrun, configedit

TARGET = automationedit.TARGET
RunsReader = Callable[[str], "dict[str, Any] | None"]


def _err(message: str, code: str = "bad_request", **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "code": code, **extra}


def run_module_check(cfg: dict[str, Any], req: dict[str, Any]) -> dict[str, Any] | None:
    return automationedit.run_module_cli(cfg, "reference-check", req)


def run_backtest_runs(name: str) -> dict[str, Any] | None:
    return automationrun.backtest_runs(name)


def save(
    cfg: dict[str, Any],
    req: dict[str, Any],
    run: automationedit.Runner = run_module_check,
    runs: RunsReader = run_backtest_runs,
) -> dict[str, Any]:
    """Check `{name, run}` with the module, confirm the run exists, then write the block it returned."""
    name, pinned = req.get("name"), req.get("run")
    if not isinstance(name, str) or not name:
        return _err("automation_reference needs a 'name'")
    if pinned is not None and not isinstance(pinned, str):
        return _err("'run' must be a Builder run's name, or null to unpin")
    checked = run(cfg, {"name": name, "run": pinned})
    if not checked:
        return _err("the automations module could not be asked (is it installed?)", "unavailable")
    if not checked.get("ok"):
        return _err(str(checked.get("error") or "the automations module refused the request"))
    problems = checked.get("problems") or []
    if problems:
        return _err("; ".join(map(str, problems)), "invalid", problems=problems)
    block = checked.get("block")
    if not isinstance(block, dict):
        return _err("the module's check returned nothing to write", "invalid")
    if pinned is not None:
        listed = runs(name)
        if not listed or not listed.get("ok"):
            return _err("the backtest package could not list this automation's runs", "unavailable")
        if pinned not in {r.get("run") for r in listed.get("runs") or []}:
            return _err(f"no Builder run {pinned} for {name}", "not_found")

    try:
        path = configedit._target_path(cfg, TARGET)
    except KeyError as exc:
        return _err(str(exc), "not_found")
    if not path.exists():
        return _err("the automations module has no config file yet", "not_found")
    text = path.read_text(encoding="utf-8")
    try:
        text = configedit.splice_value(text, "/references", block)
    except KeyError:
        text = configedit.insert_key(text, "", "references", block)
    expected = req.get("expected_mtime")
    has = isinstance(expected, (int, float)) and not isinstance(expected, bool)
    result = configedit.apply_raw_save(cfg, TARGET, text, int(expected) if has else None)
    if not result.get("ok"):
        error = str(result.get("error", ""))
        code = "conflict" if "changed on disk" in error else "guarded" if "guarded" in error else "invalid"
        return {**result, "code": code}
    return {**result, "name": name, "run": pinned}
