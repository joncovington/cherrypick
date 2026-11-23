"""A paper loop's tick cadence, journaled as a measurement break when it changes.

The mark path's resolution decides how finely a trigger or a read-side exit derivation can be
replayed, so numbers measured before and after a cadence change are not comparable. The change is
journaled once, keyed off a small state file in the module's paper data directory, and never allowed
to break the loop: any failure here is logged and swallowed.

The module supplies what is its own: the ledger's `record_measurement_break`, the break's note, and
its log. The state file and the row's shape are the same everywhere.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

from cherrypick.core import clock

STATE_FILENAME = "tick_cadence.json"
BREAK_KEY = "tick_cadence"


def note_change(
    conn: Any,
    interval_seconds: int,
    *,
    data_dir: str,
    record_break: Callable[..., Any],
    note: str,
    log: Callable[[str], Any],
) -> None:
    """Journal a tick-cadence change as a measurement break, exactly once per change.

    `record_break` is the module ledger's `record_measurement_break`, called with the keywords
    `break_date`, `key`, `old_value`, `new_value` and `note`. A first run (no state file) writes the
    state and no row; an unchanged cadence writes nothing.
    """
    try:
        state_path = os.path.join(data_dir, STATE_FILENAME)
        prev = None
        try:
            with open(state_path, encoding="utf-8") as fh:
                prev = int(json.load(fh).get("seconds"))
        except (OSError, ValueError, TypeError):
            pass
        if prev == interval_seconds:
            return
        day = clock.today_iso()
        if prev is not None:
            record_break(
                conn,
                break_date=day,
                key=BREAK_KEY,
                old_value=str(prev),
                new_value=str(interval_seconds),
                note=note,
            )
            log(f"tick cadence changed {prev}s -> {interval_seconds}s — journaled as a measurement break")
        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump({"seconds": interval_seconds, "since": day}, fh)
    except Exception as exc:  # noqa: BLE001 — never let telemetry break the loop
        log(f"cadence-change journaling failed (non-fatal): {type(exc).__name__}: {exc}")
