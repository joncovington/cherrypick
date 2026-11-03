"""What the live loop tells a person (2026-11-02): each held IC named in words in `--status`, and the
loop's own CRITICAL lines recorded for the watchdog to push. Before, an alert said "1 open live
position(s)", and a CRITICAL reached only the loop's own log."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_live_scaffold import _config, _init_db, _open_trade_row  # noqa: E402

from cherrypick.meic import live_loop, paper  # noqa: E402


def test_an_ic_is_described_by_what_is_still_held():
    row = {**_open_trade_row("XSP"), "stop_trigger_current": 2.0}
    assert (
        live_loop.describe_ic(row)
        == "OPEN1 XSP 583p/598c 5w, credit 1.07, holds put and call, stop 2.0x credit"
    )
    assert "holds call" in live_loop.describe_ic({**row, "status": "partial", "put_stop_cost": 3.1})
    assert "entry pending" in live_loop.describe_ic({**row, "status": "pending"})


def test_the_status_names_todays_held_ics(monkeypatch):
    """Shown to fail without `open_ics`: the watchdog had only a count to quote."""
    from cherrypick.meic import paths

    cfg = _config()
    monkeypatch.setattr(paper, "load_base_config", lambda: cfg)
    path = paths.live_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db_path = _init_db(path.parent, path.name)
    today = live_loop._pl._now_et().strftime("%Y-%m-%d")
    paper._save_trade({**_open_trade_row("XSP"), "trade_date": today}, db_path)
    status = live_loop.run_status(cfg, designated="1234")
    assert len(status["open_ics"]) == 1 and status["open_ics"][0].startswith("OPEN1 XSP 583p/598c")


def test_a_ticks_critical_lines_are_recorded_and_nothing_else(capsys):
    """Shown to fail with the tick logging to stdout alone: the watchdog never heard of them."""
    today = live_loop._pl._now_et().strftime("%Y-%m-%d")
    before = len(live_loop.criticals_on(today))
    live_loop._tick_log("CRITICAL: close order failed for OPEN1 (stop_put) -- position stays open")
    live_loop._tick_log("entry refused: quantity 2 is over 1")
    live_loop._tick_log("stabilization oracle failed for %s: %s", "XSP", "timeout")  # print's signature
    live_loop._quiet("CRITICAL: live schema ensure failed: locked")
    recorded = [c["message"] for c in live_loop.criticals_on(today)][before:]
    assert recorded == [
        "CRITICAL: close order failed for OPEN1 (stop_put) -- position stays open",
        "CRITICAL: live schema ensure failed: locked",
    ]
    assert "entry refused" in capsys.readouterr().out
