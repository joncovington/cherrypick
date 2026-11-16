"""Sizing beyond a count and a risk cap: a percent of an allocation, a count that steps after a run of
losses or wins, and an automation group's allocation and caps shared by its members. The same counts
reach the paper ledger's snapshot and the backtest driver's book."""

from __future__ import annotations

import copy
import sqlite3

import pytest
from market import spx_snapshot

from cherrypick.automations import arms, db, driver, engine, feed, paper, sizing
from cherrypick.automations import config as config_mod
from cherrypick.automations.model import OpenPosition, Snapshot

A = arms.load_arms()
MON = "2026-11-16"


def fly(size, **changes):
    a = copy.deepcopy(A["iron-fly-1000"])
    a["size"] = size
    a.update(changes)
    return a


def loss_per_share() -> float:
    return engine.decide(A["iron-fly-1000"], spx_snapshot()).entry.max_loss


# --- a percent of an allocation ---------------------------------------------------------------------
def test_a_percent_of_an_allocation_capped_by_dollars_and_by_contracts():
    per = 4.0 * 100  # four dollars a share at risk: $400 a contract
    assert sizing.base({"allocation_pct": 2, "allocation": 50000}, 4.0) == 2  # $1,000 budget
    assert sizing.base({"allocation_pct": 2, "allocation": 50000, "max_dollars": 500}, 4.0) == 1
    assert sizing.base({"allocation_pct": 10, "allocation": 50000, "max_contracts": 3}, 4.0) == 3
    assert sizing.base({"allocation_pct": 0.5, "allocation": 50000}, 4.0) == 0  # $250 < $400
    assert sizing.base({"allocation_pct": 0.5, "allocation": 50000, "minimum": 1}, 4.0) == 1
    # Without its own allocation it reads the group's share, and with neither it sizes nothing.
    assert sizing.base({"allocation_pct": 2}, 4.0, allocation=40000) == 2
    assert sizing.base({"allocation_pct": 2}, 4.0) == 0
    assert per == 400


def test_an_allocation_size_enters_through_the_engine():
    loss = loss_per_share()
    alloc = loss * 100 * 3 / 0.05 + 1  # 5% of it holds three contracts' risk and a dollar over
    d = engine.decide(fly({"allocation_pct": 5, "allocation": alloc}), spx_snapshot())
    assert d.enter and d.entry.quantity == 3, d.checks
    assert ("size", True, 3) in d.checks


@pytest.mark.parametrize(
    ("size", "match"),
    [
        ({"allocation_pct": 0, "allocation": 1000}, "allocation_pct"),
        ({"allocation_pct": 101, "allocation": 1000}, "allocation_pct"),
        ({"allocation_pct": 2, "allocation": -5}, "allocation"),
        ({"allocation_pct": 2, "max_contracts": 0}, "max_contracts"),
        ({"allocation_pct": 2, "max_risk": 100}, "one of"),
        ({"contracts": 2, "minimum": 1}, "unknown key"),
        ({"contracts": 0}, "contracts"),
        ({"max_risk": 0}, "max_risk"),
        ({"contracts": 1, "progression": {"after": "loss", "step": 1, "floor": 1}}, "ceiling"),
        ({"contracts": 1, "progression": {"after": "draw", "step": 1, "floor": 1, "ceiling": 3}}, "after"),
        ({"contracts": 1, "progression": {"after": "loss", "step": 0, "floor": 1, "ceiling": 3}}, "step"),
        ({"contracts": 1, "progression": {"after": "loss", "step": 1, "floor": 4, "ceiling": 3}}, "ceiling"),
        ({"contracts": 1, "progression": {"after": "loss", "step": 1, "floor": 0, "ceiling": 3}}, "floor"),
    ],
)
def test_a_malformed_size_is_refused_with_its_path(size, match):
    problems = arms.validate(fly(size))
    assert any(".size" in p and match in p for p in problems), problems


def test_the_shipped_sizes_still_validate():
    assert not [p for a in A.values() for p in arms.validate(a)]


# --- progressive risk -------------------------------------------------------------------------------
STEP_UP = {"after": "loss", "step": 1, "floor": 1, "ceiling": 3}


def test_a_count_steps_after_each_loss_and_resets_after_a_win():
    assert sizing.progressed(1, STEP_UP, []) == 1
    assert sizing.progressed(1, STEP_UP, [-50.0]) == 2
    assert sizing.progressed(1, STEP_UP, [10.0, -50.0, -20.0]) == 3
    assert sizing.progressed(1, STEP_UP, [-1.0, -1.0, -1.0, -1.0, -1.0]) == 3  # held at the ceiling
    assert sizing.progressed(1, STEP_UP, [-50.0, -20.0, 30.0]) == 1  # the win ends the run
    assert sizing.progressed(1, STEP_UP, [-50.0, 0.0]) == 1  # so does a trade at exactly zero


def test_a_count_doubles_after_wins_and_steps_down_to_its_floor():
    double = {"after": "win", "step": "double", "floor": 1, "ceiling": 8}
    assert sizing.progressed(1, double, [5.0, 5.0]) == 4
    assert sizing.progressed(1, double, [5.0] * 10) == 8
    assert sizing.progressed(1, double, [5.0, -5.0]) == 1
    down = {"after": "loss", "step": -1, "floor": 1, "ceiling": 4}
    assert sizing.progressed(4, down, [-5.0, -5.0]) == 2
    assert sizing.progressed(4, down, [-5.0] * 6) == 1


def test_the_engine_reads_the_arms_own_results_for_its_progression():
    arm = fly({"contracts": 1, "progression": STEP_UP})
    snap = spx_snapshot()
    snap.results = {"iron-fly-1000": [-100.0, -80.0], "iron-condor-1345": [50.0]}
    d = engine.decide(arm, snap)
    assert d.entry.quantity == 3
    assert ("size", True, {"base": 1, "progressed": 3, "count": 3}) in d.checks
    snap.results = {"iron-condor-1345": [-50.0, -50.0]}  # another arm's losses are not this one's
    assert engine.decide(arm, snap).entry.quantity == 1


# --- a group's allocation and caps ------------------------------------------------------------------
def with_groups(spec):
    return config_mod.load_arms({"groups": {"ensemble": {"enabled": True, **spec}}})


def test_a_group_shares_its_allocation_and_caps_among_its_members():
    loaded = with_groups(
        {
            "allocation": 100000,
            "max_risk": 2000,
            "shares": {"iron-fly-1000": 25, "long-put-1030": 10},
        }
    )
    assert loaded["iron-fly-1000"]["budget"] == {
        "group": "ensemble",
        "members": sorted(A),
        "allocation": 25000.0,
        "group_max_risk": 2000.0,
        "member_max_risk": 500.0,
    }
    # A member without a share has the whole allocation to size from and only the group's cap.
    assert loaded["iron-condor-1345"]["budget"]["allocation"] == 100000.0
    assert "member_max_risk" not in loaded["iron-condor-1345"]["budget"]
    # A group with no allocation or cap leaves its members as they were.
    assert all("budget" not in a for a in with_groups({}).values())
    status = config_mod.group_status(loaded, {"groups": {"ensemble": {"enabled": True, "max_risk": 2000}}})
    assert status["ensemble"]["max_risk"] == 2000.0


@pytest.mark.parametrize(
    ("spec", "match"),
    [
        ({"allocation": 0}, "allocation"),
        ({"max_risk": "lots"}, "max_risk"),
        ({"max_risk": 1000, "shares": {"iron-fly-1000": 60, "long-put-1030": 50}}, "more than 100"),
        ({"max_risk": 1000, "shares": {"iron-fly-1000": 0}}, "a percent"),
        ({"max_risk": 1000, "shares": {"not-a-member": 10}}, "not a member"),
        ({"max_risk": 1000, "shares": {}}, "an object of"),
        ({"shares": {"iron-fly-1000": 10}}, "a share of nothing"),
    ],
)
def test_a_malformed_group_budget_is_refused(spec, match):
    with pytest.raises(ValueError, match=match):
        with_groups(spec)


def test_an_allocation_size_needs_an_allocation_from_somewhere():
    change = {"iron-fly-1000": {"size": {"allocation_pct": 2}}}
    with pytest.raises(ValueError, match="no allocation"):
        config_mod.load_arms({"automations": change})
    got = config_mod.load_arms(
        {"automations": change, "groups": {"ensemble": {"enabled": True, "allocation": 1e5}}}
    )
    assert got["iron-fly-1000"]["budget"]["allocation"] == 1e5
    # A budget is the group's to set, never the arm's own.
    with pytest.raises(ValueError, match="budget"):
        config_mod.load_arms({"automations": {"iron-fly-1000": {"budget": {"group_max_risk": 1}}}})


def test_the_group_cap_cuts_the_count_to_fit_and_then_refuses():
    loss = loss_per_share()
    per = loss * 100
    room_for_two = per * 2 + 1
    arm = config_mod.load_arms(
        {
            "automations": {"iron-fly-1000": {"size": {"contracts": 5}}},
            "groups": {"ensemble": {"enabled": True, "max_risk": 1000 + room_for_two}},
        }
    )["iron-fly-1000"]
    held = [OpenPosition("put-spread-1300", "2026-11-13", "2026-11-20", 1000.0)]
    d = engine.decide(arm, spx_snapshot(open_positions=held))
    assert d.enter and d.entry.quantity == 2, d.checks
    size = next(v for n, ok, v in d.checks if n == "size")
    assert size["base"] == 5 and size["cut_to"] == 2 and size["group"]["group_max_risk"][0] == 1000.0
    # A position outside the group does not count against it.
    outside = [OpenPosition("someone-else", "2026-11-13", "2026-11-20", 1000.0)]
    assert engine.decide(arm, spx_snapshot(open_positions=outside)).entry.quantity == 5
    # Full: no room for even one, and no minimum lifts it over the cap.
    full = [OpenPosition("put-spread-1300", "2026-11-13", "2026-11-20", 1000.0 + per * 2)]
    capped = {**arm, "size": {"max_risk": 100, "minimum": 1}}
    d = engine.decide(capped, spx_snapshot(open_positions=full))
    assert not d.enter and d.reason == "group_risk"


def test_a_members_share_caps_its_own_open_risk():
    per = loss_per_share() * 100
    arm = config_mod.load_arms(
        {
            "automations": {
                "iron-fly-1000": {"size": {"contracts": 3}, "limits": {"max_open": 5, "per_day": 1}}
            },
            "groups": {"ensemble": {"enabled": True, "max_risk": 100000, "shares": {"iron-fly-1000": 1}}},
        }
    )["iron-fly-1000"]
    assert arm["budget"]["member_max_risk"] == 1000.0
    mine = [OpenPosition("iron-fly-1000", "2026-11-13", "2026-11-20", 1000.0 - per - 1)]
    d = engine.decide(arm, spx_snapshot(open_positions=mine))
    assert d.entry.quantity == 1
    theirs = [OpenPosition("put-spread-1300", "2026-11-13", "2026-11-20", 1000.0 - per - 1)]
    assert engine.decide(arm, spx_snapshot(open_positions=theirs)).entry.quantity == 3


def test_arms_deciding_on_one_tick_see_each_others_risk():
    per = loss_per_share() * 100
    loaded = config_mod.load_arms(
        {
            "automations": {
                "iron-fly-1000": {"size": {"contracts": 1}},
                "iron-fly-copy": {**A["iron-fly-1000"], "name": "iron-fly-copy", "size": {"contracts": 1}},
            },
            "groups": {"ensemble": {"enabled": True, "max_risk": per * 1.5}},
        }
    )
    both = {n: loaded[n] for n in ("iron-fly-1000", "iron-fly-copy")}
    out = {d.arm: d for d in paper.decide_all(both, spx_snapshot())}
    assert out["iron-fly-1000"].enter
    assert out["iron-fly-copy"].reason == "group_risk"


# --- the same data on both paths --------------------------------------------------------------------
def test_the_snapshot_record_keeps_risk_and_results():
    snap = spx_snapshot(open_positions=[OpenPosition("a", MON, MON, 420.0)])
    snap.results = {"a": [-10.0, 5.0]}
    back = Snapshot.from_dict(snap.to_dict())
    assert back.open_positions[0].risk == 420.0 and back.results == {"a": [-10.0, 5.0]}
    old = snap.to_dict()
    old.pop("results")
    for p in old["open_positions"]:
        p.pop("risk")
    back = Snapshot.from_dict(old)  # a decision recorded before either was kept still replays
    assert back.results == {} and back.open_positions[0].risk == 0.0


def test_the_driver_book_carries_each_positions_risk_and_each_arms_results():
    d = engine.decide(A["iron-fly-1000"], spx_snapshot())
    book = driver.Book()
    book.open.append(engine.Position(d.entry, A["iron-fly-1000"]))
    book.trades += [
        {"arm": "x", "net": {"verdict": -5.0, "stress": -6.0, "mid": -4.0}},
        {"arm": "x", "net": {"verdict": 7.0, "stress": 6.0, "mid": 8.0}},
    ]
    (held,) = book.open_positions()
    assert held.risk == pytest.approx(d.entry.max_loss * 100 * d.entry.quantity)
    assert book.results() == {"x": [-5.0, 7.0]}


def test_the_paper_ledger_gives_the_same_risk_and_results():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(db._SCHEMA)
    rows = [
        # (position_id, arm, status, exit_session, exit_minute, net_pnl, capital, expiration)
        ("p1", "x", "closed", "2026-11-12", 700, -5.0, 300.0, "2026-11-12"),
        ("p2", "x", "closed", "2026-11-13", None, 9.0, 300.0, "2026-11-13"),  # settled: last that day
        ("p3", "x", "closed", "2026-11-13", 900, -2.0, 300.0, "2026-11-20"),
        ("p4", "x", "open", None, None, None, 450.0, "2026-11-20"),
    ]
    for pid, arm, status, xs, xm, net, cap, exp in rows:
        conn.execute(
            "INSERT INTO ensemble_positions (position_id, arm, symbol, entry_session, expiration,"
            " entry_value, entry_json, status, exit_session, exit_minute, net_pnl, capital)"
            " VALUES (?, ?, 'SPX', '2026-11-11', ?, 0, '{}', ?, ?, ?, ?, ?)",
            (pid, arm, exp, status, xs, xm, net, cap),
        )
    assert db.closed_results(conn) == {"x": [-5.0, -2.0, 9.0]}
    from datetime import date

    (held,) = feed.open_positions(conn, date(2026, 11, 16))
    assert held.risk == 450.0


PROGRESSIVE = {"size": {"contracts": 1, "progression": STEP_UP}}


def a_losing_trade(conn, arm="iron-fly-1000"):
    conn.execute(
        "INSERT INTO ensemble_positions (position_id, arm, symbol, entry_session, expiration, entry_value,"
        " entry_json, status, exit_session, exit_minute, net_pnl, capital)"
        " VALUES ('old', ?, 'SPX', '2026-11-13', '2026-11-13', 0, '{}', 'closed',"
        " '2026-11-13', NULL, -80.0, 300)",
        (arm,),
    )
    conn.commit()


def test_the_paper_loop_steps_the_count_after_a_loss_in_its_ledger(tmp_path):
    from test_paper_loop import at, tick, world

    w = world(tmp_path, overrides={"iron-fly-1000": PROGRESSIVE})
    a_losing_trade(w.conn)
    assert tick(w, at(10, 0))["entries"] == {"iron-fly-1000": "entered"}
    assert db.position(w.conn, f"iron-fly-1000:{MON}")["quantity"] == 2


def test_the_driver_steps_the_count_after_a_loss_in_its_book(tmp_path):
    from market import CALENDAR
    from test_driver import VIX_DAILY, store
    from test_paper_loop import world

    w = world(tmp_path, overrides={"iron-fly-1000": PROGRESSIVE})
    book = driver.Book(
        trades=[{"arm": "iron-fly-1000", "net": {"verdict": -80.0, "stress": -90.0, "mid": -70.0}}]
    )
    book = driver.run(
        {"iron-fly-1000": w.arms["iron-fly-1000"]}, store(tmp_path), [MON], events=CALENDAR,
        vix_daily=VIX_DAILY, costs=w.config, book=book,
    )  # fmt: skip
    assert book.trades[-1]["quantity"] == 2
