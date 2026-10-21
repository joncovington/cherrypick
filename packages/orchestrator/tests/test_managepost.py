"""The position evaluation's post: which trades it lists, in what order, and how it states money."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import managepost


def _trade(tid, action, conf, pnl, reason="why", shape="butterfly"):
    return {
        "trade_id": tid,
        "shape": shape,
        "pnl_gross": pnl,
        "recommendation": {"action": action, "confidence": conf, "rule": "r", "reason": reason},
    }


def _doc(*trades, accounts=None):
    return {
        "session": "2026-10-19",
        "accounts": accounts or [{"account": "****1234", "trades": list(trades)}],
    }


def test_lists_only_acting_trades_most_urgent_first():
    doc = _doc(
        _trade("SPY#1", "REVIEW", 60, 39.5),
        _trade("GOOGL", "HOLD", 60, 212.5),
        _trade("X", "SETTLES", 90, 1.0),
        _trade("META", "REVIEW", 70, 54.0),
        _trade("CLF", "CLOSE", 65, 22.0, reason="the report is out"),
    )
    title, message, embed = managepost.build(doc, "16:35")
    lines = message.splitlines()
    assert [ln.split("**")[1] for ln in lines[:3]] == ["CLF butterfly", "META butterfly", "SPY#1 butterfly"]
    assert "GOOGL" not in message and "SETTLES" not in message
    assert lines[0] == "**CLF butterfly**: CLOSE (65) · +$22.00 gross · the report is out"
    assert title == "Positions to manage, 2026-10-19 16:35 ET: 3 of 5"
    assert embed["description"] == message and embed["color"] == 0xE5534B


def test_all_hold_still_posts_and_says_so():
    title, message, embed = managepost.build(_doc(_trade("A", "HOLD", 60, -5.0)), "12:30")
    assert title.endswith(": 0 of 1")
    assert message.startswith("Nothing needs action: all 1 trades hold.")
    assert embed["color"] == 0x3FB950


def test_review_only_is_amber_and_losses_are_signed():
    _, message, embed = managepost.build(_doc(_trade("A", "REVIEW", 60, -95.5)), "10:00")
    assert "-$95.50 gross" in message
    assert embed["color"] == 0xD4A72C


def test_several_accounts_are_labelled_and_long_posts_fit_discord():
    many = [_trade(f"T{i}", "REVIEW", 60, 1.0, reason="x" * 200) for i in range(40)]
    doc = _doc(accounts=[{"account": "****1111", "trades": many}, {"account": "****2222", "trades": []}])
    _, message, embed = managepost.build(doc, "10:00")
    assert "__****1111__" in message and "__****2222__" in message
    assert len(embed["description"]) <= 4096


ET = timezone(timedelta(hours=-4))


def _at(hh, mm, ss=40):
    return datetime(2026, 10, 21, hh, mm, ss, tzinfo=ET)


def test_runs_every_half_hour_post_hourly():
    """Runs at :00 and :30, each finishing ~40 s late: the hourly throttle posts on the hour only."""
    last, posted = None, []
    for hh, mm in [(10, 0), (10, 30), (11, 0), (11, 30), (12, 0), (16, 0), (16, 35)]:
        now = _at(hh, mm)
        if managepost.post_due(last, now, "2026-10-21", 60):
            posted.append(f"{hh}:{mm:02d}")
            last = {"session": "2026-10-21", "at": now.isoformat()}
    # 12:00 posts though 16:00 is the next; 16:35 is 35 minutes after the 16:00 post.
    assert posted == ["10:00", "11:00", "12:00", "16:00"]


def test_a_run_a_little_early_still_posts_within_the_slack():
    last = {"session": "2026-10-21", "at": _at(10, 0, 50).isoformat()}
    assert managepost.post_due(last, _at(11, 0, 5), "2026-10-21", 60)  # 59m15s: inside the 5-minute slack
    assert not managepost.post_due(last, _at(10, 50, 0), "2026-10-21", 60)  # 49m: not yet


def test_the_days_first_run_and_the_default_always_post():
    yesterday = {"session": "2026-10-20", "at": _at(10, 0).isoformat()}
    assert managepost.post_due(yesterday, _at(10, 0), "2026-10-21", 60)
    assert managepost.post_due(None, _at(10, 0), "2026-10-21", 60)
    today = {"session": "2026-10-21", "at": _at(10, 0).isoformat()}
    assert managepost.post_due(today, _at(10, 30), "2026-10-21", 0)  # 0: every run, as before
    assert managepost.post_due({"session": "2026-10-21", "at": "garbage"}, _at(10, 30), "2026-10-21", 60)


def test_the_setting_defaults_to_every_run():
    assert cfgmod.manage_settings({"manage": {}})["notify_every_minutes"] == 0
    assert cfgmod.manage_settings({"manage": {"notify_every_minutes": 60}})["notify_every_minutes"] == 60
    assert cfgmod.manage_settings({"manage": {"notify_every_minutes": -5}})["notify_every_minutes"] == 0
