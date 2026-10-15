"""MEIC's order-alert daemon (2026-10-15): `cherrypick.core.live.alerts` pointed at MEIC's live ledger.

Kept up by the supervisor as an arm-record companion while MEIC is armed (`live.use_order_alert_daemon`),
it records every account order alert in `live_alerts.db` beside `meic_trades.db` -- which the
orchestrator's `live-fill-alerts` job reads to push a LIVE fill alert within seconds -- and, when an
order MEIC's own ledger knows stops working (filled, cancelled, rejected, expired), it starts one
`live_loop --once --live` tick so the fill is CONFIRMED now instead of at the next minute. The tick
still asks the broker; this only asks it sooner. The tick lock keeps a spawned tick from overlapping a
scheduled one, and spawns are spaced at least `SPAWN_SPACING_SECONDS` apart.
"""

from __future__ import annotations

import os
import time

from cherrypick.core import execution as _execution
from cherrypick.core.live import alerts as _alerts

from cherrypick.meic import paths as _paths

SPAWN_SPACING_SECONDS = 10
_last_spawn = {"at": None}


def daemon() -> _alerts.AlertDaemon:
    return _alerts.AlertDaemon(
        "meic", os.path.dirname(str(_paths.live_db_path())), str(_paths.log_path("meic_alert_daemon.log"))
    )


def _deadline_seconds(config: dict) -> float | None:
    from cherrypick.meic import live_loop

    now = live_loop._pl._now_et()
    return max(0.0, (live_loop.disarm_min(config) - live_loop._now_min(now)) * 60.0 - now.second)


def on_alert(alert: dict, *, known=None, spawn=None, clock_fn=time.monotonic) -> bool:
    """Start one confirming tick when an order MEIC's ledger records stops working. Returns whether it
    spawned. `known`/`spawn` are injectable for tests."""
    from cherrypick.meic import live_loop

    state, _ = _execution.fill_state(alert)
    if state == "working":
        return False
    ids = known() if known else live_loop._known_order_ids(str(_paths.live_db_path()))
    if str(alert.get("order_id")) not in {str(i) for i in ids}:
        return False  # another loop's order, or manual trading: not MEIC's to confirm
    now = clock_fn()
    if _last_spawn["at"] is not None and now - _last_spawn["at"] < SPAWN_SPACING_SECONDS:
        return False
    _last_spawn["at"] = now
    (spawn or live_loop._spawn_first_tick)()
    return True


def main(argv=None) -> int:
    from cherrypick.meic import live_loop, paper

    config = paper.load_base_config()
    return daemon().main(
        make_broker=lambda: live_loop.make_broker(config, live_loop._designated_account()),
        deadline_seconds=lambda: _deadline_seconds(config),
        on_alert=on_alert,
        argv=argv,
    )


if __name__ == "__main__":
    raise SystemExit(main())
