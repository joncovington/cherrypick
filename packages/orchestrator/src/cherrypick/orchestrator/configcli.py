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

from . import config as cfgmod
from . import configedit, liveops


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
    result = configedit.apply_raw_save(cfg, target, text, expected if isinstance(expected, int) else None)
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
    if op == "targets":
        return {"ok": True, "targets": configedit.targets(cfg)}
    if op == "load":
        return _op_load(cfg, req)
    if op == "save":
        return _op_save(cfg, req)
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
