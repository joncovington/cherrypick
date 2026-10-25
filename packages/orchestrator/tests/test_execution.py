"""B10: the execution record -- fills against the model, overrides flagged, the paper-live gap."""

from __future__ import annotations

from cherrypick.core import killcriteria as kc

from cherrypick.orchestrator import execution as ex


def test_an_order_against_its_model_price():
    o = ex.order(module="meic", session="2026-10-15", placed="2026-10-15 13:02:54-04:00",
                 filled="2026-10-15 13:36:16-04:00", missed=False, expected=0.23, actual=0.22,
                 width=2.0, quantity=1)  # fmt: skip
    assert o["outcome"] == "filled" and o["slip_dollars"] == -1.0 and o["slip_pct_width"] == -0.5
    assert o["minutes_to_fill"] == 33.4
    miss = ex.order(module="meic", session="2026-10-15", placed=None, filled=None, missed=True,
                    expected=0.23, actual=0.22, width=2.0, quantity=1)  # fmt: skip
    assert miss["outcome"] == "missed" and miss["actual"] is None and miss["slip_dollars"] is None


def test_timestamps_in_every_spelling():
    assert ex.parse_ts("2026-06-29 10:54:03 ET").hour == 10
    assert ex.parse_ts("2026-06-30T09:53:02").tzinfo is not None
    assert ex.parse_ts("garbage") is None


def _rec(session, net, credit=100.0, cost=5.0):
    return {"session": session, "net_pnl": net, "entry_credit": credit, "cost": cost}


def test_the_gap_counts_only_sessions_live_was_working():
    live = [_rec("2026-10-15", 40.0, credit=95.0, cost=4.0)]
    paper = [_rec("2026-10-15", 50.0), _rec("2026-10-16", 30.0), _rec("2026-10-20", 99.0)]
    g = ex.gap(live, paper, {"2026-10-15", "2026-10-16"})
    # 10-20: live was not working -- not a miss. 10-16: paper traded, live did not -- a miss.
    assert g["total"] == round((40 - 50) - 30, 2)
    assert g["parts"] == {"missed": -30.0, "fill": -5.0, "costs": 1.0, "other": -6.0}
    assert g["largest"] == "missed"
    assert sum(g["parts"].values()) == g["total"]
    assert ex.gap([], paper, set())["total"] is None


def test_config_commits_flag_only_in_session_changes():
    lines = ["2026-10-15T14:10:00-04:00", "2026-10-15T20:00:00-04:00", "2026-10-17T11:00:00-04:00"]
    out = ex.config_commits("meic", git_log=lambda paths: lines)
    assert [o["session"] for o in out] == ["2026-10-15"]  # evening and Saturday are not overrides


def test_a_cleared_kill_is_an_override(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    kc.latch("meic", "live", {"state": "blocked", "reasons": ["x"]})
    assert kc.clear("meic")
    [o] = ex.kill_clears("meic")
    assert o["kind"] == "kill_cleared"
    assert ex.kill_clears("bwb") == []


def test_weekly_reads_fill_rate_slippage_and_overrides_beside_net():
    orders = [
        ex.order(module="meic", session="2026-10-15", placed=None, filled="x", missed=False, expected=0.3,
                 actual=0.31, width=2.0, quantity=1),
        ex.order(module="meic", session="2026-10-16", placed=None, filled=None, missed=True, expected=0.3,
                 actual=None, width=2.0, quantity=1),
    ]  # fmt: skip
    [w] = ex.weekly(orders, [{"kind": "manual_close", "session": "2026-10-16"}], [_rec("2026-10-15", 31.0)])
    assert w["fill_rate"] == 0.5 and w["slip_dollars"] == 1.0 and w["net"] == 31.0
    assert not w["clean"]
