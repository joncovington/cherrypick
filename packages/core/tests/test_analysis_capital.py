"""core.analysis.capital: observed peak risk and dated declared allocations
(docs/strategy-analysis-plan.md, Phase 0)."""

import argparse
import json
import sqlite3

import pytest

from cherrypick.core import analysis, ledgers
from cherrypick.core.analysis import __main__ as cli
from cherrypick.core.analysis import capital


def _r(capital_, session, net=10.0, **kw):
    return {"arm": "control", "net_pnl": net, "capital": capital_, "session": session, **kw}


# --------------------------------------------------------------------------- peak risk
def test_multi_day_positions_are_summed_on_the_sessions_they_overlap():
    records = [
        _r(500, "2026-10-05", entry_session="2026-10-01"),
        _r(300, "2026-10-03", entry_session="2026-10-02"),
        _r(400, "2026-10-09", entry_session="2026-10-06"),  # starts after the first closed
    ]
    p = capital.peak_risk(records)
    assert p["peak"] == 800 and p["session"] == "2026-10-02"
    assert p["basis"] == "session"


def test_same_session_positions_overlap_only_where_their_times_do():
    t = "2026-10-16T{}:00-04:00"
    records = [
        _r(1000, "2026-10-16", entry_time=t.format("10:00"), exit_time=t.format("11:00")),
        _r(1000, "2026-10-16", entry_time=t.format("11:00"), exit_time=t.format("12:00")),  # after
        _r(700, "2026-10-16", entry_time=t.format("10:30"), exit_time=t.format("11:30")),
    ]
    p = capital.peak_risk(records)
    # 10:30 holds the first and third (1,700); 11:00 holds the second and third, since the first
    # closed at that instant (1,700). The two 1,000s never overlapped, so 2,700 is never reached.
    assert p["peak"] == 1700
    assert p["basis"] == "intraday" and p["untimed"] == 0


def test_an_untimed_same_session_position_counts_as_open_all_session():
    records = [_r(200, "2026-10-16"), _r(300, "2026-10-16")]
    p = capital.peak_risk(records)
    assert p["peak"] == 500 and p["untimed"] == 2


def test_peak_is_withheld_when_no_trade_records_capital():
    p = capital.peak_risk([_r(None, "2026-10-16")])
    assert p["peak"] is None and "capital" in p["withheld"]
    assert (p["trades"], p["capital_known"]) == (1, 0)


def test_unscored_records_are_not_trades():
    p = capital.peak_risk([_r(100, "2026-10-16"), _r(900, "2026-10-16", net=None)])
    assert p["trades"] == 1 and p["peak"] == 100


# --------------------------------------------------------------------------- allocation file
def _write(tmp_path, data):
    p = tmp_path / "arm_capital.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_load_allocations_sorts_and_skips_notes(tmp_path):
    p = _write(
        tmp_path,
        {
            "_note": "declared account sizes",
            "flies": {
                "control": [
                    {"from": "2026-10-01", "capital": 20000},
                    {"from": "2026-08-21", "capital": 10000},
                ]
            },
        },
    )
    out = capital.load_allocations(p)
    assert [d["from"] for d in out["flies"]["control"]] == ["2026-08-21", "2026-10-01"]


def test_no_file_is_no_declarations(tmp_path):
    assert capital.load_allocations(tmp_path / "absent.json") == {}


@pytest.mark.parametrize(
    "entries, fault",
    [
        ([{"from": "21-08-2026", "capital": 1000}], "is not a date"),
        ([{"from": "2026-08-21", "capital": 0}], "positive number"),
        ([{"from": "2026-08-21", "capital": True}], "positive number"),
        ([{"from": "2026-08-21", "capital": 1}, {"from": "2026-08-21", "capital": 2}], "share a date"),
        ([], "non-empty list"),
    ],
)
def test_a_malformed_file_is_refused_whole(tmp_path, entries, fault):
    p = _write(tmp_path, {"flies": {"control": entries}})
    with pytest.raises(capital.AllocationError, match=fault):
        capital.load_allocations(p)


# --------------------------------------------------------------------------- allocation reading
def test_each_session_is_read_against_the_allocation_in_force_and_earlier_ones_are_uncovered():
    declared = [{"from": "2026-10-02", "capital": 1000.0}, {"from": "2026-10-04", "capital": 2000.0}]
    dated = [("2026-10-01", 50.0), ("2026-10-02", 100.0), ("2026-10-03", -200.0), ("2026-10-04", 200.0)]
    a = capital.allocation_reading(dated, declared)
    assert (a["covered"], a["uncovered"]) == (3, 1)
    # 100/1000 - 200/1000 + 200/2000: the 10-01 session joins nothing.
    assert a["return"] == pytest.approx(0.0)
    assert a["max_drawdown"] == pytest.approx(0.2)
    assert a["max_drawdown_dollars"] == 200.0
    assert a["current"] == 2000.0 and a["capital_over_max_drawdown"] == 10.0


def test_no_declaration_reads_as_none_not_zero():
    a = capital.allocation_reading([("2026-10-01", 50.0)], None)
    assert a["return"] is None and a["current"] is None and a["covered"] == 0


# --------------------------------------------------------------------------- in the arm reading
def test_arm_reading_carries_the_capital_block():
    records = [
        _r(500, "2026-10-01", net=50.0, slippage=None, max_profit=None),
        _r(500, "2026-10-02", net=-150.0, slippage=None, max_profit=None),
    ]
    c = analysis.arm_reading(records)["capital"]
    assert c["peak_risk"]["peak"] == 500
    assert c["return_on_peak_risk"] == -0.2
    assert c["max_drawdown_over_peak_risk"] == 0.3
    assert c["allocation"]["declared"] == []


def test_ratios_need_every_trade_to_carry_capital():
    records = [
        _r(500, "2026-10-01", slippage=None, max_profit=None),
        _r(None, "2026-10-02", slippage=None, max_profit=None),
    ]
    c = analysis.arm_reading(records)["capital"]
    assert c["peak_risk"]["peak"] == 500
    assert c["return_on_peak_risk"] is None and c["max_drawdown_over_peak_risk"] is None


# --------------------------------------------------------------------------- the MEIC trade fields
def test_meic_reader_supplies_entry_and_exit_times():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE ic_trades (symbol TEXT, risk_profile TEXT, pnl REAL, fees REAL, entry_time TEXT, "
        "exit_time TEXT, wing_width REAL, net_credit REAL, quantity INTEGER)"
    )
    conn.execute(
        "INSERT INTO ic_trades VALUES ('SPX','control',100.0,4.0,'2026-10-16 10:00:00-04:00',"
        "'2026-10-16 11:00:00-04:00',10.0,1.5,1)"
    )
    [r] = ledgers.READERS["meic_ic"](conn)
    assert r["entry_time"] == "2026-10-16 10:00:00-04:00" and r["exit_time"] == "2026-10-16 11:00:00-04:00"


# --------------------------------------------------------------------------- CLI
def _bwb_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE bwb_positions (symbol TEXT, arm TEXT, gross_pnl REAL, fees REAL, entry_max_loss REAL, "
        "quantity INTEGER, entry_session TEXT, closed_session TEXT, status TEXT)"
    )
    conn.executemany(
        "INSERT INTO bwb_positions VALUES (?,?,?,?,?,1,?,?,'closed')",
        [("SPX", "control", 120.0, 2.0, 600.0, "2026-10-01", "2026-10-08"),
         ("SPX", "control", -80.0, 2.0, 600.0, "2026-10-02", "2026-10-09")],
    )  # fmt: skip
    conn.commit()
    conn.close()


def _args(tmp_path, **kw):
    base = {"db": str(tmp_path / "b.db"), "schema": "bwb_132", "start": None, "end": None, "era": None,
            "module": None, "allocations": None}  # fmt: skip
    base.update(kw)
    return argparse.Namespace(**base)


def test_cli_applies_a_modules_allocations(tmp_path):
    _bwb_db(tmp_path / "b.db")
    p = _write(tmp_path, {"bwb": {"control": [{"from": "2026-10-01", "capital": 5000}]}})
    out = cli.cmd_read(_args(tmp_path, module="bwb", allocations=str(p)))
    assert out["ok"] and out["allocations"]["error"] is None
    c = out["groups"]["control"]["capital"]
    assert c["allocation"]["current"] == 5000.0
    if c["peak_risk"]["peak"] is not None:
        assert c["peak_risk"]["basis"] == "session"


def test_cli_reports_a_malformed_allocation_file_and_still_reads(tmp_path):
    _bwb_db(tmp_path / "b.db")
    p = tmp_path / "arm_capital.json"
    p.write_text("{not json", encoding="utf-8")
    out = cli.cmd_read(_args(tmp_path, module="bwb", allocations=str(p)))
    assert out["ok"] is True
    assert out["allocations"]["error"]
    assert out["groups"]["control"]["capital"]["allocation"]["declared"] == []
