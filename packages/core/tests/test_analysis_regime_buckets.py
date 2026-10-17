"""core.analysis.buckets' regime dimensions (2026-10-17): VIX and VIX/VIX3M at entry, joined
through core.regime.markets_at, for every module that writes no regime cuts of its own.

The guards: an entry the recorder did not cover reads `unmeasured`, never a band (a recorder
outage must not look like a calm market), and flies' and MEIC's readings carry no regime table,
because their own regime cuts are the answer to that question.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import pytest

from cherrypick.core import regime
from cherrypick.core.analysis import __main__ as cli
from cherrypick.core.analysis import buckets
from cherrypick.core.clock import ET

T0 = datetime(2026, 9, 15, 11, 0, tzinfo=ET)


@pytest.fixture()
def history_db(tmp_path):
    path = tmp_path / "gex_history.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE market_regime_history (trade_date TEXT NOT NULL, ts REAL NOT NULL, "
        "reading TEXT NOT NULL, symbol TEXT, value REAL, basis_ts REAL, usable INTEGER NOT NULL, reason TEXT)"
    )
    conn.execute(
        "CREATE TABLE daily_closes (symbol TEXT NOT NULL, trade_date TEXT NOT NULL, close REAL NOT NULL, "
        "recorded_at REAL NOT NULL, source TEXT NOT NULL)"
    )
    # 11:00 VIX 14 in contango; 11:05 VIX 26 inverted. Nothing after 11:05.
    for minute, vix, vix3m in ((0, 14.0, 16.0), (5, 26.0, 25.0)):
        ts = T0.timestamp() + minute * 60
        for reading, value in (("vix", vix), ("vix3m", vix3m)):
            conn.execute(
                "INSERT INTO market_regime_history VALUES (?,?,?,?,?,?,1,NULL)",
                ("2026-09-15", ts, reading, reading.upper(), value, ts),
            )
    conn.commit()
    conn.close()
    return path


def _rec(minute: int | None, net: float = 10.0, session: str = "2026-09-15") -> dict:
    t = None if minute is None else T0.replace(minute=minute).isoformat()
    return {"arm": "control", "session": session, "net_pnl": net, "entry_time": t}


def test_markets_at_is_regime_at_market_block_for_each_stamp(history_db):
    stamps = [T0.timestamp() + 30, T0.timestamp() + 400]
    many = regime.markets_at(stamps, history_db=history_db)
    assert many == [regime.regime_at(t, history_db=history_db)["market"] for t in stamps]


def test_entries_band_by_the_market_they_were_entered_into(history_db):
    recs = [_rec(2), _rec(7)]
    joined = buckets.attach_regime(recs, lambda ts: regime.markets_at(ts, history_db=history_db))
    assert joined == 2
    assert [buckets.vix(r) for r in recs] == ["<15", ">=25"]
    assert [buckets.term(r) for r in recs] == ["contango", "backwardation"]


def test_an_entry_the_recorder_did_not_cover_is_unmeasured_never_a_band(history_db):
    # 30 minutes after the last sample: beyond the 15-minute staleness bound. Before the first: nothing.
    recs = [_rec(35), {**_rec(0), "entry_time": "2026-09-14T11:00:00-04:00"}]
    buckets.attach_regime(recs, lambda ts: regime.markets_at(ts, history_db=history_db))
    assert [buckets.vix(r) for r in recs] == ["unmeasured", "unmeasured"]
    assert [buckets.term(r) for r in recs] == ["unmeasured", "unmeasured"]
    rows = buckets.table(recs, buckets.vix, "vix")["rows"]
    assert [r["bucket"] for r in rows] == ["unmeasured"]


def test_a_missing_history_db_is_unmeasured_throughout(tmp_path):
    recs = [_rec(2)]
    buckets.attach_regime(recs, lambda ts: regime.markets_at(ts, history_db=tmp_path / "absent.db"))
    assert buckets.vix(recs[0]) == "unmeasured"


def test_no_entry_time_is_skipped_not_unmeasured(history_db):
    recs = [_rec(None), _rec(2)]
    buckets.attach_regime(recs, lambda ts: regime.markets_at(ts, history_db=history_db))
    assert "regime" not in recs[0]
    assert buckets.table(recs, buckets.vix, "vix")["skipped"] == 1


def test_bands_are_ordered_low_to_high_with_unmeasured_last():
    def joined(vix, ratio):
        return {"session": "2026-09-15", "net_pnl": 1.0,
                "regime": {"status": "measured", "vix": vix, "vix_vix3m_ratio": ratio}}  # fmt: skip

    recs = [joined(30, 1.1), joined(None, None), joined(17, 0.98), joined(12, 0.9), joined(22, 0.9)]
    vix_rows = [r["bucket"] for r in buckets.table(recs, buckets.vix, "vix")["rows"]]
    term_rows = [r["bucket"] for r in buckets.table(recs, buckets.term, "term")["rows"]]
    assert vix_rows == ["<15", "15-20", "20-25", ">=25", "unmeasured"]
    assert term_rows == ["contango", "flat", "backwardation", "unmeasured"]


def test_the_term_edges_are_the_suites_own_lines():
    def at(ratio):
        return buckets.term({"regime": {"status": "measured", "vix_vix3m_ratio": ratio}})

    assert (at(0.9699), at(0.97), at(0.9999), at(1.0)) == ("contango", "flat", "flat", "backwardation")


def test_breakdowns_carry_regime_tables_only_when_joined(history_db):
    plain = buckets.breakdowns([_rec(2)])
    assert "vix" not in plain and "term" not in plain
    recs = [_rec(2)]
    buckets.attach_regime(recs, lambda ts: regime.markets_at(ts, history_db=history_db))
    assert {"vix", "term"} <= set(buckets.breakdowns(recs))


def _bwb_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE bwb_positions (symbol TEXT, arm TEXT, gross_pnl REAL, fees REAL, entry_max_loss REAL, "
        "quantity INTEGER, closed_session TEXT, status TEXT, entry_time TEXT, closed_at TEXT)"
    )
    conn.execute(
        "INSERT INTO bwb_positions VALUES ('SPX','control',10,1,500,1,'2026-09-15','closed',?,?)",
        (T0.replace(minute=2).isoformat(), T0.replace(hour=15).isoformat()),
    )
    conn.commit()
    conn.close()


def test_the_cli_joins_every_module_but_those_that_cut_their_own(tmp_path, history_db, capsys):
    db = tmp_path / "paper_trades.db"
    _bwb_db(db)
    cli.main(["read", "--db", str(db), "--schema", "bwb_132", "--history-db", str(history_db)])
    doc = json.loads(capsys.readouterr().out)
    assert doc["regime"]["joined"] == 1
    assert doc["groups"]["control"]["buckets"]["vix"]["rows"][0]["bucket"] == "<15"
    assert cli.OWN_REGIME_CUTS == {"fly_book", "meic_ic"}
