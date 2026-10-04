"""bwb's live log masks account numbers at the sink, the flies rule.

Order-placement lines log the broker's response whole, and it carries the full account number.
bwb's `_log` wrote it as received until 2026-10-04; the suite guardrail is that no log holds one.
"""

from cherrypick.bwb import live_loop


def test_live_log_masks_account_numbers(monkeypatch):
    seen = []
    monkeypatch.setattr(live_loop._logs, "configure", lambda *a, **k: None)
    monkeypatch.setattr(live_loop._logger, "info", seen.append)
    live_loop._log('entry order (LIVE): {"ok": true, "account_number": "5WT99991", "order_id": "O1"}')
    assert seen == ['entry order (LIVE): {"ok": true, "account_number": "****9991", "order_id": "O1"}']
