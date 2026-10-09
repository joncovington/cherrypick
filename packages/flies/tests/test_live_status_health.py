"""flies' live `--status` carries the seam's broker-contact record (2026-10-08): the tick exits 0
through a broker outage, so this is the watchdog's only view of one."""

from __future__ import annotations

import json
import os

from cherrypick.core import execution as _execution

from cherrypick.flies import db as dbmod
from cherrypick.flies import live_loop as ll


def test_status_carries_the_broker_contact_record(tmp_path):
    path = os.path.join(os.path.dirname(ll._held_path()), _execution.HEALTH_FILENAME)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"failing_since": "2026-10-08T14:00:00+00:00", "failures": 5}, f)
    conn = dbmod.connect(str(tmp_path / "live.db"))
    assert ll.run_status({}, conn)["broker_health"]["failures"] == 5
