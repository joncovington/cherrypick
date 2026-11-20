"""Stdin/stdout front-end to the config surface, for callers that aren't Python.

`settings_serve.py` serves `configedit` + `liveops` over HTTP for a human at a browser. This serves
the same two modules over one JSON request on stdin and one JSON response on stdout, for the console
(Node), which cannot import them and must not reimplement them: the guarded-pointer table, the
byte-span splicing that keeps a config's `_note`/`_header` documentation intact, the backups and the
atomic write are live-safety properties, and a second copy of them in another language is a second
copy that can drift. Everything here is a thin dispatch — no file logic of its own.

The response always carries `ok`; a refusal rides in `{"ok": false, "error": ..., "code": ...}` with
exit status 0, so a caller distinguishes "the config said no" from "the bridge is broken" by the
status alone. `code` is the machine-readable half (`guarded`, `conflict`, `invalid`, `not_found`,
`bad_request`) so callers map refusals to their own vocabulary without matching on prose.

No secret ever passes through here — `secretsops` is deliberately not wired in. The halt flag is
reachable (`set_halt`), because its whole design is that a click may toggle it; the guarded live
pointers are not, in either direction, exactly as over HTTP.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from . import automationedit, automationrun, configedit, fullsetadd, gridqueue, groupedit, ideaqueue, liveops
from . import config as cfgmod


def _err(message: str, code: str = "bad_request", **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "code": code, **extra}


def _op_load(cfg: dict[str, Any], req: dict[str, Any]) -> dict[str, Any]:
    target = req.get("target")
    if not isinstance(target, str):
        return _err("load needs a 'target'")
    try:
        return {"ok": True, **configedit.load(cfg, target)}
    except KeyError as exc:
        return _err(str(exc), "not_found")


def _op_save(cfg: dict[str, Any], req: dict[str, Any]) -> dict[str, Any]:
    """Splice every edit into the file's text, then hand the result to the raw-save path — so a
    section's worth of edits is one backup, one atomic write, and one mtime check, and a refusal
    part-way leaves the file untouched."""
    target = req.get("target")
    edits = req.get("edits")
    if not isinstance(target, str) or not isinstance(edits, list) or not edits:
        return _err("save needs a 'target' and a non-empty 'edits' list")

    guarded = configedit.GUARDED.get(target, {})
    for edit in edits:
        if not isinstance(edit, dict) or not isinstance(edit.get("pointer"), str):
            return _err("each edit needs a 'pointer' and a 'value'")
        pointer = edit["pointer"]
        if pointer in guarded:
            return _err(f"{pointer} is guarded — {guarded[pointer]}", "guarded", pointer=pointer)

    try:
        path = configedit._target_path(cfg, target)
    except KeyError as exc:
        return _err(str(exc), "not_found")
    if not path.exists():
        return _err(f"target '{target}' has no config file yet", "not_found")

    text = path.read_text(encoding="utf-8")
    for edit in edits:
        try:
            text = configedit.splice_value(text, edit["pointer"], edit.get("value"))
        except (KeyError, ValueError) as exc:
            return _err(str(exc), "not_found", pointer=edit["pointer"])

    expected = req.get("expected_mtime")
    # A browser's copy of the mtime may arrive rounded, even as a float: still the version check.
    has = isinstance(expected, (int, float)) and not isinstance(expected, bool)
    result = configedit.apply_raw_save(cfg, target, text, int(expected) if has else None)
    if not result.get("ok"):
        error = str(result.get("error", ""))
        code = "conflict" if "changed on disk" in error else "guarded" if "guarded" in error else "invalid"
        return {**result, "code": code}
    return result


def handle(req: dict[str, Any]) -> dict[str, Any]:
    op = req.get("op")
    if op == "halt_status":
        return liveops.halt_status()
    if op == "set_halt":
        present = req.get("present")
        if not isinstance(present, bool):
            return _err("set_halt needs a boolean 'present'")
        return liveops.set_halt(present)

    cfg = cfgmod.load_config()
    if op == "features":
        # The resolved module/capability/feature view (capabilities.gated_features): what the
        # console shows or hides. Read-only, and the console never re-derives the rule itself.
        from . import capabilities

        return capabilities.gated_features(cfg)
    if op == "targets":
        return {"ok": True, "targets": configedit.targets(cfg)}
    if op == "load":
        return _op_load(cfg, req)
    if op == "save":
        return _op_save(cfg, req)
    if op == "automation_draft":
        # The Builder's check of one automation, by the module itself; writes nothing.
        return automationedit.draft(cfg, req)
    if op == "automation_today":
        # The Builder's "Today, live": the module decides it now as paper would; writes nothing.
        return automationedit.today(cfg, req)
    if op == "automation_payoff":
        # The Builder's payoff chart: the structure the module's rules would take, on live quotes or
        # the last session our own recorder holds; writes nothing.
        return automationedit.payoff(cfg, req, automationedit.run_module_payoff)
    if op == "automation_strategy":
        # The Builder's strategy selector: the legs and expiry a strategy's settings make; writes nothing.
        return automationedit.strategy(cfg, req, automationedit.run_module_strategy)
    if op == "automation_entry":
        # The Builder's entry presets: the entry and the stacked filters' gates; writes nothing.
        return automationedit.entry(cfg, req, automationedit.run_module_entry)
    if op == "automation_save":
        # The Builder's save: that check, then the one entry it returned (always off) written
        # through the same raw-save path as every config save.
        return automationedit.save(cfg, req)
    if op == "automation_groups":
        # The Groups page: every group and every automation by group, from the module. Reads only.
        return groupedit.view(cfg, req, groupedit.run_module_view)
    if op == "automation_groups_check":
        # The Groups page's check of a change, by the module; writes nothing.
        return groupedit.check(cfg, req, groupedit.run_module_check)
    if op == "automation_groups_save":
        # The Groups page's save: that check, then the groups block and the moved automations' `group`
        # it returned, written through the same raw-save path as every config save.
        return groupedit.save(cfg, req, groupedit.run_module_check)
    if op == "automation_reference":
        # The Builder's pin of one Builder run as an automation's backtest reference: the module's check,
        # the backtest package's word that the run exists, then the `references` block it returned.
        from . import referenceedit

        return referenceedit.save(cfg, req, referenceedit.run_module_check, referenceedit.run_backtest_runs)
    if op == "fullset_plan":
        # What adding an underlying to the full set would fetch, from the backtest's planner; writes nothing.
        return fullsetadd.plan(cfg, req)
    if op == "fullset_add":
        # The Builder's addition to the full set: its fetches queued (capped, only while new work may
        # use the vendor), and with `run` that automation's backtest after them. No config write.
        return fullsetadd.add(cfg, req)
    if op == "automation_backtest":
        # The Builder's backtest: one explore run queued on the research queue (a `script` row), never
        # a config write; refused unless the automation's own suggestion says our data covers it.
        return automationrun.queue(cfg, req)
    if op == "automation_runs":
        # The Builder's explore-runs list: that automation's queue rows and results. Reads only.
        return automationrun.runs(cfg, req)
    if op == "automation_run":
        # The Builder's run card: one result read whole by the backtest package. Reads only.
        return automationrun.run_card(cfg, req)
    if op == "automation_ideas":
        # The Builder's idea search: one search of the newest long enough run queued on the research
        # queue (a `script` row), its split fixed in the command; never a config write.
        return ideaqueue.queue(cfg, req)
    if op == "automation_idea_searches":
        # That automation's idea searches: queue rows and results in brief. Reads only.
        return ideaqueue.searches(cfg, req)
    if op == "automation_idea_search":
        # One idea search's whole result. Reads only.
        return ideaqueue.search(cfg, req)
    if op == "automation_idea_declare":
        # Declare: one surviving idea's register row drafted into its result. No config or register write.
        return ideaqueue.declare(cfg, req)
    if op == "automation_idea_holdout":
        # A declared idea's one held-out test, logged on its result. No config write.
        return ideaqueue.holdout(cfg, req)
    if op == "automation_grid":
        # The Builder's settings grid: one replay of the automation and every exit and width setting
        # beside it, queued on the research queue with the split fixed. Writes no config.
        return gridqueue.queue(cfg, req)
    if op == "automation_grids":
        # That automation's settings grids: queue rows and results in brief. Reads only.
        return gridqueue.grids(cfg, req)
    if op == "automation_grid_result":
        # One settings grid's rows, never its trades. Reads only.
        return gridqueue.grid(cfg, req)
    if op == "automation_grid_declare":
        # Declare: one surviving setting's register row drafted into its result. No config or register write.
        return gridqueue.declare(cfg, req)
    if op == "automation_grid_holdout":
        # A declared setting's one held-out test, logged on its result. No config write.
        return gridqueue.holdout(cfg, req)
    return _err(f"unknown op: {op!r}")


def main() -> int:
    # Both ends are UTF-8 by contract. On Windows the child of a service process inherits the
    # console codepage, and the guard hints carry em dashes — left alone, a refusal comes back as
    # mojibake or kills the reader mid-decode, which is the one moment the caller needs the text.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        req = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as exc:
        print(f"configcli: request is not valid JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(req, dict):
        print("configcli: request must be a JSON object", file=sys.stderr)
        return 2
    try:
        response = handle(req)
    except Exception as exc:  # noqa: BLE001 — the bridge reports, it never crashes its caller blind
        print(f"configcli: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(response, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
