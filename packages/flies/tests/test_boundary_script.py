"""scripts/boundary_2026_10_19.py against a throwaway home: dry run writes nothing, --apply writes the
declared changes in each file's own format, and a second run finds nothing to do."""

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "boundary_2026_10_19.py"


def _load():
    spec = importlib.util.spec_from_file_location("boundary_2026_10_19", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HAND_BWB = (
    '{\r\n  "books": {\r\n    "control": { "enabled": true },\r\n    "delta": {\r\n      "enabled": true\r\n'
    '    },\r\n    "wall": {\r\n      "enabled": true,\r\n      "max_leg_spread_abs": 0.05\r\n    }\r\n  }\r\n}\r\n'
)
HAND_CURVE = '{\r\n  "books": {\r\n    "hook": {\r\n      "enabled": true\r\n    },\r\n    "near": { "enabled": true }\r\n  }\r\n}\r\n'
HAND_CALENDARS = (
    '{\r\n  "advice": {\r\n    "base_book": "control",\r\n    "bounds": {\r\n'
    '      "profit_target_pct": { "min": 0.05, "max": 0.5 },\r\n'
    '      "time_exit": { "choices": ["thu_close", "fri_noon", "fri_close"] },\r\n'
    '      "long_disposition": { "choices": ["fri_close", "mon_open"] }\r\n    }\r\n  }\r\n}\r\n'
)


def _home(tmp_path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    flies = {
        "live": {"arm": "control", "max_open_margin_dollars": 1000, "no_entry_before": "10:15"},
        "arms": {
            "control": {"enabled": True},
            **{name: {"enabled": True, "entry_modes": ["legged"]} for name in ("debit-first-up", "bwb-atm")},
            "selector": {"enabled": False, "selector": {"sources": ["control"]}},
        },
        "advice": {
            "bounds": {
                "miss_stop_minutes": {"min": 10, "max": 120},
                "refuse_trend_bucket": {"choices": ["none"]},
            }
        },
    }
    (cfg / "flies.json").write_bytes((json.dumps(flies, indent=2) + "\n").replace("\n", "\r\n").encode())
    (cfg / "bwb.json").write_bytes(HAND_BWB.encode())
    (cfg / "curve.json").write_bytes(HAND_CURVE.encode())
    (cfg / "calendars.json").write_bytes(HAND_CALENDARS.encode())
    risk = {"profiles": {"control": {"enabled": True}, "live-shadow": {"enabled": True}}}
    (cfg / "meic.risk.json").write_text(json.dumps(risk, indent=2) + "\n", encoding="utf-8")
    for module, schema in (
        ("bwb", "key"),
        ("curve", "key"),
        ("calendars", "key"),
        ("earnings", "key"),
        ("meic", "scope"),
    ):
        d = tmp_path / "data" / module
        d.mkdir(parents=True)
        conn = sqlite3.connect(str(d / "paper_trades.db"))
        if schema == "key":
            conn.execute(
                "CREATE TABLE measurement_breaks (id INTEGER PRIMARY KEY, break_date TEXT, key TEXT, old_value TEXT, "
                "new_value TEXT, note TEXT, recorded_at REAL, UNIQUE(break_date, key))"
            )
        else:
            conn.execute(
                "CREATE TABLE measurement_breaks (id INTEGER PRIMARY KEY, break_date TEXT, scope TEXT, kind TEXT, "
                "reason TEXT, detail TEXT, created_at TEXT, UNIQUE(break_date, scope, kind))"
            )
        conn.commit()
        conn.close()
    return tmp_path


def _breaks(home, module):
    conn = sqlite3.connect(str(home / "data" / module / "paper_trades.db"))
    try:
        return conn.execute("SELECT * FROM measurement_breaks").fetchall()
    finally:
        conn.close()


def test_dry_run_then_apply_then_nothing_to_do(tmp_path, monkeypatch, capsys):
    pytest.importorskip("cherrypick.earnings")
    home = _home(tmp_path)
    monkeypatch.setenv("CHERRYPICK_HOME", str(home))
    mod = _load()
    before = {p.name: p.read_bytes() for p in (home / "config").iterdir()}

    assert mod.main([]) == 0
    assert {p.name: p.read_bytes() for p in (home / "config").iterdir()} == before, "a dry run writes nothing"
    assert _breaks(home, "bwb") == []

    assert mod.main(["--apply"]) == 1, "refused before 2026-10-17 without --allow-early"
    assert mod.main(["--apply", "--allow-early"]) == 0

    flies = json.loads((home / "config" / "flies.json").read_text(encoding="utf-8"))
    assert (
        flies["arms"]["debit-first-up"]["enabled"] is False and "_retired" in flies["arms"]["debit-first-up"]
    )
    assert flies["arms"]["bwb-atm"]["enabled"] is False
    assert flies["arms"]["control"]["enabled"] is True
    assert flies["arms"]["live-shadow"]["mirror_live"] is True
    assert flies["arms"]["selector"]["enabled"] is True
    assert (
        "miss_stop_minutes" not in flies["advice"]["bounds"]
        and "refuse_trend_bucket" in flies["advice"]["bounds"]
    )
    assert "entry_pricing" not in flies["live"], "mid-first is the user's call, never the default"
    assert b"\r\n" in (home / "config" / "flies.json").read_bytes(), "line endings kept"

    bwb = json.loads((home / "config" / "bwb.json").read_text(encoding="utf-8"))
    assert bwb["books"]["delta"]["enabled"] is False and bwb["books"]["wall"]["enabled"] is False
    assert bwb["books"]["wall"]["max_leg_spread_abs"] == 0.05
    raw_bwb = (home / "config" / "bwb.json").read_bytes().decode()
    assert '"control": { "enabled": true }' in raw_bwb, "a hand-formatted file is patched, not re-dumped"

    cal_raw = (home / "config" / "calendars.json").read_bytes().decode()
    cal = json.loads(cal_raw)
    assert "time_exit" not in cal["advice"]["bounds"] and "_removed_bounds" in cal["advice"]
    assert "\n\r\n" not in cal_raw and "\r\r" not in cal_raw and cal_raw.count("\n") == cal_raw.count("\r\n")

    risk = json.loads((home / "config" / "meic.risk.json").read_text(encoding="utf-8"))
    assert {n for n in risk["profiles"] if n.startswith("live-minus-")} == set(mod.MEIC_MINUS)

    assert [r[2] for r in _breaks(home, "bwb")] == ["arms"]
    assert _breaks(home, "bwb")[0][4] == "control, bounce, flip"  # absent built-ins default on
    assert [r[2] for r in _breaks(home, "curve")] == ["arms"]
    assert [r[2] for r in _breaks(home, "calendars")] == ["advice_bounds"]
    assert [r[2] for r in _breaks(home, "earnings")] == ["strat_test_strategies"]
    assert sorted(r[2] for r in _breaks(home, "meic")) == sorted(mod.MEIC_MINUS)
    assert {r[1] for r in _breaks(home, "meic")} == {"2026-10-19"}

    after = {p.name: p.read_bytes() for p in (home / "config").iterdir()}
    capsys.readouterr()
    assert mod.main(["--apply", "--allow-early"]) == 0
    assert {p.name: p.read_bytes() for p in (home / "config").iterdir()} == after, (
        "a second run changes nothing"
    )
    assert len(_breaks(home, "meic")) == 4


HAND_BWB_ADVICE = (
    '{\r\n  "defaults": {\r\n    "delta_trigger": 0.5,\r\n    "flip_buffer": 1.001\r\n  },\r\n'
    '  "books": {\r\n    "control": { "enabled": true },\r\n    "delta": { "enabled": false }\r\n  },\r\n'
    '  "advice": {\r\n    "base_book": "control",\r\n    "bounds": {\r\n'
    '      "quantity": { "min": 1, "max": 5 },\r\n'
    '      "delta_trigger": {\r\n        "min": 0.05,\r\n        "max": 0.95\r\n      },\r\n'
    '      "flip_buffer": { "min": 1.0001, "max": 1.05 }\r\n    }\r\n  }\r\n}\r\n'
)


def test_bwb_bounds_gain_the_examples_arms_in_the_files_own_format(tmp_path, monkeypatch):
    """The advisor refuses a trigger param on a base that never arms only once the deployed bounds
    say so. The step copies the example's `arms` (bwb's own test derives them), patches a
    hand-formatted file in place, writes no ledger break (no book changed), and is idempotent."""
    (tmp_path / "config").mkdir()
    path = tmp_path / "config" / "bwb.json"
    path.write_bytes(HAND_BWB_ADVICE.encode())
    mod = _load()
    declared = {"delta_trigger": ["delta", "bounce"], "flip_buffer": ["flip"], "bounce_pullback": ["bounce"]}
    monkeypatch.setattr(mod, "_bwb_declared_arms", lambda: declared)

    plan = mod.Plan()
    mod.step_bwb(plan, tmp_path)
    old, new = plan.files[path]
    doc = json.loads(new)
    assert doc["advice"]["bounds"]["delta_trigger"]["arms"] == ["delta", "bounce"]
    assert doc["advice"]["bounds"]["flip_buffer"]["arms"] == ["flip"]
    assert "arms" not in doc["advice"]["bounds"]["quantity"]
    assert doc["defaults"] == {"delta_trigger": 0.5, "flip_buffer": 1.001}
    assert '"control": { "enabled": true }' in new and new.count("\n") == new.count("\r\n")
    assert plan.breaks == [], "an advice-bound declaration is not a bwb ledger break"

    path.write_bytes(new.encode())
    again = mod.Plan()
    mod.step_bwb(again, tmp_path)
    assert again.files == {}


def test_bwb_bounds_are_left_alone_before_the_arms_declaration_exists(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "bwb.json").write_bytes(HAND_BWB_ADVICE.encode())
    mod = _load()
    monkeypatch.setattr(mod, "_bwb_declared_arms", lambda: {})
    plan = mod.Plan()
    mod.step_bwb(plan, tmp_path)
    assert plan.files == {} and any("PR #224" in n for n in plan.notes)


def test_mid_first_only_with_its_own_flag_and_with_a_live_break(tmp_path, monkeypatch):
    pytest.importorskip("cherrypick.earnings")
    from cherrypick.flies import live_orders

    if not hasattr(live_orders, "entry_pricing_mode"):
        pytest.skip("the live pricing option is not in this checkout")
    home = _home(tmp_path)
    d = home / "data" / "flies"
    d.mkdir(parents=True)
    conn = sqlite3.connect(str(d / "live_trades.db"))
    conn.execute(
        "CREATE TABLE measurement_breaks (id INTEGER PRIMARY KEY, break_date TEXT, scope TEXT, kind TEXT, "
        "reason TEXT, detail TEXT, created_at TEXT, UNIQUE(break_date, scope, kind))"
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("CHERRYPICK_HOME", str(home))
    assert _load().main(["--apply", "--allow-early", "--enable-flies-mid-first"]) == 0
    assert (
        json.loads((home / "config" / "flies.json").read_text(encoding="utf-8"))["live"]["entry_pricing"]
        == "mid_first"
    )
    conn = sqlite3.connect(str(d / "live_trades.db"))
    assert conn.execute("SELECT break_date, scope, kind FROM measurement_breaks").fetchall() == [
        ("2026-10-19", "*", "entry_pricing")
    ]
    conn.close()
