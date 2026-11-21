"""An automation's page retires or reinstates it: the automations module's check, then the config write.

The module owns what retiring means (`automations.drafts.retire`), and this package never imports it: a save
runs the module's `cli retire-check` by subprocess, refuses on any problem it names, and writes only the
two keys the check returned in that automation's entry, `retired` (the date, or false) and `enabled`
(false either way: reinstating never switches one on), each set, added to the entry, or added in a new entry
for a shipped automation the file does not name yet. One `configedit.apply_raw_save` (backup, mtime check,
guarded pointers, atomic write), as every other config save.

Paper only. A retired automation enters nothing new; what it holds is still managed to its exits.
"""

from __future__ import annotations

from typing import Any

from . import automationedit, configedit, groupedit

TARGET = automationedit.TARGET
#: The only keys a retire or reinstate writes.
KEYS = ("retired", "enabled")


def _err(message: str, code: str = "bad_request", **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "code": code, **extra}


def run_module_check(cfg: dict[str, Any], req: dict[str, Any]) -> dict[str, Any] | None:
    return automationedit.run_module_cli(cfg, "retire-check", req)


def save(
    cfg: dict[str, Any], req: dict[str, Any], run: automationedit.Runner = run_module_check
) -> dict[str, Any]:
    """Check `{name, retired}` with the module, then write the two keys it returned."""
    name, retired = req.get("name"), req.get("retired")
    if not isinstance(name, str) or not name or not isinstance(retired, bool):
        return _err("automation_retire needs a 'name' and 'retired' true or false")
    checked = run(cfg, {"name": name, "retired": retired})
    if not checked:
        return _err("the automations module could not be asked (is it installed?)", "unavailable")
    if not checked.get("ok"):
        return _err(str(checked.get("error") or "the automations module refused the request"))
    problems = checked.get("problems") or []
    if problems:
        return _err("; ".join(map(str, problems)), "invalid", problems=problems)
    entry = checked.get("entry")
    if not isinstance(entry, dict) or set(entry) != set(KEYS) or entry.get("enabled") is not False:
        return _err("the module's check returned an entry this writer does not take", "invalid")
    if retired != (entry["retired"] is not False):
        return _err("the module's check returned the other way round", "invalid")

    try:
        path = configedit._target_path(cfg, TARGET)
    except KeyError as exc:
        return _err(str(exc), "not_found")
    if not path.exists():
        return _err("the automations module has no config file yet", "not_found")
    text = path.read_text(encoding="utf-8")
    block = automationedit._block(text)
    if block is None:
        text = configedit.insert_key(text, "", "automations", {})
        block = "automations"
    for key in KEYS:
        text = groupedit._set_key(text, block, name, key, entry[key])
    expected = req.get("expected_mtime")
    has = isinstance(expected, (int, float)) and not isinstance(expected, bool)
    result = configedit.apply_raw_save(cfg, TARGET, text, int(expected) if has else None)
    if not result.get("ok"):
        error = str(result.get("error", ""))
        code = "conflict" if "changed on disk" in error else "guarded" if "guarded" in error else "invalid"
        return {**result, "code": code}
    return {**result, "name": name, "retired": entry["retired"], "notes": checked.get("notes") or []}
