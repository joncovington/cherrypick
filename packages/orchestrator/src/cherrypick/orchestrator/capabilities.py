"""`run.py capabilities` — probe this machine for what optional features need, and record it.

The suite has two optional dependencies, and each gates features (config.CAPABILITIES):

- **claude** — the Claude Code CLI. The advisor and the two narratives shell out to it. Present when
  `claude --version` runs.
- **dolt** — the Dolt server with the earnings/options/stocks clones. The earnings module and the
  technicals engines read it. Present when the `dolt` binary runs AND all three clones exist in the
  data directory the supervised server serves from (`modules.earnings.paper.dolt_service.data_dir`,
  default `~/.cherrypick/data/earnings`). A binary with no clones is not a usable data source, and a
  feature that needs one would fail every night.

`--detect` reports; `--detect --write` records the answers in `config.json`'s `capabilities` block;
`--cap claude=false` records one by hand (a person may know better than the probe — e.g. Claude is
installed but not to be used). Detection never switches a feature ON by itself: each feature still has
its own switch. It only says whether the machine can carry it.

The write goes through configedit's splice + backup, so the rest of the file — notes, key order — is
untouched and the previous version is kept in state/config-backups/.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import config as cfgmod
from . import configedit
from .util import CREATE_NO_WINDOW

DOLT_DATABASES = ("earnings", "options", "stocks")


def _run_version(argv: list[str]) -> str | None:
    """The first line `argv` prints, or None when it cannot run or exits non-zero."""
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=30, creationflags=CREATE_NO_WINDOW
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    lines = (proc.stdout or proc.stderr or "").strip().splitlines()
    return lines[0] if lines else ""


def dolt_data_dir(cfg: dict[str, Any]) -> Path:
    paper = ((cfg.get("modules") or {}).get("earnings") or {}).get("paper") or {}
    raw = (paper.get("dolt_service") or {}).get("data_dir") or "~/.cherrypick/data/earnings"
    # The dolt_service rule (see config.paper_db_path): ~ and env vars expand; relative is ROOT's.
    p = Path(os.path.expandvars(os.path.expanduser(str(raw))))
    return p if p.is_absolute() else (cfgmod.ROOT / p).resolve()


def detect(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """{capability: {"present": bool, "detail": str}} — what this machine has right now."""
    out: dict[str, dict[str, Any]] = {}

    exe = shutil.which("claude")
    version = _run_version([exe, "--version"]) if exe else None
    out["claude"] = {
        "present": version is not None,
        "detail": f"{version} ({cfgmod.portable_path(exe)})"
        if version is not None
        else "claude not found on PATH",
    }

    exe = shutil.which("dolt")
    version = _run_version([exe, "version"]) if exe else None
    data_dir = dolt_data_dir(cfg)
    missing = [db for db in DOLT_DATABASES if not (data_dir / db / ".dolt").is_dir()]
    if version is None:
        detail = "dolt not found on PATH"
    elif missing:
        detail = f"{version}, but no clone of {', '.join(missing)} in {cfgmod.portable_path(data_dir)}"
    else:
        detail = f"{version}, clones in {cfgmod.portable_path(data_dir)}"
    out["dolt"] = {"present": version is not None and not missing, "detail": detail}
    return out


def write(values: dict[str, bool], path: Path | None = None) -> dict[str, Any]:
    """Record `values` in the suite config's `capabilities` block, keeping any capability not named.
    Creates the block (after the opening brace) when the file has none."""
    path = path or cfgmod.effective_config_path()
    text = path.read_text(encoding="utf-8")
    doc = json.loads(text)
    current = doc.get("capabilities") if isinstance(doc.get("capabilities"), dict) else {}
    merged = {**{k: v for k, v in current.items() if k in cfgmod.CAPABILITIES}, **values}
    merged = {k: merged[k] for k in cfgmod.CAPABILITIES if k in merged}
    if "capabilities" in doc:
        new_text = configedit.splice_value(text, "/capabilities", merged)
    else:
        brace = text.index("{")
        nl = "\r\n" if "\r\n" in text else "\n"
        new_text = text[: brace + 1] + nl + f'  "capabilities": {json.dumps(merged)},' + text[brace + 1 :]
    json.loads(new_text)  # never write a file that does not parse
    result = configedit.backup_and_write(path, new_text, "orchestrator")
    return {"ok": True, "capabilities": merged, **({"backup": result.get("backup")} if result else {})}


def parse_set(pairs: list[str]) -> dict[str, bool]:
    """`claude=true dolt=false` -> {"claude": True, "dolt": False}. Unknown names and values refuse."""
    out: dict[str, bool] = {}
    for pair in pairs:
        name, _, raw = pair.partition("=")
        name, raw = name.strip(), raw.strip().lower()
        if name not in cfgmod.CAPABILITIES:
            raise ValueError(f"unknown capability {name!r} (known: {', '.join(cfgmod.CAPABILITIES)})")
        if raw not in ("true", "false"):
            raise ValueError(f"{name}: value must be true or false, not {raw!r}")
        out[name] = raw == "true"
    return out


def gated_features(cfg: dict[str, Any]) -> dict[str, Any]:
    """The resolved view a surface needs: each module's switch and effective state, each
    capability, and the capability-gated features — the one answer the console asks for (via
    configcli `features`) so it never re-derives the rule."""
    caps = cfgmod.capabilities(cfg)
    review = cfgmod.review_settings(cfg)
    morning = cfgmod.morning_settings(cfg)
    services = {s.get("id"): bool(s.get("enabled")) for s in (cfg.get("services") or []) if s.get("id")}
    return {
        "ok": True,
        "capabilities": caps,
        "modules": cfgmod.module_states(cfg),
        "services": services,
        "features": {
            "advisor": bool(cfgmod.advisor_settings(cfg)["enabled"]),
            "review_narrative": bool(review["enabled"] and review["narrative"]),
            "morning_narrative": bool(morning["enabled"] and morning["narrative"]),
            "technicals": bool(cfgmod.technicals_settings(cfg)["enabled"])
            and "earnings" in cfgmod.enabled_modules(cfg),
        },
    }
