"""The Builder's run card, from a result's own trades: every row adds up, trades close in order, the
drawdown is the closed trades', skips leave `entered` out, and a change is the stored automation's."""

from __future__ import annotations

import json

import pytest

from cherrypick.backtest import automationcards as ac
from cherrypick.backtest import paths, readout


def _t(session, exit_session, net, *, gross=None, fees=1.3, settlement=0.0, slip=0.5, minute="15:00"):
    gross = net + fees + settlement + slip if gross is None else gross
    return {"arm": "mine", "session": session, "exit_session": exit_session, "exit_minute": minute,
            "gross": gross, "fees": fees, "settlement": settlement,
            "slippage": {"verdict": slip, "stress": slip * 2, "mid": 0.0},
            "net": {"verdict": net, "stress": net - slip, "mid": net + slip}, "legs": []}  # fmt: skip


TRADES = [
    _t("2025-12-30", "2026-01-02", -50.0),  # closed in 2026
    _t("2025-03-03", "2025-03-03", 100.0),
    _t("2025-06-02", "2025-06-02", -80.0, settlement=12.0),
    _t("2026-02-02", "2026-02-02", 40.0),
]


def test_each_year_and_all_add_up_and_trades_fall_in_their_exit_year():
    rows = ac.years(TRADES)
    assert [r["period"] for r in rows] == ["2025", "2026", "All"]
    for r in rows:
        assert r["gross"] - r["fees"] - r["settlement"] - r["slippage"] == pytest.approx(r["net"])
    assert rows[0]["trades"] == 2 and rows[1]["trades"] == 2
    assert rows[0]["net"] + rows[1]["net"] == pytest.approx(rows[2]["net"]) == pytest.approx(10.0)


def test_the_net_is_the_verdict_kinds_and_the_drawdown_the_closed_trades():
    tiles = ac.tiles(TRADES, 40)
    assert tiles["net"] == pytest.approx(10.0)
    # by exit: +100, -80, -50, +40 -> peak 100, worst 100-130 = -130
    assert tiles["max_dd"] == pytest.approx(-130.0)
    assert tiles["win_rate"] == pytest.approx(0.5) and tiles["per_trade"] == pytest.approx(2.5)
    assert tiles["net_over_dd"] == pytest.approx(round(10 / 130, 2))
    curve = ac.curve(TRADES)
    assert [p["net"] for p in curve] == [100.0, 20.0, -30.0, 10.0]
    assert [p["dd"] for p in curve] == [0.0, -80.0, -130.0, -90.0]


def test_net_at_mid_is_the_recorded_mid_net_and_reconciles_with_the_slippage():
    tiles, rows = ac.tiles(TRADES, 40), ac.years(TRADES)
    assert tiles["net_mid"] == pytest.approx(10.0 + 4 * 0.5)
    for r in rows:
        assert r["net_mid"] == pytest.approx(r["net"] + r["slippage"])  # P&L before slippage
    # Read as recorded, never derived: a trade whose mid net says otherwise is shown as it says.
    odd = [dict(TRADES[0], net={"verdict": -50.0, "mid": -10.0})]
    assert ac.tiles(odd, 1)["net_mid"] == pytest.approx(-10.0)


def test_a_long_curve_is_thinned_keeping_both_ends():
    many = [_t("2025-01-02", f"2025-01-{(i % 28) + 1:02d}", 1.0, minute=f"{i:05d}") for i in range(1000)]
    curve = ac.curve(many, 50)
    assert len(curve) == 50 and curve[0]["i"] == 1 and curve[-1]["i"] == 1000


def test_skips_leave_entered_out_most_first():
    assert ac.skips({"entered": 30, "no_signal": 4, "gate_iv_rank": 9}) == [
        {"reason": "gate_iv_rank", "sessions": 9},
        {"reason": "no_signal", "sessions": 4},
    ]


def test_a_change_is_a_key_of_the_stored_automation_never_the_switch_or_a_note():
    before = {"enabled": False, "note": "a", "exit": {"stop": 1.5}, "gates": {"iv_rank": [20, 60]}}
    after = {"enabled": True, "note": "b", "exit": {"stop": 2.0}, "gates": {"iv_rank": [20, 60], "skew": 6.0}}
    assert ac.changes(before, after) == [
        {"key": "exit.stop", "before": 1.5, "after": 2.0},
        {"key": "gates.skew", "before": None, "after": 6.0},
    ]
    assert ac.changes(None, after) == []


def _write(d, name, generated, arm, trades=TRADES, **over):
    d.mkdir(parents=True, exist_ok=True)
    doc = {"name": name, "symbol": "SPX", "source": "recorded-0dte", "licensed": False,
           "generated_at": generated,
           "arms": {"mine": arm}, "summary": {"mine": {"sessions": 40, "trades": len(trades), "net": 10.0,
           "reasons": {"entered": 4, "no_signal": 36}}}, "trades": trades, **over}  # fmt: skip
    (d / f"{name}.json").write_text(json.dumps(doc), encoding="utf-8")


def test_the_list_carries_each_runs_sparkline_and_what_it_changed(tmp_path, monkeypatch):
    own = tmp_path / "own"
    monkeypatch.setattr(paths, "automations_dir", lambda licensed: tmp_path / "lic" if licensed else own)
    _write(own, "builder-mine-2611151730", "2026-11-15T17:30", {"exit": {"stop": 1.5}})
    _write(own, "builder-mine-2611161730", "2026-11-16T17:30", {"exit": {"stop": 2.0}})
    runs = readout.automation_runs("mine")["runs"]
    assert [r["run"] for r in runs] == ["builder-mine-2611161730", "builder-mine-2611151730"]
    assert runs[0]["changes"] == [{"key": "exit.stop", "before": 1.5, "after": 2.0}]
    assert runs[1]["changes"] == []
    assert runs[0]["spark"][-1] == pytest.approx(10.0) and runs[0]["net"] == pytest.approx(10.0)
    assert "trades" not in runs[0]


def test_one_run_reads_whole_and_an_unknown_one_is_refused(tmp_path, monkeypatch):
    lic = tmp_path / "lic"
    monkeypatch.setattr(paths, "automations_dir", lambda licensed: lic if licensed else tmp_path / "own")
    _write(lic, "builder-mine-2611151730", "2026-11-15T17:30", {"exit": {"stop": 1.5}})
    _write(lic, "builder-mine-2611161730", "2026-11-16T17:30", {"exit": {"stop": 2.0}})
    out = readout.automation_run("mine", "builder-mine-2611161730")
    assert out["ok"] is True and out["licensed"] is True and out["previous"] == "builder-mine-2611151730"
    assert out["tiles"]["trades"] == 4 and out["skips"] == [{"reason": "no_signal", "sessions": 36}]
    assert [t["exit_session"] for t in out["trades"]] == [
        "2025-03-03",
        "2025-06-02",
        "2026-01-02",
        "2026-02-02",
    ]
    assert out["changes"] == [{"key": "exit.stop", "before": 1.5, "after": 2.0}]
    assert readout.automation_run("mine", "builder-mine-2611171730")["ok"] is False
    assert readout.automation_run("../x", "builder-mine-2611161730")["ok"] is False


def test_trades_closing_the_same_session_are_ordered_by_their_minute():
    late = _t("2025-03-03", "2025-03-03", 5.0, minute=930)
    early = _t("2025-03-03", "2025-03-03", -5.0, minute=600)
    clock = _t("2025-03-03", "2025-03-03", 1.0, minute="12:00")
    assert [t["net"]["verdict"] for t in ac.ordered([late, clock, early])] == [-5.0, 1.0, 5.0]
