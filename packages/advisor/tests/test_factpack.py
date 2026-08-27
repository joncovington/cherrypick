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


def test_gex_counts_are_rth_only(seeded):
    """The recorder logs frozen off-hours copies of the closing value, so an unbounded per-date
    count double-weights whatever sign the session ended on (2026-08-21: 181/26 unfiltered vs
    67/11 in RTH — two-thirds of the "distribution" was one frozen value on repeat). The fake home
    seeds two RTH snapshots (one positive, one negative) plus one overnight copy of the negative:
    the overnight row must not be counted."""
    pack = factpack.build(SESSION, "midday")
    counts = pack["market"]["gex"]["today_counts"]
    assert counts == {"positive": 1, "negative": 1}


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


def test_each_module_is_qualified_against_its_own_configured_rule(seeded, tmp_home):
    """The pack used to call qualify_readings() bare, so the model saw the library default while
    `calibrate` applied the module's configured rule to the same numbers — the two surfaces
    disagreed about which arms were qualified. The rule travels with the verdict now, so the model
    can cite what it was judged against."""
    fakes.write_suite_config(
        tmp_home,
        {"enabled": True},
        modules={"flies": {"calibration": {"rule": {"min_net_pnl": 0.0, "margin": 0.25}}}},
    )
    deep = factpack.build(SESSION, "deep")

    flies_rule = deep["arm_readings"]["flies"]["rule"]
    assert flies_rule["min_net_pnl"] == 0.0
    assert "margin" not in flies_rule, "margin belonged to the retired champion comparison, not the checks"
    assert flies_rule["min_days"] == 14, "the configured rule overlays the default, never replaces it"
    # A module with no calibration block is unaffected and still reports the default it was judged by.
    assert "min_net_pnl" not in deep["arm_readings"]["meic"]["rule"]

    for tag, verdict in deep["arm_readings"]["flies"]["qualification"].items():
        assert "net_pnl" in verdict["checks"], f"{tag} was not judged on money"


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


# --- the regime block: canonical series, with an honest fallback ----------------------------


def _measured(**over):
    market = {
        "status": "measured",
        "age_seconds": 12.0,
        "readings": {
            "vix": {"value": 15.9, "usable": True, "symbol": "VIX"},
            "skew": {"value": 143.9, "usable": True, "symbol": "SKEW"},
            "uso": {"value": None, "usable": False, "symbol": "USO", "reason": "stale_quote"},
        },
        "derived": {"vix_vix3m_ratio": 0.857},
        "chain": {"SPX": {"atm_iv": 0.24}},
    }
    market.update(over)
    return {"market": market, "gex": {}}


def test_regime_block_prefers_the_canonical_series(monkeypatch):
    from cherrypick.advisor import factpack

    monkeypatch.setattr(factpack._regime, "regime_at", lambda *_a, **_k: _measured())
    block = factpack._regime_now("2026-08-25", fallback=lambda: {"vix": 99.0})

    assert block["source"].startswith("market_regime_history")
    assert block["readings"] == {"vix": 15.9, "skew": 143.9}  # unusable readings are not values
    assert block["refused"] == ["uso"]  # ...but they ARE named, so a hole is legible
    assert block["derived"]["vix_vix3m_ratio"] == 0.857
    assert block["chain"]["SPX"]["atm_iv"] == 0.24
    assert "99.0" not in json.dumps(block)  # the fallback was not consulted


def test_regime_block_falls_back_and_says_so_when_unmeasured(monkeypatch):
    """A recorder outage, or any checkpoint outside RTH: the pack must not present a hole as a calm
    market, and must not silently look like the canonical read."""
    from cherrypick.advisor import factpack

    monkeypatch.setattr(
        factpack._regime,
        "regime_at",
        lambda *_a, **_k: {"market": {"status": "unmeasured", "reason": "stale_sample"}},
    )
    block = factpack._regime_now("2026-08-25", fallback=lambda: {"vix": 15.85})

    assert block["source"] == "meic.market_context (fallback)"
    assert block["reason"] == "stale_sample"
    assert block["readings"] == {"vix": 15.85}


def test_regime_block_survives_a_failing_read(monkeypatch):
    """A fact pack must never fail on a telemetry read."""
    from cherrypick.advisor import factpack

    def boom(*_a, **_k):
        raise RuntimeError("history db locked")

    monkeypatch.setattr(factpack._regime, "regime_at", boom)
    block = factpack._regime_now("2026-08-25", fallback=lambda: {"vix": 1.0})
    assert block["source"].endswith("(fallback)")
    assert block["reason"] == "regime_read_failed"


# --------------------------------------------------------------------------- mark coverage


def _marks_db(tmp_path, rows):
    import sqlite3
    conn = sqlite3.connect(tmp_path / "m.db")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE pmcc_marks (session_date TEXT, usable INTEGER, refusal TEXT)")
    conn.executemany("INSERT INTO pmcc_marks VALUES (?,?,?)", rows)
    conn.commit()
    return conn


def test_mark_coverage_states_the_denominator_in_words(tmp_path):
    """The regression this exists for. `SELECT usable, COUNT(*) n GROUP BY usable` serialises as
    `[{"usable": 1, "n": 664}]`, which the advisor read on 2026-08-24 as "1 usable of 664" and
    turned into a proposal asserting that 663 of 664 marks were unusable and that pmcc's
    assignment-exposure metric had a meaningless denominator. 664 of 664 were usable. A flag beside
    a count is two numbers that look like one ratio, and misreading it inverts the finding."""
    conn = _marks_db(tmp_path, [(SESSION, 1, None)] * 664)

    out = factpack._mark_coverage(conn, "pmcc_marks", SESSION)

    assert out["marks_total"] == 664
    assert out["marks_usable"] == 664
    assert out["marks_refused"] == 0
    assert out["usable_fraction"] == 1.0
    # No key whose value is a bare flag: every number in this dict is a count or a fraction.
    assert "usable" not in out


def test_mark_coverage_names_the_refusals(tmp_path):
    conn = _marks_db(tmp_path, [(SESSION, 1, None)] * 10
                     + [(SESSION, 0, "missing_leg_quotes")] * 3
                     + [(SESSION, 0, "stale_quote")])

    out = factpack._mark_coverage(conn, "pmcc_marks", SESSION)

    assert (out["marks_total"], out["marks_usable"], out["marks_refused"]) == (14, 10, 4)
    assert out["usable_fraction"] == 0.7143
    assert out["refusals_by_reason"][0] == {"refusal": "missing_leg_quotes", "n": 3}


def test_mark_coverage_on_a_session_with_no_marks(tmp_path):
    conn = _marks_db(tmp_path, [])
    out = factpack._mark_coverage(conn, "pmcc_marks", SESSION)
    assert out["marks_total"] == 0 and out["usable_fraction"] is None


def test_no_section_reports_a_bare_usable_flag_beside_a_count():
    """The unit tests above exercise `_mark_coverage`; they cannot see a section that stops calling
    it. This one reads the source, because the defect was never in the helper -- it was in what the
    sections emitted, and a helper nobody calls is not a fix.

    Driven off the pattern rather than a list of tables, so a module added later is covered the
    moment it writes the same query.
    """
    import inspect
    import re

    source = inspect.getsource(factpack)
    helper = inspect.getsource(factpack._mark_coverage)
    offenders = [
        match.group(0)
        for match in re.finditer(r"SELECT\s+usable,\s*COUNT\(\*\).*", source)
        if match.group(0) not in helper
    ]
    assert offenders == [], f"a section is emitting a raw usable flag beside a count: {offenders}"


def test_pmcc_lifetime_rows_are_separated_by_era():
    """pmcc's 2026-08-23 redesign cut three books to one, so its four pre-redesign rows are ONE
    trade recorded four times. Pooled with a redesign-era row they read as four observations --
    which is what the advisor read on 2026-08-24 before proposing that the book structure be
    rebuilt. Any lifetime query over pmcc_positions must carry era or it will pool the boundary."""
    import inspect
    import re

    source = inspect.getsource(factpack._pmcc)
    lifetime = [
        statement for statement in re.findall(r'"[^"]*pmcc_positions[^"]*"', source)
        if "session" not in statement
    ]
    assert lifetime, "the pmcc section stopped reading pmcc_positions"
    joined = " ".join(lifetime) + source
    assert "era" in joined, "a lifetime pmcc query pools across the redesign boundary"
    assert "GROUP BY era" in source or "GROUP BY era," in source


def test_the_deep_pack_carries_the_two_settlement_facts_that_can_recur(seeded):
    """The full audit ran once (2026-08-26) and is a settled question. What the pack carries is the
    part that can regress: two settlement prices on one session, or a side that reached expiry with
    no price and was therefore scored at full credit."""
    pack = factpack.build(SESSION, "deep")
    integrity = pack["settlement_integrity"]
    assert "settlement_prices_today" in integrity
    assert integrity["settled_with_no_price_today"] == 0


def test_settlement_integrity_is_deep_slot_only(seeded):
    """It answers a question about the whole session, and the light slots are paid for by the token."""
    assert "settlement_integrity" not in factpack.build(SESSION, "midday")


def test_the_advisor_still_depends_on_core_alone():
    """The audit it asked for lives in `meic.analytics`, and importing it here would have been the
    obvious way to surface it. This package declares `cherrypick-core` as its only dependency, so
    that import would work in a dev checkout and fail on a clean install of the advisor alone."""
    import ast
    from pathlib import Path

    src = Path(factpack.__file__).resolve().parent
    offenders = []
    for py in sorted(src.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names = [node.module]
            for name in names:
                if name.startswith("cherrypick.") and not name.startswith(
                    ("cherrypick.core", "cherrypick.advisor")
                ):
                    offenders.append(f"{py.name}: {name}")
    assert offenders == [], f"the advisor imported a module package: {offenders}"


def test_the_pack_covers_every_module_the_advisor_may_act_on():
    """Three hand-kept module lists lived in this package and one of them went stale.

    `bounds._BASE_KEY` is the source of truth — a module the advisor may resolve bounds for. Until
    2026-08-26 `factpack.MODULES` was a separate literal that omitted bwb and curve, so the pack
    could reconcile a module's enactment while carrying no facts about it: the advisor would be
    asked to design an experiment for a module it could not see. A section per module is what makes
    a proposal about it evidence-based rather than blind.
    """
    from cherrypick.advisor import bounds as _bounds

    assert factpack.MODULES == _bounds.MODULES
    missing = sorted(set(_bounds.MODULES) - set(factpack._MODULE_SECTIONS))
    assert missing == [], f"the advisor may act on these but the pack carries no facts: {missing}"


def test_no_pack_section_exists_for_a_module_the_advisor_cannot_act_on():
    """The other direction: a section for a module absent from bounds is tokens spent on a module
    that can never receive an artifact."""
    from cherrypick.advisor import bounds as _bounds

    extra = sorted(set(factpack._MODULE_SECTIONS) - set(_bounds.MODULES))
    assert extra == [], f"pack sections with no bounds entry: {extra}"
