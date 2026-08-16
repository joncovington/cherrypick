"""File model for the settings surface (`cherrypick settings`): read, edit, and organize the suite's
config files without disturbing what makes them documentation.

The suite's configs carry their docs AS DATA — `_note`/`_comment` string entries and `*_header`
section markers, in a deliberate key order. A load→json.dumps→save round trip would rewrite all of
it, so this module never re-serializes a document to edit it. A field edit locates the exact byte
span of the value at a JSON pointer and splices the new value in place (`splice_value`); everything
else — key order, notes, indentation — is untouched by construction. A raw save writes the client's
text verbatim after validation. `organize` rebuilds a live config in its example file's section
order, re-emitting every value from its original byte span.

Guarded live-trading pointers (`GUARDED`) are refused in BOTH write paths, in either direction —
this surface can never arm or de-risk live trading. Pure file logic only: no HTTP here.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from cherrypick.core import home as _home

from . import config as cfgmod
from . import init as initmod

# JSON pointers this surface refuses to write, in either direction (arming AND disarming) — the rule
# stays simple and testable, and the suite's live-arming rituals (per-day /live-flies-start, the
# gate0 attestation, hand-edited enable_live_trading) keep their single deliberate path. Values are
# the hint shown beside the locked field.
GUARDED: dict[str, dict[str, str]] = {
    "meic": {
        "/enable_live_trading": "hand-edit after the live-trading plan's gates; never from this surface",
        "/account_deploy_limit_pct": "live risk cap — hand-edit deliberately, not from this surface",
    },
    "earnings": {
        "/enable_live_trading": "hand-edit after the live-trading plan's gates; never from this surface",
    },
    "flies": {
        "/live/enabled": "flies live is armed per-day via /live-flies-start; this flag is a plan-rung step",
        "/live/gate0_confirmed": "a human attestation string — write it by hand, with the plan doc open",
        "/live/daily_loss_halt_dollars": "live loss halt — hand-edit deliberately, not from this surface",
        "/live/account_deploy_limit_pct": "live risk cap — hand-edit deliberately, not from this surface",
    },
}

# Keys whose change breaks an orchestrator coupling if done silently (docs/configuration-and-storage.md):
# a module's paper_db + trade_schema pair, and keyring_service + account designation. Any edit touching
# one of these returns a warning; the save still proceeds.
COUPLED: tuple[str, ...] = ("paper_db", "trade_schema", "keyring_service")

# The module packages whose home config files this surface edits (~/.cherrypick/config/<pkg>.json).
_MODULE_TARGETS: tuple[str, ...] = ("calendars", "earnings", "flies", "gex", "meic", "pmcc", "streamer")

# Each target's config.example.json, relative to the orchestrator checkout (cfgmod.ROOT). Used by
# `organize` as the canonical section order. A module's configured `path` wins when present.
_EXAMPLE_REL: dict[str, str] = {
    "orchestrator": "config.example.json",
    "calendars": "../calendars/config.example.json",
    "earnings": "../earnings/config/config.example.json",
    "flies": "../flies/config.example.json",
    "gex": "../gex/config.example.json",
    "meic": "../meic/config.example.json",
    "pmcc": "../pmcc/config.example.json",
    "streamer": "../streamer/config.example.json",
}


# ---------------------------------------------------------------------------
# JSON pointer + byte-span walker
# ---------------------------------------------------------------------------


def _pointer_tokens(pointer: str) -> list[str]:
    """RFC 6901 pointer → reference tokens. '/live/enabled' → ['live', 'enabled']."""
    if not pointer.startswith("/"):
        raise ValueError(f"JSON pointer must start with '/': {pointer!r}")
    return [tok.replace("~1", "/").replace("~0", "~") for tok in pointer[1:].split("/")]


def _ws(t: str, i: int) -> int:
    while i < len(t) and t[i] in " \t\r\n":
        i += 1
    return i


def _string_end(t: str, i: int) -> int:
    """t[i] is an opening quote; return the index just past the closing quote."""
    i += 1
    while i < len(t):
        c = t[i]
        if c == "\\":
            i += 2
            continue
        if c == '"':
            return i + 1
        i += 1
    raise ValueError("unterminated string in JSON text")


def _value_end(t: str, i: int) -> int:
    """t[i] is the first character of a JSON value; return the index just past it."""
    c = t[i]
    if c == '"':
        return _string_end(t, i)
    if c in "{[":
        close = "}" if c == "{" else "]"
        opener = c
        depth = 0
        while i < len(t):
            c = t[i]
            if c == '"':
                i = _string_end(t, i)
                continue
            if c == opener:
                depth += 1
            elif c == close:
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        raise ValueError("unterminated container in JSON text")
    j = i
    while j < len(t) and t[j] not in ",}] \t\r\n":
        j += 1
    return j


def _locate(t: str, i: int, tokens: list[str], pointer: str) -> tuple[int, int]:
    i = _ws(t, i)
    if not tokens:
        return i, _value_end(t, i)
    tok, rest = tokens[0], tokens[1:]
    c = t[i]
    if c == "{":
        i = _ws(t, i + 1)
        while t[i] != "}":
            key_end = _string_end(t, i)
            key = json.loads(t[i:key_end])
            i = _ws(t, key_end)
            if t[i] != ":":
                raise ValueError("expected ':' in object")
            i = _ws(t, i + 1)
            if key == tok:
                return _locate(t, i, rest, pointer)
            i = _ws(t, _value_end(t, i))
            if t[i] == ",":
                i = _ws(t, i + 1)
        raise KeyError(f"pointer not found: {pointer}")
    if c == "[":
        try:
            want = int(tok)
        except ValueError as exc:
            raise KeyError(f"pointer not found: {pointer}") from exc
        i = _ws(t, i + 1)
        n = 0
        while t[i] != "]":
            if n == want:
                return _locate(t, i, rest, pointer)
            i = _ws(t, _value_end(t, i))
            if t[i] == ",":
                i = _ws(t, i + 1)
            n += 1
        raise KeyError(f"pointer not found: {pointer}")
    raise KeyError(f"pointer not found: {pointer}")


def locate_value(text: str, pointer: str) -> tuple[int, int]:
    """The (start, end) byte span of the value at `pointer` in `text`. Raises KeyError when absent."""
    return _locate(text, 0, _pointer_tokens(pointer), pointer)


def splice_value(text: str, pointer: str, new_value: Any) -> str:
    """Replace the value at `pointer` with the JSON encoding of `new_value`, touching nothing else."""
    start, end = locate_value(text, pointer)
    return text[:start] + json.dumps(new_value, ensure_ascii=False) + text[end:]


def top_level_entries(text: str) -> list[dict[str, Any]]:
    """The top-level object's entries in file order: [{key, key_text, value_text}]. Spans come from the
    original text, so a value's own formatting (multi-line objects, spacing) survives re-emission."""
    i = _ws(text, 0)
    if i >= len(text) or text[i] != "{":
        raise ValueError("config root is not a JSON object")
    entries: list[dict[str, Any]] = []
    i = _ws(text, i + 1)
    while text[i] != "}":
        key_start = i
        key_end = _string_end(text, i)
        key = json.loads(text[key_start:key_end])
        i = _ws(text, key_end)
        if text[i] != ":":
            raise ValueError("expected ':' in object")
        v_start = _ws(text, i + 1)
        v_end = _value_end(text, v_start)
        entries.append({"key": key, "key_text": text[key_start:key_end], "value_text": text[v_start:v_end]})
        i = _ws(text, v_end)
        if text[i] == ",":
            i = _ws(text, i + 1)
    return entries


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------


def _target_path(cfg: dict[str, Any], target_id: str) -> Path:
    if target_id == "orchestrator":
        return cfgmod.effective_config_path()
    if target_id == "meic-risk":
        mcfg = (cfg.get("modules") or {}).get("meic")
        if not mcfg:
            raise KeyError("meic is not configured; no config.risk.json target")
        return cfgmod.module_root(mcfg, "meic") / "config.risk.json"
    if target_id in _MODULE_TARGETS:
        return _home.config_path(target_id)
    raise KeyError(f"unknown config target: {target_id}")


def _example_path(cfg: dict[str, Any], target_id: str) -> Path | None:
    rel = _EXAMPLE_REL.get(target_id)
    if rel is None:
        return None
    mcfg = (cfg.get("modules") or {}).get(target_id)
    if mcfg and mcfg.get("path"):
        candidate = cfgmod.module_root(mcfg, target_id) / Path(rel).name
        if target_id == "earnings":
            candidate = cfgmod.module_root(mcfg, target_id) / "config" / Path(rel).name
        if candidate.exists():
            return candidate
    return (cfgmod.ROOT / rel).resolve()


def targets(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Every editable config target, present or not (a missing module config renders as 'not present',
    it is created by that module's own init — never scaffolded from here)."""
    ids = ["orchestrator", *_MODULE_TARGETS]
    if (cfg.get("modules") or {}).get("meic"):
        ids.append("meic-risk")
    out = []
    for tid in ids:
        path = _target_path(cfg, tid)
        out.append(
            {
                "id": tid,
                "title": "suite (orchestrator)" if tid == "orchestrator" else tid,
                "portable": cfgmod.portable_path(path),
                "exists": path.exists(),
                "mtime": path.stat().st_mtime_ns if path.exists() else None,
            }
        )
    return out


def load(cfg: dict[str, Any], target_id: str) -> dict[str, Any]:
    """Read one target: its text, parsed doc, validation issues, guarded pointers, and mtime."""
    path = _target_path(cfg, target_id)
    if not path.exists():
        return {"id": target_id, "exists": False, "portable": cfgmod.portable_path(path)}
    text = path.read_text(encoding="utf-8")
    doc = json.loads(text)
    return {
        "id": target_id,
        "exists": True,
        "portable": cfgmod.portable_path(path),
        "text": text,
        "doc": doc,
        "issues": validate(cfg, target_id, doc),
        "guarded": [{"pointer": ptr, "hint": hint} for ptr, hint in GUARDED.get(target_id, {}).items()],
        "mtime": path.stat().st_mtime_ns,
    }


# ---------------------------------------------------------------------------
# Validation + guards
# ---------------------------------------------------------------------------


def validate(cfg: dict[str, Any], target_id: str, doc: Any) -> list[tuple[str, str]]:
    """Pre-write validation for a parsed document. The orchestrator config gets the full structural
    check (`init.validate_config`); module configs get the root-shape check only — their loaders are
    plain json.loads and their schemas live in the modules, which this package never imports."""
    if not isinstance(doc, dict):
        return [("error", "config root must be a JSON object")]
    if target_id == "orchestrator":
        return initmod.validate_config(doc)
    return []


def _at(doc: Any, tokens: list[str]) -> Any:
    """The value at a pointer path in a parsed doc, or a sentinel when the path is absent."""
    cur = doc
    for tok in tokens:
        if isinstance(cur, dict) and tok in cur:
            cur = cur[tok]
        elif isinstance(cur, list):
            try:
                cur = cur[int(tok)]
            except (ValueError, IndexError):
                return _MISSING
        else:
            return _MISSING
    return cur


_MISSING = object()


def guard_violations(target_id: str, old_doc: Any, new_doc: Any) -> list[str]:
    """Guarded pointers whose value differs between the old and new documents (including appearing or
    disappearing). Non-empty means the save must be refused."""
    return [
        ptr
        for ptr in GUARDED.get(target_id, {})
        if _at(old_doc, _pointer_tokens(ptr)) != _at(new_doc, _pointer_tokens(ptr))
    ]


def coupled_warnings(old_doc: Any, new_doc: Any) -> list[tuple[str, str]]:
    """Warn when any COUPLED key's value changed anywhere in the document — these pairs (paper_db +
    trade_schema, keyring_service + account designation) break orchestrator couplings if changed
    silently (docs/configuration-and-storage.md)."""

    def collect(doc: Any, path: str, out: dict[str, Any]) -> None:
        if isinstance(doc, dict):
            for k, v in doc.items():
                p = f"{path}/{k}"
                if k in COUPLED:
                    out[p] = v
                collect(v, p, out)
        elif isinstance(doc, list):
            for n, v in enumerate(doc):
                collect(v, f"{path}/{n}", out)

    old: dict[str, Any] = {}
    new: dict[str, Any] = {}
    collect(old_doc, "", old)
    collect(new_doc, "", new)
    changed = sorted(p for p in set(old) | set(new) if old.get(p, _MISSING) != new.get(p, _MISSING))
    return [
        (
            "warn",
            f"{p} changed — this key is coupled to the orchestrator's read side "
            "(see docs/configuration-and-storage.md, 'don't change silently')",
        )
        for p in changed
    ]


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------


def backup_dir() -> Path:
    return _home.state_dir() / "config-backups"


def backup_and_write(path: Path, new_text: str, target_id: str) -> dict[str, Any]:
    """Back the current file up (timestamped, under state/config-backups) then write atomically:
    tmp in the same directory + os.replace, the same idiom as the dashboard renderer."""
    bdir = backup_dir()
    bdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = bdir / f"{target_id}.{stamp}.json"
    n = 1
    while backup.exists():
        backup = bdir / f"{target_id}.{stamp}-{n}.json"
        n += 1
    shutil.copy2(path, backup)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    os.replace(tmp, path)
    return {"backup": cfgmod.portable_path(backup), "path": cfgmod.portable_path(path)}


def apply_field_edit(
    cfg: dict[str, Any], target_id: str, pointer: str, value: Any, *, force: bool = False
) -> dict[str, Any]:
    """The field-edit write path: splice one value in place, validate the result, refuse guarded
    pointers, warn on type changes (unless `force`) and coupled keys, then backup + write."""
    if pointer in GUARDED.get(target_id, {}):
        return {"ok": False, "error": f"{pointer} is guarded — {GUARDED[target_id][pointer]}"}
    path = _target_path(cfg, target_id)
    if not path.exists():
        return {"ok": False, "error": f"target '{target_id}' has no config file yet"}
    text = path.read_text(encoding="utf-8")
    old_doc = json.loads(text)
    try:
        old_value = _at(old_doc, _pointer_tokens(pointer))
        new_text = splice_value(text, pointer, value)
    except KeyError as exc:
        return {"ok": False, "error": str(exc)}
    new_doc = json.loads(new_text)

    violations = guard_violations(target_id, old_doc, new_doc)
    if violations:
        return {"ok": False, "error": f"guarded pointer(s) changed: {violations}"}

    issues = validate(cfg, target_id, new_doc)
    if old_value is not _MISSING and old_value is not None and value is not None:
        if type(old_value) is not type(value) and not (
            isinstance(old_value, (int, float)) and isinstance(value, (int, float))
        ):
            issues = issues + [
                ("warn", f"{pointer}: type changes {type(old_value).__name__} -> {type(value).__name__}")
            ]
            if not force:
                return {"ok": False, "issues": issues, "error": "type change — resend with force to apply"}
    issues = issues + coupled_warnings(old_doc, new_doc)
    errors = [msg for lvl, msg in issues if lvl == "error"]
    if errors:
        return {"ok": False, "issues": issues, "error": "; ".join(errors)}

    written = backup_and_write(path, new_text, target_id)
    return {"ok": True, "issues": issues, **written, "mtime": path.stat().st_mtime_ns}


def apply_raw_save(
    cfg: dict[str, Any], target_id: str, new_text: str, expected_mtime: int | None = None
) -> dict[str, Any]:
    """The raw-save write path: write the client's text verbatim after it parses, validates, changes
    no guarded pointer, and the file has not changed on disk since the client loaded it."""
    path = _target_path(cfg, target_id)
    if not path.exists():
        return {"ok": False, "error": f"target '{target_id}' has no config file yet"}
    if expected_mtime is not None and path.stat().st_mtime_ns != expected_mtime:
        return {"ok": False, "error": "changed on disk — reload before saving"}
    try:
        new_doc = json.loads(new_text)
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"not valid JSON: {exc}"}
    old_doc = json.loads(path.read_text(encoding="utf-8"))

    violations = guard_violations(target_id, old_doc, new_doc)
    if violations:
        return {"ok": False, "error": f"guarded pointer(s) changed: {violations}"}

    issues = validate(cfg, target_id, new_doc) + coupled_warnings(old_doc, new_doc)
    errors = [msg for lvl, msg in issues if lvl == "error"]
    if errors:
        return {"ok": False, "issues": issues, "error": "; ".join(errors)}

    if new_text == path.read_text(encoding="utf-8"):
        return {"ok": True, "issues": issues, "unchanged": True, "mtime": path.stat().st_mtime_ns}
    written = backup_and_write(path, new_text, target_id)
    return {"ok": True, "issues": issues, **written, "mtime": path.stat().st_mtime_ns}


# ---------------------------------------------------------------------------
# Organize: section order from the example file
# ---------------------------------------------------------------------------


def organize_text(live_text: str, example_text: str) -> str:
    """Rebuild a live config's top level in its example's key order, inserting the example's
    `*_header` section markers. Every live value is re-emitted from its original byte span, so no
    value or note changes; live-only keys (absent from the example) are appended at the end in their
    original order. Nested objects are never reordered. Idempotent."""
    live = top_level_entries(live_text)
    example = top_level_entries(example_text)
    live_by_key = {e["key"]: e for e in live}
    used: set[str] = set()
    parts: list[str] = []
    for ex in example:
        key = ex["key"]
        if key in live_by_key:
            entry = live_by_key[key]
            parts.append(f"  {entry['key_text']}: {entry['value_text']}")
            used.add(key)
        elif key.endswith("_header"):
            # Only headers are imported from the example — anything else absent from the live file
            # stays absent (adding config keys would change what the module reads).
            parts.append(f"  {ex['key_text']}: {ex['value_text']}")
    for entry in live:
        if entry["key"] not in used:
            parts.append(f"  {entry['key_text']}: {entry['value_text']}")
    return "{\n" + ",\n".join(parts) + "\n}\n"


def organize(cfg: dict[str, Any], target_id: str, *, apply: bool = False) -> dict[str, Any]:
    """Reorder a live config into its example's section order (dry-run by default). With `apply`,
    the result goes through the same guard/validate/backup/atomic-write path as a raw save."""
    path = _target_path(cfg, target_id)
    if not path.exists():
        return {"ok": False, "error": f"target '{target_id}' has no config file yet"}
    example = _example_path(cfg, target_id)
    if example is None or not example.exists():
        return {"ok": False, "error": f"target '{target_id}' has no example file to organize against"}
    live_text = path.read_text(encoding="utf-8")
    new_text = organize_text(live_text, example.read_text(encoding="utf-8"))
    old_doc, new_doc = json.loads(live_text), json.loads(new_text)
    added = set(new_doc) - set(old_doc)
    kept = {k: v for k, v in new_doc.items() if k in old_doc}
    if kept != old_doc or any(not k.endswith("_header") for k in added):
        return {"ok": False, "error": "organize would change the parsed document — refusing"}
    if not apply:
        return {"ok": True, "changed": new_text != live_text, "text": new_text}
    if new_text == live_text:
        return {"ok": True, "changed": False, "mtime": path.stat().st_mtime_ns}
    result = apply_raw_save(cfg, target_id, new_text)
    result["changed"] = True
    return result
