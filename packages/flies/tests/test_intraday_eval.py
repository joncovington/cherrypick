"""The intraday agent's qualification (intraday_eval.py): what the evidence lets live offer."""

import json

import pytest

from cherrypick.flies import close_tags, db, intraday_eval

ARMS = {"rule": "trend-rule", "agent": "intraday-agent"}


def _values(nets, stranded=0, entries=4, closes=None):
    return {
        s: {
            "entries": entries,
            "stranded": stranded,
            "tagged": 0,
            "settled_net": n,
            "net_closes": n if closes is None else closes[i],
            "net_closes_2x": n if closes is None else closes[i],
        }
        for i, (s, n) in enumerate(nets.items())
    }


def _summary(sessions, shadow=(), episodes=0):
    return {"paper_sessions": list(sessions), "shadow_sessions": list(shadow), "episodes": episodes}


def test_t_is_exact_to_30_and_never_more_generous_past_it():
    assert intraday_eval.t95(1) == 6.314 and intraday_eval.t95(30) == 1.697
    assert intraday_eval.t95(35) == 1.697 and intraday_eval.t95(41) == 1.684  # exact 1.690 and 1.683
    with pytest.raises(ValueError):
        intraday_eval.t95(0)


def test_paired_stats_bound_and_sessions_needed():
    out = intraday_eval.paired_stats([100.0, 300.0, 200.0, 200.0])
    assert out["mean"] == 200.0 and out["sd"] == pytest.approx(81.65, abs=0.01)
    assert out["lower_95"] == pytest.approx(200.0 - 2.353 * 81.6497 / 2, abs=0.01)
    assert out["sessions_needed"] == 5  # ceil((2.487 * 81.65 / 100) ** 2) = ceil(4.12)
    assert intraday_eval.paired_stats([50.0])["lower_95"] is None


def test_record_summary_counts_admissible_calls_and_distinct_episodes():
    pack = {
        "flies": {
            "positions": [{"id": "p1", "state": "open_vertical"}, {"id": "p2", "state": "completed"}],
            "_position_ids": {"p1": "real-1", "p2": "real-2"},
        }
    }
    recs = {
        "2026-10-06": [
            {"target": "paper", "called": True, "ok": True, "pack": pack},
            {"target": "paper", "called": True, "ok": True, "pack": pack},  # the same episode again
            {"target": "live", "called": True, "ok": True, "pack": pack},
        ],
        "2026-10-07": [
            {"target": "paper", "called": False, "skipped": "no_trigger"},
            {"target": "paper", "called": True, "ok": False, "pack": pack},
        ],
    }
    assert intraday_eval.record_summary(recs) == {
        "paper_sessions": ["2026-10-06"],
        "shadow_sessions": ["2026-10-06"],
        "episodes": 1,
    }


def _days(n):
    return [f"2026-11-{d:02d}" for d in range(1, n + 1)]


def test_nothing_past_shadow_unlocks_while_a_criterion_cannot_be_scored():
    days = _days(25)
    rule = _values(dict.fromkeys(days, 0.0), stranded=2)
    agent = _values({d: 150.0 + (i % 3) * 10 for i, d in enumerate(days)}, stranded=1)
    out = intraday_eval.evaluate(
        rule,
        agent,
        _summary(days, shadow=days[:6], episodes=80),
        live_mode_max="gates_and_closures",
        generated_at="t",
        arms=ARMS,
    )
    by = {c["id"]: c for c in out["criteria"]}
    assert by["decision_sessions"]["pass"] and by["beats_rule"]["pass"] and by["strands_no_more"]["pass"]
    assert by["live_shadow"]["pass"] is None  # scored from step 4
    assert out["unlocked"]["gates"] is False and out["offered_modes"] == ["off", "shadow"]


def test_a_noisy_positive_mean_does_not_pass_the_one_sided_bound():
    days = _days(4)
    rule = _values(dict.fromkeys(days, 0.0))
    agent = _values(dict(zip(days, (400.0, -300.0, 350.0, -250.0), strict=True)))  # mean +50, sd ~380
    out = intraday_eval.evaluate(
        rule, agent, _summary(days), live_mode_max="shadow", generated_at="t", arms=ARMS
    )
    beats = next(c for c in out["criteria"] if c["id"] == "beats_rule")
    assert out["gates"]["mean"] == 50.0 and beats["value"] < 0 and beats["pass"] is False


def test_only_sessions_with_a_decision_are_paired_and_the_cap_binds():
    rule = _values({"a": 0.0, "b": 0.0})
    agent = _values({"a": 10.0, "b": 99.0})
    out = intraday_eval.evaluate(
        rule, agent, _summary(["a"]), live_mode_max="off", generated_at="t", arms=ARMS
    )
    assert out["sessions"]["paired"] == ["a"] and out["gates"]["mean"] == 10.0
    assert out["offered_modes"] == ["off"]


@pytest.fixture
def conn(tmp_path):
    return db.connect(str(tmp_path / "paper.db"))


def _row(c, pid, arm, kind, pnl, day="2026-10-06", **tag):
    c.execute(
        "INSERT INTO fly_positions (position_id, book_id, trade_date, arm, symbol, kind, side, center, wing_width, "
        "quantity, credit, status, gross_pnl, fees, pnl) VALUES (?, ?, ?, ?, 'SPX', ?, 'call', 7720, 5, 1, 2.4, "
        "'settled', ?, 0, ?)",
        (pid, f"{day}:{arm}:SPX", day, arm, kind, pnl, pnl),
    )
    for k, v in tag.items():
        c.execute(f"UPDATE fly_positions SET {k} = ? WHERE position_id = ?", (v, pid))


def test_session_values_count_strands_and_value_tagged_closes(conn):
    _row(conn, "f", "intraday-agent", "fly", 240.0)
    fees = close_tags.round_trip_fees("SPX", 1)
    _row(conn, "v", "intraday-agent", "short_vertical", -260.0,
         close_tag_natural=3.0, close_tag_mid=2.6, close_tag_fees=fees)  # fmt: skip
    s = intraday_eval.session_values(conn, "intraday-agent")["2026-10-06"]
    assert (s["entries"], s["stranded"], s["tagged"], s["settled_net"]) == (2, 1, 1, -20.0)
    assert s["net_closes"] == pytest.approx(240.0 + (2.4 - 3.0) * 100 - fees, abs=0.01)
    assert s["net_closes_2x"] == pytest.approx(240.0 + (2.4 - 3.4) * 100 - fees, abs=0.01)


def test_run_writes_the_file_the_console_reads(conn, tmp_path, monkeypatch):
    target = tmp_path / "q.json"
    monkeypatch.setattr(intraday_eval, "qualification_path", lambda: target)
    monkeypatch.setattr(intraday_eval, "load_records", dict)
    monkeypatch.setattr(intraday_eval, "live_db_path", lambda: tmp_path / "no-live.db")
    _row(conn, "c", "control", "fly", 100.0)
    _row(conn, "r", "trend-rule", "short_vertical", -260.0, close_tag_natural=3.0, close_tag_mid=2.6)
    conn.commit()
    out = intraday_eval.run(conn, {"intraday_agent": {"enabled": True}}, write=True)
    assert json.loads(target.read_text(encoding="utf-8")) == out
    assert out["arms"] == {"control": "control", **ARMS, "live": "control"} and out["offered_modes"] == [
        "off",
        "shadow",
    ]
    assert out["trend_band_points"] == 20.0
    (day,) = out["per_session"]
    assert day["session"] == "2026-10-06" and day["decided"] is False and day["agent"] is None
    assert day["control"]["settled_net"] == 100.0 and day["rule"]["stranded"] == 1
    (close,) = out["tagged_closes"]
    assert (close["arm"], close["position_id"], close["natural"], close["fees"]) == (
        "trend-rule",
        "r",
        3.0,
        None,
    )


def test_spend_counts_checks_calls_and_cost_by_model():
    recs = {
        "2026-10-06": [
            {"target": "paper", "called": False},
            {"target": "paper", "called": True, "ok": True, "model": "opus", "cost_usd": 0.17},
            {"target": "paper", "called": True, "ok": False, "model": "opus", "cost_usd": 0.15},
            {"target": "live", "called": True, "ok": True, "model": "sonnet", "cost_usd": 0.05},
        ]
    }
    paper, live = intraday_eval.spend(recs)
    assert (paper["target"], paper["checks"], paper["calls"], paper["ok"], paper["cost_usd"]) == (
        "paper", 3, 2, 1, 0.32,
    )  # fmt: skip
    assert paper["by_model"] == {"opus": {"calls": 2, "cost_usd": 0.32}}
    assert (live["target"], live["by_model"]) == ("live", {"sonnet": {"calls": 1, "cost_usd": 0.05}})
