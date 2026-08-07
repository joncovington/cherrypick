"""End-to-end tests for a session book: engine decisions landing in the paper database."""

import pytest
from test_engine import BASE_CONFIG, cheap_fly_snapshot, q, snapshot

from cherrypick.flies import analytics, engine, fly
from cherrypick.flies import book as bookmod
from cherrypick.flies import db as dbmod


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


def test_freshly_opened_credit_spread_records_its_worst_case_floor(conn):
    """Regression (2026-07-30): a just-opened short vertical used to leave floor_dollars NULL
    until (if ever) it completed into a fly, so the dashboard's Floor column sat blank for the
    uncompleted branch -- exactly the case rule 4 says must be reported, not left invisible."""
    config = one_arm_config(entry_modes=["legged"])
    result = bookmod.process_snapshot(snapshot(underlying_price=5998.0), config, conn, "control")
    opened = next(a for a in result["actions"] if a["action"] == "credit_spread_opened")
    row = dbmod.book_positions(conn, result["book_id"])[0]
    assert row["position_id"] == opened["position_id"]
    assert row["kind"] == "short_vertical"
    assert row["floor_dollars"] is not None
    # Full defined risk (-W), net of trading fees and the worst-case (both legs ITM) exercise fee.
    assert row["floor_dollars"] == pytest.approx(
        fly.position_floor(
            {
                "kind": "short_vertical",
                "side": row["side"],
                "center": row["center"],
                "wing_width": row["wing_width"],
                "quantity": row["quantity"],
                "net": row["net"],
                "fees": row["fees"],
            }
        )
    )
    assert row["floor_dollars"] < 0


def test_the_forest_grows_alongside_a_completed_fly(conn):
    """Completing one structure does not stop the arm opening the next. That is the 'forest': several
    separate profit zones rather than one big bet, each standing on its own floor. Spot having drifted
    to 6004 puts the next ATM centre at 6005, clear of the fly already sitting at 6000."""
    config = one_arm_config(entry_modes=["legged"])
    bookmod.process_snapshot(snapshot(underlying_price=5998.0), config, conn, "control")
    result = bookmod.process_snapshot(
        snapshot(underlying_price=6004.0, puts={6000: q(1.0, 1.2), 6005: q(2.4, 2.6)}),
        config,
        conn,
        "control",
    )

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
        config,
        conn,
        "control",
    )

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
    config = {
        "defaults": {**BASE_CONFIG["defaults"], "entry_modes": ["legged"]},
        "arms": {"control": {}, "time_window": {}},
    }
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
    gex = {"ok": True, "per_strike": [{"strike": 6005, "call_gex": 5_000, "put_gex": 4_000}]}
    snap = snapshot(underlying_price=5998.0, gex=gex)
    gex_center, _ = engine.select_center(snap, engine.merged_params(BASE_CONFIG, "gex"))
    atm_center, _ = engine.select_center(snap, engine.merged_params(BASE_CONFIG, "control"))
    assert gex_center == 6005.0 and atm_center == 6000.0


# --------------------------------------------------------------------------- debit_first (Phase 1)
def test_debit_first_lifecycle_from_debit_vertical_to_risk_free_fly(conn):
    """Mirror of the legged lifecycle test: buy a debit vertical, complete it by SELLING a credit
    spread once spot has drifted toward the centre, end the session holding a fly for a net credit."""
    config = one_arm_config(entry_modes=["debit_first"])

    first_snap = snapshot(calls={5995: q(2.0, 2.4), 6000: q(1.0, 1.2)})
    first = bookmod.process_snapshot(first_snap, config, conn, "control")
    opened = [a for a in first["actions"] if a["action"] == "debit_vertical_opened"]
    assert len(opened) == 1
    assert first["stats"]["uncompleted_long_verticals"] == 1

    # Later the completing credit spread has richened (spot drifted TOWARD the centre).
    later = snapshot(calls={6000: q(3.0, 3.2), 6005: q(0.5, 0.6)})
    second = bookmod.process_snapshot(later, config, conn, "control")
    completed = [a for a in second["actions"] if a["action"] == "debit_completed"]
    assert len(completed) == 1
    assert completed[0]["net"] > 0
    assert completed[0]["floor"] > 0

    completed_id = completed[0]["position_id"]
    rows = dbmod.book_positions(conn, bookmod.book_id_for("2026-07-20", "control", "SPX"))
    row = next(r for r in rows if r["position_id"] == completed_id)
    assert row["kind"] == "fly", "the completion must UPDATE the position in place, not add a row"
    assert row["risk_free"] == 1
    assert row["completed_at"] is not None
    assert row["fees"] == pytest.approx(fly.vertical_open_fee("SPX", 1) * 2)


def test_freshly_opened_debit_vertical_records_its_worst_case_floor(conn):
    """The debit_first counterpart of test_freshly_opened_credit_spread_records_its_worst_case_floor
    -- floor_dollars must never be left NULL for the uncompleted branch."""
    config = one_arm_config(entry_modes=["debit_first"])
    snap = snapshot(calls={5995: q(2.0, 2.4), 6000: q(1.0, 1.2)})
    result = bookmod.process_snapshot(snap, config, conn, "control")
    opened = next(a for a in result["actions"] if a["action"] == "debit_vertical_opened")
    row = dbmod.book_positions(conn, result["book_id"])[0]
    assert row["position_id"] == opened["position_id"]
    assert row["kind"] == "long_vertical"
    assert row["floor_dollars"] is not None
    assert row["floor_dollars"] < 0


# --------------------------------------------------------------------------- post-completion counterfactual (step 1d)
def test_completed_debit_first_fly_keeps_tracking_the_completing_credit(conn):
    """The wait-for-better counterfactual: after a debit_first completion, the completing credit
    keeps being priced and its running MAX recorded. The completion tick seeds the tracker at the
    credit actually taken, a richer later quote raises it, a poorer one leaves it alone."""
    config = one_arm_config(entry_modes=["debit_first"])
    book_id = bookmod.book_id_for("2026-07-20", "control", "SPX")

    bookmod.process_snapshot(snapshot(calls={5995: q(2.0, 2.4), 6000: q(1.0, 1.2)}), config, conn, "control")
    second = bookmod.process_snapshot(
        snapshot(calls={6000: q(3.0, 3.2), 6005: q(0.5, 0.6)}), config, conn, "control"
    )
    completed = next(a for a in second["actions"] if a["action"] == "debit_completed")

    row = dbmod.book_positions(conn, book_id)[0]
    assert row["post_best_completing_credit"] == pytest.approx(completed["credit"], abs=1e-6)
    assert row["post_best_credit_at"] is not None

    bookmod.process_snapshot(snapshot(calls={6000: q(4.0, 4.2), 6005: q(0.5, 0.6)}), config, conn, "control")
    row = dbmod.book_positions(conn, book_id)[0]
    assert row["post_best_completing_credit"] > completed["credit"]
    richer = row["post_best_completing_credit"]

    bookmod.process_snapshot(snapshot(calls={6000: q(2.0, 2.2), 6005: q(0.5, 0.6)}), config, conn, "control")
    row = dbmod.book_positions(conn, book_id)[0]
    assert row["post_best_completing_credit"] == richer, "a poorer quote must not lower the running max"


def test_completed_legged_fly_keeps_tracking_the_completing_debit(conn):
    """Mirror for legged: after completion the completing debit keeps being priced and its running
    MIN recorded — 'how much cheaper would waiting have been' for the direction we already trade."""
    config = one_arm_config(entry_modes=["legged"])
    book_id = bookmod.book_id_for("2026-07-20", "control", "SPX")

    bookmod.process_snapshot(snapshot(underlying_price=5998.0), config, conn, "control")
    later = snapshot(underlying_price=6004.0, puts={6000: q(1.0, 1.2), 6005: q(2.4, 2.6)})
    second = bookmod.process_snapshot(later, config, conn, "control")
    completed = next(a for a in second["actions"] if a["action"] == "completed")

    row = next(r for r in dbmod.book_positions(conn, book_id) if r["kind"] == "fly")
    assert row["post_best_completing_debit"] == pytest.approx(completed["debit"], abs=1e-6)

    cheaper = snapshot(underlying_price=6004.0, puts={6000: q(0.4, 0.5), 6005: q(0.9, 1.0)})
    bookmod.process_snapshot(cheaper, config, conn, "control")
    row = next(r for r in dbmod.book_positions(conn, book_id) if r["kind"] == "fly")
    assert row["post_best_completing_debit"] < completed["debit"]


def test_left_on_table_reports_the_post_completion_improvement(conn):
    """analytics.left_on_table turns the recorded running max into the headline counterfactual:
    credit taken 2.5125, best seen later 3.5125 -> 1.0 pt / $100 left on the table."""
    config = one_arm_config(entry_modes=["debit_first"])
    bookmod.process_snapshot(snapshot(calls={5995: q(2.0, 2.4), 6000: q(1.0, 1.2)}), config, conn, "control")
    second = bookmod.process_snapshot(
        snapshot(calls={6000: q(3.0, 3.2), 6005: q(0.5, 0.6)}), config, conn, "control"
    )
    completed = next(a for a in second["actions"] if a["action"] == "debit_completed")
    bookmod.process_snapshot(snapshot(calls={6000: q(4.0, 4.2), 6005: q(0.5, 0.6)}), config, conn, "control")

    lot = analytics.left_on_table(conn, entry_mode="debit_first")
    assert lot["n"] == 1
    assert lot["improved"] == 1
    assert lot["median_improvement_pts"] == pytest.approx(1.0, abs=1e-6)
    assert lot["median_improvement_dollars"] == pytest.approx(100.0, abs=1e-2)
    assert lot["total_improvement_dollars"] == pytest.approx(100.0, abs=1e-2)
    # The GEX-regime split is the drift hypothesis under test — every tracked completion lands in
    # exactly one bucket, whatever the bucket's name is on this snapshot (no OI cache -> unknown).
    assert sum(s["n"] for s in lot["by_gex_bucket"].values()) == 1
    assert completed["credit"] == pytest.approx(2.5125, abs=1e-4)


# --------------------------------------------------------------------------- iron completion (Phase 1b)
def test_iron_completion_lifecycle_from_credit_spread_to_iron_fly(conn):
    """Mirror of the legged lifecycle test: sell a put spread, complete it into an IRON fly by
    SELLING the call spread once it has richened past the width+buffer gate."""
    config = one_arm_config(entry_modes=["legged"], completion_modes=["debit", "iron"])

    first = bookmod.process_snapshot(snapshot(underlying_price=5998.0), config, conn, "control")
    opened = [a for a in first["actions"] if a["action"] == "credit_spread_opened"]
    assert len(opened) == 1

    rich_calls = snapshot(calls={6000: q(4.0, 4.2), 6005: q(0.5, 0.6)})
    second = bookmod.process_snapshot(rich_calls, config, conn, "control")
    completed = [a for a in second["actions"] if a["action"] == "iron_completed"]
    assert len(completed) == 1
    assert completed[0]["net"] > 0 and completed[0]["floor"] > 0

    completed_id = completed[0]["position_id"]
    rows = dbmod.book_positions(conn, bookmod.book_id_for("2026-07-20", "control", "SPX"))
    row = next(r for r in rows if r["position_id"] == completed_id)
    assert row["kind"] == "iron_fly"
    assert row["completion_mode"] == "iron"
    assert row["risk_free"] == 1
    assert row["completed_at"] is not None


def test_completion_prefers_whichever_candidate_leaves_the_higher_floor(conn):
    """When both the debit completion and the iron completion clear their gates on the same
    iteration, the position takes whichever leaves the higher post-fee floor -- here, the iron
    completion's richer call spread beats the debit completion's modest one."""
    config = one_arm_config(entry_modes=["legged"], completion_modes=["debit", "iron"])
    bookmod.process_snapshot(snapshot(underlying_price=5998.0), config, conn, "control")

    both_cheap = snapshot(
        puts={6000: q(1.0, 1.2), 6005: q(2.4, 2.6)},  # debit completion also clears its gate
        calls={6000: q(4.8, 5.0), 6005: q(0.1, 0.2)},  # but the iron completion's floor is higher
    )
    result = bookmod.process_snapshot(both_cheap, config, conn, "control")
    completed = [a for a in result["actions"] if a["action"] in ("completed", "iron_completed")]
    assert len(completed) == 1
    assert completed[0]["action"] == "iron_completed"


def test_completion_modes_unset_behaves_exactly_like_before_iron_existed(conn):
    """Regression: an arm that never sets completion_modes (i.e. every arm except "iron") must be
    byte-identical to the pre-iron behaviour -- only the debit completion is ever evaluated."""
    config = one_arm_config(entry_modes=["legged"])  # no completion_modes override
    bookmod.process_snapshot(snapshot(underlying_price=5998.0), config, conn, "control")

    # Quotes where BOTH completions would clear their gates if iron were even evaluated.
    both_cheap = snapshot(
        puts={6000: q(1.0, 1.2), 6005: q(2.4, 2.6)},
        calls={6000: q(4.8, 5.0), 6005: q(0.1, 0.2)},
    )
    result = bookmod.process_snapshot(both_cheap, config, conn, "control")
    completed = [a for a in result["actions"] if a["action"] in ("completed", "iron_completed")]
    assert len(completed) == 1
    assert completed[0]["action"] == "completed"  # never iron -- completion_modes defaults to debit-only


# --------------------------------------------------------------------------- regime tagging (Phase 1c)
def test_regime_tags_recorded_at_entry_and_can_differ_at_completion(conn):
    """Entry and completion happen in different market states, so the two sets of regime columns
    must be recorded independently -- not copied from entry, not left blank at completion."""
    config = one_arm_config(entry_modes=["legged"])
    bookmod.process_snapshot(snapshot(underlying_price=5998.0), config, conn, "control")
    row = dbmod.book_positions(conn, bookmod.book_id_for("2026-07-20", "control", "SPX"))[0]
    assert row["entry_vol_bucket"] == "normal"
    assert row["entry_time_bucket"] == "midday"
    assert row["entry_skew_bucket"] == "flat"
    assert row["entry_gex_bucket"] == "unknown"
    assert row["completion_vol_bucket"] is None  # not completed yet

    # Complete at a later, different now_min (still midday here, but a different skew reading).
    later = snapshot(
        underlying_price=6004.0,
        now_min=13 * 60,
        puts={6000: q(1.0, 1.2), 6005: q(2.4, 2.6)},
        calls={5995: q(0.5, 0.6), 6005: q(0.2, 0.3)},
    )
    bookmod.process_snapshot(later, config, conn, "control")
    row = dbmod.book_positions(conn, bookmod.book_id_for("2026-07-20", "control", "SPX"))[0]
    assert row["kind"] == "fly"
    assert row["completion_vol_bucket"] is not None
    assert row["completion_time_bucket"] == "midday"
    # Entry-side columns are untouched by the completion write.
    assert row["entry_vol_bucket"] == "normal"


# --------------------------------------------------------------------------- bwb_roll (Phase 2)
def test_bwb_lifecycle_from_broken_wing_to_rolled_symmetric_fly(conn):
    """Enter a bwb for a net credit, then roll it once the roll cheapens enough -- ending the
    session holding a symmetric fly at (credit - roll_debit), same shape as every other
    completion lifecycle test in this file."""
    config = one_arm_config(entry_modes=["bwb_roll"], max_bwb_tail_dollars=1000)

    first_snap = snapshot(puts={5990: q(0.4, 0.6), 6000: q(1.9, 2.1), 6005: q(2.2, 2.4)})
    first = bookmod.process_snapshot(first_snap, config, conn, "control")
    opened = [a for a in first["actions"] if a["action"] == "bwb_opened"]
    assert len(opened) == 1
    assert first["stats"]["unrolled_bwbs"] == 1

    # 5995 is the strike the symmetric fly needs (centre 6000, wing 5) and is what the roll buys;
    # 6005 is the near wing, already held. Quoting both keeps the fixture honest about which leg
    # the roll actually reaches for -- it used to price off 6005 and 5995 was never in the chain.
    cheap_roll = snapshot(puts={5990: q(4.8, 5.0), 5995: q(5.0, 5.2), 6005: q(9.0, 9.2)})
    second = bookmod.process_snapshot(cheap_roll, config, conn, "control")
    rolled = [a for a in second["actions"] if a["action"] == "rolled"]
    assert len(rolled) == 1
    assert rolled[0]["net"] > 0 and rolled[0]["floor"] > 0

    rolled_id = rolled[0]["position_id"]
    rows = dbmod.book_positions(conn, bookmod.book_id_for("2026-07-20", "control", "SPX"))
    row = next(r for r in rows if r["position_id"] == rolled_id)
    assert row["kind"] == "fly", "the roll must UPDATE the position in place, not add a row"
    assert row["risk_free"] == 1
    assert row["rolled_at"] is not None
    assert row["completed_at"] == row["rolled_at"]  # one finished-structure column for all readers
    assert row["far_width"] == 10.0, "far_width is retained after the roll for history/rewind"


def test_freshly_opened_bwb_records_its_real_negative_tail_floor(conn):
    """Regression-shaped: a fresh bwb's floor_dollars must be the honest, possibly-large-negative
    tail number -- never NULL, never a fly's bounded-at-zero floor."""
    config = one_arm_config(entry_modes=["bwb_roll"], max_bwb_tail_dollars=1000)
    snap = snapshot(puts={5990: q(0.4, 0.6), 6000: q(1.9, 2.1), 6005: q(2.2, 2.4)})
    result = bookmod.process_snapshot(snap, config, conn, "control")
    opened = next(a for a in result["actions"] if a["action"] == "bwb_opened")
    row = dbmod.book_positions(conn, result["book_id"])[0]
    assert row["position_id"] == opened["position_id"]
    assert row["kind"] == "bwb"
    assert row["floor_dollars"] is not None
    assert row["floor_dollars"] < -400  # tail = -(10-5) * 100 = -500, less fees/reserve


# --------------------------------------------------------------------------- stale-checkout guard
def test_stale_writer_columns_is_clean_on_a_current_checkout(conn):
    """The healthy case: every regime column the schema declares is one this code fills."""
    assert dbmod.stale_writer_columns(conn) == []


def test_stale_writer_columns_catches_the_2026_08_05_failure(conn):
    """The real incident, reproduced: a ledger migrated by a NEWER checkout, then opened by an older
    one that has no idea those columns exist. It wrote NULL all day and nothing errored.

    Simulated by adding a regime column this code does not produce — which is exactly the state an
    older checkout sees, since migration is additive and the columns outlive the branch that made
    them. Note the check must compare code against the DATABASE; comparing the schema registry to
    `classify_regime` would pass here, because on a stale checkout both are stale together.
    """
    conn.execute("ALTER TABLE fly_positions ADD COLUMN entry_futuredim_bucket TEXT")
    conn.execute("ALTER TABLE fly_positions ADD COLUMN entry_futuredim_value REAL")
    assert dbmod.stale_writer_columns(conn) == [
        "entry_futuredim_bucket",
        "entry_futuredim_value",
    ]


def test_stale_writer_guard_ignores_ordinary_phase_prefixed_columns(conn):
    """Matched on the `_bucket`/`_value` regime convention rather than an exclusion list, so
    unrelated entry_/completion_ columns never trip it. `completion_latency_min` is the one that
    caught this out when the check was first written."""
    conn.execute("ALTER TABLE fly_positions ADD COLUMN completion_something_min REAL")
    conn.execute("ALTER TABLE fly_positions ADD COLUMN entry_some_id TEXT")
    assert dbmod.stale_writer_columns(conn) == []
