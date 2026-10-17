"""The advisor's backtest specs, run here (`run flies --advised`).

The advisor queues specs it admitted against flies' own advice bounds (`python -m cherrypick.advisor
backtest-requests`); this reads that queue by subprocess (no package imports another), turns each
spec into a config-defined arm `advised:<name>` (the base arm's params with the overlay on top, as
flies' paper loop builds one), runs them all over the DESIGN window in one run, and writes each spec's
result back (`backtest-result`). The holdout is never touched here: a spec that earns it is spent by a
person, through `run --window holdout --spend-holdout`.

Every spec is a trial (`results.trials`), counted the same as a human's.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from . import runner


def _advisor(*args: str) -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "cherrypick.advisor", *args], capture_output=True, text=True, timeout=600
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": (proc.stderr or proc.stdout)[-400:]}


def arm_name(request: dict) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(request["name"]).lower()).strip("-") or "spec"
    return f"advised:{slug}-{request['id']}"


def extra_arms(config: dict, requests: list[dict]) -> dict[str, dict]:
    arms = config.get("arms") or {}
    out = {}
    for r in requests:
        base = arms.get(r["base_arm"])
        if not isinstance(base, dict):
            continue
        out[arm_name(r)] = {**base, **r["params"], "enabled": True}
    return out


def run(settings: dict, *, cadence: int | None = None, advisor=_advisor, log=print, **run_kw) -> dict:
    reply = advisor("backtest-requests", "--status", "queued")
    if not reply.get("ok"):
        raise runner.RunRefused(f"the advisor's queue could not be read: {reply.get('error')}")
    requests = [r for r in reply.get("requests") or [] if r.get("module") == runner.MODULE]
    if not requests:
        return {"ok": True, "requests": 0}
    text, _ = runner._flies_config_text()
    arms = extra_arms(json.loads(text), requests)
    report = runner.run(settings, [], "design", cadence=cadence, extra_arms=arms, log=log, **run_kw)
    written = []
    for r in requests:
        name = arm_name(r)
        entry = report["arms"].get(name)
        if entry:
            outcome = {
                "summary": entry["summary"],
                "vs_control": entry.get("vs_control"),
                "no_exposure": entry.get("no_exposure"),
            }
        else:
            outcome = {"error": "base arm not in the flies config"}
        result = {
            "window": "design",
            "run_id": report["run_id"],
            "trials_for_module": report["trials_for_module"],
            **outcome,
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
            json.dump(result, fh)
        status = "done" if entry else "refused"
        advisor(
            "backtest-result", "--id", str(r["id"]), "--status", status, "--run-id", report["run_id"],
            "--result-file", fh.name,
        )  # fmt: skip
        Path(fh.name).unlink(missing_ok=True)
        written.append({"id": r["id"], "arm": name, "status": status})
    return {"ok": True, "requests": len(requests), "run_id": report["run_id"], "written": written}
