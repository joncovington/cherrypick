"""The selector arm: candidates gated by its own book, merged, scored on a frozen model -- and,
with no model, control's exact twin."""

import json

import pytest
from test_engine import BASE_CONFIG, q, snapshot

from cherrypick.flies import book as bookmod
from cherrypick.flies import db as dbmod
from cherrypick.flies import engine, paper_loop, selector

SOURCES = ["control", "vol-floor", "debit-first-atm"]

# Both structures are offered here: control sells the 6000/5995 put spread, debit-first-atm buys the
# 5995/6000 call spread. Low vol, trend unknown -> cell "low|unknown".
BOTH = snapshot(calls={5995: q(2.0, 2.4), 6000: q(1.0, 1.2)})
CELL = "low|unknown"


def config(vol_floor=0.0001, df_overrides=None, **selector_cfg):
    return {
        "defaults": dict(BASE_CONFIG["defaults"]),
        "arms": {
            "control": {},
            "vol-floor": {"min_entry_straddle_pct": vol_floor},
            "debit-first-atm": {"entry_modes": ["debit_first"], "center_rule": "atm", **(df_overrides or {})},
            "selector": {"entry_modes": [], "selector": {"sources": SOURCES, **selector_cfg}},
        },
    }


@pytest.fixture()
def conn(tmp_path):
    return dbmod.connect(str(tmp_path / "paper_trades.db"))


def stats(sessions=10, net=10.0, worst=-50.0):
    return {
        "sessions": sessions,
        "entries": sessions * 3,
        "net_per_entry": net,
        "completion_rate": 0.8,
        "worst_session": worst,
    }


def model(cells=None, overall=None, session="2026-07-20", margin=0.0, min_sessions=5):
    return {
        "module": "flies",
        "session": session,
        "procedure_version": selector.PROCEDURE_VERSION,
        "min_sessions": min_sessions,
        "margin": margin,
        "default": "control",
        "cells": cells or {},
        "overall": overall or {},
    }


def _book(conn, arm):
    rows = dbmod.book_positions(conn, bookmod.book_id_for("2026-07-20", arm, "SPX"))
    keys = ("kind", "side", "center", "wing_width", "net", "credit", "debit", "fees", "pnl", "status")
    return [tuple(r[k] for k in keys) for r in rows]


# --------------------------------------------------------------------------- the default twin
TICKS = [
    BOTH,
    snapshot(underlying_price=6004.0, puts={6000: q(1.0, 1.2), 6005: q(2.4, 2.6)}),
]


def test_with_no_model_the_selector_is_control_from_entry_to_settlement(conn):
    """The arm's starting property. Every position control holds, the selector holds identically --
    same structure, same prices, same completion, same settled P&L -- although debit-first-atm
    offered a different trade on the same tick."""
    cfg = config()
    for snap in TICKS:
        bookmod.process_snapshot(snap, cfg, conn, "control")
        bookmod.process_snapshot(
            snap, cfg, conn, "selector", selector_model=None, selector_model_reason="model_absent"
        )
    for arm in ("control", "selector"):
        bookmod.settle_book(conn, "2026-07-20", arm, "SPX", 6002.0, cfg)
    assert _book(conn, "control") and _book(conn, "selector") == _book(conn, "control")
    choices = conn.execute("SELECT chosen, reason FROM fly_selector_choices ORDER BY id").fetchall()
    assert tuple(choices[0]) == ("control+vol-floor", "no_model_default:model_absent")


# --------------------------------------------------------------------------- candidates
def test_candidates_are_gated_by_the_selectors_own_book():
    """Control's own book is irrelevant: what the selector already holds decides the gates. Holding
    a structure at 6000 refuses both sources' 6000 trades as duplicates -- including the debit-first
    one, because a long and a short vertical at one centre converge on one fly."""
    cfg = config()
    empty = selector.candidates(BOTH, cfg, SOURCES, [], [])
    assert all(c["enter"] for c in empty)

    held = {
        "kind": "short_vertical",
        "side": "put",
        "center": 6000.0,
        "wing_width": 5.0,
        "net": 2.0,
        "quantity": 1,
        "fees": 2.0,
        "status": "open",
        "entry_mode": "legged",
        "entry_time_min": 0,
    }
    blocked = selector.candidates(BOTH, cfg, SOURCES, [held], [held])
    assert not any(c["enter"] for c in blocked)
    assert {c["reason"] for c in blocked} <= {
        "duplicate_structure",
        "sign_rule_conflict",
        "entry_cadence_wait",
    }


def test_identical_plans_merge_and_different_trades_do_not():
    cands = selector.candidates(BOTH, config(), SOURCES, [], [])
    merged = selector.merge(cands)
    assert sorted(selector.label(m) for m in merged) == ["control+vol-floor", "debit-first-atm"]


def test_a_refusing_vol_floor_leaves_control_alone():
    cands = selector.candidates(BOTH, config(vol_floor=0.5), SOURCES, [], [])
    vf = next(c for c in cands if c["source"] == "vol-floor")
    assert (vf["enter"], vf["reason"]) == (False, "straddle_below_floor")
    assert sorted(selector.label(m) for m in selector.merge(cands)) == ["control", "debit-first-atm"]


# --------------------------------------------------------------------------- scoring
def test_a_merged_candidate_is_scored_strictest_first():
    merged = {"sources": ["control", "vol-floor"], "mode": "legged", "plan": {}}
    regime = {"vol_bucket": "low", "trend_bucket": "unknown"}
    both = model(cells={CELL: {"control": stats(net=-5.0), "vol-floor": stats(net=7.0)}})
    assert selector.score(merged, regime, both)["evidence"] == "vol-floor"
    # Vol-floor's cell is thin: control's cell scores it, before anyone's overall figure.
    thin = model(
        cells={CELL: {"control": stats(net=-5.0), "vol-floor": stats(sessions=2, net=7.0)}},
        overall={"vol-floor": stats(net=9.0)},
    )
    s = selector.score(merged, regime, thin)
    assert (s["evidence"], s["shrunk"], s["net_per_entry"]) == ("control", False, -5.0)
    # Nobody has a cell: the overall figure, flagged shrunk.
    s = selector.score(merged, regime, model(overall={"vol-floor": stats(net=9.0)}))
    assert (s["evidence"], s["shrunk"]) == ("vol-floor", True)
    assert not selector.score(merged, regime, model())["eligible"]


def _scored(m):
    merged = selector.merge(selector.candidates(BOTH, config(), SOURCES, [], []))
    regime = {"vol_bucket": "low", "trend_bucket": "unknown"}
    return [(c, selector.score(c, regime, m)) for c in merged]


def test_choose_departs_from_control_only_on_evidence():
    # Debit-first's cell beats control's: departure.
    m = model(cells={CELL: {"vol-floor": stats(net=5.0), "debit-first-atm": stats(net=12.0)}})
    choice, why = selector.choose(_scored(m), m)
    assert (selector.label(choice), why) == ("debit-first-atm", "model_preferred")
    # ...unless the margin eats the difference.
    m["margin"] = 10.0
    choice, why = selector.choose(_scored(m), m)
    assert (selector.label(choice), why) == ("control+vol-floor", "default")
    # A thin debit-first never displaces control, however good it looks.
    m = model(cells={CELL: {"debit-first-atm": stats(sessions=2, net=99.0)}})
    choice, why = selector.choose(_scored(m), m)
    assert (selector.label(choice), why) == ("control+vol-floor", "thin_default")


def test_choose_skips_only_on_the_defaults_own_negative_cell():
    m = model(cells={CELL: {"vol-floor": stats(net=-3.0)}})
    assert selector.choose(_scored(m), m) == (None, "default_cell_negative")
    # A negative OVERALL figure (shrunk) is not enough to skip.
    m = model(overall={"vol-floor": stats(net=-3.0)})
    choice, why = selector.choose(_scored(m), m)
    assert (selector.label(choice), why) == ("control+vol-floor", "default")


# --------------------------------------------------------------------------- completion follows the position
@pytest.mark.parametrize("df_overrides", [None, {"fee_buffer": 2.0}], ids=["shared", "own-buffer"])
def test_a_selector_debit_first_position_completes_as_debit_first_atm_would(conn, df_overrides):
    """The selector books debit-first's plan; on the completing tick it must complete at exactly the
    credit debit-first-atm's own book takes, and settle to the same P&L. The second case gives
    debit-first-atm a buffer no tick here clears, so a selector completing under its OWN params
    (the defaults' 0.10) would complete where the source would not."""
    cfg = config(df_overrides=df_overrides)
    m = model(cells={CELL: {"vol-floor": stats(net=-1.0), "debit-first-atm": stats(net=12.0)}})
    later = snapshot(calls={6000: q(3.0, 3.2), 6005: q(0.5, 0.6)})
    for snap in (BOTH, later):
        bookmod.process_snapshot(snap, cfg, conn, "debit-first-atm")
        bookmod.process_snapshot(snap, cfg, conn, "selector", selector_model=m)
    for arm in ("debit-first-atm", "selector"):
        bookmod.settle_book(conn, "2026-07-20", arm, "SPX", 6002.0, cfg)
    sel = dbmod.book_positions(conn, bookmod.book_id_for("2026-07-20", "selector", "SPX"))
    assert [r["selected_from"] for r in sel] == ["debit-first-atm"]
    assert sel[0]["selector_model_id"] == "selector-2026-07-20-v1"
    assert _book(conn, "selector") == _book(conn, "debit-first-atm")


# --------------------------------------------------------------------------- fitting
ROWS = [
    {
        "arm": "control",
        "trade_date": d,
        "pnl": p,
        "kind": k,
        "entry_vol_bucket": "low",
        "entry_trend_bucket": "unknown",
    }
    for d, p, k in [
        ("2026-09-01", 50.0, "fly"),
        ("2026-09-02", -200.0, "short_vertical"),
        ("2026-09-03", 30.0, "fly"),
    ]
]


def test_fit_is_truncation_invariant():
    """Adding rows on or after the session cannot change its model -- the no-look-ahead guard."""
    base = selector.fit(ROWS, through="2026-09-03", sources=SOURCES)
    future = ROWS + [
        {**ROWS[0], "trade_date": "2026-09-03", "pnl": 9999.0},
        {**ROWS[0], "trade_date": "2026-09-10"},
    ]
    assert selector.fit(future, through="2026-09-03", sources=SOURCES) == base
    assert base["overall"]["control"]["sessions"] == 2
    assert base["overall"]["control"]["net_per_entry"] == -75.0
    assert base["cells"][CELL]["control"]["worst_session"] == -200.0


def test_fit_never_pools_across_an_arms_era_start():
    m = selector.fit(ROWS, through="2026-09-04", sources=SOURCES, arm_starts={"control": "2026-09-02"})
    assert m["overall"]["control"]["sessions"] == 2
    assert m["overall"]["control"]["entries"] == 2


def test_fit_reads_each_arms_rows_only_as_its_own():
    """Vol-floor's trades sit on control's tape; fitting must never count one trade under both."""
    rows = ROWS + [{**ROWS[0], "arm": "vol-floor"}]
    m = selector.fit(rows, through="2026-09-04", sources=SOURCES)
    assert m["overall"]["control"]["entries"] == 3
    assert m["overall"]["vol-floor"]["entries"] == 1


def test_validate_model_names_each_failure():
    good = model(session="2026-09-04")
    assert selector.validate_model(good, "2026-09-04") == (True, "ok")
    assert selector.validate_model(None, "2026-09-04") == (False, "model_absent")
    assert selector.validate_model("junk", "2026-09-04") == (False, "model_malformed")
    assert selector.validate_model(good, "2026-09-05") == (False, "model_stale")
    assert selector.validate_model({**good, "procedure_version": 99}, "2026-09-04") == (
        False,
        "model_wrong_procedure",
    )


# --------------------------------------------------------------------------- the session pin
def test_the_model_decision_is_pinned_for_the_session(tmp_path, monkeypatch):
    """Absent at the first tick stays absent all day, even when a model lands at 11:00; the next
    session reads afresh."""
    monkeypatch.setenv("FLIES_DB_PATH", str(tmp_path / "paper_trades.db"))
    assert paper_loop.selector_session_model("2026-09-04") == (None, "model_absent")
    with open(paper_loop.selector_model_path("2026-09-04"), "w", encoding="utf-8") as fh:
        json.dump(model(session="2026-09-04"), fh)
    assert paper_loop.selector_session_model("2026-09-04") == (None, "model_absent")

    with open(paper_loop.selector_model_path("2026-09-05"), "w", encoding="utf-8") as fh:
        json.dump(model(session="2026-09-05"), fh)
    got, reason = paper_loop.selector_session_model("2026-09-05")
    assert reason == "ok" and got["session"] == "2026-09-05"


def test_selector_is_a_registered_arm():
    assert "selector" in engine.ARMS
