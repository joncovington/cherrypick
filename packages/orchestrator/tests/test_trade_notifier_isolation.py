"""The trade notifier after the 2026-09-23 `book` -> `arm` column rename, and why one module's defect
must not replay every other module's notifications.

On 2026-09-24 pmcc's and bwb's formatters still read `r['book']`, raised IndexError on every pass from
09:31 ET, and — because state is saved once after all modules — took the save down with them. Each
pass had already sent flies' events, so every flies entry, completion and settlement went out ~95
times before the close.
"""

import json
import sqlite3

import pytest

from cherrypick.orchestrator import trade_notifier as tn

pytestmark = pytest.mark.unit


class _Recorder:
    def __init__(self, *_a, **_k):
        self.sent = []

    def notify(self, level, key, title, body, embed=None):
        self.sent.append((key, body, embed))


def _pmcc_conn(arm_column: str):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        f"CREATE TABLE pmcc_positions (position_id TEXT, symbol TEXT, {arm_column} TEXT, long_strike REAL, "
        "long_expiration TEXT, short_strike REAL, short_expiration TEXT, net_debit REAL, entry_net_tv REAL, "
        "entry_downside_protection_pct REAL, entry_spot REAL, roll_count INTEGER, settlement_spot REAL, "
        "status TEXT)"
    )
    conn.execute(
        "INSERT INTO pmcc_positions VALUES ('TQQQ:control:2026-09-24', 'TQQQ', 'control', 60, '2026-10-16', "
        "79, '2026-10-02', 18.4, 0.9, 0.22, 78.6, 0, NULL, 'open')"
    )
    conn.commit()
    return conn


@pytest.mark.parametrize("arm_column", ["arm", "book"])
def test_pmcc_entry_renders_the_arm_under_either_column_name(arm_column):
    rec = _Recorder()
    st: dict = {}
    counts = tn._pmcc_process(_pmcc_conn(arm_column), st, rec, "pmcc")
    assert counts["entrys_notified"] == 1
    key, body, embed = rec.sent[0]
    assert key == "trade.pmcc.entry.TQQQ:control:2026-09-24"
    assert body.endswith("[control]") and embed["footer"]["text"] == "control"
    assert st["notified_entry_ids"] == ["TQQQ:control:2026-09-24"]


def test_a_module_that_raises_does_not_take_the_other_modules_save_with_it(tmp_path, monkeypatch):
    monkeypatch.setattr(tn, "_STATE", tmp_path / "trade_notify.json")
    monkeypatch.setattr(tn, "_LOCK", tmp_path / "trade_notify.lock")
    (tmp_path / "trade_notify.json").write_text(json.dumps({"good": {}, "bad": {}}), encoding="utf-8")
    db = tmp_path / "paper.db"
    sqlite3.connect(db).close()

    def good_process(conn, st, notifier, name):
        notifier.notify("INFO", "trade.good.entry.1", "Paper entry", "entry 1")
        st["notified_entry_ids"] = ["1"]
        return {"entrys_notified": 1}

    def bad_process(conn, st, notifier, name):
        raise IndexError("No item with that key")

    monkeypatch.setattr(
        tn, "_SCHEMAS", {"good_schema": (None, good_process), "bad_schema": (None, bad_process)}
    )
    monkeypatch.setattr(
        tn.cfgmod,
        "enabled_modules",
        lambda cfg: {
            "good": {"paper": {"notify_trades": True, "trade_schema": "good_schema"}},
            "bad": {"paper": {"notify_trades": True, "trade_schema": "bad_schema"}},
        },
    )
    monkeypatch.setattr(tn.cfgmod, "paper_db_path", lambda mcfg, name: db)
    monkeypatch.setattr(tn, "Notifier", _Recorder)

    result = tn.run(cfg={"notify": {}})

    assert result["modules"]["bad"] == {"error": "IndexError: No item with that key"}
    saved = json.loads((tmp_path / "trade_notify.json").read_text(encoding="utf-8"))
    assert saved["good"]["notified_entry_ids"] == ["1"]  # so the next pass does not send it again
    assert saved["bad"]["last_error"]["error"] == "IndexError: No item with that key"


def test_a_dry_run_formats_every_event_and_sends_and_saves_nothing(tmp_path, monkeypatch):
    # The rehearsal a schema change needs: every formatter run against the ledger, nothing leaving
    # the machine, and the next real pass unaffected -- it must still send what the dry run saw.
    monkeypatch.setattr(tn, "_STATE", tmp_path / "trade_notify.json")
    monkeypatch.setattr(tn, "_LOCK", tmp_path / "trade_notify.lock")
    before = json.dumps({"good": {}})
    (tmp_path / "trade_notify.json").write_text(before, encoding="utf-8")
    db = tmp_path / "paper.db"
    sqlite3.connect(db).close()

    def good_process(conn, st, notifier, name):
        notifier.notify("INFO", "trade.good.entry.1", "Paper entry", "entry 1")
        st["notified_entry_ids"] = ["1"]
        return {"entrys_notified": 1}

    monkeypatch.setattr(tn, "_SCHEMAS", {"good_schema": (None, good_process)})
    monkeypatch.setattr(
        tn.cfgmod,
        "enabled_modules",
        lambda cfg: {"good": {"paper": {"notify_trades": True, "trade_schema": "good_schema"}}},
    )
    monkeypatch.setattr(tn.cfgmod, "paper_db_path", lambda mcfg, name: db)

    def no_real_notifier(*_a, **_k):
        raise AssertionError("a dry run built a real notifier")

    monkeypatch.setattr(tn, "Notifier", no_real_notifier)

    result = tn.run(cfg={"notify": {}}, dry_run=True)

    assert result["dry_run"] is True
    assert result["would_send"] == [{"key": "trade.good.entry.1", "title": "Paper entry"}]
    assert (tmp_path / "trade_notify.json").read_text(encoding="utf-8") == before
