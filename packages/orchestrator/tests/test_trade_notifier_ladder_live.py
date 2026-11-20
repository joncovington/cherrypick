"""The ladder's LIVE ledger in the trade notifier: its lots, labelled LIVE, and the journal alarms a
person has to read (issue #1337). Fake ledgers and a recording notifier only; nothing is sent.

The shapes of the journal rows (mode, symbol, reason, detail) are the ladder's own, written by
`cherrypick.ladder.live_tick`; `packages/ladder/tests/test_live_tick.py` pins the ones read here.
"""

import json
import sqlite3

import pytest

from cherrypick.orchestrator import trade_notifier as tn

pytestmark = pytest.mark.unit

_LOTS = (
    "CREATE TABLE ladder_positions (position_id TEXT, arm TEXT, symbol TEXT, quantity INTEGER, "
    "buy_session TEXT, buy_price REAL, target_price REAL, sell_price REAL, entry_value REAL, status TEXT, "
    "exit_reason TEXT, hold_days INTEGER, gross_pnl REAL, fees REAL, settlement REAL, slippage REAL, "
    "net_pnl REAL, account TEXT, fees_source TEXT)"
)
_JOURNAL = (
    "CREATE TABLE ladder_decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, trade_date TEXT, arm TEXT, "
    "symbol TEXT, mode TEXT, reason TEXT, accepted INTEGER, occurrences INTEGER DEFAULT 1, "
    "first_ts TEXT, last_ts TEXT, detail TEXT)"
)


def _live_db(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(_LOTS)
    conn.execute(_JOURNAL)
    conn.commit()
    return conn


def _lot(conn, pid, status="open", **over):
    row = {
        "position_id": pid, "arm": "session", "symbol": "TQQQ", "quantity": 10, "buy_session": "2026-11-23",
        "buy_price": 93.0, "target_price": 96.72, "sell_price": None, "entry_value": -930.0,
        "status": status, "exit_reason": None, "hold_days": None, "gross_pnl": None, "fees": None,
        "settlement": None, "slippage": None, "net_pnl": None, "account": "****1234", "fees_source": None,
    }  # fmt: skip
    row.update(over)
    conn.execute(
        f"INSERT INTO ladder_positions ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
        tuple(row.values()),
    )
    conn.commit()


def _close(conn, pid):
    conn.execute(
        "UPDATE ladder_positions SET status='closed', exit_reason='target_gain', sell_price=96.72, "
        "hold_days=2, gross_pnl=37.2, fees=0.07, settlement=0.0, slippage=0.0, net_pnl=37.13, "
        "fees_source='modeled' WHERE position_id = ?",
        (pid,),
    )
    conn.commit()


def _journal(conn, mode, symbol, reason, detail=None, day="2026-11-23"):
    conn.execute(
        "INSERT INTO ladder_decisions (trade_date, arm, symbol, mode, reason, accepted, detail) "
        "VALUES (?, 'session', ?, ?, ?, 0, ?)",
        (day, symbol, mode, reason, json.dumps(detail) if detail is not None else None),
    )
    conn.commit()
    return conn.execute("SELECT max(id) FROM ladder_decisions").fetchone()[0]


class _Rec:
    def __init__(self):
        self.sent = []

    def notify(self, level, key, title, body, embed=None):
        self.sent.append({"level": level, "key": key, "title": title, "body": body, "embed": embed})


def _live_pass(conn, state, rec):
    """One live pass as `run()` makes it: the module's LIVE wrapper over its live adapter."""
    _, process = tn._LIVE_SCHEMAS["ladder_lot"]
    return process(conn, state, tn._LiveNotifier(rec), "ladder")


def _seeded(conn):
    seed, _ = tn._LIVE_SCHEMAS["ladder_lot"]
    return seed(conn)


# --------------------------------------------------------------------------- lots
def test_a_live_buy_and_sale_are_each_announced_once_and_labelled_live():
    conn = _live_db(":memory:")
    state, rec = _seeded(conn), _Rec()
    _lot(conn, "session:TQQQ:2026-11-23:1")
    _live_pass(conn, state, rec)
    _live_pass(conn, state, rec)
    assert len(rec.sent) == 1
    buy = rec.sent[0]
    assert buy["key"].startswith("live.") and buy["title"].startswith("LIVE:")
    assert "Ladder LIVE BUY" in buy["body"] and "paper" not in buy["body"].lower()
    assert buy["embed"]["title"].startswith("LIVE — ")
    _close(conn, "session:TQQQ:2026-11-23:1")
    _live_pass(conn, state, rec)
    _live_pass(conn, state, rec)
    assert len(rec.sent) == 2
    sale = rec.sent[1]
    assert "Ladder LIVE SELL" in sale["body"] and "net $+37.13" in sale["body"]
    details = sale["embed"]["fields"][0]["value"]
    assert "net $+37.13 (gross $+37.20, fees $0.07 modeled, settlement $0.00, slippage $0.00)" in details


def test_paper_is_unchanged_by_the_live_additions():
    """Paper still runs the paper adapter: paper wording, the paper key space, no journal read (a paper
    ledger has no `ladder_decisions` rows the notifier looks at)."""
    seed, process = tn._SCHEMAS["ladder_lot"]
    assert (seed, process) == (tn._ladder_seed, tn._ladder_process)
    conn = _live_db(":memory:")
    state, rec = seed(conn), _Rec()
    _lot(conn, "session:TQQQ:2026-11-23:1")
    _journal(conn, "alert", "TQQQ", "hospital session:TQQQ:2026-10-01:1", {"age_days": 40})
    process(conn, state, rec, "ladder")
    assert [s["key"] for s in rec.sent] == ["trade.ladder.entry.session:TQQQ:2026-11-23:1"]
    assert "Ladder paper BUY" in rec.sent[0]["body"]
    assert "notified_alert_ids" not in state
    _close(conn, "session:TQQQ:2026-11-23:1")
    process(conn, state, rec, "ladder")
    assert "fees $0.07" in rec.sent[1]["embed"]["fields"][0]["value"]


def test_a_paper_exit_card_names_no_fee_source():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(_LOTS.replace(", account TEXT, fees_source TEXT", ""))
    conn.execute(
        "INSERT INTO ladder_positions (position_id, arm, symbol, status, exit_reason, sell_price, "
        "gross_pnl, fees, settlement, slippage, net_pnl, hold_days, buy_session, quantity) VALUES "
        "('p', 'chain', 'TQQQ', 'closed', 'target_gain', 96.7, 37.2, 0.07, 0, 0, 37.13, 2, 'x', 10)"
    )
    row = conn.execute("SELECT * FROM ladder_positions").fetchone()
    assert "fees $0.07," in tn._embed_ladder_exit(row)["fields"][0]["value"]


def test_lots_and_alarms_already_in_the_ledger_are_seeded_not_announced():
    conn = _live_db(":memory:")
    _lot(conn, "a")
    _lot(conn, "b", status="closed", exit_reason="target_gain", sell_price=96.72, net_pnl=37.0)
    _journal(conn, "alert", "TQQQ", "hospital a", {"age_days": 40})
    _journal(conn, "blocked", "-", "drawdown_breaker")
    state, rec = _seeded(conn), _Rec()
    _live_pass(conn, state, rec)
    assert rec.sent == []
    _journal(conn, "alert", "TQQQ", "hospital b", {"age_days": 41})
    _live_pass(conn, state, rec)
    assert len(rec.sent) == 1


def test_a_state_from_before_alarms_were_announced_is_seeded_from_the_journal():
    conn = _live_db(":memory:")
    state = tn._ladder_seed(conn)  # the shape written by a pass that only knew lots
    _journal(conn, "alert", "TQQQ", "hospital a", {"age_days": 40})
    rec = _Rec()
    _live_pass(conn, state, rec)
    assert rec.sent == [] and state["notified_alert_ids"]
    _journal(conn, "alert", "TQQQ", "hospital b", {"age_days": 41})
    _live_pass(conn, state, rec)
    assert len(rec.sent) == 1


def test_a_dry_run_ledger_has_no_lots_and_announces_nothing_of_its_own():
    """Dry-run journals the orders it would send and writes no lot: nothing for the notifier to read."""
    conn = _live_db(":memory:")
    state, rec = _seeded(conn), _Rec()
    _journal(conn, "dry_run", "TQQQ", "would place buy TQQQ ldr.b.TQQQ.20261123.1.10x9300",
             {"shares": 10, "limit": 93.0})  # fmt: skip
    _journal(conn, "order", "TQQQ", "place buy TQQQ x", {"ok": True})
    _live_pass(conn, state, rec)
    assert rec.sent == []


# --------------------------------------------------------------------------- alarms
_KINDS = [
    ("alert", "TQQQ", "hospital session:TQQQ:2026-10-01:1", {"age_days": 40},
     ["hospital lot", "open 40 days", "Nothing was done"]),
    ("alert", "TQQQ", "no_whole_shares_after_split session:TQQQ:2026-10-01:1", None,
     ["less than one whole share", "needs you"]),
    ("alert", "TQQQ", "split_leaves_no_whole_share session:TQQQ:2026-10-01:1", None,
     ["less than one whole share"]),
    ("alert", "TQQQ", "sell_ended_repeatedly session:TQQQ:2026-10-01:1", None,
     ["ended unfilled several times", "needs you"]),
    ("alert", "-", "orphan_sell session-TQQQ-20261001-1", {"order_id": "9"},
     ["resting sell", "Nothing was cancelled"]),
    ("alert", "session:TQQQ:2026-11-23:1", "partial_buy", {"filled": 6}, ["filled only 6 shares"]),
    ("alert", "session:TQQQ:2026-11-23:1", "partial_sell", {"sold": 4}, ["sold in part", "no further sell"]),
    ("alert", "TQQQ", "partial_sell_review session:TQQQ:2026-11-23:1", None, ["sold in part"]),
    ("alert", "-", "sell_filled_for_no_open_lot", {"order": "ldr.s.x"}, ["ldr.s.x", "needs you"]),
    ("alert", "-", "splits_invalid: splits[0] (TQQQ) has no ISO ex_date", None,
     ["live.splits entry cannot be read", "stopped on every fund"]),
    ("alert", "-", "positions_unreadable: the broker's positions could not be read; no new buys", None,
     ["could not be read", "stopped on every fund"]),
    ("alert", "TQQQ", "suspected_split 2:1: broker holds 20, ledger 10", {"ledger": 10, "broker": 20.0},
     ["looks like a split", "nothing was restated", "live.splits"]),
    ("alert", "TQQQ", "positions_shortfall: broker holds 3, ledger 10", {"ledger": 10, "broker": 3.0},
     ["missing at the broker", "resting sells are untouched"]),
    ("positions", "TQQQ", "surplus: broker holds 15, ledger 10; the rest is not the ladder's",
     {"ledger": 10, "broker": 15.0, "status": "surplus"},
     ["broker holds 15 shares, the ledger 10", "nothing was done"]),
    ("split", "TQQQ", "restated lot x for the 2:1 split of TQQQ (ex 2026-11-23): 10 -> 20 shares",
     {"shares_after": 20}, ["restated", "live.splits", "10 -> 20 shares"]),
    ("blocked", "-", "drawdown_breaker", None, ["drawdown breaker tripped", "Sells already resting"]),
    ("blocked", "limits_invalid", "max_open_lots_total below max_open_lots", None,
     ["limits are invalid", "max_open_lots_total below max_open_lots"]),
    ("refused", "TQQQ", "place buy TQQQ ldr.b.TQQQ.20261123.1.10x9300 failed",
     {"ok": False, "unmet_gates": ["arm.record: not armed today"]},
     ["order not sent", "place buy TQQQ ldr.b.TQQQ.20261123.1.10x9300 was not sent",
      "gated: arm.record: not armed today", "planned again next tick"]),
    ("refused", "TQQQ", "place sell TQQQ ldr.s.x.10x9672 failed",
     {"ok": False, "skipped": "broker_hold"}, ["was not sent", "uncertain outcome"]),
    ("refused", "TQQQ", "place buy TQQQ ldr.b.x failed",
     {"ok": False, "uncertain": True}, ["outcome is uncertain"]),
    ("refused", "TQQQ", "cancel buy TQQQ ldr.b.x failed",
     {"ok": False, "error": "order not found"}, ["cancel buy TQQQ ldr.b.x was not sent", "order not found"]),
]  # fmt: skip


@pytest.mark.parametrize(("mode", "symbol", "reason", "detail", "words"), _KINDS)
def test_each_alarm_is_formatted_and_announced_once(mode, symbol, reason, detail, words):
    conn = _live_db(":memory:")
    state, rec = _seeded(conn), _Rec()
    _journal(conn, mode, symbol, reason, detail)
    _live_pass(conn, state, rec)
    _live_pass(conn, state, rec)
    assert len(rec.sent) == 1
    sent = rec.sent[0]
    assert sent["title"] == "LIVE: Ladder alert" and sent["key"].startswith("live.trade.ladder.alert.")
    assert "Ladder LIVE" in sent["body"] and "[session]" in sent["body"]
    text = sent["body"] + sent["embed"]["fields"][0]["value"]
    for w in words:
        assert w in text, (w, text)
    assert sent["embed"]["title"].startswith("LIVE — ALERT")


def test_a_surplus_and_a_split_are_information_and_the_rest_warn():
    conn = _live_db(":memory:")
    state, rec = _seeded(conn), _Rec()
    _journal(conn, "positions", "TQQQ", "surplus", {"ledger": 1, "broker": 2.0})
    _journal(conn, "split", "TQQQ", "restated x")
    _journal(conn, "alert", "TQQQ", "hospital x", {"age_days": 31})
    _live_pass(conn, state, rec)
    assert [s["level"] for s in rec.sent] == ["INFO", "INFO", "WARNING"]


def test_a_breaker_that_holds_for_many_ticks_is_announced_once():
    """The journal collapses a day's identical rows into one with a count (`record_decision`), so a
    breaker held for a hundred ticks is one row whose count climbs. Announced on the row, not the count."""
    conn = _live_db(":memory:")
    state, rec = _seeded(conn), _Rec()
    rid = _journal(conn, "blocked", "-", "drawdown_breaker")
    for _tick in range(100):
        conn.execute("UPDATE ladder_decisions SET occurrences = occurrences + 1 WHERE id = ?", (rid,))
        conn.commit()
        _live_pass(conn, state, rec)
    assert len(rec.sent) == 1
    _journal(conn, "blocked", "-", "drawdown_breaker", day="2026-11-24")  # the next session day's row
    _live_pass(conn, state, rec)
    assert len(rec.sent) == 2


def test_routine_blocked_rows_and_other_modes_are_not_read_at_all():
    conn = _live_db(":memory:")
    for reason in ("unarmed", "before_session_start", "after_disarm_at", "halted"):
        _journal(conn, "blocked", "-", reason)
    for why in ("name_cap_full", "no_reference_close", "ended_today", "lot_too_small"):
        _journal(conn, "blocked", "TQQQ", why)
    for mode in ("dry_run", "order"):
        _journal(conn, mode, "TQQQ", "x")
    assert conn.execute(tn._LADDER_ALERT_SQL).fetchall() == []
    _journal(conn, "blocked", "-", "drawdown_breaker")
    assert len(conn.execute(tn._LADDER_ALERT_SQL).fetchall()) == 1


def test_an_account_number_never_appears_unmasked():
    conn = _live_db(":memory:")
    state, rec = _seeded(conn), _Rec()
    _journal(conn, "alert", "TQQQ", "positions_shortfall: broker holds 3, ledger 10 account_number=5WT99991",
             {"account": "5WT99991"})  # fmt: skip
    _journal(conn, "alert", "TQQQ", 'sell_ended_repeatedly lot "account_number": "5WT99991"')
    _lot(conn, "p", account="****1234")
    _live_pass(conn, state, rec)
    blob = json.dumps(rec.sent)
    assert len(rec.sent) == 3 and "5WT99991" not in blob and "9991" not in blob.replace("****9991", "")


# --------------------------------------------------------------------------- run(): the whole pass
@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setattr(tn, "_STATE", tmp_path / "trade_notify.json")
    monkeypatch.setattr(tn, "_LOCK", tmp_path / "trade_notify.lock")
    paper, live = tmp_path / "paper.db", tmp_path / "live.db"
    modules = {
        "ladder": {
            "paper": {"notify_trades": True, "trade_schema": "ladder_lot"},
            "live": {"notify_trades": True},
        }
    }
    monkeypatch.setattr(tn.cfgmod, "enabled_modules", lambda cfg: modules)
    monkeypatch.setattr(tn.cfgmod, "paper_db_path", lambda mcfg, name: paper)
    monkeypatch.setattr(
        tn.cfgmod, "live_db_path", lambda mcfg, name: live if mcfg["live"].get("notify_trades") else None
    )
    sent = []

    class Rec:
        def __init__(self, *_a, **_k):
            pass

        def notify(self, level, key, title, body, embed=None):
            sent.append({"key": key, "title": title, "body": body})

    monkeypatch.setattr(tn, "Notifier", Rec)
    return {"paper": paper, "live": live, "modules": modules, "sent": sent}


def test_run_seeds_then_announces_live_lots_and_alarms_with_paper_beside_it(harness):
    paper = _live_db(str(harness["paper"]))
    live = _live_db(str(harness["live"]))
    _lot(live, "old")
    _journal(live, "alert", "TQQQ", "hospital old", {"age_days": 40})
    out = tn.run(cfg={"notify": {}})
    assert out["modules"]["ladder"] == {"seeded": True} and out["modules"]["ladder:live"] == {"seeded": True}
    assert harness["sent"] == []
    _lot(paper, "session:TQQQ:2026-11-24:1", account=None)
    _lot(live, "session:TQQQ:2026-11-24:1")
    _journal(live, "blocked", "-", "drawdown_breaker", day="2026-11-24")
    for _ in range(3):
        out = tn.run(cfg={"notify": {}})
    keys = sorted(s["key"] for s in harness["sent"])
    assert len(keys) == 3
    assert [k for k in keys if not k.startswith("live.")] == ["trade.ladder.entry.session:TQQQ:2026-11-24:1"]
    live_titles = {s["title"] for s in harness["sent"] if s["key"].startswith("live.")}
    assert live_titles == {"LIVE: entry", "LIVE: Ladder alert"}


def test_live_notices_follow_live_notify_trades_and_are_off_without_it(harness):
    harness["modules"]["ladder"]["live"] = {}
    _live_db(str(harness["paper"]))
    _live_db(str(harness["live"]))
    out = tn.run(cfg={"notify": {}})
    assert "ladder:live" not in out["modules"]


def test_the_shipped_config_watches_the_live_ledger():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    cfg = json.loads((root / "config.example.json").read_text(encoding="utf-8"))
    ladder = cfg["modules"]["ladder"]
    assert ladder["live"] == {"notify_trades": True}
    assert ladder["live_db"].endswith("data/ladder/live_trades.db")
    assert "task_name" not in ladder["live"]  # wiring for notices only: no live job is derived
