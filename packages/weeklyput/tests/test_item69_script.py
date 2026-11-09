from datetime import date, datetime

import pytest
from conftest import black_chain, load_script

from cherrypick.weeklyput import clock, db, paper_loop

i69 = load_script("weeklyput_item69")

ENTRY, EXPIRY = date(2026, 11, 6), date(2026, 11, 13)
EXP = EXPIRY.isoformat()


def _paper(**over):
    return {
        "position_id": f"unhedged:SPX:{EXP}",
        "symbol": "SPX",
        "expiration": EXP,
        "strike": 6550.0,
        "entry_value": 210.0,
        "net_pnl": 205.0,
        "net_bp": 3.0,
        "settle_price": 6900.0,
        **over,
    }


def test_compare_reports_the_gap():
    replay = {"strikes": [[6550.0, "P"]], "premium": 200.0, "net": 195.0, "net_bp": 2.9}
    row = i69.compare(_paper(), replay)
    assert row["replayed"] and row["same_strike"]
    assert row["credit_gap"] == pytest.approx(10.0)
    assert row["net_bp_gap"] == pytest.approx(0.1)


def test_a_trade_the_store_lacks_is_listed_never_dropped():
    row = i69.compare(_paper(), {"drop": "entry_missing"})
    assert row == {
        **{k: row[k] for k in ("position_id", "symbol", "expiration", "paper")},
        "replayed": False,
        "reason": "entry_missing",
    }
    s = i69.summarise([row])
    assert s["trades"] == 1 and s["replayed"] == 0 and s["not_replayed"] == 1


def test_summary_means_by_cycle():
    a = i69.compare(_paper(), {"strikes": [[6550.0, "P"]], "premium": 200.0, "net": 195.0, "net_bp": 2.0})
    b = i69.compare(
        _paper(position_id=f"unhedged:SPY:{EXP}", symbol="SPY", strike=655.0, net_bp=1.0),
        {"strikes": [[654.0, "P"]], "premium": 20.0, "net": 19.0, "net_bp": 3.0},
    )
    s = i69.summarise([a, b])
    assert s["cycles"][EXP] == {"paper_bp": 2.0, "replay_bp": 2.5, "names": 2}
    assert s["same_strike_share"] == 0.5


def test_track_reads_the_ledger_without_a_chain_store(tmp_path, cache, config):
    """Run on a machine without the licensed store, every trade is listed not replayed."""
    years = clock.years_to(ENTRY, EXPIRY)
    cache.last("SPX", 6800.0).last("SPY", 680.0)
    cache.chain("SPX", EXP, "SPXW", black_chain(6800.0, 0.16, years, range(6000, 7001, 5)))
    cache.chain("SPY", EXP, "SPY", black_chain(680.0, 0.16, years, range(600, 701)))
    ledger = str(tmp_path / "ledger.db")
    conn = db.connect(ledger)
    paper_loop.run_once(
        config, conn, cache_path=cache.path, when=datetime(2026, 11, 6, 15, 46, tzinfo=clock.ET)
    )
    cache.summary("SPX", "2026-11-16", prev_day_close=6900.0).summary(
        "SPY", "2026-11-16", prev_day_close=690.0
    )
    paper_loop.run_once(
        config, conn, cache_path=cache.path, when=datetime(2026, 11, 16, 9, 31, tzinfo=clock.ET)
    )
    doc = i69.track(ledger)
    assert doc["judged"] is False
    assert doc["summary"]["trades"] == 2 and doc["summary"]["replayed"] == 0
    assert {r["reason"] for r in doc["trades"]} == {"no_chain_store"}
