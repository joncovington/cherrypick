"""The Builder's save of one automation: the config editor's write path, with the module's own check first.

The automations module owns what a valid automation is; this package never imports it. So a save runs
the module's `cli draft` by subprocess (the same rule the paper loop applies, `drafts.draft`), refuses on
any problem it names, and writes only the entry that check returned, through `configedit.apply_raw_save`
(backup, mtime check, guarded pointers, atomic write) as every other config save does. The entry goes in
at `/automations/<name>` (or the block's older spelling the file uses), replaced in place when the name is
there and added as the block's last entry when it is new, so the rest of the file is untouched.

**A saved automation is always off.** The check returns the entry with `enabled: false`, and this module
refuses one that is not, so the console can neither turn an automation on through a save nor change that
by sending something else. Turning one on is the Groups page's switch (`groupedit`), from the next
session, after the module's own check.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from typing import Any

from cherrypick.core import config as core_config

from . import config as cfgmod
from . import configedit
from .util import CREATE_NO_WINDOW, first_json

TARGET = "automations"
#: The block's spellings in the order the module reads them (`automations` first).
BLOCKS: tuple[str, ...] = ("automations", *core_config.ARM_REGISTRY_KEYS)
TIMEOUT_S = 60


def _err(message: str, code: str = "bad_request", **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "code": code, **extra}


def run_module_draft(cfg: dict[str, Any], req: dict[str, Any]) -> dict[str, Any] | None:
    """The module's answer to one `cli draft` request, or None when it could not be asked."""
    return run_module_cli(cfg, "draft", req)


def run_module_today(cfg: dict[str, Any], req: dict[str, Any]) -> dict[str, Any] | None:
    """The module's answer to one `cli builder-today` request, or None when it could not be asked."""
    return run_module_cli(cfg, "builder-today", req)


def run_module_payoff(cfg: dict[str, Any], req: dict[str, Any]) -> dict[str, Any] | None:
    """The module's answer to one `cli builder-payoff` request, or None when it could not be asked."""
    return run_module_cli(cfg, "builder-payoff", req)


def run_module_cli(cfg: dict[str, Any], command: str, req: dict[str, Any]) -> dict[str, Any] | None:
    """The automations module's `cli <command>` with `req` as JSON on stdin; its JSON answer, or None."""
    mcfg = (cfg.get("modules") or {}).get(TARGET) or {}
    try:
        r = subprocess.run(
            [cfgmod.python_exe(), "-m", "cherrypick.automations.cli", command],
            input=json.dumps(req),
            cwd=str(cfgmod.module_root(mcfg, TARGET)),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=TIMEOUT_S,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return (first_json(r.stdout) or None) if r.returncode == 0 else None


Runner = Callable[[dict[str, Any], dict[str, Any]], "dict[str, Any] | None"]


def draft(cfg: dict[str, Any], req: dict[str, Any], run: Runner = run_module_draft) -> dict[str, Any]:
    """The module's check of `{name, automation}` (or, without `automation`, the editable spec), passed
    through unchanged. Read-only."""
    name = req.get("name")
    if not isinstance(name, str) or not name:
        return _err("automation_draft needs a 'name'")
    body: dict[str, Any] = {"name": name}
    if "automation" in req:
        if not isinstance(req["automation"], dict):
            return _err("'automation' must be an object")
        body["automation"] = req["automation"]
    answer = run(cfg, body)
    if not answer:
        return _err("the automations module could not be asked (is it installed?)", "unavailable")
    if not answer.get("ok"):
        return _err(str(answer.get("error") or "the automations module refused the request"), "bad_request")
    return answer


def today(cfg: dict[str, Any], req: dict[str, Any], run: Runner = run_module_today) -> dict[str, Any]:
    """The module's decision of `{name}` (or an unsaved `{name, automation}`) now, as paper would
    decide it, on a read-only snapshot (`cli builder-today`). Writes nothing."""
    return _read(cfg, req, run, "automation_today")


def payoff(cfg: dict[str, Any], req: dict[str, Any], run: Runner = run_module_payoff) -> dict[str, Any]:
    """The structure `{name}` (or an unsaved `{name, automation}`) would take and its P&L at expiry, on
    live quotes or the last session our own recorder holds (`cli builder-payoff`). Writes nothing."""
    return _read(cfg, req, run, "automation_payoff")


def run_module_strategy(cfg: dict[str, Any], req: dict[str, Any]) -> dict[str, Any] | None:
    """The module's answer to one `cli builder-strategy` request, or None when it could not be asked."""
    return run_module_cli(cfg, "builder-strategy", req)


def strategy(cfg: dict[str, Any], req: dict[str, Any], run: Runner = run_module_strategy) -> dict[str, Any]:
    """A strategy's legs and expiry from its settings, and a whole automation from it for the wizard
    (`cli builder-strategy`): the strategy's name and its settings object, nothing else. Writes nothing."""
    name = req.get("strategy")
    settings = req.get("settings", {})
    if not isinstance(name, str) or not name:
        return _err("automation_strategy needs a 'strategy'")
    if not isinstance(settings, dict):
        return _err("'settings' must be an object")
    answer = run(cfg, {"strategy": name, "settings": settings})
    if not answer:
        return _err("the automations module could not be asked (is it installed?)", "unavailable")
    if not answer.get("ok"):
        return _err(str(answer.get("error") or "the automations module refused the request"), "bad_request")
    return answer


def run_module_entry(cfg: dict[str, Any], req: dict[str, Any]) -> dict[str, Any] | None:
    """The module's answer to one `cli builder-entry` request, or None when it could not be asked."""
    return run_module_cli(cfg, "builder-entry", req)


def entry(cfg: dict[str, Any], req: dict[str, Any], run: Runner = run_module_entry) -> dict[str, Any]:
    """An entry preset's entry and the gates of the filters stacked on it (`cli builder-entry`): the
    preset's name, its settings and the filters, each a name and its settings, nothing else. Writes
    nothing."""
    name = req.get("entry")
    settings = req.get("settings", {})
    filters = req.get("filters", [])
    if not isinstance(name, str) or not name:
        return _err("automation_entry needs an 'entry'")
    if not isinstance(settings, dict):
        return _err("'settings' must be an object")
    if not isinstance(filters, list) or not all(
        isinstance(f, dict) and isinstance(f.get("filter"), str) and isinstance(f.get("settings", {}), dict)
        for f in filters
    ):
        return _err("'filters' must be a list of {filter, settings}")
    body = {
        "entry": name,
        "settings": settings,
        "filters": [{"filter": f["filter"], "settings": f.get("settings", {})} for f in filters],
    }
    answer = run(cfg, body)
    if not answer:
        return _err("the automations module could not be asked (is it installed?)", "unavailable")
    if not answer.get("ok"):
        return _err(str(answer.get("error") or "the automations module refused the request"), "bad_request")
    return answer


def _read(cfg: dict[str, Any], req: dict[str, Any], run: Runner, op: str) -> dict[str, Any]:
    """One read of the module by name, with an unsaved edit passed through and nothing else."""
    name = req.get("name")
    if not isinstance(name, str) or not name:
        return _err(f"{op} needs a 'name'")
    body: dict[str, Any] = {"name": name}
    if "automation" in req:
        if not isinstance(req["automation"], dict):
            return _err("'automation' must be an object")
        body["automation"] = req["automation"]
    answer = run(cfg, body)
    if not answer:
        return _err("the automations module could not be asked (is it installed?)", "unavailable")
    if not answer.get("ok"):
        return _err(str(answer.get("error") or "the automations module refused the request"), "bad_request")
    return answer


def _block(text: str) -> str | None:
    for key in BLOCKS:
        try:
            configedit.locate_value(text, "/" + key)
            return key
        except KeyError:
            continue
    return None


def _token(name: str) -> str:
    return name.replace("~", "~0").replace("/", "~1")


def save(
    cfg: dict[str, Any],
    req: dict[str, Any],
    run: Runner = run_module_draft,
) -> dict[str, Any]:
    """Check `{name, automation}` with the module, then write the entry it returned, off."""
    if not isinstance(req.get("automation"), dict):
        return _err("automation_save needs a 'name' and an 'automation' object")
    checked = draft(cfg, req, run)
    if not checked.get("ok"):
        return checked
    problems = checked.get("problems") or []
    if problems:
        return _err("; ".join(map(str, problems)), "invalid", problems=problems)
    entry = checked.get("entry")
    if not isinstance(entry, dict) or entry.get("enabled") is not False:
        return _err("the module's check did not return an entry that is off; nothing was written", "invalid")

    try:
        path = configedit._target_path(cfg, TARGET)
    except KeyError as exc:
        return _err(str(exc), "not_found")
    if not path.exists():
        return _err("the automations module has no config file yet", "not_found")
    text = path.read_text(encoding="utf-8")
    block = _block(text)
    if block is None:
        return _err("the automations config has no automations block", "not_found")
    name = checked["name"]
    try:
        text = configedit.splice_value(text, f"/{block}/{_token(name)}", entry)
        added = False
    except KeyError:
        text = configedit.insert_key(text, f"/{block}", name, entry)
        added = True

    expected = req.get("expected_mtime")
    has = isinstance(expected, (int, float)) and not isinstance(expected, bool)
    result = configedit.apply_raw_save(cfg, TARGET, text, int(expected) if has else None)
    if not result.get("ok"):
        error = str(result.get("error", ""))
        code = "conflict" if "changed on disk" in error else "guarded" if "guarded" in error else "invalid"
        return {**result, "code": code}
    return {**result, "name": name, "added": added, "enabled": False, "view": checked.get("view")}


def run_module_legs(cfg: dict[str, Any], req: dict[str, Any]) -> dict[str, Any] | None:
    """The module's answer to `cli legs-convert`, or None when it could not be asked."""
    return run_module_cli(cfg, "legs-convert", req)


#: The written spelling of a leg, all a conversion may write (`arms.WRITTEN_LEG_KEYS` in the module).
WRITTEN_LEG_KEYS = frozenset({"name", "action", "qty", "side", "strike"})


def convert_legs(
    cfg: dict[str, Any], *, apply: bool = False, run: Runner = run_module_legs
) -> dict[str, Any]:
    """Rewrite this machine's automations stored in the older leg spelling (side/right) in the written
    one (action/qty/side). The module names them and proves the conversion changes how none of them
    loads (`cli legs-convert`); this writes only each one's `legs`, in one save with its backup, and
    only with `apply`. A dry run lists them."""
    answer = run(cfg, {})
    if not answer:
        return _err("the automations module could not be asked (is it installed?)", "unavailable")
    if not answer.get("ok"):
        return _err(str(answer.get("error") or "the automations module refused the request"))
    problems = answer.get("problems") or []
    if problems:
        return _err("; ".join(map(str, problems)), "invalid", problems=problems)
    convert = answer.get("convert")
    if not isinstance(convert, dict):
        return _err("the module returned nothing to convert", "invalid")
    for name, legs in convert.items():
        if not isinstance(legs, list) or not all(
            isinstance(g, dict) and not (set(g) - WRITTEN_LEG_KEYS) for g in legs
        ):
            return _err(f"the module returned legs for {name} this writer does not take", "invalid")
    names = sorted(convert)
    if not apply or not names:
        return {"ok": True, "applied": False, "convert": names}
    try:
        path = configedit._target_path(cfg, TARGET)
    except KeyError as exc:
        return _err(str(exc), "not_found")
    text = path.read_text(encoding="utf-8")
    block = _block(text)
    if block is None:
        return _err("the automations config has no automations block", "not_found")
    for name in names:
        text = configedit.splice_value(text, f"/{block}/{_token(name)}/legs", convert[name])
    result = configedit.apply_raw_save(cfg, TARGET, text, None)
    if not result.get("ok"):
        return {**result, "code": "invalid"}
    return {**result, "applied": True, "convert": names}
