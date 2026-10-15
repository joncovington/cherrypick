"""The `live-shadow` arm (declared 2026-10-12, from 2026-10-15): the live configuration, in paper.

An arm declaring `mirror_live` takes its params from the live block (`engine.live_mirror_params`), so
these tests pin it to what `live_loop` trades and name, key by key, what paper cannot express.
"""

import json
import re
from pathlib import Path

import pytest
from test_live_scaffold import _loop_cfg, _snapshot

from cherrypick.flies import db as dbmod
from cherrypick.flies import engine, fly, live_loop, paper_loop

# Keys the mirror adds to the live arm's params, each the paper form of a live-block setting.
MIRROR_KEYS = {"arm", "mirror_live", "mirrors_arm", "max_open_margin_dollars", "completion_cutoff"}

# Every key of the live block, classified. A new live key fails `test_every_live_key_is_classified`
# until someone decides whether the shadow can express it.
EXPRESSED = {
    "arm": "the shadow runs the live arm's params",
    "no_entry_before": "the later of live's and the arm's, as live_loop._merged_live_params",
    "completion_cutoff": "no paper completion from the cutoff (book.py step 1), live's 15:30 default",
    "max_open_margin_dollars": "engine.margin_cap_exceeded after the engine accepts, as live",
}
NOT_EXPRESSIBLE = {
    "fresh_quote_tolerance_dollars": "live re-prices off fresh REST quotes and may skip; paper prices the cache",
    "entry_pricing": "mid_first waits a tick for a mid fill; paper fills at once at the concession",
    "max_incomplete_spreads": "count limit on incomplete live positions (null in the deployed config)",
    "max_structures_per_day": "live per-day structure budget (null in the deployed config)",
    "daily_loss_halt_dollars": "settled-net breaker over the live ledger (null in the deployed config)",
    "account_deploy_limit_pct": "a broker balance read; paper has no account",
    "negative_floor_override": "names one live position for the incomplete-count rule",
    "symbol": "paper trades the configured symbols; the shadow is read on SPX only",
}
OPERATIONAL = {
    # How live runs, not what it trades.
    "enabled",
    "gate0_confirmed",
    "settle_time",
    "disarm_time",
    "fill_watch_seconds",
    "fill_watch_poll_seconds",
    "fill_heartbeat_seconds",
    "use_order_alert_stream",
    "use_order_alert_daemon",
}
# And what paper cannot express that is not a key at all.
NOT_EXPRESSIBLE_BEHAVIOUR = (
    "fills: a live entry is a resting order the broker may not fill; paper fills on the tick it decides",
    "completion: live's limit rests at the broker and fills when the market touches it; paper completes "
    "when its modelled debit reaches the limit (and pays the limit, from 2026-10-05)",
    "cadence: live ticks once a minute, paper every 15 s",
    "quarter-end: live refuses new entries on a quarter's last session (live_loop); paper trades it",
    "halt flag, orphan sweep, held submits: live operational blocks with no paper analogue",
    "the ledger's live credit is the broker's fill price; the shadow books its floored limit",
)


def _example():
    text = (Path(__file__).resolve().parents[1] / "config.example.json").read_text(encoding="utf-8")
    return json.loads(re.sub(r"^\s*//.*$", "", text, flags=re.M))


def _with_shadow(cfg, **extra):
    cfg = json.loads(json.dumps(cfg))
    cfg.setdefault("arms", {})["live-shadow"] = {"enabled": True, "mirror_live": True, **extra}
    return cfg


def _assert_mirrors(cfg):
    live = cfg["live"]
    shadow = engine.merged_params(cfg, "live-shadow")
    traded = live_loop._merged_live_params(cfg, live.get("arm", live_loop.DEFAULT_ARM))
    strip = MIRROR_KEYS
    assert {k: v for k, v in shadow.items() if k not in strip} == {
        k: v for k, v in traded.items() if k not in strip
    }
    assert shadow["max_open_margin_dollars"] == live.get("max_open_margin_dollars")
    assert shadow["completion_cutoff"] == traded.get("completion_cutoff", live_loop.DEFAULT_CUTOFF)
    assert shadow["mirrors_arm"] == live.get("arm", live_loop.DEFAULT_ARM)
    return shadow


def test_the_shadow_trades_what_the_shipped_example_trades_live():
    shadow = _assert_mirrors(_with_shadow(_example()))
    assert shadow["no_entry_before"] == "10:15"
    assert shadow["max_open_margin_dollars"] == 1000
    assert shadow["completion_cutoff"] == "15:30"


def test_the_shadow_trades_what_this_machine_trades_live():
    path = Path.home() / ".cherrypick" / "config" / "flies.json"
    if not path.is_file():
        pytest.skip("no machine flies config")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if "live-shadow" not in (cfg.get("arms") or {}):
        cfg = _with_shadow(cfg)
    _assert_mirrors(cfg)


def test_the_mirror_fails_when_it_drifts_from_live():
    """The guard, shown to fire: a live start the mirror did not copy is a difference."""
    cfg = _with_shadow(_example())
    shadow = engine.merged_params(cfg, "live-shadow")
    traded = live_loop._merged_live_params(cfg, cfg["live"]["arm"])
    traded["no_entry_before"] = "10:30"
    assert {k: v for k, v in shadow.items() if k not in MIRROR_KEYS} != {
        k: v for k, v in traded.items() if k not in MIRROR_KEYS
    }


def test_keys_on_the_shadow_arm_itself_cannot_tune_it_away_from_live():
    cfg = _with_shadow(_example(), no_entry_before="10:00", fee_buffer=0.5, max_open_margin_dollars=9999)
    shadow = _assert_mirrors(cfg)
    assert shadow["no_entry_before"] == "10:15" and shadow["fee_buffer"] != 0.5


def test_the_shadow_follows_a_change_to_the_live_block():
    cfg = _with_shadow(_example())
    cfg["live"]["max_open_margin_dollars"] = 750
    cfg["live"]["completion_cutoff"] = "15:00"
    shadow = _assert_mirrors(cfg)
    assert shadow["max_open_margin_dollars"] == 750 and shadow["completion_cutoff"] == "15:00"


def test_a_live_arm_that_is_itself_a_mirror_is_refused():
    cfg = _with_shadow(_example())
    cfg["live"]["arm"] = "live-shadow"
    with pytest.raises(ValueError, match="nothing to mirror"):
        engine.merged_params(cfg, "live-shadow")


def test_every_live_key_is_classified():
    live_keys = {k for k in _example()["live"] if not k.startswith("_")}
    classified = set(EXPRESSED) | set(NOT_EXPRESSIBLE) | OPERATIONAL
    assert live_keys - classified == set(), "classify the new live key for the live-shadow arm"
    assert not set(EXPRESSED) & set(NOT_EXPRESSIBLE)
    assert NOT_EXPRESSIBLE_BEHAVIOUR


def test_the_shadow_is_registered_and_the_example_still_runs_control_alone():
    assert "live-shadow" in engine.ARMS
    assert "live-shadow" not in _example()["arms"]


# --------------------------------------------------------------------------- the paper gates
def _shadow_params(**live):
    cfg = _loop_cfg()
    cfg["live"].update({"max_open_margin_dollars": 1000, "no_entry_before": "10:15", **live})
    return engine.merged_params(_with_shadow(cfg), "live-shadow")


def _open_vertical(center, credit=1.0):
    return {
        "kind": "short_vertical",
        "side": "put",
        "center": center,
        "wing_width": 5,
        "quantity": 1,
        "net": credit,
        "fees": 3.44,
        "symbol": "SPX",
        "status": "open",
    }


def test_the_paper_margin_cap_agrees_with_the_live_one():
    held = [
        _open_vertical(7480.0),
        _open_vertical(7470.0, 0.8),
        {**_open_vertical(7460.0), "kind": "fly", "net": 0.4},
    ]
    plan = {"wing_width": 5, "credit": 1.15, "quantity": 1}
    for cap in (500, 1000, 1500):
        assert engine.margin_cap_exceeded(cap, held, 5, 1.15, 1) == live_loop.margin_cap_exceeded(
            cap, held, plan
        )


def test_the_shadow_refuses_an_entry_past_the_cap_and_control_does_not():
    held = [_open_vertical(7480.0), _open_vertical(7470.0), _open_vertical(7460.0)]
    enter, reason, _ = engine.evaluate_credit_spread_entry(_snapshot(), _shadow_params(), held, held)
    assert (enter, reason) == (False, "max_open_margin_reached")
    plain = engine.merged_params(_loop_cfg(), "gex")
    assert engine.evaluate_credit_spread_entry(_snapshot(), plain, held, held)[0]


def test_the_shadow_books_the_tick_floored_credit_live_would_ask():
    shadow_ok, _, shadow = engine.evaluate_credit_spread_entry(_snapshot(), _shadow_params(), [], [])
    _, _, plain = engine.evaluate_credit_spread_entry(
        _snapshot(), engine.merged_params(_loop_cfg(), "gex"), [], []
    )
    assert shadow_ok
    assert shadow["credit"] == pytest.approx(1.15) and plain["credit"] == pytest.approx(1.15)
    # A credit that is not on a nickel is floored for the shadow only.
    snap = _snapshot()
    snap["puts"][7500.0] = {**snap["puts"][7500.0], "mid": 2.63, "bid": 2.53, "ask": 2.73}
    _, _, shadow = engine.evaluate_credit_spread_entry(snap, _shadow_params(), [], [])
    _, _, plain = engine.evaluate_credit_spread_entry(snap, engine.merged_params(_loop_cfg(), "gex"), [], [])
    assert plain["credit"] == pytest.approx(1.18) and shadow["credit"] == pytest.approx(1.15)
    assert shadow["slippage"] == pytest.approx(plain["slippage"] + 0.03)


def test_the_shadow_stops_completing_at_lives_cutoff():
    shadow = _shadow_params()
    assert not engine.completion_cutoff_reached(shadow, 15 * 60 + 29)
    assert engine.completion_cutoff_reached(shadow, 15 * 60 + 30)
    assert not engine.completion_cutoff_reached(engine.merged_params(_loop_cfg(), "gex"), 15 * 60 + 59)


def test_no_entry_before_the_live_start():
    enter, reason, _ = engine.evaluate_credit_spread_entry(
        _snapshot(now_min=10 * 60 + 5), _shadow_params(), [], []
    )
    assert (enter, reason) == (False, "before_open_gate")


def test_the_arm_added_break_is_dated_the_boundary_and_needs_mirror_live(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    conn = dbmod.connect(dbmod.default_db_path())
    paper_loop._note_live_shadow_arm(conn, {"arms": {"live-shadow": {"enabled": True}}, "live": {}})
    assert [
        b for b in dbmod.measurement_breaks(conn, scope="live-shadow") if b["scope"] == "live-shadow"
    ] == []
    paper_loop._note_live_shadow_arm(conn, _with_shadow(_example()))
    rows = [b for b in dbmod.measurement_breaks(conn, scope="live-shadow") if b["scope"] == "live-shadow"]
    assert [(b["break_date"], b["kind"]) for b in rows] == [("2026-10-15", "arm_added")]
    assert "$1000" in rows[0]["reason"] and "10:15" in rows[0]["reason"]


def test_the_paper_book_stops_completing_the_shadow_at_the_cutoff_but_not_control(tmp_path):
    from test_engine import BASE_CONFIG, q, snapshot

    from cherrypick.flies import book as bookmod

    conn = dbmod.connect(str(tmp_path / "paper_trades.db"))
    cfg = {
        "defaults": {**BASE_CONFIG["defaults"], "entry_modes": ["legged"]},
        "arms": {"control": {}, "live-shadow": {"enabled": True, "mirror_live": True}},
        "live": {"arm": "control", "max_open_margin_dollars": 1000, "completion_cutoff": "15:30"},
    }
    for arm in ("control", "live-shadow"):
        opened = bookmod.process_snapshot(snapshot(underlying_price=5998.0), cfg, conn, arm)["actions"]
        assert any(a["action"] == "credit_spread_opened" for a in opened), arm
    late = snapshot(
        underlying_price=6004.0, now_min=15 * 60 + 31, puts={6000: q(1.0, 1.2), 6005: q(2.4, 2.6)}
    )
    control = bookmod.process_snapshot(late, cfg, conn, "control")["actions"]
    shadow = bookmod.process_snapshot(late, cfg, conn, "live-shadow")["actions"]
    assert any(a["action"] == "completed" for a in control)
    assert not any(a["action"] == "completed" for a in shadow)
    assert any(a.get("reason") == "completion_cutoff_reached" for a in shadow)


def test_open_margin_counts_the_same_rows_on_both_sides():
    """A completed risk-free fly contributes nothing on either side; a short vertical its worst case."""
    flyrow = {**_open_vertical(7460.0), "kind": "fly", "net": 1.0}
    assert fly.is_risk_free(flyrow)
    assert engine.margin_cap_exceeded(1, [flyrow], 5, 5, 1) == (False, 0.0)
