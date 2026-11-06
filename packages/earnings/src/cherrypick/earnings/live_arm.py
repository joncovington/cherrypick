"""The per-day arm for live earnings orders (2026-11-06).

Earnings has no live loop: a live order is placed by hand through ``tt.py execute_trade --live``.
Every other live module runs under a per-day arm a human writes with a literal YES, and from
2026-11-06 so does this one. An order that OPENS anything is refused unless today's arm record is
present and before its disarm time; a closing order is never held back.

    python -m cherrypick.earnings.live_arm status
    python -m cherrypick.earnings.live_arm arm --confirm YES
    python -m cherrypick.earnings.live_arm disarm

The record is the suite's own (``cherrypick.core.live``): ``state/earnings-live-arm.json``, dated
the ET session it arms. The orchestrator derives no job from it (earnings is not a live module in
its config); its watchdog lists earnings as armed while the record is valid, which only holds a
window-only streamer recycle. Arming refuses while ``enable_live_trading`` is off, since the record
would arm nothing. Nothing here places, sizes or reads an order.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from cherrypick.core import live as _live

MODULE = "earnings"
ET = ZoneInfo("America/New_York")
DISARM_TIME = "17:00"
CONFIRMATION = "YES"


def _now(now: datetime | None) -> datetime:
    return (now or datetime.now(ET)).astimezone(ET)


def _minutes(hhmm: str) -> int:
    h, m = (int(x) for x in hhmm.split(":"))
    return h * 60 + m


def refusal(now: datetime | None = None) -> str | None:
    """Why an opening live order must be refused right now, or None when today's arm is valid."""
    t = _now(now)
    return _live.should_disarm(
        _live.arm_record_date(MODULE),
        today=t.date().isoformat(),
        now_min=t.hour * 60 + t.minute,
        disarm_min=_minutes(DISARM_TIME),
        disarm_label=f"{DISARM_TIME} ET",
    )


def status(now: datetime | None = None) -> dict:
    why = refusal(now)
    return {
        "module": MODULE,
        "armed": why is None,
        "armed_for": _live.arm_record_date(MODULE),
        "disarm_time": f"{DISARM_TIME} ET",
        "why_not": why,
        "record": str(_live.arm_record_path(MODULE)),
    }


def arm(confirm: str | None, *, live_enabled: bool, now: datetime | None = None) -> dict:
    """Write today's arm record, only on a literal YES and with the live switch on."""
    if confirm != CONFIRMATION:
        return {"ok": False, "error": f"arming needs --confirm {CONFIRMATION} (literal); nothing written"}
    if not live_enabled:
        return {
            "ok": False,
            "error": "enable_live_trading is off in the earnings config; an arm would arm nothing",
        }
    t = _now(now)
    if t.hour * 60 + t.minute >= _minutes(DISARM_TIME):
        return {"ok": False, "error": f"past the {DISARM_TIME} ET disarm time; arm on the session you trade"}
    path = _live.write_arm_record(
        MODULE,
        date=t.date().isoformat(),
        armed_by="cherrypick.earnings.live_arm",
        at=t.isoformat(timespec="seconds"),
    )
    out = {
        "ok": True,
        "armed_for": t.date().isoformat(),
        "disarm_time": f"{DISARM_TIME} ET",
        "record": str(path),
    }
    warning = _live.quarter_end_warning(t.date().isoformat())
    if warning is not None:
        out["warning"] = warning
    return out


def disarm() -> dict:
    return _live.disarm(MODULE)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    a = sub.add_parser("arm")
    a.add_argument("--confirm", help=f"must be the literal {CONFIRMATION}")
    sub.add_parser("disarm")
    args = ap.parse_args(argv)
    if args.cmd == "status":
        out = status()
    elif args.cmd == "arm":
        from cherrypick.earnings import tt

        out = arm(args.confirm, live_enabled=tt._live_trading_enabled())
    else:
        out = disarm()
    print(json.dumps(out, indent=2))
    return 0 if out.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
