"""The hourly suite-status digest: rendering conventions, the live field, delta watermarking, and job
derivation.

Two conventions here are load-bearing suite-wide and pinned hardest:
- null is never zero — an unmeasured figure renders as an em dash, because the hour an input is
  broken is exactly the hour a $0 would mislead;
- no suite-level net — the review package refuses to sum across modules on purpose, and this card
  must not quietly reinvent the total it refused (live or paper).
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import jobspec, status_digest

DASH = "—"


def module_block(**overrides) -> dict:
    base = {
        "ok": True,
        "book": "paper",
        "health": {
            "loop_ticked": True,
            "iterations": 120,
            "entries": 4,
            "entry_attempts": {"filled": 3, "gate_blocked": 7},
        },
        "results": {"closed": 3, "gross": 500.0, "cost": 20.0, "net": 480.0, "wins": 2, "losses": 1},
        "concentration": {"sign_flips_without_largest": False},
        "carried_overnight": {"positions": 0, "capital_at_risk": None},
    }
    base.update(overrides)
    return base


def facts(**modules) -> dict:
    return {
        "session": "2026-09-01",
        "status": "provisional",
        "fact_version": 7,
        "modules": modules or {"meic": module_block()},
    }


def fly_live(**overrides) -> dict:
    """A live flies block as `_live_inputs` builds it: three completed flies, two legs still open,
    a book that settles green only inside a band."""
    block = {
        "armed": True,
        "ok": True,
        "agent": "shadow",
        "closed": {"trades": 0, "net_pnl": 0.0, "wins": 0, "losses": 0},
        "carried": {"positions": 0},
        "fly": {
            "kinds": {"fly": 3, "open vertical": 2},
            "open": 5,
            "books": [
                {
                    "arm": "control",
                    "symbol": "SPX",
                    "net_cash": 532.45,
                    "worst": -502.55,
                    "worst_at": 7815.01,
                    "floor_holds": 0,
                    "band_low": 7755.0,
                    "band_high": 7810.01,
                    "pnl": None,
                }
            ],
            # ts None = age unknown, so no "(Nm old)" — the stale case has its own test.
            "marks": {"control:SPX": {"pnl": 99.67, "spot": 7810.14, "ts": None}},
        },
    }
    block.update(overrides)
    return block


def live(modules=None, not_armed=(), broker="match") -> dict:
    if broker == "match":
        broker = {"verdict": "MATCH", "accounts": [{"pending_orders": 2}]}
    return {"modules": modules or {}, "not_armed": list(not_armed), "broker": broker}


def build(facts_doc=None, watchdog=None, morning=None, halted=False, prev=None, live_doc=None):
    return status_digest.build_digest(
        "2026-09-01", "13:00", facts_doc, watchdog, morning, halted, prev, live=live_doc
    )


def build_close():
    return status_digest.build_digest("2026-09-01", "16:35", facts(), None, None, False, None, close=True)


def field(embed: dict, name: str) -> str:
    return next(f["value"] for f in embed["fields"] if f["name"] == name)


def all_text(embed: dict, message: str) -> str:
    return (
        message
        + embed.get("description", "")
        + " ".join(f["name"] + " " + f["value"] for f in embed["fields"])
    )


# --------------------------------------------------------------------------- rendering conventions
def test_null_renders_as_em_dash_never_zero():
    """An unmeasured net/capital must be visibly unmeasured. Verified to fail: rendering None as 0
    puts '+$0' in the field and the assertion below catches both spellings."""
    doc = facts(
        meic=module_block(
            results={"closed": 2, "net": None, "wins": None, "losses": None},
            carried_overnight={"positions": 1, "capital_at_risk": None},
        )
    )
    doc["modules"]["meic"]["health"].pop("entries")  # an artifact predating fact set v7
    _, message, embed, _ = build(doc)
    paper = field(embed, "Paper")
    assert f"2 closed {DASH}" in paper
    assert f"open {DASH} at risk" in paper
    assert f"{DASH} in" in paper
    assert "0 in" not in paper
    assert "$0" not in paper and "$0" not in message


def test_carried_capital_says_how_many_positions_it_leaves_out():
    # The total is of KNOWN capital only (2026-09-24); a reader must be told it is short.
    doc = facts(
        meic=module_block(carried_overnight={"positions": 3, "capital_at_risk": 1480.0, "capital_unknown": 2})
    )
    _, _, embed, _ = build(doc)
    paper = field(embed, "Paper")
    assert "3 open $1,480 at risk (+2 unknown)" in paper  # unsigned: capital, not P&L


def test_no_suite_level_net_is_ever_printed():
    """Two modules whose nets sum to a round, recognizable figure — if any code path totals across
    modules, that figure appears somewhere in the output and this fails (verified by adding such a
    total on purpose during development). Live and paper are held to the same rule."""
    doc = facts(
        meic=module_block(results={"closed": 1, "net": 700.0, "wins": 1, "losses": 0}),
        flies=module_block(results={"closed": 1, "net": 300.0, "wins": 1, "losses": 0}),
    )
    lv = live(
        {
            "flies": fly_live(fly={}, closed={"trades": 1, "net_pnl": 1200.0, "wins": 1, "losses": 0}),
            "bwb": fly_live(fly={}, closed={"trades": 1, "net_pnl": 800.0, "wins": 1, "losses": 0}),
        }
    )
    _, message, embed, _ = build(doc, live_doc=lv)
    text = all_text(embed, message)
    assert "+$700" in text and "+$300" in text and "+$1,200" in text and "+$800" in text
    assert "1,000" not in text and "2,000" not in text


@pytest.mark.parametrize("key", ["arm", "profile"])
def test_concentration_sign_flip_gets_a_caveat(key):
    """`arm` is the fact_version 8 spelling and `profile` is every set written before it. The digest
    runs against whatever session it is handed, including a backfill of an old one, so it names the
    arm under both. Reading only one spelling would not error -- it would print the generic "largest
    arm" and lose the one fact the caveat exists to carry."""
    doc = facts(
        meic=module_block(
            concentration={
                "sign_flips_without_largest": True,
                "largest": {key: "control"},
            }
        )
    )
    _, _, embed, _ = build(doc)
    assert "net sign rests on control" in field(embed, "Paper")


def test_missing_fact_set_says_so_and_still_reports_health():
    wd = {"overall": "OK", "in_session": True, "findings": []}
    _, message, embed, snapshot = build(None, watchdog=wd)
    assert "no paper fact set for 2026-09-01 yet" in embed["description"]
    assert "watchdog OK" in embed["description"]
    assert snapshot["modules"] == {}
    assert "watchdog OK" in message
    assert not any(f["name"] == "Paper" for f in embed["fields"])


def test_watchdog_problems_surface_as_attention_and_halt_leads_the_live_field():
    wd = {
        "overall": "WARN",
        "in_session": True,
        "findings": [
            {"status": "OK", "title": "fine", "message": "x"},
            {"status": "WARN", "title": "Streamer", "message": "stale 12m. Run `run.py ps` for the rest."},
        ],
    }
    _, message, embed, _ = build(facts(), watchdog=wd, halted=True, live_doc=live())
    attention = field(embed, "Attention")
    assert "WARN: Streamer — stale 12m" in attention
    assert "run.py ps" not in attention  # the runbook prose after the first sentence is the console's
    assert "fine" not in attention  # OK findings are noise at this altitude
    assert field(embed, "Live").startswith("\U0001f6d1 LIVE HALT FLAG IS SET")
    assert "LIVE HALTED" in message


def test_an_all_clear_watchdog_spends_no_field():
    _, _, embed, _ = build(facts(), watchdog={"overall": "OK", "findings": []})
    assert not any(f["name"] == "Attention" for f in embed["fields"])


def test_unreadable_module_is_reported_not_dropped():
    doc = facts(curve={"ok": False, "reason": "ledger locked"})
    _, message, embed, snapshot = build(doc)
    assert "unreadable: ledger locked" in field(embed, "Paper")
    assert "curve unreadable" in message
    assert "curve" not in snapshot["modules"]  # a broken read must not become the next delta base


def test_quiet_modules_fold_into_one_line_and_loop_internals_stay_off_the_card():
    doc = facts(
        meic=module_block(),
        curve=module_block(
            health={"loop_ticked": True, "iterations": 9, "entries": 0}, results={"closed": 0}
        ),
        contango=module_block(health={"loop_ticked": True, "entries": 0}, results={"closed": 0}),
    )
    _, _, embed, _ = build(doc)
    paper = field(embed, "Paper")
    assert "quiet: curve, contango" in paper
    assert "iterations" not in paper and "attempts" not in paper and "gate_blocked" not in paper


def test_a_stalled_loop_is_never_folded_into_quiet():
    doc = facts(curve=module_block(health={"loop_ticked": False, "entries": 0}, results={"closed": 0}))
    _, _, embed, _ = build(doc)
    paper = field(embed, "Paper")
    assert "**curve** ⚠ loop not ticked" in paper
    assert "quiet" not in paper


def test_card_color_tracks_the_worst_of_phase_watchdog_and_broker():
    green = {"phase": {"phase": "green", "gates_met": 5, "gates_total": 5}}
    ok = {"overall": "OK", "in_session": True, "findings": []}
    assert build(facts(), ok, green)[2]["color"] == status_digest._COLOR_GREEN
    assert build(facts(), ok, {"phase": {"phase": "red"}})[2]["color"] == status_digest._COLOR_RED
    assert (
        build(facts(), {"overall": "CRITICAL", "findings": []}, green)[2]["color"] == status_digest._COLOR_RED
    )
    assert build(facts(), ok, {"phase": {"phase": "yellow"}})[2]["color"] == status_digest._COLOR_AMBER
    # A missing input can never look green — the morning pack's own missing-data rule.
    assert build(facts(), ok, None)[2]["color"] == status_digest._COLOR_SLATE
    assert build(facts(), ok, green, halted=True)[2]["color"] == status_digest._COLOR_AMBER


@pytest.mark.parametrize(
    "verdict, color, warned",
    [
        ("MISMATCH", status_digest._COLOR_RED, True),  # held disagreement: the one red verdict
        ("SETTLING", status_digest._COLOR_AMBER, True),  # seen once, usually clears next pass
        ("UNKNOWN", status_digest._COLOR_AMBER, True),
        ("IDLE", status_digest._COLOR_GREEN, False),  # nothing armed, broker not asked: no alarm
        ("MATCH", status_digest._COLOR_GREEN, False),
    ],
)
def test_broker_verdicts_are_graded_not_all_red(verdict, color, warned):
    """IDLE is the normal state on a day nothing is armed; grading it as a mismatch would turn every
    such card red all day (verified: the earlier `verdict != MATCH` rule fails IDLE and SETTLING)."""
    green = {"phase": {"phase": "green", "gates_met": 5, "gates_total": 5}}
    ok = {"overall": "OK", "findings": []}
    lv = live({"flies": fly_live()}, broker={"verdict": verdict, "accounts": []})
    _, _, embed, _ = build(facts(), ok, green, live_doc=lv)
    assert embed["color"] == color
    assert (f"⚠ broker {verdict}" in field(embed, "Live")) is warned


# --------------------------------------------------------------------------- the live field
def test_live_leads_the_card():
    _, _, embed, _ = build(facts(), live_doc=live({"flies": fly_live()}))
    assert embed["fields"][0]["name"] == "Live"


def test_live_flies_shows_structures_mark_band_worst_and_spot():
    _, message, embed, _ = build(facts(), live_doc=live({"flies": fly_live()}))
    value = field(embed, "Live")
    assert "**flies** armed · agent shadow" in value
    assert "3 flies · 2 open verticals" in value
    assert "mark +$100 · green 7755–7810 · worst -$503 @7815 · spot 7810" in value
    assert "broker MATCH · 2 orders working" in value
    assert "mark +$100" in message


def test_a_settled_live_book_shows_its_result_not_a_stale_mark():
    fly = fly_live()["fly"]
    fly["open"] = 0
    fly["books"][0]["pnl"] = -42.0
    lv = live({"flies": fly_live(fly=fly, closed={"trades": 5, "net_pnl": -42.0, "wins": 3, "losses": 2})})
    value = field(build(facts(), live_doc=lv)[2], "Live")
    assert "control SPX settled -$42" in value
    assert "5 closed 3W/2L -$42" in value
    assert "mark" not in value and "spot" not in value


def test_an_armed_module_with_nothing_on_says_so():
    lv = live({"bwb": fly_live(fly={}, agent=None)})
    assert "**bwb** armed\nno live entries yet" in field(build(facts(), live_doc=lv)[2], "Live")


def test_live_enabled_but_unarmed_is_named_once():
    value = field(build(facts(), live_doc=live(not_armed=["bwb"], broker=None))[2], "Live")
    assert value == "nothing live today\nnot armed: bwb"


def test_an_unread_or_failed_live_read_never_reads_as_quiet():
    assert field(build(facts())[2], "Live") == f"live {DASH} (not read)"
    value = field(build(facts(), live_doc={"error": "OperationalError: locked"})[2], "Live")
    assert value == "live read failed: OperationalError: locked"


def test_a_stale_mark_says_its_age():
    fly = fly_live()["fly"]
    fly["marks"]["control:SPX"]["ts"] = "2020-01-01T11:00:00-04:00"
    value = field(build(facts(), live_doc=live({"flies": fly_live(fly=fly)}))[2], "Live")
    assert "mark +$100 (" in value and "m old)" in value


def test_each_book_carries_its_own_mark_and_spot():
    fly = fly_live()["fly"]
    fly["books"].append(
        {
            **fly["books"][0],
            "symbol": "XSP",
            "band_low": 775.0,
            "band_high": 781.0,
            "worst": -50.0,
            "worst_at": 782.0,
        }
    )
    fly["marks"]["control:XSP"] = {"pnl": -7.0, "spot": 781.0, "ts": None}
    value = field(build(facts(), live_doc=live({"flies": fly_live(fly=fly)}))[2], "Live")
    assert "control SPX · mark +$100 · green 7755–7810 · worst -$503 @7815 · spot 7810" in value
    assert "control XSP · mark -$7 · green 775–781 · worst -$50 @782 · spot 781" in value


def test_a_holding_floor_still_carries_its_band():
    """Root CLAUDE.md: a book-level floor always carries the price band over which it holds."""
    fly = fly_live()["fly"]
    fly["books"][0].update(floor_holds=1, worst=40.0, unbounded_below=1)
    value = field(build(facts(), live_doc=live({"flies": fly_live(fly=fly)}))[2], "Live")
    assert "floor holds 7755–7810 (unbounded below) · worst +$40" in value


def test_the_halt_is_said_once_in_the_message():
    _, message, _, _ = build(facts(), halted=True, live_doc=live({"flies": fly_live()}))
    assert message.count("HALT") == 1
    assert "broker MATCH" in message  # the slot the repeated halt line used to take


def test_a_stale_broker_snapshot_says_its_age():
    broker = {"verdict": "MATCH", "accounts": [], "generated_at": "2020-01-01T00:00:00+00:00"}
    value = field(build(facts(), live_doc=live({"flies": fly_live()}, broker=broker))[2], "Live")
    assert "broker MATCH (" in value and "m old)" in value


def _fly_ledger(path, session="2026-09-01"):
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE fly_positions (position_id TEXT, trade_date TEXT, arm TEXT, symbol TEXT, kind TEXT,"
        " status TEXT, entry_fill_status TEXT);"
        "CREATE TABLE fly_books (trade_date TEXT, arm TEXT, symbol TEXT, net_cash REAL, worst REAL,"
        " worst_at REAL, floor_holds INTEGER, band_low REAL, band_high REAL, unbounded_below INTEGER,"
        " pnl REAL);"
        "CREATE TABLE fly_live_marks (position_id TEXT, trade_date TEXT, iteration_ts TEXT, mark_pnl REAL,"
        " spot REAL);"
    )
    conn.executemany(
        "INSERT INTO fly_positions VALUES (?, ?, 'control', ?, ?, ?, ?)",
        [
            ("a", session, "SPX", "fly", "open", "filled"),
            ("b", session, "SPX", "fly", "open", None),  # pre-live-path row: no fill status, still held
            ("c", session, "SPX", "short_vertical", "open", "filled"),
            ("d", session, "SPX", "short_vertical", "cancelled", None),  # never placed
            ("e", session, "SPX", "short_vertical", "open", "pending"),  # working at the broker: not held
            ("f", session, "XSP", "fly", "open", "filled"),  # a second book
            ("y", "2026-08-31", "SPX", "fly", "settled", "filled"),  # yesterday's
        ],
    )
    conn.execute(
        "INSERT INTO fly_books VALUES (?, 'control', 'SPX', 500, -300, 7815, 0, 7755, 7810, 0, NULL)",
        (session,),
    )
    conn.executemany(
        "INSERT INTO fly_live_marks VALUES (?, ?, ?, ?, ?)",
        [
            ("a", session, "2026-09-01T13:00:00", 10.0, 7800.0),
            ("a", session, "2026-09-01T13:05:00", 40.0, 7808.0),
            ("c", session, "2026-09-01T13:05:00", 25.0, 7808.0),
            ("e", session, "2026-09-01T13:05:00", 999.0, 7808.0),  # pending: must not reach the mark
            ("f", session, "2026-09-01T13:05:00", -3.0, 781.0),
        ],
    )
    conn.commit()
    conn.close()


def test_fly_intraday_reads_today_only_and_the_latest_mark_alone(tmp_path):
    """The mark is the LATEST iteration's sum — summing every iteration would multiply it by the
    tick count (verified: dropping the iteration_ts filter reports +$75)."""
    db = tmp_path / "live_trades.db"
    _fly_ledger(db)
    out = status_digest._fly_intraday(db, "2026-09-01")
    assert out["kinds"] == {"fly": 3, "open vertical": 1}  # the pending entry is not a held vertical
    assert out["open"] == 4
    assert out["marks"]["control:SPX"]["pnl"] == 65.0 and out["marks"]["control:SPX"]["spot"] == 7808.0
    assert out["marks"]["control:XSP"] == {"pnl": -3.0, "spot": 781.0, "ts": "2026-09-01T13:05:00"}
    assert out["books"][0]["worst"] == -300


def test_fly_intraday_reports_a_ledger_it_cannot_open(tmp_path):
    out = status_digest._fly_intraday(tmp_path / "missing" / "live_trades.db", "2026-09-01")
    assert set(out) == {"error"} and "OperationalError" in out["error"]


def _live_env(monkeypatch, modules, arm_records=None):
    """Drive `_live_inputs` with a canned `report.live_run` envelope and arm records."""
    from cherrypick.orchestrator import supervisor

    monkeypatch.setattr(status_digest.report, "live_run", lambda cfg, session: {"modules": modules})
    monkeypatch.setattr(supervisor, "read_arm_records", lambda cfg: arm_records or {})


def test_an_unreadable_unarmed_ledger_is_shown_never_dropped(monkeypatch):
    """It may hold open positions from an earlier session; 'nothing live today' would hide them."""
    _live_env(monkeypatch, {"bwb": {"ok": False, "live": True, "reason": "read failed: database is locked"}})
    lv = status_digest._live_inputs({"modules": {}}, "2026-09-01")
    value = field(build(facts(), live_doc=lv)[2], "Live")
    assert "**bwb** not armed today\nunreadable: read failed: database is locked" in value
    assert "nothing live today" not in value


def test_armed_before_the_first_trade_is_not_a_fault(monkeypatch):
    _live_env(
        monkeypatch,
        {"bwb": {"ok": False, "live": True, "reason": "no live ledger yet"}},
        {"bwb": {"date": "2026-09-01"}},
    )
    lv = status_digest._live_inputs({"modules": {}}, "2026-09-01")
    value = field(build(facts(), live_doc=lv)[2], "Live")
    assert "**bwb** armed\nno live entries yet" in value
    assert "unreadable" not in value


def test_one_failing_module_costs_its_own_lines_not_the_post(monkeypatch, tmp_path):
    """A flies ledger that will not open (locked mid-write) must show as unreadable on the card."""
    _live_env(
        monkeypatch, {"flies": {"ok": True, "live": True, "schema": "fly_book", "trades": 0, "open": {}}}
    )
    cfg = {"modules": {"flies": {"live_db": str(tmp_path / "nope" / "live_trades.db")}}}
    lv = status_digest._live_inputs(cfg, "2026-09-01")
    assert lv["modules"]["flies"]["ok"] is False
    assert "read failed" in lv["modules"]["flies"]["reason"]


def test_fly_intraday_degrades_on_an_older_ledger(tmp_path):
    db = tmp_path / "old.db"
    sqlite3.connect(db).execute(
        "CREATE TABLE fly_positions (trade_date TEXT, kind TEXT, status TEXT)"
    ).connection.close()
    out = status_digest._fly_intraday(db, "2026-09-01")
    assert out == {"kinds": {}, "open": 0}


# --------------------------------------------------------------------------- deltas
def test_a_new_close_carries_the_net_moved_since_the_last_post():
    prev = {"session": "2026-09-01", "modules": {"meic": {"closed": 1, "net": 100.0, "entries": 2}}}
    doc = facts(meic=module_block(results={"closed": 3, "net": 480.0, "wins": 2, "losses": 1}))
    _, _, embed, snapshot = build(doc, prev=prev)
    assert "3 closed 2W/1L +$480 (+$380 since last)" in field(embed, "Paper")
    assert snapshot["modules"]["meic"] == {"closed": 3, "net": 480.0}


def test_unchanged_module_shows_no_delta():
    prev = {"session": "2026-09-01", "modules": {"meic": {"closed": 3, "net": 480.0, "entries": 4}}}
    _, _, embed, _ = build(facts(), prev=prev)
    assert "since last" not in field(embed, "Paper")


def test_entries_lead_the_activity_line_even_with_no_closes():
    """'0 closed' undersells a module mid-day: entries are the day's activity number, and for the
    multi-day modules the opened and closed populations differ."""
    doc = facts(
        pmcc=module_block(
            results={"closed": 0, "gross": None, "cost": None, "net": None, "wins": 0, "losses": 0},
            health={"loop_ticked": True, "iterations": 50, "entries": 2},
        )
    )
    _, _, embed, _ = build(doc)
    assert field(embed, "Paper").startswith("**pmcc** 2 in")


def test_flies_completions_render_beside_entries():
    """Flies' lifecycle split: entered is one leg of a two-stage structure, completed is the moment
    the floor becomes a guarantee — the card shows both, and only for a module that measures it."""
    doc = facts(
        flies=module_block(health={"loop_ticked": True, "iterations": 900, "entries": 5, "completions": 4}),
        meic=module_block(),
    )
    paper = field(build(doc)[2], "Paper")
    assert "**flies** 5 in (4 done)" in paper
    assert "**meic** 4 in ·" in paper


# --------------------------------------------------------------------------- artifact/session guards
def test_a_stale_artifact_is_refused(tmp_path):
    """Yesterday's fact set presented as today's is worse than none."""
    p = tmp_path / "eod-2026-08-31.json"
    p.write_text(json.dumps({"session": "2026-08-31", "modules": {}}), encoding="utf-8")
    assert status_digest._load_session_artifact(p, "2026-09-01") is None
    assert status_digest._load_session_artifact(p, "2026-08-31") is not None


def test_run_discards_a_prior_days_watermark(isolated_state, tmp_path, monkeypatch):
    """A watermark from a previous session must not produce a delta against today. Exercised through
    run() so the state read/write path is the one under test. Data reads are pointed at an empty tmp
    home so the unit lane never reads the developer's real artifacts."""
    monkeypatch.setattr(status_digest.corehome, "data_dir", lambda pkg=None, **kw: tmp_path / (pkg or "data"))
    (isolated_state / "status_digest.json").write_text(
        json.dumps({"session": "2026-08-31", "modules": {"meic": {"closed": 99, "net": 9.0}}}),
        encoding="utf-8",
    )
    sent = {}

    class Spy:
        def __init__(self, _cfg):
            pass

        def notify(self, level, key, title, message, embed=None, **record):
            sent.update({"message": message, "embed": embed, **record})
            return {"log": {"ok": True}}

    monkeypatch.setattr(status_digest, "Notifier", Spy)
    monkeypatch.setattr(status_digest, "_refresh_facts", lambda: None)
    res = status_digest.run(cfg={"notify": {}}, force=True)
    assert res["ok"]
    assert "since last" not in json.dumps(sent["embed"])
    # No module declares a live ledger here, so the live field says nothing is live — not "not read".
    assert sent["embed"]["fields"][0] == {"name": "Live", "value": "nothing live today"}
    # The outbound record knows it as the day's digest (notifier.send_webhook).
    assert (sent["kind"], sent["session"]) == ("digest", res["session"])
    # The watermark now names today's session, so the NEXT run deltas correctly.
    saved = json.loads((isolated_state / "status_digest.json").read_text(encoding="utf-8"))
    assert saved["session"] == res["session"]


# --------------------------------------------------------------------------- job derivation
def _derive(cfg):
    from cherrypick.orchestrator import timeutil

    return jobspec.derive_jobs(cfg, pythonw="pythonw", launcher="run.py", now=timeutil.now_et())


def test_status_digest_job_is_off_by_default_with_a_reason():
    jobs, _errors = _derive({})
    job = next(j for j in jobs if j.id == "status-digest")
    assert not job.enabled
    assert "status_digest" in job.enabled_reason


def test_status_digest_job_derives_windowed_hourly_on_trading_days():
    cfg = {"status_digest": {"enabled": True}}
    job = next(j for j in _derive(cfg)[0] if j.id == "status-digest")
    assert job.enabled
    assert job.kind == jobspec.KIND_INTERVAL and job.interval_seconds == 3600
    assert (job.window_start, job.window_end) == ("10:00", "16:10")
    assert job.trading_days_only
    assert job.argv == ("pythonw", "run.py", "notify-status")


def test_the_close_card_is_its_own_daily_job_after_settlement():
    """One CLOSE card per session, after the 0DTE books settle (~16:15) and the official 16:30
    review-provisional build — the day's final intraday word."""
    cfg = {"status_digest": {"enabled": True}}
    job = next(j for j in _derive(cfg)[0] if j.id == "status-digest-close")
    assert job.enabled
    assert job.kind == jobspec.KIND_DAILY and job.at_et == "16:35"
    assert job.trading_days_only
    assert job.argv == ("pythonw", "run.py", "notify-status", "--close")

    off = next(j for j in _derive({})[0] if j.id == "status-digest-close")
    assert not off.enabled and "status_digest" in off.enabled_reason


def test_the_close_card_says_close():
    _, message, embed, _ = build_close()
    assert embed["title"].startswith("CLOSE · SUITE")
    assert message.startswith("Suite close")


def test_settings_reader_defaults():
    sd = cfgmod.status_digest_settings({})
    assert sd == {
        "enabled": False,
        "interval_minutes": 60,
        "start": "10:00",
        "end": "16:10",
        "close_at": "16:35",
        "channels": ["log"],
    }
