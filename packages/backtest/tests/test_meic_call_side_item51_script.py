"""Hypothesis 51's study (scripts/meic_call_side_item51.py): the flags, the call side's net, the pairing,
the verdict's checks and floors, the hourly reading rule and the validation. Invented numbers only, no
run, no store.

Each bar is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_spec = importlib.util.spec_from_file_location("meic_call_side_item51", SCRIPTS / "meic_call_side_item51.py")
st = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(st)

ET = ZoneInfo("America/New_York")
START, END = "2022-05-16", "2025-06-30"


def entry(**kw):
    base = {
        "session": "2023-03-01",
        "when": datetime(2023, 3, 1, 14, 0, tzinfo=ET),
        "pnl": 20.0,
        "fees": 4.0,
        "call_pnl": -30.0,
        "put_credit": 1.0,
        "net_credit": 1.5,
        "wing": 10.0,
        "qty": 1,
        "spot": 4000.0,
    }
    return {**base, **kw}


# ---------------------------------------------------------------- the flags


def test_state_boundaries_are_at_one():
    assert st.state_of(20.0, 20.0, 15.0) == "inverted"  # VIX / VIX3M exactly 1
    assert st.state_of(20.0, 20.01, 15.0) == "calm"
    assert st.state_of(20.0, 22.0, 20.0) == "hump"  # VIX9D / VIX exactly 1, VIX under VIX3M
    assert st.state_of(20.0, 22.0, 19.99) == "calm"
    # a hump needs VIX under VIX3M: with VIX at VIX3M the curve is inverted, whatever VIX9D says
    assert st.state_of(22.0, 22.0, 30.0) == "inverted"
    assert st.state_of(None, 22.0, 20.0) is None
    assert st.state_of(20.0, 0.0, 20.0) is None


def test_open_flags_read_the_open_prints_from_invented_csv_rows(tmp_path):
    head = "DATE,OPEN,HIGH,LOW,CLOSE\n"
    (tmp_path / "VIX.csv").write_text(
        head + "03/01/2023,25,26,24,18\n03/02/2023,15,16,14,30\n", encoding="utf-8"
    )
    (tmp_path / "VIX3M.csv").write_text(
        head + "03/01/2023,24,25,23,30\n03/02/2023,20,21,19,10\n", encoding="utf-8"
    )
    (tmp_path / "VIX9D.csv").write_text(
        head + "03/01/2023,10,10,10,10\n03/02/2023,15,15,15,15\n", encoding="utf-8"
    )
    f = {n: st.read_daily(tmp_path / f"{n}.csv") for n in ("VIX", "VIX3M", "VIX9D")}
    flags = st.open_states(f["VIX"], f["VIX3M"], f["VIX9D"])
    # 03/01: open VIX 25 over VIX3M 24 is inverted although its close (18 under 30) is not;
    # 03/02: open VIX9D 15 at VIX 15 with VIX under VIX3M 20 is a hump
    assert flags == {"2023-03-01": "inverted", "2023-03-02": "hump"}


def test_a_missing_print_is_no_flag(tmp_path):
    head = "DATE,OPEN,HIGH,LOW,CLOSE\n"
    (tmp_path / "a.csv").write_text(head + "03/01/2023,25,26,24,18\n", encoding="utf-8")
    a = st.read_daily(tmp_path / "a.csv")
    assert st.open_states(a, {}, a) == {"2023-03-01": None}


def test_the_spx_file_holds_closes_only(tmp_path):
    (tmp_path / "SPX.csv").write_text("DATE,SPX\n03/01/2023,4000.5\n", encoding="utf-8")
    assert st.read_daily(tmp_path / "SPX.csv") == {date(2023, 3, 1): {"spx": 4000.5, "close": 4000.5}}


# ---------------------------------------------------------------- the call side's net


def test_call_side_net_and_pairing():
    e = entry(call_pnl=-30.0, fees=4.0)
    # the call side as it traded: -30 less half the fees (2) is -32; the variant less the base is +32
    assert st.credited_diff(e) == 32.0
    # its fees not credited back: the variant still pays them, so only the call legs' P&L comes off
    assert st.kept_diff(e) == 30.0
    assert st.entry_net(e) == 16.0
    # a call side that made money is a negative difference
    assert st.credited_diff(entry(call_pnl=10.0, fees=4.0)) == -8.0


def test_buying_power_and_net_over_it():
    e = entry()
    assert st.condor_risk(e) == (10 - 1.5) * 100
    assert st.put_risk(e) == (10 - 1.0) * 100
    bp = st.per_buying_power([e, entry(qty=2)])
    base_net = 16.0 + 16.0
    assert bp["condor"] == round(base_net / (850 + 1700), 5)
    assert bp["put_only_fees_credited"] == round((base_net + 32.0 * 2) / (900 + 1800), 5)
    assert bp["put_only_fees_kept"] == round((base_net + 30.0 * 2) / (900 + 1800), 5)
    assert st.per_buying_power([])["condor"] is None


def _ledger(dirpath: Path, day: str, rows):
    """A run's per-session ledger with the columns the study reads. rows: dicts of an entry."""
    db = sqlite3.connect(dirpath / f"{day}.db")
    db.execute(
        "CREATE TABLE ic_trades (trade_date TEXT, entry_time TEXT, pnl REAL, fees REAL, put_credit REAL, "
        "net_credit REAL, wing_width REAL, quantity INTEGER, underlying_price_entry REAL, "
        "ic_order_id TEXT, arm TEXT)"
    )
    db.execute("CREATE TABLE ic_spread_legs (ic_order_id TEXT, side TEXT, pnl REAL)")
    for i, r in enumerate(rows):
        oid = f"{day}-{i}"
        db.execute(
            "INSERT INTO ic_trades VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                day,
                r.get("entry_time", f"{day}T19:00:00+00:00"),
                r.get("pnl", 20.0),
                r.get("fees", 4.0),
                r.get("put_credit", 1.0),
                r.get("net_credit", 1.5),
                r.get("wing", 10.0),
                r.get("qty", 1),
                r.get("spot", 4000.0),
                oid,
                r.get("arm", "be"),
            ),
        )
        if r.get("call_pnl", -30.0) is not None:
            db.execute("INSERT INTO ic_spread_legs VALUES (?,?,?)", (oid, "call", r.get("call_pnl", -30.0)))
        db.execute("INSERT INTO ic_spread_legs VALUES (?,?,?)", (oid, "put", r.get("put_pnl", 50.0)))
    db.commit()
    db.close()


def test_read_entries_sums_call_legs_and_counts_what_it_leaves_out(tmp_path):
    _ledger(tmp_path, "2023-03-01", [{}, {"arm": "other"}, {"call_pnl": None}, {"pnl": None}, {"pnl": 99.0}])
    _ledger(tmp_path, "2026-03-01", [{}])
    rows, counts = st.read_entries(tmp_path, "be", "2022-05-16", "2025-06-30")
    assert (
        len(rows) == 2
    )  # the other arm, the entry with no call leg result, the open one, the out-of-window one
    # the last one's legs (50 - 30 = 20) do not add to its pnl of 99
    assert counts == {"skipped_entries": 2, "leg_sum_mismatch_entries": 1, "quantity_not_one": 0}
    e = rows[0]
    assert e["call_pnl"] == -30.0 and e["fees"] == 4.0
    # the entry time is stored with an offset and read as ET: 19:00 UTC on 2023-03-01 is 14:00 ET
    assert e["when"] == datetime(2023, 3, 1, 14, 0, tzinfo=ET)


def test_net_mismatch_counts_sessions_not_entries():
    es = [
        entry(session="a", pnl=10, fees=2),
        entry(session="a", pnl=10, fees=2),
        entry(session="b", pnl=5, fees=1),
    ]
    recorded = {"a": {"net": 16.0}, "b": {"net": 4.0}, "c": {"net": 9.0}}
    assert st.net_mismatch_sessions(es, recorded) == 0
    assert st.net_mismatch_sessions(es, {**recorded, "b": {"net": -1.0}}) == 1  # a session-level charge
    assert st.net_mismatch_sessions(es, {"c": {"net": 9.0}}) == 0  # a session with no entry is not one


# ---------------------------------------------------------------- the statistics


def test_stats_block_halves_years_and_best_year():
    d = {"2022-06-01": 10.0, "2022-07-01": 14.0, "2023-06-01": 2.0, "2024-06-01": 3.0, "2024-07-01": 5.0}
    s = st.stats_block(d, "2022-05-16", "2024-12-31")
    assert s["n"] == 5 and s["mean"] == 6.8
    assert s["by_year"]["2022"] == {"n": 2, "sum": 24.0, "mean": 12.0}
    assert s["best_year"] == "2022"
    assert s["mean_without_best_year"] == round((2 + 3 + 5) / 3, 2)
    # the midpoint of 2022-05-16..2024-12-31 is 2023-09-08: 2022's two and 2023's one are the first half
    assert s["halves"] == {"first": round((10 + 14 + 2) / 3, 2), "second": 4.0}


def test_best_year_removal_can_empty_the_cell():
    s = st.stats_block({"2023-01-05": 3.0, "2023-02-05": 4.0}, START, END)
    assert s["best_year"] == "2023" and s["mean_without_best_year"] is None


def test_episodes_split_on_more_than_five_sessions_between():
    sessions = [f"2023-01-{d:02d}" for d in range(1, 29)]  # 28 consecutive invented sessions
    flagged = [sessions[0], sessions[6], sessions[7], sessions[14]]
    # 0 and 6 have five sessions between them (1..5): one episode; 7 follows; 14 has six between 7 and 14
    assert st.episodes(flagged, sessions) == [[sessions[0], sessions[6], sessions[7]], [sessions[14]]]
    # one more session between and the first two part
    assert st.episodes([sessions[0], sessions[7]], sessions) == [[sessions[0]], [sessions[7]]]


def test_each_episode_removed_in_turn():
    sessions = [f"2023-{m:02d}-{d:02d}" for m in (1, 2, 3, 4) for d in range(1, 21)]
    d = {sessions[0]: 100.0, sessions[1]: 100.0, sessions[60]: -1.0, sessions[75]: 2.0}
    out = st.without_each_episode(d, sessions, START, END)
    assert [(e["flagged_sessions"], e["n_left"]) for e in out] == [(2, 2), (1, 3), (1, 3)]
    assert out[0]["mean_left"] == 0.5 and out[0]["positive_left"] is True
    assert out[1]["mean_left"] == round((100 + 100 + 2) / 3, 2)
    assert out[2]["mean_left"] == round((100 + 100 - 1) / 3, 2)
    d2 = {sessions[0]: 100.0, sessions[1]: 100.0, sessions[60]: -300.0, sessions[75]: -300.0}
    assert st.without_each_episode(d2, sessions, START, END)[0]["positive_left"] is False


def test_bins_are_the_declared_ones():
    assert [st.bin_of(x) for x in (0.0, 0.0049, 0.005, -0.0099, 0.01, -0.03)] == [
        "<0.5%",
        "<0.5%",
        "0.5-1%",
        "0.5-1%",
        ">=1%",
        ">=1%",
    ]
    out = st.binned([(0.001, 5.0), (0.002, 7.0), (None, 1.0), (0.02, -2.0)])
    assert out["<0.5%"] == {"n": 2, "mean": 6.0, "sum": 12.0}
    assert out["unknown"]["n"] == 1 and ">=1%" in out and "0.5-1%" not in out


def test_the_overnight_gap_uses_the_previous_close():
    closes = {date(2023, 3, 1): {"close": 100.0}, date(2023, 3, 2): {"close": 110.0}}
    assert st.gap_pct("2023-03-02", {"2023-03-02": 101.0}, closes) == pytest.approx(0.01)
    assert st.gap_pct("2023-03-02", {}, closes) is None
    assert st.gap_pct("2023-03-01", {"2023-03-01": 101.0}, closes) is None  # nothing before it


# ---------------------------------------------------------------- the verdict


def passing_cell(n=40, mean=40.0):
    """A cell that clears every check: n sessions over the design window, a steady positive difference."""
    d0, step = date(2022, 6, 1), 1000 // n
    diffs = {(d0 + timedelta(days=step * i)).isoformat(): mean + (i % 7) * 3 for i in range(n)}
    kept = {k: v - 2.0 for k, v in diffs.items()}
    return {
        "paired": st.stats_block(diffs, START, END),
        "fees_not_credited": st.stats_block(kept, START, END),
    }


def test_a_clean_cell_passes_and_each_check_can_fail():
    cells = {"be:inverted": passing_cell()}
    st.judge(cells, 30)
    assert cells["be:inverted"]["verdict"] == "pass"
    assert all(cells["be:inverted"]["checks"].values())

    def failing(mutate, name):
        c = {"x": passing_cell()}
        mutate(c["x"])
        st.judge(c, 30)
        assert c["x"]["verdict"] == "fail" and not c["x"]["checks"][name], name

    failing(lambda c: c["paired"].update(mean=-1.0), "paired_mean_positive")
    failing(lambda c: c["paired"].update(t=1.9), "paired_t_above_bar")
    failing(lambda c: c["paired"].update(p=0.2), "holm")
    failing(lambda c: c["paired"]["halves"].update(second=-0.5), "paired_both_halves_positive")
    failing(lambda c: c["paired"]["halves"].update(first=None), "paired_both_halves_positive")
    failing(lambda c: c["fees_not_credited"].update(mean=-0.1), "positive_with_fees_not_credited")
    failing(lambda c: c["paired"].update(mean_without_best_year=-0.1), "positive_without_best_year")
    failing(lambda c: c["paired"].update(mean_without_best_year=None), "positive_without_best_year")


def test_holm_across_the_six_cells_is_what_decides():
    cells = {f"c{i}": passing_cell() for i in range(6)}
    for c in cells.values():
        c["paired"]["p"] = 0.02  # each alone clears 0.05; the smallest of six needs 0.05 / 6
    st.judge(cells, 30)
    assert {c["verdict"] for c in cells.values()} == {"fail"}
    assert not any(c["checks"]["holm"] for c in cells.values())
    cells["c0"]["paired"]["p"] = 0.005
    for k in cells:
        cells[k].pop("verdict")
    st.judge(cells, 30)
    assert cells["c0"]["verdict"] == "pass"


def test_floors_are_30_for_part_one_and_20_for_part_two():
    assert st.MIN_FLAGGED == {1: 30, 2: 20}
    for part, floor in st.MIN_FLAGGED.items():
        at = {"x": passing_cell(n=floor)}
        below = {"x": passing_cell(n=floor - 1)}
        st.judge(at, floor)
        st.judge(below, floor)
        assert at["x"]["verdict"] == "pass", part
        assert below["x"]["verdict"] == "no verdict" and "flagged sessions" in below["x"]["why"]


def test_a_no_verdict_cell_enters_holm_at_one():
    cells = {"a": passing_cell(), "b": passing_cell(n=5)}
    st.judge(cells, 30)
    assert cells["a"]["verdict"] == "pass" and cells["b"]["verdict"] == "no verdict"


def test_holdout_reads_only_what_the_design_passed():
    design = {
        "arms": {
            "a": {"verdict": "pass"},
            "b": {"verdict": "fail"},
            "c": {"verdict": "pass"},
            "d": {"verdict": "pass"},
        }
    }
    cells = {
        "a": {"paired": {"n": 30, "mean": 5.0}},
        "b": {"paired": {"n": 30, "mean": 5.0}},
        "c": {"paired": {"n": 30, "mean": -5.0}},
        "d": {"paired": {"n": 19, "mean": 5.0}},
    }
    st.judge_holdout(cells, design, 20)
    assert {k: v["verdict"] for k, v in cells.items()} == {
        "a": "pass",
        "b": "fail",
        "c": "fail",
        "d": "no verdict",
    }
    assert cells["b"]["why"] == "design window: fail"
    assert st.overall(cells) == "pass"
    assert st.overall({"x": {"verdict": "no verdict"}}) == "no verdict"
    assert st.overall({"x": {"verdict": "fail"}, "y": {"verdict": "no verdict"}}) == "fail"


def test_periods_of_the_holdout():
    assert st.period_of("2025-06-30") == "design"
    assert st.period_of("2025-07-01") == "judged"
    assert st.period_of("2026-01-23") == "judged"
    assert st.period_of("2026-01-26") == "explored"
    assert st.period_of("2026-10-12") == "explored"
    assert st.period_of("2026-10-13") == "judged"


# ---------------------------------------------------------------- the hourly reading


def bars(symbol, day, closes_by_start):
    return [(symbol, day, hhmm, c) for hhmm, c in closes_by_start.items()]


def test_a_reading_is_the_last_bar_finished_never_one_in_progress():
    # Yahoo's bars start at :30 and run an hour: the 12:30 bar ends 13:30, the 13:30 bar ends 14:30
    s = st.build_series(
        bars("VIX", "2023-03-01", {"11:30": 10.0, "12:30": 11.0, "13:30": 12.0, "14:30": 13.0})
    )
    at = lambda h, m: st.read_at(s, "VIX", datetime(2023, 3, 1, h, m, tzinfo=ET))  # noqa: E731
    assert at(14, 0) == 11.0  # the 13:30 bar is still running at 14:00: its 12.0 must not be read
    assert at(14, 30) == 12.0  # a bar is read at its end, not before
    assert at(14, 29) == 11.0
    assert at(13, 0) == 10.0
    assert at(9, 45) is None  # nothing has finished yet
    assert at(16, 0) == 13.0  # the 14:30 bar ended at 15:30; this series has no 15:30 bar
    # another day's bars are never read
    assert st.read_at(s, "VIX", datetime(2023, 3, 2, 14, 0, tzinfo=ET)) is None


def test_the_broker_bars_start_on_the_hour_and_the_last_ends_at_the_close():
    # the broker's 09:00 bar holds 09:30-10:00; its 15:00 bar ends 16:00
    s = st.build_series(bars("VIX", "2023-03-01", {"09:00": 1.0, "10:00": 2.0, "15:00": 3.0}))
    at = lambda h, m: st.read_at(s, "VIX", datetime(2023, 3, 1, h, m, tzinfo=ET))  # noqa: E731
    assert at(10, 0) == 1.0 and at(11, 0) == 2.0 and at(15, 59) == 2.0 and at(16, 0) == 3.0
    # Yahoo's last bar (15:30) is cut at 16:00, the same instant as the broker's 15:00 one
    assert st.bar_end("2023-03-01", "15:30") == datetime(2023, 3, 1, 16, 0, tzinfo=ET)
    assert st.bar_end("2023-03-01", "09:30") == datetime(2023, 3, 1, 10, 30, tzinfo=ET)


def test_the_curve_at_an_entry_reads_all_three_series():
    rows = []
    for sym, v in (("VIX", 25.0), ("VIX3M", 24.0), ("VIX9D", 30.0)):
        rows += bars(sym, "2023-03-01", {"12:30": v})
    s = st.build_series(rows)
    assert st.curve_at(s, datetime(2023, 3, 1, 14, 0, tzinfo=ET)) == "inverted"
    assert st.curve_at(s, datetime(2023, 3, 1, 13, 29, tzinfo=ET)) is None  # the bars are still running
    # an entry's time given in another zone is converted
    assert st.curve_at(s, datetime(2023, 3, 1, 19, 0, tzinfo=ZoneInfo("UTC"))) == "inverted"


def test_flagging_entries_by_the_curve_at_each_entry():
    rows = []
    for sym, calm, stressed in (("VIX", 20.0, 25.0), ("VIX3M", 24.0, 24.0), ("VIX9D", 15.0, 30.0)):
        rows += bars(sym, "2023-03-01", {"11:30": calm})  # read at 13:00: calm
        rows += bars(sym, "2023-03-01", {"12:30": stressed})  # read from 13:30: inverted
    s = st.build_series(rows)
    early = entry(when=datetime(2023, 3, 1, 13, 0, tzinfo=ET))
    late = entry(when=datetime(2023, 3, 1, 14, 0, tzinfo=ET))
    before_open = entry(when=datetime(2023, 3, 1, 10, 0, tzinfo=ET))
    flagged, unread = st.flag_entries(2, [early, late, before_open], {}, s)
    assert flagged["inverted"] == {"2023-03-01": [late]} and flagged["hump"] == {}
    assert unread == 1
    # part 1 flags every entry of the session by the open
    flagged, unread = st.flag_entries(1, [early, late], {"2023-03-01": "hump"}, None)
    assert flagged["hump"] == {"2023-03-01": [early, late]} and unread == 0


def test_yahoo_rows_keep_the_regular_session_only():
    utc = ZoneInfo("UTC")
    raw = [
        (datetime(2023, 3, 1, 8, 30, tzinfo=utc), 1, 1, 1, 1),  # 03:30 ET, overnight
        (datetime(2023, 3, 1, 14, 30, tzinfo=utc), 1, 1, 1, 15.5),  # 09:30 ET
        (datetime(2023, 3, 1, 20, 30, tzinfo=utc), 1, 1, 1, 16.5),  # 15:30 ET
        (datetime(2023, 3, 1, 21, 30, tzinfo=utc), 1, 1, 1, 17.5),  # 16:30 ET, after the close
        (datetime(2023, 3, 1, 16, 30, tzinfo=utc), 1, 1, 1, 0.0),  # no price
    ]
    out = st.regular_hours_rows("VIX", raw)
    assert [(r["et_day"], r["et_time"], r["close"]) for r in out] == [
        ("2023-03-01", "09:30", 15.5),
        ("2023-03-01", "15:30", 16.5),
    ]
    assert out[0]["ts_ms"] == int(datetime(2023, 3, 1, 14, 30, tzinfo=utc).timestamp() * 1000)


def test_the_yahoo_store_upserts(tmp_path):
    conn = sqlite3.connect(tmp_path / "y.db")
    row = {
        "symbol": "VIX",
        "ts_ms": 1,
        "et_day": "2023-03-01",
        "et_time": "09:30",
        "open": 1,
        "high": 1,
        "low": 1,
        "close": 15.0,
    }
    st.upsert_bars(conn, [row], 1.0)
    st.upsert_bars(conn, [{**row, "close": 16.0}, {**row, "ts_ms": 2, "et_time": "10:30"}], 2.0)
    assert conn.execute("SELECT COUNT(*), MAX(close) FROM yahoo_bars").fetchone() == (2, 16.0)


# ---------------------------------------------------------------- validation


def two_sources(days=("2026-02-02", "2026-02-03"), y_shift=1.0, y_state_flip=False):
    """Invented bars for both sources: the broker's on the hour, Yahoo's at :30, every hour carrying
    the same level so that only a deliberate change separates them."""
    broker, yahoo = [], []
    for d in days:
        for sym, level in (("VIX", 20.0), ("VIX3M", 22.0), ("VIX9D", 18.0)):
            for h in range(9, 16):
                broker.append((sym, d, f"{h:02d}:00", level))
            for h in range(9, 16):
                yv = level * y_shift
                if y_state_flip and sym == "VIX9D":
                    yv = 25.0  # a hump where the broker has a calm curve
                yahoo.append((sym, d, f"{h:02d}:30", yv))
    return st.build_series(yahoo), st.build_series(broker)


SPAN = ("2026-01-26", "2026-11-05")


def test_agreeing_sources_pass_validation():
    y, b = two_sources()
    r = st.validate_series(y, b, SPAN)
    assert r["passed"] is True and r["days"] == 2
    assert r["close_within_1pct"]["share"] == 1.0 and r["state_agreement"]["share"] == 1.0
    assert r["same_end_16_00"]["n"] == 6 and set(r["by_hour"]) == {"11", "12", "13", "14", "15", "16"}


def test_closes_off_by_more_than_one_percent_fail_validation():
    y, b = two_sources(y_shift=1.0105)
    r = st.validate_series(y, b, SPAN)
    assert r["close_within_1pct"]["share"] == 0.0 and r["passed"] is False
    y, b = two_sources(y_shift=1.0095)
    assert st.validate_series(y, b, SPAN)["close_within_1pct"]["share"] == 1.0


def test_the_closes_bar_is_95_percent_of_shared_bars():
    y, b = two_sources(days=tuple(f"2026-02-{d:02d}" for d in range(2, 22)))  # 20 days
    # push one day's 15:30 VIX bar (read only at 16:00) off by 5%: one bad reading of 360
    day = "2026-02-02"
    y["VIX"][day] = [(e, c * 1.05 if (e.hour, e.minute) == (16, 0) else c) for e, c in y["VIX"][day]]
    r = st.validate_series(y, b, SPAN)
    assert (r["close_within_1pct"]["n"], r["close_within_1pct"]["ok"]) == (360, 359)
    assert r["passed"] is True
    # spread the error over enough bars and the share drops below the bar
    for d in y["VIX"]:
        y["VIX"][d] = [(e, c * 1.05) for e, c in y["VIX"][d]]
    assert st.validate_series(y, b, SPAN)["passed"] is False


def test_states_that_disagree_fail_validation_even_when_closes_match_loosely():
    y, b = two_sources(y_state_flip=True)
    r = st.validate_series(y, b, SPAN)
    assert r["state_agreement"]["share"] == 0.0
    assert r["passed"] is False


def test_only_shared_days_in_the_span_count():
    y, b = two_sources(days=("2026-02-02",))
    y2, _ = two_sources(days=("2026-02-02", "2026-02-03"))
    assert st.validate_series(y2, b, SPAN)["days"] == 1
    assert st.validate_series(y, b, ("2026-03-01", "2026-11-05")) is None


def five_minute_broker(days, level_at):
    """Invented broker 5-minute bars, 09:30-15:55 starts, each close `level_at(symbol, end)`."""
    rows = []
    for d in days:
        for sym in ("VIX", "VIX3M", "VIX9D"):
            for m in range(9 * 60 + 30, 16 * 60, 5):
                start = f"{m // 60:02d}:{m % 60:02d}"
                end = st.bar_end(d, start, 5)
                rows.append((sym, d, start, level_at(sym, end)))
    return st.build_series(rows, 5)


def test_validation_compares_each_yahoo_bar_with_the_broker_at_the_same_instant():
    """2026-11-05: the first build read each source at whole hours, so Yahoo's reading was up to 30
    minutes older than the broker's and a moving market failed it. The judged comparison is the same
    instant: each Yahoo bar's close against the broker's 5-minute bar ending when it ends."""
    days = ("2026-09-01", "2026-09-02")
    base = {"VIX": 20.0, "VIX3M": 22.0, "VIX9D": 18.0}

    def trending(sym, end):  # a market that rises every minute: whole-hour readings 30 minutes apart differ
        return base[sym] * (1 + 0.0005 * (end.hour * 60 + end.minute - 570))

    broker = five_minute_broker(days, trending)
    yahoo_rows = []
    for d in days:
        for sym in base:
            for h in range(9, 16):
                start = f"{h:02d}:30"
                yahoo_rows.append((sym, d, start, trending(sym, st.bar_end(d, start))))
    yahoo = st.build_series(yahoo_rows)
    r = st.validate_same_instant(yahoo, broker, SPAN)
    assert r["passed"] and r["close_within_1pct"]["share"] == 1.0 and r["state_agreement"]["share"] == 1.0
    assert r["close_within_1pct"]["n"] == 2 * 3 * 7  # every Yahoo bar of both days, three symbols
    # the same instants with Yahoo 2% off fail
    off = {s: {d: [(e, c * 1.02) for e, c in bars_] for d, bars_ in by.items()} for s, by in yahoo.items()}
    assert st.validate_same_instant(off, broker, SPAN)["passed"] is False
    # no broker 5-minute day in the span: nothing to judge
    assert st.validate_same_instant(yahoo, broker, ("2026-10-01", "2026-11-05")) is None


def test_after_close_prints_are_no_bar_of_the_session():
    # the broker's 16:00 bar holds the indexes' 16:00-16:15 prints; capped at 16:00 it would be read
    # at 16:00 in place of the session's last bar
    s = st.build_series(bars("VIX", "2026-09-01", {"15:00": 3.0, "16:00": 9.0}))
    assert st.read_at(s, "VIX", datetime(2026, 9, 1, 16, 0, tzinfo=ET)) == 3.0
    assert len(s["VIX"]["2026-09-01"]) == 1


def test_part_two_refuses_until_validation_passed(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "out_dir", lambda: tmp_path)
    with pytest.raises(SystemExit, match="validate"):
        st.require_validation()
    (tmp_path / "validation.json").write_text(json.dumps({"passed": False}), encoding="utf-8")
    with pytest.raises(SystemExit, match="did not validate"):
        st.require_validation()
    (tmp_path / "validation.json").write_text(json.dumps({"passed": True}), encoding="utf-8")
    st.require_validation()


# ---------------------------------------------------------------- a study end to end


def invented_world(tmp_path, monkeypatch, n=40, call_loss=True):
    """Three bases with the same invented ledgers: `n` stressed sessions across the design window, an
    entry each, a call side that loses by a varying amount; plus calm sessions between."""
    runs = tmp_path / "runs"
    d0 = date(2022, 6, 1)
    stressed = [(d0 + timedelta(days=28 * i)).isoformat() for i in range(n)]
    calm = [(d0 + timedelta(days=28 * i + 1)).isoformat() for i in range(n)]
    for base in st.BASES:
        d = runs / f"r-{base}"
        d.mkdir(parents=True)
        for i, day in enumerate(stressed + calm):
            call = (-30.0 - (i % 5) * 4 if call_loss else 30.0) if day in stressed else 10.0
            _ledger(
                d, day, [{"arm": base, "call_pnl": call, "pnl": call + 50.0, "put_pnl": 50.0, "spot": 4000.0}]
            )
    cboe = tmp_path / "cboe"
    cboe.mkdir()
    head = "DATE,OPEN,HIGH,LOW,CLOSE\n"

    def us(day):
        y, m, dd = day.split("-")
        return f"{m}/{dd}/{y}"

    vix = vix3m = vix9d = head
    for day in stressed:
        vix += f"{us(day)},25,25,25,25\n"
        vix3m += f"{us(day)},24,24,24,24\n"
        vix9d += f"{us(day)},20,20,20,20\n"
    for day in calm:
        vix += f"{us(day)},15,15,15,15\n"
        vix3m += f"{us(day)},20,20,20,20\n"
        vix9d += f"{us(day)},12,12,12,12\n"
    for name, text in (("VIX", vix), ("VIX3M", vix3m), ("VIX9D", vix9d)):
        (cboe / f"{name}.csv").write_text(text, encoding="utf-8")
    monkeypatch.setattr(st, "cboe_path", lambda n: cboe / f"{n}.csv")
    monkeypatch.setattr(st, "out_dir", lambda: tmp_path)
    monkeypatch.setattr(st, "yahoo_db_path", lambda: tmp_path / "none.db")
    monkeypatch.setattr(st.paths, "results_dir", lambda: tmp_path)
    monkeypatch.setattr(st, "run_for", lambda base, window: f"r-{base}")
    # the recorded session nets are the entries' own, bar three sessions that carry a session-level charge
    recorded = {}
    for i, day in enumerate(stressed + calm):
        call = (-30.0 - (i % 5) * 4 if call_loss else 30.0) if day in stressed else 10.0
        recorded[day] = {"net": call + 50.0 - 4.0 + (5.0 if i < 3 else 0.0), "fees": 4.0, "trades": 1}
    monkeypatch.setattr(st.i43, "session_rows", lambda rid: {rid[2:]: recorded})
    return stressed


def test_part_one_design_end_to_end(tmp_path, monkeypatch):
    invented_world(tmp_path, monkeypatch)
    doc = st.study(1, "design")
    assert doc["window"] == "design" and doc["span"] == [START, END] and doc["part"] == 1
    assert set(doc["arms"]) == {f"{b}:{s}" for b in st.BASES for s in st.STATES}
    cell = doc["arms"]["be:inverted"]
    assert cell["flagged_sessions"] == 40 and cell["paired"]["n"] == 40 and cell["flagged_no_entry"] == 0
    assert cell["entries"] == 40
    # the call side lost 30, 34, ... 46 (mean 38) and half of the 4 in fees comes back: 40 a session
    assert cell["paired"]["mean"] == 40.0 and cell["fees_not_credited"]["mean"] == 38.0
    assert cell["verdict"] == "pass" and doc["verdict"] == "pass"
    assert set(doc["arms"]["w15:inverted"]["paired"]["by_year"]) == {"2022", "2023", "2024", "2025"}
    # no hump sessions were invented, so that cell has nothing to judge
    assert (
        doc["arms"]["be:hump"]["verdict"] == "no verdict" and doc["arms"]["be:hump"]["flagged_sessions"] == 0
    )
    assert doc["ledger_check"]["be"]["sessions"] == 80
    assert doc["ledger_check"]["be"]["net_mismatch_sessions"] == 3
    assert doc["episode_definition"].startswith("runs of flagged sessions")
    md = st.report(doc)
    assert "be:inverted" in md and "Ledger check" in md


def test_a_call_side_that_makes_money_does_not_pass(tmp_path, monkeypatch):
    invented_world(tmp_path, monkeypatch, call_loss=False)
    doc = st.study(1, "design")
    cell = doc["arms"]["be:inverted"]
    assert cell["paired"]["mean"] < 0 and cell["verdict"] == "fail"
    assert doc["verdict"] == "fail"


def test_too_few_flagged_sessions_is_no_verdict(tmp_path, monkeypatch):
    invented_world(tmp_path, monkeypatch, n=29)
    doc = st.study(1, "design")
    assert doc["arms"]["be:inverted"]["verdict"] == "no verdict"
    assert doc["verdict"] == "no verdict"


def test_the_holdout_needs_the_design_result(tmp_path, monkeypatch):
    invented_world(tmp_path, monkeypatch)
    with pytest.raises(SystemExit, match="design result is missing"):
        st.study(1, "holdout")


def test_missing_inputs_refuse(tmp_path, monkeypatch):
    invented_world(tmp_path, monkeypatch)
    monkeypatch.setattr(st, "run_for", lambda base, window: None)
    with pytest.raises(SystemExit, match="no run for"):
        st.study(1, "design")
    monkeypatch.setattr(st, "cboe_path", lambda n: tmp_path / "gone" / f"{n}.csv")
    with pytest.raises(SystemExit, match="missing"):
        st.study(1, "design")
