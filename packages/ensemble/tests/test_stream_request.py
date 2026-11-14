"""The stream request is derived from the enabled arms: narrow in time, wide only for the entry minutes, and
a new arm needs no code here."""

from __future__ import annotations

import json
from datetime import datetime

from cachebuilder import CacheBuilder
from cherrypick.core import streamrequests
from cherrypick.core.clock import ET
from test_paper_loop import DAY, NEXT, at, make_config, world

from cherrypick.ensemble import config as config_mod
from cherrypick.ensemble import db, paper_loop, stream_request


def wanted(w, when):
    return stream_request.wanted(w.config, w.arms, w.conn, w.cache_path, when)


def test_the_window_widens_only_around_an_arms_entry(tmp_path):
    w = world(tmp_path)  # iron-fly-1000: 10:00 to 10:04
    assert wanted(w, at(9, 49))["window_hints"] == {}
    near = wanted(w, at(9, 50))
    assert near["window_hints"] == {"SPX": 60} and near["expirations"] == {"SPX": [DAY]}
    assert wanted(w, at(10, 6))["window_hints"] == {"SPX": 60}  # two minutes of grace after the window
    gone = wanted(w, at(10, 7))
    assert gone["window_hints"] == {} and gone["expirations"] == {}


def test_an_open_positions_expiry_and_legs_stay_requested(tmp_path):
    w = world(tmp_path)
    paper_loop.run_once(
        w.config, w.arms, w.conn, cache_path=w.cache_path, when=at(10, 0), evts=None, replay_to=w.replay
    )
    assert wanted(w, at(14, 0))["expirations"] == {"SPX": [DAY]}
    path = stream_request.write(
        w.config, w.arms, w.conn, str(tmp_path / "paper.db"), cache_path=w.cache_path, when=at(14, 0)
    )
    request = json.loads(path.read_text(encoding="utf-8"))
    assert request["leg_sources"][0]["query"].endswith("FROM ensemble_legs WHERE status = 'open'")
    assert request["symbols"] == ["SPX"] and request["nearest_window"] == {"SPX": False}
    assert (
        streamrequests.legs_from_source(request["leg_sources"][0])
        and len(streamrequests.legs_from_source(request["leg_sources"][0])) == 4
    )


def test_what_an_arm_reads_decides_what_is_asked_for(tmp_path):
    w = world(tmp_path, keep=("iron-fly-1000", "long-put-1030"))
    need = wanted(w, at(9, 55))
    assert need["window_events"] == {"SPX": ["Quote", "Greeks", "Summary"]}  # a delta leg
    assert need["legs"] == ["VIX"] and need["history_days"]["VIX"] > 0  # the standard-deviation move
    (tmp_path / "b").mkdir()
    only_fly = world(tmp_path / "b", keep=("iron-fly-1000",))
    assert wanted(only_fly, at(9, 55))["window_events"] == {"SPX": ["Quote"]}


def test_the_twenty_day_arm_asks_for_the_listed_expiry_its_rule_names(tmp_path):
    w = world(tmp_path, keep=("gld-call-spread-20d",), gld=True)
    w.cache.listed("GLD", ["2026-11-17", "2026-11-20", "2026-12-04", "2026-12-18"])
    need = wanted(w, at(12, 20))
    assert need["expirations"] == {"GLD": ["2026-12-04"]}  # 18 days is the nearest within three of the 20th
    assert need["history_days"]["GLD"] == 16  # the previous close plus the 15-day margin
    # a cache that has not listed the symbol yet gets every date the rule allows
    w.cache.conn.execute("DELETE FROM stream_expirations WHERE underlying_symbol = 'GLD'")
    w.cache.conn.commit()
    assert len(wanted(w, at(12, 20))["expirations"]["GLD"]) == 7


def test_a_new_arm_on_a_new_symbol_needs_no_code(tmp_path):
    new = {
        "name": "tlt-spread",
        "symbol": "TLT",
        "expiry": {"kind": "market_days", "days": 1},
        "entry": {"kind": "time", "at": "11:00", "window": 5},
        "weekdays": [0, 1, 2, 3, 4],
        "legs": [
            {"name": "long_call", "right": "C", "side": 1, "strike": {"rule": "delta", "target": 0.5}},
            {
                "name": "short_call",
                "right": "C",
                "side": -1,
                "strike": {"rule": "from_leg", "leg": "long_call", "points": 2, "round": "nearest"},
            },
        ],
        "size": {"contracts": 1},
    }
    config = make_config(("tlt-spread",), {"tlt-spread": new})
    arms = config_mod.load_arms(config)
    assert config_mod.symbols(config, arms) == {"TLT": {"root": "TLT", "window_strikes": 60}}
    conn = db.connect(str(tmp_path / "p.db"))
    cache = CacheBuilder(tmp_path / "c.db")
    need = stream_request.wanted(config, arms, conn, cache.path, datetime(2026, 11, 16, 10, 55, tzinfo=ET))
    assert need["symbols"] == ["TLT"] and need["window_hints"] == {"TLT": 60}
    assert need["expirations"] == {"TLT": [NEXT]}
