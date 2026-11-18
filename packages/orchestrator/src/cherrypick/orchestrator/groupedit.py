"""The console's Groups page save: the automations module's check, then the config editor's write path.

The module owns what a group is (`automations.groupedit`), and this package never imports it. A save
runs the module's `cli groups-check` by subprocess, refuses on any problem it names, and writes only what
that check returned, in one write through `configedit.apply_raw_save` (backup, mtime check, guarded
pointers, atomic write) as every other config save does:

- the `groups` block, replaced whole (the check keeps the file's notes in it), or added when the file has
  none;
- each moved automation's `group` key, set in its entry, added to it, or added as a one-key entry for a
  shipped automation the file does not name yet.

Nothing else in the file changes. A group switched on here turns nothing on by itself: a member that is
off stays off (a Builder save is always off). A group switched off switches its members off for new
entries; what they hold is still managed and settled.
"""

from __future__ import annotations

from typing import Any

from . import automationedit, configedit

TARGET = automationedit.TARGET


def _err(message: str, code: str = "bad_request", **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "code": code, **extra}


def run_module_view(cfg: dict[str, Any], req: dict[str, Any]) -> dict[str, Any] | None:
    return automationedit.run_module_cli(cfg, "groups-view", req)


def run_module_check(cfg: dict[str, Any], req: dict[str, Any]) -> dict[str, Any] | None:
    return automationedit.run_module_cli(cfg, "groups-check", req)


def view(
    cfg: dict[str, Any], req: dict[str, Any], run: automationedit.Runner = run_module_view
) -> dict[str, Any]:
    """Every group and every automation by group, from the module (`cli groups-view`). Read-only."""
    answer = run(cfg, {})
    if not answer:
        return _err("the automations module could not be asked (is it installed?)", "unavailable")
    if not answer.get("ok"):
        return _err(str(answer.get("error") or "the automations module refused the request"))
    return answer


def check(
    cfg: dict[str, Any], req: dict[str, Any], run: automationedit.Runner = run_module_check
) -> dict[str, Any]:
    """The module's check of `{groups, members}`, passed through. Read-only."""
    if not isinstance(req.get("groups"), dict) or not isinstance(req.get("members", {}), dict):
        return _err("automation_groups needs 'groups' and 'members' objects")
    answer = run(cfg, {"groups": req["groups"], "members": req.get("members") or {}})
    if not answer:
        return _err("the automations module could not be asked (is it installed?)", "unavailable")
    if not answer.get("ok"):
        return _err(str(answer.get("error") or "the automations module refused the request"))
    return answer


def _set_group(text: str, block: str, name: str, group: str | None) -> str:
    entry = f"/{block}/{automationedit._token(name)}"
    try:
        return configedit.splice_value(text, f"{entry}/group", group)
    except KeyError:
        pass
    try:
        return configedit.insert_key(text, entry, "group", group)
    except KeyError:
        return configedit.insert_key(text, f"/{block}", name, {"group": group})


def save(
    cfg: dict[str, Any], req: dict[str, Any], run: automationedit.Runner = run_module_check
) -> dict[str, Any]:
    """Check `{groups, members}` with the module, then write the block and the moves it returned."""
    checked = check(cfg, req, run)
    if not checked.get("ok"):
        return checked
    problems = checked.get("problems") or []
    if problems:
        return _err("; ".join(map(str, problems)), "invalid", problems=problems)
    block, moves = checked.get("block"), checked.get("moves") or {}
    if not isinstance(block, dict) or not isinstance(moves, dict):
        return _err("the module's check returned nothing to write", "invalid")

    try:
        path = configedit._target_path(cfg, TARGET)
    except KeyError as exc:
        return _err(str(exc), "not_found")
    if not path.exists():
        return _err("the automations module has no config file yet", "not_found")
    text = path.read_text(encoding="utf-8")
    try:
        text = configedit.splice_value(text, "/groups", block)
    except KeyError:
        text = configedit.insert_key(text, "", "groups", block)
    if moves:
        entries = automationedit._block(text)
        if entries is None:
            text = configedit.insert_key(text, "", "automations", {})
            entries = "automations"
        for name, group in sorted(moves.items()):
            text = _set_group(text, entries, name, group)

    expected = req.get("expected_mtime")
    has = isinstance(expected, (int, float)) and not isinstance(expected, bool)
    result = configedit.apply_raw_save(cfg, TARGET, text, int(expected) if has else None)
    if not result.get("ok"):
        error = str(result.get("error", ""))
        code = "conflict" if "changed on disk" in error else "guarded" if "guarded" in error else "invalid"
        return {**result, "code": code}
    return {**result, "moved": sorted(moves), "groups": sorted(k for k in block if not k.startswith("_"))}
