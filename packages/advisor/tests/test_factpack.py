"""What the pack promises: it degrades, it aggregates, it labels, and it fits in a budget."""

from __future__ import annotations

import json
import pathlib

import fakes
import pytest
from cherrypick.core import clock as core_clock

from cherrypick.advisor import clock as _clock
from cherrypick.advisor import factpack, paths, store

SESSION = "2026-08-13"

# The ceilings live in `factpack` now, derived from the plan's token targets, and are enforced
# against the REAL pack by `write` — not only here. These tests run against a seeded fixture, which
# is why they went on passing while the live deep pack grew from 250KB to 731KB across nine
# sessions: they were measuring a pack nobody reads.
LIGHT_MAX_BYTES = factpack.LIGHT_MAX_BYTES
DEEP_MAX_BYTES = factpack.DEEP_MAX_BYTES


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
    assert {"arm": "control", "outcome": "filled", "n": 1} in meic["entry_attempts"]
    assert {r["block_detail"] for r in meic["top_block_details"]} == {
        "regime_gex_negative",
        "cadence_not_clear",
    }
    assert meic["regime_session"]["ticks"] == 1
    assert meic["regime_session"]["gex_bucket"] == {"positive": 1}
    assert meic["regime_session"]["last_in_hours"]["loop_time"] == "15:05:00"

    flies = pack["paper"]["flies"]
    assert flies["books"][0]["arm"] == "control"
    assert flies["books"][0]["band_low"] == 5540.0 and flies["books"][0]["floor_holds"] == 1

    earnings = pack["paper"]["earnings"]
    assert earnings["open_positions"][0]["order_id"] == "e-1"
    assert earnings["open_positions"][0]["last_mark"] == 42.0  # the usable mark, not the refused one


def test_meic_regime_is_a_distribution_over_in_hours_ticks(seeded):
    """2026-09-15's shape: the tag read one way all session and the opposite way for the eight
    ticks after the bell, and the pack reported the eight. Post-close ticks are counted, never
    tallied; a stored `unknown` with a recorded negative sign reads `negative`."""
    meic_db = seeded / "data" / "meic" / "paper_trades.db"

    def tick(hhmmss, bucket, sign, price=5600.0):
        return {
            "loop_date": SESSION,
            "loop_time": f"{SESSION} {hhmmss}.000000-04:00",
            "symbol": "SPX",
            "underlying_price": price,
            "gex_bucket": bucket,
            "gex_positive": sign,
            "created_at": f"{SESSION}T{hhmmss}",
        }

    fakes.insert(
        meic_db,
        "iteration_regime",
        [
            tick("10:01:00", "unknown", 0),
            tick("10:02:00", "unknown", 0),
            tick("10:03:00", "unknown", 0),
            tick("11:00:00", "unknown", None),
            tick("16:03:00", "deep_positive", 1, price=5601.0),
        ],
    )
    meic = factpack.build(SESSION, "deep")["paper"]["meic"]
    block = meic["regime_session"]
    assert block["window"] == "09:30-16:00 ET"
    assert block["ticks"] == 5 and block["post_close_ticks"] == 1
    assert block["gex_bucket"] == {"negative": 3, "positive": 1, "unknown": 1}
    assert block["last_in_hours"]["loop_time"] == "15:05:00"  # not the 16:03 row
    assert "latest_regime" not in meic


def test_regime_read_is_clamped_to_the_sessions_close(monkeypatch):
    """The recorder's last sample lands seconds before the bell and the deep slot runs at 17:00:
    a read clamped to end-of-day was an hour stale every night by construction."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    seen = []

    def capture(ts, **_k):
        seen.append(ts)
        return _measured()

    monkeypatch.setattr(factpack._regime, "regime_at", capture)
    et = ZoneInfo("America/New_York")
    factpack._regime_now("2026-09-15", fallback=lambda: {})
    factpack._regime_now("2025-11-28", fallback=lambda: {})  # the day after Thanksgiving: a half day
    assert datetime.fromtimestamp(seen[0], et).strftime("%H:%M") == "16:00"
    assert datetime.fromtimestamp(seen[1], et).strftime("%H:%M") == "13:00"


def test_gex_counts_are_rth_only(seeded):
    """The recorder logs frozen off-hours copies of the closing value, so an unbounded per-date
    count double-weights whatever sign the session ended on (2026-08-21: 181/26 unfiltered vs
    67/11 in RTH — two-thirds of the "distribution" was one frozen value on repeat). The fake home
    seeds two RTH snapshots (one positive, one negative) plus one overnight copy of the negative:
    the overnight row must not be counted."""
    pack = factpack.build(SESSION, "midday")
    counts = pack["market"]["gex"]["today_counts"]
    assert counts == {"positive": 1, "negative": 1}


def test_gex_latest_snapshot_is_the_last_rth_row_not_the_overnight_copy(seeded):
    """The snapshot read the newest row of the date whatever its hour, so after the close it was
    the overnight copy -- the same off-hours rows that fed the overview's gates (2026-09-30)."""
    rth_open, rth_close = core_clock.rth_bounds(SESSION)
    latest = factpack.build(SESSION, "midday")["market"]["gex"]["latest"]
    assert latest is not None
    assert rth_open <= latest["ts"] < rth_close


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
    review.write_text(
        json.dumps({"session": SESSION, "status": "provisional", "modules": {}}), encoding="utf-8"
    )

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
    assert flies_rule["min_sessions"] == 14, "the configured rule overlays the default, never replaces it"
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
        {
            "trade_date": SESSION,
            "symbol": "SPX",
            "risk_profile": profile,
            "net_credit": 2.4,
            "wing_width": 20,
            "quantity": 1,
            "pnl": pnl,
            "fees": 6.0,
            "status": "closed",
            "exit_time": f"{SESSION}T20:10:00",
            "ic_order_id": order_id,
            "created_at": f"{SESSION}T14:31:00",
        }
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
    store.add_proposal(
        conn,
        checkpoint_id=cid,
        module="meic",
        kind="creative",
        payload={"title": "trade overnight gaps"},
        status="dismissed",
        reject_reason="user dismissed",
    )
    conn.close()

    journal = factpack.build(SESSION, "deep")["advisor_journal"]
    assert journal["proposals"][0]["fate"] == "dismissed"
    assert journal["proposals"][0]["payload"]["title"] == "trade overnight gaps"


def test_pending_proposals_compound_into_the_next_slot(seeded):
    conn = store.connect()
    cid = store.record_checkpoint(conn, session=SESSION, slot="open", model="sonnet", ok=True)
    store.add_proposal(
        conn,
        checkpoint_id=cid,
        module="flies",
        kind="bounded_adjustment",
        payload={"params": [{"param": "fee_buffer", "value": 0.1}]},
        status="proposed",
    )
    conn.close()

    pack = factpack.build(SESSION, "am1")
    assert pack["pending_proposals"][0]["slot"] == "open"
    assert pack["pending_proposals"][0]["kind"] == "bounded_adjustment"


def test_packs_stay_inside_their_token_budget(seeded, tmp_home):
    """Aggregates, not row dumps. Seed a busy day and check the serialized size."""
    busy = [
        {
            "ts": f"{SESSION}T14:{m:02d}:00",
            "trade_date": SESSION,
            "risk_profile": f"arm-{m % 4}",
            "symbol": "SPX",
            "outcome": "gate_blocked",
            "block_detail": f"reason_{m % 11}",
        }
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
    conn = _marks_db(
        tmp_path,
        [(SESSION, 1, None)] * 10 + [(SESSION, 0, "missing_leg_quotes")] * 3 + [(SESSION, 0, "stale_quote")],
    )

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
        statement
        for statement in re.findall(r'"[^"]*pmcc_positions[^"]*"', source)
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


def test_pack_size_reports_over_budget_without_raising(seeded):
    """A size check must never cost a session its advice, so it reports and returns."""
    pack = factpack.build(SESSION, "deep")
    lines = []
    out = factpack.pack_size(pack, "deep", warn=lines.append)

    assert out["ceiling"] == factpack.DEEP_MAX_BYTES
    assert out["over_budget"] is (out["bytes"] > out["ceiling"])
    assert lines == ([] if not out["over_budget"] else lines)


def test_pack_size_warns_with_the_slot_and_the_ratio():
    lines = []
    huge = {"filler": "x" * (factpack.DEEP_MAX_BYTES + 10)}
    out = factpack.pack_size(huge, "deep", warn=lines.append)

    assert out["over_budget"] is True
    assert len(lines) == 1
    assert "deep" in lines[0] and "ceiling" in lines[0]


def test_the_ceilings_are_pinned_so_a_raise_is_a_deliberate_act():
    """Moving the bar to meet the pack is how a ceiling stops meaning anything.

    Raised once, on 2026-08-26, and this test is what made that a deliberate act rather than a
    quiet one — it failed, which is the whole reason it exists. The numbers are no longer derived
    from token targets: they are ATTENTION budgets, because neither of the things a size limit
    usually protects was actually at risk. The deep pack is ~15% of a 1M-token window, and the
    whole eight-slot schedule costs about $1.40 a day. What a 470KB pack costs is that findings
    inside it go unread.

    Light moved 32,000 -> 48,000 against measured evidence: 35 stored light packs run a median of
    28.6KB and a maximum of 42,707, so the old bar was breached by honest packs. The ~8k-token
    target predated the suite having seven modules.
    """
    assert factpack.LIGHT_MAX_BYTES == 48_000
    assert factpack.DEEP_MAX_BYTES == 200_000


def test_the_deep_ceiling_stays_below_the_pack_it_is_meant_to_constrain():
    """The anti-drift half, and the reason the pin above is not just a number to bump.

    A ceiling raised until it clears the artifact reports success forever. The deep pack is ~425KB
    after the flag taper and the ceiling is 200KB, so it still reports over-budget every session —
    which is the honest state and is recorded as debt rather than hidden. If someone raises
    DEEP_MAX_BYTES past the real pack to silence the warning, this fails.

    Bounded by the smallest deep pack observed rather than the current one, so the test does not
    itself drift upward with the artifact.
    """
    assert factpack.DEEP_MAX_BYTES < 400_000


# ------------------------------------------------- the 2026-09-01 declarations (proposals #101-#103)


def test_earnings_pack_declares_the_advised_twin_pairing(tmp_home):
    """The pack flagged twin pairs as 'double-tagging' twice (#94, #103) because nothing in it said
    the pairing is the design. Now it is measured: `paired` is the by-design case, and only an
    advised row with no control under the stripped order_id is a defect."""
    earnings = fakes.make_db(paths.module_data_dir("earnings") / "paper_trades.db", fakes.EARNINGS_DDL)
    fakes.insert(
        earnings,
        "trades",
        [
            {
                "order_id": "st-ic-DELL-1",
                "symbol": "DELL",
                "expiration": "2026-08-15",
                "profile": "strat_test:iron_condor",
                "entry_credit": 12.8,
                "opened_at": 1.0,
            },
            {
                "order_id": "advised-st-ic-DELL-1",
                "symbol": "DELL",
                "expiration": "2026-08-15",
                "profile": "advised:strat_test:iron_condor",
                "entry_credit": 12.8,
                "opened_at": 1.0,
            },
            {
                "order_id": "advised-orphan-1",
                "symbol": "PANW",
                "expiration": "2026-08-15",
                "profile": "advised:strat_test:iron_condor",
                "entry_credit": 8.9,
                "opened_at": 1.0,
            },
        ],
    )
    out = factpack._earnings(SESSION)
    twins = out["advised_twins"]
    assert twins["advised_rows"] == 2
    assert twins["paired"] == 1
    assert twins["unpaired_advised"] == 1
    assert "PAIRED-TWIN DESIGN" in out["_twin_note"]


def test_bwb_pack_carves_non_base_books_out_of_the_contrast(tmp_home):
    """The 'wall' book (opt-in, 2026-08-31) trades a different structure by its own config
    declaration, but the pack's note still said 'four identical books' — so the model read wall's
    rows as the contrast silently breaking (#101). The carve-out is derived from the rows."""
    bwb = fakes.make_db(
        paths.module_data_dir("bwb") / "paper_trades.db",
        "CREATE TABLE bwb_positions (position_id TEXT, arm TEXT, symbol TEXT, entry_session TEXT,"
        " body_strike REAL, near_strike REAL, far_strike REAL, expiration TEXT, status TEXT,"
        " entry_credit REAL, armed_at TEXT, addon_fired_at TEXT, gross_pnl REAL, fees REAL,"
        " exit_reason TEXT);",
    )
    fakes.insert(
        bwb,
        "bwb_positions",
        [
            {"position_id": "p1", "arm": "control", "symbol": "SPX", "status": "open"},
            {"position_id": "p2", "arm": "advised:control", "symbol": "SPX", "status": "open"},
            {"position_id": "p3", "arm": "wall", "symbol": "SPX", "status": "open"},
        ],
    )
    out = factpack._bwb(SESSION)
    assert out["non_base_books"] == ["wall"]
    assert "SEPARATE structure" in out["_note"]


def test_flies_band_containment_separates_breach_from_containment(tmp_home):
    """The derivation proposal #102 asked for: floor_holds classified by whether the session's
    realized range stayed inside the book's band. One contained book (holds), one breached at the
    lower edge (fails) — the separation block is the headline."""
    flies = fakes.make_db(paths.module_data_dir("flies") / "paper_trades.db", fakes.FLIES_DDL)
    fakes.insert(
        flies,
        "fly_books",
        [
            {
                "book_id": "c",
                "trade_date": SESSION,
                "arm": "control",
                "symbol": "SPX",
                "band_low": 5500.0,
                "band_high": 5700.0,
                "worst": 120.0,
                "floor_holds": 1,
            },
            {
                "book_id": "a",
                "trade_date": SESSION,
                "arm": "advised:control",
                "symbol": "SPX",
                "band_low": 5560.0,
                "band_high": 5700.0,
                "worst": -900.0,
                "floor_holds": 0,
            },
        ],
    )
    gex = fakes.make_db(paths.module_data_dir("gex") / "gex_history.db", fakes.GEX_DDL)
    rth_open, _ = core_clock.rth_bounds(SESSION)
    fakes.insert(
        gex,
        "gex_regime_history",
        [
            # An overnight row filed under the session, off another expiry's chain: it must not be
            # read as the morning's walls (that was the day's first ROW until 2026-09-30).
            {
                "symbol": "SPX",
                "trade_date": SESSION,
                "ts": rth_open - 9 * 3600,
                "spot": 5590.0,
                # A band the session's range (5540..5610) breaks: reading it as the morning's walls
                # turns the 1.0 hit rate below into 0.0.
                "put_wall": 5580.0,
                "call_wall": 5600.0,
            },
            {
                "symbol": "SPX",
                "trade_date": SESSION,
                "ts": rth_open + 60,
                "spot": 5555.0,
                "put_wall": 5500.0,
                "call_wall": 5700.0,
            },
            {"symbol": "SPX", "trade_date": SESSION, "ts": rth_open + 120, "spot": 5540.0},
            {"symbol": "SPX", "trade_date": SESSION, "ts": rth_open + 180, "spot": 5610.0},
        ],
    )
    out = factpack._flies_band_containment()
    assert out["books_scored"] == 2 and out["books_unmatched"] == 0
    # control: lower margin 5540-5500=+40, upper 5700-5610=+90 -> contained, binding on the lower edge
    # advised: lower margin 5540-5560=-20 -> breached at the lower edge
    assert out["separation"]["contained"]["n"] == 1
    assert out["separation"]["contained"]["floor_holds_rate"] == 1.0
    assert out["separation"]["breached"]["n"] == 1
    assert out["separation"]["breached"]["floor_holds_rate"] == 0.0
    assert out["by_edge"]["lower"]["n"] == 2
    # The morning walls (5500..5700) contained the realized range (5540..5610).
    assert out["forecasts"]["gex_walls"] == {
        "hit_rate": 1.0,
        "n": 1,
        "_basis": out["forecasts"]["gex_walls"]["_basis"],
    }
    # No VIX1D reading or prior close seeded: the forecast reports n=0 and a null rate, never a guess.
    assert out["forecasts"]["vix1d_implied"]["n"] == 0
    assert out["forecasts"]["vix1d_implied"]["hit_rate"] is None


def test_a_book_with_no_recorded_range_is_unmatched_not_guessed(tmp_home):
    flies = fakes.make_db(paths.module_data_dir("flies") / "paper_trades.db", fakes.FLIES_DDL)
    fakes.insert(
        flies,
        "fly_books",
        [
            {
                "book_id": "c",
                "trade_date": "1999-01-01",
                "arm": "control",
                "symbol": "SPX",
                "band_low": 1.0,
                "band_high": 2.0,
                "worst": 1.0,
                "floor_holds": 1,
            }
        ],
    )
    out = factpack._flies_band_containment()
    assert out["books_scored"] == 0 and out["books_unmatched"] == 1


def test_the_band_containment_section_rides_the_deep_pack_only(tmp_home):
    assert "flies_band_containment" not in factpack.build(SESSION, "midday")
    assert "flies_band_containment" in factpack.build(SESSION, "deep")


# --------------------------------------------------------------------------- 2026-09-12 review fixes


def test_the_regime_block_is_read_at_the_session_not_at_wall_clock_now(seeded, monkeypatch):
    """Every other query in the market block takes the session; the regime read took `now`, so a
    pack rebuilt for a past date described today's regime under that date's heading."""
    from datetime import datetime

    from cherrypick.advisor import clock

    seen: list[float] = []

    def fake_regime_at(ts, **_kw):
        seen.append(ts)
        return {"market": {"status": "unmeasured", "reason": "test"}}

    monkeypatch.setattr(factpack._regime, "regime_at", fake_regime_at)
    factpack.build(SESSION, "open")
    rth_close = datetime.fromisoformat(clock.rth_close_iso(SESSION)).timestamp()
    assert seen and seen[0] == rth_close, "a past session must be read at its own close"


def test_an_unreadable_module_config_is_unknown_not_live_trading_off(tmp_home):
    """The wrong answer in the live-posture block is a safety statement to the model."""
    pack = factpack.build(SESSION, "open")
    posture = pack["live"]["posture"]
    assert posture["meic"]["enable_live_trading"] is None
    assert posture["meic"]["config_read"] is False


def test_a_read_module_config_reports_the_flag_as_written(seeded):
    fakes.write_config(seeded, "meic", {"enable_live_trading": False})
    posture = factpack.build(SESSION, "open")["live"]["posture"]
    assert posture["meic"] == {"enable_live_trading": False, "config_read": True}


def test_a_refused_query_is_named_and_never_reads_as_zero(seeded):
    """A renamed column used to make `settled_with_no_price_today` read 0 -- which the docstring
    beside it says means the settlement guard held -- and `control_fired` read False, the finding
    that the control was gated out. Both are now None, and the pack says which query it could not
    run."""
    import sqlite3

    db = paths.module_data_dir("meic") / "paper_trades.db"
    conn = sqlite3.connect(db)
    # Not `profile`/`arm`: every spelling in core.db.ARM_COLUMNS is now resolved, not refused. A
    # name no ledger has ever used is what a column that moved somewhere unexpected looks like.
    conn.execute("ALTER TABLE ic_trades RENAME COLUMN risk_profile TO variant")
    conn.commit()
    conn.close()

    pack = factpack.build(SESSION, "deep")
    assert pack["paper"]["meic"]["control_fired"]["fired"] is None
    assert pack["query_errors"], "the refused queries must be listed"
    assert any("ic_trades" in e["sql"] for e in pack["query_errors"])


def test_a_clean_build_lists_no_query_errors(seeded):
    assert factpack.build(SESSION, "open")["query_errors"] == []


def test_experiments_full_carries_one_fresh_verdict_per_experiment_not_raw_rows(seeded):
    """Measurement break 2026-09-14: the section used to dump raw store rows (every past verdict
    body, the bounds snapshot, the prose in full) at 110KB of a 439KB pack, and the verdict it
    carried for an active experiment was frozen at the first session one was attached."""
    from cherrypick.advisor import experiments, store

    fakes.write_config(seeded, "meic", fakes.advice_block({"stop_trigger_ratio": {"min": 0.85, "max": 0.95}}))
    fakes.write_suite_config(seeded, {"enabled": True, "modules": {"meic": {"enabled": True}}})
    conn = store.connect()
    # Admitted on a CURRENT session: advice is single-session and the fixture's SESSION is past.
    today = fakes.anchor_session()
    admitted = experiments.admit_spec(conn, session=today, module="meic", params={"stop_trigger_ratio": 0.9})
    assert admitted["ok"], admitted
    # A stale stored body: what the old section would have handed the model verbatim.
    store.update_experiment(
        conn, admitted["experiment_id"], sessions_run=4, verdict_json=json.dumps({"stale": True})
    )
    conn.close()

    active = factpack.build(SESSION, "deep")["experiments_full"]["active"]
    assert [e["id"] for e in active] == [admitted["experiment_id"]]
    brief = active[0]
    assert "verdict_json" not in brief and "bounds_snapshot_json" not in brief
    assert brief["verdict"]["computed_by"] == "cherrypick.advisor.verdicts"
    assert brief["verdict"]["sessions_run"] == 4, "fresh, not the stale stored body"
    assert brief["params"] == {"stop_trigger_ratio": 0.9}


# --------------------------------------------------------------------------- regime cuts (2026-09-19)
def _regime_doc(module="flies", session=SESSION, **overrides):
    doc = {
        "cut_version": 2,
        "module": module,
        "generated_at": "t",
        "session": session,
        "thin_below_sessions": 3,
        "era": {
            "start": "2026-08-21",
            "bounding_break": {"break_date": "2026-08-21", "scope": "*", "kind": "cutover"},
            "ignored_future": [{"break_date": "2026-12-18", "scope": "*", "kind": "entry_rules"}],
            "caveats": [],
        },
        "arms": [
            {
                "arm": "control",
                "era_start": "2026-08-21",
                "sessions": 19,
                "trades": 128,
                "win_rate": 0.8,
                "net_pnl": 3306.0,
                "completion_rate": 0.8,
                "dimensions": {
                    "gex": {
                        "coverage_pct": 100.0,
                        "degenerate": False,
                        "sessions": 19,
                        "effective_n": 19,
                        "underpowered": False,
                        "buckets": [
                            {
                                "bucket": "diffuse",
                                "sessions": 13,
                                "trades": 70,
                                "net_pnl": 861.0,
                                "avg_pnl": 12.3,
                                "win_rate": 0.8,
                                "completion_rate": 0.8,
                                "avg_win": 40.0,
                                "fee_drag_pct": 3.0,
                                "thin": False,
                            },
                            {
                                "bucket": "pinning",
                                "sessions": 2,
                                "trades": 4,
                                "net_pnl": 500.0,
                                "avg_pnl": 125.0,
                                "win_rate": 1.0,
                                "completion_rate": 1.0,
                                "thin": True,
                            },
                        ],
                    },
                    "skew": {
                        "coverage_pct": 20.0,
                        "degenerate": False,
                        "sessions": 4,
                        "effective_n": 4,
                        "underpowered": True,
                        "buckets": [{"bucket": "x", "sessions": 4, "trades": 5, "thin": False}],
                    },
                    "center_offset": {
                        "coverage_pct": 100.0,
                        "degenerate": True,
                        "sessions": 19,
                        "effective_n": 19,
                        "underpowered": False,
                        "buckets": [{"bucket": "at_spot", "sessions": 19, "trades": 128, "thin": False}],
                    },
                },
            },
            {
                "arm": "advised:new",
                "era_start": "2026-09-17",
                "sessions": 2,
                "trades": 9,
                "win_rate": 1.0,
                "net_pnl": 90.0,
                "dimensions": {},
            },
        ],
        "cross_tabs": [
            {
                "dims": ["gex", "trend"],
                "arms": [
                    {
                        "arm": "control",
                        "cells": [
                            {
                                "buckets": ["diffuse", "flat"],
                                "sessions": 13,
                                "trades": 39,
                                "net_pnl": 1002.0,
                                "avg_pnl": 25.0,
                                "win_rate": 0.9,
                                "thin": False,
                            },
                            {
                                "buckets": ["pinning", "up"],
                                "sessions": 2,
                                "trades": 4,
                                "net_pnl": -166.0,
                                "avg_pnl": -41.0,
                                "win_rate": 0.5,
                                "thin": True,
                            },
                        ],
                    },
                    {
                        "arm": "advised:new",
                        "cells": [
                            {"buckets": ["a", "b"], "sessions": 1, "trades": 2, "net_pnl": 1.0, "thin": True}
                        ],
                    },
                ],
            }
        ],
    }
    doc.update(overrides)
    return doc


def _as_cut_v1(doc: dict) -> dict:
    """The same document as cut_version 1 spelled it: `arms`/`arm` were `books`/`book`. Dated
    per-session artifacts written before the bump are never rewritten, so this shape is read for
    good and a test that only ever built the new one would prove nothing about them."""
    out = dict(doc)
    out["cut_version"] = 1
    out["books"] = [{("book" if k == "arm" else k): v for k, v in a.items()} for a in doc.get("arms") or []]
    out.pop("arms", None)
    out["cross_tabs"] = [
        {
            "dims": t["dims"],
            "books": [{("book" if k == "arm" else k): v for k, v in b.items()} for b in t.get("arms") or []],
        }
        for t in doc.get("cross_tabs") or []
    ]
    if "arm_column" in out:
        out["book_column"] = out.pop("arm_column")
    return out


def _write_regime_doc(tmp_home, module, doc):
    d = paths.module_data_dir(module)
    d.mkdir(parents=True, exist_ok=True)
    (d / "regime_cuts.json").write_text(json.dumps(doc), encoding="utf-8")


def test_the_regime_cuts_section_rides_the_deep_pack_only(tmp_home):
    assert "regime_cuts" not in factpack.build(SESSION, "midday")
    deep = factpack.build(SESSION, "deep")
    assert "regime_cuts" in deep and "_note" in deep["regime_cuts"]


def test_regime_cuts_absent_artifact_reports_absent_per_module(tmp_home):
    out = factpack._regime_cuts(SESSION, ("flies", "meic"))
    assert out["flies"]["_absent"] and out["meic"]["_absent"]
    assert set(out) == {"_note", "flies", "meic"}


def test_regime_cuts_thin_cells_carry_no_pnl(tmp_home):
    """Shown to fail by keeping net_pnl on a thin cell: a model reading a two-session cell's net
    is the exact misread the artifact exists to prevent. Cells are one string each."""
    _write_regime_doc(tmp_home, "flies", _regime_doc())
    out = factpack._regime_cuts(SESSION, ("flies",))["flies"]
    gex = out["arms"][0]["dimensions"]["gex"]["buckets"]
    assert gex["pinning"] == "sessions=2 trades=4 thin"
    assert gex["diffuse"] == "sessions=13 trades=70 completion=80% net=+861"
    # cross-tab: non-thin cells only, and a book left with none says so
    ct = out["cross_tabs"][0]["arms"]
    assert ct[0]["cells"] == {"diffuse/flat": "sessions=13 trades=39 win=90% net=+1002"}
    assert ct[1]["cells"] == {} and ct[1]["_all_thin"] is True


def test_regime_cuts_drops_low_coverage_and_degenerate_dimensions_and_says_so(tmp_home):
    _write_regime_doc(tmp_home, "flies", _regime_doc())
    arm = factpack._regime_cuts(SESSION, ("flies",))["flies"]["arms"][0]
    assert set(arm["dimensions"]) == {"gex"}
    assert "coverage 20.0%" in arm["_dropped"]["skew"] and "degenerate" in arm["_dropped"]["center_offset"]


def test_regime_cuts_collapses_thin_arms_and_labels_a_stale_artifact(tmp_home):
    _write_regime_doc(tmp_home, "flies", _regime_doc(session="2026-08-12"))
    out = factpack._regime_cuts(SESSION, ("flies",))["flies"]
    assert out["_stale"] == {"artifact_session": "2026-08-12", "pack_session": SESSION}
    assert out["_thin_arms"] == [{"arm": "advised:new", "sessions": 2, "trades": 9}]
    assert [b["arm"] for b in out["arms"]] == ["control"]
    assert out["era"]["ignored_future"] == ["2026-12-18"] and out["era"]["bounding_break"] == "cutover"


def test_regime_cuts_carries_flies_outcome_distributions_and_omits_them_for_meic(tmp_home):
    """Shown to fail with KeyError before the thinning copied the two keys."""
    latency = {"n": 4, "p25": 17.5, "p50": 25.0, "p75": 32.5, "max": 40.0}
    gap = {"n": 2, "min": -0.45, "p25": -0.4, "p50": -0.35, "p75": -0.3}
    flies = _regime_doc()
    flies["arms"][0]["completion_latency_min"] = latency
    flies["arms"][0]["miss_gap"] = gap
    _write_regime_doc(tmp_home, "flies", flies)
    _write_regime_doc(tmp_home, "meic", _regime_doc(module="meic"))
    out = factpack._regime_cuts(SESSION, ("flies", "meic"))
    control = out["flies"]["arms"][0]
    assert control["completion_latency_min"] == latency and control["miss_gap"] == gap
    meic = out["meic"]["arms"][0]
    assert "completion_latency_min" not in meic and "miss_gap" not in meic
    assert "miss_gap" in out["_note"]


def test_regime_cuts_marks_fragile_cells_and_lists_only_significant_pairs(tmp_home):
    """Shown to fail by dropping the p filter: the p=0.39 pair is listed beside the p=0.0002 one,
    and a model reading both as findings is the multiplicity problem the stamps exist to name."""
    doc = _regime_doc()
    gex = doc["arms"][0]["dimensions"]["gex"]
    diffuse = next(c for c in gex["buckets"] if c["bucket"] == "diffuse")
    diffuse["fragile"] = True
    diffuse["history"] = {"snapshots": 6, "first_net": -100.0, "sign_changes": 2}
    gex["paired"] = [
        {
            "a": "diffuse",
            "b": "pinning",
            "sessions": 12,
            "a_better_sessions": 8,
            "b_better_sessions": 4,
            "mean_diff_per_trade": 17.3,
            "sign_test_p": 0.3877,
        },
        {
            "a": "diffuse",
            "b": "clustered",
            "sessions": 25,
            "a_better_sessions": 3,
            "b_better_sessions": 22,
            "mean_diff_per_trade": -23.0,
            "sign_test_p": 0.0002,
        },
    ]
    doc["multiplicity"] = {"paired_tests": 2, "paired_below_alpha": 1, "paired_expected_by_chance": 0.2}
    _write_regime_doc(tmp_home, "flies", doc)
    out = factpack._regime_cuts(SESSION, ("flies",))["flies"]
    dim = out["arms"][0]["dimensions"]["gex"]
    assert dim["buckets"]["diffuse"] == "sessions=13 trades=70 completion=80% net=+861 fragile"
    assert out["paired"] == ["control/gex: diffuse>clustered 3-22/25 per_trade=-23 p=0.0002"]
    assert out["sign_changed"] == ["control/gex/diffuse: 2x over 6 snapshots, first -100.0 now 861.0"]
    assert "paired_more" not in out and out["multiplicity"] == doc["multiplicity"]
    assert "fragile" in factpack._REGIME_CUTS_NOTE and "paired" in factpack._REGIME_CUTS_NOTE


def test_regime_cuts_caps_the_paired_list_strongest_first_and_counts_the_rest(tmp_home):
    doc = _regime_doc()
    gex = doc["arms"][0]["dimensions"]["gex"]
    gex["paired"] = [
        {
            "a": f"k{i}",
            "b": "z",
            "sessions": 20,
            "a_better_sessions": 18,
            "b_better_sessions": 2,
            "mean_diff_per_trade": 1.0,
            "sign_test_p": round(0.001 * (20 - i), 4),
        }
        for i in range(factpack.REGIME_CUTS_LIST_MAX + 3)
    ]
    _write_regime_doc(tmp_home, "flies", doc)
    out = factpack._regime_cuts(SESSION, ("flies",))["flies"]
    assert len(out["paired"]) == factpack.REGIME_CUTS_LIST_MAX and out["paired_more"] == 3
    last = factpack.REGIME_CUTS_LIST_MAX + 2
    assert out["paired"][0].startswith(f"control/gex: k{last}>z")  # lowest p first


def test_regime_cuts_carries_the_flies_gate_replay_one_line_per_rule(tmp_home):
    """Shown to fail with the copy removed: miss-stop-90 was proposed without the replay its own
    bound note asks for, because the advisor reads only the pack."""

    def block(net, kept=184, losing=6, worst=-836.05):
        return {
            "entries": 184,
            "kept": kept,
            "completion_rate": 0.7935,
            "net_pnl": net,
            "days": 25,
            "losing_days": losing,
            "worst_day": worst,
        }

    doc = _regime_doc()
    doc["gate_replay"] = {
        "arm": "control",
        "start": "2026-08-21",
        "end": SESSION,
        "base": block(3690.39),
        "miss_stop": {"90": block(2593.78, kept=155, losing=9, worst=-817.85)},
        "trend_bucket": {"up_from_open": block(5109.84, kept=134, worst=-438.17)},
    }
    _write_regime_doc(tmp_home, "flies", doc)
    out = factpack._regime_cuts(SESSION, ("flies",))["flies"]["gate_replay"]
    assert out["arm"] == "control" and out["start"] == "2026-08-21"
    assert out["rules"]["miss_stop:90"] == (
        "kept=155/184 completion=79% net=+2594 (-1097) losing_days=9/25 worst_day=-817.85"
    )
    assert out["rules"]["base"].startswith("kept=184/184") and "(" not in out["rules"]["base"]
    assert set(out["rules"]) == {"base", "miss_stop:90", "trend_bucket:up_from_open"}
    assert "gate_replay" in factpack._REGIME_CUTS_NOTE


def test_regime_cuts_underpowered_is_emitted_only_when_true(tmp_home):
    doc = _regime_doc()
    doc["arms"][0]["dimensions"]["gex"]["underpowered"] = True
    _write_regime_doc(tmp_home, "flies", doc)
    out = factpack._regime_cuts(SESSION, ("flies",))["flies"]
    assert out["arms"][0]["dimensions"]["gex"]["underpowered"] is True
    doc["arms"][0]["dimensions"]["gex"]["underpowered"] = False
    _write_regime_doc(tmp_home, "flies", doc)
    out = factpack._regime_cuts(SESSION, ("flies",))["flies"]
    assert "underpowered" not in out["arms"][0]["dimensions"]["gex"]


def test_regime_cuts_unknown_cut_version_is_absent_not_misread(tmp_home):
    _write_regime_doc(tmp_home, "meic", _regime_doc(module="meic", cut_version=3))
    out = factpack._regime_cuts(SESSION, ("meic",))["meic"]
    assert "cut_version 3" in out["_absent"]


def test_regime_cuts_thinned_sections_fit_the_attention_budget(tmp_home):
    """A synthetic worst case: flies 12 books x 6 dims x 4 buckets, meic 3 books x 8 dims x 4
    buckets, every cell populated, plus a twelve-cell cross-tab per book for every pair the module
    declares -- two for flies since 2026-09-21, one for meic. The two thinned sections together
    must stay under 64 KB; twelve MATURE flies books is the far case."""

    def big(module, n_books, dims, pairs):
        arm_rows = []
        for i in range(n_books):
            book = {
                "arm": f"b{i}",
                "era_start": "2026-08-21",
                "sessions": 20,
                "trades": 400,
                "win_rate": 0.8,
                "net_pnl": 1234.56,
                "completion_rate": 0.77,
                "dimensions": {},
            }
            for dim in dims:
                book["dimensions"][dim] = {
                    "coverage_pct": 100.0,
                    "degenerate": False,
                    "sessions": 20,
                    "effective_n": 20,
                    "underpowered": False,
                    "buckets": [
                        {
                            "bucket": f"k{j}",
                            "sessions": 20,
                            "trades": 100,
                            "net_pnl": 123.45,
                            "avg_pnl": 1.23,
                            "win_rate": 0.81,
                            "completion_rate": 0.7,
                            "value_min": 0.1,
                            "value_max": 0.9,
                            "wins": 80,
                            "losses": 20,
                            "gross_pnl": 200.0,
                            "fees": 76.55,
                            "avg_win": 3.0,
                            "avg_loss": -4.0,
                            "fee_drag_pct": 38.3,
                            "profit_factor": 1.5,
                            "thin": False,
                            "fragile": True,
                            "robustness": {"net_interval": [-1.0, 1.0], "largest_session_share": 0.5},
                            "history": {"snapshots": 10, "first_net": 1.0, "sign_changes": 3},
                        }
                        for j in range(4)
                    ],
                    "paired": [
                        {
                            "a": f"k{a}",
                            "b": f"k{b}",
                            "sessions": 20,
                            "a_better_sessions": 16,
                            "b_better_sessions": 4,
                            "mean_diff_per_trade": -123.45,
                            "median_diff_per_trade": -100.0,
                            "sign_test_p": 0.0118,
                        }
                        for a in range(4)
                        for b in range(a + 1, 4)
                    ],
                }
            arm_rows.append(book)
        cells = [
            {
                "buckets": [f"g{j}", f"t{k}"],
                "sessions": 20,
                "trades": 50,
                "net_pnl": 10.0,
                "avg_pnl": 0.2,
                "win_rate": 0.8,
                "thin": False,
            }
            for j in range(4)
            for k in range(3)
        ]
        return _regime_doc(
            module=module,
            arms=arm_rows,
            cross_tabs=[
                {"dims": list(pair), "arms": [{"arm": b["arm"], "cells": cells} for b in arm_rows]}
                for pair in pairs
            ],
        )

    flies_doc = big(
        "flies",
        12,
        ["vol", "gex", "time", "skew", "center_offset", "trend", "drift_alignment"],
        [("gex", "trend"), ("gex", "drift_alignment")],
    )
    replay_block = {
        "entries": 184,
        "kept": 150,
        "completion_rate": 0.8,
        "net_pnl": -12345.67,
        "days": 25,
        "losing_days": 11,
        "worst_day": -1234.56,
    }
    flies_doc["gate_replay"] = {
        "arm": "control",
        "start": "2026-08-21",
        "end": SESSION,
        "base": replay_block,
        "miss_stop": {str(m): replay_block for m in (15, 30, 45, 60, 90)},
        "trend_bucket": {b: replay_block for b in ("up_from_open", "down_from_open")},
        "entry_windows": {f"10:00-1{i}:00,13:00-14:30": replay_block for i in range(4)},
    }
    _write_regime_doc(tmp_home, "flies", flies_doc)
    _write_regime_doc(tmp_home, "meic", big("meic", 3, [f"d{i}" for i in range(8)], [("gex", "trend")]))
    out = factpack._regime_cuts(SESSION, ("flies", "meic"))
    assert len(json.dumps(out, indent=2)) < 72_000


# --------------------------------------------------------------------------- the v8 arm rename


def _write_eod(session: str, module_block: dict) -> None:
    d = paths.module_data_dir("review")
    d.mkdir(parents=True, exist_ok=True)
    (d / f"eod-{session}.json").write_text(
        json.dumps({"session": session, "status": "final", "modules": {"meic": module_block}}),
        encoding="utf-8",
    )


@pytest.mark.parametrize("key", ["by_arm", "by_profile"])
def test_the_review_trend_reads_the_arm_split_under_either_spelling(tmp_home, key):
    """Review's fact set renamed `by_profile` -> `by_arm` at fact_version 8 and the sets written
    before it are never rewritten, so a five-session trend straddles both spellings for a working
    week after the bump. Both arrive in the pack as `by_arm`, because the model is being asked to
    compare five sessions and two spellings would read as two different things.

    The failure this prevents is silent: a dropped split leaves the session in the trend with its
    totals intact and only the arms missing, which looks like a session where nothing was split.
    """
    prior = _clock.previous_sessions(SESSION, factpack.TREND_SESSIONS)[0]
    _write_eod(prior, {"ok": True, key: {"control": {"closed": 2, "net": 10.0, "wins": 1}}})

    entry = next(e for e in factpack._review_trend(SESSION) if e["session"] == prior)
    assert entry["modules"]["meic"]["by_arm"] == {"control": {"closed": 2, "net": 10.0, "wins": 1}}
    assert "by_profile" not in entry["modules"]["meic"]


def test_a_cut_version_1_artifact_thins_exactly_like_the_version_2_one(tmp_home):
    """cut_version 2 renamed the artifact's `books`/`book` to `arms`/`arm`. The latest file is
    rewritten nightly and reaches 2 within a day, but the dated per-session copies beside it are
    never rewritten, so the pack reads 1 for good.

    Asserting the two thin to the SAME dict is the point: an equality here fails on any divergence,
    including ones this test's author did not think of. A reader that took only `arms` would not
    throw on a v1 artifact — it would emit a module with zero arms and a `_stale` block, which
    reads as a module that traded nothing rather than as an artifact that was not understood.
    """
    v2 = _regime_doc()
    _write_regime_doc(tmp_home, "flies", v2)
    from_v2 = factpack._regime_cuts(SESSION, ("flies",))["flies"]

    _write_regime_doc(tmp_home, "flies", _as_cut_v1(v2))
    from_v1 = factpack._regime_cuts(SESSION, ("flies",))["flies"]

    assert from_v1 == from_v2
    assert [a["arm"] for a in from_v1["arms"]] == ["control"]
    assert from_v1["cross_tabs"][0]["arms"][0]["arm"] == "control"


def _entry_attempt_blocks(pack: dict) -> dict[str, list]:
    """Every module's `entry_attempts` in the pack, found by walking it rather than by naming the
    modules — a hand-kept list would silently stop covering the next module to grow one."""
    found = {}
    for module, section in (pack.get("paper") or {}).items():
        rows = section.get("entry_attempts") if isinstance(section, dict) else None
        if isinstance(rows, list) and rows:
            found[module] = rows
    return found


def test_every_modules_entry_attempts_name_the_variant_arm(seeded):
    """The pack sets six modules side by side, and their ledgers spell the variant three different
    ways: meic `risk_profile`, pmcc/bwb/curve/calendars `book`, flies `arm`. Before pack_version 2
    all three reached the model in one document, which asks it to know they are the same thing.

    This covers only the modules the fixture seeds (flies and meic today), which is why the source
    test below exists as well: it holds for all six whatever the fixture happens to contain.
    """
    pack = factpack.build(SESSION, "deep")
    blocks = _entry_attempt_blocks(pack)
    assert blocks, "no module reported entry attempts; this test would pass vacuously"

    for module, rows in blocks.items():
        for row in rows:
            assert "arm" in row, f"{module} entry_attempts row has no `arm`: {sorted(row)}"
            leaked = {"book", "risk_profile", "profile"} & set(row)
            assert not leaked, f"{module} entry_attempts row still carries {sorted(leaked)}"


def test_every_entry_attempts_statement_aliases_its_arm_column(seeded):
    """The source companion to the test above, in the posture this module already uses for pmcc's
    era pooling: read the statements rather than the output, so the rule holds for the four modules
    the fixture does not seed.

    Two rules, both driven off the file so a module added later is covered the day its statement is
    written. No statement may hand the model a raw `book` or `risk_profile`; and every statement
    whose table HAS an arm column must yield it as `arm`.

    calendars is the one exception and it is not an oversight: `dc_entry_attempts` carries no arm
    column at all, because a week's double calendar is attempted once and then booked to both arms.
    It is named here so that the day it grows one, this test fails rather than quietly skipping it.
    """
    import re

    src = pathlib.Path(factpack.__file__).read_text(encoding="utf-8")
    joined = re.sub(r'"\s+"', "", src)  # statements split across adjacent literals
    stmts = re.findall(r'"(SELECT[^"]*?entry_attempts[^"]*)"', joined)
    assert len(stmts) >= 6, f"expected a statement per module, found {len(stmts)}"

    ARMLESS = ("dc_entry_attempts",)
    for stmt in stmts:
        names = [c.strip().split()[-1] for c in stmt[len("SELECT ") : stmt.index(" FROM ")].split(",")]
        leaked = {"book", "risk_profile", "profile"} & set(names)
        assert not leaked, f"hands the model a raw {sorted(leaked)}: {stmt[:90]}"
        if any(t in stmt for t in ARMLESS):
            assert "arm" not in names, f"{ARMLESS} grew an arm column; drop it from ARMLESS: {stmt[:90]}"
            continue
        assert "arm" in names, f"names no `arm` column: {stmt[:90]}"
