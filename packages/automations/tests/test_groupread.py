"""Each group as one unit (`groupread`, `cli groups-read`): its members' line stacked, net and drawdown
against its allocation, the most risk held at once against its cap and each member's against its share,
the entries the cap or a share refused, and how its members move together (`core.portfolio`)."""

from __future__ import annotations

import json
from datetime import date, timedelta

from cherrypick.automations import cli, groupread

ARMS = {"a": {"group": "g"}, "b": {"group": "g"}, "c": {"group": None}}
SPEC = {"g": {"enabled": True, "allocation": 10000, "max_risk": 2000, "shares": {"a": 60, "b": 40}}}


def _pos(arm, entry, exit_, net, capital=500.0, status="closed"):
    return {
        "arm": arm,
        "entry_session": entry,
        "exit_session": exit_,
        "status": status,
        "capital": capital,
        "net_pnl": net if status == "closed" else None,
        "gross_pnl": net,
        "fees": 1.0,
        "settlement": 0.0,
        "slippage": 2.0,
        "net_stress": net - 1,
        "net_mid": net + 2,
    }


def _refused(arm, session, read):
    detail = json.dumps({"check": "group_risk", "value": {"base": 2, "group": read, "why": "group_risk"}})
    return {
        "arm": arm,
        "symbol": "SPX",
        "entry_session": session,
        "outcome": "missed",
        "refusal": "group_risk",
        "detail": detail,
    }


def test_the_groups_line_stacks_its_members_and_reads_against_its_allocation():
    pos = [
        _pos("a", "2026-11-02", "2026-11-02", 300),
        _pos("b", "2026-11-03", "2026-11-03", -500),
        _pos("c", "2026-11-03", "2026-11-03", 999),
    ]
    g = groupread.read(ARMS, SPEC, pos, [])["groups"][0]
    assert g["members"] == ["a", "b"]
    assert (g["line"]["trades"], g["line"]["net"], g["line"]["drawdown"]) == (2, -200.0, 500.0)
    assert g["line"]["curve"] == [{"x": "2026-11-02", "y": 300.0}, {"x": "2026-11-03", "y": -200.0}]
    assert g["against_allocation"] == {"net": -0.02, "drawdown": 0.05, "since": "2026-11-02", "changes": []}


def test_the_automations_in_no_group_are_one_line_beside_the_groups():
    out = groupread.read(ARMS, SPEC, [_pos("c", "2026-11-03", "2026-11-03", 999)], [])
    assert out["ungrouped"]["members"] == ["c"]
    assert out["ungrouped"]["line"]["net"] == 999.0


def test_without_an_allocation_there_is_nothing_to_read_against():
    g = groupread.read(ARMS, {"g": {"enabled": True}}, [], [])["groups"][0]
    assert g["against_allocation"] is None and g["risk"] is None


def test_risk_at_once_sums_what_is_open_on_each_session_and_reads_it_against_the_cap():
    pos = [
        _pos("a", "2026-11-02", "2026-11-04", 10, capital=800),
        _pos("b", "2026-11-03", "2026-11-03", 10, capital=600),
        _pos("b", "2026-11-05", None, 0, capital=700, status="open"),
        _pos("a", "2026-11-06", "2026-11-06", 10, capital=900),
    ]
    g = groupread.read(ARMS, SPEC, pos, [])["groups"][0]
    # 11-03: 800 + 600 open together; 11-06: 700 still open + 900.
    assert g["risk"] == {"peak": 1600.0, "session": "2026-11-06", "cap": 2000, "of_cap": 0.8}
    member = {m["name"]: m for m in g["member_risk"]}
    assert (member["a"]["peak"], member["a"]["limit"], member["a"]["of_limit"]) == (900.0, 1200.0, 0.75)
    assert (member["b"]["peak"], member["b"]["limit"]) == (700.0, 800.0)


def test_a_position_counts_through_the_session_it_closed_and_not_after():
    held = [_pos("a", "2026-11-02", "2026-11-04", 0, capital=800)]
    # Entering on its closing session counts both: the upper bound at a session's grain.
    assert (
        groupread.peak_risk([*held, _pos("b", "2026-11-04", "2026-11-04", 0, capital=300)])["peak"] == 1100.0
    )
    # The session after, it is gone.
    assert (
        groupread.peak_risk([*held, _pos("b", "2026-11-05", "2026-11-05", 0, capital=300)])["peak"] == 800.0
    )
    assert groupread.peak_risk([]) is None


def test_a_refusal_counts_against_the_cap_with_less_room():
    reg = [
        _refused("a", "2026-11-05", {"group_max_risk": [1900, 2000], "member_max_risk": [500, 1200]}),
        _refused("b", "2026-11-04", {"group_max_risk": [900, 2000], "member_max_risk": [790, 800]}),
        _refused("a", "2026-11-03", {"group_max_risk": [1500, 2000]}),
        {
            "arm": "a",
            "symbol": "SPX",
            "entry_session": "2026-11-02",
            "outcome": "missed",
            "refusal": "group_risk",
            "detail": None,
        },
        # Not a group refusal, and not a member's: neither counts.
        {
            "arm": "a",
            "symbol": "SPX",
            "entry_session": "2026-11-01",
            "outcome": "missed",
            "refusal": "gate:vix_level",
            "detail": None,
        },
        _refused("c", "2026-11-05", {"group_max_risk": [1, 2]}),
    ]
    r = groupread.read(ARMS, SPEC, [], reg)["groups"][0]["refused"]
    assert (r["cap"], r["share"], r["unknown"]) == (2, 1, 1)
    assert [x["session"] for x in r["latest"]] == ["2026-11-05", "2026-11-04", "2026-11-03", "2026-11-02"]
    assert r["latest"][1] == {"session": "2026-11-04", "automation": "b", "by": "share"}


def test_members_that_move_together_read_so_and_a_short_record_is_thin():
    start = date(2026, 6, 1)
    days = [start + timedelta(days=i) for i in range(60) if (start + timedelta(days=i)).weekday() < 5]
    pos = []
    for i, d in enumerate(days):
        s = d.isoformat()
        x = 100.0 if i % 3 else -150.0
        pos += [_pos("a", s, s, x), _pos("b", s, s, 2 * x)]
    t = groupread.read(ARMS, SPEC, pos, [])["groups"][0]["together"]
    pair = t["pairs"][0]
    assert (pair["a"], pair["b"], pair["zero_filled"]) == ("a", "b", 1.0)
    assert t["effective_members"] == 1.0 and t["thin"] is False
    short = groupread.read(ARMS, SPEC, pos[:10], [])["groups"][0]["together"]
    assert short["thin"] is True


def test_one_member_with_closes_has_nothing_to_move_with():
    pos = [_pos("a", "2026-11-02", "2026-11-02", 300)]
    assert groupread.read(ARMS, SPEC, pos, [])["groups"][0]["together"] is None


def test_the_cli_reads_no_ledger_as_no_trades(tmp_path, capsys, monkeypatch):
    cfg = tmp_path / "automations.json"
    cfg.write_text(json.dumps({}), encoding="utf-8")
    assert cli.main(["--config", str(cfg), "--db", str(tmp_path / "none.db"), "groups-read"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and all(g["line"]["trades"] == 0 for g in out["groups"])
    assert not (tmp_path / "none.db").exists()
