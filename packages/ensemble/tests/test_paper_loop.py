"""The paper loop against a stream cache built through the real DDL: entries, refusals, windows, exits,
settlement and the replay record. Invented numbers throughout; no network, no broker, no order."""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

from cachebuilder import CacheBuilder
from cherrypick.core.clock import ET
from market import CALENDAR

from cherrypick.ensemble import arms as arms_mod
from cherrypick.ensemble import config as config_mod
from cherrypick.ensemble import db, paper_loop, record

DAY = "2026-11-16"  # a Monday
NEXT = "2026-11-17"
ZERO_DTE_YEARS = 6.0 / (24 * 365)


def at(hour, minute, day=DAY):
    y, m, d = (int(x) for x in day.split("-"))
    return datetime(y, m, d, hour, minute, tzinfo=ET)


def make_config(keep, overrides=None):
    """Only the arms in `keep` enabled, the rest of the shipped arms switched off."""
    arms = {n: {"enabled": False} for n in arms_mod.ARMS if n not in keep}
    arms.update({n: {"enabled": True} for n in keep})
    for name, change in (overrides or {}).items():
        arms[name] = {**arms.get(name, {}), **change}
    return {"defaults": {}, "arms": arms}


def world(tmp_path, keep=("iron-fly-1000",), overrides=None, *, spot=6000.0, gld=False):
    cache = CacheBuilder(tmp_path / "cache.db")
    cache.last("SPX", spot).last("VIX", 16.0)
    cache.history("SPX", DAY, [5800.0 + i for i in range(39)] + [6000.0])
    cache.history("VIX", DAY, [16.0, 16.0, 16.0])
    cache.summary("SPX", DAY, day_open=6000.0)
    cache.listed("SPX", [DAY, NEXT])
    cache.chain("SPX", DAY, "SPXW", spot, ZERO_DTE_YEARS)
    if gld:
        cache.last("GLD", 300.0)
        cache.history("GLD", DAY, [290.0 + i * 0.1 for i in range(39)] + [300.0])
        cache.listed("GLD", [DAY, NEXT])
        cache.chain("GLD", NEXT, "GLD", 300.0, 1.0 / 365, step=1.0, width=40.0)
    config = make_config(keep, overrides)
    arms = config_mod.load_arms(config)
    conn = db.connect(str(tmp_path / "paper.db"))
    return SimpleNamespace(
        cache=cache,
        cache_path=cache.path,
        config=config,
        arms=arms,
        conn=conn,
        replay=tmp_path / "replay",
    )


def tick(w, when, **kw):
    return paper_loop.run_once(
        w.config, w.arms, w.conn, cache_path=w.cache_path, when=when, evts=CALENDAR, replay_to=w.replay, **kw
    )


def test_an_arm_enters_records_everything_and_settles(tmp_path):
    w = world(tmp_path)
    out = tick(w, at(10, 0))
    assert out["entries"] == {"iron-fly-1000": "entered"}

    pos = db.position(w.conn, f"iron-fly-1000:{DAY}")
    assert pos["status"] == "open" and pos["quantity"] >= 1 and pos["expiration"] == DAY
    legs = db.legs(w.conn, pos["position_id"])
    assert len(legs) == 4 and all(g["streamer_symbol"].startswith(".SPXW") for g in legs)
    assert db.entry(w.conn, "iron-fly-1000", "SPX", DAY)["outcome"] == "entered"
    decision = w.conn.execute("SELECT * FROM ensemble_decisions WHERE reason = 'enter'").fetchone()
    assert decision["accepted"] == 1
    # an arm that has entered is not decided again in its window
    assert tick(w, at(10, 1))["entries"] == {} and len(db.positions(w.conn)) == 1

    # the decision input was recorded and runs again to the same decisions
    files = list(w.replay.glob("*.jsonl"))
    assert [f.name for f in files] == [f"{DAY}.jsonl"]
    result = record.replay_file(files[0], CALENDAR, costs=w.config)
    assert result and all(r["recorded"] == r["replayed"] for r in result)

    # the expiry's close posts the next morning: settled at it, the money adding up
    w.cache.summary("SPX", NEXT, prev_close=6003.0)
    paper_loop.run_settlements(w.config, w.conn, cache_path=w.cache_path, when=at(9, 31, NEXT))
    done = db.position(w.conn, pos["position_id"])
    assert (
        done["status"] == "closed" and done["exit_reason"] == "settlement" and done["settle_price"] == 6003.0
    )
    assert round(done["entry_value"] + done["exit_value"], 2) == done["gross_pnl"]
    assert done["net_pnl"] == round(
        done["gross_pnl"] - done["fees"] - done["settlement"] - done["slippage"], 2
    )
    assert done["fees"] >= done["entry_fees"] > 0 and done["slippage"] > 0
    assert {g["status"] for g in db.legs(w.conn, pos["position_id"])} == {"closed"}


def test_a_refused_arm_records_the_gate_that_blocked_it(tmp_path):
    w = world(tmp_path, spot=6100.0)  # 1.65 standard deviations up: past the arm's +/-1.1
    out = tick(w, at(10, 0))
    assert out["entries"] == {"iron-fly-1000": "gate:sd_change"}
    row = w.conn.execute("SELECT * FROM ensemble_decisions WHERE arm = 'iron-fly-1000'").fetchone()
    assert row["reason"] == "gate:sd_change" and row["accepted"] == 0
    assert json.loads(row["detail"])["check"] == "gate:sd_change"
    assert db.open_position_count(w.conn) == 0
    # a second tick in the window counts, it does not add a row
    tick(w, at(10, 1))
    row = w.conn.execute("SELECT occurrences FROM ensemble_decisions WHERE arm = 'iron-fly-1000'").fetchone()
    assert row["occurrences"] == 2
    # the window closes unentered: a missed row with the last reason
    out = tick(w, at(10, 5))
    assert out["missed"] == {"iron-fly-1000": "missed"}
    entry = db.entry(w.conn, "iron-fly-1000", "SPX", DAY)
    assert entry["outcome"] == "missed" and entry["refusal"] == "gate:sd_change"


def test_nothing_fills_after_the_window(tmp_path):
    w = world(tmp_path)  # a market the arm would enter
    out = tick(w, at(10, 5))  # the window is 10:00 to 10:04
    assert out["entries"] == {} and db.open_position_count(w.conn) == 0
    assert db.entry(w.conn, "iron-fly-1000", "SPX", DAY)["outcome"] == "missed"
    assert db.entry(w.conn, "iron-fly-1000", "SPX", DAY)["refusal"] == "no_tick_in_window"


def test_a_stale_feed_leaves_the_arm_pending_for_the_next_tick(tmp_path):
    w = world(tmp_path)
    w.cache.last("SPX", 6000.0, age=600.0)  # the last print is ten minutes old
    out = tick(w, at(10, 0))
    assert out["entries"] == {"iron-fly-1000": "no_spot"}
    assert db.entry(w.conn, "iron-fly-1000", "SPX", DAY)["outcome"] == "pending"
    w.cache.last("SPX", 6000.0)
    assert tick(w, at(10, 1))["entries"] == {"iron-fly-1000": "entered"}


def test_a_touch_stop_closes_at_the_quotes(tmp_path):
    w = world(tmp_path, overrides={"iron-fly-1000": {"exit": {"touch": {"pct": -0.5}}}})
    tick(w, at(10, 0))
    pid = f"iron-fly-1000:{DAY}"
    assert db.position(w.conn, pid)["status"] == "open"
    w.cache.last("SPX", 5960.0)  # 0.67% under the entry spot
    out = tick(w, at(10, 20))
    assert out["managed"]["closed"] == 1
    done = db.position(w.conn, pid)
    assert done["exit_reason"] == "touch" and done["exit_minute"] == 620
    assert round(done["entry_value"] + done["exit_value"], 2) == done["gross_pnl"]
    assert done["net_pnl"] == round(
        done["gross_pnl"] - done["fees"] - done["settlement"] - done["slippage"], 2
    )
    assert done["settlement"] == 0 and done["slippage"] > db.position(w.conn, pid)["entry_slippage"]


def test_a_time_exit_closes_before_the_bell(tmp_path):
    w = world(tmp_path, overrides={"iron-fly-1000": {"exit": {"close_before_expiry_min": 30}}})
    tick(w, at(10, 0))
    assert tick(w, at(15, 29))["managed"]["closed"] == 0
    assert tick(w, at(15, 30))["managed"]["closed"] == 1
    assert db.position(w.conn, f"iron-fly-1000:{DAY}")["exit_reason"] == "time"


def test_an_open_position_is_marked_and_the_marks_are_kept(tmp_path):
    w = world(tmp_path)
    tick(w, at(10, 0))
    tick(w, at(10, 20))
    pid = f"iron-fly-1000:{DAY}"
    marks = db.marks(w.conn, pid)
    assert marks and marks[-1]["usable"] == 1
    assert db.position(w.conn, pid)["worst_mark_value"] is not None


def test_a_close_whose_legs_do_not_price_is_retried(tmp_path):
    w = world(tmp_path, overrides={"iron-fly-1000": {"exit": {"touch": {"pct": -0.5}}}})
    tick(w, at(10, 0))
    w.cache.last("SPX", 5960.0)
    w.cache.conn.execute("UPDATE stream_quotes SET updated_at = updated_at - 3600")  # every quote stale
    w.cache.conn.commit()
    out = tick(w, at(10, 20))
    assert out["managed"]["pending_close"] == 1 and out["managed"]["closed"] == 0
    assert db.position(w.conn, f"iron-fly-1000:{DAY}")["status"] == "open"
    w.cache.conn.execute("UPDATE stream_quotes SET updated_at = updated_at + 3600")
    w.cache.conn.commit()
    assert tick(w, at(10, 21))["managed"]["closed"] == 1


ORB = {"orb-put-spread": {"gates": []}}


def orb_world(tmp_path, first_sample):
    w = world(tmp_path, keep=("orb-put-spread",), overrides=ORB, spot=6001.0)
    for m in range(first_sample, 631):
        db.record_spot(w.conn, "SPX", DAY, m, 6000.0)  # a flat opening range at 6000
    return w


def test_the_opening_range_arm_enters_on_the_first_break(tmp_path):
    w = orb_world(tmp_path, 570)
    out = tick(w, at(10, 31))  # minute 631: the first spot above the range's high
    assert out["entries"] == {"orb-put-spread": "entered"}
    assert db.position(w.conn, f"orb-put-spread:{DAY}")["entry_minute"] == 631


def test_a_trail_that_began_late_refuses_the_arms_that_read_it(tmp_path):
    w = orb_world(tmp_path, 600)  # the loop started half an hour after the open
    out = tick(w, at(10, 31))
    assert out["entries"] == {"orb-put-spread": "trail_incomplete"}
    assert db.open_position_count(w.conn) == 0


def test_the_spot_is_recorded_a_minute_at_a_time(tmp_path):
    w = world(tmp_path)
    tick(w, at(9, 45))
    tick(w, at(9, 46))
    assert [m for m, _ in db.trail(w.conn, "SPX", DAY)] == [585, 586]


def test_the_shipped_config_resolves_to_the_eight_arms():
    config = config_mod.load_config(
        str(__import__("pathlib").Path(__file__).resolve().parents[1] / "config.example.json")
    )
    arms = config_mod.load_arms(config)
    assert len(config_mod.enabled(arms)) == 8
    assert set(config_mod.symbols(config, arms)) == {"SPX", "GLD"}


def test_a_misspelt_arm_key_is_refused_with_its_path():
    import pytest

    with pytest.raises(ValueError, match="iron-fly-1000: unknown key 'wekdays'"):
        config_mod.load_arms({"arms": {"iron-fly-1000": {"wekdays": [0]}}})


def test_a_positive_net_and_a_drawdown_are_read_from_the_ledger(tmp_path):
    from cherrypick.ensemble import analytics

    assert analytics.drawdown([5, -3, -4, 2]) == 7
    assert analytics.drawdown([1, 2, 3]) == 0
    w = world(tmp_path)
    tick(w, at(10, 0))
    w.cache.summary("SPX", NEXT, prev_close=6003.0)
    paper_loop.run_settlements(w.config, w.conn, cache_path=w.cache_path, when=at(9, 31, NEXT))
    s = analytics.summary(w.conn)
    assert s["combined"]["trades"] == 1 and set(s["arms"]) == {"iron-fly-1000"}
    assert s["combined"]["net"] == s["arms"]["iron-fly-1000"]["net"]
