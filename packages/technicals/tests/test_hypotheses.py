"""Round 2's guards: the halves stay apart, filters and exits re-walk positions, and a pass needs
both an edge and a profit."""

from __future__ import annotations

import sys

from cherrypick.technicals import hypotheses as hy
from cherrypick.technicals import setups

sys.path.insert(0, __file__.rsplit("tests", 1)[0] + "tests")
from test_setups import _readings  # noqa: E402
from test_study_run import _land  # noqa: E402


def test_the_halves_are_fixed_by_ticker_and_split_the_names():
    names = [f"N{k}" for k in range(400)]
    a = [n for n in names if hy.half(n) == "A"]
    assert hy.half("AAPL") == hy.half("AAPL")
    assert 150 < len(a) < 250  # a fair split, not everything in one half


def test_a_filter_re_walks_so_a_blocked_signal_does_not_block_the_next():
    """Find a position during which the setup signalled again (blocked: one position at a time).
    Filter that position's entry out, and the first blocked signal must now be taken -- a filter
    applied to the finished walk would have lost it."""
    r = _readings()
    enter = setups.RULES["reversion"][0]
    for t in setups.run("reversion", r):
        inside = [j for j in range(t.entry + 1, t.exit or len(r.closes)) if enter(r, j)]
        if inside:
            break
    else:
        raise AssertionError("the fixture should have a blocked signal")
    entries = {x.entry for x in setups.run("reversion", r, allow=lambda i: i != t.entry)}
    assert t.entry not in entries and inside[0] in entries


def test_the_ten_session_exit_closes_every_position_ten_sessions_in():
    r = _readings()
    for t in setups.run("pullback", r, leave=hy.EXITS["hold_10"]):
        assert t.exit is None or (t.exit - t.entry == hy.HOLD and t.reason == "time")
    t = setups.exit_from("pullback", r, 100, leave=hy.EXITS["hold_10"])
    assert t.exit == 110


def test_the_no_target_exit_never_exits_at_a_target():
    r = _readings()
    trades = setups.run("pullback", r, leave=hy.EXITS["chandelier_only"])
    assert trades and all(t.reason in (None, "stop") for t in trades)


def _out(edge, p, net, judged=True):
    return {"test": {"edge_r": edge, "p": p, "judged": judged}, "describe": {"expectancy_r": net}}


def test_a_pass_needs_a_significant_edge_and_a_profit():
    out = {
        "edge-and-profit": _out(0.2, 0.0001, 0.1),
        "edge-but-loses": _out(0.2, 0.0001, -0.1),
        "profit-no-edge": _out(0.01, 0.4, 0.3),
        "too-small": _out(0.5, 0.0001, 0.5, judged=False),
    }
    hy.decide(out)
    assert {k for k, v in out.items() if v["passed"]} == {"edge-and-profit"}
    assert out["edge-but-loses"]["significant"] is True and out["edge-but-loses"]["profitable"] is False


def test_stage_a_reads_only_half_a_names(tmp_path):
    path = tmp_path / "history.db"
    _land(path, names=8)
    result = hy.run(
        "A", ids=["mr-above-200", "pullback-hold-10"], workers=2, path=path, progress=lambda m: None
    )
    expected = sum(1 for k in range(8) if hy.half(f"S{k}") == "A")
    assert result["names_in_universe"] == expected
    assert set(result["hypotheses"]) == {"mr-above-200", "pullback-hold-10"}
    held = result["hypotheses"]["pullback-hold-10"]
    if held["describe"]["entries"]:
        # no filter, so every candidate in the same half counts: its own name's other days at least
        assert held["median_baseline_draws"] >= 20


def test_a_baseline_draw_must_pass_the_hypothesis_filter():
    """Random entries for "SPY above its 200-session SMA" are drawn only on such days: a draw on a
    day the market was below is refused, and one on an up day is held to the setup's exit."""
    from test_study import _name

    nm = _name([100.0 + (k % 7) for k in range(60)])
    up_day, down_day = nm.dates[30], nm.dates[31]
    ctx = hy.Context(nm, {up_day: True, down_day: False})
    h = hy.BY_ID["trend-market-up"]
    assert hy.baseline_trade(h, nm, ctx, 31) is None
    t = hy.baseline_trade(h, nm, ctx, 30)
    assert t is not None and t.entry == 30
    assert hy.baseline_trade(hy.BY_ID["pullback-hold-10"], nm, ctx, 31).exit == 41  # no filter; its exit
