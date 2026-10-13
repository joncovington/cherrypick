"""The trade notifier's state file and the P&L its exit cards print.

2026-10-12: `state/trade_notify.json` was 7.3 MB, rewritten indented every 30 seconds around the
clock whether or not anything changed. 3.9 MB of it was MEIC's state -- its notify switched off since
2026-08-19 -- holding a digest bucket of 271,000 exits. Those were REPLAYS: the remembered-id lists
were cut to the newest 4,000 while the query kept returning every exit ever closed, so from the
4,001st exit on the oldest ones looked new on every pass. The same audit found MEIC's and earnings'
exit cards printing GROSS P&L under "P&L".
"""

import json
import os
import sqlite3
import time
from datetime import date, timedelta

import pytest

from cherrypick.orchestrator import trade_notifier as tn

pytestmark = pytest.mark.unit


class _Recorder:
    def __init__(self, *_a, **_k):
        self.sent = []

    def notify(self, level, key, title, body, embed=None):
        self.sent.append((key, body, embed))


def _meic_db(path, n_closed: int, n_open: int = 0):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE ic_trades (id INTEGER PRIMARY KEY, symbol TEXT, risk_profile TEXT, "
        "put_strike REAL, call_strike REAL, wing_width REAL, net_credit REAL, quantity INTEGER, "
        "status TEXT, entry_time TEXT, exit_time TEXT, exit_reason TEXT, pnl REAL, fees REAL, "
        "put_stop_cost REAL, call_stop_cost REAL)"
    )
    for i in range(1, n_closed + n_open + 1):
        closed = i <= n_closed
        conn.execute(
            "INSERT INTO ic_trades VALUES (?, 'XSP', 'control', 590, 600, 2, 0.5, 1, ?, "
            "'2026-10-09 10:00:00-04:00', ?, ?, ?, ?, NULL, NULL)",
            (
                i,
                "expired" if closed else "open",
                "2026-10-09 16:00:00-04:00" if closed else None,
                "expired" if closed else None,
                40.0 if closed else None,
                4.0 if closed else None,
            ),
        )
    conn.commit()
    conn.close()


@pytest.fixture
def harness(tmp_path, monkeypatch):
    """run() against one MEIC paper ledger, per-trade mode, with a recording notifier."""
    monkeypatch.setattr(tn, "_STATE", tmp_path / "trade_notify.json")
    monkeypatch.setattr(tn, "_LOCK", tmp_path / "trade_notify.lock")
    db = tmp_path / "paper.db"
    modules = {"meic": {"paper": {"notify_trades": True, "trade_schema": "meic_ic"}}}
    monkeypatch.setattr(tn.cfgmod, "enabled_modules", lambda cfg: modules)
    monkeypatch.setattr(tn.cfgmod, "paper_db_path", lambda mcfg, name: db)
    sent = []

    class Rec(_Recorder):
        def notify(self, level, key, title, body, embed=None):
            sent.append(key)

    monkeypatch.setattr(tn, "Notifier", Rec)
    return {"db": db, "modules": modules, "sent": sent, "state": tmp_path / "trade_notify.json"}


def _run():
    return tn.run(cfg={"notify": {}})


# --------------------------------------------------------------------------- P&L is net
def _row(**cols):
    """One sqlite3.Row with these columns, the type every formatter is handed."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn.execute("SELECT " + ", ".join(f"? AS {k}" for k in cols), tuple(cols.values())).fetchone()


def test_a_meic_exit_card_prints_net_and_labels_the_gross():
    r = _row(
        id=1, symbol="SPX", arm="control", exit_reason="expired", pnl=100.0, fees=7.5,
        entry_time="2026-10-09 10:00:00-04:00", exit_time="2026-10-09 16:00:00-04:00",
    )  # fmt: skip
    assert "P&L $+92.50 (gross $+100.00)" in tn._fmt_meic_exit(r)
    details = tn._embed_meic_exit(r)["fields"][0]["value"]
    assert "P&L $+92.50 (gross $+100.00, fees $7.50)" in details


def test_an_earnings_exit_card_prints_net_of_both_cost_legs():
    r = _row(
        order_id="x", strategy="iron_fly", symbol="ZM", pnl=50.0, entry_cost=6.0, exit_cost=4.0,
        arm="default", opened_at=None, closed_at=None,
    )  # fmt: skip
    assert "P&L $+40.00 (gross $+50.00)" in tn._fmt_earnings_exit(r)
    assert "P&L $+40.00 (gross $+50.00, costs $10.00)" in tn._embed_earnings_exit(r)["fields"][0]["value"]


def test_a_missing_net_reads_not_recorded_never_zero():
    r = _row(
        shares=10, symbol="SVXY", exit_mid=50.0, net_pnl=None, gross_pnl=None, fees=1.0, slippage=0.5,
        exit_reason="switch", entry_session="2026-10-06", arm="control",
    )  # fmt: skip
    assert "net n/r" in tn._fmt_contango_exit(r)
    assert "net n/r (gross n/r" in tn._embed_contango_exit(r)["fields"][0]["value"]
    meic = _row(id=1, symbol="SPX", arm="control", exit_reason="x", pnl=None, fees=None,
                entry_time="", exit_time="")  # fmt: skip
    assert "P&L n/r" in tn._fmt_meic_exit(meic)


# --------------------------------------------------------------------------- the state file
def test_an_unchanged_pass_does_not_rewrite_the_file_but_still_shows_it_ran(harness):
    _meic_db(harness["db"], n_closed=3)
    assert _run()["modules"]["meic"] == {"seeded": True}
    _run()  # the first processing pass adds the digest's own keys
    text = harness["state"].read_text(encoding="utf-8")
    assert "\n" not in text and ": " not in text, "compact, not indented"
    old = time.time() - 3600
    os.utime(harness["state"], (old, old))

    out = _run()
    assert out["written"] is False
    assert harness["state"].read_text(encoding="utf-8") == text
    assert harness["state"].stat().st_mtime > old + 3000, "the watchdog reads the mtime as liveness"


def test_a_pass_that_changes_state_writes_it(harness):
    _meic_db(harness["db"], n_closed=3, n_open=1)
    _run()
    conn = sqlite3.connect(harness["db"])
    conn.execute(
        "UPDATE ic_trades SET exit_time = '2026-10-09 16:00:00-04:00', pnl = 1, fees = 0 WHERE id = 4"
    )
    conn.commit()
    conn.close()
    out = _run()
    assert out["written"] is True
    assert harness["sent"] == ["trade.meic.exit.4"]
    assert 4 in json.loads(harness["state"].read_text(encoding="utf-8"))["meic"]["notified_exit_ids"]


def test_a_module_no_longer_notified_has_its_state_pruned(harness):
    _meic_db(harness["db"], n_closed=3)
    harness["state"].write_text(
        json.dumps({"retired": {"notified_entry_ids": ["a"] * 1000}, "meic": {}}), encoding="utf-8"
    )
    out = _run()
    assert out["pruned"] == ["retired"]
    assert set(json.loads(harness["state"].read_text(encoding="utf-8"))) == {"meic"}


def test_reenabling_a_module_seeds_it_and_sends_nothing_from_its_history(harness):
    # The incident shape: MEIC's state frozen at id 6,495, ledger at 23,426. Switched off, the
    # state is pruned; switched back on, it is seeded at the ledger as it stands.
    _meic_db(harness["db"], n_closed=50)
    _run()
    harness["modules"]["meic"]["paper"]["notify_trades"] = False
    assert _run()["pruned"] == ["meic"]
    _meic_db_add(harness["db"], 51, 80)  # trades closed while it was off
    harness["modules"]["meic"]["paper"]["notify_trades"] = True
    assert _run()["modules"]["meic"] == {"seeded": True}
    assert _run()["modules"]["meic"]["exits_notified"] == 0
    assert harness["sent"] == []


def test_a_stale_state_is_reseeded_at_the_ledger_not_caught_up(harness):
    # A state that survived but was not processed for days (the module's notify off under an
    # older notifier that never pruned; the suite down for a week): the ledger moved on beneath it.
    _meic_db(harness["db"], n_closed=80)
    stale_on = (date.today() - timedelta(days=tn._STALE_DAYS + 3)).isoformat()
    harness["state"].write_text(
        json.dumps(
            {"meic": {"last_entry_id": 10, "notified_exit_ids": list(range(1, 11)), "processed_on": stale_on}}
        ),
        encoding="utf-8",
    )
    out = _run()
    assert out["modules"]["meic"] == {"seeded": True, "reseeded": "stale"}
    st = json.loads(harness["state"].read_text(encoding="utf-8"))["meic"]
    assert st["last_entry_id"] == 80 and st["exit_floor_id"] == 80
    assert _run()["modules"]["meic"]["exits_notified"] == 0
    assert harness["sent"] == []


def test_a_fresh_stamp_is_not_stale():
    assert not tn._stale({"processed_on": date.today().isoformat()}, date.today())
    assert not tn._stale({}, date.today()), "legacy state without a stamp was processed last pass"
    assert tn._stale({"processed_on": "2026-08-19"}, date(2026, 10, 12))


def _meic_db_add(path, first: int, last: int):
    conn = sqlite3.connect(path)
    for i in range(first, last + 1):
        conn.execute(
            "INSERT INTO ic_trades VALUES (?, 'XSP', 'control', 590, 600, 2, 0.5, 1, 'expired', "
            "'2026-10-09 10:00:00-04:00', '2026-10-09 16:00:00-04:00', 'expired', 1, 0, NULL, NULL)",
            (i,),
        )
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------- no replay past the cap
def test_exits_past_the_id_cap_are_never_sent_twice(harness, monkeypatch):
    monkeypatch.setattr(tn, "_ID_CAP", 5)
    _meic_db(harness["db"], n_closed=2, n_open=0)
    _run()  # seed
    _meic_db_add(harness["db"], 3, 20)
    for _ in range(4):
        _run()
    exits = [k for k in harness["sent"] if ".exit." in k]
    assert sorted(exits) == sorted(f"trade.meic.exit.{i}" for i in range(3, 21))
    st = json.loads(harness["state"].read_text(encoding="utf-8"))["meic"]
    assert len(st["notified_exit_ids"]) <= 5 and st["exit_floor_id"] >= 15


def test_a_legacy_capped_list_is_read_as_a_floor_not_replayed(harness):
    # The old writer kept the newest 4,000 in order; everything below the oldest kept id was sent.
    _meic_db(harness["db"], n_closed=30)
    harness["state"].write_text(
        json.dumps({"meic": {"last_entry_id": 30, "notified_exit_ids": list(range(21, 31))}}),
        encoding="utf-8",
    )
    assert _run()["modules"]["meic"]["exits_notified"] == 0
    assert harness["sent"] == []


def test_text_ids_are_pruned_to_the_ledger_and_never_cut():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE t (position_id TEXT)")
    conn.executemany("INSERT INTO t VALUES (?)", [(f"P{i:05d}",) for i in range(6000)])
    st = {"notified_x_ids": [f"P{i:05d}" for i in range(6000)] + ["GONE"]}
    rec = _Recorder()
    stages = [("notified_x_ids", "x", "X", lambda r: "", lambda r: {}, "SELECT * FROM t")]
    counts = tn._run_stages(conn, st, rec, "m", stages)
    assert counts == {"xs_notified": 0} and rec.sent == []
    assert len(st["notified_x_ids"]) == 6000 and "GONE" not in st["notified_x_ids"]


# --------------------------------------------------------------------------- the digest bucket
def test_a_legacy_list_bucket_reads_as_counts():
    b = tn._pending_bucket({"entries": ["a", "a", "b"], "exits": [1.5, -0.5, 2.0]})
    assert b == {"entries": {"a": 2, "b": 1}, "exits": 3, "net": 3.0}
    assert tn._pending_bucket(b) == b
