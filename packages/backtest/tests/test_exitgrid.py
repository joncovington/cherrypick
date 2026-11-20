"""The settings grid (#1294): the grid is read from the rule alone, a setting is scored by what it changes
against the base per session, reading the design part only, against the best random change of the
whole grid; the held-out part is read once per declared setting. Synthetic trades throughout."""

from __future__ import annotations

import importlib.util
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from cherrypick.backtest import exitgrid, gridfiles, ideasearch, paths

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_spec = importlib.util.spec_from_file_location("exit_grid", SCRIPTS / "exit_grid.py")
drv = importlib.util.module_from_spec(_spec)
sys.modules["exit_grid"] = drv
_spec.loader.exec_module(drv)


def _leg(name, right, side, **strike):
    return {"name": name, "right": right, "side": side, "strike": strike}


FLY = {
    "name": "fly",
    "symbol": "SPX",
    "expiry": {"kind": "dte", "days": 0},
    "entry": {"kind": "time", "at": "10:00", "window": 5},
    "weekdays": [0, 1, 2, 3, 4],
    "legs": [
        _leg("short_put", "P", -1, rule="spot", points=0, match="nearest"),
        _leg("short_call", "C", -1, rule="same_as", leg="short_put"),
        _leg("long_put", "P", 1, rule="from_leg", leg="short_put", points=-20, match="nearest"),
        _leg("long_call", "C", 1, rule="from_leg", leg="short_call", points=20, match="nearest"),
    ],
    "size": {"contracts": 1},
    "limits": {"max_open": 1, "per_day": 1},
    "exit": {"close_before_expiry_min": 60, "stop_credit_multiple": 2.0},
    "gates": [{"gate": "no_position_today", "arm": "fly"}],
    "budget": {"group": "g", "members": ["fly", "other"], "group_max_risk": 1000},
}


# --------------------------------------------------------------------------- the grid
def test_the_grid_is_read_from_the_rule_and_keeps_the_automations_own_value_as_the_base():
    g = exitgrid.variants(FLY)
    assert "stop_credit_multiple=2" not in g, "the automation's own value is the base, never a candidate"
    assert {"stop_credit_multiple=1", "stop_credit_multiple=off", "close_before_expiry_min=15"} <= set(g)
    assert "close_before_expiry_min=60" not in g
    assert not any(k.startswith("stop_debit_pct") for k in g), "it already sets a credit stop"
    assert not any(k.startswith("close_at_dte") for k in g), "a same-day expiry has no days to close by"
    assert g["width=x2"]["arm"]["legs"][2]["strike"]["points"] == -40
    assert g["width=x2"]["arm"]["legs"][0]["strike"]["points"] == 0, "only legs placed from another leg"
    assert "stop_credit_multiple" not in g["stop_credit_multiple=off"]["arm"]["exit"]


def test_each_setting_runs_under_its_own_name_cap_and_gates():
    a = exitgrid.variants(FLY)["profit_target_pct=50"]["arm"]
    assert a["name"] == "fly~profit_target_pct=50"
    assert a["budget"]["members"] == ["fly~profit_target_pct=50"], "a group cap counts only its own positions"
    assert a["gates"] == [{"gate": "no_position_today", "arm": "fly~profit_target_pct=50"}]
    assert FLY["budget"]["members"] == ["fly", "other"] and FLY["exit"]["stop_credit_multiple"] == 2.0


def test_a_later_expiry_takes_the_days_close_and_a_debit_structure_the_debit_stop():
    long_put = {**FLY, "expiry": {"kind": "at_least", "days": 30}, "exit": {}, "legs": [FLY["legs"][2]]}
    g = exitgrid.variants(long_put)
    assert any(k.startswith("close_at_dte") for k in g) and not any(k.startswith("close_before") for k in g)
    assert any(k.startswith("stop_debit_pct") for k in g) and not any(k.startswith("stop_credit") for k in g)


# --------------------------------------------------------------------------- scoring
def _sessions(n=240):
    d, out = date(2025, 1, 2), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _trades(sessions, nets):
    return [
        {"session": s, "net": {"verdict": v}, "fees": 2.0, "slippage": {"verdict": 3.0}}
        for s, v in zip(sessions, nets, strict=True)
    ]


def _world(gain=25.0, noise=40):
    sessions = _sessions()
    rng = random.Random(7)
    base_nets = [rng.gauss(0, 100) for _ in sessions]
    grid = {
        f"profit_target_pct={v}": {"axis": "profit_target_pct", "value": v, "words": f"pt {v}"}
        for v in (25, 50, 75)
    }
    settings = {}
    for k in grid:
        r = random.Random(hash(k) % 1000)
        settings[k] = _trades(
            sessions, [b + (r.gauss(0, 30) if r.random() < 0.4 else 0.0) for b in base_nets]
        )
    for j in range(noise):
        k = f"noise={j}"
        grid[k] = {"axis": "noise", "value": None, "words": k}
        r = random.Random(1000 + j)
        settings[k] = _trades(
            sessions, [b + (r.gauss(0, 30) if r.random() < 0.4 else 0.0) for b in base_nets]
        )
    grid["good"] = {"axis": "good", "value": None, "words": "the good one"}
    r = random.Random(5)
    settings["good"] = _trades(
        sessions, [b + (gain + r.gauss(0, 10) if r.random() < 0.6 else 0.0) for b in base_nets]
    )
    return sessions, _trades(sessions, base_nets), settings, grid


def test_a_planted_gain_survives_and_noise_settings_do_not():
    sessions, base, settings, grid = _world()
    split = ideasearch.split_date(sessions[0], sessions[-1])
    out = exitgrid.search(base, settings, grid, FLY, sessions, split, draws=400)
    rows = {r["id"]: r for r in out["rows"]}
    assert rows["good"]["survives"], rows["good"]["gates"]
    assert out["survivors"] == 1
    assert out["tried"] == len(grid)
    assert out["base"]["held_out_sessions"] == len([s for s in sessions if s >= split])
    assert rows["good"]["gain_shrunk"] < rows["good"]["gain"], "the claim is shrunk by the grid's luck"


def test_the_search_never_reads_the_held_out_part():
    sessions, base, settings, grid = _world()
    split = ideasearch.split_date(sessions[0], sessions[-1])
    one = exitgrid.search(base, settings, grid, FLY, sessions, split, draws=200)
    later = {
        k: [{**t, "net": {"verdict": 1e6}} if t["session"] >= split else t for t in v]
        for k, v in settings.items()
    }
    two = exitgrid.search(base, later, grid, FLY, sessions, split, draws=200)
    assert one == two


def test_a_setting_that_changes_nothing_is_listed_and_not_tried():
    sessions, base, settings, grid = _world(noise=3)
    settings["same"] = base
    grid["same"] = {"axis": "same", "value": None, "words": "same"}
    out = exitgrid.search(base, settings, grid, FLY, sessions, sessions[160], draws=100)
    row = next(r for r in out["rows"] if r["id"] == "same")
    assert row["scored"] is False and row["why"] == "changes no session" and out["tried"] == len(grid) - 1


def test_a_lone_peak_on_its_axis_fails_the_plateau():
    grid = {
        f"profit_target_pct={v}": {"axis": "profit_target_pct", "value": v, "words": ""} for v in (25, 50, 75)
    }
    means = {"profit_target_pct=25": -5.0, "profit_target_pct=50": 10.0, "profit_target_pct=75": -4.0}
    assert exitgrid.plateau("profit_target_pct=50", grid, means, FLY)["verdict"] == "lone_peak"
    means["profit_target_pct=75"] = 9.0
    means["profit_target_pct=25"] = 8.0
    assert exitgrid.plateau("profit_target_pct=50", grid, means, FLY)["verdict"] == "plateau"


def test_the_held_out_test_reads_the_later_sessions_only():
    sessions, base, settings, _grid = _world()
    split = ideasearch.split_date(sessions[0], sessions[-1])
    read = exitgrid.holdout(base, settings["good"], sessions, split)
    assert read["sessions"] == len([s for s in sessions if s >= split]) and read["verdict"] == "passed"
    flat = exitgrid.holdout(base, base, sessions, split)
    assert flat["verdict"] == "no_verdict" and flat["moved"] == 0


# --------------------------------------------------------------------------- files
@pytest.fixture
def stored(tmp_path, monkeypatch):
    own = tmp_path / "own"
    own.mkdir()
    monkeypatch.setattr(paths, "automations_dir", lambda licensed: own)
    sessions, base, settings, grid = _world()
    split = ideasearch.split_date(sessions[0], sessions[-1])
    out = exitgrid.search(base, settings, grid, FLY, sessions, split, draws=300)
    doc = {"name": "grid-fly-2611181000", "automation": "fly", "run": "builder-fly-2611161730",
           "licensed": False, "sessions": [sessions[0], sessions[-1]], "replayed_sessions": sessions,
           **out, "trades": {"base": base, **settings}, "declared": [], "held_out_uses": [],
           "generated_at": "2026-11-18T10:00:00"}  # fmt: skip
    (own / "grid-fly-2611181000.json").write_text(json.dumps(doc), encoding="utf-8")
    return own


def test_a_read_serves_the_rows_and_never_the_trades(stored):
    got = gridfiles.grid_result("fly", "grid-fly-2611181000")
    assert got["ok"] and got["rows"] and "trades" not in got and "replayed_sessions" not in got
    assert gridfiles.grids("fly")["grids"][0]["survivors"] == 1


def test_declare_only_a_survivor_and_test_it_once(stored):
    rid = "grid-fly-2611181000"
    assert gridfiles.holdout("fly", rid, "good", {})["ok"] is False, "declare first"
    assert gridfiles.declare("fly", rid, "noise=0")["ok"] is False
    got = gridfiles.declare("fly", rid, "good")
    assert got["ok"] and got["row"]["settings_grid"]["split"] and got["row"]["id"] is None
    assert gridfiles.declare("fly", rid, "good")["ok"] is False, "once"
    first = gridfiles.holdout("fly", rid, "good", {})
    assert first["ok"] and first["verdict"] == "passed" and first["uses"] == 1
    assert gridfiles.holdout("fly", rid, "good", {})["ok"] is False, "the held-out part is read once"
    assert gridfiles.grid_result("fly", rid)["held_out_used"] == 1


# --------------------------------------------------------------------------- the driver's wiring
def test_the_driver_replays_the_run_and_every_setting_under_their_own_names(tmp_path, monkeypatch):
    own = tmp_path / "own"
    own.mkdir()
    monkeypatch.setattr(paths, "automations_dir", lambda licensed: own)
    sessions = _sessions(30)
    arm = {k: v for k, v in FLY.items() if k != "gates"}
    run_trades = [{"arm": "fly", "session": s, "net": {"verdict": 1.0}} for s in sessions]
    doc = {"name": "builder-fly-2611161730", "symbol": "SPX", "source": "x",
           "sessions": [sessions[0], sessions[-1]], "arms": {"fly": arm}, "trades": run_trades,
           "generated_at": "2026-11-16T17:30:00"}  # fmt: skip
    (own / "builder-fly-2611161730.json").write_text(json.dumps(doc), encoding="utf-8")

    class H:
        pass

    h = H()
    h.sessions = sessions
    seen = {}

    def prepare(chosen, symbol, start, end, named, s, *, min_density):
        seen["prepare"] = (list(chosen), named, start, end)
        return type("Src", (), {"name": "x", "licensed": False})(), Path("x"), h, {}, {}

    def run(chosen, history, days, *, book, **_kw):
        seen.setdefault("arms", set(chosen))
        for name in chosen:
            book.trades.append(
                {"arm": name, "session": days[0], "net": {"verdict": 2.0 if "~" in name else 1.0}}
            )
        return book

    monkeypatch.setattr(drv.ab, "prepare", prepare)
    monkeypatch.setattr(drv.driver, "run", run)
    monkeypatch.setattr(drv.settings, "load", lambda _p: {})
    monkeypatch.setattr(drv.licensing, "status", lambda _s: {"active": True})
    monkeypatch.setattr(drv.ens_config, "load_config", lambda: {})
    monkeypatch.setattr(drv.events, "load_calendar", lambda path=None: None)
    split = ideasearch.split_date(sessions[0], sessions[-1])
    assert drv.main(["run", "--name", "fly", "--run", "builder-fly-2611161730", "--split", split,
                     "--out", "grid-fly-2611181000", "--draws", "50"]) == 0  # fmt: skip
    out = json.loads((own / "grid-fly-2611181000.json").read_text(encoding="utf-8"))
    assert seen["prepare"][0] == ["fly"] and seen["prepare"][1] == "x", "the run's own store, its base only"
    assert "fly" in seen["arms"] and all(n == "fly" or n.startswith("fly~") for n in seen["arms"])
    assert out["base_matches_run"] is True
    assert set(out["trades"]) == {"base", *out["grid"]}
    assert all(t["arm"] == "fly" for t in out["trades"]["base"])
