"""Item 95's script (share_ladder_item95.py): invented quotes, and the paper ledger's own ticks where
this machine has them (skipped elsewhere, as in CI)."""

from __future__ import annotations

import importlib.util
import os
import random
import sqlite3
import sys
from pathlib import Path

import pytest
from cherrypick.ladder import rules

from cherrypick.backtest.study import rule as kit_rule

_spec = importlib.util.spec_from_file_location(
    "share_ladder_item95", Path(__file__).resolve().parents[1] / "scripts" / "share_ladder_item95.py"
)
m = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = m  # its dataclasses look their module up while being built
_spec.loader.exec_module(m)


def tick_tape(sessions: list[tuple[str, float, list[tuple[float, float]]]]) -> m.Tape:
    """Each tick its own bar: (day, ref, [(bid, ask), ...])."""
    tape = m.Tape()
    for day, ref, ticks in sessions:
        tape.add_session(day, ref, [[q] for q in ticks])
    return tape


def lots(tape, arm):
    entry, target = m.ARMS[arm]
    return m.run_arm(tape, entry, target)


def test_the_declared_rules_are_complete():
    kit_rule.check_rule(m.RULE)
    assert m.RULE["splits"] == [m.CONTROL_RULE["split"]]
    assert set(m.ARMS) == {"session", "chain", "once", "decay"}
    assert len(m.FUNDS) == 17


def test_the_chain_smokes_then_studies():
    assert [s["step"] for s in m.CHAIN] == ["smoke", "study"]


def test_a_bar_path_takes_the_extreme_nearer_the_open_first():
    assert m.path(10.0, 10.5, 9.9, 10.2) == [10.0, 9.9, 10.5, 10.2]
    assert m.path(10.0, 10.1, 9.5, 9.6) == [10.0, 10.1, 9.5, 9.6]


def test_a_crossing_fills_at_the_level_and_a_gap_at_the_ask():
    # ref 100: level 93.00. The first quote of a session already under it: a gap, filled at the ask.
    gap = tick_tape([("2020-01-02", 100.0, [(89.99, 90.00), (90.0, 90.01)])])
    assert [x.price for x in lots(gap, "once")] == [90.0]
    cross = tick_tape([("2020-01-02", 100.0, [(93.49, 93.50), (92.49, 92.50)])])
    got = lots(cross, "once")
    assert [x.price for x in got] == [93.0] and got[0].level == 93.0 and got[0].shares == 10


def test_session_steps_down_from_the_close_and_sells_inside_the_session():
    day = [(93.49, 93.5), (92.99, 93.0), (86.48, 86.49), (89.95, 89.96), (96.72, 96.73)]
    got = lots(tick_tape([("2020-01-02", 100.0, day)]), "session")
    assert [(x.level, x.price, x.sell) for x in got] == [(93.0, 93.0, 96.72), (86.49, 86.49, 89.95)]
    assert all(x.exit_session == 0 for x in got)


def test_once_buys_once_a_session_and_chain_steps_from_the_last_lot():
    day = [(93.49, 93.5), (92.99, 93.0), (86.48, 86.49)]
    t = tick_tape([("2020-01-02", 100.0, day)])
    assert [x.price for x in lots(t, "once")] == [93.0]
    assert [x.price for x in lots(t, "chain")] == [93.0, 86.49]


def test_chain_keeps_its_anchor_across_sessions_and_session_restarts():
    s1 = ("2020-01-02", 100.0, [(93.49, 93.5), (92.99, 93.0)])
    s2 = ("2020-01-03", 92.0, [(86.0, 86.01), (85.5, 85.51)])  # 92 x 0.93 = 85.56, chain wants 86.49
    t = tick_tape([s1, s2])
    assert [x.price for x in lots(t, "chain")] == [93.0, 86.01]  # gap at the open of day 2, at the ask
    assert [x.price for x in lots(t, "session")] == [93.0, 85.56]


def test_a_lot_never_sells_on_the_bar_it_was_bought_in():
    tape = m.Tape()
    # one bar: buys at 93.00 on its second quote, then trades through the target on its third
    tape.add_session("2020-01-02", 100.0, [[(93.49, 93.5), (92.99, 93.0), (97.0, 97.01)], [(95.0, 95.01)]])
    got = lots(tape, "once")
    assert got[0].exit_i is None


def test_the_cap_holds_three_lots():
    day = [(93.49, 93.5)] + [(p - 0.01, p) for p in (93.0, 86.49, 80.43, 74.80, 69.56)]
    got = lots(tick_tape([("2020-01-02", 100.0, day)]), "session")
    assert len(got) == 3


def test_decay_sells_at_breakeven_after_day_30():
    s1 = ("2020-01-02", 100.0, [(93.49, 93.5), (92.99, 93.0)])
    s2 = ("2020-02-10", 95.0, [(93.10, 93.11)])  # day 39: breakeven for 10 shares at 93.00
    t = tick_tape([s1, s2])
    be = rules.breakeven(93.0, 10)
    got = lots(t, "decay")
    assert got[0].sell == pytest.approx(max(be, 93.10)) and got[0].exit_session == 1
    assert lots(t, "once")[0].exit_i is None


def test_the_session_search_matches_a_plain_scan():
    rng = random.Random(1)
    tape = m.Tape()
    for k in range(300):
        p = rng.uniform(10, 20)
        tape.add_session(f"2020-{1 + k // 28:02d}-{1 + k % 28:02d}", 15.0, [[(p, p + 0.01)]])
    for _ in range(200):
        k, stop, t = rng.randrange(300), rng.randrange(300) + 1, rng.uniform(10, 21)
        plain = next((j for j in range(k, stop) if tape.sessions[j].max_bid >= t), None)
        assert tape.first_session_reaching(k, stop, t) == plain


def test_a_session_disagreeing_with_the_daily_close_is_skipped():
    closes = {"2020-01-02": 100.0, "2020-01-03": 101.0, "2020-01-06": 50.0}
    rows = [
        ("2020-01-03T10:00", 101.0, 101.0, 101.0, 101.0),
        ("2020-01-06T10:00", 100.0, 100.0, 100.0, 100.0),  # 100 against a daily 50: skipped
    ]
    tape = m.tape_from_bars(rows, closes, 0.005, "2020-01-01")
    assert [s.day for s in tape.sessions] == ["2020-01-03"]
    assert tape.skipped["disagrees"] == 1


def test_the_control_draws_as_many_lots_a_year_and_keeps_the_cap():
    sessions = [(f"2020-01-{d:02d}", 100.0, [(99.0, 99.01), (99.5, 99.51)]) for d in range(1, 29)]
    tape = tick_tape(sessions)
    got = m.control_lots(tape, {"2020": 10}, "fixed", random.Random(3), 0, len(tape.sessions))
    assert len(got) == 3  # nothing reaches +4%, so the cap holds the rest out
    assert len({x.session for x in got}) == 3


def test_both_rules_must_pass():
    kit = {"verdict": {a: "pass" for a in m.ARMS}}
    control = {a: {"verdict": "pass"} for a in m.ARMS}
    control["chain"]["verdict"] = "fail"
    control["once"]["verdict"] = "no verdict"
    assert m.combined(kit, control) == {
        "session": "pass",
        "chain": "fail",
        "once": "no verdict",
        "decay": "pass",
    }


def test_raw_prices_take_each_split_from_its_ex_date():
    # adjusted closes 50, 50, 51; a 2-for-1 on 01-06: raw 100, 100 before, 51 after
    closes = {"2020-01-02": 50.0, "2020-01-03": 50.0, "2020-01-06": 51.0}
    rows = [("2020-01-03T10:00", 100.0, 100.0, 100.0, 100.0), ("2020-01-06T10:00", 51.0, 51.0, 51.0, 51.0)]
    tape = m.tape_from_bars(rows, closes, 0.005, "2020-01-01", splits={"2020-01-06": 2.0})
    assert [(s.day, s.f, s.ref) for s in tape.sessions] == [
        ("2020-01-03", 2.0, 100.0),
        ("2020-01-06", 1.0, 50.0),
    ]
    assert tape.skipped["disagrees"] == 0


def test_a_lot_held_across_a_split_sells_at_its_restated_target():
    tape = m.Tape()
    tape.add_session("2020-01-02", 100.0, [[(93.49, 93.5)], [(92.99, 93.0)]], f=2.0)
    # after a 2-for-1 the 96.72 target is 48.36; a bid of 48.36 sells it, worth 96.72 a buy-term share
    tape.add_session("2020-01-03", 47.0, [[(48.0, 48.01)], [(48.36, 48.37)]], f=1.0)
    lot = lots(tape, "once")[0]
    assert (lot.price, lot.shares, lot.sell, lot.exit_session) == (93.0, 10, 48.36, 1)
    row = m.lot_money(tape, lot, len(tape.sessions))
    assert row["net"] == rules.money(10, 93.0, 96.72)["net_pnl"]


def test_the_chain_anchor_carries_across_a_split():
    tape = m.Tape()
    tape.add_session("2020-01-02", 100.0, [[(93.49, 93.5)], [(92.99, 93.0)]], f=2.0)
    # the next chain level, 86.49 before the split, is 43.24 after a 2-for-1 (46.5 x 0.93 = 43.245)
    tape.add_session("2020-01-03", 46.0, [[(43.5, 43.51)], [(43.23, 43.24)]], f=1.0)
    assert [x.price for x in lots(tape, "chain")] == [93.0, 43.24]


PAPER = Path(os.path.expanduser("~/.cherrypick/data/ladder/paper_trades.db"))


@pytest.mark.skipif(not PAPER.exists(), reason="no paper ledger on this machine")
def test_the_replay_gives_the_lots_paper_recorded():
    """The paper ledger's own ticks, each its own bar, through run_arm: the same lots, prices,
    sizes and sales as the paper loop recorded."""
    conn = sqlite3.connect(f"file:{PAPER}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    for symbol in [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM ladder_positions")]:
        ticks = conn.execute(
            "SELECT trade_date, ts, bid, ask, ref_close FROM ladder_snapshots "
            "WHERE symbol = ? AND kind = 'tick' AND status = 'ok' ORDER BY id",
            (symbol,),
        ).fetchall()
        by_day: dict[str, list] = {}
        for t in ticks:
            by_day.setdefault(t["trade_date"], []).append(t)
        tape, stamps = m.Tape(), []
        for day, ts in sorted(by_day.items()):
            ref = next((t["ref_close"] for t in ts if t["ref_close"]), None)
            if ref is None:
                continue
            tape.add_session(day, ref, [[(t["bid"], t["ask"])] for t in ts])
            stamps += [t["ts"] for t in ts]
        for arm in m.ARMS:
            want = [
                (r["buy_time"], r["buy_price"], r["quantity"], r["sell_time"], r["sell_price"])
                for r in conn.execute(
                    "SELECT * FROM ladder_positions WHERE arm = ? AND symbol = ? ORDER BY buy_time, id",
                    (arm, symbol),
                )
            ]
            got = [
                (stamps[x.i], x.price, x.shares, stamps[x.exit_i] if x.exit_i is not None else None, x.sell)
                for x in lots(tape, arm)
            ]
            assert got == want, (symbol, arm)
    conn.close()


def test_the_window_ends_at_the_last_whole_month():
    from datetime import date

    assert m.last_whole_month_end(date(2026, 11, 19)) == "2026-10-31"
    assert m.last_whole_month_end(date(2027, 1, 2)) == "2026-12-31"
