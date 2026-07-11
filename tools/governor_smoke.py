#!/usr/bin/env python3
"""One-shot LIVE deploy-limit governor smoke (dry-run only; never submits).

Runs unattended via a scheduled task (`Cherrypick-GovernorSmoke`). Each fire it gates on the ET
trading session; outside a live session it exits immediately (cheap no-op through weekends/holidays).
During a live session with a fresh 0DTE candidate it:
  1. fetches a real XSP iron-condor candidate from the module's `tt.py get_strategies`,
  2. temporarily sets `account_deploy_limit_pct` restrictively in the module's config.json,
  3. runs `tt.py execute_trade` **without --live** (a dry run — validation only, no order submitted),
  4. reads the `governor` verdict the core computed against the *real* account, and
  5. ALWAYS restores config.json (try/finally), notifies the result, and on a conclusive pass
     self-deletes its scheduled task + writes a done-marker so it never runs again.

Safety: this script only ever calls `execute_trade` in dry-run mode (no `--live` flag is ever passed);
the core places a live order on exactly one path (live=True + clean preflight + allowing governor),
which this never reaches. It touches the broker only for reads + a dry-run preflight.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cherrypick.notify import Notifier  # noqa: E402
from cherrypick.orchestrator import config as cfgmod  # noqa: E402
from cherrypick.orchestrator import timeutil  # noqa: E402

TASK_NAME = "Cherrypick-GovernorSmoke"
MODULE = "meic"
SYMBOL = "XSP"
TEST_LIMIT_PCT = 1  # capacity * 1% — any real 1-lot spread's BP effect far exceeds this → would block
_LOG = cfgmod.LOGS_DIR / "governor_smoke.log"
_DONE = cfgmod.STATE_DIR / "governor_smoke.done.json"

_LEG_ACTIONS = {
    "short_put": "sell to open",
    "long_put": "buy to open",
    "short_call": "sell to open",
    "long_call": "buy to open",
}


def _log(rec: dict) -> None:
    _LOG.parent.mkdir(parents=True, exist_ok=True)
    with _LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), **rec}) + "\n")


def _run_tt(root: Path, argv: list[str], timeout: int) -> dict:
    r = subprocess.run(
        [cfgmod.python_exe(), *argv], cwd=str(root), capture_output=True, text=True, timeout=timeout
    )
    try:
        return json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "error": (r.stdout or r.stderr or "")[:400]}


def _delete_task() -> None:
    subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], capture_output=True, text=True)


def _build_order(candidate: dict) -> dict | None:
    legs_in = candidate.get("legs")
    if not isinstance(legs_in, dict):
        return None
    legs = []
    for key, action in _LEG_ACTIONS.items():
        leg = legs_in.get(key)
        if not leg or not leg.get("symbol"):
            return None
        legs.append(
            {
                "instrument_type": leg.get("instrument_type", "Equity Option"),
                "symbol": leg["symbol"],
                "action": action,
                "quantity": 1,
            }
        )
    spec: dict = {"time_in_force": "Day", "order_type": "Limit", "legs": legs}
    credit = candidate.get("net_credit") or candidate.get("net_credit_per_contract")
    if credit is not None:
        spec["price"] = abs(float(credit))
        spec["price_effect"] = "credit"
    return spec


def main() -> None:
    cfg = cfgmod.load_config()
    if _DONE.exists():
        _delete_task()
        return

    tz = cfg.get("timezone", "America/New_York")
    holidays = timeutil.load_holidays(cfg, cfgmod.module_root)
    now = timeutil.now_et(tz)
    # Gate: only during a live trading session. Cheap no-op on weekends/holidays/off-hours.
    if not (timeutil.is_trading_day(now, holidays) and timeutil.is_session_window(now, holidays)):
        return

    mcfg = cfg.get("modules", {}).get(MODULE)
    if not mcfg or not mcfg.get("enabled"):
        return
    root = cfgmod.module_root(mcfg, MODULE)
    notifier = Notifier(cfg.get("notify"))

    # 1. fresh 0DTE candidate
    candidate = _run_tt(root, ["src/tt.py", "get_strategies", "--symbol", SYMBOL], timeout=90)
    if not candidate.get("ok") or candidate.get("dte") != 0:
        _log({"stage": "candidate", "ok": False, "dte": candidate.get("dte"), "note": "no valid 0DTE yet"})
        return  # not conclusive — leave the task to retry next session
    order = _build_order(candidate)
    if not order:
        _log({"stage": "build_order", "ok": False, "note": "could not assemble 4-leg order"})
        return

    # 2/3/5. set the governor, dry-run, ALWAYS restore
    cfg_path = root / "config.json"
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    _MISSING = object()
    prior = raw.get("account_deploy_limit_pct", _MISSING)
    result: dict = {}
    try:
        raw["account_deploy_limit_pct"] = TEST_LIMIT_PCT
        cfg_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        # NOTE: no --live — dry run only. The core computes the governor verdict without submitting.
        result = _run_tt(
            root, ["src/tt.py", "execute_trade", "--order", json.dumps(order)], timeout=90
        )
    finally:
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
        if prior is _MISSING:
            raw.pop("account_deploy_limit_pct", None)
        else:
            raw["account_deploy_limit_pct"] = prior
        cfg_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")

    gov = result.get("governor") or {}
    enforced = gov.get("deploy_governor") == "enforced"
    # Would the governor have blocked a LIVE submit? projected deployment must exceed the cap.
    try:
        would_block = float(gov.get("account_deployed_after", 0)) > float(gov.get("account_deploy_limit", 0))
    except (TypeError, ValueError):
        would_block = False
    passed = bool(result.get("dry_run")) and enforced and would_block

    rec = {
        "stage": "smoke",
        "passed": passed,
        "dry_run": result.get("dry_run"),
        "governor": gov,  # capacity/limit/deployed_after only — no account number
        "error": result.get("error"),
    }
    _log(rec)

    if passed:
        _DONE.write_text(json.dumps(rec, indent=2), encoding="utf-8")
        cap = gov.get("account_buying_power_capacity")
        lim = gov.get("account_deploy_limit")
        dep = gov.get("account_deployed_after")
        notifier.notify(
            "INFO",
            "governor_smoke",
            "Deploy-limit governor smoke PASSED",
            f"Live account (dry-run): governor enforced. capacity={cap}, limit@{TEST_LIMIT_PCT}%={lim}, "
            f"projected_deploy={dep} > limit → a live submit would be blocked. No order submitted.",
        )
        _delete_task()
    else:
        notifier.notify(
            "WARN",
            "governor_smoke",
            "Deploy-limit governor smoke inconclusive",
            f"dry_run={result.get('dry_run')} enforced={enforced} would_block={would_block} "
            f"error={result.get('error')}. Config restored; task left to retry.",
        )


if __name__ == "__main__":
    main()
