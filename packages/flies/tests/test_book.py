"""End-to-end tests for a session book: engine decisions landing in the paper database."""

import pytest
from test_engine import BASE_CONFIG, cheap_fly_snapshot, q, snapshot

import book as bookmod
import db as dbmod
import engine
import fly


@pytest.fixture()
def conn(tmp_path):
    return dbmod.connect(str(tmp_path / "paper_trades.db"))


def one_arm_config(**defaults):
    cfg = {"defaults": {**BASE_CONFIG["defaults"], **defaults}, "arms": {"control": {}}}
    return cfg


# --------------------------------------------------------------------------- the legged lifecycle
def test_legged_lifecycle_from_credit_spread_to_risk_free_fly(conn):
    """The full Book C mechanism through the database: sell a spread, complete it cheaper on a later
    iteration, and end the session holding a fly whose worst case is a profit."""
    config = one_arm_config(entry_modes=["legged"])

    first = bookmod.process_snapshot(snapshot(underlying_price=5998.0), config, conn, "control")
    opened = [a for a in first["actions"] if a["action"] == "credit_spread_opened"]
    assert len(opened) == 1
    assert first["stats"]["uncompleted_verticals"] == 1
    assert first["stats"]["completion_rate"] == 0.0

    # Later in the day the completing spread has cheapened (price drifted up, away from it).
    later = snapshot(underlying_price=6004.0, puts={6000: q(1.0, 1.2), 6005: q(2.4, 2.6)})
    second = bookmod.process_snapshot(later, config, conn, "control")
    completed = [a for a in second["actions"] if a["action"] == "completed"]
    assert len(completed) == 1
    assert completed[0]["net"] > 0
    assert completed[0]["floor"] > 0

    completed_id = completed[0]["position_id"]
    rows = dbmod.book_positions(conn, bookmod.book_id_for("2026-07-20", "control", "SPX"))
    row = next(r for r in rows if r["position_id"] == completed_id)
    assert row["kind"] == "fly", "the completion must UPDATE the position in place, not add a row"
    assert row["risk_free"] == 1
    assert row["completed_at"] is not None
    # Both fee stacks are charged — the guarantee is measured after costs, not before.
    assert row["fees"] == pytest.approx(fly.vertical_open_fee("SPX", 1) * 2)


def test_the_forest_grows_alongside_a_completed_fly(conn):
    """Completing one structure does not stop the arm opening the next. That is the 'forest': several
    separate profit zones rather than one big bet, each standing on its own floor. Spot having drifted
    to 6004 puts the next ATM centre at 6005, clear of the fly already sitting at 6000."""
    config = one_arm_config(entry_modes=["legged"])
    bookmod.process_snapshot(snapshot(underlying_price=5998.0), config, conn, "control")
    result = bookmod.process_snapshot(
        snapshot(underlying_price=6004.0, puts={6000: q(1.0, 1.2), 6005: q(2.4, 2.6)}),
        config, conn, "control")

    rows = dbmod.book_positions(conn, result["book_id"])
    assert len(rows) == 2
    assert {r["kind"] for r in rows} == {"fly", "short_vertical"}
    assert {r["center"] for r in rows} == {6000.0, 6005.0}


def test_uncompleted_spread_settles_as_an_ordinary_vertical(conn):
    """The branch expected to dominate. When the completion never comes, we are holding a plain
    credit spread with full defined risk — and the ledger must record it as exactly that."""
    config = one_arm_config(entry_modes=["legged"])
    bookmod.process_snapshot(snapshot(underlying_price=5998.0), config, conn, "control")

    result = bookmod.settle_book(conn, "2026-07-20", "control", "SPX", 5990.0, config)
    rows = dbmod.book_positions(conn, result["book_id"])
    assert rows[0]["kind"] == "short_vertical"
    assert rows[0]["status"] == "settled"
    assert rows[0]["pnl"] < 0, "a short put spread settling below its short strike loses"
    assert result["stats"]["completion_rate"] == 0.0


def test_pin_is_recorded_when_the_fly_finishes_inside_its_wings(conn):
    config = one_arm_config(entry_modes=["legged"])
    bookmod.process_snapshot(snapshot(underlying_price=5998.0), config, conn, "control")
    bookmod.process_snapshot(
        snapshot(underlying_price=6004.0, puts={6000: q(1.0, 1.2), 6005: q(2.4, 2.6)}),
        config, conn, "control")

    result = bookmod.settle_book(conn, "2026-07-20", "control", "SPX", 6000.5, config)
    rows = dbmod.book_positions(conn, result["book_id"])
    assert rows[0]["pinned"] == 1
    assert result["stats"]["pin_rate"] == 1.0
    # gross_pnl is before fees and pnl is after — the split the orchestrator's reader relies on.
    assert rows[0]["gross_pnl"] > rows[0]["pnl"]


# --------------------------------------------------------------------------- the funded mode
def test_outright_fly_is_blocked_until_the_book_has_premium(conn):
    """An empty book cannot fund anything, so the very first action of an outright-only arm is a
    refusal. That is the gate that bounds this mode's floor by construction."""
    config = one_arm_config(entry_modes=["outright"])
    result = bookmod.process_snapshot(cheap_fly_snapshot(), config, conn, "control")
    skips = [a for a in result["actions"] if a["action"] == "entry_skipped"]
    assert skips[0]["reason"] == "not_funded_by_realized_credit"
    assert result["cash"]["net_cash"] == 0.0


def test_book_funded_by_an_open_spread_is_not_called_risk_free(conn):
    """The distinction this module exists to enforce. A book holding an open credit spread can look
    green across the middle of its risk graph and still lose outside that spread's wings, so the roll-up
    reports `unbounded_below` and a bounded band rather than a clean floor."""
    config = one_arm_config(entry_modes=["legged", "outright"])
    bookmod.process_snapshot(snapshot(underlying_price=5998.0), config, conn, "control")
    result = bookmod.process_snapshot(cheap_fly_snapshot(), config, conn, "control")

    assert result["floor"]["unbounded_below"] is True
    assert result["floor"]["floor_holds"] is False
    assert result["floor"]["band"] is not None


# --------------------------------------------------------------------------- books stay separate
def test_each_arm_keeps_its_own_book(conn):
    """Arms must never share positions or capital — a shared book lets one lucky structure paper over
    a strategy that does not work, which is the reason MEIC moved to per-portfolio accounting."""
    config = {"defaults": {**BASE_CONFIG["defaults"], "entry_modes": ["legged"]},
              "arms": {"control": {}, "time_window": {}}}
    snap = snapshot(underlying_price=5998.0)
    a = bookmod.process_snapshot(snap, config, conn, "control")
    b = bookmod.process_snapshot(snap, config, conn, "time_window")

    assert a["book_id"] != b["book_id"]
    assert len(dbmod.book_positions(conn, a["book_id"])) == 1
    assert len(dbmod.book_positions(conn, b["book_id"])) == 1


def test_reprocessing_the_same_snapshot_does_not_duplicate_a_position(conn):
    """A mid-session restart re-runs iterations. The centre-occupied gate keeps that idempotent."""
    config = one_arm_config(entry_modes=["legged"])
    snap = snapshot(underlying_price=5998.0)
    first = bookmod.process_snapshot(snap, config, conn, "control")
    second = bookmod.process_snapshot(snap, config, conn, "control")

    assert len(dbmod.book_positions(conn, first["book_id"])) == 1
    skips = [a for a in second["actions"] if a["action"] == "entry_skipped"]
    assert skips[0]["reason"] == "center_already_occupied"


def test_book_roll_up_is_persisted_for_the_read_side(conn):
    config = one_arm_config(entry_modes=["legged"])
    bookmod.process_snapshot(snapshot(underlying_price=5998.0), config, conn, "control")
    row = conn.execute("SELECT * FROM fly_books").fetchone()
    assert row["arm"] == "control" and row["symbol"] == "SPX"
    assert row["credit_collected"] > 0
    assert row["band_low"] is not None and row["band_high"] is not None


def test_arms_differ_only_in_where_they_center(conn):
    """The comparison is only meaningful if the arms share every gate. Given the same snapshot, the
    gex arm should centre somewhere the ATM arms would not — and nothing else should change."""
    gex = {"ok": True, "per_strike": [{"strike": 6005, "net_gex": 9_000}]}
    snap = snapshot(underlying_price=5998.0, gex=gex)
    gex_center, _ = engine.select_center(snap, engine.merged_params(BASE_CONFIG, "gex"))
    atm_center, _ = engine.select_center(snap, engine.merged_params(BASE_CONFIG, "control"))
    assert gex_center == 6005.0 and atm_center == 6000.0
