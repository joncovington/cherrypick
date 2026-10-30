"""Hypothesis 26's study (scripts/share_ladder_item26.py): the bar merge, the fills, the arms, the
control and the verdict. Invented bars only, no store.

Each rule is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_spec = importlib.util.spec_from_file_location("share_ladder_item26", SCRIPTS / "share_ladder_item26.py")
sl = importlib.util.module_from_spec(_spec)
sys.modules["share_ladder_item26"] = sl  # its dataclasses look their module up by name
_spec.loader.exec_module(sl)

B = sl.Bar


def _days(*ohlc: tuple[float, float, float, float]) -> list:
    return [B(f"2026-01-{i + 1:02d}", *x) for i, x in enumerate(ohlc)]


def test_merge_takes_the_broker_unless_missing_or_off_by_more_than_a_percent():
    broker = {"d1": B("d1", 1, 1, 1, 100.0), "d2": B("d2", 1, 1, 1, 300.0)}
    yahoo = {"d1": B("d1", 1, 1, 1, 100.5), "d2": B("d2", 1, 1, 1, 100.0), "d3": B("d3", 1, 1, 1, 50.0)}
    merged = sl.merge_daily(broker, yahoo)
    assert [b.c for b in merged] == [100.0, 100.0, 50.0]  # d1 broker (0.5% off), d2 Yahoo, d3 Yahoo only


def test_once_fills_at_the_level_or_the_open_and_only_once_a_session():
    bar = B("d", 95.0, 96.0, 90.0, 92.0)
    assert sl.entry_fills("once", bar, 100.0, 0.07, None, 3) == [93.0]
    gap = B("d", 91.0, 92.0, 85.0, 86.0)
    assert sl.entry_fills("once", gap, 100.0, 0.07, None, 3) == [91.0]
    assert sl.entry_fills("once", B("d", 99, 99, 93.5, 94), 100.0, 0.07, None, 3) == []


def test_steps_take_each_level_reached_and_chain_steps_from_the_last_buy():
    bar = B("d", 99.0, 99.0, 85.0, 86.0)
    assert sl.entry_fills("steps", bar, 100.0, 0.07, None, 5) == pytest.approx([93.0, 86.0])
    assert sl.entry_fills("steps", bar, 100.0, 0.07, None, 1) == pytest.approx([93.0])
    assert sl.entry_fills("chain", bar, 100.0, 0.07, None, 5) == pytest.approx([93.0, 93.0 * 0.93])
    # With a lot open at 90, the next is 7% under 90 (83.7), whatever the previous close.
    assert sl.entry_fills("chain", B("d", 89, 89, 83.0, 85), 100.0, 0.07, 90.0, 1) == pytest.approx([83.7])
    assert sl.entry_fills("chain", B("d", 89, 89, 84.0, 85), 100.0, 0.07, 90.0, 1) == []


def test_a_lot_is_never_sold_in_the_session_it_was_bought_and_sells_at_target_or_open():
    bars = _days(
        (100, 100, 100, 100),
        (95, 120, 93, 96),  # buys at 93; the high would reach the target, but not the same session
        (96, 96.5, 95, 96),  # target 96.72 not reached
        (98, 99, 97, 98),  # opens over the target: sold at the open
    )
    run = sl.simulate(bars, "2026-01-02", "2026-01-04", "once", 1, cost=0.0)
    shares = int(sl.TRANCHE // 93.0)
    assert run.opened == 1 and run.closed == 1
    assert abs(run.pnl - shares * (98.0 - 93.0)) < 1e-6
    assert run.holds == [2]


def test_the_cap_counts_lots_held_at_the_start_of_the_session():
    bars = _days(
        (100, 100, 100, 100),
        (95, 95, 93, 94),  # lot 1 at 93
        (94, 98, 87, 88),  # lot 1 sells at 96.72; a buy at 87.42 is refused: the slot frees tomorrow
        (88, 88, 80, 81),  # buys at 81.84
    )
    run = sl.simulate(bars, "2026-01-02", "2026-01-04", "once", 1, cost=0.0)
    assert run.opened == 2 and run.closed == 1 and run.open_at_end == 1


def test_costs_come_off_both_sides():
    bars = _days((100, 100, 100, 100), (95, 95, 93, 94), (94, 97, 94, 96))
    free = sl.simulate(bars, "2026-01-02", "2026-01-03", "once", 1, cost=0.0)
    paid = sl.simulate(bars, "2026-01-02", "2026-01-03", "once", 1, cost=0.001)
    shares = int(sl.TRANCHE // 93.0)
    assert abs((free.pnl - paid.pnl) - shares * (93.0 + 93.0 * 1.04) * 0.001) < 1e-6


def test_the_control_buys_at_the_open_of_its_drawn_sessions():
    bars = _days((100, 100, 100, 100), (99, 99, 99, 99), (98, 120, 97, 110))
    run = sl.simulate(bars, "2026-01-02", "2026-01-03", "once", 1, cost=0.0, random_days={"2026-01-02"})
    assert run.opened == 1 and run.closed == 1  # bought at 99, sold at 102.96 next session
    none = sl.simulate(bars, "2026-01-02", "2026-01-03", "once", 1, cost=0.0, random_days=set())
    assert none.opened == 0


def test_reference_close_is_the_latest_four_oclock_close():
    daily = [B("2026-10-21", 1, 1, 1, 103.0), B("2026-10-22", 1, 1, 1, 82.0)]
    hourly = [B("2026-10-22T08:00", 1, 1, 1, 1), B("2026-10-22T16:00", 1, 1, 1, 1)]
    assert sl.reference_closes(hourly, daily) == [103.0, 82.0]


def test_outside_hours_clip_keeps_a_bar_within_three_percent_of_its_body():
    bar = sl.clip(B("2026-10-29T16:00", 102.6, 123.0, 68.5, 91.2))
    assert abs(bar.h - 102.6 * 1.03) < 1e-9 and abs(bar.l - 91.2 * 0.97) < 1e-9


def _measure(edge: float, closed: int = 40) -> dict:
    return {"edge": edge, "closed": closed}


def test_verdict_passes_only_when_every_check_holds_and_is_no_verdict_when_thin():
    def names(n, design_edges, holdout=1.0):
        return {
            f"N{i}": {
                arm: {
                    w: _measure(design_edges[i % len(design_edges)])
                    for w in ("design", "half1", "half2", "stress")
                }
                | {"holdout": _measure(holdout)}
                for arm in sl.ARMS
            }
            for i in range(n)
        }

    good = sl.judge(names(10, [2.0, 2.5, 3.0, 1.5]))
    assert all(v["verdict"] == "pass" for v in good.values())
    bad_holdout = sl.judge(names(10, [2.0, 2.5, 3.0, 1.5], holdout=-1.0))
    assert all(v["verdict"] == "fail" for v in bad_holdout.values())
    noisy = sl.judge(names(10, [5.0, -4.0]))
    assert all(v["verdict"] == "fail" for v in noisy.values())
    thin = sl.judge(names(5, [2.0, 2.5, 3.0, 1.5]))
    assert all(v["verdict"] == "no verdict" for v in thin.values())


def test_one_fund_per_index_and_leverage_keeps_the_most_traded():
    dv = {"UPRO": 270e6, "SPXL": 410e6, "SSO": 200e6, "TNA": 250e6, "URTY": 26e6, "SOXL": 8e9, "AGQ": 2e8}
    kept, dropped = sl.one_per_bet(list(dv), dv)
    assert kept == ["AGQ", "SOXL", "SPXL", "SSO", "TNA"]  # SSO is 2x SPY: a different bet from SPXL
    assert dropped == {"UPRO": "SPXL", "URTY": "TNA"}
    alone, none = sl.one_per_bet(["UPRO", "SOXL"], dv)
    assert alone == ["SOXL", "UPRO"] and none == {}
