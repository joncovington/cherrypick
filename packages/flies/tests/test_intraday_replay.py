"""The intraday agent's historical replay (intraday_replay.py): the gate replayed exactly, a decision
used only after it was made, a store apart from the forward record, and forward figures never
overwritten by replayed ones."""

import json

import pytest

from cherrypick.flies import intraday_advice, intraday_eval, intraday_replay

TTL = 600.0
BAND = 20.0


def _row(pid, side, drift, pnl, kind="fly", at="2026-09-30T11:00:00-04:00"):
    return {
        "position_id": pid,
        "kind": kind,
        "side": side,
        "entry_time": at,
        "entry_trend_value": drift,
        "pnl": pnl,
    }


ENTRY = intraday_replay._epoch("2026-09-30T11:00:00-04:00")


def test_the_minute_grid_is_the_forward_jobs_cadence():
    grid = intraday_replay.minutes("2026-09-30")
    assert len(grid) == 390 and grid[1] - grid[0] == 60
    assert intraday_replay._epoch("2026-09-30T09:30:00-04:00") == grid[0]


@pytest.mark.parametrize(
    ("side", "drift", "refused"),
    [
        ("call", 25.0, True),  # up day: a call vertical needs it to turn down
        ("put", 25.0, False),  # a put vertical completes on the up move
        ("put", -25.0, True),
        ("call", 15.0, False),  # inside the band: not committed
        ("call", None, False),  # no open recorded: the gate fails open
    ],
)
def test_the_gate_is_the_engines_own_test_on_the_stamped_drift(side, drift, refused):
    assert intraday_replay.opposes(_row("x", side, drift, 0.0), BAND) is refused


def test_a_decision_counts_only_after_it_was_made_and_while_it_is_fresh():
    d = [{"as_of": ENTRY - 300, "trend_gate": "off"}, {"as_of": ENTRY + 30, "trend_gate": "on"}]
    assert intraday_replay.gate_at(d, ENTRY, TTL) == "off"  # the later one had not been made yet
    assert intraday_replay.gate_at(d, ENTRY + 600, TTL) == "on"  # the first has expired
    assert intraday_replay.gate_at(d, ENTRY + 700, TTL) is None  # and so has the second
    assert intraday_replay.gate_at([], ENTRY, TTL) is None
    assert intraday_replay.gate_at([{"as_of": ENTRY, "trend_gate": "off"}], ENTRY, TTL) is None  # not before


def test_a_session_under_each_rule():
    rows = [
        _row("ok", "put", 25.0, 200.0),  # with the trend: everyone takes it
        _row("against", "call", 25.0, -260.0, kind="short_vertical"),  # the rule refuses it
    ]
    off = [{"as_of": ENTRY - 60, "trend_gate": "off"}]
    out = intraday_replay.score_session(rows, off, band=BAND, ttl_seconds=TTL)
    assert out["control"]["settled_net"] == -60.0 and out["control"]["stranded"] == 1
    assert out["rule"]["settled_net"] == 200.0 and out["refused_by_rule"] == 1
    # The agent had the gate off: it took the stranded vertical the rule refused.
    assert out["agent"]["settled_net"] == -60.0 and out["admitted_by_agent"] == 1
    on = intraday_replay.score_session(rows, [], band=BAND, ttl_seconds=TTL)
    assert on["agent"] == on["rule"]  # no decision: the agent arm is the rule


def test_resume_and_done():
    recs = [{"as_of": 100.0, "called": True}, {"as_of": 400.0, "called": True}]
    assert intraday_replay.resume_from(recs) == 400.0 and not intraday_replay.is_done(recs)
    assert intraday_replay.is_done([*recs, {intraday_replay.DONE: True}])
    assert intraday_replay.resume_from([]) is None


def test_the_replay_store_is_apart_from_the_forward_record(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    intraday_advice._append("2026-09-30", {"called": True}, intraday_advice.REPLAY_STORE)
    assert intraday_advice.records("2026-09-30") == []
    assert intraday_advice.records("2026-09-30", intraday_advice.REPLAY_STORE) == [{"called": True}]
    assert intraday_eval.load_records() == {}  # the forward readers never see it


def _day(net):
    return {
        "entries": 1,
        "stranded": 0,
        "tagged": 0,
        "settled_net": net,
        "net_closes": net,
        "net_closes_2x": net,
    }


def test_the_replay_never_overwrites_a_forward_session():
    values = {"control": {}, "rule": {"2026-10-07": _day(10.0)}, "agent": {"2026-10-07": _day(20.0)}}
    decided = {"2026-10-07"}
    replay = {
        "sessions": {
            "2026-10-07": {
                "control": _day(0.0),
                "rule": _day(-999.0),
                "agent": _day(-999.0),
                "decided": True,
            },
            "2026-09-30": {"control": _day(5.0), "rule": _day(1.0), "agent": _day(2.0), "decided": True},
            "2026-09-29": {"control": _day(5.0), "rule": _day(1.0), "agent": _day(1.0), "decided": False},
        }
    }
    taken = intraday_eval.merge_replay(values, decided, replay)
    assert taken == ["2026-09-29", "2026-09-30"]
    assert values["rule"]["2026-10-07"]["settled_net"] == 10.0
    assert values["agent"]["2026-09-30"]["settled_net"] == 2.0
    assert decided == {"2026-10-07", "2026-09-30"}
    assert intraday_eval.merge_replay(values, decided, None) == []


def test_score_counts_only_sessions_run_to_the_bell(tmp_path, monkeypatch):
    from cherrypick.flies import db

    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    conn = db.connect(str(tmp_path / "paper.db"))
    for day in ("2026-09-29", "2026-09-30"):
        conn.execute(
            "INSERT INTO fly_positions (position_id, book_id, trade_date, arm, symbol, kind, side, center, wing_width, "
            "quantity, credit, status, pnl, entry_time, entry_trend_value) VALUES (?, ?, ?, 'control', 'SPX', "
            "'short_vertical', 'call', 7720, 5, 1, 2.4, 'settled', -260, ?, 25.0)",
            (f"v-{day}", f"{day}:control:SPX", day, f"{day}T11:00:00-04:00"),
        )
    conn.commit()
    store = intraday_advice.REPLAY_STORE
    intraday_advice._append(
        "2026-09-29", {"as_of": 1.0, "called": True, "model": "opus", "cost_usd": 0.17}, store
    )
    intraday_advice._append(
        "2026-09-30", {"as_of": 1.0, "called": True, "model": "opus", "cost_usd": 0.17}, store
    )
    intraday_advice._append("2026-09-30", {intraday_replay.DONE: True}, store)
    out = intraday_replay.score(conn, {"intraday_agent": {"enabled": True}}, write=True)
    assert list(out["sessions"]) == ["2026-09-30"]  # 09-29 never reached the bell
    s = out["sessions"]["2026-09-30"]
    assert (s["control"]["settled_net"], s["rule"]["settled_net"], s["decided"]) == (-260.0, 0.0, False)
    assert out["scores_closes"] is False and out["calls"] == 1 and out["cost_usd"] == 0.17
    assert json.loads(intraday_replay.result_path().read_text(encoding="utf-8")) == out
