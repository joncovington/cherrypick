"""What curve asks the shared producer for -- and, since 2026-09-30, what it declines."""

import json
from datetime import date

from cherrypick.curve import db, stream_request


def test_request_declares_quotes_and_greeks_on_its_own_dates_only(tmp_path):
    """The loop reads call quotes and greeks on the one monthly target: never the VXX weekly the
    nearest window serves, never open interest or option trades. If the provider ever starts
    reading any of those, this declaration has to change with it."""
    db_path = str(tmp_path / "paper.db")
    conn = db.connect(db_path)
    path = stream_request.write(
        {"symbol": "VXX"}, conn, db_path, cache_path=str(tmp_path / "c.db"), today=date(2026, 9, 30)
    )
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["symbols"] == ["VXX"]
    assert payload["legs"] == ["VIX", "VIX3M"]
    assert payload["window_events"] == {"VXX": ["Quote", "Greeks"]}
    assert payload["nearest_window"] == {"VXX": False}
