"""What the pack promises: it degrades, it aggregates, it labels, and it fits in a budget."""

from __future__ import annotations

import json

import fakes
import pytest

from cherrypick.advisor import factpack, paths, store

SESSION = "2026-08-13"

# The pack is paid for by the token. These are generous ceilings on the JSON, not targets: light
# packs aim at ~8K tokens and deep at ~30K, and a pack that blows through them is a section that
# started dumping rows.
LIGHT_MAX_BYTES = 40_000
DEEP_MAX_BYTES = 150_000


@pytest.fixture
def seeded(tmp_home):
    fakes.seed_suite(tmp_home, SESSION)
    return tmp_home


def test_a_pack_builds_against_an_empty_home_and_says_so(tmp_home):
    """Nothing has ever run. Every section must be present and empty rather than absent or raising
    -- the model needs to be able to tell "no trades" from "no data"."""
    pack = factpack.build(SESSION, "open")
    assert pack["pack_version"] == factpack.PACK_VERSION
    assert set(pack) >= {"market", "paper", "live", "experiments", "pending_proposals"}
    assert pack["paper"]["meic"]["_absent"]
    assert pack["market"]["day_range"] == []
    assert pack["live"]["halt_flag_present"] is False


def test_the_light_pack_carries_each_modules_day(seeded):
    pack = factpack.build(SESSION, "midday")

    meic = pack["paper"]["meic"]
    assert {"profile": "control", "outcome": "filled", "n": 1} in meic["entry_attempts"]
    assert {r["block_detail"] for r in meic["top_block_details"]} == {
        "regime_gex_negative",
        "cadence_not_clear",
    }
    assert meic["latest_regime"]["loop_time"] == "15:05:00"

    flies = pack["paper"]["flies"]
    assert flies["books"][0]["arm"] == "control"
    assert flies["books"][0]["band_low"] == 5540.0 and flies["books"][0]["floor_holds"] == 1

    earnings = pack["paper"]["earnings"]
    assert earnings["open_positions"][0]["order_id"] == "e-1"
    assert earnings["open_positions"][0]["last_mark"] == 42.0  # the usable mark, not the refused one


def test_todays_range_only(seeded):
    """`stream_summary` keys on the ET trade date. A row from another day is stale by definition."""
    pack = factpack.build(SESSION, "open")
    assert [r["trade_date"] for r in pack["market"]["day_range"]] == [SESSION]


def test_live_facts_are_present_and_labeled(seeded):
    pack = factpack.build(SESSION, "open")
    live = pack["live"]
    assert "enactment is paper-only" in live["_note"]
    assert live["flies_live"]["shape"] == "flies.analytics.live_vs_paper/v1"
    assert live["flies_live"]["live"]["settled_today"]["n"] == 1
    assert live["posture"]["flies"]["arm_record"]["armed_today"] is False


def test_the_halt_flag_shows_up(seeded, tmp_home):
    (tmp_home / "state").mkdir(exist_ok=True)
    (tmp_home / "state" / "halt-live.flag").write_text("", encoding="utf-8")
    assert factpack.build(SESSION, "open")["live"]["halt_flag_present"] is True


def test_the_deep_pack_adds_what_the_deep_slot_reasons_from(seeded, tmp_home):
    stop_bounds = {"stop_trigger_ratio": {"min": 0.85, "max": 0.95}}
    fakes.write_config(tmp_home, "meic", fakes.advice_block(stop_bounds))
    review = tmp_home / "data" / "review" / f"eod-{SESSION}.json"
    review.parent.mkdir(parents=True, exist_ok=True)
    review.write_text(json.dumps({"session": SESSION, "status": "provisional", "modules": {}}),
                      encoding="utf-8")

    light = factpack.build(SESSION, "close")
    deep = factpack.build(SESSION, "deep")

    assert "arm_readings" not in light
    assert deep["review_today"]["status"] == "provisional"
    assert deep["bounds"]["meic"]["enabled"] is True
    assert deep["bounds"]["flies"]["enabled"] is False  # no advice block in that config
    assert "advised:control" in deep["arm_readings"]["meic"]["readings"]
    assert deep["advice_audit"]["meic"]["for_next_session"] is None
    assert deep["advisor_journal"]["proposals"] == []
    assert deep["arm_readings"]["meic"]["collisions"] == []  # seed_suite's arms don't collide


def test_the_deep_pack_flags_identically_reading_arms(seeded, tmp_home):
    """Found live 2026-08-14: meic's gex-open/gex-blocked read identical in every field despite
    naming opposite gate conditions. The fact pack must surface that as a collision, not let the
    model read two tags as two independent pieces of evidence."""
    meic_db = tmp_home / "data" / "meic" / "paper_trades.db"
    twin_trades = [
        {"trade_date": SESSION, "symbol": "SPX", "risk_profile": profile, "net_credit": 2.4,
         "wing_width": 20, "quantity": 1, "pnl": pnl, "fees": 6.0, "status": "closed",
         "exit_time": f"{SESSION}T20:10:00", "ic_order_id": order_id, "created_at": f"{SESSION}T14:31:00"}
        for profile in ("gex-open", "gex-blocked")
        for order_id, pnl in [(f"{profile}-1", 20.0), (f"{profile}-2", -6.0)]
    ]
    fakes.insert(meic_db, "ic_trades", twin_trades)

    deep = factpack.build(SESSION, "deep")
    collisions = deep["arm_readings"]["meic"]["collisions"]
    assert len(collisions) == 1
    assert collisions[0]["tags"] == ["gex-open", "gex-blocked"]


def test_the_journal_carries_dismissals_so_they_are_not_re_proposed(seeded):
    conn = store.connect()
    cid = store.record_checkpoint(conn, session=SESSION, slot="deep", model="opus", ok=True)
    store.add_proposal(conn, checkpoint_id=cid, module="meic", kind="creative",
                       payload={"title": "trade overnight gaps"}, status="dismissed",
                       reject_reason="user dismissed")
    conn.close()

    journal = factpack.build(SESSION, "deep")["advisor_journal"]
    assert journal["proposals"][0]["fate"] == "dismissed"
    assert journal["proposals"][0]["payload"]["title"] == "trade overnight gaps"


def test_pending_proposals_compound_into_the_next_slot(seeded):
    conn = store.connect()
    cid = store.record_checkpoint(conn, session=SESSION, slot="open", model="sonnet", ok=True)
    store.add_proposal(conn, checkpoint_id=cid, module="flies", kind="bounded_adjustment",
                       payload={"params": [{"param": "fee_buffer", "value": 0.1}]},
                       status="proposed")
    conn.close()

    pack = factpack.build(SESSION, "am1")
    assert pack["pending_proposals"][0]["slot"] == "open"
    assert pack["pending_proposals"][0]["kind"] == "bounded_adjustment"


def test_packs_stay_inside_their_token_budget(seeded, tmp_home):
    """Aggregates, not row dumps. Seed a busy day and check the serialized size."""
    busy = [
        {"ts": f"{SESSION}T14:{m:02d}:00", "trade_date": SESSION, "risk_profile": f"arm-{m % 4}",
         "symbol": "SPX", "outcome": "gate_blocked", "block_detail": f"reason_{m % 11}"}
        for m in range(0, 60)
    ]
    fakes.insert(tmp_home / "data" / "meic" / "paper_trades.db", "entry_attempts", busy)

    light = paths.pack_path(SESSION, "open")
    factpack.write(SESSION, "open")
    assert light.stat().st_size < LIGHT_MAX_BYTES, "light pack is dumping rows"

    factpack.write(SESSION, "deep")
    assert paths.pack_path(SESSION, "deep").stat().st_size < DEEP_MAX_BYTES


def test_write_puts_the_pack_where_the_script_looks_for_it(seeded):
    path = factpack.write(SESSION, "open")
    assert path == paths.pack_path(SESSION, "open")
    assert json.loads(path.read_text(encoding="utf-8"))["slot"] == "open"


def test_an_unknown_slot_is_a_programming_error_not_a_pack():
    with pytest.raises(ValueError):
        factpack.build(SESSION, "afternoon-ish")
