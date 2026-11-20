"""The ladder's live loop commands (issue #1337): arm, disarm, status and one tick.

The order path exists and ships in dry-run: `live_tick` holds the broker adapter, the reconcile and the
tick, and is the only ladder module that imports anything that trades. This file imports nothing that
does (a source scan in the tests holds that), so arming, disarming and reading the status never reach a
broker.

    --arm --confirm YES   print DISCLAIMER.md; write today's arm record on exactly YES, only while
                          live.enabled, live.gate0_confirmed and modules.ladder.enabled hold
    --install-task        the same arm without the prompt (/live-ladder-start calls it after its YES)
    --disarm              remove the arm record
    --status              one JSON object: every gate, the masked account, armed today, dry-run or
                          live, anything held after an uncertain submit; no broker call
    --once                one tick (`live_tick.run_once`): gates, orphan sweep, reconcile, then plan
                          and apply while armed. Sends nothing while `live.dry_run` is true

The arm is per day. The record is good for its date only, and `live.disarm_at` (ET) ends the day: the
tick removes a record dated another day before it reads anything, and at `disarm_at` it cancels the
resting buys while the arm is still valid, then disarms itself.
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
DRY_RUN_NOTE = (
    "the order path ships in dry-run: the tick reads the broker and journals the orders it would send, "
    "and sends none until live.dry_run is set to false by hand"
)
LIVE_NOTE = "live.dry_run is false: with every gate held, the tick places and cancels real orders"

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
    from cherrypick.ladder import live_tick

    dry = live_tick.dry_run(config)
    return {
        "ok": True,
        "module": MODULE,
        "order_path": True,
        "dry_run": dry,
        "mode": "dry-run" if dry else "live",
        "note": DRY_RUN_NOTE if dry else LIVE_NOTE,
        "held": live_tick.read_held(),
        "checks": live_tick.read_checks(),
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
        from cherrypick.ladder import live_tick

        out["order_path"] = True
        out["dry_run"] = live_tick.dry_run(config)
        out["note"] = DRY_RUN_NOTE if out["dry_run"] else LIVE_NOTE
        out.pop("cadence", None)  # there is no ladder-live supervisor job to tick yet
    return out


def uninstall_task() -> dict:
    return _live.disarm(MODULE)


def run_once(config: dict, **kwargs) -> dict:
    """One tick: `live_tick.run_once`. The broker adapter is built there, after the gates pass."""
    from cherrypick.ladder import live_tick

    return live_tick.run_once(config, **kwargs)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="cherrypick-ladder live loop: arm, status and one tick")
    ap.add_argument("--config")
    ap.add_argument("--once", action="store_true", help="one tick; sends nothing while live.dry_run is true")
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
