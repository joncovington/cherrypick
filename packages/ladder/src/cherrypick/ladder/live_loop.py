"""The ladder's live loop, gates and arm only. There is no order path yet (issue #1337).

What exists: the readiness gates (`live_gates`), the per-day arm record (`cherrypick.core.live`) and
a status readout. What does not: any order, any broker call that places, cancels or changes
anything, the `live.task_name` that would make the supervisor run this as a job, and a live ledger.
A source scan in the tests holds that: this file and `live_gates` import nothing that trades.

    --arm --confirm YES   print DISCLAIMER.md; write today's arm record on exactly YES, only while
                          live.enabled, live.gate0_confirmed and modules.ladder.enabled hold
    --install-task        the same arm without the prompt (/live-ladder-start calls it after its YES)
    --disarm              remove the arm record
    --status              one JSON readiness object: every gate, the masked account, armed today
    --once                evaluate readiness and log that there is no order path yet

The arm is per day. The record is good for its date only, and `live.disarm_at` (ET) ends the day: a
future tick calls `cherrypick.core.live.should_disarm` first and disarms itself on a stale date or
past that time. Today `--once` reports the same verdict through the `arm.record` gate.
"""

from __future__ import annotations

import argparse
import json
import logging
import os

from cherrypick.core import home as _home
from cherrypick.core import live as _live
from cherrypick.core import logs as _logs
from cherrypick.core import redact as _redact
from cherrypick.core.clock import minute_of_day, now_et, now_iso

from cherrypick.ladder import cli as climod
from cherrypick.ladder import live_gates, paper_loop, stream_request

MODULE = "ladder"
ARMED_BY = "live-ladder-start"
NO_ORDER_PATH = "the ladder has no order path yet: arming records the arm and nothing else"

_logger = logging.getLogger("ladder_live_loop")


def log_file():
    return _home.logs_dir("ladder") / "ladder_live.log"


def _log(message: str) -> None:
    _logs.configure(_logger, log_file())
    _logger.info(_redact.redact_accounts(message))


def halt_flag_path() -> str:
    return str(_home.halt_flag_path())


def arm_record_path() -> str:
    return str(_live.arm_record_path(MODULE))


def arm_stamp_date() -> str | None:
    return _live.arm_record_date(MODULE)


def _designated() -> str | None:
    from cherrypick.ladder import credentials

    return credentials.designated_account()


def gates_now(config: dict, *, designated: str | None = None, when=None) -> list[live_gates.Gate]:
    """Every gate against the machine's state right now. Reads files and the keyring, never a broker."""
    when = when or now_et()
    return live_gates.evaluate(
        config,
        module_enabled=_live.suite_module_enabled(MODULE),
        armed_for=arm_stamp_date(),
        today=when.date().isoformat(),
        now_min=minute_of_day(when),
        designated=designated if designated is not None else _designated(),
        halted=os.path.exists(halt_flag_path()),
        arm_names=list(paper_loop.arms(config)),
        fund_names=list(stream_request.names(config)),
    )


def status(config: dict, *, designated: str | None = None, when=None) -> dict:
    """One JSON readiness object. No broker call; the account is shown masked."""
    when = when or now_et()
    account = designated if designated is not None else _designated()
    gates = gates_now(config, designated=account, when=when)
    today = when.date().isoformat()
    return {
        "ok": True,
        "module": MODULE,
        "order_path": False,
        "note": NO_ORDER_PATH,
        "today": today,
        "armed_today": arm_stamp_date() == today,
        "armed_for": arm_stamp_date(),
        "account": _redact.mask_account(account) if account else None,
        "halt_flag": os.path.exists(halt_flag_path()),
        "ready": live_gates.ready(gates),
        "new_buys_allowed": live_gates.new_buys_allowed(gates),
        "unmet_gates": [g.name for g in live_gates.unmet(gates)],
        "gates": [g.as_dict() for g in gates],
    }


def install_task(config: dict | None = None) -> dict:
    """Arm for today: the arm record and nothing else. Refuses, writing nothing, unless the three
    config gates hold. Under a live supervisor only (`cherrypick.core.live.arm`)."""
    config = config if config is not None else climod.load_config()
    unmet = live_gates.config_unmet(config, module_enabled=_live.suite_module_enabled(MODULE))
    if unmet:
        return {
            "ok": False,
            "error": "config gates unmet; nothing was armed: "
            + "; ".join(f"{g.name}: {g.reason}" for g in unmet),
            "unmet_gates": [g.name for g in unmet],
        }
    out = _live.arm(MODULE, date=now_et().date().isoformat(), at=now_iso(), armed_by=ARMED_BY)
    if out.get("ok"):
        out["order_path"] = False
        out["note"] = NO_ORDER_PATH
        out.pop("cadence", None)  # there is no ladder-live job to tick
    return out


def uninstall_task() -> dict:
    return _live.disarm(MODULE)


def run_once(config: dict, *, designated: str | None = None, when=None) -> dict:
    """Evaluate readiness and say there is no order path. Places nothing, cancels nothing."""
    report = status(config, designated=designated, when=when)
    _log(
        f"tick: ready={report['ready']} armed_today={report['armed_today']} "
        f"unmet={report['unmet_gates']}; no order path yet"
    )
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="cherrypick-ladder live gates and arm (no order path yet)")
    ap.add_argument("--config")
    ap.add_argument("--once", action="store_true", help="evaluate readiness; places nothing")
    ap.add_argument("--status", action="store_true", help="one JSON readiness object")
    ap.add_argument(
        "--arm", action="store_true", help="arm for today: prints DISCLAIMER.md, needs --confirm YES"
    )
    ap.add_argument("--confirm", help="with --arm: the word YES, exactly")
    ap.add_argument(
        "--install-task",
        action="store_true",
        help="arm for today, the agent path (/live-ladder-start calls it after its own YES)",
    )
    ap.add_argument("--disarm", "--uninstall-task", dest="disarm", action="store_true", help="disarm now")
    args = ap.parse_args(argv)

    def out(payload) -> int:
        print(json.dumps(payload, indent=2, default=str))
        return 0 if payload.get("ok", True) else 1

    if args.arm or args.install_task:
        config = climod.load_config(args.config)
        return out(
            _live.arm_from_cli(
                MODULE,
                lambda: install_task(config),
                confirm=args.confirm,
                prompted=args.arm,
                require_enabled=True,
            )
        )
    if args.disarm:
        return out(uninstall_task())
    config = climod.load_config(args.config)
    if args.status:
        return out(status(config))
    if args.once:
        return out(run_once(config))
    ap.error("choose one of --once, --status, --arm, --install-task, --disarm")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
